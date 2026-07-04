"""Existing-data BTC path-dynamics research.

Tests non-duplicate targets using only historical files already on disk:
competing first-touch side/time, post-touch continuation, reversal depth,
next-round path-opportunity arrival, aggressive-flow bursts, and Binance
spot/perpetual flow propagation. Research-only; never changes live models.
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
ROOT = BACKEND.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND))

import test_180d_anchor_roundtrip_strategy as anchor  # noqa: E402

VERSION = "2026-07-01-path-dynamics-v1"
EXTENSIONS = (10, 20)
RETRACE_LEVELS = (25, 50, 75, 100)
TIME_BUCKETS = ((30, "30S"), (60, "60S"), (120, "120S"))
FLOW_FEATURES = [
    "cvd_change", "cvd_1m", "cvd_5m", "vpin", "large_trade_delta",
    "large_trade_imbalance", "funding_velocity", "cvd_spot", "cvd_perp",
    "cvd_divergence", "perp_spot_basis_bps", "vol_spot", "vol_perp",
]


def setup_log(out: Path) -> logging.Logger:
    out.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("path-dynamics")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    for handler in (logging.StreamHandler(sys.stdout),
                    logging.FileHandler(out / "run.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    return log


def hit_index(values: np.ndarray, condition) -> int | None:
    hits = np.where(condition(values))[0]
    return int(hits[0]) if len(hits) else None


def first_passage(after_hi, after_lo, anchor_price, touch_level, stop_level, side_up, extension):
    """Conservative ordering: same-bar continuation+reversal/stop is not a win."""
    for k in range(len(after_hi)):
        continuation = after_hi[k] >= touch_level + extension if side_up else after_lo[k] <= touch_level - extension
        anchor_revert = after_lo[k] <= anchor_price if side_up else after_hi[k] >= anchor_price
        stop = after_hi[k] >= stop_level if side_up else after_lo[k] <= stop_level
        if continuation and (anchor_revert or stop):
            return 0
        if anchor_revert or stop:
            return 0
        if continuation:
            return 1
    return 0


def build_dynamics(paths, bars, features, log):
    ts = bars.ts_ms.to_numpy(np.int64)
    hi = bars.high.to_numpy(float)
    lo = bars.low.to_numpy(float)
    volume = bars.volume.to_numpy(float)
    taker = bars.taker_buy.to_numpy(float)
    index = {int(value): i for i, value in enumerate(ts)}
    round_rows, event_rows = [], []
    for n, row in enumerate(paths.itertuples(index=False), 1):
        horizon = int(row.horizon)
        start = int(row.round_start)
        i = index.get(start)
        width = horizon * 2
        if i is None or i + width > len(bars):
            continue
        ph, pl = hi[i:i + width], lo[i:i + width]
        pv, pt = volume[i:i + width], taker[i:i + width]
        price = float(row.anchor)
        barrier = float(anchor.PRIMARY[horizon])
        up = hit_index(ph, lambda x: x >= price + barrier)
        down = hit_index(pl, lambda x: x <= price - barrier)
        candidates = [value for value in (up, down) if value is not None]
        record = row._asdict()
        if not candidates:
            first_side = "NONE"
            first_index = None
        else:
            first_index = min(candidates)
            if up == first_index and down == first_index:
                first_side = "AMBIGUOUS"
            else:
                first_side = "UP" if up == first_index else "DOWN"
        record["first_touch_side"] = first_side
        record["first_touch_up"] = int(first_side == "UP")
        record["first_touch_down"] = int(first_side == "DOWN")
        record["first_touch_none"] = int(first_side == "NONE")
        seconds = (first_index + 1) * 30 if first_index is not None else horizon * 60 + 30
        time_label = "NONE"
        if first_index is not None:
            time_label = "LATE"
            for limit, label in TIME_BUCKETS:
                if seconds <= limit:
                    time_label = label
                    break
        record["first_touch_time_bucket"] = time_label
        record["first_touch_joint"] = (f"{first_side}_{time_label}" if first_side in ("UP", "DOWN")
                                         else first_side)

        total_volume = float(np.sum(pv))
        taker_imbalance = 2 * float(np.sum(pt)) / (total_volume + anchor.EPS) - 1
        active = float(row.future_volume_ratio) >= 1.25
        record["future_taker_imbalance"] = taker_imbalance
        record["flow_burst_up"] = int(active and taker_imbalance >= 0.08)
        record["flow_burst_down"] = int(active and taker_imbalance <= -0.08)
        record["flow_burst_any"] = int(record["flow_burst_up"] or record["flow_burst_down"])
        round_rows.append(record)

        if first_side in ("UP", "DOWN"):
            t = int(first_index)
            side_up = first_side == "UP"
            touch_level = price + barrier if side_up else price - barrier
            stop_level = price + 2 * barrier if side_up else price - 2 * barrier
            entry_ambiguous = (pl[t] <= price or ph[t] >= stop_level) if side_up else (
                ph[t] >= price or pl[t] <= stop_level)
            if not entry_ambiguous:
                after_hi, after_lo = ph[t + 1:], pl[t + 1:]
                if len(after_hi):
                    continuation = (float(np.max(after_hi) - touch_level) if side_up else
                                    float(touch_level - np.min(after_lo)))
                    retrace = (float(touch_level - np.min(after_lo)) if side_up else
                               float(np.max(after_hi) - touch_level))
                else:
                    continuation = retrace = 0.0
                event = {name: record[name] for name in features}
                event.update({
                    "round_id": row.round_id, "round_start": start, "horizon": horizon,
                    "touch_side_up": int(side_up), "touch_time_s": seconds,
                    "seconds_left": horizon * 60 - seconds,
                    "max_continuation_usd": max(0.0, continuation),
                    "max_retrace_fraction": max(0.0, retrace / barrier),
                })
                for extension in EXTENSIONS:
                    event[f"continue_{extension}_before_revert"] = first_passage(
                        after_hi, after_lo, price, touch_level, stop_level, side_up, extension)
                # Partial retrace must happen before the stop; same-bar stop wins conservatively.
                reached = {level: 0 for level in RETRACE_LEVELS}
                for k in range(len(after_hi)):
                    stop = after_hi[k] >= stop_level if side_up else after_lo[k] <= stop_level
                    if stop:
                        break
                    depth = (touch_level - after_lo[k]) / barrier if side_up else (
                        after_hi[k] - touch_level) / barrier
                    for level in RETRACE_LEVELS:
                        if depth >= level / 100:
                            reached[level] = 1
                for level, value in reached.items():
                    event[f"retrace_{level}_before_stop"] = value
                event_rows.append(event)
        if n % 10_000 == 0:
            log.info("dynamics labels %s/%s", f"{n:,}", f"{len(paths):,}")

    rounds = pd.DataFrame(round_rows).sort_values("round_start").reset_index(drop=True)
    events = pd.DataFrame(event_rows).sort_values("round_start").reset_index(drop=True)
    # Predict opportunity in the following same-horizon round. Outcome end spans two rounds.
    pieces = []
    for horizon, frame in rounds.groupby("horizon", sort=False):
        frame = frame.sort_values("round_start").copy()
        frame["next_primary_touch"] = ((frame.first_touch_up + frame.first_touch_down).shift(-1))
        frame["next_roundtrip"] = frame[f"roundtrip_{int(anchor.PRIMARY[int(horizon)])}"].shift(-1)
        frame["next_high_activity"] = frame.high_activity.shift(-1)
        frame["next_outcome_end"] = frame.round_start.shift(-1) + int(horizon) * 60_000
        pieces.append(frame.iloc[:-1])
    rounds = pd.concat(pieces, ignore_index=True).sort_values("round_start").reset_index(drop=True)
    log.info("dynamics complete rounds=%s events=%s", f"{len(rounds):,}", f"{len(events):,}")
    return rounds, events


def load_flow_features(rounds, events, log):
    cross = pd.read_parquet(ROOT / "data" / "crossvenue_flow.parquet")
    trade = pd.read_parquet(ROOT / "data" / "trade_features_backfill.parquet").rename(
        columns={"candle_ts": "ts_ms"})
    flow = cross.merge(trade, on="ts_ms", how="outer").sort_values("ts_ms")
    flow = flow[["ts_ms"] + FLOW_FEATURES].drop_duplicates("ts_ms", keep="last")

    def attach(frame):
        result = frame.copy()
        result["flow_join_ts"] = result.round_start.astype("int64") - 60_000
        result = result.merge(flow, left_on="flow_join_ts", right_on="ts_ms", how="left")
        result = result.drop(columns=["ts_ms"])
        return result

    rounds = attach(rounds)
    events = attach(events)
    coverage = float(rounds[FLOW_FEATURES].notna().all(axis=1).mean())
    log.info("flow feature complete-row coverage %.2f%%", coverage * 100)
    return rounds, events, coverage


def split_masks(frame, target=""):
    starts = np.sort(frame.round_start.unique())
    train_cut = starts[int(len(starts) * 0.64)]
    test_cut = starts[int(len(starts) * 0.80)]
    target_end = f"{target}_outcome_end"
    if target_end in frame:
        outcome_end = frame[target_end]
    elif target.startswith("next_") and "next_outcome_end" in frame:
        outcome_end = frame["next_outcome_end"]
    else:
        outcome_end = frame.round_start + frame.horizon * 60_000
    train = (frame.round_start < train_cut) & (outcome_end <= train_cut)
    valid = ((frame.round_start >= train_cut) & (frame.round_start < test_cut) &
             (outcome_end <= test_cut))
    test = frame.round_start >= test_cut
    return train.to_numpy(), valid.to_numpy(), test.to_numpy()


def evaluate_binary(frame, columns, target, family, variant, cfg, log):
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import roc_auc_score

    data = frame.dropna(subset=[target] + columns).reset_index(drop=True)
    train, valid, test = split_masks(data, target)
    train_idx = anchor.cap_rows(train, cfg.max_train_rows)
    x = data[columns].to_numpy(np.float32)
    y = data[target].to_numpy(int)
    if any(len(np.unique(y[mask])) < 2 for mask in (train_idx, np.where(valid)[0], np.where(test)[0])):
        return None
    val_probs, test_probs, model_rows = {}, {}, []
    for name, factory in anchor.classifier_factories(cfg).items():
        model = None
        started = time.time()
        try:
            model = factory()
            model.fit(x[train_idx], y[train_idx])
            pv = np.asarray(model.predict_proba(x[valid]))[:, 1]
            pt = np.asarray(model.predict_proba(x[test]))[:, 1]
            val_probs[name], test_probs[name] = pv, pt
            model_rows.append({"model": name, "validation_auc": roc_auc_score(y[valid], pv),
                               "elapsed_sec": time.time() - started})
        finally:
            del model
            gc.collect()
    ranked = sorted(val_probs, key=lambda name: next(row["validation_auc"] for row in model_rows
                                                     if row["model"] == name), reverse=True)[:3]
    pv = np.mean([val_probs[name] for name in ranked], axis=0)
    pt = np.mean([test_probs[name] for name in ranked], axis=0)
    iso = IsotonicRegression(out_of_bounds="clip").fit(pv, y[valid])
    pvc, ptc = iso.transform(pv), iso.transform(pt)
    threshold = anchor.choose_threshold(y[valid], pvc)
    result = {"family": family, "variant": variant, "target": target,
              "model": "ENSEMBLE_TOP3_ISOTONIC", "members": "+".join(ranked),
              "validation_auc": roc_auc_score(y[valid], pvc), "test_n": int(test.sum()),
              **anchor.classification_metrics(y[test], ptc, threshold)}
    pred = data.loc[test, ["round_id", "round_start", "horizon"]].copy()
    pred["family"], pred["variant"], pred["target"] = family, variant, target
    pred["actual"], pred["probability"], pred["signal"] = y[test], ptc, ptc >= threshold
    return result, pred


def evaluate_multiclass(frame, columns, target, family, cfg, log):
    from sklearn.metrics import (accuracy_score, balanced_accuracy_score, f1_score,
                                 log_loss, precision_recall_fscore_support, roc_auc_score)
    from sklearn.preprocessing import LabelEncoder

    data = frame.dropna(subset=[target] + columns).reset_index(drop=True)
    data = data[data[target] != "AMBIGUOUS"].reset_index(drop=True)
    train, valid, test = split_masks(data, target)
    train_idx = anchor.cap_rows(train, cfg.max_train_rows)
    encoder = LabelEncoder().fit(data[target])
    x = data[columns].to_numpy(np.float32)
    y = encoder.transform(data[target])
    val_probs, test_probs, scores = {}, {}, {}
    for name, factory in anchor.classifier_factories(cfg).items():
        model = None
        try:
            model = factory()
            model.fit(x[train_idx], y[train_idx])
            pv, pt = np.asarray(model.predict_proba(x[valid])), np.asarray(model.predict_proba(x[test]))
            val_probs[name], test_probs[name] = pv, pt
            scores[name] = roc_auc_score(y[valid], pv, multi_class="ovr", average="macro")
        except Exception as exc:
            log.warning("multiclass model failed %s/%s: %s", target, name, exc)
        finally:
            del model
            gc.collect()
    ranked = sorted(scores, key=scores.get, reverse=True)[:3]
    probability = np.mean([test_probs[name] for name in ranked], axis=0)
    probability = np.clip(probability, 1e-12, None)
    probability = probability / probability.sum(axis=1, keepdims=True)
    predicted = np.argmax(probability, axis=1)
    precision, recall, _, support = precision_recall_fscore_support(
        y[test], predicted, labels=np.arange(len(encoder.classes_)), zero_division=0)
    result = {"family": family, "target": target, "members": "+".join(ranked),
              "test_n": int(test.sum()), "macro_auc": roc_auc_score(
                  y[test], probability, multi_class="ovr", average="macro"),
              "accuracy": accuracy_score(y[test], predicted),
              "balanced_accuracy": balanced_accuracy_score(y[test], predicted),
              "macro_f1": f1_score(y[test], predicted, average="macro"),
              "logloss": log_loss(y[test], probability),
              "class_metrics_json": json.dumps({label: {"precision": float(precision[i]),
                                                          "recall": float(recall[i]),
                                                          "support": int(support[i])}
                                                for i, label in enumerate(encoder.classes_)})}
    return result


def evaluate_regression(frame, columns, target, family, variant, cfg):
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    data = frame.dropna(subset=[target] + columns).reset_index(drop=True)
    train, valid, test = split_masks(data, target)
    train_idx = anchor.cap_rows(train, cfg.max_train_rows)
    x = data[columns].to_numpy(np.float32)
    y = data[target].to_numpy(float)
    val_predictions, test_predictions, validation_mae = {}, {}, {}
    for name, factory in anchor.regressor_factories(cfg).items():
        model = None
        try:
            model = factory()
            model.fit(x[train_idx], y[train_idx])
            pv, pt = np.asarray(model.predict(x[valid])), np.asarray(model.predict(x[test]))
            val_predictions[name], test_predictions[name] = pv, pt
            validation_mae[name] = mean_absolute_error(y[valid], pv)
        finally:
            del model
            gc.collect()
    members = sorted(validation_mae, key=validation_mae.get)[:3]
    prediction = np.mean([test_predictions[name] for name in members], axis=0)
    result = {"family": family, "variant": variant, "target": target,
              "members": "+".join(members), "test_n": int(test.sum()),
              "mae": float(mean_absolute_error(y[test], prediction)),
              "rmse": float(mean_squared_error(y[test], prediction) ** 0.5),
              "r2": float(r2_score(y[test], prediction)),
              "baseline_median_mae": float(mean_absolute_error(
                  y[test], np.full(int(test.sum()), np.median(y[train_idx]))))}
    return result


def run_flow_propagation(out, log):
    flow = pd.read_parquet(ROOT / "data" / "crossvenue_flow.parquet").sort_values("ts_ms").reset_index(drop=True)
    cut = int(len(flow) * 0.70)
    train, test = flow.iloc[:cut], flow.iloc[cut:].copy()
    rows = []
    for leader, follower in (("cvd_perp", "cvd_spot"), ("cvd_spot", "cvd_perp")):
        threshold = float(train[leader].abs().quantile(0.90))
        for horizon in (1, 3, 5):
            future = sum(test[follower].shift(-step) for step in range(1, horizon + 1))
            candidate = np.where((test[leader].abs() >= threshold) & future.notna())[0]
            selected, last = [], -horizon
            for idx in candidate:
                if idx - last >= horizon:
                    selected.append(idx)
                    last = idx
            if not selected:
                continue
            lead = test[leader].to_numpy()[selected]
            follow = future.to_numpy()[selected]
            correct = np.sign(lead) == np.sign(follow)
            own_flow = test[follower].to_numpy()[selected]
            own_correct = np.sign(own_flow) == np.sign(follow)
            n, wins = len(correct), int(correct.sum())
            rows.append({"leader": leader, "follower": follower, "horizon_minutes": horizon,
                         "event_threshold": threshold, "independent_events": n,
                         "follow_rate": wins / n, "wilson_lb": anchor.wilson_lb(wins, n),
                         "follower_own_persistence": float(np.mean(own_correct)),
                         "incremental_follow_pp": float((np.mean(correct) - np.mean(own_correct)) * 100),
                         "signed_correlation": float(np.corrcoef(lead, follow)[0, 1])})
    result = pd.DataFrame(rows)
    result.to_csv(out / "spot_perp_flow_propagation.csv", index=False)
    log.info("flow propagation rows=%d", len(result))
    return result


def write_report(out, coverage, binary_metrics, multiclass_metrics, regression_metrics,
                 propagation, unavailable):
    b = pd.DataFrame(binary_metrics)
    m = pd.DataFrame(multiclass_metrics)
    r = pd.DataFrame(regression_metrics)
    lines = ["# Existing-Data Path Dynamics", "", f"Version: `{VERSION}`", "",
             "## Boundary", "",
             "Only historical data already present before this run was used. No Polymarket forward-recorder "
             "rows were used and unavailable venue/oracle/liquidation history was not synthesized.", "",
             f"Flow-feature complete-row coverage: **{coverage:.2%}**", "",
             "## Binary Specialist Heads", "", anchor.markdown_table(b.round(4)), "",
             "## Competing-Risk Multiclass Heads", "", anchor.markdown_table(m.round(4)), "",
             "## Continuous Path/Flow Heads", "", anchor.markdown_table(r.round(4)), "",
             "## Spot/Perpetual Flow Propagation", "", anchor.markdown_table(propagation.round(4)), "",
             "## Unavailable Or Already Concluded", ""]
    lines += [f"- {item}" for item in unavailable]
    lines += ["", "## Interpretation", "",
              "- First-touch side must beat a majority/base classifier, not merely report high raw accuracy.",
              "- Continuation and retrace labels are conservative: same-bar ordering never receives a win.",
              "- Next-round opportunity means a path event, not guaranteed Polymarket profit.",
              "- Spot/perpetual flow propagation is not the same as executable cross-exchange arbitrage.",
              "- No result is a live-betting approval."]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def run(args):
    out = Path(args.output_dir) if args.output_dir else ROOT / "data" / "research" / "path_dynamics_180d_30s"
    log = setup_log(out)
    started = time.time()
    decision_dir = ROOT / "data" / "research" / "decision_heads_180d_30s"
    anchor_dir = ROOT / "data" / "research" / "anchor_roundtrip_180d_30s"
    paths = pd.read_parquet(decision_dir / "path_labels.parquet")
    features = json.loads((anchor_dir / "feature_names.json").read_text(encoding="utf-8"))["open_70"]
    cfg_json = json.loads((anchor_dir / "config.json").read_text(encoding="utf-8"))
    cfg = anchor.Config(days=int(cfg_json.get("days", 180)), interval="30s", threads=args.threads,
                        max_train_rows=args.max_train_rows, quick=args.quick, offline=True,
                        output_dir=str(out))
    bars = anchor.load_bars(cfg, log)
    rounds, events = build_dynamics(paths, bars, features, log)
    del bars
    gc.collect()
    rounds, events, coverage = load_flow_features(rounds, events, log)
    rounds.to_parquet(out / "round_dynamics.parquet", index=False)
    events.to_parquet(out / "touch_dynamics.parquet", index=False)

    binary_specs = []
    for horizon in cfg.horizons:
        round_frame = rounds[rounds.horizon == horizon].copy()
        event_frame = events[events.horizon == horizon].copy()
        for target in ("first_touch_up", "first_touch_down", "flow_burst_up", "flow_burst_down",
                       "flow_burst_any", "next_primary_touch", "next_roundtrip", "next_high_activity"):
            binary_specs.append((round_frame, target, f"round_{horizon}m"))
        for target in ("continue_10_before_revert", "continue_20_before_revert",
                       "retrace_25_before_stop", "retrace_50_before_stop",
                       "retrace_75_before_stop", "retrace_100_before_stop"):
            binary_specs.append((event_frame, target, f"post_touch_{horizon}m"))
    if args.quick:
        keep = {"first_touch_up", "flow_burst_any", "next_primary_touch",
                "continue_10_before_revert", "retrace_50_before_stop"}
        binary_specs = [spec for spec in binary_specs if spec[1] in keep]

    metrics, predictions = [], []
    for number, (frame, target, family) in enumerate(binary_specs, 1):
        log.info("binary target %d/%d %s", number, len(binary_specs), target)
        comparison_frame = frame.dropna(subset=FLOW_FEATURES).reset_index(drop=True)
        for variant, columns in (("base70", features), ("base70_plus_flow", features + FLOW_FEATURES)):
            result = evaluate_binary(comparison_frame, columns, target, family, variant, cfg, log)
            if result:
                metric, pred = result
                metrics.append(metric)
                predictions.append(pred)
    metric_df = pd.DataFrame(metrics)
    pred_df = pd.concat(predictions, ignore_index=True)
    metric_df.to_csv(out / "binary_metrics.csv", index=False)
    pred_df.to_csv(out / "binary_predictions.csv", index=False)

    multiclass = []
    for horizon in cfg.horizons:
        frame = rounds[rounds.horizon == horizon].copy()
        for target in ("first_touch_side", "first_touch_time_bucket", "first_touch_joint"):
            if args.quick and target != "first_touch_side":
                continue
            comparison_frame = frame.dropna(subset=FLOW_FEATURES).reset_index(drop=True)
            for variant, columns in (("base70", features),
                                     ("base70_plus_flow", features + FLOW_FEATURES)):
                result = evaluate_multiclass(comparison_frame, columns, target,
                                             f"{horizon}m_competing_risk", cfg, log)
                if result:
                    result["variant"] = variant
                    multiclass.append(result)
    multiclass_df = pd.DataFrame(multiclass)
    multiclass_df.to_csv(out / "multiclass_metrics.csv", index=False)

    regression = []
    for horizon in cfg.horizons:
        for frame, target, family in (
                (rounds[rounds.horizon == horizon], "future_taker_imbalance", f"round_{horizon}m"),
                (events[events.horizon == horizon], "max_continuation_usd", f"post_touch_{horizon}m"),
                (events[events.horizon == horizon], "max_retrace_fraction", f"post_touch_{horizon}m")):
            comparison_frame = frame.dropna(subset=FLOW_FEATURES).reset_index(drop=True)
            for variant, columns in (("base70", features),
                                     ("base70_plus_flow", features + FLOW_FEATURES)):
                regression.append(evaluate_regression(comparison_frame, columns, target, family,
                                                      variant, cfg))
    regression_df = pd.DataFrame(regression)
    regression_df.to_csv(out / "regression_metrics.csv", index=False)
    propagation = run_flow_propagation(out, log)
    unavailable = [
        "Coinbase/Bybit/OKX price leadership: no synchronized bulk historical ticker file.",
        "Settlement-oracle divergence: no historical Pyth/Chainlink tick archive.",
        "True liquidation cascade: no historical liquidation-event stream; price/volume proxy already rejected.",
        "Passive fill and queue position: no order submission/trade matching history.",
        "Binance futures depth direction: already tested on seven cached days at AUC 0.51-0.54; not duplicated.",
        "Generic candle+flow direction uplift: already tested and negative; this run tests new path labels instead.",
    ]
    write_report(out, coverage, metrics, multiclass, regression, propagation, unavailable)
    (out / "config.json").write_text(json.dumps({"version": VERSION, "quick": args.quick,
                                                   "threads": args.threads,
                                                   "max_train_rows": args.max_train_rows}, indent=2),
                                      encoding="utf-8")
    log.info("DONE elapsed=%.1f minutes output=%s", (time.time() - started) / 60, out)
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--max-train-rows", type=int, default=120_000)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--output-dir", default="")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
