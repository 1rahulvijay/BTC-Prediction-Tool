"""Endpoint cost-clearance rate - can a BTCUSDT perp move clear the round trip at horizon h?

This is NOT a strategy test and NOT a profitability ceiling. It computes

    P(|endpoint move over h| > round_trip_cost)

the share of anchor timestamps at which a position held exactly h and closed AT THE
ENDPOINT would clear the assumed round trip, given perfect direction. It deliberately
excludes maximum favourable excursion, stop/target exits, variable holding periods,
spread and depth changes, funding, partial fills, and maker fill probability and
adverse selection. It screens ONE execution assumption: fixed horizon, endpoint exit.

CAUSAL MATCHING RULES (frozen)
    price(t)    last aggTrade at or before second t
    price(t+h)  last aggTrade at or before second t+h - never the first trade AFTER
                t+h, which would leak information past the endpoint
    anchors whose endpoint second has no eligible trade are EXCLUDED, never carried
    horizons never cross a day-file boundary

Motivation: PROFIT_CAMPAIGN_V1 measured profit factor 0.0000 at 30s over 374 trades
(zero winners), rising to 0.0025 at 180s and 0.0630 at 900s. That pattern says the
binding constraint may be horizon-vs-cost rather than signal quality. V1's archive is
24h; this reads ~3.5 years of perp aggTrades to test the same question out of sample.

Causal by construction: the move at t uses only prices at t and t+h, and every
candidate t is evaluated - no selection on outcome.

    python backend/research/event_conditional_v1/measure_horizon_viability.py --stride 10
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import random
import sys

import duckdb

CACHE = r"C:\Users\rahul\Documents\BTC-Prediction-Tool\data\backfill_cache"

# Seconds. 30/180/900 match the V1 campaign horizons exactly so results are comparable.
HORIZONS = [30, 60, 180, 300, 900, 1800, 3600]

# Round-trip cost in bps. 12.0 is the V1 FROZEN cost (5bps taker + 1bp impact, per leg,
# two legs). The rest are sensitivities, NOT alternative frozen assumptions:
#   10.0  taker 4bps + 1bp impact  (BNB discount / VIP-1 territory)
#    8.0  taker 3bps + 1bp impact  (higher VIP tier)
#    6.0  maker 2bps + 1bp impact  (Priority-2 maker conversion, base maker rate)
#    4.0  maker 1bps + 1bp impact  (maker at tier)
BARRIERS = [12.0, 10.0, 8.0, 6.0, 4.0]

# Admission uses the day-block bootstrap lower bound, never the point estimate: anchors
# within a day overlap heavily, so row counts vastly overstate independent sample size.
BOOTSTRAP_SEED = 20260728
BOOTSTRAP_RESAMPLES = 2000


def day_block_lb95(day_values: list[float], seed: int = BOOTSTRAP_SEED,
                   resamples: int = BOOTSTRAP_RESAMPLES) -> float | None:
    """5th percentile of the day-resampled mean. Days are the resampling unit."""
    k = len(day_values)
    if k < 5:
        return None
    rng = random.Random(seed)
    draws = sorted(sum(day_values[rng.randrange(k)] for _ in range(k)) / k
                   for _ in range(resamples))
    return draws[int(0.05 * resamples)]

PER_DAY_SQL = """
WITH sec AS (
    SELECT CAST(transact_time / 1000 AS BIGINT) AS s,
           last(price ORDER BY transact_time)   AS px
    FROM read_csv(?, header = true, columns = {
        'agg_trade_id':'BIGINT','price':'DOUBLE','quantity':'DOUBLE',
        'first_trade_id':'BIGINT','last_trade_id':'BIGINT',
        'transact_time':'BIGINT','is_buyer_maker':'VARCHAR'})
    GROUP BY 1
)
SELECT ? AS horizon_s,
       count(*)                                        AS n,
       avg(abs(mv))                                    AS mean_abs_bps,
       median(abs(mv))                                 AS median_abs_bps,
       quantile_cont(abs(mv), 0.90)                    AS q90_abs_bps,
       quantile_cont(abs(mv), 0.99)                    AS q99_abs_bps
