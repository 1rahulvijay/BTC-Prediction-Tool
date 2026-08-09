import argparse
import json
import os
import pandas as pd
import numpy as np
import time

import sys
sys.path.insert(0, os.path.dirname(__file__))
from edge_probe import _load_bars, FEATURE_BUILDERS
from artifact_identity import (
    atomic_write_json,
    feature_schema_hash,
    hash_file,
    hash_json,
)

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data"
)
DAYS_SIDECAR = os.path.join(DATA_DIR, "research_matrix_1m.days.txt")
MANIFEST_PATH = os.path.join(DATA_DIR, "research_matrix_1m.manifest.json")
MONTHLY_QUALITY_PATH = os.path.join(DATA_DIR, "research_matrix_monthly_quality.json")
MONTHLY_QUALITY_CSV_PATH = os.path.join(DATA_DIR, "research_matrix_monthly_quality.csv")
MIN_SOURCE_COVERAGE = 0.98
MAX_CONTIGUOUS_GAP_MINUTES = int(
    os.environ.get("BTC_MAX_CONTIGUOUS_GAP_MINUTES", "15")
)
MANDATORY_DYNAMIC_FEATURES = (
    "cvd_1m",
    "cvd_5m",
    "vpin",
    "large_trade_delta",
    "cvd_spot",
    "cvd_perp",
    "perp_spot_basis_bps",
    "vol_spot",
    "vol_perp",
)


def _path_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _source_mtimes() -> dict:
    return {
        "trade_features_backfill.parquet": _path_mtime(os.path.join(DATA_DIR, "trade_features_backfill.parquet")),
        "crossvenue_flow.parquet": _path_mtime(os.path.join(DATA_DIR, "crossvenue_flow.parquet")),
        "btc_1m_data.csv": _path_mtime(os.path.join(DATA_DIR, "btc_1m_data.csv")),
    }


