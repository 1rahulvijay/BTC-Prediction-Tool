"""COMPLETE-SET ARBITRAGE - the one test that cannot be wrong in an interesting way.

THE CLAIM
    A Polymarket binary market has two outcome tokens, UP and DOWN, on one condition_id.
    Exactly one pays $1 at settlement. So holding one of each is worth exactly $1, always,
    regardless of what BTC does.

        UP_ask + DOWN_ask < $1   ->  buy both, hold to settlement, collect $1.  Riskless.
        UP_bid + DOWN_bid > $1   ->  sell both, deliver $1 at settlement.       Riskless.

    No forecast. No direction. No volatility view. No model. The edge is present in the book
    or it is not, and the data already on disk answers it.

WHY THIS IS WORTH RUNNING BEFORE ANY MODEL
    Every predictive result in this repository has died on contact with a control. This one
    has no free parameters to overfit, no signal to be spurious, and no counterparty who
    "already knows" - the arithmetic is fixed by the settlement rule.

    If it exists at executable size, it is the only thing found so far that could carry
    capital. If it does not, that is equally decisive and costs one afternoon.

WHAT WOULD MAKE A NAIVE VERSION LIE
    1. STALE BOOKS. The two tokens update asynchronously. Comparing a fresh UP quote against a
       DOWN quote from 40 seconds ago invents arbitrage that never existed simultaneously.
       Every pair here must be quoted within MAX_STALENESS_MS of each other.
    2. UNSYNCHRONIZED BOOKS. The recorder flags `synchronized` and `valid`; 23,472 summaries
       are already marked invalid. Those are excluded.
    3. QUOTED SIZE IS NOT EXECUTABLE SIZE. A one-cent edge on 3 shares is noise. Everything is
       reported size-weighted, and the executable quantity is min(up_size, down_size).
    4. FEES AND SETTLEMENT COSTS. Observed trade fee_rate_bps is 0.0, but gas and any
       settlement-time fee are real. The result is reported across a COST SWEEP rather than at
       one assumed number, so the reader can see where it dies.

    python research/complete_set_arbitrage_test.py
"""
from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

DB = Path(__file__).resolve().parents[1] / "data" / "polymarket_l2.duckdb"
MAX_STALENESS_MS = 1_000          # both quotes must be live within this of each other
COST_SWEEP_CENTS = (0.0, 0.5, 1.0, 2.0, 3.0)


