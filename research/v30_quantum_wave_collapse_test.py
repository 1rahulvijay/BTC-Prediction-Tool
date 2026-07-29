"""V30 - "Wave collapse" superposition signal, measured economically.

The framing is metaphorical: there is no quantum mechanics in a price series. What it reduces
to is a probability mixture over up/down states collapsing when one dominates. Tested honestly:
build the mixture from causal momentum, trade when it concentrates.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import evaluate  # noqa: E402


def main():
    def signal(part):
        # Mixture weight from causal momentum agreement across horizons.
        up = ((part["ret_5"] > 0).astype(float)
              + (part["ret_15"] > 0).astype(float)
              + (part["ret_60"] > 0).astype(float)) / 3.0
        collapsed = (up > 0.99) | (up < 0.01)
        return np.where(collapsed, np.sign(up - 0.5), 0)

    evaluate("V30 - state collapse on momentum agreement, split + costed", signal,
             notes="no quantum mechanics is involved; this is a probability mixture")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