def _matrix_summary(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        ts = pd.read_parquet(path, columns=["ts_ms"])["ts_ms"]
        if ts.empty:
            return None
        min_ts = int(ts.min())
        max_ts = int(ts.max())
        return {
            "rows": int(len(ts)),
            "min_ts_ms": min_ts,
            "max_ts_ms": max_ts,
            "span_days": max(0.0, (max_ts - min_ts) / 86_400_000.0),
        }
    except Exception:
        return None


def _coverage_ok(summary: dict | None, days: int) -> bool:
    if not summary:
        return False
    # Crypto trades 24/7, so a genuine N-day 1m matrix should be close to N*1440 rows.
    # Leave tolerance for exchange archive outages, but reject obvious stale fallbacks
    # such as a 60-day matrix being stamped as 180d.
    min_span = max(1.0, float(days) * 0.90)
    min_rows = int(float(days) * 1440.0 * 0.80)
    return summary.get("span_days", 0.0) >= min_span and summary.get("rows", 0) >= min_rows


def _source_coverage_ok(manifest: dict) -> bool:
    coverage = manifest.get("source_coverage") or {}
    monthly = manifest.get("monthly_quality") or {}
    return (
        all(
            float(coverage.get(name, 0.0)) >= MIN_SOURCE_COVERAGE
            for name in ("trade_features", "crossvenue")
        )
        and bool(monthly.get("passed", False))
    )


def _load_manifest() -> dict:
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _manifest_describes_matrix(manifest: dict, matrix_path: str) -> bool:
    """Does this manifest describe the parquet actually on disk?

    The skip gate checked the requested window, coverage, source coverage and source mtimes -
    everything EXCEPT the one property that makes a manifest worth trusting. On 2026-08-09 the
    parquet had been rewritten (Aug 9, 360d, 518,400 rows) while the manifest still described a
    different file (Jul 28, requested_days=60), and their hashes disagreed. The gate skipped
    anyway, so `artifact_identity.current_training_identity` went on reading a training_data_hash
    for a parquet that no longer existed - and would have stamped it onto a freshly trained
    bundle as provenance.

    `sources_older` cannot catch this: it compares SOURCE mtimes to the MATRIX mtime and says
    nothing about whether the manifest was regenerated alongside the matrix. Hashing 147MB costs
    ~0.1s, which is nothing next to certifying a model against data it was not trained on.
    """
    recorded = manifest.get("training_data_hash")
    if not recorded or not os.path.exists(matrix_path):
        return False        # absent is not "fine" - it is unproven, so rebuild
    try:
        return str(recorded) == hash_file(matrix_path)
    except Exception:
        return False


def _default_days() -> int:
    raw = os.environ.get("BTC_HISTORICAL_DAYS") or os.environ.get("BTC_BACKFILL_DAYS") or "60"
    try:
        return max(5, int(str(raw).strip()))
    except (TypeError, ValueError):
        return 60


def _source_present_rate(df: pd.DataFrame, column: str) -> float:
    if column not in df.columns or df.empty:
        return 0.0
    return float(pd.to_numeric(df[column], errors="coerce").fillna(0).gt(0).mean())


def _max_gap_minutes(
    ts_ms: pd.Series,
    expected_start_ms: int | None = None,
    expected_end_ms: int | None = None,
) -> float:
    values = np.sort(pd.to_numeric(ts_ms, errors="coerce").dropna().unique())
    if not len(values):
        return float("inf")
    gaps = (
        [float(max(0.0, np.max(np.diff(values)) / 60_000.0 - 1.0))]
        if len(values) >= 2
        else [0.0]
    )
    if expected_start_ms is not None:
        gaps.append(float(max(0.0, (values[0] - expected_start_ms) / 60_000.0)))
    if expected_end_ms is not None:
        gaps.append(float(max(0.0, (expected_end_ms - values[-1]) / 60_000.0)))
    return max(gaps)


def _monthly_quality_report(df: pd.DataFrame) -> dict:
    work = df.copy()
    work["ts_ms"] = pd.to_numeric(work["ts_ms"], errors="coerce")
    future_ts = int(work["ts_ms"].gt(int(time.time() * 1000) + 60_000).sum())
    invalid_ts = int(work["ts_ms"].isna().sum())
    work = work.dropna(subset=["ts_ms"]).copy()
    work["ts_ms"] = work["ts_ms"].astype(np.int64)
    work["month"] = pd.to_datetime(
        work["ts_ms"], unit="ms", utc=True
    ).dt.strftime("%Y-%m")
    global_first_ts = int(work["ts_ms"].min()) if not work.empty else 0
    global_last_ts = int(work["ts_ms"].max()) if not work.empty else 0

    core = ["open", "high", "low", "close", "volume"]
    feature_columns = [
        column
        for column in work.columns
        if column
        not in {
            "ts_ms",
            "timestamp",
            "month",
            "_trade_source_present",
            "_cross_source_present",
            *core,
        }
        and not column.startswith("future_")
    ]
    numeric = {name: pd.to_numeric(work[name], errors="coerce") for name in core}
    ohlc_invalid = (
        (numeric["open"] <= 0)
        | (numeric["high"] <= 0)
        | (numeric["low"] <= 0)
        | (numeric["close"] <= 0)
        | (numeric["volume"] < 0)
        | (numeric["high"] < numeric["open"])
        | (numeric["high"] < numeric["close"])
        | (numeric["low"] > numeric["open"])
        | (numeric["low"] > numeric["close"])
    )
    work["_ohlc_invalid"] = ohlc_invalid.fillna(True)
    work["_core_null"] = pd.concat(numeric, axis=1).isna().any(axis=1)

    rows: list[dict] = []
    months = (
        pd.period_range(
            pd.to_datetime(global_first_ts, unit="ms", utc=True).strftime("%Y-%m"),
            pd.to_datetime(global_last_ts, unit="ms", utc=True).strftime("%Y-%m"),
            freq="M",
        )
        if not work.empty
        else []
    )
    for period in months:
        month = str(period)
        group = work.loc[work["month"].eq(month)]
        month_start = int(pd.Timestamp(f"{month}-01", tz="UTC").timestamp() * 1000)
        month_end = int(
            (pd.Timestamp(f"{month}-01", tz="UTC") + pd.offsets.MonthBegin(1)).timestamp()
            * 1000
            - 60_000
        )
        expected_start = max(global_first_ts, month_start)
        expected_end = min(global_last_ts, month_end)
        expected = max(1, (expected_end - expected_start) // 60_000 + 1)
        unique_rows = int(group["ts_ms"].nunique())
        coverage = min(1.0, unique_rows / expected)
        duplicate_rows = int(len(group) - unique_rows)
        trade_coverage = _source_present_rate(group, "_trade_source_present")
        cross_coverage = _source_present_rate(group, "_cross_source_present")
        max_gap = _max_gap_minutes(
            group["ts_ms"], expected_start, expected_end
        )
        feature_values = (
            group[feature_columns].apply(pd.to_numeric, errors="coerce")
            if feature_columns
            else pd.DataFrame(index=group.index)
        )
        feature_cells = max(1, feature_values.shape[0] * feature_values.shape[1])
        feature_nan_pct = float(feature_values.isna().sum().sum() / feature_cells)
        feature_zero_pct = float(
            feature_values.fillna(0.0).eq(0.0).sum().sum() / feature_cells
        )
        constant_features = int(
            sum(feature_values[column].nunique(dropna=True) <= 1 for column in feature_values)
        )
        unavailable_mandatory = []
        for column in MANDATORY_DYNAMIC_FEATURES:
            if column not in group.columns:
                unavailable_mandatory.append(f"{column}:missing")
                continue
            values = pd.to_numeric(group[column], errors="coerce")
            non_null = values.dropna()
            if len(non_null) == 0:
                unavailable_mandatory.append(f"{column}:all_null")
            elif non_null.nunique() <= 1:
                unavailable_mandatory.append(f"{column}:constant")
            elif float(non_null.ne(0.0).mean()) < 0.01:
                unavailable_mandatory.append(f"{column}:over_99pct_zero")
        passed = (
            coverage >= MIN_SOURCE_COVERAGE
            and trade_coverage >= MIN_SOURCE_COVERAGE
            and cross_coverage >= MIN_SOURCE_COVERAGE
            and duplicate_rows == 0
            and max_gap <= MAX_CONTIGUOUS_GAP_MINUTES
            and int(group["_core_null"].sum()) == 0
            and int(group["_ohlc_invalid"].sum()) == 0
            and not unavailable_mandatory
        )
        rows.append(
            {
                "month": month,
                "rows": int(len(group)),
                "expected_rows_in_observed_span": int(expected),
                "minute_coverage": coverage,
                "trade_features_coverage": trade_coverage,
                "crossvenue_coverage": cross_coverage,
                "duplicate_rows": duplicate_rows,
                "max_contiguous_gap_minutes": max_gap,
                "core_null_rows": int(group["_core_null"].sum()),
                "ohlc_invalid_rows": int(group["_ohlc_invalid"].sum()),
                "feature_nan_pct": feature_nan_pct,
                "feature_zero_pct": feature_zero_pct,
                "constant_feature_count": constant_features,
                "feature_count": int(len(feature_columns)),
                "unavailable_mandatory_features": unavailable_mandatory,
                "passed": bool(passed),
            }
        )

    passed = (
        bool(rows)
        and all(row["passed"] for row in rows)
        and invalid_ts == 0
        and future_ts == 0
    )
    return {
        "passed": passed,
        "minimum_monthly_coverage": MIN_SOURCE_COVERAGE,
        "maximum_contiguous_gap_minutes": MAX_CONTIGUOUS_GAP_MINUTES,
        "invalid_timestamp_rows": invalid_ts,
        "future_timestamp_rows": future_ts,
        "months": rows,
    }


def _source_file_identity(paths: dict[str, str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for name, path in paths.items():
        try:
            stat = os.stat(path)
            result[name] = {
                "path": os.path.abspath(path),
                "size_bytes": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
        except OSError:
            result[name] = {
                "path": os.path.abspath(path),
                "missing": True,
            }
    return result


def _official_ohlc_parity(base_df: pd.DataFrame) -> dict:
    """Compare aggregate-trade OHLC with a small cached official Binance kline tail."""
    configured = os.environ.get("BTC_OFFICIAL_1M_PARITY_PATH")
    candidate = configured or os.path.join(
        DATA_DIR, "cache", "btcusdt_1m_3d.json"
    )
    if not os.path.exists(candidate):
        return {
            "available": False,
            "passed": True,
            "reason": "official 1m reference cache unavailable",
            "path": candidate,
        }
    try:
        with open(candidate, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        rows = payload.get("klines") if isinstance(payload, dict) else payload
        reference = pd.DataFrame(rows or [])
        if reference.empty or "time" not in reference.columns:
            raise ValueError("reference has no kline rows")
        reference["ts_ms"] = pd.to_numeric(
            reference["time"], errors="coerce"
        ).astype("Int64") * 1000
        columns = ["ts_ms", "open", "high", "low", "close"]
        reference = reference[columns].dropna(subset=["ts_ms"])
        joined = base_df[columns].merge(
            reference,
            on="ts_ms",
            how="inner",
            suffixes=("_agg", "_official"),
        )
        if len(joined) < 100:
            return {
                "available": True,
                "passed": False,
                "reason": f"only {len(joined)} overlapping minutes",
                "path": candidate,
            }
        differences = []
        per_field = {}
        for field in ("open", "high", "low", "close"):
            diff = np.abs(
                pd.to_numeric(joined[f"{field}_agg"], errors="coerce")
                - pd.to_numeric(joined[f"{field}_official"], errors="coerce")
            ).dropna()
            differences.extend(diff.tolist())
            per_field[field] = {
                "median_absolute_difference": float(np.median(diff)),
                "p99_absolute_difference": float(np.quantile(diff, 0.99)),
                "maximum_absolute_difference": float(np.max(diff)),
            }
        all_differences = np.asarray(differences, dtype=float)
        median_diff = float(np.median(all_differences))
        p99_diff = float(np.quantile(all_differences, 0.99))
        median_limit = float(os.environ.get("BTC_OHLC_MEDIAN_DIFF_MAX", "0.001"))
        p99_limit = float(os.environ.get("BTC_OHLC_P99_DIFF_MAX", "0.011"))
        return {
            "available": True,
            "passed": median_diff <= median_limit and p99_diff <= p99_limit,
            "path": candidate,
            "overlap_minutes": int(len(joined)),
            "median_absolute_difference": median_diff,
            "p99_absolute_difference": p99_diff,
            "median_limit": median_limit,
            "p99_limit": p99_limit,
            "fields": per_field,
        }
    except Exception as exc:
        return {
            "available": True,
            "passed": False,
            "reason": str(exc),
            "path": candidate,
        }


def export_base_csv(days=60):
    """
    Exports the base OHLCV from edge_probe._load_bars into data/btc_1m_data.csv.
    `days` controls the window (the daily aggTrade downloads are cached in backfill_cache/,
    so re-exporting a wider window only re-downloads days not already on disk).
    """
    csv_path = os.path.join(DATA_DIR, "btc_1m_data.csv")
    print(f"Loading base OHLCV from cache via _load_bars({days})...")
    bars = _load_bars(days)
    
    if bars is not None and len(bars["close"]) > 0:
        # Build pandas dataframe from bars
        df = pd.DataFrame({
            "ts_ms": bars["minute"] * 60000,
            "open": bars["open"],
            "high": bars["high"],
            "low": bars["low"],
            "close": bars["close"],
            "volume": bars["vol"],
            "trade_count": bars["count"],
            "taker_buy": bars["taker_buy"],
            "taker_sell": bars["taker_sell"]
        })
        
        # Merge some basic features from FEATURE_BUILDERS since the user specifically requested them
        for feat in ["realized_vol", "intensity", "vpin", "range_compression", "liquidity_shock"]:
            if feat in FEATURE_BUILDERS:
                X, cols = FEATURE_BUILDERS[feat](bars)
                for i, col in enumerate(cols):
                    df[col] = X[:, i]
        
        df.to_csv(csv_path, index=False)
        print(f"Exported base OHLCV and base features to {csv_path} ({len(df)} rows)")
        return df
    else:
        print("Failed to load base data from cache.")
        return pd.DataFrame()

def make_labels(df):
    """
    Computes the labels for future prediction targets.
    """
    df['future_close_5m'] = df['close'].shift(-5)
    df['future_high_5m'] = df['high'].rolling(window=5).max().shift(-5)
    df['future_low_5m'] = df['low'].rolling(window=5).min().shift(-5)
    
    df['ret_5m'] = df['future_close_5m'] - df['close']
    df['future_abs_move_5m'] = df['ret_5m'].abs()
    df['future_direction_5m'] = np.sign(df['ret_5m'])
    
    expected_move = df['close'] * 0.002 # 20 bps move
    mae_long = df['close'] - df['future_low_5m']
    mae_short = df['future_high_5m'] - df['close']

    is_big_long = (df['ret_5m'] > expected_move) & (mae_long < 0.4 * expected_move)
    is_big_short = (df['ret_5m'] < -expected_move) & (mae_short < 0.4 * expected_move)
    df['tradable_move_label'] = (is_big_long | is_big_short).astype(int)
    
    df['fail_fast_label'] = ((df['future_abs_move_5m'] > expected_move) & (df['tradable_move_label'] == 0)).astype(int)
    
    return df

def main(days=None, force=False):
    days = days or _default_days()
    out_path = os.path.join(DATA_DIR, "research_matrix_1m.parquet")

    # Idempotent skip: only skip when the requested window matches, coverage is believable,
    # and upstream source files have not changed since the matrix was written.
    if not force and os.path.exists(out_path):
        manifest = _load_manifest()
        summary = _matrix_summary(out_path)
        matrix_mtime = _path_mtime(out_path)
        sources = _source_mtimes()
        sources_older = all(float(v or 0.0) <= matrix_mtime for v in sources.values())
        legacy_days = None
        if os.path.exists(DAYS_SIDECAR):
            try:
                legacy_days = int(open(DAYS_SIDECAR).read().strip())
            except (ValueError, OSError):
                legacy_days = None
        manifest_days = int(manifest.get("requested_days", 0) or 0)
        describes = _manifest_describes_matrix(manifest, out_path)
        if (
            (manifest_days == days or legacy_days == days)
            and _coverage_ok(summary, days)
            and _source_coverage_ok(manifest)
            and sources_older
            and describes
        ):
            print(
                f"Research matrix already built for {days}d "
                f"({summary['rows']:,} rows, span={summary['span_days']:.1f}d). "
                "Skipping. Use --force to rebuild."
            )
            return
        print(
            "Research matrix rebuild required: "
            f"days_match={manifest_days == days or legacy_days == days}, "
            f"coverage_ok={_coverage_ok(summary, days)}, sources_older={sources_older}, "
            f"manifest_describes_matrix={describes}."
        )

    print(f"Building unified research matrix for {days} days...")

    # 1. Base Data — always (re)export to the requested window so the span is correct.
    base_df = export_base_csv(days=days)
    if base_df is None or base_df.empty:
        csv_path = os.path.join(DATA_DIR, "btc_1m_data.csv")
        if os.path.exists(csv_path):
            print("Base export empty; falling back to existing btc_1m_data.csv")
            base_df = pd.read_csv(csv_path)

    if base_df is None or base_df.empty:
        print("No base data found.")
        return

    ohlc_parity = _official_ohlc_parity(base_df)
    if ohlc_parity.get("available"):
        print(
            "Official OHLC parity: "
            f"passed={ohlc_parity.get('passed')} "
            f"overlap={ohlc_parity.get('overlap_minutes', 0)} "
            f"median_diff={ohlc_parity.get('median_absolute_difference')} "
            f"p99_diff={ohlc_parity.get('p99_absolute_difference')}"
        )
    if not ohlc_parity.get("passed", False):
        print(
            "\nERROR: aggregate-trade OHLC does not match the official Binance "
            f"1m reference: {ohlc_parity}"
        )
        print("The previous research matrix is preserved.")
        raise SystemExit(2)

    # Ensure timestamp is datetime and ts_ms is present
    if 'timestamp' in base_df.columns and 'ts_ms' not in base_df.columns:
        base_df['timestamp'] = pd.to_datetime(base_df['timestamp'])
        base_df['ts_ms'] = base_df['timestamp'].astype(np.int64) // 10**6
    elif 'ts_ms' in base_df.columns and 'timestamp' not in base_df.columns:
        base_df['timestamp'] = pd.to_datetime(base_df['ts_ms'], unit='ms')

    # 2. Trade Features (includes spot & funding)
    trade_path = os.path.join(DATA_DIR, "trade_features_backfill.parquet")
    if os.path.exists(trade_path):
        trade_df = pd.read_parquet(trade_path)
        if 'candle_ts' in trade_df.columns:
            trade_df.rename(columns={"candle_ts": "ts_ms"}, inplace=True)
        trade_df = trade_df.sort_values("ts_ms").drop_duplicates("ts_ms", keep="last")
        trade_df["_trade_source_present"] = 1
    else:
        print(f"Missing {trade_path}")
        trade_df = pd.DataFrame(columns=["ts_ms"])

    # 3. Crossvenue Flow
    cross_path = os.path.join(DATA_DIR, "crossvenue_flow.parquet")
    if os.path.exists(cross_path):
        cross_df = pd.read_parquet(cross_path)
        cross_df = cross_df.sort_values("ts_ms").drop_duplicates("ts_ms", keep="last")
        cross_df["_cross_source_present"] = 1
    else:
        print(f"Missing {cross_path}")
        cross_df = pd.DataFrame(columns=["ts_ms"])

    # Merge
    print(f"Base OHLCV rows: {len(base_df)}")
    print(f"Trade features rows: {len(trade_df)}")
    print(f"Crossvenue rows: {len(cross_df)}")
    
    merged = pd.merge(base_df, trade_df, on="ts_ms", how="left")
    merged = pd.merge(merged, cross_df, on="ts_ms", how="left")

    source_coverage = {
        "trade_features": _source_present_rate(merged, "_trade_source_present"),
        "crossvenue": _source_present_rate(merged, "_cross_source_present"),
    }
    print("Joined source coverage: " + ", ".join(
        f"{name}={value * 100:.2f}%" for name, value in source_coverage.items()))
    if any(value < MIN_SOURCE_COVERAGE for value in source_coverage.values()):
        print(
            f"\nERROR: source coverage is below the required {MIN_SOURCE_COVERAGE * 100:.0f}%. "
            "The previous research matrix is preserved; fix/retry the backfill."
        )
        raise SystemExit(2)

    monthly_quality = _monthly_quality_report(merged)
    atomic_write_json(MONTHLY_QUALITY_PATH, monthly_quality)
    pd.DataFrame(monthly_quality["months"]).to_csv(
        MONTHLY_QUALITY_CSV_PATH, index=False
    )
    failed_months = [
        row["month"] for row in monthly_quality["months"] if not row["passed"]
    ]
    if not monthly_quality["passed"]:
        print(
            "\nERROR: monthly data-quality gate failed. "
            f"Failed months={failed_months or 'global timestamp checks'}; "
            f"details={MONTHLY_QUALITY_PATH}"
        )
        print("The previous research matrix is preserved.")
        raise SystemExit(2)
    print(
        f"Monthly data-quality gate passed for {len(monthly_quality['months'])} months."
    )
    merged.drop(columns=["_trade_source_present", "_cross_source_present"],
                errors="ignore", inplace=True)
    
    # Sort
    merged.sort_values("ts_ms", inplace=True)
    
    # Build targets
    merged = make_labels(merged)
    
    # Cleanup and Save
    tmp_path = f"{out_path}.tmp.{os.getpid()}"
    try:
        merged.to_parquet(tmp_path, index=False)
        summary = _matrix_summary(tmp_path)
        if not _coverage_ok(summary, days):
            print("\nERROR: Research matrix coverage is too low for the requested window.")
            if summary:
                print(
                    f"Requested: {days}d | Actual rows: {summary['rows']:,} | "
                    f"Actual span: {summary['span_days']:.1f}d"
                )
            print("The previous matrix is preserved and the days sidecar is unchanged.")
            raise SystemExit(2)
        os.replace(tmp_path, out_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    sources = _source_mtimes()
    source_paths = {
        "trade_features": trade_path,
        "crossvenue": cross_path,
        "base_ohlcv_csv": os.path.join(DATA_DIR, "btc_1m_data.csv"),
    }
    source_files = _source_file_identity(source_paths)
    ok = _coverage_ok(summary, days)
    training_data_hash = hash_file(out_path)
    monthly_quality_hash = hash_file(MONTHLY_QUALITY_PATH)
    manifest = {
        "requested_days": int(days),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "coverage_ok": bool(ok),
        "source_coverage": source_coverage,
        "summary": summary or {},
        "source_mtimes": sources,
        "source_files": source_files,
        "source_manifest_hash": hash_json(source_files),
        "monthly_quality": {
            "passed": bool(monthly_quality["passed"]),
            "months_checked": len(monthly_quality["months"]),
            "path": MONTHLY_QUALITY_PATH,
        },
        "monthly_quality_hash": monthly_quality_hash,
        "training_data_hash": training_data_hash,
        "feature_schema_hash": feature_schema_hash(merged.columns),
        "ohlc_provenance": {
            "source": "Binance spot aggTrades",
            "resolution": "1m",
            "open": "first aggregate trade in minute",
            "high": "maximum aggregate trade price in minute",
            "low": "minimum aggregate trade price in minute",
            "close": "last aggregate trade in minute",
            "volume": "sum of aggregate trade quantity in minute",
            "validated_invariants": True,
            "official_parity": ohlc_parity,
        },
    }
    atomic_write_json(MANIFEST_PATH, manifest)

    try:
        with open(DAYS_SIDECAR, "w") as f:
            f.write(str(days))
    except OSError:
        pass

    print(f"\nFinal Research Matrix saved to {out_path}")
    print(f"Window: {days} days | Rows: {len(merged)}")
    print(f"Coverage: span={summary['span_days']:.1f}d | manifest={MANIFEST_PATH}")
    print(f"Columns: {list(merged.columns)}")


def selftest():
    import tempfile

    n = 65 * 1440
    ts_ms = np.arange(n, dtype=np.int64) * 60_000
    close = np.full(n, 100.0)
    data = {
        "ts_ms": ts_ms,
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": np.ones(n),
        "_trade_source_present": np.ones(n),
        "_cross_source_present": np.ones(n),
    }
    for offset, column in enumerate(MANDATORY_DYNAMIC_FEATURES, start=1):
        data[column] = np.sin(np.arange(n) / (10.0 + offset))
    frame = pd.DataFrame(data)
    report = _monthly_quality_report(frame)
    assert report["passed"] and len(report["months"]) == 3

    broken = frame.drop(index=np.arange(1000, 1020))
    broken_report = _monthly_quality_report(broken)
    assert not broken_report["passed"]
    assert any(
        row["max_contiguous_gap_minutes"] >= 20
        for row in broken_report["months"]
    )

    boundary_gap = frame.drop(index=np.arange(31 * 1440, 31 * 1440 + 20))
    boundary_report = _monthly_quality_report(boundary_gap)
    assert not boundary_report["passed"]
    assert any(
        row["max_contiguous_gap_minutes"] >= 20
        for row in boundary_report["months"]
    )

    month_values = pd.to_datetime(frame["ts_ms"], unit="ms", utc=True).dt.strftime("%Y-%m")
    missing_month_report = _monthly_quality_report(frame.loc[month_values.ne("1970-02")])
    assert not missing_month_report["passed"]
    february = next(
        row for row in missing_month_report["months"] if row["month"] == "1970-02"
    )
    assert february["rows"] == 0 and february["minute_coverage"] == 0.0

    zeroed = frame.copy()
    zeroed["cvd_1m"] = 0.0
    zero_report = _monthly_quality_report(zeroed)
    assert not zero_report["passed"]
    assert any(
        any("cvd_1m:" in item for item in row["unavailable_mandatory_features"])
        for row in zero_report["months"]
    )

    previous_reference = os.environ.get("BTC_OFFICIAL_1M_PARITY_PATH")
    try:
        with tempfile.TemporaryDirectory() as temporary:
            reference_path = os.path.join(temporary, "official.json")
            reference = pd.DataFrame(
                {
                    "time": ts_ms[:200] // 1000,
                    "open": close[:200],
                    "high": close[:200] + 1.0,
                    "low": close[:200] - 1.0,
                    "close": close[:200],
                }
            )
            with open(reference_path, "w", encoding="utf-8") as handle:
                json.dump({"klines": reference.to_dict("records")}, handle)
            os.environ["BTC_OFFICIAL_1M_PARITY_PATH"] = reference_path
            assert _official_ohlc_parity(frame.iloc[:200])["passed"]
            changed = frame.iloc[:200].copy()
            changed.loc[0:20, "open"] += 1.0
            assert not _official_ohlc_parity(changed)["passed"]
    finally:
        if previous_reference is None:
            os.environ.pop("BTC_OFFICIAL_1M_PARITY_PATH", None)
        else:
            os.environ["BTC_OFFICIAL_1M_PARITY_PATH"] = previous_reference

    # A manifest may only authorize a skip if it describes the parquet actually on disk.
    # Found live on 2026-08-09: parquet rewritten Aug 9 (360d, 518,400 rows) while the manifest
    # still described a Jul 28 build (requested_days=60), hashes disagreeing - and the skip gate
    # fired anyway, because it checked days/coverage/source-mtimes and never checked THIS.
    with tempfile.TemporaryDirectory() as tmp:
        matrix = os.path.join(tmp, "m.parquet")
        with open(matrix, "wb") as handle:
            handle.write(b"matrix-bytes")
        real = hash_file(matrix)
        assert _manifest_describes_matrix({"training_data_hash": real}, matrix)
        assert not _manifest_describes_matrix({"training_data_hash": "deadbeef"}, matrix), \
            "a STALE hash must not authorize a skip - this is the case that occurred"
        assert not _manifest_describes_matrix({}, matrix), \
            "an ABSENT hash is unproven, not fine - absent must never read as pass"
        assert not _manifest_describes_matrix({"training_data_hash": real},
                                              os.path.join(tmp, "gone.parquet")), \
            "and a missing parquet cannot be described by any manifest"

    print("build_research_matrix self-test: ALL PASS")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the 1m research matrix for the keeper heads.")
    ap.add_argument("--days", type=int, default=None,
                    help="History window in days (default: BTC_HISTORICAL_DAYS or 60).")
    ap.add_argument("--force", action="store_true", help="Rebuild even if the sidecar matches.")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
    else:
        main(days=args.days, force=args.force)
