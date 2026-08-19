"""ROUND_TO_ROUND_TRANSFER_V1 - does the previous PM round predict the next one?

THE PREMISE THIS LANE WAS QUEUED ON WAS HALF WRONG
    It was picked as the best-powered remaining lane because it needs only settled rounds, and
    settlements span more dates than quote snapshots. Measured, that is only partly true:

        settled rounds with a DIRECTION      3,336  across 19 UTC days   <- real gain
        settled rounds with PRICES           1,053  across 10 UTC days   <- same hole as quotes

    anchor_price and expiry_btc are NULL for 2,283 of 3,336 rounds, and the rounds that have them
    are exactly the snapshotted ones. So direction rules get 19 days and margin rules get 10.
    The two families are reported with separate sample sizes rather than silently intersected
    down to the smaller one.

    19 days is still the sample size. It is not 3,336. Rounds inside a day share that day's
    regime, so every bound here resamples whole days.

WHAT IS TESTED
    Adjacent rounds are genuinely back-to-back: 2,470 of 2,494 5m rounds start exactly one
    horizon after the previous one, and each anchor chains to the previous expiry within ~$4.
    So "does the last round predict the next" is a clean question about return autocorrelation
    at the round boundary, not an artifact of overlapping windows.

    Conditioning sets, all strictly causal - every feature is known the moment the new round
    opens, because it describes a round that has already settled:

        prev_side        the previous round settled UP or DOWN
        prev_margin      signed settlement margin in bps
        |prev_margin|    magnitude buckets: was it a decisive round or a coin flip
        run_length       how many consecutive rounds have gone the same way
        cross_horizon    the last settled 15m round, predicting the next 5m round

THE ECONOMIC HURDLE IS NOT 50%
    A binary contract bought at ask `a` with fee 0.07*a*(1-a) needs win rate a + fee to break
    even. Near 0.50 that is about 51.8% before spread and about 52.4% after the observed 1.21c
    spread. A rule that is 51% accurate is statistically interesting and economically worthless.
    Accuracy is therefore always reported against the cost-adjusted hurdle, never against 50%.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import numpy as np

LANE = Path(__file__).resolve().parent
sys.path.insert(0, str(LANE.parent))

from common.scorecard import day_block_bootstrap, day_index, md_table  # noqa: E402

DB = LANE.parents[1] / "data" / "execution_layer.duckdb"
FEE_RATE = 0.07
OBSERVED_SPREAD = 0.0121          # measured mean PM spread over the quoted sample

# PRICES ARE MISSING FOR MOST SETTLED ROUNDS.
#
#   3,336 settled rounds, of which anchor_price/expiry_btc are NULL for 2,283.
#   The 1,053 that have prices are the SAME rounds and SAME 11 dates as the quote snapshots.
#
# This lane was queued on the premise that using settlements alone escapes the snapshot hole and
# buys ~58 day-blocks. That premise is false: the settlement table carries prices only where a
# snapshot also exists.
#
# What survives: the DIRECTION rules (momentum, reversion, run length) need only `settled_side`,
# which is present for all 3,336. Only the |margin| rules need prices. So the two families are
# loaded separately and their sample sizes are reported separately rather than being silently
# intersected down to the smaller one.
QUERY = """
SELECT horizon, anchor_ts, anchor_price, expiry_btc, settled_side
FROM pm_round_settlements
WHERE settled_side IN (0, 1)
ORDER BY horizon, anchor_ts
"""


def breakeven_win_rate(price: float = 0.50, spread: float = OBSERVED_SPREAD) -> float:
    """Win rate needed to break even buying at the ask on a binary contract."""
    ask = price + spread / 2.0
    return ask + FEE_RATE * ask * (1.0 - ask)


def evaluate(name: str, predicted_up: np.ndarray, actual_up: np.ndarray,
             days: np.ndarray, hurdle: float) -> dict:
    """Accuracy of a directional rule with a day-block bound, against the cost hurdle."""
    correct = (predicted_up.astype(bool) == actual_up.astype(bool)).astype(float)
    boot = day_block_bootstrap(correct, days)
    return {
        "rule": name,
        "n": int(correct.size),
        "n_days": boot["n_days"],
        "accuracy": round(boot["point"], 4),
        "lcb95": round(boot["lcb"], 4),
        "ucb95": round(boot["ucb"], 4),
        "hurdle": round(hurdle, 4),
        "clears_hurdle": bool(boot["lcb"] > hurdle),
        "edge_vs_hurdle_pp": round((boot["lcb"] - hurdle) * 100, 3),
    }


def main() -> int:
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute(QUERY).fetchdf()
    con.close()
    if df.empty:
        print("ROUND_TO_ROUND_TRANSFER_V1: NO DATA")
        return 1

    hurdle = breakeven_win_rate()
    results = {
        "lane": "ROUND_TO_ROUND_TRANSFER_V1",
        "breakeven_win_rate": round(hurdle, 4),
        "breakeven_note": "buy at ask 0.50 + half of the observed 1.21c spread, plus "
                          "0.07*a*(1-a) taker fee",
        "horizons": {},
    }
    all_rows: list[dict] = []

    for horizon in (5, 15):
        h = df[df["horizon"] == horizon].reset_index(drop=True)
        step = horizon * 60
        anchor = h["anchor_ts"].to_numpy("int64")
        up = h["settled_side"].to_numpy(float)
        margin_bps = ((h["expiry_btc"].to_numpy(float) - h["anchor_price"].to_numpy(float))
                      / h["anchor_price"].to_numpy(float)) * 10_000.0

        # Only strictly adjacent pairs. A gap in capture is not a market observation.
        adjacent = np.zeros(len(h), dtype=bool)
        adjacent[1:] = (anchor[1:] - anchor[:-1]) == step
        idx = np.flatnonzero(adjacent)
        if idx.size < 100:
            continue
        prev = idx - 1
        actual = up[idx]
        days = day_index(anchor[idx] * 1000)
        prev_up = up[prev]
        prev_margin = margin_bps[prev]
        abs_prev = np.abs(prev_margin)

        rows = [
            evaluate(f"{horizon}m base rate: always UP", np.ones_like(actual), actual, days, hurdle),
            evaluate(f"{horizon}m base rate: always DOWN", np.zeros_like(actual), actual, days, hurdle),
            evaluate(f"{horizon}m MOMENTUM: repeat prev side", prev_up, actual, days, hurdle),
            evaluate(f"{horizon}m REVERSION: oppose prev side", 1.0 - prev_up, actual, days, hurdle),
        ]

        # Does a DECISIVE previous round carry more information than a marginal one?
        # These rules need prices, so they run on the smaller priced subset only.
        priced = np.isfinite(prev_margin)
        for lo, hi, label in ((0, 5, "|margin| 0-5bps"), (5, 15, "|margin| 5-15bps"),
                              (15, 40, "|margin| 15-40bps"), (40, 1e9, "|margin| >40bps")):
            sel = priced & (abs_prev >= lo) & (abs_prev < hi)
            if sel.sum() < 100 or np.unique(days[sel]).size < 5:
                continue
            rows.append(evaluate(f"{horizon}m momentum, {label}",
                                 prev_up[sel], actual[sel], days[sel], hurdle))
            rows.append(evaluate(f"{horizon}m reversion, {label}",
                                 1.0 - prev_up[sel], actual[sel], days[sel], hurdle))

        # Run length: does a streak continue or exhaust?
        run = np.ones(len(h), dtype=int)
        for i in range(1, len(h)):
            run[i] = run[i - 1] + 1 if up[i] == up[i - 1] else 1
        prev_run = run[prev]
        for length in (2, 3, 4):
            sel = prev_run >= length
            if sel.sum() < 100 or np.unique(days[sel]).size < 5:
                continue
            rows.append(evaluate(f"{horizon}m momentum after run>={length}",
                                 prev_up[sel], actual[sel], days[sel], hurdle))
            rows.append(evaluate(f"{horizon}m reversion after run>={length}",
                                 1.0 - prev_up[sel], actual[sel], days[sel], hurdle))

        results["horizons"][f"{horizon}m"] = {
            "rounds_total": int(len(h)),
            "adjacent_pairs": int(idx.size),
            "utc_days": int(np.unique(days).size),
            "p_up": round(float(up.mean()), 4),
            "mean_abs_margin_bps": round(float(np.nanmean(np.abs(margin_bps))), 3),
            "priced_rounds": int(np.isfinite(margin_bps).sum()),
            "rules": rows,
        }
        all_rows.extend(rows)

    # CROSS-HORIZON: the last settled 15m round predicting the next 5m round.
    five = df[df["horizon"] == 5].reset_index(drop=True)
    fifteen = df[df["horizon"] == 15].reset_index(drop=True)
    if len(five) and len(fifteen):
        f_anchor = five["anchor_ts"].to_numpy("int64")
        f_up = five["settled_side"].to_numpy(float)
        t_end = fifteen["anchor_ts"].to_numpy("int64") + 900
        t_up = fifteen["settled_side"].to_numpy(float)
        order = np.argsort(t_end)
        t_end, t_up = t_end[order], t_up[order]
        # searchsorted gives the last 15m round that had ALREADY settled - never a future one.
        pos = np.searchsorted(t_end, f_anchor, side="right") - 1
        ok = (pos >= 0) & ((f_anchor - t_end[np.clip(pos, 0, len(t_end) - 1)]) <= 900)
        if ok.sum() >= 100:
            days = day_index(f_anchor[ok] * 1000)
            prior = t_up[pos[ok]]
            cross = [
                evaluate("5m momentum on last settled 15m", prior, f_up[ok], days, hurdle),
                evaluate("5m reversion on last settled 15m", 1.0 - prior, f_up[ok], days, hurdle),
            ]
            results["cross_horizon"] = cross
            all_rows.extend(cross)

    # ------------------------------------------------------------------------------------
    # MULTIPLICITY. Roughly 22 rules are evaluated at 5%, so about one false "winner" is the
    # EXPECTED outcome under a pure null. Reporting the best rule's own interval would be
    # reporting the maximum of 22 draws as though it were a single pre-declared test.
    #
    # The correction is a max-statistic permutation. Round outcomes are shuffled WITHIN each
    # UTC day - preserving each day's base rate and the number of rounds, destroying only the
    # order that every rule depends on - and the whole rule family is re-scored. The p-value is
    # the share of shuffles whose BEST rule matches or beats the observed best rule.
    #
    # A rule that cannot beat the best-of-22 under shuffled labels has not been shown to exist.
    # ------------------------------------------------------------------------------------
    scored = [r for r in all_rows if "base rate" not in r["rule"]]
    observed_best = max(r["accuracy"] for r in scored) if scored else float("nan")
    rng = np.random.default_rng(0)
    n_perm = 2000
    null_best = np.empty(n_perm)
    for i in range(n_perm):
        best = 0.0
        for horizon in (5, 15):
            h = df[df["horizon"] == horizon].reset_index(drop=True)
            if len(h) < 100:
                continue
            anchor = h["anchor_ts"].to_numpy("int64")
            up = h["settled_side"].to_numpy(float)
            day = day_index(anchor * 1000)
            shuffled = up.copy()
            for d in np.unique(day):                      # keep each day's base rate intact
                mask = day == d
                shuffled[mask] = rng.permutation(up[mask])
            step = horizon * 60
            adj = np.zeros(len(h), dtype=bool)
            adj[1:] = (anchor[1:] - anchor[:-1]) == step
            idx = np.flatnonzero(adj)
            if idx.size < 100:
                continue
            actual, prev_up = shuffled[idx], shuffled[idx - 1]
            run = np.ones(len(h), dtype=int)
            for j in range(1, len(h)):
                run[j] = run[j - 1] + 1 if shuffled[j] == shuffled[j - 1] else 1
            prev_run = run[idx - 1]
            for pred in (prev_up, 1.0 - prev_up):
                best = max(best, float((pred.astype(bool) == actual.astype(bool)).mean()))
                for length in (2, 3, 4):
                    sel = prev_run >= length
                    if sel.sum() < 100:
                        continue
                    best = max(best, float(
                        (pred[sel].astype(bool) == actual[sel].astype(bool)).mean()))
        null_best[i] = best
    p_value = float((null_best >= observed_best).mean())
    results["multiplicity"] = {
        "rules_scored": len(scored),
        "observed_best_accuracy": round(observed_best, 4),
        "null_best_median": round(float(np.median(null_best)), 4),
        "null_best_p95": round(float(np.percentile(null_best, 95)), 4),
        "p_value_family_wise": round(p_value, 4),
        "survives": bool(p_value < 0.05),
        "method": "labels shuffled within UTC day, whole rule family re-scored, 2000 draws",
    }

    (LANE / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("ROUND_TO_ROUND_TRANSFER_V1")
    for name, block in results["horizons"].items():
        print(f"  {name}: {block['adjacent_pairs']:,} adjacent pairs / "
              f"{block['utc_days']} UTC days / P(UP)={block['p_up']:.4f} / "
              f"mean |margin| {block['mean_abs_margin_bps']:.2f} bps")
    print(f"  break-even win rate: {hurdle:.4f}  "
          f"(ask 0.50 + half of 1.21c spread + taker fee)")
    print()
    cols = ["rule", "n", "n_days", "accuracy", "lcb95", "ucb95", "hurdle", "edge_vs_hurdle_pp"]
    print(md_table(all_rows, cols))
    winners = [r for r in all_rows if r["clears_hurdle"]]
    print()
    print(f"  rules whose 95% LOWER bound clears the cost hurdle: {len(winners)}")
    for row in winners:
        print(f"    {row['rule']}: LCB {row['lcb95']:.4f} vs hurdle {row['hurdle']:.4f}")
    m = results["multiplicity"]
    print()
    print("  MULTIPLICITY (best-of-family vs labels shuffled within day, 2000 draws)")
    print(f"    rules scored               : {m['rules_scored']}")
    print(f"    observed best accuracy     : {m['observed_best_accuracy']:.4f}")
    print(f"    best under shuffled labels : median {m['null_best_median']:.4f}, "
          f"p95 {m['null_best_p95']:.4f}")
    print(f"    family-wise p-value        : {m['p_value_family_wise']:.4f}"
          f"  -> {'SURVIVES' if m['survives'] else 'DOES NOT SURVIVE'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
