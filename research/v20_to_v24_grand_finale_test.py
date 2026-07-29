"""V20-V24 - The combined strategies, each split and costed.

The original reported in-sample figures including "Win Rate: 2.04% | Cumulative Profit:
-10032.30%" - the fixed-notional artifact. Each idea is now a causal signal measured the same
way, so they are comparable to one another and to the baseline.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import evaluate  # noqa: E402


def main():
    strategies = {
        "V20 momentum continuation": lambda p: np.where(
            (p["ret_5"] > 0) & (p["ret_15"] > 0), 1,
            np.where((p["ret_5"] < 0) & (p["ret_15"] < 0), -1, 0)),
        "V21 mean reversion z60": lambda p: np.where(
            p["z_60"].abs() > 2.0, -np.sign(p["z_60"]), 0),
        "V22 volatility breakout": lambda p: np.where(
            (p["vol_z"] > 1.5) & (p["rng_z"] > 1.0), np.sign(p["ret_1"]), 0),
        "V23 volume-confirmed move": lambda p: np.where(
            (p.get("vol_ratio", 1) > 2.0) & (p["ret_5"].abs() > 0), np.sign(p["ret_5"]), 0),
        "V24 compression release": lambda p: np.where(
            (p["rng_z"] < -1.0) & (p["ret_1"].abs() > 0), np.sign(p["ret_1"]), 0),
    }
    for name, fn in strategies.items():
        evaluate(name + " (split + costed)", fn)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
