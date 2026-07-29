"""V13 - Live-API strategy, split and costed.

The original already reported a LOSS (-10.13%, Sharpe -37.96) which was honest in direction,
but it had no split. Re-measured with train/test separation so the number is comparable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import evaluate  # noqa: E402


def main():
    def signal(part):
        return np.where(part["ret_15"] > part["vol_30"] * 2, -1,
                        np.where(part["ret_15"] < -part["vol_30"] * 2, 1, 0))

    evaluate("V13 - live-API style mean reversion (was: -10.13%, no split)", signal,
             notes="direction of the original finding is preserved; now split and costed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
