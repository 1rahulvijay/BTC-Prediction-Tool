"""A short frozen warm-up must not weaken or expand the artifact training identity."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    backend = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as raw:
        env = os.environ.copy()
        env.update({
            "BTC_DATA_DIR": raw,
            "BTC_DB_PATH": str(Path(raw) / "analytics.duckdb"),
            "BTC_MODEL_TRAINING_DAYS": "1000",
            "BTC_HISTORICAL_DAYS": "3",
            "BTC_SERVING_WARMUP_DAYS": "3",
        })
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json, server; print(json.dumps({"
                "'training': server.HISTORICAL_DAYS, "
                "'warmup': server.SERVING_WARMUP_DAYS, "
                "'max_klines': server.MAX_KLINES}))",
            ],
            cwd=backend,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        assert payload["training"] == 1000, payload
        assert payload["warmup"] == 3, payload
        assert payload["max_klines"] == 3 * 24 * 60 + 1500, payload
    print("serving-warmup-namespace: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
