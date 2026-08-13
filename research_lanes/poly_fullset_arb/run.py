"""POLY_FULLSET_ARB_V1 and HEDGED_POLY_MM_V1 — the two lanes the taker fee did not close.

POLY_FULLSET_ARB_V1
    UP and DOWN are the two outcomes of one binary market, so exactly one pays $1. Buying both
    for less than $1 all-in is a mechanical inconsistency, not a forecast. Scan for it.

        cost = ask_UP + ask_DOWN + fee(ask_UP) + fee(ask_DOWN)
        edge = 1 - cost

HEDGED_POLY_MM_V1
    POLYMARKET_RESIDUAL_V1 was killed by the taker fee. Makers pay zero platform fee, so the
    maker side deserves its own test.

    WHAT THIS CAN AND CANNOT MEASURE. There is no fill data here - only quotes. So this
    computes an UPPER BOUND: assume the quote is filled whenever posted, at the quoted price,
    with NO adverse selection. Real making is strictly worse, because you are filled
    preferentially when the market is about to move against you.

    An upper bound is still decisive in one direction. If even the no-adverse-selection case
    loses money, the lane is closed and no fill model rescues it. If it wins, the result is
    INCONCLUSIVE and the next step is measuring toxicity, not trading.

    python research_lanes/poly_fullset_arb/run.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

LANE = Path(__file__).resolve().parent
sys.path.insert(0, str(LANE.parent))
from common.pm_data import load_official, round_bootstrap  # noqa: E402

TAKER_COEF = 0.07
#: Polymarket crypto maker rebate pool share, as documented. A VARIABLE, not guaranteed income
#: - it is performance-based and the published rate can change. Modelled at 0 in the headline
#: and shown separately, so no result depends on it.
REBATE_SHARE = 0.20


def taker_fee(price):
    p = np.asarray(price, float)
    return TAKER_COEF * p * (1.0 - p)


def fullset_arb(d) -> dict:
    """Buy UP and DOWN together for < $1 all-in."""
    up_a = d["up_ask"].to_numpy(float)
    dn_a = d["down_ask"].to_numpy(float)
    ok = np.isfinite(up_a) & np.isfinite(dn_a) & (up_a > 0) & (dn_a > 0)

    gross = up_a[ok] + dn_a[ok]                       # before fees
    fees = taker_fee(up_a[ok]) + taker_fee(dn_a[ok])
    net_cost = gross + fees
    edge = 1.0 - net_cost

    size = np.minimum(d["up_top_ask_size"].to_numpy(float)[ok],
                      d["down_top_ask_size"].to_numpy(float)[ok])
    size = np.where(np.isfinite(size), size, 0.0)

    out = {
        "n_snapshots": int(ok.sum()),
        "gross_sum_median": float(np.median(gross)),
        "gross_sum_min": float(gross.min()),
        "pct_gross_below_1": float((gross < 1.0).mean()),
        "pct_net_below_1": float((net_cost < 1.0).mean()),
        "best_net_edge": float(edge.max()),
        "median_fee_both_legs": float(np.median(fees)),
    }
    hits = edge > 0
    out["n_executable"] = int(hits.sum())
    if hits.any():
        out["executable_edge_mean"] = float(edge[hits].mean())
        out["executable_size_median"] = float(np.median(size[hits]))
        out["executable_dollar_pnl_total"] = float((edge[hits] * size[hits]).sum())
    return out


def hedged_maker_upper_bound(d) -> dict:
    """UPPER BOUND on maker EV: filled at the quote, no adverse selection, no queue risk."""
    y = d["settled_up"].to_numpy(float)
    up_b = d["up_bid"].to_numpy(float)
    dn_b = d["down_bid"].to_numpy(float)
    rid = d["round_id"].to_numpy()

    # Post a bid on UP: pay up_bid, receive 1 if UP settles. Maker platform fee is zero.
    pnl_up = y - up_b
    # Post a bid on DOWN: pay down_bid, receive 1 if DOWN settles.
    pnl_dn = (1.0 - y) - dn_b
    # Two-sided: both quotes resting. Exactly one side pays out; you hold both legs, which is
    # a complete set, so the combined position is worth exactly $1 at settlement.
    pnl_two = 1.0 - (up_b + dn_b)

    res = {}
    for name, pnl in (("bid_up_only", pnl_up), ("bid_down_only", pnl_dn),
                      ("two_sided_both_filled", pnl_two)):
        b = round_bootstrap(pnl, rid, np.mean, n_boot=800)
        res[name] = {"ev": b["point"], "lcb": b["lcb"], "ucb": b["ucb"],
                     "n_rows": b["n_rows"], "n_rounds": b["n_rounds"]}

    spread = (d["up_ask"] - d["up_bid"]).to_numpy(float)
    res["up_spread_median"] = float(np.nanmedian(spread))
    res["up_spread_p25"] = float(np.nanpercentile(spread, 25))
    res["up_spread_p75"] = float(np.nanpercentile(spread, 75))
    # Rebate, shown separately so nothing above depends on it.
    res["rebate_share_modelled"] = REBATE_SHARE
    res["note"] = "upper bound: no adverse selection, no queue position, guaranteed fill"
    return res


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", type=Path)
    parser.add_argument("--settlements", type=Path)
    parser.add_argument("--output", type=Path, default=LANE / "results.json")
    args = parser.parse_args()
    load_kwargs = {}
    if args.snapshots:
        load_kwargs["snapshots_path"] = args.snapshots
    if args.settlements:
        load_kwargs["settlements_path"] = args.settlements
    d = load_official(**load_kwargs)
    if d.empty:
        print("no joined rows"); return 1
    print(f"rows={len(d):,}  rounds={d.round_id.nunique():,}  days={d.day.nunique()}")

    print("\n=== POLY_FULLSET_ARB_V1 ===")
    a = fullset_arb(d)
    print(f"  snapshots with both asks     {a['n_snapshots']:,}")
    print(f"  median ask_UP + ask_DOWN     {a['gross_sum_median']:.4f}   (parity = 1.0000)")
    print(f"  minimum observed sum         {a['gross_sum_min']:.4f}")
    print(f"  % where GROSS sum < 1.00     {a['pct_gross_below_1']:.3%}")
    print(f"  median fee, both legs        {a['median_fee_both_legs']:.4f}")
    print(f"  % where NET cost < 1.00      {a['pct_net_below_1']:.3%}")
    print(f"  executable opportunities     {a['n_executable']:,}")
    if a["n_executable"]:
        print(f"    mean edge/share            {a['executable_edge_mean']:+.4f}")
        print(f"    median executable size     {a['executable_size_median']:.1f}")
        print(f"    total $ across all hits    {a['executable_dollar_pnl_total']:+.2f}")

    print("\n=== HEDGED_POLY_MM_V1 (upper bound) ===")
    m = hedged_maker_upper_bound(d)
    print(f"  UP spread  p25/median/p75    {m['up_spread_p25']:.4f} / "
          f"{m['up_spread_median']:.4f} / {m['up_spread_p75']:.4f}")
    for k in ("bid_up_only", "bid_down_only", "two_sided_both_filled"):
        r = m[k]
        v = "POSITIVE" if r["lcb"] > 0 else ("marginal" if r["ev"] > 0 else "negative")
        print(f"  {k:<24}EV={r['ev']:+.4f}  95%CI[{r['lcb']:+.4f},{r['ucb']:+.4f}]  "
              f"rounds={r['n_rounds']}  {v}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"fullset_arb": a, "hedged_maker_upper_bound": m,
                    "n_rows": int(len(d)), "n_rounds": int(d.round_id.nunique()),
                    "n_days": int(d.day.nunique())}, indent=2, default=float) + "\n",
        encoding="utf-8")
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
