"""Reproduces the ceiling analysis quoted in docs/RESEARCH_RESULTS_MASTER.md.

WHY THIS FILE EXISTS
    The two tables in that document - gross edge before costs, and move size versus cost across
    horizons - are the most consequential output of the V1-V31 work, because together they say
    the 5m/15m taker lane cannot clear costs regardless of model. They were originally computed
    ad hoc and typed into the document.

    A number in a document with no script behind it is exactly the failure this suite was
    auditing other scripts for. This regenerates both tables from the real data so the claim
    can be checked rather than trusted.

    python research/ceiling_analysis.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import DEFAULT_FEE_BPS, DEFAULT_SPREAD_BPS, causal_frame, load_btc, split  # noqa: E402

TAKER_COST_BPS = 2.0 * DEFAULT_FEE_BPS + DEFAULT_SPREAD_BPS      # 9.0
# Executable proxy: one passive entry (2 bps) plus one taker exit
# (5 bps). It deliberately does not grant a second passive fill.
MAKER_TAKER_COST_BPS = 7.0

STRATEGIES = {
    "momentum": lambda p: np.where(
        (p["ret_5"] > 0) & (p["ret_15"] > 0), 1,
        np.where((p["ret_5"] < 0) & (p["ret_15"] < 0), -1, 0)),
    "mean-reversion z60": lambda p: np.where(
        p["z_60"].abs() > 2.0, -np.sign(p["z_60"]), 0),
    "volatility breakout": lambda p: np.where(
        (p["vol_z"] > 1.5) & (p["rng_z"] > 1.0), np.sign(p["ret_1"]), 0),
    "Kalman fade": lambda p: np.where(
        p["z_240"].abs() > 2.0, -np.sign(p["z_240"]), 0),
}


def gross_edge_table() -> None:
    """Is there ANY directional edge before costs are deducted?"""
    print("=" * 84)
    print("1. GROSS EDGE, OUT-OF-SAMPLE, BEFORE ANY COSTS")
    print("=" * 84)
    _, test = split(causal_frame())
    print(f"{'strategy':<22}{'trades':>9}{'gross bps/trade':>20}{'net after cost':>17}")
    print("-" * 84)
    any_gross = False
    for name, fn in STRATEGIES.items():
        direction = fn(test)
        active = direction != 0
        realised = test.loc[active, "fwd"].to_numpy() * direction[active]
        count = int(active.sum())
        gross = realised.mean() * 1e4
        stderr = realised.std(ddof=1) * 1e4 / np.sqrt(max(count, 1))
        any_gross = any_gross or (gross - 2 * stderr > 0)
        print(f"{name:<22}{count:>9}{gross:>13.2f} +-{stderr:>4.1f}"
              f"{gross - TAKER_COST_BPS:>17.2f}")
    print("-" * 84)
    print(f"  any strategy with gross edge > 2 standard errors above zero: {any_gross}")
    print("  If this is False, the constraint is NOT cost - there is no signal to amplify,")
    print("  and a better estimator of zero is still zero.")


def horizon_table() -> None:
    """How big is the typical move relative to the cost of capturing it?"""
    print("\n" + "=" * 84)
    print("2. MOVE SIZE VERSUS COST, BY HORIZON")
    print("=" * 84)
    close = load_btc(200_000)["close"]
    print(f"{'horizon':>9}{'median |move|':>16}{'taker x':>10}{'maker x':>10}  verdict (taker)")
    print("-" * 84)
    for horizon in (1, 5, 15, 30, 60, 240, 720, 1440):
        move = (close.shift(-horizon) / close - 1).abs().dropna().median() * 1e4
        taker = move / TAKER_COST_BPS
        maker = move / MAKER_TAKER_COST_BPS
        verdict = "yes" if taker > 3 else "marginal" if taker > 1.5 else "NO"
        print(f"{horizon:>7}m{move:>15.1f}{taker:>10.2f}{maker:>10.2f}  {verdict}")
    print("-" * 84)
    print(
        f"  taker cost {TAKER_COST_BPS:.1f} bps round trip | "
        f"passive-entry/taker-exit proxy {MAKER_TAKER_COST_BPS:.1f} bps"
    )
    print("  A ratio near 1.0 means the typical move is the SAME SIZE as the cost of")
    print("  capturing it, so breaking even requires the whole move, correctly signed,")
    print("  every time. That is arithmetic, not a modelling difficulty.")


def main() -> int:
    gross_edge_table()
    horizon_table()
    print("\n" + "=" * 84)
    print("CONCLUSION - the two levers with measured leverage")
    print("=" * 84)
    print("  1. PASSIVE ENTRY can reduce cost, but not by assuming both legs fill passively.")
    print("     Sequenced L2 now records replayable books; exact queue priority remains absent.")
    print("  2. LONGER HORIZONS  are testable on existing data right now.")
    print("\n  Reported in docs/RESEARCH_RESULTS_MASTER.md; this script regenerates both tables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
