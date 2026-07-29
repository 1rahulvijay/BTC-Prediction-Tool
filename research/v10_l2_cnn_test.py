"""V10 - CNN over L2 book images. BLOCKED.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import blocked  # noqa: E402


def main():
    return blocked(
        "V10 - CNN over L2 depth images",
        "no L2 depth sequences are archived; the recorder stores top-of-book only",
        "sequenced @depth@100ms archive with gap detection")


if __name__ == "__main__":
    raise SystemExit(main())
