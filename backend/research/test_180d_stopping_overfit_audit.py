"""Walk-forward overfitting audit for the TP50/SL10 BTC-path policy.

Regenerates exclusive first-touch-side signals in expanding chronological folds,
selects an exit policy only on each fold's validation era, and evaluates all
policies on the next untouched era. Research only; no live models are changed.
"""
from __future__ import annotations

import argparse
import gc
import itertools
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
import test_180d_path_dynamics as dynamics  # noqa: E402
import test_180d_round_state_and_stopping as stopping  # noqa: E402


VERSION = "2026-07-01-stopping-overfit-v1"
DEFAULT_OUT = ROOT / "data" / "research" / "stopping_overfit_audit_180d"
FIXED_POLICY = ("BARRIER", 50, 10)
POLICIES = ([('TIME', seconds) for seconds in (30, 60, 120, 9999)] +
            [('BARRIER', tp, sl) for tp in (10, 20, 30, 50) for sl in (10, 20, 30, 50)])
MEMBERS = {
    (5, "first_touch_up"): ("catboost", "histgb", "lightgbm"),
    (5, "first_touch_down"): ("catboost", "lightgbm", "histgb"),
    (15, "first_touch_up"): ("logreg", "random_forest", "extra_trees"),
    (15, "first_touch_down"): ("catboost", "extra_trees", "random_forest"),
}


def setup_log(out):
    out.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("stopping-overfit")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(out / "run.log", encoding="utf-8")):
        handler.setFormatter(formatter)
        log.addHandler(handler)
    return log


def policy_name(policy):
    return ":".join(str(value) for value in policy)


def make_folds(frame, fold_days=20, minimum_train_days=60, minimum_partial_days=10):
    start = pd.to_datetime(int(frame.round_start.min()), unit="ms", utc=True).floor("D")
    end = pd.to_datetime(int(frame.round_start.max()), unit="ms", utc=True).ceil("D")
    folds = []
    test_start = start + pd.Timedelta(days=minimum_train_days + fold_days)
    number = 1
    while test_start + pd.Timedelta(days=fold_days) <= end + pd.Timedelta(seconds=1):
        folds.append({
            "fold": number,
            "train_start": int(start.timestamp() * 1000),
            "validation_start": int((test_start - pd.Timedelta(days=fold_days)).timestamp() * 1000),
            "test_start": int(test_start.timestamp() * 1000),
            "test_end": int((test_start + pd.Timedelta(days=fold_days)).timestamp() * 1000),
        })
        number += 1
        test_start += pd.Timedelta(days=fold_days)
    remaining_days = (end - test_start).total_seconds() / 86_400
    if remaining_days >= minimum_partial_days:
        folds.append({
            "fold": number,
            "train_start": int(start.timestamp() * 1000),
            "validation_start": int((test_start - pd.Timedelta(days=fold_days)).timestamp() * 1000),
            "test_start": int(test_start.timestamp() * 1000),
            "test_end": int(end.timestamp() * 1000),
        })
    return folds


def masks_for_fold(data, fold):
    outcome_end = data.round_start + data.horizon * 60_000
    train = ((data.round_start >= fold["train_start"]) &
             (data.round_start < fold["validation_start"]) &
             (outcome_end <= fold["validation_start"]))
    valid = ((data.round_start >= fold["validation_start"]) &
             (data.round_start < fold["test_start"]) &
             (outcome_end <= fold["test_start"]))
    test = ((data.round_start >= fold["test_start"]) &
            (data.round_start < fold["test_end"]) &
            (outcome_end <= fold["test_end"]))
    return train.to_numpy(), valid.to_numpy(), test.to_numpy()


