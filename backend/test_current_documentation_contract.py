"""Keep canonical current-state documentation synchronized with executable contracts."""
from __future__ import annotations

import re
from pathlib import Path

from features import FEATURE_NAMES
from model_contract import (
    MODEL_ARCH_VERSION,
    MODEL_FEATURE_NAMES,
    MODEL_FEATURE_SCHEMA_HASH,
    MODEL_NUM_FEATURES,
)


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "active" / "CURRENT_IMPLEMENTATION_TEST_AND_GAP_LEDGER_2026-07-31.md"


def main() -> int:
    text = LEDGER.read_text(encoding="utf-8")
    assert f"| raw feature count | {len(FEATURE_NAMES)} |" in text
    assert f"| main-model feature count | {MODEL_NUM_FEATURES} |" in text
    assert f"| main-model feature hash | `{MODEL_FEATURE_SCHEMA_HASH}` |" in text
    assert f"| main architecture | `{MODEL_ARCH_VERSION}` |" in text
    assert "| feature semantics | v4 |" in text
    assert "| training semantics | v3 |" in text
    assert all(name in text for name in FEATURE_NAMES)
    assert all(name in text for name in MODEL_FEATURE_NAMES)

    start_text = (ROOT / "start.bat").read_text(encoding="utf-8")
    match = re.search(r'BTC_HISTORICAL_DAYS=(\d+)', start_text)
    assert match and f"| configured historical window | {int(match.group(1)):,} days |" in text

    master = ROOT / "docs" / "active" / "MASTER_STATE_AND_ROADMAP_2026-07-28.md"
    models = ROOT / "docs" / "active" / "ALL_MODELS_PREDICTIONS_AND_FEATURES_2026-07-02.md"
    implementation = ROOT / "docs" / "IMPLEMENTATION_STATUS.md"
    assert "SUPERSEDED" in master.read_text(encoding="utf-8")[:1200]
    assert "SUPERSEDED" in models.read_text(encoding="utf-8")[:1200]
    assert "SUPERSEDED" in implementation.read_text(encoding="utf-8")[:1200]
    assert LEDGER.name in (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    print(
        "CURRENT DOCUMENTATION CONTRACT PASS "
        f"raw={len(FEATURE_NAMES)} model={MODEL_NUM_FEATURES} hash={MODEL_FEATURE_SCHEMA_HASH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
