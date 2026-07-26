"""Fail-closed execution/capacity prediction serving."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from .model_common import artifact_issues, predict_classifier, predict_member_mean
from .trade_schema import CONFIG_VERSION, FEATURE_COLUMNS, MODE
from .champion_resolver import resolve_artifact
from .freeze_guard import ArtifactPin


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
ARTIFACT_NAME = "complete_trade_execution_heads.pkl"
LEGACY_PATH = DATA / "saved_models" / ARTIFACT_NAME
# Resolved through the champion bundle on every load. A promotion that swaps
# champion.json must actually reach serving, or the atomic pointer is decorative.
MODEL_PATH = LEGACY_PATH          # rebound by _resolve() below

QUANTILES = (0.50, 0.80, 0.95)

_BUNDLE = None
_MANIFEST: dict[str, Any] = {}
_RESOLUTION: dict = {}
_TOKEN = None
_PIN = ArtifactPin("execution")
_ERROR = ""
_MTIME = -1.0
_CHECKED = 0.0


def load_model(force: bool = False):
    global _BUNDLE, _MANIFEST, _ERROR, _MTIME, _CHECKED
    global MODEL_PATH
    now = time.time()
    if not force and _CHECKED and now - _CHECKED < 30.0:
        return _BUNDLE
    global _RESOLUTION, _TOKEN
    resolved, _resolution = resolve_artifact(ARTIFACT_NAME, LEGACY_PATH)
    _RESOLUTION = _resolution
    # CACHE ON IDENTITY, NOT TIME. mtime is not model identity: two bundles can carry the same
    # mtime, so a champion swap could leave the OLD bundle loaded while MODEL_PATH claimed the
    # new one. The token also forces a reload after an evidence-mode refusal, which previously
    # left _MTIME stale and could pin a None result.
    token = (
        _resolution.get("bundle_hash"),
        _resolution.get("source"),
        str(resolved) if resolved else None,
    )
    if token != _TOKEN:
        _TOKEN = token
        _MTIME = -1.0
        force = True
    if resolved is None:
        # Evidence mode with no verified bundle: serve NO model rather than unverified bytes.
        _BUNDLE, _MANIFEST, _ERROR = None, {}, str(_resolution.get("note") or "no verified bundle")
        _CHECKED = now
        return None
    MODEL_PATH = resolved
    _CHECKED = now
    try:
        mtime = MODEL_PATH.stat().st_mtime if MODEL_PATH.is_file() else -1.0
        if not force and mtime == _MTIME:
            return _BUNDLE
        _MTIME = mtime
        manifest, issues = artifact_issues(MODEL_PATH)
        if issues:
            _BUNDLE, _MANIFEST, _ERROR = None, manifest, "; ".join(issues)
            return None
        # Under BTC_FREEZE_MODEL the evidence clock describes ONE bundle. A changed artifact is
        # refused and the pinned one kept, rather than silently spliced into the middle of a run.
        if not _PIN.check(manifest.get("artifact_sha256")):
            return _BUNDLE
        bundle = joblib.load(MODEL_PATH)
        if bundle.get("version") != CONFIG_VERSION or bundle.get("mode") != MODE:
            _BUNDLE, _MANIFEST, _ERROR = None, manifest, "bundle version/mode mismatch"
            return None
        _BUNDLE, _MANIFEST, _ERROR = bundle, manifest, ""
    except Exception as exc:
        _BUNDLE, _ERROR = None, f"{type(exc).__name__}: {exc}"
    return _BUNDLE


def status() -> dict[str, Any]:
    bundle = load_model()
    return {
        "loaded": bundle is not None,
        "promotable": bool(_MANIFEST.get("input_promotable")),
        "training_status": (bundle or {}).get("training_status"),
        "error": _ERROR or None,
        "artifact_hash": _MANIFEST.get("artifact_sha256"),
        "policy_hash": _MANIFEST.get("policy_hash"),
        # Resolution provenance, so a legacy fallback is visible through the status API rather
        # than only in a log line nobody reads.
        "resolution_source": _RESOLUTION.get("source"),
        "bundle_verified": _RESOLUTION.get("verified"),
        "bundle_hash": _RESOLUTION.get("bundle_hash"),
        "evidence_mode": _RESOLUTION.get("evidence_mode"),
        "resolution_note": _RESOLUTION.get("note"),
        **_PIN.status(),
    }


def score(horizon: int, values: dict[str, Any]) -> dict[str, Any]:
    bundle = load_model()
    base = {
        "status": "MODEL_UNAVAILABLE" if not bundle else "MISSING_FEATURES",
        "promotable": status()["promotable"],
        "entry_slippage": {},
        "capacity": {},
        "events": {},
        "reason_codes": [],
    }
    if not bundle:
        base["reason_codes"].append(_ERROR or "artifact_missing")
        return base
    try:
        row = np.asarray(
            [[float(values[column]) for column in FEATURE_COLUMNS]], dtype=np.float32
        )
    except (KeyError, TypeError, ValueError):
        base["reason_codes"].append("missing_feature")
        return base
    if not np.isfinite(row).all():
        base["reason_codes"].append("nonfinite_feature")
        return base
    horizon_bundle = (bundle.get("horizons") or {}).get(int(horizon))
    if not horizon_bundle:
        base["reason_codes"].append("unsupported_horizon")
        return base
    for target, output_key in (
        ("entry_arrival_slippage", "entry_slippage"),
        ("max_executable_qty", "capacity"),
    ):
        models = (horizon_bundle.get("quantiles") or {}).get(target) or {}
        predictions = []
        labels = []
        for quantile in QUANTILES:
            members = models.get(float(quantile))
            if not members:
                continue
            predictions.append(float(predict_member_mean(members, row)[0]))
            labels.append(quantile)
        if predictions:
            ordered = np.maximum.accumulate(predictions)
            base[output_key] = {
                f"q{int(label*100):02d}": round(float(value), 6)
                for label, value in zip(labels, ordered)
            }
    for target, head in (horizon_bundle.get("events") or {}).items():
        if head.get("supported"):
            base["events"][target] = round(
                float(predict_classifier(head["members"], head.get("calibrator"), row)[0]),
                5,
            )
    base["status"] = (
        "SHADOW_ESTIMATE" if base["promotable"] else "PILOT_ESTIMATE_NOT_ACTIONABLE"
    )
    if not base["promotable"]:
        base["reason_codes"].append("insufficient_forward_evidence")
    return base


if __name__ == "__main__":
    assert score(5, {})["status"] in ("MODEL_UNAVAILABLE", "MISSING_FEATURES")
    print("execution_serving self-test: ALL PASS")
