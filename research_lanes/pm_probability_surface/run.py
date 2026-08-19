"""PM_PROBABILITY_SURFACE_V1 - cross-strike consistency between same-expiry 5m and 15m rounds.

THE PROPOSED LANE ASSUMED A STRIKE LADDER THAT DOES NOT EXIST
    The idea was to fit p(K,T) across many strikes at one expiry and trade the outliers.
    Polymarket's BTC up/down rounds are SINGLE-STRIKE: the anchor is the price at window open.
    There is no ladder to fit.

    What does exist: a 5m round and a 15m round expiring at the same second with different
    anchors. Same expiry, two strikes. That supports one clean structural trade:

        buy UP   on the LOWER  strike   pays 1 if S_T >  K_lo
        buy DOWN on the HIGHER strike   pays 1 if S_T <= K_hi

    If both legs settled off the SAME S_T, the combined payoff would be >= 1 in every state, and
    2 when S_T lands inside the band. Any total cost below 1.0 would then be riskless profit.

WHY IT IS NOT RISKLESS - THE FINDING THAT MATTERS
    The two legs do NOT settle off the same observable.

        5m  rounds -> chainlink_btc_usd_twap_30s
        15m rounds -> chainlink_btc_usd_twap_60s

    In the recorded data 217 of 246 settled pairs have a DIFFERENT expiry price, and the state
    that is impossible under a shared reference - the higher strike settling UP while the lower
    strike settles DOWN - is actually observed.

    So this is not an arbitrage. It is a spread trade carrying basis risk between a 30-second and
    a 60-second TWAP. This lane measures it as such and prices that basis instead of assuming it
    away. Reporting "riskless profit" here would be reporting a fiction.
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

QUERY = """
WITH m AS (
  SELECT mt.slug, mt.horizon, mt.end_ts, mt.required_reference_source AS ref,
         st.anchor_price AS k, st.settled_side AS up, st.expiry_btc
  FROM pm_round_meta mt JOIN pm_round_settlements st USING (slug)
  WHERE st.settled_side IN (0, 1) AND st.anchor_price IS NOT NULL
),
pairs AS (
  SELECT a.slug s5, b.slug s15, a.end_ts, a.k k5, b.k k15,
         a.up up5, b.up up15, a.expiry_btc px5, b.expiry_btc px15,
         a.ref ref5, b.ref ref15
  FROM m a JOIN m b ON a.end_ts = b.end_ts AND a.horizon = 5 AND b.horizon = 15
  WHERE abs(a.k - b.k) > 0.5
)
SELECT p.*, x.ts, x.up_ask a5_up, x.down_ask a5_dn,
       y.up_ask a15_up, y.down_ask a15_dn,
       x.up_top_ask_size sz5_up, x.down_top_ask_size sz5_dn,
       y.up_top_ask_size sz15_up, y.down_top_ask_size sz15_dn
FROM pairs p
JOIN pm_round_snapshots x ON x.slug = p.s5
JOIN pm_round_snapshots y ON y.slug = p.s15 AND abs(y.ts - x.ts) < 1.0
WHERE x.up_ask IS NOT NULL AND y.up_ask IS NOT NULL
  AND x.down_ask IS NOT NULL AND y.down_ask IS NOT NULL
