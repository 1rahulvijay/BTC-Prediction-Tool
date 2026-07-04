"""Systemic PCA absorption-ratio fragility probe using synchronized free crypto data.

This is distinct from the app's existing order-book `absorption_ratio`. The new
measure is named `systemic_absorption_*` and may only veto/downgrade; it never
creates UP/DOWN. BTC spot/perp come from the research matrix. ETH/SOL 5m closes
are downloaded from Binance Vision monthly archives and cached locally.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import zipfile
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR", ROOT / "data"))
MATRIX = DATA / "research_matrix_1m.parquet"
PERSISTENCE = DATA / "persistence_dataset.parquet"
PERSIST_MODEL = DATA / "saved_models" / "persistence_model.pkl"
CACHE = DATA / "cache" / "binance_vision_5m"
OUT = DATA / "research" / "systemic_absorption"
SYMBOLS = ("ETHUSDT", "SOLUSDT")
BASE_FEATURES = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude"]
WINDOWS = {"30m": 6, "2h": 24, "6h": 72}


def wilson_low(hits: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = hits / total
    den = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return (centre - margin) / den


def _month_range(start: pd.Timestamp, end: pd.Timestamp):
    current = pd.Timestamp(start.year, start.month, 1, tz="UTC")
    final = pd.Timestamp(end.year, end.month, 1, tz="UTC")
    while current <= final:
        yield current
        current = current + pd.offsets.MonthBegin(1)


def _read_kline_zip(raw: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        name = archive.namelist()[0]
        frame = pd.read_csv(archive.open(name), header=None)
    if frame.empty:
        return pd.DataFrame(columns=["ts_ms", "close"])
    ts = pd.to_numeric(frame.iloc[:, 0], errors="coerce")
    # Binance Vision spot timestamps from 2025 onward may be microseconds.
    ts = np.where(ts > 1e14, ts / 1000.0, ts)
    return pd.DataFrame({"ts_ms": ts.astype("int64"), "close": pd.to_numeric(frame.iloc[:, 4], errors="coerce")}).dropna()


def download_symbol(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    pieces = []
    session = requests.Session()
    session.headers.update({"User-Agent": "btc-systemic-absorption-research/1.0"})
    for month in _month_range(start, end):
        tag = month.strftime("%Y-%m")
        cache_path = CACHE / f"{symbol}-5m-{tag}.zip"
        if cache_path.exists():
            raw = cache_path.read_bytes()
        else:
            url = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/5m/{symbol}-5m-{tag}.zip"
            response = session.get(url, timeout=30)
            if response.status_code != 200:
                print(f"[absorption] monthly missing {symbol} {tag}; trying daily archives", flush=True)
                month_end = month + pd.offsets.MonthBegin(1) - pd.Timedelta(days=1)
                day_start = max(start.normalize(), month)
                day_end = min(end.normalize(), month_end)
                day = day_start
                while day <= day_end:
                    day_tag = day.strftime("%Y-%m-%d")
                    day_cache = CACHE / f"{symbol}-5m-{day_tag}.zip"
                    if day_cache.exists():
                        day_raw = day_cache.read_bytes()
                    else:
                        daily_url = (f"https://data.binance.vision/data/spot/daily/klines/{symbol}/5m/"
                                     f"{symbol}-5m-{day_tag}.zip")
                        daily_response = session.get(daily_url, timeout=30)
                        if daily_response.status_code != 200:
                            day += pd.Timedelta(days=1)
                            continue
                        day_raw = daily_response.content
                        day_cache.write_bytes(day_raw)
                    try:
                        pieces.append(_read_kline_zip(day_raw))
                    except Exception as exc:
                        print(f"[absorption] bad daily archive {symbol} {day_tag}: {exc}", flush=True)
                    day += pd.Timedelta(days=1)
                continue
            raw = response.content
            cache_path.write_bytes(raw)
        try:
            pieces.append(_read_kline_zip(raw))
            print(f"[absorption] loaded {symbol} {tag}", flush=True)
        except Exception as exc:
            print(f"[absorption] bad archive {symbol} {tag}: {exc}", flush=True)
    if not pieces:
        return pd.DataFrame(columns=["ts_ms", symbol.lower()])
    result = pd.concat(pieces, ignore_index=True).drop_duplicates("ts_ms").sort_values("ts_ms")
    result = result[(result["ts_ms"] >= int(start.timestamp() * 1000))
                    & (result["ts_ms"] <= int(end.timestamp() * 1000))]
    return result.rename(columns={"close": symbol.lower()})


def absorption_metrics(values: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return top-20% absorption, top eigen share and effective rank."""
    x = np.asarray(values, float)
    n, dimensions = x.shape
    absorption = np.full(n, np.nan)
    top_share = np.full(n, np.nan)
    effective_rank = np.full(n, np.nan)
    top_k = max(1, int(math.ceil(dimensions * 0.20)))
    for index in range(window - 1, n):
        sample = x[index - window + 1:index + 1]
        if not np.isfinite(sample).all():
            continue
        covariance = np.cov(sample, rowvar=False)
        eigenvalues = np.clip(np.linalg.eigvalsh(covariance), 0.0, None)
        total = float(eigenvalues.sum())
        if total <= 1e-12:
            continue
        weights = eigenvalues / total
        absorption[index] = float(np.sort(eigenvalues)[-top_k:].sum() / total)
        top_share[index] = float(eigenvalues[-1] / total)
        entropy = -float(np.sum(weights[weights > 0] * np.log(weights[weights > 0])))
        effective_rank[index] = float(np.exp(entropy))
    return absorption, top_share, effective_rank


