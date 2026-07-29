"""V27 - Event-driven (spiking) signal with correct accounting and a split.

WHAT THE ORIGINAL DID
    Reported "-834.10%" - the fixed-notional accounting artifact again.

WHAT THIS DOES
    Keeps the event-driven idea: membrane potential accumulates normalised drive, a spike
    fires past a threshold, and the trade fades the over-extension. Causal, costed, split.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import evaluate  # noqa: E402



def main():
    def signal(part):
        drive = part["ret_1"] / part["vol_30"].replace(0, np.nan)
        membrane = drive.fillna(0).rolling(20).sum()
        fires = (membrane.abs() > 3.0).astype(int)
        return -np.sign(membrane) * fires

    evaluate("V27 - spiking event model, correct accounting (was: -834%)", signal,
             notes="event framing retained; the impossible loss was an accounting bug")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
