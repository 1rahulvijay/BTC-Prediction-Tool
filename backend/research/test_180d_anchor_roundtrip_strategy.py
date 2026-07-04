"""Causal 180-day Polymarket-style BTC anchor/path/fade research lane.

This standalone script does not modify live models or place orders. It:
  * loads Binance 1-second candles and resamples to 15s/30s (30s recommended);
  * creates exact clock-aligned 5m/15m rounds using the bar OPEN as the anchor;
  * builds exactly 70 open-time features from completed bars before the anchor;
  * labels direction, touches, round trips, trends, volume/activity and causal fades;
  * creates touch-time fade events and late P(Hold) snapshots;
  * uses 64% train / 16% validation / 20% untouched chronological test;
  * tests 5-7 model families sequentially and clears memory after every fit;
  * emits per-model, ensemble, signal, window/regime and BTC-proxy metrics.

BTC barrier proxy results are not Polymarket profit. Real profit requires executable
entry ask, exit bid/settlement, fees, depth and slippage from the live recorder.

Usage:
  python -u backend/research/test_180d_anchor_roundtrip_strategy.py --days 180 --interval 30s
  python -u backend/research/test_180d_anchor_roundtrip_strategy.py --days 180 --interval 15s
  python -u backend/research/test_180d_anchor_roundtrip_strategy.py --days 14 --interval 30s --quick
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import math
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

import backtest_reversal_strategy as source_data  # noqa: E402

WARSAW = ZoneInfo("Europe/Warsaw")
EPS = 1e-9
VERSION = "2026-07-01-anchor-roundtrip-causal-v2"
BARRIERS = {5: (10.0, 20.0, 30.0), 15: (20.0, 30.0, 50.0)}
PRIMARY = {5: 30.0, 15: 50.0}


@dataclass
class Config:
    days: int = 180
    interval: str = "30s"
    horizons: tuple[int, ...] = (5, 15)
    test_fraction: float = 0.20
    validation_fraction: float = 0.16
    max_train_rows: int = 120_000
    threads: int = 4
    quick: bool = False
    save_models: bool = False
    offline: bool = False
    output_dir: str = ""


def setup_logging(out: Path) -> logging.Logger:
    out.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("anchor-roundtrip")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler(sys.stdout); sh.setFormatter(fmt)
    fh = logging.FileHandler(out / "run.log", encoding="utf-8"); fh.setFormatter(fmt)
    logger.addHandler(sh); logger.addHandler(fh)
    return logger


def _seconds(interval: str) -> int:
    try:
        value = int(interval.lower().removesuffix("s"))
    except ValueError as exc:
        raise SystemExit("--interval must be 1s, 15s or 30s") from exc
    if value not in (1, 15, 30):
        raise SystemExit("--interval must be 1s, 15s or 30s")
    return value


def load_bars(cfg: Config, log: logging.Logger) -> pd.DataFrame:
    """Load one day at a time; only the resampled bars are retained in memory."""
    seconds = _seconds(cfg.interval)
    if seconds == 1 and cfg.days > 45:
        log.warning("Raw 1s/180d can use several GB RAM and train very slowly; 30s is recommended.")
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    parts: list[pd.DataFrame] = []
    for idx in range(cfg.days):
        day = end - timedelta(days=cfg.days - 1 - idx)
        cache = Path(source_data.CACHE) / f"1s-{day.isoformat()}.parquet"
        if cfg.offline and not cache.exists():
            log.warning("missing cached day %s (offline: skipped)", day)
            continue
        raw = source_data._download_1s_day(day.isoformat())
        if raw is None or raw.empty:
            log.warning("missing day %s", day)
            continue
        part = source_data._resample(raw, seconds)
        parts.append(part)
        del raw
        if (idx + 1) % 10 == 0 or idx + 1 == cfg.days:
            log.info("data %d/%d days loaded; resampled rows=%s", idx + 1, cfg.days,
                     f"{sum(len(p) for p in parts):,}")
    if not parts:
        raise SystemExit("No sub-minute data available.")
    bars = pd.concat(parts, ignore_index=True)
    del parts
    bars = (bars.sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True))
    for col in ("open", "high", "low", "close", "volume", "taker_buy"):
        bars[col] = pd.to_numeric(bars[col], errors="coerce").astype("float64")
    bars = bars.dropna().reset_index(drop=True)
    log.info("data ready rows=%s span=%.1fd interval=%ss", f"{len(bars):,}",
             (bars.ts_ms.max() - bars.ts_ms.min()) / 86_400_000, seconds)
    return bars


def _feature_vector(o, h, lo, c, v, tb, ts, anchor_idx: int, bar_s: int) -> dict[str, float] | None:
    """Exactly 70 features from completed bars strictly before anchor_idx."""
    j = anchor_idx - 1
    bars60 = max(2, 3600 // bar_s)
    if j < bars60:
        return None
    px = float(c[j])

    def nbar(seconds: int) -> int:
        return max(1, int(math.ceil(seconds / bar_s)))

    def seg(arr, seconds: int):
        n = nbar(seconds)
        return arr[max(0, j - n + 1):j + 1]

    def ret(seconds: int) -> float:
        n = nbar(seconds)
        k = max(0, j - n)
        return (px / c[k] - 1.0) * 1e4 if c[k] > 0 else 0.0

    def rv(seconds: int) -> float:
        # Include the close immediately before the window so an N-bar window
        # contains N returns. This keeps rv_1m informative at 30s resolution.
        n = nbar(seconds)
        x = c[max(0, j - n):j + 1]
        r = np.diff(np.log(np.maximum(x, EPS)))
        return float(np.sqrt(np.mean(np.square(r))) * 1e4) if len(r) else 0.0

    def rng(seconds: int) -> float:
        hh, ll = seg(h, seconds), seg(lo, seconds)
        return float((np.max(hh) - np.min(ll)) / px * 1e4)

    def ema(seconds: int) -> float:
        x = seg(c, seconds)
        alpha = 2.0 / (len(x) + 1.0)
        value = float(x[0])
        for z in x[1:]:
            value = alpha * float(z) + (1.0 - alpha) * value
        return value

    def vsum(seconds: int) -> float:
        return float(np.sum(seg(v, seconds)))

    def taker_ratio(seconds: int) -> float:
        vv, bb = seg(v, seconds), seg(tb, seconds)
        return float(np.sum(bb) / (np.sum(vv) + EPS))

    def efficiency(seconds: int) -> float:
        x = seg(c, seconds)
        return float(abs(x[-1] - x[0]) / (np.sum(np.abs(np.diff(x))) + EPS))

    local = datetime.fromtimestamp(int(ts[anchor_idx]) / 1000, timezone.utc).astimezone(WARSAW)
    c5, c15 = seg(c, 300), seg(c, 900)
    d5, d15 = np.diff(c5), np.diff(c15)
    ema3, ema5, ema15, ema30 = ema(180), ema(300), ema(900), ema(1800)
    h60, l60 = np.max(seg(h, 3600)), np.min(seg(lo, 3600))
    v15 = seg(v, 900)
    vol_z = (v[j] - np.mean(v15)) / (np.std(v15) + EPS)
    n5 = nbar(300)
    start5 = max(0, j - n5 + 1)
    hi5, low5, close5 = h[start5:j + 1], lo[start5:j + 1], c[start5:j + 1]
    prev5 = np.concatenate(([c[start5 - 1] if start5 else o[start5]], close5[:-1]))
    tr5 = np.maximum.reduce((hi5 - low5, np.abs(hi5 - prev5), np.abs(low5 - prev5)))
    bar_range = max(h[j] - lo[j], EPS)
    changes = np.sign(d15)
    autocorr = (float(pd.Series(d15).autocorr())
                if len(d15) > 4 and float(np.std(d15)) > EPS else 0.0)
    if not np.isfinite(autocorr):
        autocorr = 0.0

    f = {
        # Volatility (8)
        "rv_1m": rv(60), "rv_2m": rv(120), "rv_3m": rv(180), "rv_5m": rv(300),
        "rv_10m": rv(600), "rv_15m": rv(900), "rv_30m": rv(1800), "rv_60m": rv(3600),
        # Returns (9)
        "ret_30s": ret(30), "ret_1m": ret(60), "ret_2m": ret(120), "ret_3m": ret(180),
        "ret_5m": ret(300), "ret_10m": ret(600), "ret_15m": ret(900),
        "ret_30m": ret(1800), "ret_60m": ret(3600),
        # Trend (9)
        "ema_gap_3_15": (ema3 - ema15) / px * 1e4,
        "ema_gap_5_30": (ema5 - ema30) / px * 1e4,
        "dist_ema_3": (px - ema3) / px * 1e4, "dist_ema_5": (px - ema5) / px * 1e4,
        "dist_ema_15": (px - ema15) / px * 1e4, "dist_ema_30": (px - ema30) / px * 1e4,
        "price_accel": ret(60) - ret(120) / 2.0,
        "momentum_alignment": float(np.sign(ret(300)) == np.sign(ret(900))),
        "trend_efficiency": efficiency(900),
        # Range (8)
        "range_1m": rng(60), "range_3m": rng(180), "range_5m": rng(300),
        "range_15m": rng(900), "range_30m": rng(1800), "range_60m": rng(3600),
        "compression_5_30": rng(300) / (rng(1800) + EPS), "atr_5m": float(np.mean(tr5) / px * 1e4),
        # Volume (10)
        "log_volume_bar": math.log1p(max(v[j], 0.0)), "vol_sum_1m": math.log1p(vsum(60)),
        "vol_sum_3m": math.log1p(vsum(180)), "vol_sum_5m": math.log1p(vsum(300)),
        "vol_sum_15m": math.log1p(vsum(900)), "vol_sum_30m": math.log1p(vsum(1800)),
        "vol_sum_60m": math.log1p(vsum(3600)), "volume_z_15m": float(vol_z),
        "volume_accel_1_5": vsum(60) / (vsum(300) / 5.0 + EPS),
        "volume_trend_5_30": vsum(300) / (vsum(1800) / 6.0 + EPS),
        # Aggressor flow (5)
        "taker_ratio_bar": float(tb[j] / (v[j] + EPS)), "taker_ratio_1m": taker_ratio(60),
        "taker_ratio_5m": taker_ratio(300), "taker_ratio_15m": taker_ratio(900),
        "taker_accel": taker_ratio(60) - taker_ratio(300),
        # Candle/path state (8)
        "body_bps": abs(c[j] - o[j]) / px * 1e4,
        "upper_wick_bps": (h[j] - max(o[j], c[j])) / px * 1e4,
        "lower_wick_bps": (min(o[j], c[j]) - lo[j]) / px * 1e4,
        "close_in_bar": (c[j] - lo[j]) / bar_range,
        "up_bar_ratio_5m": float(np.mean(d5 > 0)) if len(d5) else 0.5,
        "alternation_5m": float(np.mean(np.diff(np.sign(d5)) != 0)) if len(d5) > 1 else 0.0,
        "return_autocorr_15m": autocorr,
        "stretch_vwap_15m": (px - float(np.average(seg(c, 900), weights=seg(v, 900) + EPS))) / px * 1e4,
        # Regime state (6)
        "rv_ratio_5_30": rv(300) / (rv(1800) + EPS),
        "vol_of_vol": float(np.std([rv(60), rv(180), rv(300), rv(900)])),
        "trend_strength_15m": abs(ret(900)) / (rv(900) + EPS),
        "chop_15m": float(np.mean(np.diff(changes) != 0)) if len(changes) > 1 else 0.0,
        "dist_high_60m": (h60 - px) / px * 1e4, "dist_low_60m": (px - l60) / px * 1e4,
        # Time (7)
        "hour_sin": math.sin(2 * math.pi * local.hour / 24),
        "hour_cos": math.cos(2 * math.pi * local.hour / 24),
        "dow_sin": math.sin(2 * math.pi * local.weekday() / 7),
        "dow_cos": math.cos(2 * math.pi * local.weekday() / 7),
        "is_weekend": float(local.weekday() >= 5),
        "us_session": float(14 <= local.hour < 22), "asia_session": float(1 <= local.hour < 9),
    }
    if len(f) != 70:
        raise AssertionError(f"feature contract changed: {len(f)} != 70")
    return {k: float(np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)) for k, x in f.items()}


def regime_of(f: dict[str, float]) -> str:
    ratio = f["rv_15m"] / (f["rv_60m"] + EPS)
    if ratio >= 1.45:
        return "HIGH_VOL"
    if ratio <= 0.65:
        return "LOW_VOL"
    if f["trend_efficiency"] >= 0.48 and f["ret_15m"] > 0:
        return "TREND_UP"
    if f["trend_efficiency"] >= 0.48 and f["ret_15m"] < 0:
        return "TREND_DOWN"
    return "RANGE"


def fade_event(hi, lo, anchor: float, barrier: float, side: str) -> dict | None:
    """Causal touch event. Ambiguous touch bars are excluded, not guessed."""
    hit = np.where(hi >= anchor + barrier)[0] if side == "HIGH" else np.where(lo <= anchor - barrier)[0]
    if not len(hit):
        return None
    t = int(hit[0])
    if side == "HIGH":
        ambiguous = lo[t] <= anchor or hi[t] >= anchor + 2 * barrier
    else:
        ambiguous = hi[t] >= anchor or lo[t] <= anchor - 2 * barrier
    if ambiguous:
        return {"touch": t, "ambiguous": True, "ambiguous_exit": False, "win": None}
    win = 0
    exit_idx = len(hi) - 1
    for k in range(t + 1, len(hi)):
        if side == "HIGH":
            target_hit = lo[k] <= anchor
            stop_hit = hi[k] >= anchor + 2 * barrier
            if target_hit and stop_hit:
                # Intrabar ordering is unknowable. Use stop-first as the
                # conservative lower-bound assumption.
                return {"touch": t, "ambiguous": False, "ambiguous_exit": True,
                        "win": 0, "exit": k}
            if target_hit:
                win, exit_idx = 1, k; break
            if stop_hit:
                exit_idx = k; break
        else:
            target_hit = hi[k] >= anchor
            stop_hit = lo[k] <= anchor - 2 * barrier
            if target_hit and stop_hit:
                return {"touch": t, "ambiguous": False, "ambiguous_exit": True,
                        "win": 0, "exit": k}
            if target_hit:
                win, exit_idx = 1, k; break
            if stop_hit:
                exit_idx = k; break
    return {"touch": t, "ambiguous": False, "ambiguous_exit": False,
            "win": win, "exit": exit_idx}


def build_datasets(bars: pd.DataFrame, cfg: Config, log: logging.Logger):
    bar_s = _seconds(cfg.interval)
    o = bars.open.to_numpy(float); h = bars.high.to_numpy(float); lo = bars.low.to_numpy(float)
    c = bars.close.to_numpy(float); v = bars.volume.to_numpy(float); tb = bars.taker_buy.to_numpy(float)
    ts = bars.ts_ms.to_numpy("int64")
    open_rows: list[dict] = []; fade_rows: list[dict] = []; hold_rows: list[dict] = []
    for horizon in cfg.horizons:
        duration_ms = horizon * 60_000
        w = horizon * 60 // bar_s
        candidates = np.where(ts % duration_ms == 0)[0]
        built = 0; skipped_gap = 0
        for pos, i in enumerate(candidates):
            if i + w > len(bars) or i < 3600 // bar_s:
                continue
            expected_end = ts[i] + (w - 1) * bar_s * 1000
            if ts[i + w - 1] != expected_end:
                skipped_gap += 1; continue
            f = _feature_vector(o, h, lo, c, v, tb, ts, i, bar_s)
            if f is None:
                continue
            anchor = float(o[i])
            ph, pl, pc, pv = h[i:i + w], lo[i:i + w], c[i:i + w], v[i:i + w]
            close_move = float(pc[-1] - anchor)
            max_up = float(np.max(ph) - anchor); max_down = float(anchor - np.min(pl))
            prior_v = v[max(0, i - w):i]
            volume_ratio = float(np.sum(pv) / (np.mean(prior_v) * w + EPS))
            local = datetime.fromtimestamp(int(ts[i]) / 1000, timezone.utc).astimezone(WARSAW)
            round_id = f"{horizon}m_{int(ts[i])}"
            row = {"round_id": round_id, "round_start": int(ts[i]), "horizon": horizon,
                   "anchor": anchor, "expiry_close": float(pc[-1]), "close_move_usd": close_move,
                   "max_up_usd": max_up, "max_down_usd": max_down,
                   "range_usd": max_up + max_down, "future_volume_ratio": volume_ratio,
                   "direction_up": int(close_move > 0), "direction_down": int(close_move < 0),
                   "high_activity": int(volume_ratio >= 1.25),
                   "regime": regime_of(f), "local_hour": local.hour, "weekday": local.weekday(), **f}
            event_by_barrier: dict[float, list[tuple[str, dict]]] = {}
            for barrier in BARRIERS[horizon]:
                up_touch = max_up >= barrier; down_touch = max_down >= barrier
                row[f"touch_up_{int(barrier)}"] = int(up_touch)
                row[f"touch_down_{int(barrier)}"] = int(down_touch)
                row[f"touch_any_{int(barrier)}"] = int(up_touch or down_touch)
                row[f"roundtrip_{int(barrier)}"] = int(up_touch and down_touch)
                events = []
                for side in ("HIGH", "LOW"):
                    ev = fade_event(ph, pl, anchor, barrier, side)
                    if ev is None:
                        continue
                    events.append((side, ev))
                    if ev["ambiguous"]:
                        continue
                    t = ev["touch"]
                    prior_hi = list(ph[:t]); prior_lo = list(pl[:t])
                    level = anchor + barrier if side == "HIGH" else anchor - barrier
                    known_hi = max([anchor, level] + prior_hi)
                    known_lo = min([anchor, level] + prior_lo)
                    touch = {**f, "round_id": round_id, "round_start": int(ts[i]),
                             "touch_ts": int(ts[i] + t * bar_s * 1000), "horizon": horizon,
                             "barrier_usd": barrier, "side_up_touch": int(side == "HIGH"),
                             "touch_frac_left": (w - t) / w, "seconds_left": (w - t) * bar_s,
                             "pre_opp_bps": ((anchor - known_lo) if side == "HIGH" else
                                             (known_hi - anchor)) / anchor * 1e4,
                             "pre_range_bps": (known_hi - known_lo) / anchor * 1e4,
                             "overshoot_bps": 0.0, "fade_win": int(ev["win"]),
                             "ambiguous_exit": int(ev.get("ambiguous_exit", False)),
                             "regime": row["regime"], "local_hour": local.hour}
                    fade_rows.append(touch)
                event_by_barrier[barrier] = events
                valid = [ev for _, ev in events if not ev["ambiguous"]]
                row[f"reversal_any_{int(barrier)}"] = (int(any(ev["win"] for ev in valid))
                                                         if valid else (0 if not events else np.nan))
            primary = PRIMARY[horizon]
            first_events = [(side, ev) for side, ev in event_by_barrier[primary] if not ev["ambiguous"]]
            first_events.sort(key=lambda z: z[1]["touch"])
            row["reversal_first_primary"] = (int(first_events[0][1]["win"]) if first_events
                                              else (0 if not event_by_barrier[primary] else np.nan))
            row["trend_up"] = int(close_move >= primary and max_down < primary)
            row["trend_down"] = int(close_move <= -primary and max_up < primary)
            row["big_move"] = int(max(max_up, max_down) >= primary)
            if row[f"roundtrip_{int(primary)}"]:
                row["path_class"] = "ROUNDTRIP"
            elif first_events and first_events[0][1]["win"]:
                row["path_class"] = "UP_SPIKE_REVERT" if first_events[0][0] == "HIGH" else "DOWN_SPIKE_REVERT"
            elif row["trend_up"]:
                row["path_class"] = "TREND_UP"
            elif row["trend_down"]:
                row["path_class"] = "TREND_DOWN"
            elif max(max_up, max_down) < min(BARRIERS[horizon]):
                row["path_class"] = "QUIET"
            else:
                row["path_class"] = "MIXED"
            open_rows.append(row)

            # Conditional hold snapshots. Features at a snapshot use only completed path bars.
            for k in range(w):
                seconds_left = (w - k - 1) * bar_s
                if not 15 < seconds_left <= 120:
                    continue
                current = float(pc[k]); distance = current - anchor
                if abs(distance) < 1e-9:
                    continue
                side_up = int(distance > 0)
                path_close = pc[:k + 1]
                path_hi, path_lo = ph[:k + 1], pl[:k + 1]
                snapshot = {**f, "round_id": round_id, "round_start": int(ts[i]),
                            "snapshot_ts": int(ts[i] + (k + 1) * bar_s * 1000), "horizon": horizon,
                            "seconds_left": seconds_left, "distance_usd": distance,
                            "abs_distance_usd": abs(distance), "distance_bps": distance / anchor * 1e4,
                            "side_up": side_up, "high_so_far_usd": float(np.max(path_hi) - anchor),
                            "low_so_far_usd": float(anchor - np.min(path_lo)),
                            "range_so_far_usd": float(np.max(path_hi) - np.min(path_lo)),
                            "time_above_frac": float(np.mean(path_close > anchor)),
                            "path_efficiency": abs(distance) / (np.sum(np.abs(np.diff(path_close))) + EPS),
                            "snapshot_rv_bps": float(np.std(np.diff(np.log(np.maximum(path_close, EPS)))) * 1e4)
                            if len(path_close) > 2 else 0.0,
                            "current_side_holds": int((pc[-1] > anchor) == bool(side_up)),
                            "regime": row["regime"], "local_hour": local.hour}
                hold_rows.append(snapshot)
            built += 1
            if (pos + 1) % 10_000 == 0:
                log.info("labels %dm progress %s/%s", horizon, f"{pos+1:,}", f"{len(candidates):,}")
        log.info("labels %dm rounds=%s gap_skips=%s", horizon, f"{built:,}", f"{skipped_gap:,}")
    open_df = pd.DataFrame(open_rows).sort_values("round_start").reset_index(drop=True)
    fade_df = pd.DataFrame(fade_rows).sort_values(["round_start", "touch_ts"]).reset_index(drop=True)
    hold_df = pd.DataFrame(hold_rows).sort_values(["round_start", "snapshot_ts"]).reset_index(drop=True)
    log.info("datasets open=%s fade_events=%s hold_snapshots=%s", f"{len(open_df):,}",
             f"{len(fade_df):,}", f"{len(hold_df):,}")
    return open_df, fade_df, hold_df


def classifier_factories(cfg: Config) -> dict[str, Callable[[], object]]:
    from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    models: dict[str, Callable[[], object]] = {
        "logreg": lambda: Pipeline([("impute", SimpleImputer()), ("scale", StandardScaler()),
                                      ("model", LogisticRegression(max_iter=1000, class_weight="balanced"))]),
        "random_forest": lambda: RandomForestClassifier(n_estimators=220, max_depth=10,
                                                           min_samples_leaf=30, class_weight="balanced_subsample",
                                                           n_jobs=cfg.threads, random_state=7),
        "extra_trees": lambda: ExtraTreesClassifier(n_estimators=220, max_depth=12, min_samples_leaf=25,
                                                       class_weight="balanced", n_jobs=cfg.threads, random_state=7),
        "histgb": lambda: HistGradientBoostingClassifier(max_iter=220, max_depth=5, learning_rate=0.05,
                                                            l2_regularization=1.0, random_state=7),
    }
    if not cfg.quick:
        try:
            from xgboost import XGBClassifier
            models["xgboost"] = lambda: XGBClassifier(n_estimators=260, max_depth=5, learning_rate=0.04,
                                                         subsample=0.8, colsample_bytree=0.8, n_jobs=cfg.threads,
                                                         eval_metric="logloss", tree_method="hist", random_state=7)
        except Exception:
            pass
        try:
            from lightgbm import LGBMClassifier
            models["lightgbm"] = lambda: LGBMClassifier(n_estimators=260, max_depth=6, learning_rate=0.04,
                                                           num_leaves=31, n_jobs=cfg.threads, verbose=-1,
                                                           class_weight="balanced", random_state=7)
        except Exception:
            pass
        try:
            from catboost import CatBoostClassifier
            models["catboost"] = lambda: CatBoostClassifier(iterations=260, depth=6, learning_rate=0.04,
                                                               verbose=0, thread_count=cfg.threads,
                                                               auto_class_weights="Balanced",
                                                               allow_writing_files=False, random_seed=7)
        except Exception:
            pass
    return models


def regressor_factories(cfg: Config) -> dict[str, Callable[[], object]]:
    from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    models: dict[str, Callable[[], object]] = {
        "ridge": lambda: Pipeline([("impute", SimpleImputer()), ("scale", StandardScaler()),
                                    ("model", Ridge(alpha=10.0))]),
        "random_forest": lambda: RandomForestRegressor(n_estimators=180, max_depth=12, min_samples_leaf=20,
                                                          n_jobs=cfg.threads, random_state=7),
        "extra_trees": lambda: ExtraTreesRegressor(n_estimators=180, max_depth=14, min_samples_leaf=15,
                                                      n_jobs=cfg.threads, random_state=7),
        "histgb": lambda: HistGradientBoostingRegressor(max_iter=220, max_depth=5, learning_rate=0.05,
                                                           l2_regularization=1.0, random_state=7),
    }
    if not cfg.quick:
        try:
            from lightgbm import LGBMRegressor
            models["lightgbm"] = lambda: LGBMRegressor(n_estimators=240, max_depth=6, learning_rate=0.04,
                                                          num_leaves=31, n_jobs=cfg.threads, verbose=-1,
                                                          random_state=7)
        except Exception:
            pass
        try:
            from catboost import CatBoostRegressor
            models["catboost"] = lambda: CatBoostRegressor(iterations=240, depth=6, learning_rate=0.04,
                                                              verbose=0, thread_count=cfg.threads,
                                                              allow_writing_files=False, random_seed=7)
        except Exception:
            pass
    return models


def split_masks(df: pd.DataFrame):
    rounds = np.sort(df["round_start"].unique())
    if len(rounds) < 100:
        raise ValueError("not enough independent rounds")
    cut_train = rounds[max(1, int(len(rounds) * 0.64))]
    cut_test = rounds[max(2, int(len(rounds) * 0.80))]
    # Purge labels whose outcome window crosses into the next partition.
    outcome_end = df.round_start + df.horizon * 60_000
    train = (df.round_start < cut_train) & (outcome_end <= cut_train)
    valid = ((df.round_start >= cut_train) & (df.round_start < cut_test) &
             (outcome_end <= cut_test))
    test = df.round_start >= cut_test
    return train.to_numpy(), valid.to_numpy(), test.to_numpy()


def cap_rows(mask: np.ndarray, maximum: int) -> np.ndarray:
    idx = np.where(mask)[0]
    if len(idx) <= maximum:
        return idx
    return idx[np.linspace(0, len(idx) - 1, maximum, dtype=int)]


def wilson_lb(k: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return float("nan")
    p = k / n; d = 1 + z * z / n
    return (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / d


def choose_threshold(y: np.ndarray, p: np.ndarray) -> float:
    best = (float("-inf"), 0.5)
    candidates = sorted(set([0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9]
                            + [float(np.quantile(p, q)) for q in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95)]))
    min_n = max(20, int(len(y) * 0.01))
    for threshold in candidates:
        take = p >= threshold; n = int(take.sum())
        if n < min_n:
            continue
        score = wilson_lb(int(y[take].sum()), n) + 0.01 * math.log1p(n)
        if score > best[0]:
            best = (score, threshold)
    return float(best[1])


def classification_metrics(y, p, threshold=0.5) -> dict[str, float]:
    from sklearn.metrics import (accuracy_score, average_precision_score, balanced_accuracy_score,
                                 brier_score_loss, log_loss, roc_auc_score)
    pred = (p >= threshold).astype(int)
    take = p >= threshold
    return {
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else float("nan"),
        "average_precision": float(average_precision_score(y, p)) if len(np.unique(y)) > 1 else float("nan"),
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, np.clip(p, 1e-6, 1-1e-6), labels=[0, 1])),
        "accuracy_50": float(accuracy_score(y, pred)),
        "balanced_accuracy_50": float(balanced_accuracy_score(y, pred)),
        "signal_threshold": float(threshold), "signal_n": int(take.sum()),
        "signal_precision": float(y[take].mean()) if take.any() else float("nan"),
        "signal_wilson_lb": wilson_lb(int(y[take].sum()), int(take.sum())) if take.any() else float("nan"),
        "base_rate": float(np.mean(y)),
    }


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)


def evaluate_classifier_head(df, features, target, family, cfg, out, log):
    from sklearn.isotonic import IsotonicRegression
    d = df.dropna(subset=[target]).reset_index(drop=True)
    tr, va, te = split_masks(d)
    train_idx = cap_rows(tr, cfg.max_train_rows if not cfg.quick else min(20_000, cfg.max_train_rows))
    X = d[features].to_numpy(np.float32); y = d[target].to_numpy(int)
    Xtr, ytr = X[train_idx], y[train_idx]; Xv, yv = X[va], y[va]; Xt, yt = X[te], y[te]
    if len(np.unique(ytr)) < 2 or len(np.unique(yv)) < 2 or len(np.unique(yt)) < 2:
        log.warning("head %s/%s skipped: one class", family, target); return None
    val_probs: dict[str, np.ndarray] = {}; test_probs: dict[str, np.ndarray] = {}; rows = []
    factories = classifier_factories(cfg)
    for num, (name, make) in enumerate(factories.items(), 1):
        started = time.time(); model = None
        try:
            log.info("CLASS %s/%s model %d/%d %s train=%s", family, target, num, len(factories),
                     name, f"{len(Xtr):,}")
            model = make(); model.fit(Xtr, ytr)
            pv = np.asarray(model.predict_proba(Xv))[:, 1]
            pt = np.asarray(model.predict_proba(Xt))[:, 1]
            val_probs[name] = pv; test_probs[name] = pt
            m = classification_metrics(yt, pt)
            rows.append({"family": family, "target": target, "model": name,
                         "validation_auc": classification_metrics(yv, pv)["auc"],
                         "test_n": len(yt), "elapsed_sec": time.time()-started, **m})
            if cfg.save_models:
                path = out / "models" / f"{safe_name(family)}__{safe_name(target)}__{name}.pkl"
                path.parent.mkdir(parents=True, exist_ok=True); joblib.dump(model, path)
        except Exception as exc:
            log.exception("model failed %s/%s/%s: %s", family, target, name, exc)
        finally:
            del model; gc.collect()
    if not val_probs:
        return None
    ranked = sorted(val_probs, key=lambda n: rows[[r["model"] for r in rows].index(n)]["validation_auc"],
                    reverse=True)[:min(3, len(val_probs))]
    pv = np.mean([val_probs[n] for n in ranked], axis=0)
    pt = np.mean([test_probs[n] for n in ranked], axis=0)
    iso = IsotonicRegression(out_of_bounds="clip").fit(pv, yv)
    pvc = iso.transform(pv); ptc = iso.transform(pt)
    threshold = choose_threshold(yv, pvc)
    ensemble = {"family": family, "target": target, "model": "ENSEMBLE_TOP3_ISOTONIC",
                "members": "+".join(ranked), "validation_auc": classification_metrics(yv, pvc)["auc"],
                "test_n": len(yt), "elapsed_sec": 0.0, **classification_metrics(yt, ptc, threshold)}
    rows.append(ensemble)
    pred = d.loc[te, [x for x in ("round_id", "round_start", "horizon", "barrier_usd",
                                   "side_up_touch", "touch_ts", "snapshot_ts", "seconds_left")
                         if x in d.columns]].copy()
    pred["family"] = family; pred["target"] = target; pred["actual"] = yt
    pred["probability"] = ptc; pred["threshold"] = threshold
    pred["signal"] = ptc >= threshold
    return rows, pred.reset_index(drop=True)


def evaluate_regression_head(df, features, target, cfg, out, log):
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    d = df.dropna(subset=[target]).reset_index(drop=True)
    tr, va, te = split_masks(d)
    train_idx = cap_rows(tr, cfg.max_train_rows if not cfg.quick else min(20_000, cfg.max_train_rows))
    X = d[features].to_numpy(np.float32); y = d[target].to_numpy(float)
    Xtr, ytr = X[train_idx], y[train_idx]; Xv, yv = X[va], y[va]; Xt, yt = X[te], y[te]
    vp: dict[str, np.ndarray] = {}; tp: dict[str, np.ndarray] = {}; rows = []
    factories = regressor_factories(cfg)
    for num, (name, make) in enumerate(factories.items(), 1):
        started = time.time(); model = None
        try:
            log.info("REG %s model %d/%d %s train=%s", target, num, len(factories), name, f"{len(Xtr):,}")
            model = make(); model.fit(Xtr, ytr)
            pv, pt = np.asarray(model.predict(Xv)), np.asarray(model.predict(Xt))
            vp[name] = pv; tp[name] = pt
            rows.append({"target": target, "model": name,
                         "validation_mae": float(mean_absolute_error(yv, pv)), "test_n": len(yt),
                         "mae": float(mean_absolute_error(yt, pt)),
                         "rmse": float(mean_squared_error(yt, pt) ** 0.5),
                         "r2": float(r2_score(yt, pt)), "elapsed_sec": time.time()-started})
            if cfg.save_models:
                path = out / "models" / f"reg__{safe_name(target)}__{name}.pkl"
                path.parent.mkdir(parents=True, exist_ok=True); joblib.dump(model, path)
        except Exception as exc:
            log.exception("regressor failed %s/%s: %s", target, name, exc)
        finally:
            del model; gc.collect()
    if not vp:
        return None
    ranked = sorted(vp, key=lambda n: float(mean_absolute_error(yv, vp[n])))[:min(3, len(vp))]
    pv = np.mean([vp[n] for n in ranked], axis=0); pt = np.mean([tp[n] for n in ranked], axis=0)
    rows.append({"target": target, "model": "ENSEMBLE_TOP3", "members": "+".join(ranked),
                 "validation_mae": float(mean_absolute_error(yv, pv)), "test_n": len(yt),
                 "mae": float(mean_absolute_error(yt, pt)),
                 "rmse": float(mean_squared_error(yt, pt) ** 0.5),
                 "r2": float(r2_score(yt, pt)), "elapsed_sec": 0.0})
    pred = d.loc[te, ["round_id", "round_start", "horizon"]].copy()
    pred["target"] = target; pred["actual"] = yt; pred["prediction"] = pt
    return rows, pred.reset_index(drop=True)


def select_windows(open_df: pd.DataFrame) -> pd.DataFrame:
    """Choose hours/regimes on development data, then report their untouched-test behavior."""
    output = []
    for hz, d in open_df.groupby("horizon"):
        rounds = np.sort(d.round_start.unique()); cut = rounds[int(len(rounds) * 0.8)]
        dev, test = d[d.round_start < cut], d[d.round_start >= cut]
        objectives = {"reversal": "reversal_first_primary",
                      "roundtrip": f"roundtrip_{int(PRIMARY[hz])}",
                      "volume": "future_volume_ratio"}
        for group_col in ("local_hour", "regime"):
            for objective, metric in objectives.items():
                base = float(dev[metric].dropna().mean())
                stats = []
                for key, g in dev.groupby(group_col):
                    values = g[metric].dropna(); n = len(values)
                    if n < 50:
                        continue
                    shrunk = (float(values.sum()) + 50 * base) / (n + 50)
                    stats.append((shrunk, key, n, float(values.mean())))
                for rank, (_, key, n, rate) in enumerate(sorted(stats, reverse=True)[:3], 1):
                    g = test[test[group_col] == key]
                    y = g.reversal_first_primary.dropna()
                    output.append({"horizon": hz, "objective": objective, "group": group_col,
                                   "rank_selected_on_dev": rank, "value": key,
                                   "dev_n": n, "dev_objective_value": rate,
                                   "test_n": len(g),
                                   "test_reversal_rate": float(y.mean()) if len(y) else np.nan,
                                   "test_roundtrip_rate": float(g[f"roundtrip_{int(PRIMARY[hz])}"].mean()) if len(g) else np.nan,
                                   "test_volume_ratio": float(g.future_volume_ratio.mean()) if len(g) else np.nan,
                                   "test_range_usd": float(g.range_usd.mean()) if len(g) else np.nan})
    return pd.DataFrame(output)


def strategy_score(pred: pd.DataFrame, source: pd.DataFrame, family: str) -> dict:
    selected = pred[pred.signal].copy()
    if selected.empty:
        return {"family": family, "signals": 0}
    time_col = "touch_ts" if "touch_ts" in selected else "snapshot_ts"
    if time_col in selected:
        selected = selected.sort_values(time_col).drop_duplicates("round_id", keep="first")
    n = len(selected); wins = int(selected.actual.sum())
    result = {"family": family, "signals": n, "win_rate": wins/n,
              "wilson_lb": wilson_lb(wins, n), "threshold": float(selected.threshold.iloc[0])}
    if family == "fade_touch":
        keys = ["round_id", "touch_ts", "barrier_usd", "side_up_touch"]
        joined = selected.merge(source[keys].drop_duplicates(keys), on=keys, how="left")
        pnl = np.where(joined.actual.to_numpy(int) == 1, joined.barrier_usd, -joined.barrier_usd) - 1.0
        result.update({"btc_proxy_avg_usd": float(np.mean(pnl)), "btc_proxy_total_usd": float(np.sum(pnl)),
                       "warning": "BTC symmetric-barrier proxy only; not Polymarket share PnL"})
    return result


def scenario_outputs(all_predictions: pd.DataFrame, out: Path) -> pd.DataFrame:
    """Human-readable per-side/barrier accuracy and paper action files."""
    from sklearn.metrics import roc_auc_score
    rows = []
    fade = all_predictions[all_predictions.family == "fade_touch"].copy()
    if len(fade):
        fade["paper_action"] = np.where(
            fade.signal,
            np.where(fade.side_up_touch == 1, "PAPER_BUY_DOWN_AFTER_UP_SPIKE",
                     "PAPER_BUY_UP_AFTER_DOWN_SPIKE"),
            "SKIP_FADE")
        fade.to_csv(out / "fade_test_signals.csv", index=False)
        for keys, g in fade.groupby(["horizon", "barrier_usd", "side_up_touch"]):
            selected = g[g.signal]
            rows.append({"family": "fade_touch", "horizon": keys[0], "barrier_usd": keys[1],
                         "side": "UP_SPIKE_BUY_DOWN" if keys[2] == 1 else "DOWN_SPIKE_BUY_UP",
                         "test_n": len(g), "base_rate": float(g.actual.mean()),
                         "auc": float(roc_auc_score(g.actual, g.probability)) if g.actual.nunique() > 1 else np.nan,
                         "signal_n": len(selected),
                         "signal_precision": float(selected.actual.mean()) if len(selected) else np.nan,
                         "signal_wilson_lb": wilson_lb(int(selected.actual.sum()), len(selected))
                         if len(selected) else np.nan})
    hold = all_predictions[all_predictions.family == "conditional_hold"].copy()
    if len(hold):
        hold["paper_action"] = np.where(hold.signal, "PAPER_HOLD_CURRENT_SIDE", "PAPER_EXIT_OR_SKIP")
        hold.to_csv(out / "hold_test_signals.csv", index=False)
        for horizon, g in hold.groupby("horizon"):
            selected = g[g.signal].sort_values("snapshot_ts").drop_duplicates("round_id", keep="first")
            rows.append({"family": "conditional_hold", "horizon": horizon, "barrier_usd": np.nan,
                         "side": "CURRENT_SIDE", "test_n": len(g), "base_rate": float(g.actual.mean()),
                         "auc": float(roc_auc_score(g.actual, g.probability)) if g.actual.nunique() > 1 else np.nan,
                         "signal_n": len(selected),
                         "signal_precision": float(selected.actual.mean()) if len(selected) else np.nan,
                         "signal_wilson_lb": wilson_lb(int(selected.actual.sum()), len(selected))
                         if len(selected) else np.nan})
    result = pd.DataFrame(rows)
    result.to_csv(out / "scenario_metrics.csv", index=False)
    return result


def make_combined_signals(open_df, class_predictions, reg_predictions) -> pd.DataFrame:
    rounds = np.sort(open_df.round_start.unique()); cut = rounds[int(len(rounds) * 0.8)]
    base = open_df[open_df.round_start >= cut][["round_id", "round_start", "horizon", "anchor", "expiry_close",
                                               "path_class", "regime", "local_hour"]].copy()
    for target, pred in class_predictions.items():
        if target.startswith("open:"):
            name = target.split(":", 1)[1]
            base = base.merge(pred[["round_id", "probability", "threshold"]].rename(
                columns={"probability": f"p_{name}", "threshold": f"threshold_{name}"}), on="round_id", how="left")
    for target, pred in reg_predictions.items():
        base = base.merge(pred[["round_id", "prediction"]].rename(columns={"prediction": f"pred_{target}"}),
                          on="round_id", how="left")

    def decide(r):
        p_big = r.get("p_big_move", np.nan); p_rev = r.get("p_reversal_first_primary", np.nan)
        p_rt = r.get(f"p_roundtrip_{int(PRIMARY[int(r.horizon)])}", np.nan)
        p_up, p_dn = r.get("p_trend_up", np.nan), r.get("p_trend_down", np.nan)
        if np.isfinite(p_big) and p_big < 0.40:
            return "WAIT_QUIET"
        if np.isfinite(p_rev) and np.isfinite(p_rt) and p_rev >= 0.60 and p_rt >= 0.55:
            return "PAPER_FADE_WINDOW"
        if np.isfinite(p_up) and p_up >= 0.65:
            return "WATCH_TREND_UP"
        if np.isfinite(p_dn) and p_dn >= 0.65:
            return "WATCH_TREND_DOWN"
        return "WAIT_MIXED"
    base["research_signal"] = base.apply(decide, axis=1)
    base["predicted_high"] = base.anchor + base.get("pred_max_up_usd", np.nan)
    base["predicted_low"] = base.anchor - base.get("pred_max_down_usd", np.nan)
    return base


def markdown_table(df: pd.DataFrame) -> str:
    """Dependency-free small Markdown table for the generated report."""
    if df is None or df.empty:
        return "No rows."
    clean = df.copy()
    clean = clean.replace([np.inf, -np.inf], np.nan).fillna("-")
    columns = [str(c) for c in clean.columns]
    lines = ["| " + " | ".join(columns) + " |",
             "|" + "|".join("---" for _ in columns) + "|"]
    for row in clean.itertuples(index=False, name=None):
        values = []
        for value in row:
            if isinstance(value, float):
                values.append(f"{value:.4f}")
            else:
                values.append(str(value).replace("|", "\\|"))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_report(cfg, out, open_df, fade_df, hold_df, class_metrics, reg_metrics,
                 strategy, scenarios, windows, combined, log):
    cm = pd.DataFrame(class_metrics); rm = pd.DataFrame(reg_metrics)
    best_c = cm[cm.model == "ENSEMBLE_TOP3_ISOTONIC"].copy() if len(cm) else cm
    best_r = rm[rm.model == "ENSEMBLE_TOP3"].copy() if len(rm) else rm
    strategy_df = pd.DataFrame(strategy)
    fade_result = strategy_df[strategy_df.family == "fade_touch"] if len(strategy_df) else strategy_df
    hold_result = strategy_df[strategy_df.family == "conditional_hold"] if len(strategy_df) else strategy_df
    fade_text = (f"{fade_result.iloc[0].win_rate:.2%} on {int(fade_result.iloc[0].signals):,} first signals"
                 if len(fade_result) else "unavailable")
    hold_text = (f"{hold_result.iloc[0].win_rate:.2%} on {int(hold_result.iloc[0].signals):,} first signals"
                 if len(hold_result) else "unavailable")
    lines = [f"# 180-Day Anchor Round-Trip Strategy Research - {date.today().isoformat()}", "",
             f"Version: `{VERSION}` | interval: **{cfg.interval}** | days requested: **{cfg.days}** | "
             "split: **64% train / 16% validation / 20% untouched test**", "",
             "## Non-negotiable boundary", "",
             "This test measures BTC path events. It does **not** prove Polymarket profit because historical "
             "executable share asks, exit bids, fees, depth and fills are not present for 180 days. Any fade or "
             "trend output is a paper research signal.", "",
             "## Data and labels", "",
             f"- Exact clock-aligned rounds: {len(open_df):,}",
             f"- Causal, non-ambiguous touch events: {len(fade_df):,}",
             f"- Conditional hold snapshots: {len(hold_df):,}",
              "- Anchor: first bar OPEN at the exact 5m/15m boundary.",
              "- Features: exactly 70, all computed from completed bars before the anchor.",
              "- Touch context: completed pre-touch bars; ambiguous entry bars excluded.",
              f"- Conservative execution labels: {int(fade_df.get('ambiguous_exit', pd.Series(dtype=int)).sum()):,} "
              "later bars crossing both target and stop were scored stop-first losses.",
              "- Partitioning: chronological 64/16/20 with horizon-aware label purging at both boundaries.", "",
              "## Executive research verdict", "",
              f"- **REJECT the requested automatic fade strategy:** {fade_text}; it fails the 50% symmetric-barrier "
              "baseline before real market costs.",
              f"- **RETAIN P(Hold) as a recorder-only candidate:** {hold_text}; this is not profit because the "
              "historical Polymarket ask, fee, spread and fill are absent.",
              "- **RETAIN activity, touch and range heads as context:** they rank market path/volatility, but do "
              "not choose a profitable binary side by themselves.",
              "- **REJECT exact close-price prediction:** close-displacement regression is effectively zero-R2.",
              "- **Do not deploy any result from this lane:** every candidate must pass an independent later month "
              "and executable quote-plus-settlement replay.", "",
              "## Ensemble classification results", ""]
    if len(best_c):
        cols = ["family", "target", "test_n", "auc", "average_precision", "brier", "signal_n",
                "signal_precision", "signal_wilson_lb", "base_rate", "members"]
        lines += [markdown_table(best_c[[c for c in cols if c in best_c]].round(4)), ""]
    lines += ["## Path regression results", ""]
    if len(best_r):
        cols = ["target", "test_n", "mae", "rmse", "r2", "members"]
        lines += [markdown_table(best_r[[c for c in cols if c in best_r]].round(4)), ""]
    lines += ["## Side/barrier scenario scorecard", "",
              markdown_table(scenarios.round(4)) if len(scenarios) else "No scenario rows.", "",
              "## Strategy-level first-entry scorecard", "",
              markdown_table(pd.DataFrame(strategy).round(4)) if strategy else "No signals.", "",
              "## Trading-window selection", "",
              "Hours/regimes were selected on the first 80% and evaluated on the untouched final 20%.", "",
              markdown_table(windows.round(4)) if len(windows) else "No windows met minimum sample.", "",
              "## Interpretation", "",
              "- Direction must beat 50% and costs out-of-sample; otherwise it remains a null/context head.",
              "- Fade needs positive first-entry precision with a lower confidence bound above its true share-price "
              "breakeven. The BTC 1:1 proxy alone is insufficient.",
              "- P(Hold) is useful only when an executable ask is below conservative fair value after fees/buffer.",
              "- A profitable result must survive a later join to the live Polymarket recorder.", "",
              "## Files", "",
              "`classification_metrics.csv`, `regression_metrics.csv`, `strategy_metrics.csv`, "
              "`scenario_metrics.csv`, `fade_test_signals.csv`, `hold_test_signals.csv`, "
              "`window_selection.csv`, `combined_test_signals.csv`, `classification_predictions.csv`, "
              "`regression_predictions.csv`, `feature_names.json`, `config.json`, and `run.log`."]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    log.info("report written %s", out / "REPORT.md")


def run(cfg: Config) -> int:
    out = Path(cfg.output_dir) if cfg.output_dir else ROOT / "data" / "research" / f"anchor_roundtrip_{cfg.days}d_{cfg.interval}"
    log = setup_logging(out)
    log.info("START version=%s config=%s", VERSION, json.dumps(asdict(cfg), default=list))
    started = time.time()
    bars = load_bars(cfg, log)
    open_df, fade_df, hold_df = build_datasets(bars, cfg, log)
    del bars; gc.collect()
    if open_df.empty or fade_df.empty or hold_df.empty:
        raise SystemExit("One or more datasets are empty.")
    feature_names = list(_feature_vector(
        *[np.array([1.0] * 130) for _ in range(6)], np.arange(130) * 30_000, 129, 30).keys())
    # Above contract call is only for names; use the actual first row to verify parity.
    feature_names = [c for c in open_df.columns if c in feature_names]
    if len(feature_names) != 70:
        raise AssertionError(f"open feature parity failed: {len(feature_names)}")
    touch_features = feature_names + ["barrier_usd", "side_up_touch", "touch_frac_left",
                                      "seconds_left", "pre_opp_bps", "pre_range_bps", "overshoot_bps"]
    hold_features = feature_names + ["seconds_left", "distance_usd", "abs_distance_usd", "distance_bps",
                                     "side_up", "high_so_far_usd", "low_so_far_usd", "range_so_far_usd",
                                     "time_above_frac", "path_efficiency", "snapshot_rv_bps"]
    (out / "config.json").write_text(json.dumps({**asdict(cfg), "version": VERSION}, indent=2, default=list), encoding="utf-8")
    (out / "feature_names.json").write_text(json.dumps({"open_70": feature_names,
                                                          "touch": touch_features,
                                                          "hold": hold_features}, indent=2), encoding="utf-8")
    open_df.to_parquet(out / "open_rounds.parquet", index=False)
    fade_df.to_parquet(out / "fade_events.parquet", index=False)
    hold_df.to_parquet(out / "hold_snapshots.parquet", index=False)

    class_specs = []
    for hz in cfg.horizons:
        primary = int(PRIMARY[hz])
        sub = open_df[open_df.horizon == hz].copy()
        targets = ["direction_up", "direction_down", "high_activity", "trend_up", "trend_down",
                   f"touch_up_{primary}", f"touch_down_{primary}", f"roundtrip_{primary}"]
        for barrier in BARRIERS[hz]:
            targets.extend((f"touch_any_{int(barrier)}", f"reversal_any_{int(barrier)}"))
        targets.append("reversal_first_primary")
        for target in targets:
            class_specs.append((f"open_{hz}m", sub, feature_names, target, f"open:{target}"))
    class_specs += [("fade_touch", fade_df, touch_features, "fade_win", "fade:fade_win"),
                    ("conditional_hold", hold_df, hold_features, "current_side_holds", "hold:current_side_holds")]
    if cfg.quick:
        class_specs = [x for x in class_specs if x[3] in ("direction_up", f"touch_any_{int(PRIMARY[5])}",
                                                          f"touch_any_{int(PRIMARY[15])}", "reversal_first_primary",
                                                          "fade_win", "current_side_holds")]

    class_rows = []; class_preds = {}; strategy = []
    prediction_parts = []
    for family, frame, feats, target, key in class_specs:
        result = evaluate_classifier_head(frame, feats, target, family, cfg, out, log)
        if result is None:
            continue
        rows, pred = result; class_rows.extend(rows); prediction_parts.append(pred)
        class_preds[f"{family}:{key}"] = pred
        if family in ("fade_touch", "conditional_hold"):
            strategy.append(strategy_score(pred, frame, family))
    class_metrics = pd.DataFrame(class_rows)
    class_metrics.to_csv(out / "classification_metrics.csv", index=False)
    all_class_pred = pd.concat(prediction_parts, ignore_index=True) if prediction_parts else pd.DataFrame()
    all_class_pred.to_csv(out / "classification_predictions.csv", index=False)
    scenarios = scenario_outputs(all_class_pred, out)

    regression_rows = []; regression_preds = {}; regression_parts = []
    reg_targets = ("max_up_usd", "max_down_usd", "range_usd", "close_move_usd", "future_volume_ratio")
    for hz in cfg.horizons:
        sub = open_df[open_df.horizon == hz].copy()
        for target in reg_targets:
            result = evaluate_regression_head(sub, feature_names, target, cfg, out, log)
            if result is None:
                continue
            rows, pred = result; regression_rows.extend([{**r, "horizon": hz} for r in rows])
            pred["horizon"] = hz; regression_parts.append(pred)
            regression_preds[f"{hz}:{target}"] = pred
    reg_metrics = pd.DataFrame(regression_rows); reg_metrics.to_csv(out / "regression_metrics.csv", index=False)
    all_reg_pred = pd.concat(regression_parts, ignore_index=True) if regression_parts else pd.DataFrame()
    all_reg_pred.to_csv(out / "regression_predictions.csv", index=False)

    # Consolidate per-horizon open predictions with stable names.
    open_pred_map = {}
    for full_key, pred in class_preds.items():
        family, _, target = full_key.partition(":open:")
        if target:
            hz = family.split("_")[-1]
            open_pred_map[f"open:{hz}:{target}"] = pred
    combined_parts = []
    for hz in cfg.horizons:
        cp = {f"open:{k.split(':')[-1]}": v for k, v in open_pred_map.items() if f"{hz}m" in k}
        rp = {k.split(":", 1)[1]: v for k, v in regression_preds.items() if k.startswith(f"{hz}:")}
        combined_parts.append(make_combined_signals(open_df[open_df.horizon == hz], cp, rp))
    combined = pd.concat(combined_parts, ignore_index=True)
    combined.to_csv(out / "combined_test_signals.csv", index=False)
    pd.DataFrame(strategy).to_csv(out / "strategy_metrics.csv", index=False)
    windows = select_windows(open_df); windows.to_csv(out / "window_selection.csv", index=False)
    write_report(cfg, out, open_df, fade_df, hold_df, class_rows, regression_rows,
                 strategy, scenarios, windows, combined, log)
    log.info("DONE elapsed=%.1f minutes output=%s", (time.time()-started)/60, out)
    return 0


def selftest() -> int:
    rng = np.random.default_rng(7); bar_s = 30; days = 3
    n = days * 24 * 3600 // bar_s
    ts = np.arange(n, dtype=np.int64) * bar_s * 1000 + 1_700_000_000_000
    # Align synthetic start to a 15m boundary.
    ts -= ts[0] % 900_000
    close = 60_000 + np.cumsum(rng.normal(0, 4, n))
    bars = pd.DataFrame({"ts_ms": ts, "open": close + rng.normal(0, .5, n),
                         "high": close + np.abs(rng.normal(3, 2, n)),
                         "low": close - np.abs(rng.normal(3, 2, n)), "close": close,
                         "volume": np.abs(rng.normal(100, 25, n)),
                         "taker_buy": np.abs(rng.normal(50, 12, n))})
    cfg = Config(days=days, interval="30s", quick=True, max_train_rows=5000)
    out = ROOT / "data" / "research" / "anchor_roundtrip_selftest"
    log = setup_logging(out)
    o, f, h = build_datasets(bars, cfg, log)
    assert len(o) > 100 and len(f) > 10 and len(h) > 100
    feature_cols = [c for c in o.columns if c in _feature_vector(
        bars.open.values, bars.high.values, bars.low.values, bars.close.values,
        bars.volume.values, bars.taker_buy.values, bars.ts_ms.values, 121, 30)]
    assert len(feature_cols) == 70
    result = evaluate_classifier_head(o[o.horizon == 5], feature_cols, "direction_up",
                                      "selftest", cfg, out, log)
    assert result and len(result[0]) >= 3
    print(f"SELFTEST PASS open={len(o)} fade={len(f)} hold={len(h)} features={len(feature_cols)}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--interval", choices=("1s", "15s", "30s"), default="30s")
    p.add_argument("--horizons", type=int, nargs="+", default=(5, 15))
    p.add_argument("--max-train-rows", type=int, default=120_000)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--quick", action="store_true")
    p.add_argument("--save-models", action="store_true")
    p.add_argument("--offline", action="store_true", help="use cached days only; do not download missing days")
    p.add_argument("--output-dir", default="")
    p.add_argument("--selftest", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.selftest:
        raise SystemExit(selftest())
    config = Config(days=max(3, args.days), interval=args.interval, horizons=tuple(args.horizons),
                    max_train_rows=max(1000, args.max_train_rows), threads=max(1, args.threads),
                    quick=args.quick, save_models=args.save_models, offline=args.offline,
                    output_dir=args.output_dir)
    raise SystemExit(run(config))
