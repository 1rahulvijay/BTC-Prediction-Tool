"""Replay Polymarket share trades after causal BTC shocks.

For each trustworthy round, this script finds the first 5-second BTC move of
$10/$20/$30, then compares buying the share in the move direction (MOMENTUM)
with buying the temporarily losing share (FADE). Entries use recorded asks;
exits use later recorded bids; estimated taker fees apply to both legs.

Research only: the recorder does not prove fills or preserve exchange event time.
"""
from __future__ import annotations

import argparse
import itertools
import math
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from . import test_polymarket_market_response as market
except ImportError:
    import test_polymarket_market_response as market


DEFAULT_OUT = market.DATA / "research" / "polymarket_shock_trade_replay"
SHOCKS = (10.0, 20.0, 30.0)
ENTRY_DELAYS = (0, 2)
EXIT_DELAYS = (2, 5, 10, 20, 30)
MAX_SPREAD = 0.05
MIN_TOP_SIZE = 1.0


def after_index(ts, target, tolerance=3.0):
    index = int(np.searchsorted(ts, target, side="left"))
    if index >= len(ts) or ts[index] - target > tolerance:
        return None
    return index


def detect_events(snap):
    rows = []
    for threshold in SHOCKS:
        for slug, group in snap.groupby("slug", sort=False):
            group = group.sort_values("ts").reset_index(drop=True)
            ts = group.ts.to_numpy(float)
            btc = group.btc_price.to_numpy(float)
            selected = None
            for i in range(1, len(group)):
                j = int(np.searchsorted(ts, ts[i] - 5.0, side="right") - 1)
                if j < 0 or not (3.0 <= ts[i] - ts[j] <= 8.0):
                    continue
                shock = float(btc[i] - btc[j])
                if abs(shock) >= threshold and float(group.seconds_left.iloc[i]) > 35:
                    selected = (i, j, shock)
                    break
            if selected is None:
                continue
            i, j, shock = selected
            rows.append({
                "threshold_usd": threshold, "slug": slug, "horizon": int(group.horizon.iloc[0]),
                "event_ts": float(ts[i]), "event_index": i, "baseline_index": j,
                "shock_side": 1 if shock > 0 else 0, "shock_usd": abs(shock),
                "baseline_btc": float(btc[j]), "event_btc": float(btc[i]),
                "seconds_left": float(group.seconds_left.iloc[i]),
            })
    return pd.DataFrame(rows)


def side_values(group, side, index):
    prefix = "up" if side == 1 else "down"
    return {
        "ask": float(group[f"{prefix}_ask"].iloc[index]),
        "bid": float(group[f"{prefix}_bid"].iloc[index]),
        "spread": float(group[f"{prefix}_spread"].iloc[index]),
        "top_size": float(group[f"{prefix}_top_ask_size"].iloc[index]),
    }


def exit_trade(group, entry_index, side, entry_ask, entry_fee, exit_index):
    values = side_values(group, side, exit_index)
    exit_bid = values["bid"]
    exit_fee = float(market.fee(exit_bid))
    return exit_bid - entry_ask - entry_fee - exit_fee, exit_bid, exit_fee


