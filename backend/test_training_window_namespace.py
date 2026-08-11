"""Artifact versions must describe the fit window, not the runtime warm-up window."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def main() -> int:
    env = os.environ.copy()
    env.update({
        "BTC_MODEL_TRAINING_DAYS": "1000",
        "BTC_HISTORICAL_DAYS": "3",
        "BTC_BACKFILL_DAYS": "3",
    })
    code = (
        "import json, sys, keeper_head_training as k, train_signed_quantiles as s, "
        "train_round_state_heads as r, train_heads as h; "
        "sys.path.insert(0, 'decision'); import train_selectivity_models as x; "
        "print(json.dumps({'keeper': k.TRAIN_DAYS_TAG, "
        "'signed': s.TRAIN_DAYS_TAG, 'round': r.TRAIN_DAYS_TAG, "
        "'selectivity': x.TRAIN_DAYS_TAG, 'orchestrator': h.DAYS, "
        "'signed_version': s.HEAD_VERSION}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, env=env,
        capture_output=True, text=True, check=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["keeper"] == "1000d", payload
    assert payload["signed"] == "1000", payload
    assert payload["round"] == "1000", payload
    assert payload["selectivity"] == "1000", payload
    assert payload["orchestrator"] == "1000", payload
    assert "1000d" in payload["signed_version"], payload
    print("training-window-namespace: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
