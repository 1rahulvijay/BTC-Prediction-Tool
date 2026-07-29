"""V10 - Graph neural network over book topology. BLOCKED.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import blocked  # noqa: E402


def main():
    return blocked(
        "V10 - GNN over order-book graph",
        "no depth stream exists to build a book graph from",
        "sequenced L2 depth with per-level events")


if __name__ == "__main__":
    raise SystemExit(main())