def replay_trades(snap, sett, events):
    outcomes = sett.set_index("slug").settled_side.to_dict() if len(sett) else {}
    groups = {slug: group.sort_values("ts").reset_index(drop=True)
              for slug, group in snap.groupby("slug", sort=False)}
    rows = []
    for event in events.itertuples(index=False):
        group = groups[event.slug]
        ts = group.ts.to_numpy(float)
        for entry_delay in ENTRY_DELAYS:
            entry_index = after_index(ts, event.event_ts + entry_delay)
            if entry_index is None:
                continue
            for strategy in ("MOMENTUM", "FADE"):
                side = int(event.shock_side if strategy == "MOMENTUM" else 1 - event.shock_side)
                entry = side_values(group, side, entry_index)
                if not (0 < entry["ask"] < 1 and entry["spread"] <= MAX_SPREAD
                        and entry["top_size"] >= MIN_TOP_SIZE):
                    continue
                entry_ts = float(ts[entry_index])
                entry_fee = float(market.fee(entry["ask"]))
                common = {
                    "threshold_usd": event.threshold_usd, "slug": event.slug,
                    "horizon": event.horizon, "event_ts": event.event_ts,
                    "shock_side": "UP" if event.shock_side else "DOWN", "shock_usd": event.shock_usd,
                    "strategy": strategy, "trade_side": "UP" if side else "DOWN",
                    "entry_delay_s": entry_delay, "entry_ts": entry_ts,
                    "entry_ask": entry["ask"], "entry_fee": entry_fee,
                    "entry_spread": entry["spread"], "entry_top_size": entry["top_size"],
                }
                for exit_delay in EXIT_DELAYS:
                    exit_index = after_index(ts, entry_ts + exit_delay)
                    if exit_index is None:
                        continue
                    net, exit_bid, exit_fee = exit_trade(
                        group, entry_index, side, entry["ask"], entry_fee, exit_index)
                    rows.append({**common, "exit_rule": f"TIME_{exit_delay}S",
                                 "exit_ts": float(ts[exit_index]), "exit_bid": exit_bid,
                                 "exit_fee": exit_fee, "net_per_contract": net,
                                 "settled_win": np.nan})

                # A fade exits on the first return through the pre-shock BTC level; otherwise settlement.
                if strategy == "FADE" and event.slug in outcomes:
                    recross_index = None
                    for j in range(entry_index + 1, len(group)):
                        price = float(group.btc_price.iloc[j])
                        if ((event.shock_side == 1 and price <= event.baseline_btc)
                                or (event.shock_side == 0 and price >= event.baseline_btc)):
                            recross_index = j
                            break
                    if recross_index is not None:
                        net, exit_bid, exit_fee = exit_trade(
                            group, entry_index, side, entry["ask"], entry_fee, recross_index)
                        rows.append({**common, "exit_rule": "RECROSS_OR_SETTLEMENT",
                                     "exit_ts": float(ts[recross_index]), "exit_bid": exit_bid,
                                     "exit_fee": exit_fee, "net_per_contract": net,
                                     "settled_win": np.nan})
                    else:
                        won = int(int(outcomes[event.slug]) == side)
                        rows.append({**common, "exit_rule": "RECROSS_OR_SETTLEMENT",
                                     "exit_ts": float(group.ts.iloc[-1]), "exit_bid": np.nan,
                                     "exit_fee": 0.0,
                                     "net_per_contract": won - entry["ask"] - entry_fee,
                                     "settled_win": won})

                if event.slug in outcomes:
                    won = int(int(outcomes[event.slug]) == side)
                    rows.append({**common, "exit_rule": "SETTLEMENT",
                                 "exit_ts": float(event.event_ts + event.seconds_left),
                                 "exit_bid": float(won), "exit_fee": 0.0,
                                 "net_per_contract": won - entry["ask"] - entry_fee,
                                 "settled_win": won})
    return pd.DataFrame(rows)


def profit_factor(values):
    x = np.asarray(values, dtype=float)
    positive = x[x > 0].sum()
    negative = -x[x < 0].sum()
    return float(positive / negative) if negative > 0 else np.inf


def max_drawdown(values):
    curve = np.cumsum(np.asarray(values, dtype=float))
    if not len(curve):
        return 0.0
    peaks = np.maximum.accumulate(np.r_[0.0, curve])[1:]
    return float(np.max(peaks - curve))


def sign_flip_pvalue(values, seed=29, draws=20000):
    x = np.asarray(values, dtype=float)
    if len(x) < 5 or np.mean(x) <= 0:
        return np.nan if len(x) < 5 else 1.0
    observed = float(np.mean(x))
    if len(x) <= 15:
        means = [np.mean(x * np.asarray(signs)) for signs in itertools.product((-1.0, 1.0), repeat=len(x))]
        return float((np.sum(np.asarray(means) >= observed) + 1) / (len(means) + 1))
    rng = np.random.default_rng(seed)
    signs = rng.choice((-1.0, 1.0), size=(draws, len(x)))
    null_means = np.mean(signs * x, axis=1)
    return float((np.sum(null_means >= observed) + 1) / (draws + 1))


