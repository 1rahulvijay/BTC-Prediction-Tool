"""A filename alone may never freeze a stale or partial retrain."""
from __future__ import annotations

import json
import runpy as _bootstrap_runpy
import tempfile
from pathlib import Path

_bootstrap_runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from model_contract import MODEL_ARCH_VERSION, MODEL_FEATURE_SCHEMA_HASH  # noqa: E402
from validate_retrain_marker import MARKER_SCHEMA_VERSION, validate  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        model_dir = root / "bundle"
        model_dir.mkdir()
        bundle_id = "marker-contract-test"
        manifest = {
            "model_arch_version": MODEL_ARCH_VERSION,
            "model_feature_schema_hash": MODEL_FEATURE_SCHEMA_HASH,
            "model_bundle_id": bundle_id,
            "requested_days": 1000,
        }
        (model_dir / "artifact_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        marker = root / "complete.json"
        payload = {
            "schema_version": MARKER_SCHEMA_VERSION,
            "historical_days": 1000,
            "model_arch": MODEL_ARCH_VERSION,
            "feature_schema_hash": MODEL_FEATURE_SCHEMA_HASH,
            "model_bundle_id": bundle_id,
            "model_dir": str(model_dir),
            "deployment_state": "shadow",
            "heads_complete": True,
            "model_trained": True,
        }
        marker.write_text(json.dumps(payload), encoding="utf-8")
        assert validate(marker, 1000) == []

        payload["model_arch"] = "stale"
        marker.write_text(json.dumps(payload), encoding="utf-8")
        assert any("model_arch" in issue for issue in validate(marker, 1000))

        payload["model_arch"] = MODEL_ARCH_VERSION
        payload["heads_complete"] = False
        marker.write_text(json.dumps(payload), encoding="utf-8")
        assert any("heads_complete" in issue for issue in validate(marker, 1000))

    print("retrain-marker-contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