def build_frame(days: int) -> pd.DataFrame:
    cols = ["ts_ms", "open", "high", "low", "close", "volume", "rv_15m", "rv_30m", "rv_60m",
            "compression_ratio", "shock_magnitude", "perp_spot_basis_bps", "cvd_spot", "cvd_perp",
            "vol_spot", "vol_perp"]
    minute = pd.read_parquet(MATRIX, columns=cols).sort_values("ts_ms")
    end_ms = int(minute["ts_ms"].max())
    start_ms = end_ms - int(days * 86400_000)
    minute = minute[minute["ts_ms"] >= start_ms].copy()
    minute["bucket"] = (minute["ts_ms"].astype("int64") // 300_000) * 300_000
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
           "rv_15m": "last", "rv_30m": "last", "rv_60m": "last", "compression_ratio": "last",
           "shock_magnitude": "last", "perp_spot_basis_bps": "last", "cvd_spot": "last",
           "cvd_perp": "last", "vol_spot": "sum", "vol_perp": "sum"}
    frame = minute.groupby("bucket", as_index=False).agg(agg).rename(columns={"bucket": "ts_ms"})
    start = pd.to_datetime(frame["ts_ms"].min(), unit="ms", utc=True)
    end = pd.to_datetime(frame["ts_ms"].max(), unit="ms", utc=True)
    for symbol in SYMBOLS:
        external = download_symbol(symbol, start, end)
        frame = frame.merge(external, on="ts_ms", how="inner")
    if len(frame) < 5000:
        raise RuntimeError(f"insufficient synchronized cross-asset rows: {len(frame)}")

    frame["btc_perp"] = frame["close"] * (1.0 + frame["perp_spot_basis_bps"].fillna(0) / 10_000.0)
    price_cols = ["close", "btc_perp", "ethusdt", "solusdt"]
    price_returns = np.log(frame[price_cols]).diff()
    # Scale each return by only past 30-day volatility to prevent one volatile asset dominating PCA.
    scale = price_returns.rolling(30 * 24 * 12, min_periods=288).std().shift(1)
    price_z = price_returns / scale.replace(0, np.nan)

    extra = pd.DataFrame(index=frame.index)
    extra["basis_change"] = frame["perp_spot_basis_bps"].diff()
    extra["cvd_spot_change"] = frame["cvd_spot"].diff()
    extra["cvd_perp_change"] = frame["cvd_perp"].diff()
    extra["spot_volume_change"] = np.log1p(frame["vol_spot"].clip(lower=0)).diff()
    extra["perp_volume_change"] = np.log1p(frame["vol_perp"].clip(lower=0)).diff()
    extra_scale = extra.rolling(30 * 24 * 12, min_periods=288).std().shift(1)
    extended = pd.concat([price_z, extra / extra_scale.replace(0, np.nan)], axis=1)

    for label, window in WINDOWS.items():
        ar, top, rank = absorption_metrics(price_z.to_numpy(), window)
        frame[f"systemic_absorption_price_{label}"] = ar
        frame[f"systemic_top_eigen_price_{label}"] = top
        frame[f"systemic_effective_rank_price_{label}"] = rank
        arx, topx, rankx = absorption_metrics(extended.to_numpy(), window)
        frame[f"systemic_absorption_extended_{label}"] = arx
        frame[f"systemic_top_eigen_extended_{label}"] = topx
        frame[f"systemic_effective_rank_extended_{label}"] = rankx

    for prefix in ("price", "extended"):
        source = frame[f"systemic_absorption_{prefix}_2h"]
        mean = source.rolling(30 * 24 * 12, min_periods=288).mean().shift(1)
        std = source.rolling(30 * 24 * 12, min_periods=288).std().shift(1)
        frame[f"systemic_absorption_{prefix}_spike"] = source - mean
        frame[f"systemic_absorption_{prefix}_zscore"] = (source - mean) / std.replace(0, np.nan)
    return frame.replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)


