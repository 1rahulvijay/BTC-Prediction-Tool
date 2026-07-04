import argparse
import json
import os
import pandas as pd
import numpy as np
import time

import sys
sys.path.insert(0, os.path.dirname(__file__))
from edge_probe import _load_bars, FEATURE_BUILDERS

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data"
)
DAYS_SIDECAR = os.path.join(DATA_DIR, "research_matrix_1m.days.txt")
MANIFEST_PATH = os.path.join(DATA_DIR, "research_matrix_1m.manifest.json")
MIN_SOURCE_COVERAGE = 0.98


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
    return all(float(coverage.get(name, 0.0)) >= MIN_SOURCE_COVERAGE
               for name in ("trade_features", "crossvenue"))


def _load_manifest() -> dict:
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _default_days() -> int:
    raw = os.environ.get("BTC_HISTORICAL_DAYS") or os.environ.get("BTC_BACKFILL_DAYS") or "60"
    try:
        return max(5, int(str(raw).strip()))
    except (TypeError, ValueError):
        return 60


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
            "open": bars["close"], # Approximation: close is carried over
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
        if (
            (manifest_days == days or legacy_days == days)
            and _coverage_ok(summary, days)
            and _source_coverage_ok(manifest)
            and sources_older
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
            f"coverage_ok={_coverage_ok(summary, days)}, sources_older={sources_older}."
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
        "trade_features": float(merged.get("_trade_source_present", pd.Series(0, index=merged.index)).notna().mean()),
        "crossvenue": float(merged.get("_cross_source_present", pd.Series(0, index=merged.index)).notna().mean()),
    }
    print("Joined source coverage: " + ", ".join(
        f"{name}={value * 100:.2f}%" for name, value in source_coverage.items()))
    if any(value < MIN_SOURCE_COVERAGE for value in source_coverage.values()):
        print(
            f"\nERROR: source coverage is below the required {MIN_SOURCE_COVERAGE * 100:.0f}%. "
            "The previous research matrix is preserved; fix/retry the backfill."
        )
        raise SystemExit(2)
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
    ok = _coverage_ok(summary, days)
    manifest = {
        "requested_days": int(days),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "coverage_ok": bool(ok),
        "source_coverage": source_coverage,
        "summary": summary or {},
        "source_mtimes": sources,
    }
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    try:
        with open(DAYS_SIDECAR, "w") as f:
            f.write(str(days))
    except OSError:
        pass

    print(f"\nFinal Research Matrix saved to {out_path}")
    print(f"Window: {days} days | Rows: {len(merged)}")
    print(f"Coverage: span={summary['span_days']:.1f}d | manifest={MANIFEST_PATH}")
    print(f"Columns: {list(merged.columns)}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the 1m research matrix for the keeper heads.")
    ap.add_argument("--days", type=int, default=None,
                    help="History window in days (default: BTC_HISTORICAL_DAYS or 60).")
    ap.add_argument("--force", action="store_true", help="Rebuild even if the sidecar matches.")
    args = ap.parse_args()
    main(days=args.days, force=args.force)