"""


def taker_fee(p, rate=FEE_RATE):
    p = np.clip(np.asarray(p, dtype=float), 0.0, 1.0)
    return rate * p * (1.0 - p)


def main() -> int:
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute(QUERY).fetchdf()
    con.close()
    if df.empty:
        print("PM_PROBABILITY_SURFACE_V1: NO DATA")
        return 1

    five_is_low = df["k5"].to_numpy(float) < df["k15"].to_numpy(float)
    ask_up_lo = np.where(five_is_low, df["a5_up"], df["a15_up"]).astype(float)
    ask_dn_hi = np.where(five_is_low, df["a15_dn"], df["a5_dn"]).astype(float)
    up_lo = np.where(five_is_low, df["up5"], df["up15"]).astype(float)
    up_hi = np.where(five_is_low, df["up15"], df["up5"]).astype(float)
    size_lo = np.where(five_is_low, df["sz5_up"], df["sz15_up"]).astype(float)
    size_hi = np.where(five_is_low, df["sz15_dn"], df["sz5_dn"]).astype(float)

    cost = ask_up_lo + taker_fee(ask_up_lo) + ask_dn_hi + taker_fee(ask_dn_hi)
    payoff = up_lo + (1.0 - up_hi)          # official per-market settlement on both legs
    profit_c = (payoff - cost) * 100.0
    days = day_index((df["ts"].to_numpy(float) * 1000).astype("int64"))
    boot = day_block_bootstrap(profit_c, days)

    # One bet per pair. Quoting the same spread 100 times inside one round does not create 100
    # independent opportunities, so the bet count must collapse to the pair.
    #
    # THE ENTRY RULE MUST BE CAUSAL. Selecting the pair's CHEAPEST quote - the obvious way to
    # write this - picks the best entry in hindsight and inflates the result by roughly 19c per
    # bet. Nothing at decision time knows which quote will turn out cheapest. Two admissible
    # rules are reported instead:
    #
    #   first     - take the earliest simultaneous quote, no selection at all
    #   threshold - take the first quote whose all-in cost is below 1.0, else no trade
    df = df.assign(_cost=cost, _profit=profit_c, _payoff=payoff,
                   _cap=np.minimum(size_lo, size_hi), _day=days)
    ordered = df.sort_values("ts")
    first = ordered.groupby("end_ts", sort=False).head(1)
    cheap = ordered[ordered["_cost"] < 1.0].groupby("end_ts", sort=False).head(1)

    pair_profit = first["_profit"].to_numpy(float)
    pair_boot = day_block_bootstrap(pair_profit, first["_day"].to_numpy())
    if len(cheap):
        cheap_boot = day_block_bootstrap(cheap["_profit"].to_numpy(float),
                                         cheap["_day"].to_numpy())
    else:
        cheap_boot = {"point": float("nan"), "lcb": float("nan"),
                      "ucb": float("nan"), "n_days": 0}
    # Retained only to quantify the bias, never reported as a result.
    hindsight = df.loc[df.groupby("end_ts")["_cost"].idxmin()]
    hindsight_mean = float(hindsight["_profit"].mean())
    best = first

    results = {
        "lane": "PM_PROBABILITY_SURFACE_V1",
        "structure": "long UP on lower strike + DOWN on higher strike, same expiry second",
        "observations": int(len(df)),
        "distinct_pairs": int(best.shape[0]),
        "utc_days": int(np.unique(days).size),
        "settlement_references": {
            "5m": sorted({str(v) for v in df["ref5"].fillna("unrecorded")}),
            "15m": sorted({str(v) for v in df["ref15"].fillna("unrecorded")}),
        },
        "riskless_if_shared_reference": False,
        "payoff_zero_states": {
            "all_observations": int((payoff == 0).sum()),
            "distinct_pairs": int((best["_payoff"].to_numpy() == 0).sum()),
            "note": "payoff 0 is impossible under a shared settlement price; it occurs because "
                    "the 5m leg settles on a 30s TWAP and the 15m leg on a 60s TWAP",
        },
        "cost": {
            "median": round(float(np.median(cost)), 4),
            "min": round(float(cost.min()), 4),
            "frac_below_1": round(float((cost < 1.0).mean()), 4),
        },
        "profit_cents_all_observations": {
            "mean": round(boot["point"], 4), "lcb95": round(boot["lcb"], 4),
            "ucb95": round(boot["ucb"], 4), "n_days": boot["n_days"],
        },
        "profit_cents_first_quote_per_pair": {
            "mean": round(pair_boot["point"], 4), "lcb95": round(pair_boot["lcb"], 4),
            "ucb95": round(pair_boot["ucb"], 4), "n_days": pair_boot["n_days"],
            "n_bets": int(pair_profit.size),
        },
        "profit_cents_cost_below_1_rule": {
            "mean": round(cheap_boot["point"], 4), "lcb95": round(cheap_boot["lcb"], 4),
            "ucb95": round(cheap_boot["ucb"], 4), "n_days": cheap_boot["n_days"],
            "n_bets": int(len(cheap)),
        },
        "hindsight_bias_check": {
            "cheapest_quote_per_pair_mean": round(hindsight_mean, 4),
            "causal_first_quote_mean": round(pair_boot["point"], 4),
            "inflation_cents": round(hindsight_mean - pair_boot["point"], 4),
            "note": "selecting the cheapest quote per pair is not executable; the gap is the "
                    "size of the look-ahead bias it introduces",
        },
        "capacity": {
            "median_shares_min_leg": round(float(np.median(best["_cap"])), 1),
            "median_dollar_profit_per_pair": round(
                float(np.median(pair_profit / 100.0 * best["_cap"])), 4),
        },
    }
    (LANE / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("PM_PROBABILITY_SURFACE_V1")
    print(f"  {results['observations']:,} simultaneous quotes / "
          f"{results['distinct_pairs']} pairs / {results['utc_days']} UTC days")
    print(f"  5m  settles on: {results['settlement_references']['5m']}")
    print(f"  15m settles on: {results['settlement_references']['15m']}")
    print("  -> not a shared observable, so the structure is NOT riskless")
    print()
    print(md_table([
        {"basis": "all observations", **results["profit_cents_all_observations"]},
        {"basis": "first quote per pair (causal)",
         **results["profit_cents_first_quote_per_pair"]},
        {"basis": "first quote under 1.0 (causal)",
         **results["profit_cents_cost_below_1_rule"]},
    ], ["basis", "mean", "lcb95", "ucb95", "n_days", "n_bets"]))
    print()
    print(f"  median combined cost     : {results['cost']['median']:.4f} "
          f"(riskless threshold would be 1.0000)")
    print(f"  fraction of quotes < 1.0 : {results['cost']['frac_below_1']:.2%}")
    print(f"  payoff-zero states       : {results['payoff_zero_states']['all_observations']} obs "
          f"/ {results['payoff_zero_states']['distinct_pairs']} pairs")
    print(f"  median capacity          : {results['capacity']['median_shares_min_leg']:.0f} shares")
    hb = results["hindsight_bias_check"]
    print(f"  hindsight check          : cheapest-quote selection reports "
          f"{hb['cheapest_quote_per_pair_mean']:+.2f}c vs causal "
          f"{hb['causal_first_quote_mean']:+.2f}c "
          f"({hb['inflation_cents']:+.2f}c of look-ahead)")
    verdict = "POSITIVE" if pair_boot["lcb"] > 0 else "NOT ESTABLISHED"
    print(f"  95% lower bound per bet  : {pair_boot['lcb']:+.4f}c -> {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
