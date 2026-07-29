"""V29 - Hawkes-style self-exciting intensity, measured economically.

The original reported an in-sample win rate with no split. Event intensity is estimated with a
causal exponentially-decayed count of large moves - the defining feature of a self-exciting
process - and the trade fades clustered bursts.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import evaluate  # noqa: E402


def main():
    def signal(part):
        events = (part["ret_1"].abs() > part["vol_30"] * 2.0).astype(float)
        intensity = events.ewm(halflife=30).mean()
        hot = (intensity > intensity.rolling(240).quantile(0.9)).astype(int)
        return -np.sign(part["ret_5"]) * hot

    evaluate("V29 - Hawkes self-excitation, split + costed", signal,
             notes="intensity is a causal decayed event count; no future information")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