def fit_head(data, features, target, horizon, masks, cfg, log, fold_number):
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import roc_auc_score

    train, valid, test = masks
    train_idx = anchor.cap_rows(train, cfg.max_train_rows)
    valid_idx, test_idx = np.flatnonzero(valid), np.flatnonzero(test)
    x = data[features].to_numpy(np.float32)
    y = data[target].to_numpy(int)
    if any(len(index) == 0 or len(np.unique(y[index])) < 2 for index in (train_idx, valid_idx, test_idx)):
        raise ValueError(f"one-class/empty partition for h={horizon} target={target} fold={fold_number}")
    factories = anchor.classifier_factories(cfg)
    requested = MEMBERS[(horizon, target)]
    validation_probabilities, test_probabilities, used = [], [], []
    for model_name in requested:
        if model_name not in factories:
            log.warning("model unavailable fold=%d h=%d target=%s model=%s", fold_number, horizon, target, model_name)
            continue
        started = time.time()
        model = factories[model_name]()
        try:
            log.info("[FOLD %d] h=%dm target=%s model=%s train=%d", fold_number, horizon,
                     target, model_name, len(train_idx))
            model.fit(x[train_idx], y[train_idx])
            validation_probabilities.append(np.asarray(model.predict_proba(x[valid_idx]))[:, 1])
            test_probabilities.append(np.asarray(model.predict_proba(x[test_idx]))[:, 1])
            used.append(model_name)
            log.info("[FOLD %d] model=%s complete %.1fs", fold_number, model_name, time.time() - started)
        finally:
            del model
            gc.collect()
    if len(used) < 2:
        raise RuntimeError(f"fewer than two fixed members available for h={horizon} target={target}")
    raw_valid = np.mean(validation_probabilities, axis=0)
    raw_test = np.mean(test_probabilities, axis=0)
    calibrator = IsotonicRegression(out_of_bounds="clip").fit(raw_valid, y[valid_idx])
    calibrated_valid = calibrator.transform(raw_valid)
    calibrated_test = calibrator.transform(raw_test)
    threshold = anchor.choose_threshold(y[valid_idx], calibrated_valid)
    return {
        "target": target, "members": "+".join(used), "threshold": threshold,
        "validation_auc": float(roc_auc_score(y[valid_idx], calibrated_valid)),
        "test_auc": float(roc_auc_score(y[test_idx], calibrated_test)),
        "validation_idx": valid_idx, "test_idx": test_idx,
        "validation_probability": calibrated_valid, "test_probability": calibrated_test,
    }


def combine_signals(data, up, down, split):
    index_key = f"{split}_idx"
    probability_key = f"{split}_probability"
    indices = up[index_key]
    if not np.array_equal(indices, down[index_key]):
        raise AssertionError("UP/DOWN prediction rows are misaligned")
    result = data.iloc[indices][[
        "round_id", "round_start", "horizon", "anchor", "first_touch_up", "first_touch_down"
    ]].copy()
    result["up_probability"] = up[probability_key]
    result["down_probability"] = down[probability_key]
    result["up_signal"] = result.up_probability >= up["threshold"]
    result["down_signal"] = result.down_probability >= down["threshold"]
    result["signal_state"] = np.select(
        [result.up_signal & result.down_signal, result.up_signal, result.down_signal],
        ["DUAL", "UP", "DOWN"], default="NONE")
    exclusive = result[result.up_signal ^ result.down_signal].copy()
    exclusive["side"] = np.where(exclusive.up_signal, 1, -1)
    exclusive["side_correct"] = np.where(
        exclusive.side == 1, exclusive.first_touch_up, exclusive.first_touch_down).astype(int)
    return result, exclusive


def policy_values(signals, bars, bar_index, policy, cost=2.0):
    gross = np.asarray([stopping.policy_pnl(row, bars, bar_index, policy)
                        for row in signals.itertuples(index=False)], dtype=float)
    return gross - float(cost), gross


