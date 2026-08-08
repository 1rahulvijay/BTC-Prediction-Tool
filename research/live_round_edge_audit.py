"""
Does the served lean have ANY edge on live recorded rounds?

Every previous accuracy number in this repository comes from the verifier's own tables,
which grade under the training contract. `price_to_beat` is different: it is the venue's
question, recorded live - anchor at the window open, settle at the window close, UP if the
end is >= the anchor. There is nothing to grade and nothing to interpret. Either the lean
was on the winning side or it was not.

This asks the only question that matters before any of the machinery is worth running:
on real recorded rounds, is the lean better than a coin flip, and does it clear costs?

Standalone. Read-only. Prints its own result and exits non-zero only on a data problem,
never on an unfavourable finding - a study that fails when the answer is bad is not a study.

    python research/live_round_edge_audit.py
"""

from __future__ import annotations

import math
import os
import random
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DB = os.environ.get("BTC_ANALYTICS_DB", str(ROOT / "data" / "analytics.duckdb"))

#: Polymarket binary round trip. A share bought at the ask and settled costs the spread
#: crossed on entry plus the venue fee. Expressed as the extra win rate needed above 50%.
#: 2 cents of round-trip cost on a ~50c binary is 2 percentage points of win rate.
COST_CENTS = 2.0


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval. Normal-approximation intervals are wrong near 0.5 at small n."""
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    d = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def fetch(conn, horizon: int) -> list[tuple[str, str, int, str]]:
    return conn.execute("""
        SELECT our_direction, actual_direction, timestamp,
               COALESCE(NULLIF(lean_source, ''), 'unknown')
        FROM price_to_beat
        WHERE resolved
          AND horizon = ?
          AND our_direction IN ('UP', 'DOWN')
          AND actual_direction IN ('UP', 'DOWN')
          AND COALESCE(source, 'pyth') = 'pyth'
        ORDER BY timestamp
    """, (horizon,)).fetchall()


def report(rows, label: str) -> dict:
    n = len(rows)
    if n == 0:
        print(f"  {label:<26} no rows")
        return {"n": 0}
    hits = sum(1 for lean, actual, _, _ in rows if lean == actual)
    rate = hits / n
    lo, hi = wilson(hits, n)
    verdict = ("BEATS a coin flip" if lo > 0.50
               else "LOSES to a coin flip" if hi < 0.50
               else "indistinguishable from a coin flip")
    print(f"  {label:<26} n={n:>5}  win={rate:.4f}  95% CI [{lo:.4f}, {hi:.4f}]  {verdict}")
    return {"n": n, "hits": hits, "rate": rate, "lo": lo, "hi": hi}


def main() -> int:
    if not Path(DB).exists():
        print(f"no analytics database at {DB}")
        return 2

    conn = duckdb.connect(DB, read_only=True)
    try:
        print("=" * 78)
        print("LIVE ROUND EDGE AUDIT - the venue's own question, on recorded rounds")
        print("=" * 78)

        for horizon in (5, 15):
            rows = fetch(conn, horizon)
            if not rows:
                print(f"\n{horizon}m: no resolved rounds")
                continue

            span_days = (rows[-1][2] - rows[0][2]) / 86_400_000.0
            print(f"\n{horizon}m  ({len(rows)} resolved rounds over {span_days:.0f} days)")
            print("-" * 78)
            overall = report(rows, "all leans")

            for src in sorted({r[3] for r in rows}):
                report([r for r in rows if r[3] == src], f"lean_source={src}")

            half = len(rows) // 2
            report(rows[:half], "first half (by time)")
            report(rows[half:], "second half (by time)")

            # THE SHAPE OF THE BET vs THE SHAPE OF THE MARKET.
            up_leans = sum(1 for lean, _, _, _ in rows if lean == "UP") / len(rows)
            up_actual = sum(1 for _, a, _, _ in rows if a == "UP") / len(rows)
            print(f"  {'directional bias':<26} leans UP {up_leans:.1%} of the time; "
                  f"the market settles UP {up_actual:.1%} of the time")
            # A bettor with this bias and NO information scores this by arithmetic alone.
            bias_only = up_leans * up_actual + (1 - up_leans) * (1 - up_actual)
            print(f"  {'bias-only expectation':<26} {bias_only:.4f}  "
                  f"(what that bias alone earns against this market, with zero skill)")
            print(f"  {'observed minus bias-only':<26} {overall['rate'] - bias_only:+.4f}"
                  f"  <- the part attributable to information")

            # PERMUTATION NULL. Shuffle the leans against the outcomes; anything the real
            # sequence achieves that the shuffles also achieve is not evidence.
            rng = random.Random(20260808)
            leans = [r[0] for r in rows]
            actuals = [r[1] for r in rows]
            null = []
            for _ in range(2000):
                rng.shuffle(leans)
                null.append(sum(1 for a, b in zip(leans, actuals) if a == b) / len(rows))
            null.sort()
            better = sum(1 for v in null if v >= overall["rate"])
            print(f"  {'shuffled null':<26} median {null[len(null) // 2]:.4f}, "
                  f"95th pct {null[int(0.95 * len(null))]:.4f}")
            print(f"  {'p(shuffle >= observed)':<26} {better / len(null):.3f}")

            # WHERE THE BIAS LIVES. If the lean tracked the trend, the UP share would
            # differ between TRENDING_UP and TRENDING_DOWN. If it does not, the bias is a
            # property of the head rather than a response to the market.
            regimes = conn.execute("""
                SELECT COALESCE(NULLIF(regime, ''), '?') AS reg, COUNT(*) AS n,
                       AVG(CASE WHEN our_direction = 'UP' THEN 1.0 ELSE 0.0 END),
                       AVG(CASE WHEN actual_direction = 'UP' THEN 1.0 ELSE 0.0 END),
                       AVG(CASE WHEN our_direction = actual_direction THEN 1.0 ELSE 0.0 END)
                FROM price_to_beat
                WHERE resolved AND horizon = ?
                  AND our_direction IN ('UP', 'DOWN')
                  AND actual_direction IN ('UP', 'DOWN')
                  AND COALESCE(source, 'pyth') = 'pyth'
                GROUP BY 1 HAVING COUNT(*) >= 50 ORDER BY 2 DESC
            """, (horizon,)).fetchall()
            if regimes:
                print("  by regime:")
                for reg, rn, up_l, up_m, win in regimes:
                    print(f"    {reg:<16} n={rn:>5}  UP-lean {up_l:>5.1%}  "
                          f"market UP {up_m:>5.1%}  win {win:.4f}")
                trend = {r[0]: r for r in regimes if r[0].startswith("TRENDING")}
                if "TRENDING_UP" in trend and "TRENDING_DOWN" in trend:
                    tu, td = trend["TRENDING_UP"][2], trend["TRENDING_DOWN"][2]
                    print(f"    {'trend response':<16} UP-lean is {tu:.1%} in an UPTREND and "
                          f"{td:.1%} in a DOWNTREND")
                    print(f"    {'':<16} a lean that TRACKED the trend would differ here; "
                          f"the gap is {abs(tu - td):.1%}")

            need = 0.50 + COST_CENTS / 100.0
            print(f"  {'break-even after costs':<26} {need:.4f} "
                  f"({COST_CENTS:.0f}c round trip on a ~50c binary)")
            print(f"  {'clears costs?':<26} "
                  f"{'YES' if overall['lo'] > need else 'NO - the lower bound is below it'}")

        print()
        print("=" * 78)
        print("Read the CI, not the point estimate. A win rate whose interval spans 0.50 is")
        print("a coin flip that happened to land somewhere, and one whose interval sits")
        print("BELOW 0.50 is a coin flip plus a cost.")
        print("=" * 78)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