FROM (
    SELECT (b.px - a.px) / a.px * 10000.0 AS mv
    FROM sec a JOIN sec b ON b.s = a.s + ?
) t
"""

EXCEED_SQL = """
WITH sec AS (
    SELECT CAST(transact_time / 1000 AS BIGINT) AS s,
           last(price ORDER BY transact_time)   AS px
    FROM read_csv(?, header = true, columns = {
        'agg_trade_id':'BIGINT','price':'DOUBLE','quantity':'DOUBLE',
        'first_trade_id':'BIGINT','last_trade_id':'BIGINT',
        'transact_time':'BIGINT','is_buyer_maker':'VARCHAR'})
    GROUP BY 1
), mv AS (
    SELECT abs((b.px - a.px) / a.px * 10000.0) AS m
    FROM sec a JOIN sec b ON b.s = a.s + ?
)
SELECT count(*) AS n, __COLS__ FROM mv
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stride", type=int, default=16,
                    help="sample every Nth day (1 = all 1,286 days)")
    ap.add_argument("--out", default="horizon_viability_result.json")
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(CACHE, "BTCUSDT-perp-aggTrades-*.csv")))
    if not files:
        print("no perp aggTrade files found")
        return 1
    sample = files[::a.stride]
    print(f"perp days available : {len(files)}")
    print(f"sampling stride     : {a.stride}  ->  {len(sample)} days")
    print(f"span                : {os.path.basename(sample[0])[-14:-4]}"
          f" .. {os.path.basename(sample[-1])[-14:-4]}")
    print(f"horizons (s)        : {HORIZONS}")
    print(f"barriers (bps)      : {BARRIERS}   [12.0 = V1 frozen cost]")
    print()

    con = duckdb.connect()
    con.execute("PRAGMA threads=4")

    cols = ", ".join(f"avg(CASE WHEN m > {b} THEN 1.0 ELSE 0.0 END) AS ex_{str(b).replace('.','_')}"
                     for b in BARRIERS)
    exceed_sql = EXCEED_SQL.replace("__COLS__", cols)

    # accumulator: horizon -> per-day rows (day-level so we can day-block later)
    acc = {h: [] for h in HORIZONS}
    for i, f in enumerate(sample, 1):
        day = os.path.basename(f)[-14:-4]
        try:
            for h in HORIZONS:
                st = con.execute(PER_DAY_SQL, [f, h, h]).fetchone()
                ex = con.execute(exceed_sql, [f, h]).fetchone()
                if not st or not st[1]:
                    continue
                acc[h].append({
                    "day": day, "n": st[1], "mean_abs": st[2], "median_abs": st[3],
                    "q90_abs": st[4], "q99_abs": st[5],
                    "exceed": {str(b): ex[1 + j] for j, b in enumerate(BARRIERS)},
                })
        except Exception as exc:
            print(f"  skip {day}: {type(exc).__name__}: {str(exc)[:60]}")
            continue
        if i % 10 == 0 or i == len(sample):
            print(f"  processed {i}/{len(sample)} days ({day})")

    print()
    print("=" * 84)
    print("ENDPOINT COST-CLEARANCE RATE (perp, day-averaged, with day-block LB95)")
    print("=" * 84)
    print(f"{'horizon':>8} {'days':>5} {'med|mv|':>9} "
          + " ".join(f"{'P>'+str(b):>8}" for b in BARRIERS)
          + f" | {'LB95>12':>8} {'LB95>6':>8}")
    print("-" * 84)

    day_ids = sorted(os.path.basename(f)[-14:-4] for f in sample)
    out = {
        "stride": a.stride, "n_days": len(sample), "barriers": BARRIERS,
        "days_available": len(files),
        "sample_manifest_sha256": hashlib.sha256("|".join(day_ids).encode()).hexdigest(),
        "sampled_days": day_ids,
        "bootstrap_seed": BOOTSTRAP_SEED, "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "dataset_role": "DESIGN_ONLY",
        "horizons": {},
    }
    for h in HORIZONS:
        rows = acc[h]
        if not rows:
            continue
        nd = len(rows)
        med = sum(r["median_abs"] for r in rows) / nd
        mean = sum(r["mean_abs"] for r in rows) / nd
        q90 = sum(r["q90_abs"] for r in rows) / nd
        exc = {str(b): sum(r["exceed"][str(b)] for r in rows) / nd for b in BARRIERS}
        lb12 = day_block_lb95([r["exceed"]["12.0"] for r in rows])
        lb6 = day_block_lb95([r["exceed"]["6.0"] for r in rows])
        print(f"{h:>7}s {nd:>5} {med:>9.2f} "
              + " ".join(f"{exc[str(b)]*100:>7.2f}%" for b in BARRIERS)
              + f" | {lb12*100:>7.2f}% {lb6*100:>7.2f}%")
        out["horizons"][str(h)] = {
            "n_days": nd, "median_abs_bps": med, "mean_abs_bps": mean, "q90_abs_bps": q90,
            "p_exceed": exc,
            "lb95": {str(b): day_block_lb95([r["exceed"][str(b)] for r in rows])
                     for b in BARRIERS},
            "per_day": [{"day": r["day"], "median_abs": r["median_abs"],
                         "exceed": r["exceed"]} for r in rows],
        }

    print("-" * 84)
    print("P>12 is the share of anchors clearing the V1 frozen round trip with PERFECT")
    print("direction, under fixed-horizon endpoint exit. Not a profitability ceiling.")
    print("Admission uses LB95 (day-block bootstrap), never the point estimate.")

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
