"""Round-state, flip-risk, and BTC-proxy stopping research.

Uses only existing 180-day 30-second Binance paths. Tests touch-to-settlement
conversion, last flips, irreversible lead time, anchor recrosses, occupancy,
path archetypes, extrema/volume timing, volatility onset, late shocks,
path transitions, opportunity drought, and validation-selected stopping rules.
"""
from __future__ import annotations

import argparse
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

VERSION = "2026-07-01-round-state-stopping-v1"
SNAPSHOT_SECONDS = (120, 90, 60, 30)
SHOCKS = (20, 50, 100)


def setup_log(out: Path) -> logging.Logger:
    out.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("round-state-stopping")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    for handler in (logging.StreamHandler(sys.stdout),
                    logging.FileHandler(out / "run.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    return log


def carry_sign(values: np.ndarray) -> np.ndarray:
    result = np.sign(values).astype(int)
    last = 0
    for i, value in enumerate(result):
        if value == 0:
            result[i] = last
        else:
            last = value
    if len(result) and result[0] == 0:
        nonzero = np.where(result != 0)[0]
        if len(nonzero):
            result[:nonzero[0]] = result[nonzero[0]]
    return result


def thirds_bucket(index: int, length: int) -> str:
    fraction = (index + 1) / max(length, 1)
    return "EARLY" if fraction <= 1 / 3 else ("MIDDLE" if fraction <= 2 / 3 else "LATE")


def build_state_datasets(rounds, touch_events, bars, features, log):
    ts = bars.ts_ms.to_numpy(np.int64)
    op = bars.open.to_numpy(float)
    hi = bars.high.to_numpy(float)
    lo = bars.low.to_numpy(float)
    close = bars.close.to_numpy(float)
    volume = bars.volume.to_numpy(float)
    index = {int(value): i for i, value in enumerate(ts)}
    round_rows, snapshot_rows = [], []
    for number, row in enumerate(rounds.itertuples(index=False), 1):
        horizon = int(row.horizon)
        width = horizon * 2
        i = index.get(int(row.round_start))
        if i is None or i + width > len(bars):
            continue
        ph, pl, pc, pv = hi[i:i + width], lo[i:i + width], close[i:i + width], volume[i:i + width]
        price = float(row.anchor)
        signed = carry_sign(pc - price)
        nonzero = np.where(signed != 0)[0]
        changes = np.where((signed[1:] != signed[:-1]) & (signed[1:] != 0) & (signed[:-1] != 0))[0] + 1
        recrosses = int(len(changes))
        first_stable = int(changes[-1]) if len(changes) else (int(nonzero[0]) if len(nonzero) else width - 1)
        irreversible_elapsed = (first_stable + 1) * 30
        time_above = float(np.mean(pc > price))
        high_idx, low_idx, volume_idx = int(np.argmax(ph)), int(np.argmin(pl)), int(np.argmax(pv))
        primary = float(anchor.PRIMARY[horizon])
        up_hit = np.where(ph >= price + primary)[0]
        down_hit = np.where(pl <= price - primary)[0]
        first_touch = min([int(x[0]) for x in (up_hit, down_hit) if len(x)], default=width + 1)
        total_range = float(np.max(ph) - np.min(pl))
        if total_range < primary:
            archetype = "QUIET"
        elif first_touch < width and first_touch >= int(width * 0.70):
            archetype = "LATE_BREAKOUT"
        elif recrosses >= 3:
            archetype = "CHOP"
        elif recrosses == 1:
            archetype = "SINGLE_REVERSAL"
        elif recrosses == 0:
            archetype = "TREND_UP" if pc[-1] > price else "TREND_DOWN"
        else:
            archetype = "MIXED"

        pre_rv = max(float(row.rv_5m), anchor.EPS)
        path_returns = np.diff(np.log(np.maximum(np.r_[op[i], pc], anchor.EPS)))
        onset = None
        for k in range(2, len(path_returns)):
            local_rv = float(np.sqrt(np.mean(np.square(path_returns[k - 2:k + 1]))) * 1e4)
            if local_rv >= pre_rv * 1.75:
                onset = k
                break
        record = row._asdict()
        record.update({
            "anchor_recross_count": recrosses,
            "anchor_recross_bucket": "3_PLUS" if recrosses >= 3 else str(recrosses),
            "has_anchor_flip": int(recrosses > 0),
            "last_flip_in_final_120s": int(len(changes) > 0 and (width - changes[-1]) * 30 <= 120),
            "irreversible_lead_seconds": irreversible_elapsed,
            "irreversible_lead_fraction": irreversible_elapsed / (horizon * 60),
            "time_above_anchor": time_above,
            "path_archetype": archetype,
            "high_time_bucket": thirds_bucket(high_idx, width),
            "low_time_bucket": thirds_bucket(low_idx, width),
            "volume_peak_bucket": thirds_bucket(volume_idx, width),
            "high_time_fraction": (high_idx + 1) / width,
            "low_time_fraction": (low_idx + 1) / width,
            "volume_peak_fraction": (volume_idx + 1) / width,
            "volatility_onset_bucket": "NONE" if onset is None else thirds_bucket(onset, width),
        })
        round_rows.append(record)

        for seconds_left in SNAPSHOT_SECONDS:
            if seconds_left >= horizon * 60:
                continue
            completed = width - seconds_left // 30
            k = completed - 1
            current_side = int(pc[k] > price)
            future_signed = carry_sign(pc[k + 1:] - price)
            future_flip = int(any(value != 0 and (value > 0) != bool(current_side) for value in future_signed))
            current_price = float(pc[k])
            future_hi, future_lo = ph[k + 1:], pl[k + 1:]
            snap = {name: record[name] for name in features}
            snap.update({
                "round_id": row.round_id, "round_start": int(row.round_start), "horizon": horizon,
                "snapshot_ts": int(row.round_start) + completed * 30_000,
                "seconds_left": seconds_left, "current_side_up": current_side,
                "distance_usd": current_price - price, "abs_distance_usd": abs(current_price - price),
                "range_so_far_usd": float(np.max(ph[:k + 1]) - np.min(pl[:k + 1])),
                "recrosses_so_far": int(np.sum(np.diff(carry_sign(pc[:k + 1] - price)) != 0)),
                "time_above_so_far": float(np.mean(pc[:k + 1] > price)),
                "future_side_flip": future_flip,
            })
            for shock in SHOCKS:
                excursion = (max(float(np.max(future_hi) - current_price),
                                 float(current_price - np.min(future_lo))) if len(future_hi) else 0.0)
                snap[f"late_shock_{shock}"] = int(excursion >= shock)
            snapshot_rows.append(snap)
        if number % 10_000 == 0:
            log.info("state labels %s/%s", f"{number:,}", f"{len(rounds):,}")

    round_df = pd.DataFrame(round_rows).sort_values("round_start").reset_index(drop=True)
    snapshot_df = pd.DataFrame(snapshot_rows).sort_values(["round_start", "snapshot_ts"]).reset_index(drop=True)
    touch = touch_events.merge(round_df[["round_id", "direction_up"]], on="round_id", how="inner")
    touch["touch_side_settles"] = (touch.touch_side_up.astype(int) == touch.direction_up.astype(int)).astype(int)
    log.info("state datasets rounds=%s snapshots=%s touches=%s", f"{len(round_df):,}",
             f"{len(snapshot_df):,}", f"{len(touch):,}")
    return round_df, snapshot_df, touch


def add_transition_and_drought(round_df):
    pieces = []
    state_columns = ["anchor_recross_count", "irreversible_lead_fraction", "time_above_anchor",
                     "high_time_fraction", "low_time_fraction", "volume_peak_fraction",
                     "close_move_usd", "range_usd", "future_volume_ratio"]
    archetypes = sorted(round_df.path_archetype.unique())
    for horizon, frame in round_df.groupby("horizon", sort=False):
        frame = frame.sort_values("round_start").copy()
        for label in archetypes:
            frame[f"archetype_{label}"] = (frame.path_archetype == label).astype(int)
        frame["next_archetype"] = frame.path_archetype.shift(-1)
        frame["next_archetype_outcome_end"] = frame.round_start + 2 * int(horizon) * 60_000
        opportunity = frame[f"roundtrip_{int(anchor.PRIMARY[int(horizon)])}"].to_numpy(int)
        rounds_to = np.full(len(frame), 11, dtype=int)
        for i in range(len(frame)):
            future = np.where(opportunity[i + 1:i + 11] == 1)[0]
            if len(future):
                rounds_to[i] = int(future[0] + 1)
        frame["rounds_to_next_opportunity"] = rounds_to
        for count in (3, 6):
            target = f"next_opportunity_within_{count}_rounds"
            frame[target] = (rounds_to <= count).astype(int)
            frame[f"{target}_outcome_end"] = frame.round_start + (count + 1) * int(horizon) * 60_000
        frame["transition_feature_names"] = json.dumps(state_columns + [f"archetype_{x}" for x in archetypes])
        pieces.append(frame.iloc[:-10].copy())
    return pd.concat(pieces, ignore_index=True).sort_values("round_start").reset_index(drop=True), state_columns, archetypes


def policy_pnl(row, bars, index, policy):
    i = index[int(row.round_start)]
    width = int(row.horizon) * 2
    ph = bars.high.to_numpy(float)[i:i + width]
    pl = bars.low.to_numpy(float)[i:i + width]
    pc = bars.close.to_numpy(float)[i:i + width]
    side = int(row.side)
    entry = float(row.anchor)
    if policy[0] == "TIME":
        seconds = policy[1]
        k = width - 1 if seconds == 9999 else min(width - 1, max(0, seconds // 30 - 1))
        return side * (float(pc[k]) - entry)
    take_profit, stop_loss = policy[1], policy[2]
    for k in range(width):
        favorable = float(ph[k] - entry) if side == 1 else float(entry - pl[k])
        adverse = float(entry - pl[k]) if side == 1 else float(ph[k] - entry)
        if adverse >= stop_loss:
            return -float(stop_loss)
        if favorable >= take_profit:
            return float(take_profit)
    return side * (float(pc[-1]) - entry)


def pnl_metrics(values):
    x = np.asarray(values, float)
    positive, negative = x[x > 0].sum(), -x[x < 0].sum()
    curve = np.cumsum(x)
    drawdown = np.maximum.accumulate(np.r_[0.0, curve])[1:] - curve
    tail_n = max(1, int(np.ceil(len(x) * 0.05)))
    return {"signals": len(x), "win_rate": float(np.mean(x > 0)), "mean_pnl_usd": float(np.mean(x)),
            "median_pnl_usd": float(np.median(x)), "profit_factor": float(positive / negative) if negative else np.inf,
            "cvar_5pct_usd": float(np.mean(np.sort(x)[:tail_n])),
            "max_drawdown_usd": float(np.max(drawdown)) if len(drawdown) else 0.0}


def day_block_ci(values, timestamps, seed=7):
    frame = pd.DataFrame({"value": np.asarray(values, float),
                          "day": pd.to_datetime(timestamps, unit="ms", utc=True).date})
    groups = [group.value.to_numpy(float) for _, group in frame.groupby("day")]
    if len(groups) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(2000):
        sampled = [groups[index] for index in rng.integers(0, len(groups), len(groups))]
        means.append(float(np.mean(np.concatenate(sampled))))
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def run_stopping(round_df, bars, out, log):
    predictions = pd.read_csv(ROOT / "data" / "research" / "path_dynamics_180d_30s" /
                              "binary_predictions.csv")
    index = {int(value): i for i, value in enumerate(bars.ts_ms.to_numpy(np.int64))}
    rows, trade_rows = [], []
    for horizon in (5, 15):
        family = f"round_{horizon}m"
        variant = "base70_plus_flow"
        up = predictions[(predictions.family == family) & (predictions.variant == variant) &
                         (predictions.target == "first_touch_up")][["round_id", "signal"]].rename(
                             columns={"signal": "up_signal"})
        down = predictions[(predictions.family == family) & (predictions.variant == variant) &
                           (predictions.target == "first_touch_down")][["round_id", "signal"]].rename(
                               columns={"signal": "down_signal"})
        signals = up.merge(down, on="round_id")
        signals = signals[signals.up_signal.astype(bool) ^ signals.down_signal.astype(bool)].copy()
        signals["side"] = np.where(signals.up_signal, 1, -1)
        signals = signals.merge(round_df[["round_id", "round_start", "horizon", "anchor", "regime",
                                          "path_archetype"]], on="round_id")
        signals = signals.sort_values("round_start").reset_index(drop=True)
        cut = len(signals) // 2
        select, test = signals.iloc[:cut], signals.iloc[cut:]
        policies = [("TIME", seconds) for seconds in (30, 60, 120, 9999)]
        policies += [("BARRIER", tp, sl) for tp in (10, 20, 30, 50) for sl in (10, 20, 30, 50)]
        selection = []
        for policy in policies:
            values = [policy_pnl(row, bars, index, policy) - 2.0 for row in select.itertuples(index=False)]
            selection.append((float(np.mean(values)), policy))
        _, champion = max(selection, key=lambda value: value[0])
        for name, policy in (("SELECTED", champion), ("SETTLEMENT", ("TIME", 9999))):
            gross = [policy_pnl(row, bars, index, policy) for row in test.itertuples(index=False)]
            for cost in (0.0, 2.0, 5.0):
                net = np.asarray(gross) - cost
                metric = pnl_metrics(net)
                lower, upper = day_block_ci(net, test.round_start.to_numpy(np.int64))
                rows.append({"horizon": horizon, "policy_role": name, "policy": str(policy),
                             "selection_half_n": len(select), "cost_usd": cost, **metric,
                             "day_block_mean_ci_low": lower, "day_block_mean_ci_high": upper,
                             "warning": "BTC path proxy, not Polymarket share PnL"})
                for source, gross_value, net_value in zip(test.itertuples(index=False), gross, net):
                    trade_rows.append({"round_id": source.round_id, "round_start": source.round_start,
                                       "horizon": horizon, "side": source.side, "regime": source.regime,
                                       "path_archetype": source.path_archetype, "policy_role": name,
                                       "policy": str(policy), "cost_usd": cost,
                                       "gross_pnl_usd": gross_value, "net_pnl_usd": net_value})
        log.info("stopping h=%dm champion=%s select=%d test=%d", horizon, champion, len(select), len(test))
    result = pd.DataFrame(rows)
    result.to_csv(out / "stopping_policy_metrics.csv", index=False)
    pd.DataFrame(trade_rows).to_csv(out / "stopping_trade_results.csv", index=False)
    return result


def write_report(out, binary, multiclass, regression, stopping, prior_failure):
    lines = ["# Round-State And Stopping Research", "", f"Version: `{VERSION}`", "",
             "## Boundary", "",
             "All state labels use existing 30-second Binance paths. Stopping PnL is a BTC symmetric-path "
             "proxy and is not Polymarket share PnL.", "",
             "## Binary Heads", "", anchor.markdown_table(pd.DataFrame(binary).round(4)), "",
             "## Multiclass Heads", "", anchor.markdown_table(pd.DataFrame(multiclass).round(4)), "",
             "## Continuous Heads", "", anchor.markdown_table(pd.DataFrame(regression).round(4)), "",
             "## Validation-Selected Stopping", "", anchor.markdown_table(stopping.round(4)), "",
             "## Previously Tested Confidence Failure", "",
             anchor.markdown_table(prior_failure.round(4)), "",
             "## Rules", "",
             "- Last-flip predicts any future anchor-side change, not merely final settlement.",
             "- Irreversible lead is known only retrospectively and is a supervised timing target.",
             "- Path transitions and drought labels include extended outcome purging.",
             "- Stopping policies are selected on the first half of the old test and reported on its second half.",
             "- No output is a live-betting approval."]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def run(args):
    out = Path(args.output_dir) if args.output_dir else ROOT / "data" / "research" / "round_state_stopping_180d_30s"
    log = setup_log(out)
    started = time.time()
    path_dir = ROOT / "data" / "research" / "path_dynamics_180d_30s"
    anchor_dir = ROOT / "data" / "research" / "anchor_roundtrip_180d_30s"
    rounds = pd.read_parquet(path_dir / "round_dynamics.parquet")
    touches = pd.read_parquet(path_dir / "touch_dynamics.parquet")
    features = json.loads((anchor_dir / "feature_names.json").read_text(encoding="utf-8"))["open_70"]
    cfg = anchor.Config(days=180, interval="30s", threads=args.threads,
                        max_train_rows=args.max_train_rows, quick=args.quick, offline=True,
                        output_dir=str(out))
    bars = anchor.load_bars(cfg, log)
    states, snapshots, touch_state = build_state_datasets(rounds, touches, bars, features, log)
    transitions, state_features, archetypes = add_transition_and_drought(states)
    states.to_parquet(out / "round_states.parquet", index=False)
    snapshots.to_parquet(out / "late_snapshots.parquet", index=False)
    touch_state.to_parquet(out / "touch_settlement.parquet", index=False)
    transitions.to_parquet(out / "transition_drought.parquet", index=False)

    snapshot_features = features + ["seconds_left", "current_side_up", "distance_usd",
                                    "abs_distance_usd", "range_so_far_usd", "recrosses_so_far",
                                    "time_above_so_far"]
    touch_features = features + ["touch_side_up", "touch_time_s", "seconds_left"]
    transition_features = features + state_features + [f"archetype_{name}" for name in archetypes]
    binary_specs = []
    for horizon in cfg.horizons:
        r = states[states.horizon == horizon]
        s = snapshots[snapshots.horizon == horizon]
        t = touch_state[touch_state.horizon == horizon]
        x = transitions[transitions.horizon == horizon]
        binary_specs += [(r, features, "has_anchor_flip", f"round_{horizon}m"),
                         (r, features, "last_flip_in_final_120s", f"round_{horizon}m"),
                         (t, touch_features, "touch_side_settles", f"touch_{horizon}m"),
                         (s, snapshot_features, "future_side_flip", f"snapshot_{horizon}m")]
        for shock in SHOCKS:
            binary_specs.append((s, snapshot_features, f"late_shock_{shock}", f"snapshot_{horizon}m"))
        for count in (3, 6):
            target = f"next_opportunity_within_{count}_rounds"
            binary_specs.append((x, transition_features, target, f"drought_{horizon}m"))
    if args.quick:
        keep = {"touch_side_settles", "future_side_flip", "late_shock_50",
                "next_opportunity_within_3_rounds"}
        binary_specs = [spec for spec in binary_specs if spec[2] in keep]

    binary, binary_predictions = [], []
    for number, (frame, columns, target, family) in enumerate(binary_specs, 1):
        log.info("binary %d/%d %s/%s", number, len(binary_specs), family, target)
        result = dynamics.evaluate_binary(frame, columns, target, family, "existing_data", cfg, log)
        if result:
            metric, prediction = result
            binary.append(metric)
            binary_predictions.append(prediction)
    pd.DataFrame(binary).to_csv(out / "binary_metrics.csv", index=False)
    pd.concat(binary_predictions, ignore_index=True).to_csv(out / "binary_predictions.csv", index=False)

    multiclass = []
    round_targets = ("anchor_recross_bucket", "path_archetype", "high_time_bucket",
                     "low_time_bucket", "volume_peak_bucket", "volatility_onset_bucket")
    if args.quick:
        round_targets = ("path_archetype", "volatility_onset_bucket")
    for horizon in cfg.horizons:
        frame = states[states.horizon == horizon]
        for target in round_targets:
            result = dynamics.evaluate_multiclass(frame, features, target, f"round_{horizon}m", cfg, log)
            if result:
                multiclass.append(result)
        transition = transitions[transitions.horizon == horizon]
        result = dynamics.evaluate_multiclass(transition, transition_features, "next_archetype",
                                              f"transition_{horizon}m", cfg, log)
        if result:
            multiclass.append(result)
    pd.DataFrame(multiclass).to_csv(out / "multiclass_metrics.csv", index=False)

    regression = []
    for horizon in cfg.horizons:
        frame = states[states.horizon == horizon]
        regression_targets = (("irreversible_lead_seconds", "time_above_anchor") if args.quick else
                              ("irreversible_lead_seconds", "time_above_anchor", "high_time_fraction",
                               "low_time_fraction", "volume_peak_fraction"))
        for target in regression_targets:
            regression.append(dynamics.evaluate_regression(frame, features, target,
                                                           f"round_{horizon}m", "existing_data", cfg))
        transition = transitions[transitions.horizon == horizon]
        regression.append(dynamics.evaluate_regression(transition, transition_features,
                                                       "rounds_to_next_opportunity",
                                                       f"drought_{horizon}m", "existing_data", cfg))
    pd.DataFrame(regression).to_csv(out / "regression_metrics.csv", index=False)
    stopping = run_stopping(states, bars, out, log)
    prior_failure = pd.read_csv(ROOT / "data" / "research" / "decision_heads_180d_30s" /
                                "model_failure_metrics.csv")
    write_report(out, binary, multiclass, regression, stopping, prior_failure)
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
