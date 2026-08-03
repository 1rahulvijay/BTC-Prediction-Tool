"""PHASE5C_101 - are large BTC moves gradual volatility or discrete jumps?

WHY IT DECIDES SOMETHING
    A jump regime and a diffusion regime need different exits. A stop placed for diffusion is
    gapped through by a jump; a target sized for jumps never fills in diffusion. Before either
    is designed, it is worth knowing which one produces the moves.

    Barndorff-Nielsen and Shephard: realized variance measures total variation, while BIPOWER
    variation is robust to jumps. Their difference is the jump component.

        RV = sum r_t^2
        BV = (pi/2) * sum |r_t| * |r_{t-1}|
        jump share = max(RV - BV, 0) / RV

    360 days of 1-minute bars supports a hypothesis test here (MDE ~7.4 points), unlike the
    21-day Polymarket window.

    python research/phase5c/test_jump_vs_diffusion_decomposition.py
"""
from __future__ import annotations

RESEARCH_STATUS = "VALID_DIAGNOSTIC"
CAPITAL_AUTHORITY = False

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import BINANCE_DAYS, load_bars  # noqa: E402

WINDOW_M = 60          # bars per estimation window
BIPOWER_SCALE = np.pi / 2.0


def decompose(returns: np.ndarray) -> dict:
    """Split total variation into a continuous part and a jump part."""
    returns = np.asarray(returns, float)
    if len(returns) < 3:
        return {}
    realized = float(np.sum(returns ** 2))
    bipower = float(BIPOWER_SCALE * np.sum(np.abs(returns[1:]) * np.abs(returns[:-1])))
    jump = max(realized - bipower, 0.0)
    return {"realized": realized, "bipower": bipower, "jump": jump,
            "jump_share": float(jump / realized) if realized > 0 else 0.0}


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    rng = np.random.default_rng(9)
    diffusion = rng.normal(0, 0.001, 500)
    smooth = decompose(diffusion)
    check(smooth["jump_share"] < 0.25,
          "pure diffusion carries a small jump share - bipower tracks realized variance")

    jumpy = diffusion.copy()
    jumpy[250] = 0.05                       # one 5% bar dwarfing everything around it
    spiked = decompose(jumpy)
    check(spiked["jump_share"] > 0.7,
          "a single large bar makes the jump component dominate (measured 0.778 - bipower "
          "still absorbs a little of it, which is why the threshold is not 0.9)")
    check(spiked["realized"] > smooth["realized"],
          "the jump raises total realized variance")
    check(abs(spiked["bipower"] - smooth["bipower"]) / smooth["bipower"] < 2.0,
          "bipower is ROBUST to the jump - that robustness is what makes the split work")
    check(decompose(np.array([0.0, 0.0, 0.0]))["jump_share"] == 0.0,
          "a flat window has zero jump share rather than a divide-by-zero")
    check(decompose(np.array([1.0])) == {},
          "a window too short to estimate returns nothing rather than a fabricated number")

    print(f"\nJUMP DECOMPOSITION SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    print("=" * 96)
    print("PHASE5C-101  JUMP VERSUS DIFFUSION - what produces the large moves?")
    print("=" * 96)
    frame = load_bars()
    close = frame["close"].to_numpy(float)
    returns = np.diff(np.log(close))
    total = len(returns) // WINDOW_M
    print(f"  {len(frame):,} bars over ~{BINANCE_DAYS} days | {total:,} disjoint "
          f"{WINDOW_M}-minute windows")

    shares, moves = [], []
    for index in range(total):
        chunk = returns[index * WINDOW_M:(index + 1) * WINDOW_M]
        part = decompose(chunk)
        if not part:
            continue
        shares.append(part["jump_share"])
        moves.append(abs(chunk.sum()) * 10_000.0)
    shares = np.asarray(shares)
    moves = np.asarray(moves)

    print()
    print(f"  jump share of total variation: median {np.median(shares):.3f}, "
          f"mean {shares.mean():.3f}, p90 {np.quantile(shares, 0.90):.3f}")
    print(f"  windows where jumps carry >50% of variation: {(shares > 0.5).mean():.1%}")
    print()
    print(f"{'|move| quintile':<18}{'n':>8}{'median move bps':>18}{'median jump share':>20}")
    edges = np.quantile(moves, np.linspace(0, 1, 6))
    for index in range(5):
        mask = (moves >= edges[index]) & (moves <= edges[index + 1])
        if mask.sum() < 50:
            continue
        print(f"{f'Q{index + 1}':<18}{int(mask.sum()):>8,}"
              f"{np.median(moves[mask]):>18.1f}{np.median(shares[mask]):>20.3f}")

    biggest = shares[moves >= np.quantile(moves, 0.95)]
    print()
    print(f"  In the largest 5% of hourly moves the median jump share is "
          f"{np.median(biggest):.3f}.")
    if np.median(biggest) > 0.5:
        print("  Large moves are predominantly JUMPS. A stop sized for diffusion will be gapped")
        print("  through, so any exit rule in this lane must price gap risk rather than assume")
        print("  a continuous path.")
    else:
        print("  Large moves are predominantly CONTINUOUS. Stops are meaningful here and a")
        print("  diffusion-style exit is not structurally wrong.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