def policy_metrics(values):
    if not len(values):
        return {"n": 0, "mean": np.nan, "median": np.nan, "win_rate": np.nan,
                "profit_factor": np.nan, "max_drawdown": np.nan, "cvar_5pct": np.nan}
    base = stopping.pnl_metrics(values)
    return {"n": base["signals"], "mean": base["mean_pnl_usd"], "median": base["median_pnl_usd"],
            "win_rate": base["win_rate"], "profit_factor": base["profit_factor"],
            "max_drawdown": base["max_drawdown_usd"], "cvar_5pct": base["cvar_5pct_usd"]}


def pbo_from_folds(policy_frame, horizon):
    frame = policy_frame[policy_frame.horizon == horizon]
    pivot = frame.pivot(index="fold", columns="policy", values="test_mean").dropna(axis=1)
    fold_ids = list(pivot.index)
    rows = []
    if len(fold_ids) < 4 or pivot.shape[1] < 2:
        return pd.DataFrame(), np.nan
    width = len(fold_ids) // 2
    for selected_folds in itertools.combinations(fold_ids, width):
        selected_folds = list(selected_folds)
        held_folds = [fold for fold in fold_ids if fold not in selected_folds]
        development = pivot.loc[selected_folds].mean(axis=0)
        held = pivot.loc[held_folds].mean(axis=0)
        champion = development.idxmax()
        rank = int(held.rank(ascending=False, method="min").loc[champion])
        percentile = rank / len(held)
        rows.append({"horizon": horizon, "development_folds": ",".join(map(str, selected_folds)),
                     "held_folds": ",".join(map(str, held_folds)), "selected_policy": champion,
                     "held_rank": rank, "policy_count": len(held), "held_rank_percentile": percentile,
                     "below_median": int(percentile > 0.5),
                     "development_mean": float(development.loc[champion]),
                     "held_mean": float(held.loc[champion])})
    result = pd.DataFrame(rows)
    return result, float(result.below_median.mean())


def aggregate_results(policy_frame, fold_summary, fixed_trades, selected_trades):
    rows = []
    for horizon in sorted(fold_summary.horizon.unique()):
        fold_sub = fold_summary[fold_summary.horizon == horizon]
        policy_sub = policy_frame[policy_frame.horizon == horizon]
        fixed_policy = policy_name(FIXED_POLICY)
        fixed_fold = policy_sub[policy_sub.policy == fixed_policy]
        _, pbo = pbo_from_folds(policy_frame, horizon)
        for role, ledger in (("FIXED_TP50_SL10", fixed_trades), ("FOLD_SELECTED", selected_trades)):
            trades = ledger[ledger.horizon == horizon].sort_values("round_start")
            for cost in (0.0, 2.0, 5.0):
                values = trades.gross_pnl.to_numpy(float) - cost
                fold_net = trades.assign(net=trades.gross_pnl - cost).groupby("fold").net.mean()
                metrics = policy_metrics(values)
                low, high = stopping.day_block_ci(values, trades.round_start.to_numpy(np.int64))
                rows.append({
                    "horizon": horizon, "role": role, "cost": cost, **metrics,
                    "day_block_ci_low": low, "day_block_ci_high": high,
                    "positive_fold_rate": float((fold_net > 0).mean()),
                    "worst_fold_mean": float(fold_net.min()),
                    "median_test_rank": fixed_fold.test_rank.median() if role == "FIXED_TP50_SL10" else
                    fold_sub.selected_test_rank.median(),
                    "policy_pbo": pbo,
                })
    return pd.DataFrame(rows)


def markdown_table(frame):
    return anchor.markdown_table(frame)


