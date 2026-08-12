"""Validate the retrain completion marker without importing ML/GPU runtimes."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_contract import (  # noqa: E402
    MODEL_ARCH_VERSION,
    MODEL_FEATURE_SCHEMA_HASH,
)


MARKER_SCHEMA_VERSION = 2
VALID_DEPLOYMENT_STATES = {"active", "shadow", "gate_rejected"}


def validate(path: Path, expected_days: int) -> list[str]:
    issues: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"marker is unreadable: {type(exc).__name__}: {exc}"]
    if not isinstance(payload, dict):
        return ["marker root is not an object"]

    expected = {
        "schema_version": MARKER_SCHEMA_VERSION,
        "historical_days": int(expected_days),
        "model_arch": MODEL_ARCH_VERSION,
        "feature_schema_hash": MODEL_FEATURE_SCHEMA_HASH,
        "heads_complete": True,
        "model_trained": True,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            issues.append(f"{key}={payload.get(key)!r}, expected {value!r}")

    bundle_id = str(payload.get("model_bundle_id") or "").strip()
    if not bundle_id:
        issues.append("model_bundle_id is missing")
    state = str(payload.get("deployment_state") or "")
    if state not in VALID_DEPLOYMENT_STATES:
        issues.append(f"deployment_state={state!r} is not recognized")

    model_dir = Path(str(payload.get("model_dir") or ""))
    if not model_dir.is_dir():
        issues.append(f"model_dir is absent: {model_dir}")
        return issues
    manifest_path = model_dir / "artifact_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        issues.append(f"bundle manifest is unreadable: {type(exc).__name__}: {exc}")
        return issues
    if manifest.get("model_arch_version") != MODEL_ARCH_VERSION:
        issues.append("bundle manifest model_arch_version differs")
    if str(manifest.get("model_bundle_id") or "") != bundle_id:
        issues.append("bundle manifest model_bundle_id differs")
    if str(manifest.get("model_feature_schema_hash") or "") != MODEL_FEATURE_SCHEMA_HASH:
        issues.append("bundle manifest model_feature_schema_hash differs")
    if int(manifest.get("requested_days") or 0) != int(expected_days):
        issues.append("bundle manifest requested_days differs")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--marker", required=True)
    parser.add_argument("--days", required=True, type=int)
    args = parser.parse_args()
    marker = Path(os.path.abspath(args.marker))
    issues = validate(marker, args.days)
    if issues:
        print(f"[marker] INVALID {marker}")
        for issue in issues:
            print(f"[marker]   {issue}")
        return 1
    print(f"[marker] VALID {marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
