"""V4 - Breeden-Litzenberger risk-neutral density. BLOCKED, not simulated.

The original computed a density from a SIMULATED Black-Scholes chain and reported
"Calculated True Market-Implied Probability: 11.81%". A density derived from invented option
prices measures the invention, not the market.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import blocked  # noqa: E402


def main():
    return blocked(
        "V4 - Breeden-Litzenberger risk-neutral density",
        "no Deribit per-strike option chain is stored anywhere in this repository",
        "persist strike, bid, ask, mark_iv and expiry from the Deribit chain; note also that Deribit's shortest BTC expiry is DAILY while this lane trades 5m/15m contracts")


if __name__ == "__main__":
    raise SystemExit(main())
