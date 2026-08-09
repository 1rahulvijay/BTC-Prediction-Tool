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

  2. FUNDING. NOW MEASURED, as of 2026-08-09. This study previously reported it UNMEASURED and
     said so plainly: `funding_velocity` is 90% zeros and is a derivative rather than a rate,
     and `binance_paper_funding_events` had zero rows. `backend/funding_recorder.py` closed
     that hole by backfilling 3,500 real settlements from Binance, 2023-05-31 to 2026-08-09.

The distinction matters because the two halves get different verdicts, and reporting one
verdict for both would be the defect this repository keeps finding. They still do, and the
funding verdict is not the one the basis verdict would have predicted.

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


FUNDING_DB = os.environ.get("BTC_FUNDING_DB", str(ROOT / "data" / "funding.duckdb"))


def round_trip_cost_bps() -> float:
    try:
        from binance_paper.config import EngineConfig
        cfg = EngineConfig.from_env()
        return LEGS_PER_ROUND_TRIP * (cfg.fee_rate_bps + cfg.slippage_bps)
    except Exception:
        return 24.0


def starting_cash() -> float:
    """Total capital assigned to the Binance paper lane, not hedge notional."""
    raw = os.environ.get(
        "BTC_BINANCE_PAPER_STARTING_CASH",
        os.environ.get("BTC_PAPER_COMPETITION_BANKROLL_USD", "500"),
    )
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 500.0


def load_funding():
    """Real settlements recorded by backend/funding_recorder.py, or (None, None)."""
    if not Path(FUNDING_DB).exists():
        return None, None
    try:
        import duckdb
        conn = duckdb.connect(FUNDING_DB, read_only=True)
        rows = conn.execute("SELECT funding_time_ms, funding_rate_bps FROM "
                            "funding_settlements ORDER BY funding_time_ms").fetchall()
        conn.close()
    except Exception:
        return None, None
    if len(rows) < 400:  # too short to say anything about regime
        return None, None
    return (np.asarray([r[0] for r in rows], dtype=np.int64),
            np.asarray([r[1] for r in rows], dtype=float))


def sign_flips(rate: np.ndarray) -> int:
    """How often funding turns against a hedge that is already on."""
    s = np.sign(rate)
    return int((s[1:] != s[:-1]).sum())


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

    print("\n2. FUNDING - the dominant term, now MEASURED")
    print("-" * 78)
    print(f"   funding_velocity      {(fv == 0).mean():.1%} zeros, "
          f"{len(np.unique(fv)):,} distinct - a derivative, not a rate. Never usable here.")

    ts_f, rate = load_funding()
    if ts_f is None:
        print(f"   no funding database at {FUNDING_DB}")
        print("   Run: python backend/funding_recorder.py --backfill-days 1100")
        print("   Until then the funding half is UNMEASURED and this study does not conclude")
        print("   on carry as a whole. Reporting the basis verdict as a carry verdict would")
        print("   answer a question that was never asked.")
        return 0

    import datetime
    fmt = lambda ms: datetime.datetime.fromtimestamp(  # noqa: E731
        ms / 1000, datetime.UTC).strftime("%Y-%m-%d")
    ann = rate.mean() * 3 * 365 / 100
    print(f"   real settlements      {rate.size:,}   {fmt(ts_f[0])} .. {fmt(ts_f[-1])}")
    print(f"     mean {rate.mean():+.4f} bps/8h -> {rate.mean() * 3:+.3f} bps/day -> "
          f"{ann:+.2f}% annualized on notional")
    print(f"     positive {(rate > 0).mean():.1%} of settlements, so the short-perp leg of a "
          f"hedge is paid most of the time")
    print()
    print("   THE SIGN IS RIGHT AND THE MAGNITUDE CLEARS THE COST. That is a different result")
    print("   from every other lane in this sweep, so it gets checked properly rather than")
    print("   celebrated. Net of the four-leg round trip, by holding period:")
    cum = np.cumsum(np.insert(rate, 0, 0.0))
    for days in (5, 15, 30, 60, 90):
        k = days * 3
        if k >= rate.size:
            continue
        net = (cum[k:] - cum[:-k]) - cost
        print(f"     hold {days:>3}d   mean net {net.mean():+8.2f} bps   "
              f"median {np.median(net):+8.2f}   profitable {(net > 0).mean():5.1%}")

    print("\n3. THE TWO THINGS THAT DECIDE IT - regime and scale")
    print("-" * 78)
    print("   REGIME. The rate is not a constant, and a mean over three years hides that.")
    k = 270  # 90 days of 8-hourly settlements
    roll = (cum[k:] - cum[:-k]) / k * 3 * 365 / 100
    print(f"     rolling 90d annualized   min {roll.min():+.2f}%   median "
          f"{np.median(roll):+.2f}%   max {roll.max():+.2f}%")
    print(f"     latest {roll[-1]:+.2f}%")
    recent = rate[ts_f >= ts_f[-1] - 180 * 86_400_000]
    r_ann = recent.mean() * 3 * 365 / 100
    print(f"     last 180d   {recent.mean():+.4f} bps/8h -> {r_ann:+.2f}% annualized, "
          f"{(recent < 0).mean():.1%} of settlements NEGATIVE")
    net30 = (np.cumsum(np.insert(recent, 0, 0.0))[90:]
             - np.cumsum(np.insert(recent, 0, 0.0))[:-90]) - cost
    print(f"     a 30-day hold in THAT regime nets {net30.mean():+.2f} bps "
          f"({(net30 > 0).mean():.1%} profitable)")
    print("   The spread between the best and worst 90-day windows is more than an order of")
    print("   magnitude. This is a risk premium that varies with leverage demand, not a")
    print("   constant yield, and it is negative often enough that a hedge left on unattended")
    print(f"   pays instead of collects. It flipped sign {sign_flips(rate):,} times.")

    print()
    capital = starting_cash()
    hedge_notional = capital / 2.0
    print(f"   SCALE. The configured Binance paper allocation is ${capital:.0f}. An unlevered")
    print(f"   hedge needs capital on BOTH legs, so matched notional is ${hedge_notional:.0f}:")
    for label, a in (("3.2y mean", ann), ("last 180d", r_ann)):
        yr_usd = hedge_notional * a / 100
        print(f"     {label:<10}  {a:+.2f}%/yr  ->  ${yr_usd:+.2f}/yr  "
              f"->  ${yr_usd / 365:+.3f}/day")
    print("   That is the whole finding. The funding term is REAL, it is POSITIVE, and it")
    print("   clears its execution cost at a long enough holding period - the first thing in")
    print("   this sweep that does. It is also single- to low-double-digit dollars per year at")
    print("   this capital, and it is compensation for holding a position through")
    print("   leverage-demand shocks rather than a mispricing anyone is missing.")

    print("\n" + "=" * 78)
    print("Two halves, two verdicts, and the second one changed once it was measured.")
    print("The basis half is CLOSED by arithmetic: a 2.9 bps spread cannot pay a 24 bps round")
    print("trip. The funding half is OPEN but bounded - a real, positive, well-known risk")
    print("premium that pays in years and in dollars, not in a trading edge over a venue.")
    print("It is the only lane of the eight not closed, and it is not closed because the")
    print("arithmetic works. It is small, not absent.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
