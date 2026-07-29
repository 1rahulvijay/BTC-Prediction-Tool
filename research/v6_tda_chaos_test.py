"""V6 - Topological data analysis for iceberg detection. BLOCKED.

An order book at one instant is a monotone price axis with a size per level: it has no
non-trivial loops, so Betti-1 "liquidity holes" do not exist in that object. Any signal would
come entirely from an unspecified embedding choice. It is also data-blocked.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import blocked  # noqa: E402


def main():
    return blocked(
        "V6 - persistent homology on the order book",
        "no depth stream exists (bookTicker and orderbook.1 are top-of-book only)",
        "sequenced @depth@100ms diffs with (U, u) continuity plus REST snapshots")


if __name__ == "__main__":
    raise SystemExit(main())