QUERY = """
WITH paired AS (
    SELECT condition_id,
           MAX(CASE WHEN outcome = 'UP'   THEN asset_id END) AS up_id,
           MAX(CASE WHEN outcome = 'DOWN' THEN asset_id END) AS down_id,
           MAX(horizon) AS horizon
    FROM pm_l2_markets
    GROUP BY condition_id
    HAVING COUNT(DISTINCT outcome) = 2
),
book AS (
    SELECT asset_id, exchange_ts_ms, best_bid, best_ask, best_bid_size, best_ask_size
    FROM pm_l2_book_summaries
    WHERE synchronized AND valid
      AND best_ask IS NOT NULL AND best_bid IS NOT NULL
      AND best_ask > 0 AND best_bid > 0
),
up AS (SELECT p.condition_id, p.horizon, b.* FROM paired p JOIN book b ON b.asset_id = p.up_id),
dn AS (SELECT p.condition_id, b.* FROM paired p JOIN book b ON b.asset_id = p.down_id)
SELECT
    up.condition_id,
    up.horizon,
    up.exchange_ts_ms                                   AS ts_ms,
    up.best_ask                                         AS up_ask,
    dn.best_ask                                         AS dn_ask,
    up.best_bid                                         AS up_bid,
    dn.best_bid                                         AS dn_bid,
    LEAST(up.best_ask_size, dn.best_ask_size)           AS buy_size,
    LEAST(up.best_bid_size, dn.best_bid_size)           AS sell_size,
    ABS(up.exchange_ts_ms - dn.exchange_ts_ms)          AS skew_ms
FROM up
ASOF JOIN dn
  ON up.condition_id = dn.condition_id
 AND up.exchange_ts_ms >= dn.exchange_ts_ms
WHERE ABS(up.exchange_ts_ms - dn.exchange_ts_ms) <= {staleness}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staleness-ms", type=int, default=MAX_STALENESS_MS)
    args = parser.parse_args()

    con = duckdb.connect(str(DB), read_only=True)
    rows = con.execute(QUERY.format(staleness=args.staleness_ms)).df()

    print("=" * 98)
    print("COMPLETE-SET ARBITRAGE - UP + DOWN must equal $1 at settlement")
    print("=" * 98)
    print(f"  paired quote observations : {len(rows):,}")
    print(f"  distinct conditions       : {rows['condition_id'].nunique()}")
    print(f"  max quote skew allowed    : {args.staleness_ms} ms "
          f"(median observed {rows['skew_ms'].median():.0f} ms)")
    if rows.empty:
        print("\n  no simultaneously-quoted pairs found - nothing to test")
        return 0

    rows["buy_cost"] = rows["up_ask"] + rows["dn_ask"]        # pay this, receive $1
    rows["sell_credit"] = rows["up_bid"] + rows["dn_bid"]     # receive this, owe $1

    print(f"\n  UP_ask + DOWN_ask : median {rows['buy_cost'].median():.4f}   "
          f"min {rows['buy_cost'].min():.4f}   p1 {rows['buy_cost'].quantile(0.01):.4f}")
    print(f"  UP_bid + DOWN_bid : median {rows['sell_credit'].median():.4f}   "
          f"max {rows['sell_credit'].max():.4f}   p99 {rows['sell_credit'].quantile(0.99):.4f}")

    print("\n" + "-" * 98)
    print("BUY SIDE - pay UP_ask + DOWN_ask, receive $1 at settlement")
    print(f"{'cost/cents':>12}{'opportunities':>16}{'% of quotes':>14}"
          f"{'median edge':>14}{'median size':>14}{'total $ edge':>16}")
    print("-" * 98)
    for cents in COST_SWEEP_CENTS:
        threshold = 1.0 - cents / 100.0
        hit = rows[rows["buy_cost"] < threshold]
        if hit.empty:
            print(f"{cents:>11.1f}c{0:>16}{0.0:>13.3f}%{'-':>14}{'-':>14}{'-':>16}")
            continue
        edge = threshold - hit["buy_cost"]
        dollars = float((edge * hit["buy_size"]).sum())
        print(f"{cents:>11.1f}c{len(hit):>16,}{len(hit) / len(rows) * 100:>13.3f}%"
              f"{edge.median():>14.4f}{hit['buy_size'].median():>14.1f}{dollars:>16,.0f}")

    print("\n" + "-" * 98)
    print("SELL SIDE - receive UP_bid + DOWN_bid, owe $1 at settlement")
    print(f"{'cost/cents':>12}{'opportunities':>16}{'% of quotes':>14}"
          f"{'median edge':>14}{'median size':>14}{'total $ edge':>16}")
    print("-" * 98)
    for cents in COST_SWEEP_CENTS:
        threshold = 1.0 + cents / 100.0
        hit = rows[rows["sell_credit"] > threshold]
        if hit.empty:
            print(f"{cents:>11.1f}c{0:>16}{0.0:>13.3f}%{'-':>14}{'-':>14}{'-':>16}")
            continue
        edge = hit["sell_credit"] - threshold
        dollars = float((edge * hit["sell_size"]).sum())
        print(f"{cents:>11.1f}c{len(hit):>16,}{len(hit) / len(rows) * 100:>13.3f}%"
              f"{edge.median():>14.4f}{hit['sell_size'].median():>14.1f}{dollars:>16,.0f}")

    print("\n" + "=" * 98)
    print("READING THIS")
    print("=" * 98)
    print("  The cost sweep is the point. A wide edge at 0c that vanishes by 1c is not an")
    print("  opportunity - it is the spread, and it belongs to whoever is quoting it.")
    print("  An edge that survives 2-3c of assumed cost, at size, on many distinct")
    print("  conditions, is the only genuinely riskless structure available here.")
    print()
    print("  Even then this is a QUOTED opportunity, not a filled one. Both legs must be")
    print("  taken simultaneously; taking one and missing the other converts a riskless")
    print("  spread into an outright directional position, which is the exact risk this")
    print("  structure exists to avoid. Executable confirmation requires live testing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
