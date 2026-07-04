"""Causal specialist-head research for BTC anchor markets.

This lane extends the 180-day anchor dataset with decision-focused targets:
time-to-touch, first-barrier order, reversal timing, path quantiles, future
regime, a liquidation-cascade proxy, model-failure filtering, and recorder-
backed Polymarket EV/fair-price/exit/fill diagnostics.

It is research-only. It never places orders or modifies deployed models.
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import math
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
ROOT = BACKEND.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND))

import test_180d_anchor_roundtrip_strategy as anchor  # noqa: E402

VERSION = "2026-07-01-decision-heads-v1"
CHECKPOINTS = (30, 60, 120)
CASCADE_USD = {5: 100.0, 15: 200.0}
QUANTILES = (0.10, 0.50, 0.90)
MIN_QUOTE_TRAIN_ROUNDS = 200
MIN_QUOTE_PROMOTION_ROUNDS = 1000
FEE_RATE = 0.07
FAIR_CAP = 0.91


def setup_log(out: Path) -> logging.Logger:
    out.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("decision-heads")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    for handler in (logging.StreamHandler(sys.stdout),
                    logging.FileHandler(out / "run.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    return log


def first_hit(values: np.ndarray, predicate) -> int | None:
    idx = np.where(predicate(values))[0]
    return int(idx[0]) if len(idx) else None


def build_path_labels(open_df: pd.DataFrame, bars: pd.DataFrame, features: list[str], log: logging.Logger):
    """Create path and event targets without recomputing or peeking into features."""
    ts = bars.ts_ms.to_numpy(np.int64)
    op = bars.open.to_numpy(float)
    hi = bars.high.to_numpy(float)
    lo = bars.low.to_numpy(float)
    close = bars.close.to_numpy(float)
    volume = bars.volume.to_numpy(float)
    taker_buy = bars.taker_buy.to_numpy(float)
    index = {int(value): i for i, value in enumerate(ts)}
    path_rows: list[dict] = []
    event_rows: list[dict] = []
    for n, row in enumerate(open_df.itertuples(index=False), 1):
        start = int(row.round_start)
        i = index.get(start)
        horizon = int(row.horizon)
        bars_n = horizon * 2
        if i is None or i + bars_n > len(bars):
            continue
        ph, pl, pc = hi[i:i + bars_n], lo[i:i + bars_n], close[i:i + bars_n]
        price = float(row.anchor)
        record = row._asdict()
        horizon_s = horizon * 60

        for barrier in anchor.BARRIERS[horizon]:
            b = int(barrier)
            up_idx = first_hit(ph, lambda x, p=price, z=barrier: x >= p + z)
            down_idx = first_hit(pl, lambda x, p=price, z=barrier: x <= p - z)
            candidates = [x for x in (up_idx, down_idx) if x is not None]
            any_idx = min(candidates) if candidates else None
            record[f"time_to_touch_any_{b}_s"] = ((any_idx + 1) * 30 if any_idx is not None
                                                    else horizon_s + 30)
            record[f"touch_any_{b}_observed"] = int(any_idx is not None)
            for seconds in CHECKPOINTS:
                if seconds <= horizon_s:
                    bars_limit = seconds // 30
                    record[f"touch_any_{b}_within_{seconds}s"] = int(
                        any_idx is not None and any_idx < bars_limit)
            if barrier == anchor.PRIMARY[horizon]:
                for seconds in (60, 120):
                    limit = seconds // 30
                    record[f"primary_touch_up_within_{seconds}s"] = int(
                        up_idx is not None and up_idx < limit)
                    record[f"primary_touch_down_within_{seconds}s"] = int(
                        down_idx is not None and down_idx < limit)

                # First-touch barrier-order event. Same-bar entry ambiguity is excluded.
                if candidates:
                    t = min(candidates)
                    side_up = up_idx == t and down_idx != t
                    side_down = down_idx == t and up_idx != t
                    entry_ambiguous = not (side_up or side_down)
                    if side_up:
                        entry_ambiguous = pl[t] <= price or ph[t] >= price + 2 * barrier
                    elif side_down:
                        entry_ambiguous = ph[t] >= price or pl[t] <= price - 2 * barrier
                    if not entry_ambiguous:
                        result = "TIMEOUT"
                        exit_idx = bars_n - 1
                        for k in range(t + 1, bars_n):
                            target = pl[k] <= price if side_up else ph[k] >= price
                            stop = ph[k] >= price + 2 * barrier if side_up else pl[k] <= price - 2 * barrier
                            if target and stop:
                                result, exit_idx = "STOP", k
                                break
                            if target:
                                result, exit_idx = "REVERT", k
                                break
                            if stop:
                                result, exit_idx = "STOP", k
                                break
                        elapsed = (exit_idx - t) * 30
                        event = {name: record[name] for name in features}
                        event.update({
                            "round_id": row.round_id, "round_start": start, "horizon": horizon,
                            "barrier_usd": float(barrier), "touch_side_up": int(side_up),
                            "touch_time_s": (t + 1) * 30, "seconds_left": horizon_s - (t + 1) * 30,
                            "first_barrier_revert": int(result == "REVERT"),
                            "first_barrier_stop": int(result == "STOP"),
                            "first_barrier_timeout": int(result == "TIMEOUT"),
                            "barrier_result": result,
                        })
                        for seconds in CHECKPOINTS:
                            event[f"reversal_within_{seconds}s"] = int(result == "REVERT" and elapsed <= seconds)
                        event_rows.append(event)

        returns = np.diff(np.log(np.maximum(pc, anchor.EPS)))
        future_rv = float(np.sqrt(np.mean(np.square(returns))) * 1e4) if len(returns) else 0.0
        path_eff = abs(float(row.close_move_usd)) / (float(np.sum(np.abs(np.diff(pc)))) + anchor.EPS)
        future_features = (anchor._feature_vector(op, hi, lo, close, volume, taker_buy, ts,
                                                  i + bars_n, 30)
                           if i + bars_n < len(bars) else None)
        future_regime = anchor.regime_of(future_features) if future_features else row.regime
        record["future_regime"] = future_regime
        record["regime_transition"] = int(future_regime != row.regime)
        record["future_high_vol"] = int(future_regime == "HIGH_VOL")
        record["future_trend"] = int(future_regime in ("TREND_UP", "TREND_DOWN"))
        threshold = CASCADE_USD[horizon]
        active = float(row.future_volume_ratio) >= 1.5 and path_eff >= 0.45
        record["cascade_proxy_up"] = int(active and float(row.max_up_usd) >= threshold and row.close_move_usd > 0)
        record["cascade_proxy_down"] = int(active and float(row.max_down_usd) >= threshold and row.close_move_usd < 0)
        record["cascade_proxy_any"] = int(record["cascade_proxy_up"] or record["cascade_proxy_down"])
        record["future_rv_bps"] = future_rv
        record["future_path_efficiency"] = path_eff
        path_rows.append(record)
        if n % 10_000 == 0:
            log.info("path labels %s/%s", f"{n:,}", f"{len(open_df):,}")
    paths = pd.DataFrame(path_rows).sort_values("round_start").reset_index(drop=True)
    events = pd.DataFrame(event_rows).sort_values("round_start").reset_index(drop=True)
    log.info("path labels complete rounds=%s primary_events=%s", f"{len(paths):,}", f"{len(events):,}")
    return paths, events


def run_classification_heads(paths, events, features, cfg, out, log):
    rows: list[dict] = []
    predictions: list[pd.DataFrame] = []
    specs = []
    for horizon in cfg.horizons:
        frame = paths[paths.horizon == horizon].copy()
        for barrier in anchor.BARRIERS[horizon]:
            for seconds in CHECKPOINTS:
                if seconds <= horizon * 60:
                    target = f"touch_any_{int(barrier)}_within_{seconds}s"
                    specs.append((f"touch_{horizon}m", frame, features, target))
        specs.extend((f"path_{horizon}m", frame, features, target) for target in (
            "primary_touch_up_within_60s", "primary_touch_down_within_60s",
            "primary_touch_up_within_120s", "primary_touch_down_within_120s",
            "regime_transition", "future_high_vol", "future_trend",
            "cascade_proxy_up", "cascade_proxy_down", "cascade_proxy_any"))
    event_features = features + ["barrier_usd", "touch_side_up", "touch_time_s", "seconds_left"]
    for target in ("first_barrier_revert", "first_barrier_stop", "first_barrier_timeout",
                   "reversal_within_30s", "reversal_within_60s", "reversal_within_120s"):
        specs.append(("post_touch", events, event_features, target))
    if cfg.quick:
        keep = {"touch_any_30_within_120s", "touch_any_50_within_120s",
                "first_barrier_revert", "regime_transition", "cascade_proxy_any"}
        specs = [spec for spec in specs if spec[3] in keep]
    log.info("classification heads=%d models/head=%d", len(specs), len(anchor.classifier_factories(cfg)))
    for number, (family, frame, columns, target) in enumerate(specs, 1):
        log.info("HEAD %d/%d %s/%s", number, len(specs), family, target)
        result = anchor.evaluate_classifier_head(frame, columns, target, family, cfg, out, log)
        if result is None:
            continue
        metric_rows, pred = result
        rows.extend(metric_rows)
        predictions.append(pred)
    metrics = pd.DataFrame(rows)
    preds = pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame()
    metrics.to_csv(out / "classification_metrics.csv", index=False)
    preds.to_csv(out / "classification_predictions.csv", index=False)
    return metrics, preds


def run_quantile_heads(paths, features, cfg, out, log):
    from lightgbm import LGBMRegressor
    from sklearn.metrics import mean_absolute_error, mean_pinball_loss

    metrics: list[dict] = []
    predictions: list[pd.DataFrame] = []
    for horizon in cfg.horizons:
        frame = paths[paths.horizon == horizon].reset_index(drop=True)
        train, valid, test = anchor.split_masks(frame)
        train_idx = anchor.cap_rows(train, cfg.max_train_rows)
        x = frame[features].to_numpy(np.float32)
        for target in ("max_up_usd", "max_down_usd", "range_usd"):
            y = frame[target].to_numpy(float)
            estimates: dict[float, np.ndarray] = {}
            validation_estimates: dict[float, np.ndarray] = {}
            for q in QUANTILES:
                log.info("QUANTILE h=%dm target=%s q=%.2f train=%s", horizon, target, q, f"{len(train_idx):,}")
                model = LGBMRegressor(objective="quantile", alpha=q, n_estimators=350, num_leaves=31,
                                      learning_rate=0.035, max_depth=7, min_child_samples=40,
                                      n_jobs=cfg.threads, random_state=7, verbose=-1)
                model.fit(x[train_idx], y[train_idx])
                validation_estimates[q] = np.asarray(model.predict(x[valid]))
                estimates[q] = np.asarray(model.predict(x[test]))
                metrics.append({"horizon": horizon, "target": target, "quantile": q,
                                "test_n": int(test.sum()),
                                "pinball_loss": float(mean_pinball_loss(y[test], estimates[q], alpha=q)),
                                "empirical_coverage": float(np.mean(y[test] <= estimates[q]))})
                del model
                gc.collect()
            ordered = np.sort(np.column_stack([estimates[q] for q in QUANTILES]), axis=1)
            ordered_valid = np.sort(np.column_stack([validation_estimates[q] for q in QUANTILES]), axis=1)
            actual = y[test]
            metrics.append({"horizon": horizon, "target": target, "quantile": "interval_10_90",
                            "test_n": len(actual),
                            "pinball_loss": np.nan,
                            "empirical_coverage": float(np.mean((actual >= ordered[:, 0]) &
                                                                (actual <= ordered[:, 2]))),
                            "median_mae": float(mean_absolute_error(actual, ordered[:, 1])),
                            "mean_interval_width": float(np.mean(ordered[:, 2] - ordered[:, 0]))})
            valid_actual = y[valid]
            scores = np.maximum.reduce((ordered_valid[:, 0] - valid_actual,
                                        valid_actual - ordered_valid[:, 2],
                                        np.zeros(len(valid_actual))))
            conformal_q = min(1.0, math.ceil((len(scores) + 1) * 0.80) / len(scores))
            correction = float(np.quantile(scores, conformal_q, method="higher"))
            calibrated_low = ordered[:, 0] - correction
            calibrated_high = ordered[:, 2] + correction
            metrics.append({"horizon": horizon, "target": target, "quantile": "conformal_10_90",
                            "test_n": len(actual), "pinball_loss": np.nan,
                            "empirical_coverage": float(np.mean((actual >= calibrated_low) &
                                                                (actual <= calibrated_high))),
                            "median_mae": float(mean_absolute_error(actual, ordered[:, 1])),
                            "mean_interval_width": float(np.mean(calibrated_high - calibrated_low)),
                            "conformal_correction": correction})
            pred = frame.loc[test, ["round_id", "round_start", "horizon"]].copy()
            pred["target"] = target
            pred["actual"] = actual
            pred["q10"], pred["q50"], pred["q90"] = ordered[:, 0], ordered[:, 1], ordered[:, 2]
            pred["conformal_q10"], pred["conformal_q90"] = calibrated_low, calibrated_high
            predictions.append(pred)
    metric_df = pd.DataFrame(metrics)
    pred_df = pd.concat(predictions, ignore_index=True)
    metric_df.to_csv(out / "quantile_metrics.csv", index=False)
    pred_df.to_csv(out / "quantile_predictions.csv", index=False)
    return metric_df, pred_df


def run_failure_heads(paths, features, cfg, out, log):
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    rows = []
    prediction_rows = []
    for horizon in cfg.horizons:
        frame = paths[paths.horizon == horizon].reset_index(drop=True)
        train, valid, test = anchor.split_masks(frame)
        train_idx = anchor.cap_rows(train, cfg.max_train_rows)
        x = frame[features].to_numpy(np.float32)
        y = frame.direction_up.to_numpy(int)
        val_probs, test_probs, val_auc = {}, {}, {}
        for name, factory in anchor.classifier_factories(cfg).items():
            model = None
            try:
                log.info("FAILURE base h=%dm %s", horizon, name)
                model = factory()
                model.fit(x[train_idx], y[train_idx])
                pv = np.asarray(model.predict_proba(x[valid]))[:, 1]
                pt = np.asarray(model.predict_proba(x[test]))[:, 1]
                val_probs[name], test_probs[name] = pv, pt
                val_auc[name] = roc_auc_score(y[valid], pv)
            finally:
                del model
                gc.collect()
        members = sorted(val_auc, key=val_auc.get, reverse=True)[:3]
        pv = np.mean([val_probs[name] for name in members], axis=0)
        pt = np.mean([test_probs[name] for name in members], axis=0)
        disagreement_v = np.std([val_probs[name] for name in members], axis=0)
        disagreement_t = np.std([test_probs[name] for name in members], axis=0)
        yv, yt = y[valid], y[test]
        correct_v = ((pv >= 0.5).astype(int) == yv).astype(int)
        correct_t = ((pt >= 0.5).astype(int) == yt).astype(int)
        xv = np.column_stack((x[valid], pv, np.abs(pv - 0.5) * 2, disagreement_v))
        xt = np.column_stack((x[test], pt, np.abs(pt - 0.5) * 2, disagreement_t))
        half = max(100, len(xv) // 2)
        meta_models = {
            "logreg": make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, class_weight="balanced")),
            "random_forest": RandomForestClassifier(n_estimators=240, max_depth=8, min_samples_leaf=25,
                                                     class_weight="balanced", n_jobs=cfg.threads, random_state=7),
            "histgb": HistGradientBoostingClassifier(max_iter=220, max_depth=4, learning_rate=0.04,
                                                       l2_regularization=2.0, random_state=7),
        }
        candidates = []
        for name, model in meta_models.items():
            model.fit(xv[:half], correct_v[:half])
            p_cal = np.asarray(model.predict_proba(xv[half:]))[:, 1]
            p_test = np.asarray(model.predict_proba(xt))[:, 1]
            auc = roc_auc_score(correct_v[half:], p_cal)
            candidates.append((auc, name, p_cal, p_test))
        auc, member, p_cal, p_correct = max(candidates, key=lambda item: item[0])
        threshold = anchor.choose_threshold(correct_v[half:], p_cal)
        retained = p_correct >= threshold
        n = int(retained.sum())
        wins = int(correct_t[retained].sum()) if n else 0
        rows.append({
            "horizon": horizon, "base_members": "+".join(members), "meta_model": member,
            "meta_validation_auc": auc, "failure_test_auc": roc_auc_score(1 - correct_t, 1 - p_correct),
            "test_n": len(yt), "base_accuracy": accuracy_score(yt, (pt >= 0.5).astype(int)),
            "retain_threshold": threshold, "retained_n": n, "retention_rate": n / len(yt),
            "retained_accuracy": wins / n if n else np.nan,
            "retained_wilson_lb": anchor.wilson_lb(wins, n) if n else np.nan,
        })
        pred = frame.loc[test, ["round_id", "round_start", "horizon"]].copy()
        pred["direction_actual"] = yt
        pred["direction_p_up"] = pt
        pred["direction_correct"] = correct_t
        pred["p_model_correct"] = p_correct
        pred["p_model_failure"] = 1 - p_correct
        pred["retain"] = retained
        prediction_rows.append(pred)
    metric_df = pd.DataFrame(rows)
    pred_df = pd.concat(prediction_rows, ignore_index=True)
    metric_df.to_csv(out / "model_failure_metrics.csv", index=False)
    pred_df.to_csv(out / "model_failure_predictions.csv", index=False)
    return metric_df, pred_df


def fee_per_share(price):
    p = np.clip(np.asarray(price, float), 0.0, 1.0)
    return FEE_RATE * p * (1.0 - p)


def load_recorder():
    import duckdb
    db = ROOT / "data" / "execution_layer.duckdb"
    try:
        con = duckdb.connect(str(db), read_only=True)
        snapshots = con.execute("SELECT * FROM pm_round_snapshots").fetchdf()
        settlements = con.execute("SELECT * FROM pm_round_settlements").fetchdf()
        con.close()
        return snapshots, settlements, "live_db"
    except Exception:
        snapshots_path = ROOT / "data" / "pm_export_snapshots.parquet"
        settlements_path = ROOT / "data" / "pm_export_settlements.parquet"
        if not snapshots_path.exists():
            return pd.DataFrame(), pd.DataFrame(), "missing"
        return (pd.read_parquet(snapshots_path),
                pd.read_parquet(settlements_path) if settlements_path.exists() else pd.DataFrame(),
                "parquet_export")


def evaluate_quote_heads(out: Path, log: logging.Logger):
    snapshots, settlements, source = load_recorder()
    status = {"source": source, "raw_snapshots": len(snapshots), "raw_rounds": 0,
              "trustworthy_rounds": 0, "joined_settled_rounds": 0,
              "training_gate": "BLOCKED", "promotion_gate": "BLOCKED"}
    if snapshots.empty:
        pd.DataFrame([status]).to_csv(out / "quote_data_status.csv", index=False)
        return (status, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame())
    status["raw_rounds"] = int(snapshots.slug.nunique())
    first = snapshots.sort_values("ts").groupby("slug", as_index=False).first()
    good = set(first.loc[(first.ts.astype(float) - first.anchor_ts.astype(float)).abs() <= 5.0, "slug"])
    snapshots = snapshots[snapshots.slug.isin(good)].copy()
    status["trustworthy_rounds"] = int(snapshots.slug.nunique())
    wins = settlements.set_index("slug")["settled_side"].to_dict() if len(settlements) else {}
    snapshots = snapshots[snapshots.slug.isin(wins)].copy()
    joined = int(snapshots.slug.nunique())
    status["joined_settled_rounds"] = joined
    status["training_gate"] = "PASS" if joined >= MIN_QUOTE_TRAIN_ROUNDS else "BLOCKED"
    status["promotion_gate"] = "PASS" if joined >= MIN_QUOTE_PROMOTION_ROUNDS else "BLOCKED"
    if snapshots.empty:
        pd.DataFrame([status]).to_csv(out / "quote_data_status.csv", index=False)
        return (status, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame())

    sig_t = snapshots.vol_60s_pct * np.sqrt(np.maximum(snapshots.seconds_left, 1) / 60.0)
    z = snapshots.distance_pct.abs() / np.maximum(sig_t, 1e-9)
    phi = 0.5 * (1 + np.vectorize(math.erf)(np.clip(z, -8, 8) / math.sqrt(2)))
    barrier_fair = pd.Series(phi, index=snapshots.index, dtype=float)
    keeper_fair = snapshots.p_hold_cur.astype(float).fillna(barrier_fair)
    snapshots["fair"] = np.minimum(np.minimum(barrier_fair, keeper_fair), FAIR_CAP)
    up = snapshots.current_side == 1
    snapshots["cur_ask"] = np.where(up, snapshots.up_ask, snapshots.down_ask)
    snapshots["cur_bid"] = np.where(up, snapshots.up_bid, snapshots.down_bid)
    snapshots["cur_spread"] = np.where(up, snapshots.up_spread, snapshots.down_spread)
    snapshots["cur_size"] = np.where(up, snapshots.up_top_ask_size, snapshots.down_top_ask_size)
    snapshots["fee"] = fee_per_share(snapshots.cur_ask)
    snapshots["raw_edge"] = snapshots.fair - snapshots.cur_ask - snapshots.fee
    snapshots["won"] = [int(wins[str(slug)] == int(side)) for slug, side in
                         zip(snapshots.slug.astype(str), snapshots.current_side)]

    base_gate = ((snapshots.seconds_left > 15) & (snapshots.seconds_left <= 120) &
                 ((snapshots.btc_price - snapshots.anchor_price).abs() >= 10) &
                 (snapshots.cur_spread <= 0.03) & (snapshots.cur_size > 0) &
                 snapshots.cur_ask.notna() & (snapshots.p_hold_cur >= 0.93))
    ev_rows, entry_rows = [], []
    for buffer in (0.00, 0.01, 0.02, 0.03, 0.05):
        for slippage in (0.00, 0.01, 0.02):
            eligible = snapshots[base_gate & (snapshots.raw_edge >= buffer + slippage)]
            entries = eligible.sort_values("ts").drop_duplicates("slug", keep="first").copy()
            n = len(entries)
            if n:
                entries["pnl_settlement"] = entries.won - entries.cur_ask - entries.fee - slippage
                entries["buffer"] = buffer
                entries["slippage"] = slippage
                entry_rows.append(entries)
            k = int(entries.won.sum()) if n else 0
            ev_rows.append({"buffer": buffer, "slippage": slippage, "signals": n,
                            "win_rate": k / n if n else np.nan,
                            "wilson_lb": anchor.wilson_lb(k, n) if n else np.nan,
                            "avg_fair": entries.fair.mean() if n else np.nan,
                            "avg_ask": entries.cur_ask.mean() if n else np.nan,
                            "net_per_contract": entries.pnl_settlement.mean() if n else np.nan,
                            "status": "EXPLORATORY" if joined < MIN_QUOTE_TRAIN_ROUNDS else "TESTABLE"})
    entries_all = pd.concat(entry_rows, ignore_index=True) if entry_rows else pd.DataFrame()

    exit_rows = []
    if len(entries_all):
        for entry in entries_all.itertuples(index=False):
            later = snapshots[(snapshots.slug == entry.slug) & (snapshots.ts > entry.ts)]
            if later.empty:
                continue
            side_bid = later.up_bid if int(entry.current_side) == 1 else later.down_bid
            best_bid = float(side_bid.max())
            exit_fee = float(fee_per_share([best_bid])[0])
            pnl = best_bid - float(entry.cur_ask) - float(entry.fee) - exit_fee
            exit_rows.append({"slug": entry.slug, "horizon": int(entry.horizon), "buffer": entry.buffer,
                              "slippage": entry.slippage,
                              "entry_ask": entry.cur_ask, "best_future_bid": best_bid,
                              "price_only_exit_pnl": pnl, "profitable_exit_quote": int(pnl > 0)})
    exit_df = pd.DataFrame(exit_rows)

    fill_rows = []
    sample = snapshots[base_gate].sort_values("ts").drop_duplicates("slug", keep="first")
    for size in (1, 10, 50, 100):
        fill_rows.append({"contracts": size, "rounds": len(sample),
                          "taker_depth_fill_rate": float((sample.cur_size >= size).mean()) if len(sample) else np.nan,
                          "passive_fill_status": "BLOCKED_NO_ORDER_OR_TRADE_MATCHING"})
    fill_df = pd.DataFrame(fill_rows)

    calibration = snapshots[base_gate].sort_values("ts").drop_duplicates("slug", keep="first").copy()
    if len(calibration):
        calibration["ask_bin"] = pd.cut(calibration.cur_ask, np.linspace(0, 1, 11), include_lowest=True)
        cal_df = calibration.groupby("ask_bin", observed=True).agg(
            rounds=("slug", "nunique"), avg_ask=("cur_ask", "mean"),
            actual_win_rate=("won", "mean"), avg_model_fair=("fair", "mean")).reset_index()
        cal_df["ask_bin"] = cal_df.ask_bin.astype(str)
        fair_metrics = pd.DataFrame([{
            "rounds": len(calibration),
            "model_fair_brier": float(np.mean(np.square(calibration.won - calibration.fair))),
            "market_ask_brier": float(np.mean(np.square(calibration.won - calibration.cur_ask))),
            "model_fair_mae": float(np.mean(np.abs(calibration.won - calibration.fair))),
            "market_ask_mae": float(np.mean(np.abs(calibration.won - calibration.cur_ask))),
            "avg_model_minus_ask": float(np.mean(calibration.fair - calibration.cur_ask)),
            "status": "EXPLORATORY" if joined < MIN_QUOTE_TRAIN_ROUNDS else "TESTABLE",
        }])
    else:
        cal_df = pd.DataFrame()
        fair_metrics = pd.DataFrame()
    ev_df = pd.DataFrame(ev_rows)
    pd.DataFrame([status]).to_csv(out / "quote_data_status.csv", index=False)
    ev_df.to_csv(out / "quote_ev_metrics.csv", index=False)
    exit_df.to_csv(out / "quote_exit_metrics.csv", index=False)
    fill_df.to_csv(out / "quote_fill_metrics.csv", index=False)
    cal_df.to_csv(out / "quote_calibration.csv", index=False)
    fair_metrics.to_csv(out / "quote_fair_value_metrics.csv", index=False)
    log.info("quote heads source=%s joined=%d train_gate=%s promotion_gate=%s", source, joined,
             status["training_gate"], status["promotion_gate"])
    return status, ev_df, exit_df, fill_df, cal_df, fair_metrics


def write_report(out, paths, events, class_metrics, quantile_metrics, failure_metrics,
                 quote_status, ev, exits, fills, fair_metrics):
    ensembles = class_metrics[class_metrics.model == "ENSEMBLE_TOP3_ISOTONIC"].copy()
    selected = ensembles[["family", "target", "test_n", "auc", "brier", "signal_n",
                          "signal_precision", "signal_wilson_lb", "base_rate", "members"]]
    interval = quantile_metrics[quantile_metrics["quantile"].astype(str).str.contains("10_90")]
    lines = [
        "# Decision-Head Research Scorecard", "",
        f"Version: `{VERSION}`", "",
        "## Boundary", "",
        "BTC path heads use the causal 180-day 64/16/20 split. Quote-dependent results use only actual "
        "recorder quotes and official settlements. Missing quote history is never synthesized.", "",
        "## Coverage", "",
        f"- Path rounds: {len(paths):,}",
        f"- Causal primary-touch events: {len(events):,}",
        f"- Trustworthy quote+settlement rounds: {quote_status['joined_settled_rounds']}",
        f"- Quote training gate ({MIN_QUOTE_TRAIN_ROUNDS} rounds): **{quote_status['training_gate']}**",
        f"- Quote promotion gate ({MIN_QUOTE_PROMOTION_ROUNDS} rounds): **{quote_status['promotion_gate']}**", "",
        "## Classification Heads", "",
        anchor.markdown_table(selected.round(4)), "",
        "## Quantile Path Heads", "",
        anchor.markdown_table(interval.round(4)), "",
        "## Model-Failure / Skip Head", "",
        anchor.markdown_table(failure_metrics.round(4)), "",
        "## Recorder Net-EV", "",
        anchor.markdown_table(ev.round(4)), "",
        "## Fair Share Price", "",
        anchor.markdown_table(fair_metrics.round(4)), "",
        "## Exit Opportunity", "",
        (anchor.markdown_table(exits.groupby(["horizon", "buffer", "slippage"]).agg(
            signals=("slug", "count"), profitable_exit_rate=("profitable_exit_quote", "mean"),
            avg_price_only_pnl=("price_only_exit_pnl", "mean")).reset_index().round(4))
         if len(exits) else "No eligible exit rows."), "",
        "## Fill Diagnostics", "",
        anchor.markdown_table(fills.round(4)), "",
        "## Interpretation Rules", "",
        "- `cascade_proxy` is a BTC price/volume proxy, not observed liquidation data.",
        "- Quantile heads are useful only when 10-90% coverage is near 80% on untouched test.",
        "- A model-failure head helps only if retained accuracy improves with a useful retention rate.",
        "- Net EV, fair price and exit opportunity remain exploratory until the quote gate passes.",
        "- Taker depth is observable; passive fill probability is blocked without order/trade matching.",
        "- Nothing in this report is a live-betting approval.", "",
        "## Files", "",
        "`classification_metrics.csv`, `classification_predictions.csv`, `quantile_metrics.csv`, "
        "`quantile_predictions.csv`, `model_failure_metrics.csv`, `model_failure_predictions.csv`, "
        "`quote_data_status.csv`, `quote_ev_metrics.csv`, `quote_exit_metrics.csv`, "
        "`quote_fill_metrics.csv`, `quote_calibration.csv`, `quote_fair_value_metrics.csv`, "
        "`path_labels.parquet`, "
        "`primary_touch_events.parquet`, `config.json`, and `run.log`.",
    ]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def run(args) -> int:
    out = Path(args.output_dir) if args.output_dir else ROOT / "data" / "research" / "decision_heads_180d_30s"
    log = setup_log(out)
    started = time.time()
    anchor_dir = Path(args.anchor_dir) if args.anchor_dir else ROOT / "data" / "research" / "anchor_roundtrip_180d_30s"
    open_path = anchor_dir / "open_rounds.parquet"
    if not open_path.exists():
        raise SystemExit(f"Missing prerequisite: {open_path}. Run run_180d_anchor_roundtrip_strategy.bat first.")
    open_df = pd.read_parquet(open_path)
    feature_contract = json.loads((anchor_dir / "feature_names.json").read_text(encoding="utf-8"))
    features = feature_contract["open_70"]
    if len(features) != 70 or open_df[features].isna().any().any():
        raise SystemExit("Invalid 70-feature prerequisite dataset.")
    source_cfg = json.loads((anchor_dir / "config.json").read_text(encoding="utf-8"))
    cfg = anchor.Config(days=int(source_cfg.get("days", 180)), interval=source_cfg.get("interval", "30s"),
                        threads=args.threads, max_train_rows=args.max_train_rows, quick=args.quick,
                        offline=True, output_dir=str(out))
    if cfg.interval != "30s":
        raise SystemExit("Decision-head v1 currently requires the validated 30s anchor dataset.")
    log.info("START version=%s rounds=%s quick=%s", VERSION, f"{len(open_df):,}", args.quick)
    bars = anchor.load_bars(cfg, log)
    paths, events = build_path_labels(open_df, bars, features, log)
    del bars
    gc.collect()
    paths.to_parquet(out / "path_labels.parquet", index=False)
    events.to_parquet(out / "primary_touch_events.parquet", index=False)
    class_metrics, _ = run_classification_heads(paths, events, features, cfg, out, log)
    quantile_metrics, _ = run_quantile_heads(paths, features, cfg, out, log)
    failure_metrics, _ = run_failure_heads(paths, features, cfg, out, log)
    quote_status, ev, exits, fills, _, fair_metrics = evaluate_quote_heads(out, log)
    (out / "config.json").write_text(json.dumps({"version": VERSION, "source": str(anchor_dir),
                                                   "config": asdict(cfg)}, indent=2), encoding="utf-8")
    write_report(out, paths, events, class_metrics, quantile_metrics, failure_metrics,
                 quote_status, ev, exits, fills, fair_metrics)
    log.info("DONE elapsed=%.1f minutes output=%s", (time.time() - started) / 60, out)
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchor-dir", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--max-train-rows", type=int, default=120_000)
    parser.add_argument("--quick", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