def write_report(out, fold_summary, aggregate, policy_aggregate, pbo_rows):
    fixed = aggregate[(aggregate.role == "FIXED_TP50_SL10") & (aggregate.cost == 2.0)]
    passes = fixed[(fixed.positive_fold_rate >= 0.8) & (fixed.day_block_ci_low > 0)
                   & (fixed.policy_pbo <= 0.2)]
    champion_counts = fold_summary.groupby(["horizon", "selected_policy"]).size().rename("folds").reset_index()
    fold_count = int(fold_summary.fold.nunique())
    lines = [
        "# TP50/SL10 Walk-Forward Overfitting Audit", "", f"Version: `{VERSION}`", "",
        "## Verdict", "",
        ("The fixed policy passed the strict audit for: " + ", ".join(f"{int(x)}m" for x in passes.horizon)
         if len(passes) else "The fixed policy did not pass the strict multi-era audit."),
        "This is a post-selection historical audit, not a live-profit claim. PnL remains a BTC-path proxy.", "",
        "## Fold Results", "", markdown_table(fold_summary.round(4)), "",
        "## Aggregate Fixed And Fold-Selected Results", "", markdown_table(aggregate.round(4)), "",
        "## Policy Aggregate", "", markdown_table(policy_aggregate.round(4)), "",
        "## Validation Champion Frequency", "", markdown_table(champion_counts), "",
        "## Policy Selection Overfitting", "", markdown_table(pbo_rows.round(4)), "",
        "## Method", "",
        f"- {fold_count} expanding walk-forward tests after 60 days of initial training and 20 days of validation; "
        "the final fold may be shorter when the source archive ends mid-fold.",
        "- Horizon-aware purging prevents labels from crossing fold boundaries.",
        "- First-touch UP/DOWN model families are frozen from the prior research result.",
        "- Isotonic calibration, signal thresholds and exit-policy selection use validation only.",
        "- Exactly-one-side signals are evaluated on the following untouched 20-day era.",
        "- Same-bar TP/SL ambiguity is scored stop-first.",
        "- Twenty policies are compared; TP50/SL10 is also reported as the fixed previously selected policy.",
        "- PBO is estimated by selecting policies on combinations of OOS eras and ranking them on held eras.", "",
        "## Boundary", "",
        "The policy uses signed BTC-dollar path movement and a fixed dollar cost. It does not model Polymarket "
        "share asks, bids, fills, binary payoff curvature or fees. No output authorizes live betting.",
    ]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def run(args):
    out = Path(args.output_dir) if args.output_dir else DEFAULT_OUT
    log = setup_log(out)
    started = time.time()
    path_dir = ROOT / "data" / "research" / "path_dynamics_180d_30s"
    anchor_dir = ROOT / "data" / "research" / "anchor_roundtrip_180d_30s"
    rounds = pd.read_parquet(path_dir / "round_dynamics.parquet")
    features = json.loads((anchor_dir / "feature_names.json").read_text(encoding="utf-8"))["open_70"]
    model_features = features + dynamics.FLOW_FEATURES
    rounds = rounds.dropna(subset=model_features + ["first_touch_up", "first_touch_down"]).copy()
    folds = make_folds(rounds, args.fold_days, args.minimum_train_days, args.minimum_partial_fold_days)
    if args.max_folds:
        folds = folds[-args.max_folds:]
    if len(folds) < 4:
        raise ValueError(f"need at least four folds for PBO, found {len(folds)}")
    log.info("audit folds=%d features=%d policies=%d", len(folds), len(model_features), len(POLICIES))

    cfg = anchor.Config(days=180, interval="30s", threads=args.threads,
                        max_train_rows=args.max_train_rows, quick=False, offline=True,
                        output_dir=str(out))
    bars = anchor.load_bars(cfg, log)
    bar_index = {int(value): index for index, value in enumerate(bars.ts_ms.to_numpy(np.int64))}
    fold_rows, policy_rows, prediction_rows = [], [], []
    fixed_trade_rows, selected_trade_rows = [], []

    for fold in folds:
        for horizon in (5, 15):
            data = rounds[rounds.horizon == horizon].sort_values("round_start").reset_index(drop=True)
            masks = masks_for_fold(data, fold)
            up = fit_head(data, model_features, "first_touch_up", horizon, masks, cfg, log, fold["fold"])
            down = fit_head(data, model_features, "first_touch_down", horizon, masks, cfg, log, fold["fold"])
            validation_all, validation = combine_signals(data, up, down, "validation")
            test_all, test = combine_signals(data, up, down, "test")
            if len(validation) < 20 or len(test) < 20:
                log.warning("fold=%d h=%d sparse exclusive signals validation=%d test=%d",
                            fold["fold"], horizon, len(validation), len(test))
            for split_name, prediction in (("validation", validation_all), ("test", test_all)):
                saved = prediction.copy()
                saved["fold"], saved["split"] = fold["fold"], split_name
                prediction_rows.append(saved)

            per_policy = []
            for policy in POLICIES:
                validation_net, _ = policy_values(validation, bars, bar_index, policy, cost=2.0)
                test_net, _ = policy_values(test, bars, bar_index, policy, cost=2.0)
                per_policy.append({"fold": fold["fold"], "horizon": horizon,
                                   "policy": policy_name(policy),
                                   "validation_n": len(validation_net),
                                   "validation_mean": np.mean(validation_net) if len(validation_net) else np.nan,
                                   "test_n": len(test_net),
                                   "test_mean": np.mean(test_net) if len(test_net) else np.nan,
                                   "test_profit_factor": stopping.pnl_metrics(test_net)["profit_factor"]
                                   if len(test_net) else np.nan})
            policies = pd.DataFrame(per_policy)
            selected_name = policies.sort_values("validation_mean", ascending=False).policy.iloc[0]
            policies["test_rank"] = policies.test_mean.rank(ascending=False, method="min").astype(int)
            policy_rows.extend(policies.to_dict("records"))
            selected_policy = next(policy for policy in POLICIES if policy_name(policy) == selected_name)

            fixed_net, fixed_gross = policy_values(test, bars, bar_index, FIXED_POLICY, cost=2.0)
            selected_net, selected_gross = policy_values(test, bars, bar_index, selected_policy, cost=2.0)
            fixed_row = policies[policies.policy == policy_name(FIXED_POLICY)].iloc[0]
            selected_row = policies[policies.policy == selected_name].iloc[0]
            fold_rows.append({
                "fold": fold["fold"], "horizon": horizon,
                "validation_start": fold["validation_start"], "test_start": fold["test_start"],
                "test_end": fold["test_end"], "up_members": up["members"], "down_members": down["members"],
                "up_threshold": up["threshold"], "down_threshold": down["threshold"],
                "up_test_auc": up["test_auc"], "down_test_auc": down["test_auc"],
                "validation_exclusive_signals": len(validation), "test_exclusive_signals": len(test),
                "test_signal_precision": test.side_correct.mean() if len(test) else np.nan,
                "test_dual_rate": (test_all.signal_state == "DUAL").mean(),
                "selected_policy": selected_name, "selected_validation_mean": selected_row.validation_mean,
                "selected_test_mean": selected_row.test_mean, "selected_test_rank": selected_row.test_rank,
                "fixed_test_mean": fixed_row.test_mean, "fixed_test_rank": fixed_row.test_rank,
            })
            for ledger, role, gross in ((fixed_trade_rows, "FIXED_TP50_SL10", fixed_gross),
                                        (selected_trade_rows, "FOLD_SELECTED", selected_gross)):
                for source, value in zip(test.itertuples(index=False), gross):
                    ledger.append({"fold": fold["fold"], "horizon": horizon, "role": role,
                                   "round_id": source.round_id, "round_start": source.round_start,
                                   "side": source.side, "side_correct": source.side_correct,
                                   "gross_pnl": value,
                                   "policy": policy_name(FIXED_POLICY) if role == "FIXED_TP50_SL10" else selected_name})
            log.info("[FOLD %d] h=%dm signals=%d precision=%.3f selected=%s test=%.2f fixed=%.2f",
                     fold["fold"], horizon, len(test), test.side_correct.mean() if len(test) else np.nan,
                     selected_name, np.mean(selected_net), np.mean(fixed_net))

    fold_summary = pd.DataFrame(fold_rows)
    policy_frame = pd.DataFrame(policy_rows)
    predictions = pd.concat(prediction_rows, ignore_index=True)
    fixed_trades = pd.DataFrame(fixed_trade_rows)
    selected_trades = pd.DataFrame(selected_trade_rows)
    pbo_parts, pbo_summary = [], []
    for horizon in (5, 15):
        rows, estimate = pbo_from_folds(policy_frame, horizon)
        pbo_parts.append(rows)
        pbo_summary.append({"horizon": horizon, "pbo": estimate, "combinations": len(rows)})
    pbo_rows = pd.concat(pbo_parts, ignore_index=True)
    aggregate = aggregate_results(policy_frame, fold_summary, fixed_trades, selected_trades)
    policy_aggregate = policy_frame.groupby(["horizon", "policy"]).agg(
        folds=("fold", "nunique"), mean_validation=("validation_mean", "mean"),
        mean_test=("test_mean", "mean"), worst_test=("test_mean", "min"),
        positive_test_folds=("test_mean", lambda values: float(np.mean(np.asarray(values) > 0))),
        median_test_rank=("test_rank", "median")).reset_index()
    policy_aggregate["is_fixed_tp50_sl10"] = policy_aggregate.policy == policy_name(FIXED_POLICY)

    fold_summary.to_csv(out / "fold_summary.csv", index=False)
    policy_frame.to_csv(out / "fold_policy_metrics.csv", index=False)
    policy_aggregate.to_csv(out / "policy_aggregate.csv", index=False)
    predictions.to_csv(out / "walkforward_signal_predictions.csv", index=False)
    fixed_trades.to_csv(out / "fixed_policy_trades.csv", index=False)
    selected_trades.to_csv(out / "selected_policy_trades.csv", index=False)
    pbo_rows.to_csv(out / "policy_pbo_combinations.csv", index=False)
    pd.DataFrame(pbo_summary).to_csv(out / "policy_pbo_summary.csv", index=False)
    aggregate.to_csv(out / "aggregate_metrics.csv", index=False)
    (out / "config.json").write_text(json.dumps({"version": VERSION, "fold_days": args.fold_days,
                                                   "minimum_train_days": args.minimum_train_days,
                                                   "folds": folds, "fixed_policy": FIXED_POLICY,
                                                   "policy_count": len(POLICIES)}, indent=2), encoding="utf-8")
    write_report(out, fold_summary, aggregate, policy_aggregate, pbo_rows)
    log.info("DONE elapsed=%.1f minutes output=%s", (time.time() - started) / 60, out)
    print("\nFOLD SUMMARY\n" + fold_summary.round(4).to_string(index=False))
    print("\nAGGREGATE\n" + aggregate.round(4).to_string(index=False))
    print("\nPBO\n" + pd.DataFrame(pbo_summary).round(4).to_string(index=False))
    return 0


def selftest():
    toy = pd.DataFrame({"round_start": np.arange(180) * 86_400_000 + 1_700_000_000_000})
    folds = make_folds(toy)
    assert len(folds) == 5
    assert policy_name(FIXED_POLICY) == "BARRIER:50:10"
    frame = pd.DataFrame({"fold": np.repeat(np.arange(1, 6), 2), "horizon": 5,
                          "policy": ["A", "B"] * 5,
                          "test_mean": [1, 0, 1, 0, -1, 2, 1, 0, 1, 0]})
    rows, estimate = pbo_from_folds(frame, 5)
    assert len(rows) == 10 and 0 <= estimate <= 1
    print("SELFTEST PASS")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--max-train-rows", type=int, default=120_000)
    parser.add_argument("--fold-days", type=int, default=20)
    parser.add_argument("--minimum-train-days", type=int, default=60)
    parser.add_argument("--minimum-partial-fold-days", type=int, default=10)
    parser.add_argument("--max-folds", type=int, default=0)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--selftest", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    if arguments.selftest:
        selftest()
        raise SystemExit(0)
    raise SystemExit(run(arguments))
