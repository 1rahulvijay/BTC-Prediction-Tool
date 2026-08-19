"""DIRECT_PNL_DISTRIBUTION_V1 - the realized net-PnL distribution of every executable action.

WHAT THIS MEASURES, AND WHAT IT DELIBERATELY DOES NOT
    It does NOT train a model to forecast a PnL distribution. It measures the distribution that
    actually occurred, per action, at every decision moment where an executable quote existed.

    That ordering is the point. A forecaster of net PnL is only worth building if some action's
    realized net PnL has a positive lower bound somewhere in the state space. If every action
    loses at every level of selectivity, a better estimator of the loss is not progress.

THE ACTIONS
    BUY_UP     pay up_ask now, receive 1.0 if the round settles UP, else 0
    BUY_DOWN   pay down_ask now, receive 1.0 if it settles DOWN, else 0
    WAIT       0.0 exactly, always - the benchmark every other action must beat

COSTS ARE TAKEN, NOT ASSUMED
    Entry is the ASK, never the mid. A mid-price study cannot produce an executable edge; that
    is the same error that made the Polymarket atlas unbackfillable. The taker fee is the
    venue's own curve, 0.07*p*(1-p) per share, read from recorded round metadata where present.

    Settlement is the official recorded outcome, not a price-feed proxy.

INDEPENDENCE
    A round is observed many times, and adjacent rounds share market state. Every bound here
    resamples whole UTC DAYS. The per-snapshot rows are the population; the day is the unit.
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


def taker_fee(price: np.ndarray, rate: np.ndarray) -> np.ndarray:
    """Polymarket crypto taker fee per share. Zero at the extremes, maximal at 0.50."""
    p = np.clip(price, 0.0, 1.0)
    return rate * p * (1.0 - p)


def load() -> dict:
    con = duckdb.connect(str(DB), read_only=True)
    rows = con.execute(
        """
        SELECT s.ts, s.slug, s.horizon, s.seconds_left,
               s.up_ask, s.down_ask, s.up_bid, s.down_bid,
               s.p_hold_up, s.p_hold_down, s.decision_tier,
               s.up_top_ask_size, s.down_top_ask_size,
               t.settled_side, COALESCE(m.fee_rate, ?) AS fee_rate,
               COALESCE(m.fees_enabled, TRUE) AS fees_enabled
        FROM pm_round_snapshots s
        JOIN pm_round_settlements t USING (slug)
        LEFT JOIN pm_round_meta m USING (slug)
        WHERE s.up_ask IS NOT NULL AND s.down_ask IS NOT NULL
          AND s.up_ask > 0 AND s.up_ask < 1 AND s.down_ask > 0 AND s.down_ask < 1
          AND t.settled_side IN (0, 1)
        """,
        [FEE_RATE],
    ).fetchdf()
    con.close()
    return rows


def pnl_table(df) -> dict:
    up_win = df["settled_side"].to_numpy().astype(float)      # 1 = UP settled
    rate = np.where(df["fees_enabled"].to_numpy().astype(bool),
                    df["fee_rate"].to_numpy().astype(float), 0.0)
    up_ask = df["up_ask"].to_numpy().astype(float)
    down_ask = df["down_ask"].to_numpy().astype(float)

    # Net PnL per share, in cents of a $1 contract.
    buy_up = (up_win - up_ask - taker_fee(up_ask, rate)) * 100.0
    buy_down = ((1.0 - up_win) - down_ask - taker_fee(down_ask, rate)) * 100.0
    return {"BUY_UP": buy_up, "BUY_DOWN": buy_down, "WAIT": np.zeros_like(buy_up)}


def summarize(name: str, values: np.ndarray, days: np.ndarray) -> dict:
    boot = day_block_bootstrap(values, days, stat=np.mean)
    q = np.percentile(values, [1, 5, 25, 50, 75, 95, 99])
    tail = values[values <= np.percentile(values, 5)]
    return {
        "action": name,
        "n": int(values.size),
        "n_days": int(np.unique(days).size),
        "mean_cents": round(float(values.mean()), 4),
        "lcb95_cents": round(float(boot["lcb"]), 4),
        "p_profit": round(float((values > 0).mean()), 4),
        "q01": round(float(q[0]), 2), "q05": round(float(q[1]), 2),
        "q25": round(float(q[2]), 2), "q50": round(float(q[3]), 2),
        "q75": round(float(q[4]), 2), "q95": round(float(q[5]), 2),
        "q99": round(float(q[6]), 2),
        "expected_shortfall_5pct": round(float(tail.mean()) if tail.size else float("nan"), 2),
    }


def main() -> int:
    df = load()
    if df.empty:
        print("DIRECT_PNL_DISTRIBUTION_V1: NO DATA")
        return 1
    days = day_index((df["ts"].to_numpy().astype(float) * 1000).astype("int64"))
    actions = pnl_table(df)

    results = {
        "lane": "DIRECT_PNL_DISTRIBUTION_V1",
        "rows": int(len(df)),
        "rounds": int(df["slug"].nunique()),
        "utc_days": int(np.unique(days).size),
        "unit": "cents per $1 contract, net of taker fee, entered at the ask",
    }

    overall = [summarize(name, values, days) for name, values in actions.items()]
    results["unconditional"] = overall

    # SELECTIVITY. The claim under test is that trading only the best few percent turns a
    # losing action profitable. Selection uses ONLY information available at the snapshot.
    selective = []
    for name, edge_col in (("BUY_UP", "p_hold_up"), ("BUY_DOWN", "p_hold_down")):
        signal = df[edge_col].to_numpy().astype(float)
        values = actions[name]
        ok = np.isfinite(signal)
        for pct in (50, 25, 10, 5, 2, 1):
            if ok.sum() == 0:
                continue
            cut = np.percentile(signal[ok], 100 - pct)
            sel = ok & (signal >= cut)
            if sel.sum() < 200 or np.unique(days[sel]).size < 5:
                continue
            row = summarize(f"{name} top {pct}% by {edge_col}", values[sel], days[sel])
            row["selected_frac"] = round(float(sel.mean()), 4)
            selective.append(row)
    results["selective"] = selective

    # TIME SLICING. Late-round quotes are cheaper to be right about but pay less.
    by_time = []
    left = df["seconds_left"].to_numpy().astype(float)
    for lo, hi in ((0, 30), (30, 60), (60, 120), (120, 300), (300, 900)):
        sel = (left >= lo) & (left < hi)
        if sel.sum() < 200 or np.unique(days[sel]).size < 5:
            continue
        for name in ("BUY_UP", "BUY_DOWN"):
            row = summarize(f"{name} {lo}-{hi}s left", actions[name][sel], days[sel])
            by_time.append(row)
    results["by_time_remaining"] = by_time

    audit = tail_risk_audit(df, actions)
    results["tail_risk_audit"] = audit

    out = LANE / "results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    cols = ["action", "n", "n_days", "mean_cents", "lcb95_cents", "p_profit", "q05", "q50", "q95",
            "expected_shortfall_5pct"]
    print("DIRECT_PNL_DISTRIBUTION_V1")
    print(f"  {results['rows']:,} snapshots / {results['rounds']:,} rounds / "
          f"{results['utc_days']} UTC days")
    print()
    print(md_table(overall, cols))
    print()
    if selective:
        print(md_table(selective, cols))
    positive = [r for r in overall + selective + by_time
                if r["lcb95_cents"] > 0 and r["action"] != "WAIT"]
    print()
    print(f"  actions with a POSITIVE 95% lower bound: {len(positive)}")
    for row in positive:
        print(f"    {row['action']}: LCB {row['lcb95_cents']:+.4f}c")
    print()
    print("  TAIL-RISK AUDIT (one bet per round, EV at the 95% upper bound on loss rate)")
    print(md_table(audit, ["action", "bets_rounds", "losses_observed", "median_entry",
                           "gain_if_right_c", "loss_if_wrong_c", "loss_gain_ratio",
                           "p_loss_95ub", "ev_at_loss_ub_c"]))
    survives = [a for a in audit if a["ev_at_loss_ub_c"] > 0]
    print()
    print(f"  selections surviving the tail-risk audit: {len(survives)}")
    print(f"  wrote {out.relative_to(LANE.parents[1])}")
    return 0



# ---------------------------------------------------------------------------------------------
# TAIL-RISK AUDIT
#
# The selective rows above report a positive lower bound. That bound is not trustworthy, and
# this section exists to show why rather than to assert it.
#
# Three separate problems compound:
#
#   1. THE BET COUNT IS INFLATED. A round is snapshotted ~22 times. Those are not 22 bets; they
#      are one bet observed 22 times. The day-block bootstrap corrects for day-level dependence,
#      not for the same position being counted repeatedly inside a day.
#
#   2. THE PAYOFF IS VIOLENTLY ASYMMETRIC. Entry is at ~0.997, so the trade risks 99.7c to make
#      0.3c - a 332:1 loss-to-gain ratio. An estimator that has seen no losses cannot bound the
#      EV of a bet like that, because the entire EV lives in the unobserved tail.
#
#   3. A BOOTSTRAP CANNOT RESAMPLE AN EVENT THAT NEVER HAPPENED. If no day in the sample
#      contains a loss, every resampled day is profitable and the lower bound is positive by
#      construction. The interval describes the sample, not the risk.
#
# So instead of the bootstrap, this applies the rule of three: with zero losses in n rounds, the
# 95% upper bound on the loss rate is about 3/n. EV is then evaluated AT that bound - the worst
# loss rate still consistent with the evidence.
# ---------------------------------------------------------------------------------------------

def tail_risk_audit(df, actions) -> list[dict]:
    out = []
    for name, sig_col, ask_col in (("BUY_UP", "p_hold_up", "up_ask"),
                                   ("BUY_DOWN", "p_hold_down", "down_ask")):
        signal = df[sig_col].to_numpy(dtype=float)
        values = actions[name]
        for pct in (2, 1):
            cut = np.percentile(signal[np.isfinite(signal)], 100 - pct)
            sel = np.isfinite(signal) & (signal >= cut)
            if sel.sum() == 0:
                continue
            sub = df.loc[sel]
            # One bet per round: the earliest qualifying snapshot. This is the real bet count.
            first = sub.groupby("slug", sort=False).head(1).index
            pos = df.index.get_indexer(first)
            round_pnl = values[pos]
            rounds = int(round_pnl.size)
            losses = int((round_pnl < 0).sum())
            ask = float(sub.loc[first, ask_col].median())
            gain = (1.0 - ask) * 100.0
            loss = ask * 100.0
            # Rule of three when no loss is observed; otherwise the observed rate.
            p_loss_ub = 3.0 / rounds if losses == 0 else (
                losses / rounds + 1.96 * ((losses / rounds) * (1 - losses / rounds) / rounds) ** 0.5
            )
            out.append({
                "action": f"{name} top {pct}%",
                "bets_rounds": rounds,
                "snapshots": int(sel.sum()),
                "losses_observed": losses,
                "median_entry": round(ask, 4),
                "gain_if_right_c": round(gain, 3),
                "loss_if_wrong_c": round(loss, 2),
                "loss_gain_ratio": round(loss / gain, 1) if gain > 0 else None,
                "p_loss_95ub": round(p_loss_ub, 4),
                "ev_at_loss_ub_c": round((1 - p_loss_ub) * gain - p_loss_ub * loss, 3),
            })
    return out

if __name__ == "__main__":
    raise SystemExit(main())