def _targets(frame: pd.DataFrame, horizon_minutes: int, train_end: int):
    bars = horizon_minutes // 5
    close = frame["close"].to_numpy(float)
    high = frame["high"].to_numpy(float)
    low = frame["low"].to_numpy(float)
    future_close = np.full(len(frame), np.nan)
    future_low = np.full(len(frame), np.nan)
    future_close[:-bars] = close[bars:]
    for index in range(len(frame) - bars):
        future_low[index] = low[index + 1:index + 1 + bars].min()
    absolute = np.abs(future_close - close)
    threshold = float(np.nanquantile(absolute[:train_end], .75))
    return (absolute >= threshold).astype(int), ((future_low - close) <= -threshold).astype(int), threshold


def evaluate(frame: pd.DataFrame) -> list[dict]:
    ar_features = [column for column in frame if column.startswith("systemic_")]
    results = []
    split = int(len(frame) * .70)
    valid_end = len(frame) - 3
    for horizon in (5, 15):
        y_move, y_drop, threshold = _targets(frame, horizon, split)
        for target, y in (("big_move", y_move), ("big_drop", y_drop)):
            for model_name, features in (("baseline", BASE_FEATURES),
                                         ("baseline_plus_systemic_absorption", BASE_FEATURES + ar_features)):
                model = Pipeline([("scale", StandardScaler()),
                                  ("model", LogisticRegression(class_weight="balanced", max_iter=1000, C=.2))])
                model.fit(frame.loc[:split - 1, features], y[:split])
                prob = model.predict_proba(frame.loc[split:valid_end - 1, features])[:, 1]
                truth = y[split:valid_end]
                results.append({"horizon": horizon, "target": target, "model": model_name,
                                "threshold_usd": threshold, "n_test": len(truth),
                                "base_rate": float(truth.mean()), "auc": float(roc_auc_score(truth, prob)),
                                "brier": float(brier_score_loss(truth, prob))})
    return results


