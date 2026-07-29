"""V7 - Fisher-Rao crash geodesic. BLOCKED as framed.

Fisher information on a fitted state distribution is legitimate mathematics, but there is no
exact order-book density here, no uniquely defined crash state, and no evidence that a
Fisher-Rao distance leads volatility. As a DISTRIBUTION-SHIFT monitor it is buildable; as a
crash oracle it is not.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import blocked  # noqa: E402


def main():
    return blocked(
        "V7 - Fisher-Rao geodesic crash detector",
        "no order-book probability density exists, and the crash state is undefined",
        "reframe as a distribution-shift monitor whose only actions are reduce size, cancel quotes and enter CLOSE_ONLY")


if __name__ == "__main__":
    raise SystemExit(main())