def bh_qvalues(pvalues):
    p = np.asarray(pvalues, dtype=float)
    q = np.full(len(p), np.nan)
    valid = np.flatnonzero(np.isfinite(p))
    if not len(valid):
        return q
    order = valid[np.argsort(p[valid])]
    adjusted = p[order] * len(valid) / np.arange(1, len(valid) + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    q[order] = np.minimum(adjusted, 1.0)
    return q


def summarize(trades):
    if trades.empty:
        return pd.DataFrame()
    augmented = pd.concat([trades, trades.assign(horizon=0)], ignore_index=True)
    keys = ["threshold_usd", "horizon", "strategy", "entry_delay_s", "exit_rule"]
    rows = []
    for group_number, (key, group) in enumerate(augmented.groupby(keys, sort=True)):
        group = group.sort_values("entry_ts")
        x = group.net_per_contract.to_numpy(float)
        low, high = market.bootstrap_mean_ci(x, seed=41 + group_number)
        wins = int(np.sum(x > 0))
        tail_n = max(1, int(math.ceil(len(x) * 0.05)))
        rows.append({
            **dict(zip(keys, key)), "trades": len(group), "profitable": wins,
            "win_rate": wins / len(group), "wilson_low": market.wilson_low(wins, len(group)),
            "mean_net": float(np.mean(x)), "median_net": float(np.median(x)),
            "mean_ci_low": low, "mean_ci_high": high, "profit_factor": profit_factor(x),
            "cvar_5pct": float(np.mean(np.sort(x)[:tail_n])), "max_drawdown": max_drawdown(x),
            "mean_entry_ask": group.entry_ask.mean(), "mean_spread": group.entry_spread.mean(),
            "mean_top_size": group.entry_top_size.mean(),
            "one_sided_signflip_p": sign_flip_pvalue(x, seed=101 + group_number),
        })
    result = pd.DataFrame(rows)
    result["bh_qvalue"] = bh_qvalues(result.one_sided_signflip_p)
    result["robust_positive"] = ((result.trades >= 20) & (result.mean_ci_low > 0)
                                 & (result.bh_qvalue <= 0.05))
    return result


def markdown_table(frame):
    return market.markdown_table(frame)


def write_report(out, coverage, events, metrics):
    eligible = metrics[metrics.trades >= 5].copy()
    best = eligible.sort_values(["robust_positive", "mean_net"], ascending=False).head(20)
    robust = metrics[metrics.robust_positive]
    lines = [
        "# Polymarket BTC-Shock Share Replay", "", "Status: PAPER research only", "",
        "## Verdict", "",
        ("No configuration passed the declared robustness gate."
         if robust.empty else f"{len(robust)} configurations passed the exploratory robustness gate."),
        "The result is not promotable regardless of individual cell performance because the recorder covers "
        "only two independent days and fewer than 200 joined rounds.", "",
        "## Coverage", "", markdown_table(coverage), "",
        "## Design", "",
        "- First causal 5-second BTC shock of $10/$20/$30 per round.",
        "- MOMENTUM buys the share in the shock direction; FADE buys the opposite share.",
        "- Entry at recorded ask with 0s or 2s simulated decision delay.",
        "- Exit at recorded bid after 2/5/10/20/30 seconds, at settlement, or fade recross with settlement fallback.",
        "- Estimated taker fee charged on both round-trip legs.",
        "- Spread <=5c and top-ask size >=1 contract.",
        "- One event per round per predeclared shock threshold.",
        "- Whole-round bootstrap interval, sign-flip test and Benjamini-Hochberg correction.", "",
        "## Event Counts", "",
        markdown_table(events.groupby(["threshold_usd", "horizon"]).size().rename("events").reset_index()), "",
        "## Best Cells With At Least Five Trades", "",
        markdown_table(best.round(5)), "",
        "## Interpretation Rules", "",
        "- Horizon 0 means 5m and 15m pooled; horizon-specific rows remain the primary evidence.",
        "- A positive mean with a confidence interval crossing zero is noise-compatible.",
        "- Cells share rounds and nested thresholds, so raw winners are correlated.",
        "- `robust_positive` requires n>=20, positive bootstrap lower bound and BH q<=0.05.",
        "- Recorded asks/bids are quote opportunities, not observed fills after latency.", "",
        "## Promotion Boundary", "",
        "Do not change live actions. Repeat automatically as the recorder grows. Trainability begins at 200 "
        "trustworthy joined rounds; promotion requires at least 1,000 and a later untouched period.",
    ]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def run(args):
    out = Path(args.output_dir) if args.output_dir else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    raw_snap, raw_sett, source = market.load_tables()
    snap, sett, removed = market.clean_tables(raw_snap, raw_sett)
    coverage = market.coverage_metrics(snap, sett, source, removed)
    events = detect_events(snap)
    trades = replay_trades(snap, sett, events)
    metrics = summarize(trades)

    coverage.to_csv(out / "coverage.csv", index=False)
    events.to_csv(out / "shock_events.csv", index=False)
    trades.to_csv(out / "trade_replay.csv", index=False)
    metrics.to_csv(out / "strategy_metrics.csv", index=False)
    write_report(out, coverage, events, metrics)

    print(coverage.to_string(index=False))
    print("\nEVENTS\n" + (events.groupby(["threshold_usd", "horizon"]).size().rename("events").to_string()
                             if len(events) else "none"))
    eligible = metrics[metrics.trades >= 5].sort_values("mean_net", ascending=False).head(25)
    print("\nTOP CELLS (exploratory; n>=5)\n" +
          (eligible.round(5).to_string(index=False) if len(eligible) else "none"))
    print(f"\nRobust-positive cells: {int(metrics.robust_positive.sum()) if len(metrics) else 0}")
    print(f"Wrote {out}")
    return 0


def selftest():
    assert after_index(np.array([1.0, 2.2, 4.0]), 2.0) == 1
    assert after_index(np.array([1.0, 7.0]), 2.0, tolerance=3.0) is None
    assert abs(profit_factor([2, -1, 1]) - 3.0) < 1e-12
    q = bh_qvalues([0.01, 0.04, np.nan])
    assert np.allclose(q[:2], [0.02, 0.04]) and np.isnan(q[2])
    assert sign_flip_pvalue([1, 1, 1, 1, 1]) < 0.1
    print("SELFTEST PASS")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--selftest", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    if arguments.selftest:
        selftest()
        raise SystemExit(0)
    raise SystemExit(run(arguments))
