"""V25 - Cycle signal with correct accounting and a split.

WHAT THE ORIGINAL DID
    Reported "Cumulative Return: -212.50%", impossible for unleveraged capital. It added a
    FIXED notional to capital regardless of balance, so the account ran past zero.

WHAT THIS DOES
    Same cycle-fade idea, but stake is a fraction of current capital floored at zero, costs
    are applied, and the result is reported in and out of sample.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import evaluate  # noqa: E402



def main():
    def signal(part):
        detrended = part["close"] - part["close"].rolling(120).mean()
        phase = np.sign(detrended.fillna(0))
        extreme = (detrended.abs() > detrended.rolling(240).std() * 1.5).astype(int)
        return -phase * extreme

    evaluate("V25 - cycle fade, correct accounting (was: -212% impossible)", signal,
             notes="the original added a fixed notional to an already-negative balance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
