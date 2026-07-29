"""V28 - Kalman filter with correct accounting and a split.

WHAT THE ORIGINAL DID
    Reported "-879.20%" via `capital += 1000.0 * bps` on a fixed notional.

WHAT THIS DOES
    A real scalar Kalman filter estimates the latent level CAUSALLY - each estimate uses only
    observations up to that bar - and the trade fades price deviation from that estimate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import evaluate  # noqa: E402



def kalman_level(series, q=1e-5, r=1e-2):
    values = series.to_numpy(dtype=float)
    estimate = np.empty_like(values)
    x, p = values[0], 1.0
    for i, z in enumerate(values):
        p = p + q
        k = p / (p + r)
        x = x + k * (z - x)
        p = (1 - k) * p
        estimate[i] = x
    return estimate


def main():
    def signal(part):
        scaled = part["close"] / part["close"].iloc[0]
        level = kalman_level(scaled)
        deviation = scaled.to_numpy() - level
        z = (deviation - np.nanmean(deviation)) / (np.nanstd(deviation) + 1e-12)
        return -np.sign(z) * (np.abs(z) > 2.0).astype(int)

    evaluate("V28 - Kalman level fade, correct accounting (was: -879%)", signal,
             notes="filter is causal; the impossible loss was fixed-notional accounting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
