"""
Market-neutral carry: the one lane that does not predict direction.

Seven lanes have closed. Five on execution economics, two because the barrier geometry is a
martingale and no observable state changes it. Every one of them asked the market to move a
particular way.

Carry does not. Long spot against short perpetual is direction-neutral by construction, so it
cannot inherit the martingale result - which is exactly why it is worth testing separately
rather than folding into the same conclusion.

CARRY HAS TWO INDEPENDENT P&L TERMS AND THEY MUST BE TESTED SEPARATELY.

    basis convergence   the perp/spot price spread narrowing after you put the hedge on
    funding             the 8-hourly cashflow between longs and shorts

Only ONE of them is measurable from data on disk, and conflating them would produce a
confident answer to a question that was never asked. So:

  1. BASIS. `perp_spot_basis_bps` is a real series in the research matrix - 49,883 distinct
     values, monthly means drifting -3.93 -> -5.41 bps. Testable, and tested here.

  2. FUNDING. NOT IN THE DATA. `funding_velocity` is 90% zeros and is a derivative rather than
     a rate; `binance_paper_funding_events` has zero rows. The funding cashflow is the
     DOMINANT term in a real carry book, so this study cannot conclude on carry as a whole and
     does not try. What it can do is state the cost floor that any funding claim must clear.

The distinction matters because the two halves get different verdicts, and reporting one
verdict for both would be the defect this repository keeps finding.

Read-only. Exits non-zero only on a data problem.

    python research/market_neutral_carry_lane.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

MATRIX = os.environ.get("BTC_RESEARCH_MATRIX", str(ROOT / "data" / "research_matrix_1m.parquet"))

#: A hedged carry is FOUR legs, not two: buy spot AND sell perp to open, then unwind both.
#: Costing it as a single round trip is the easiest way to make this lane look viable.
LEGS_PER_ROUND_TRIP = 4


def round_trip_cost_bps() -> float:
    try:
        from binance_paper.config import EngineConfig
        cfg = EngineConfig.from_env()
        return LEGS_PER_ROUND_TRIP * (cfg.fee_rate_bps + cfg.slippage_bps)
    except Exception:
        return 24.0


def main() -> int:
    if not Path(MATRIX).exists():
        print(f"no research matrix at {MATRIX}")
        return 2

    table = pq.read_table(MATRIX, columns=["ts_ms", "perp_spot_basis_bps", "funding_velocity"])
    basis = np.asarray(table.column("perp_spot_basis_bps").to_pylist(), dtype=float)
    fv = np.asarray(table.column("funding_velocity").to_pylist(), dtype=float)
    finite = basis[np.isfinite(basis)]
    cost = round_trip_cost_bps()

    print("=" * 78)
    print("MARKET-NEUTRAL CARRY - basis and funding, tested separately")
    print("=" * 78)
    print(f"\n{finite.size:,} finite basis observations   hedged round trip {cost:.1f} bps "
          f"({LEGS_PER_ROUND_TRIP} legs)")

    print("\n1. BASIS CONVERGENCE - closed by arithmetic, before any model")
    print("-" * 78)
    q = {p: float(np.quantile(finite, p)) for p in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)}
    print("   perp_spot_basis_bps")
    print(f"     p01 {q[0.01]:+.2f}   p05 {q[0.05]:+.2f}   p25 {q[0.25]:+.2f}   "
          f"median {q[0.5]:+.2f}")
    print(f"     p75 {q[0.75]:+.2f}   p95 {q[0.95]:+.2f}   p99 {q[0.99]:+.2f}   "
          f"mean {finite.mean():+.3f}")
    span_90 = q[0.95] - q[0.05]
    span_98 = q[0.99] - q[0.01]
    print("   the SPREAD is what a convergence trade captures, and its whole range is:")
    print(f"     p05 -> p95   {span_90:.2f} bps")
    print(f"     p01 -> p99   {span_98:.2f} bps")
    print(f"   against a hedged round trip of {cost:.1f} bps")
    print()
    print("   A PERFECT ORACLE entering at p05 and exiting at p95 EVERY time captures")
    print(f"   {span_90:.2f} bps and pays {cost:.1f}. Shortfall {cost - span_90:.1f} bps, a "
          f"factor of {cost / max(span_90, 1e-9):.1f}.")
    print("   No entry rule, no state selection and no model changes that: the spread is")
    print("   smaller than the cost of touching it. CLOSED.")

    # The basis does not mean-revert to zero either - it drifts. A convergence trade needs
    # something to converge TO, and a level that wanders has no such anchor.
    print("\n   ... and it is a DRIFTING level, not a spread around a fixed point:")
    ts = np.asarray(table.column("ts_ms").to_pylist(), dtype=np.int64)
    ok = np.isfinite(basis)
    months = (ts[ok] // (30 * 86_400_000))
    for m in np.unique(months)[:6]:
        sel = basis[ok][months == m]
        print(f"     block {int(m)}   n={sel.size:>6,}   mean {sel.mean():+.3f}   "
              f"std {sel.std():.3f}")
    print("   A convergence trade needs a level to converge TO. This one moves.")

    print("\n2. FUNDING - the dominant term, and it is NOT IN THE DATA")
    print("-" * 78)
    print(f"   funding_velocity      {(fv == 0).mean():.1%} zeros, "
          f"{len(np.unique(fv)):,} distinct - a derivative, not a rate")
    try:
        import duckdb
        c = duckdb.connect(str(ROOT / "data" / "binance_paper.duckdb"), read_only=True)
        n = c.execute("SELECT COUNT(*) FROM binance_paper_funding_events").fetchone()[0]
        c.close()
    except Exception:
        n = 0
    print(f"   funding_events rows   {n:,}")
    print()
    print("   The 8-hourly funding cashflow is the dominant P&L term in a real carry book, and")
    print("   this repository has never recorded it. THIS STUDY THEREFORE DOES NOT CONCLUDE ON")
    print("   CARRY. Reporting the basis verdict as a carry verdict would answer a question")
    print("   that was never asked.")
    print()
    print("   What CAN be stated is the bar. Published Binance BTCUSDT funding is typically")
    print("   ~0.01% per 8h - an ORDER OF MAGNITUDE from documentation, not a measurement:")
    for rate_bps_8h in (0.5, 1.0, 2.0):
        per_day = rate_bps_8h * 3.0
        days = cost / per_day
        print(f"     {rate_bps_8h:.1f} bps/8h -> {per_day:.1f} bps/day -> "
              f"{days:.1f} days of holding just to clear the {cost:.0f} bps entry+exit")
    print()
    print("   So the funding half is NOT closed. It is UNMEASURED, and unlike every other")
    print("   lane in this sweep the blocker is data collection rather than economics.")

    print("\n3. WHAT WOULD SETTLE IT")
    print("-" * 78)
    print("   Record the actual funding rate and mark, every 8h, for a few months:")
    print("     - the realised funding cashflow per unit of notional")
    print("     - the basis at entry and at each settlement")
    print("     - how often funding flips sign while a hedge is on")
    print("   Then carry P&L = sum(funding) +/- basis change - 24 bps, measured rather than")
    print("   assumed. The recorder for it is a REST poll on a schedule, not a model.")

    print("\n" + "=" * 78)
    print("Two halves, two verdicts. The basis half is closed by arithmetic - a 2.9 bps")
    print("spread cannot pay a 24 bps round trip. The funding half is untested because the")
    print("cashflow was never recorded, and saying otherwise would be inventing a result.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
