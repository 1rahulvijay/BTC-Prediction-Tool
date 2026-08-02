from __future__ import annotations
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.phase5_standalone.common.runner import standalone_entry

if __name__ == "__main__":
    raise SystemExit(standalone_entry(str(Path(__file__).with_name("run.py")), ["--selftest"]))
