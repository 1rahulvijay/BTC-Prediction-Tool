"""V31 - Regime TRANSITION signal, measured economically.

The original reported a 0.00% win rate in-sample with no split. Transitions are detected
causally as a volatility-state change, and the trade takes the direction of the breakout.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import evaluate  # noqa: E402


def main():
    def signal(part):
        prior = part["vol_z"].shift(5)
        transition = (prior < 0.0) & (part["vol_z"] > 1.0)
        return np.where(transition, np.sign(part["ret_5"]), 0)

    evaluate("V31 - volatility regime transition, split + costed", signal,
             notes="transition detected from a lagged state change, not from the future")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
