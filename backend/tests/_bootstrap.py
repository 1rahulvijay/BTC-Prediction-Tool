"""Preserve legacy direct-script imports after tests moved out of backend/."""
from __future__ import annotations

import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

for path in (BACKEND_DIR, REPO_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)
