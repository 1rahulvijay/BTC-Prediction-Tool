"""Fail-closed serving for executable share-price path quantiles."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from .model_common import artifact_issues, predict_classifier, predict_member_mean
from .trade_labels import inv_logit, logit
from .trade_schema import (
    CONFIG_VERSION,
    FEATURE_COLUMNS,
    FUTURE_OFFSETS_S,
    target_offset_valid,
    MODE,
    QUANTILES,
)


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
ARTIFACT_NAME = "complete_trade_share_path.pkl"
LEGACY_PATH = DATA / "saved_models" / ARTIFACT_NAME
# Resolved through the champion bundle on every load. A promotion that swaps
# champion.json must actually reach serving, or the atomic pointer is decorative.
MODEL_PATH = LEGACY_PATH          # rebound by _resolve() below


from .champion_resolver import resolve_artifact
from .freeze_guard import ArtifactPin


_BUNDLE: dict[str, Any] | None = None
_MTIME = -1.0
_CHECKED = 0.0
_ERROR = ""
_MANIFEST: dict[str, Any] = {}
_RESOLUTION: dict = {}
_TOKEN = None
_PIN = ArtifactPin("share")


def load_model(force: bool = False) -> dict[str, Any] | None:
    global _BUNDLE, _MTIME, _CHECKED, _ERROR, _MANIFEST
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
        manifest, issues = artifact_issues(
            MODEL_PATH,
            require_promotable=False,
            require_training_dataset=not bool(_RESOLUTION.get("verified")),
        )
        if issues:
            _BUNDLE, _MANIFEST, _ERROR = None, manifest, "; ".join(issues)
            return None
        # Under BTC_FREEZE_MODEL the evidence clock describes ONE bundle. A changed artifact is
        # refused and the pinned one kept, rather than silently spliced into the middle of a run.
        if not _PIN.check(manifest.get("artifact_sha256")):
            return _BUNDLE
        bundle = _verified_load(MODEL_PATH)
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
        "training_status": (bundle or {}).get("training_status"),
        "promotable": bool(_MANIFEST.get("input_promotable"))
        and bool(_MANIFEST.get("m0_passed")),
        "independent_rounds": _MANIFEST.get("independent_rounds"),
        "calendar_weeks": _MANIFEST.get("calendar_weeks"),
        "error": _ERROR or None,
        "artifact": str(MODEL_PATH),
        "artifact_hash": _MANIFEST.get("artifact_sha256"),
        "policy_hash": _MANIFEST.get("policy_hash"),
        # Resolution provenance, so a legacy fallback is visible through the status API rather
        # than only in a log line nobody reads.
        "resolution_source": _RESOLUTION.get("source"),
        "bundle_verified": _RESOLUTION.get("verified"),
        "bundle_hash": _RESOLUTION.get("bundle_hash"),
        "bundle_manifest_sha256": _RESOLUTION.get("bundle_manifest_sha256"),
        "promoted_at": _RESOLUTION.get("promoted_at"),
        "evidence_mode": _RESOLUTION.get("evidence_mode"),
        "resolution_note": _RESOLUTION.get("note"),
        **_PIN.status(),
    }


def _feature_row(values: dict[str, Any]) -> np.ndarray | None:
    try:
        row = np.asarray([[float(values[name]) for name in FEATURE_COLUMNS]], dtype=np.float32)
    except (KeyError, TypeError, ValueError):
        return None
    return row if np.isfinite(row).all() else None


def score_candidate(horizon: int, values: dict[str, Any]) -> dict[str, Any]:
    bundle = load_model()
    model_status = status()
    base = {
        "mode": MODE,
        "status": "MODEL_UNAVAILABLE" if not bundle else "MISSING_FEATURES",
        "promotable": model_status["promotable"],
        "path": {},
        "ask_path": {},
        "summary": {},
        "events": {},
        "crossing_path": {},
        "reason_codes": [],
    }
    if not bundle:
        base["reason_codes"].append(_ERROR or "artifact_missing")
        return base
    row = _feature_row(values)
    if row is None:
        base["reason_codes"].append("missing_or_nonfinite_feature")
        return base
    horizon_bundle = (bundle.get("horizons") or {}).get(int(horizon))
    if not horizon_bundle:
        base["reason_codes"].append("unsupported_horizon")
        return base
    current_bid = float(values["own_bid"])
    # Same validity rule as the dataset builder, from the same function. A 120s path point at a
    # 30s-left checkpoint is not a forecast - the contract settles first, and after the label fix
    # the model has no training signal there at all.
    seconds_left = float(values.get("seconds_left") or 0.0)
    for offset in FUTURE_OFFSETS_S:
        if not target_offset_valid(offset, seconds_left):
            continue
        models = (horizon_bundle.get("quantiles") or {}).get(int(offset)) or {}
        transformed: list[float] = []
        labels: list[float] = []
        for quantile in QUANTILES:
            members = models.get(float(quantile))
            if not members:
                continue
            delta = float(predict_member_mean(members, row)[0])
            transformed.append(inv_logit(logit(current_bid) + delta))
            labels.append(float(quantile))
        if transformed:
            ordered = np.maximum.accumulate(np.asarray(transformed, dtype=float))
            base["path"][str(offset)] = {
                f"q{int(quantile * 100):02d}": round(float(value), 5)
                for quantile, value in zip(labels, ordered)
            }
    for target, head in (horizon_bundle.get("events") or {}).items():
        if not head.get("supported"):
            continue
        probability = float(
            predict_classifier(head["members"], head.get("calibrator"), row)[0]
        )
        probability = round(min(1.0, max(0.0, probability)), 5)
        if target.startswith("label_") and "_by_" in target:
            event, raw_offset = target[6:].rsplit("_by_", 1)
            offset = raw_offset[:-1] if raw_offset.endswith("s") else raw_offset
            base["crossing_path"].setdefault(str(offset), {})[event] = probability
        else:
            base["events"][target] = probability
    current_ask = float(values["own_ask"])
    for offset in FUTURE_OFFSETS_S:
        models = (horizon_bundle.get("ask_quantiles") or {}).get(int(offset)) or {}
        predictions = []
        labels = []
        for quantile in (0.10, 0.50, 0.90):
            members = models.get(float(quantile))
            if not members:
                continue
            delta = float(predict_member_mean(members, row)[0])
            predictions.append(inv_logit(logit(current_ask) + delta))
            labels.append(quantile)
        if predictions:
            ordered = np.maximum.accumulate(np.asarray(predictions, dtype=float))
            base["ask_path"][str(offset)] = {
                f"q{int(quantile * 100):02d}": round(float(value), 5)
                for quantile, value in zip(labels, ordered)
            }
    for target, models in (horizon_bundle.get("summary_quantiles") or {}).items():
        predictions = []
        labels = []
        for quantile in (0.10, 0.50, 0.90):
            members = models.get(float(quantile))
            if not members:
                continue
            predictions.append(float(predict_member_mean(members, row)[0]))
            labels.append(quantile)
        if predictions:
            ordered = np.maximum.accumulate(np.asarray(predictions, dtype=float))
            base["summary"][target] = {
                f"q{int(label * 100):02d}": round(float(value), 6)
                for label, value in zip(labels, ordered)
            }
    base["status"] = (
        "SHADOW_ESTIMATE"
        if model_status["promotable"]
        else "PILOT_ESTIMATE_NOT_ACTIONABLE"
    )
    if not model_status["promotable"]:
        base["reason_codes"].append("insufficient_forward_evidence")
    return base


def selftest() -> None:
    assert _feature_row({name: 1.0 for name in FEATURE_COLUMNS}) is not None
    assert _feature_row({}) is None
    output = score_candidate(5, {})
    assert output["status"] in ("MODEL_UNAVAILABLE", "MISSING_FEATURES")
    print("share_path_serving self-test: ALL PASS")


def _verified_load(path):
    """Hash-check against the sidecar manifest BEFORE deserializing.

    Deserialization executes arbitrary code, so validating after loading has already lost.
    Pre-migration artifacts carry no manifest; they load while BTC_STRICT_ARTIFACT_IDENTITY
    is off and are counted as remaining debt."""
    import sys as _sys
    from pathlib import Path as _Path

    for _up in (1, 2, 3):
        _cand = str(_Path(__file__).resolve().parents[_up - 1])
        if (_Path(_cand) / "verified_io.py").is_file() and _cand not in _sys.path:
            _sys.path.insert(0, _cand)
    from verified_io import verified_load as _vl

    return _vl(path)


if __name__ == "__main__":
    selftest()