def phold_veto(frame: pd.DataFrame) -> list[dict]:
    if not PERSISTENCE.exists() or not PERSIST_MODEL.exists():
        return []
    cols = ["window_start_ms", "horizon", "seconds_left", "seconds_elapsed",
            "distance_pct", "vol_60s_pct", "label"]
    p = pd.read_parquet(PERSISTENCE, columns=cols)
    p["current_ts_5m"] = (((p["window_start_ms"] + p["seconds_elapsed"] * 1000) // 300_000) * 300_000).astype("int64")
    p = p.merge(frame[["ts_ms", "systemic_absorption_price_zscore",
                       "systemic_absorption_extended_zscore"]],
                left_on="current_ts_5m", right_on="ts_ms", how="inner")
    p["abs_distance_pct"] = p["distance_pct"].abs()
    p["dist_vol_ratio"] = p["abs_distance_pct"] / (p["vol_60s_pct"] + 1e-6)
    bundle = joblib.load(PERSIST_MODEL)
    raw = bundle["clf"].predict_proba(p[bundle["features"]].to_numpy(float))[:, 1]
    p["p_hold"] = bundle["iso"].predict(raw)
    selected = p[p["p_hold"] >= .93].sort_values(["window_start_ms", "seconds_elapsed"])
    selected = selected.groupby(["window_start_ms", "horizon"], as_index=False).first()
    price_q90 = float(selected["systemic_absorption_price_zscore"].quantile(.90)) if len(selected) else 0.0
    extended_q90 = float(selected["systemic_absorption_extended_zscore"].quantile(.90)) if len(selected) else 0.0
    filters = {
        "baseline_phold_093": np.ones(len(selected), bool),
        "exclude_price_ar_z2": selected["systemic_absorption_price_zscore"].to_numpy() < 2.0,
        "exclude_extended_ar_z2": selected["systemic_absorption_extended_zscore"].to_numpy() < 2.0,
        "exclude_price_ar_top10": selected["systemic_absorption_price_zscore"].to_numpy() < price_q90,
        "exclude_extended_ar_top10": selected["systemic_absorption_extended_zscore"].to_numpy() < extended_q90,
    }
    output = []
    for name, mask in filters.items():
        subset = selected.loc[mask]
        hits = int(subset["label"].sum())
        output.append({"filter": name, "n": len(subset),
                       "coverage": len(subset) / len(selected) if len(selected) else 0,
                       "held_rate": hits / len(subset) if len(subset) else 0,
                       "wilson_low": wilson_low(hits, len(subset)),
                       "bad_avoided": int((selected.loc[~mask, "label"] == 0).sum()),
                       "good_lost": int((selected.loc[~mask, "label"] == 1).sum())})
    return output


def selftest() -> None:
    rng = np.random.default_rng(7)
    common = rng.normal(size=(500, 1))
    correlated = common + rng.normal(scale=.05, size=(500, 4))
    independent = rng.normal(size=(500, 4))
    high = absorption_metrics(correlated, 72)[0]
    low = absorption_metrics(independent, 72)[0]
    assert np.nanmean(high) > np.nanmean(low) + .25
    assert 0 < wilson_low(9, 10) < .9
    print("SYSTEMIC ABSORPTION SELFTEST PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=OUT)
    args = parser.parse_args()
    selftest()
    if args.selftest:
        return 0
    frame = build_frame(args.days)
    metrics = evaluate(frame)
    veto = phold_veto(frame)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.output_dir / "systemic_absorption_5m.parquet", index=False)
    pd.DataFrame(metrics).to_csv(args.output_dir / "feature_lift.csv", index=False)
    pd.DataFrame(veto).to_csv(args.output_dir / "phold_veto.csv", index=False)
    summary = {"rows": len(frame), "metrics": metrics, "phold_veto": veto,
               "warning": "systemic absorption is a veto candidate only, never a direction trigger"}
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(pd.DataFrame(metrics).round(4).to_string(index=False))
    print("\nP(Hold) veto\n" + (pd.DataFrame(veto).round(4).to_string(index=False) if veto else "unavailable"))
    print(f"\nWrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
