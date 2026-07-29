"""V14 - Lead-lag, split and costed.

The original reported -0.70% with no split. A single-venue archive cannot test CROSS-VENUE
lead-lag, so what is measured here is autocorrelation lead-lag within one series - stated
plainly rather than labelled cross-venue arbitrage.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import evaluate  # noqa: E402


def main():
    def signal(part):
        lead = part["ret_1"].shift(1)
        return np.where(lead.abs() > part["vol_30"] * 3, np.sign(lead), 0)

    evaluate("V14 - within-series lead-lag (NOT cross-venue: one venue archived)", signal,
             notes="cross-venue lead-lag needs a second venue's synchronised book")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
