"""V12 - Execution backtest, split and costed.

The original reported 51.54% win rate / +42.93% / Sharpe 1.40 with NO out-of-sample split, so
that figure describes the data the rule was chosen on. Same idea, honestly measured.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import evaluate  # noqa: E402


def main():
    def signal(part):
        return np.where(part["z_60"] < -1.5, 1, np.where(part["z_60"] > 1.5, -1, 0))

    evaluate("V12 - execution strategy (was: +42.93% in-sample only)", signal,
             notes="the original 1.40 Sharpe had no out-of-sample separation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
