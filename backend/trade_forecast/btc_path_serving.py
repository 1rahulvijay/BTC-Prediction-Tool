"""Fail-closed serving for time-indexed BTC path and first-passage forecasts."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from .model_common import artifact_issues, predict_member_mean
from .trade_schema import (
    BTC_FEATURE_COLUMNS,
    CONFIG_VERSION,
    FUTURE_OFFSETS_S,
    target_offset_valid,
    MODE,
    QUANTILES,
)
from .train_btc_path_model import EVENT_CLASSES
from .champion_resolver import resolve_artifact
from .freeze_guard import ArtifactPin


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
ARTIFACT_NAME = "complete_trade_btc_path.pkl"
LEGACY_PATH = DATA / "saved_models" / ARTIFACT_NAME
# Resolved through the champion bundle on every load. A promotion that swaps
# champion.json must actually reach serving, or the atomic pointer is decorative.
MODEL_PATH = LEGACY_PATH          # rebound by _resolve() below


_BUNDLE = None
_MANIFEST: dict[str, Any] = {}
_RESOLUTION: dict = {}
_TOKEN = None
_PIN = ArtifactPin("btc")
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
        manifest, issues = artifact_issues(
            MODEL_PATH,
            require_promotable=False,
            expected_feature_columns=BTC_FEATURE_COLUMNS,
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
        "promotable": bool(_MANIFEST.get("input_promotable")),
        "training_status": (bundle or {}).get("training_status"),
        "independent_rounds": _MANIFEST.get("independent_rounds"),
        "calendar_weeks": _MANIFEST.get("calendar_weeks"),
        "error": _ERROR or None,
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


def _row(values: dict[str, Any]) -> np.ndarray | None:
    try:
        output = np.asarray(
            [[float(values[column]) for column in BTC_FEATURE_COLUMNS]], dtype=np.float32
        )
    except (KeyError, TypeError, ValueError):
        return None
    return output if np.isfinite(output).all() else None


def _competing_probabilities(head: dict[str, Any], row: np.ndarray) -> dict[str, float]:
    raw_members = []
    for _, model in head["members"]:
        raw = np.asarray(model.predict_proba(row), dtype=float)[0]
        values = np.zeros(len(EVENT_CLASSES), dtype=float)
        for source, label in enumerate(model.classes_):
            if str(label) in EVENT_CLASSES:
                values[EVENT_CLASSES.index(str(label))] = raw[source]
        raw_members.append(values)
    values = np.mean(raw_members, axis=0)
    for index, label in enumerate(EVENT_CLASSES):
        calibrator = (head.get("calibrators") or {}).get(label)
        if calibrator is not None:
            values[index] = float(calibrator.predict([values[index]])[0])
    total = float(values.sum())
    values = values / total if total > 0 else np.full(len(values), 1.0 / len(values))
    return {label: round(float(values[index]), 5) for index, label in enumerate(EVENT_CLASSES)}


def score(horizon: int, values: dict[str, Any]) -> dict[str, Any]:
    bundle = load_model()
    model_status = status()
    base = {
        "mode": MODE,
        "status": "MODEL_UNAVAILABLE" if not bundle else "MISSING_FEATURES",
        "promotable": model_status["promotable"],
        "path": {},
        "summary": {},
        "competing_risk": {},
        "reason_codes": [],
    }
    if not bundle:
        base["reason_codes"].append(_ERROR or "artifact_missing")
        return base
    row = _row(values)
    if row is None:
        base["reason_codes"].append("missing_or_nonfinite_feature")
        return base
    horizon_bundle = (bundle.get("horizons") or {}).get(int(horizon))
    if not horizon_bundle:
        base["reason_codes"].append("unsupported_horizon")
        return base
    current = float(values["current_btc"])
    seconds_left = float(values.get("seconds_left") or 0.0)
    for offset in (*FUTURE_OFFSETS_S, "settlement"):
        # "settlement" is always reachable; a numeric offset past expiry is not.
        if offset != "settlement" and not target_offset_valid(offset, seconds_left):
            continue
        key = f"{offset}s" if isinstance(offset, int) else str(offset)
        models = (horizon_bundle.get("quantiles") or {}).get(key) or {}
        predictions = []
        labels = []
        for quantile in QUANTILES:
            members = models.get(float(quantile))
            if not members:
                continue
            bps = float(predict_member_mean(members, row)[0])
            predictions.append(current * (1.0 + bps / 10_000.0))
            labels.append(float(quantile))
        if predictions:
            ordered = np.maximum.accumulate(np.asarray(predictions))
            base["path"][key] = {
                f"q{int(quantile*100):02d}": round(float(value), 2)
                for quantile, value in zip(labels, ordered)
            }
    risk = horizon_bundle.get("competing_risk") or {}
    if risk.get("supported"):
        base["competing_risk"] = _competing_probabilities(risk, row)
    for label in ("mfe", "mae", "first_event_time"):
        models = (horizon_bundle.get("quantiles") or {}).get(label) or {}
        predictions = []
        quantile_labels = []
        for quantile in QUANTILES:
            members = models.get(float(quantile))
            if not members:
                continue
            value = float(predict_member_mean(members, row)[0])
            predictions.append(value)
            quantile_labels.append(float(quantile))
        if predictions:
            ordered = np.maximum.accumulate(np.asarray(predictions, dtype=float))
            point = {
                f"q{int(quantile * 100):02d}": round(float(value), 6)
                for quantile, value in zip(quantile_labels, ordered)
            }
            if label in ("mfe", "mae"):
                point["unit"] = "bps"
                point["q50_usd"] = (
                    round(current * float(point["q50"]) / 10_000.0, 2)
                    if point.get("q50") is not None
                    else None
                )
            else:
                point["unit"] = "seconds"
            base["summary"][label] = point
    base["status"] = (
        "SHADOW_ESTIMATE"
        if model_status["promotable"]
        else "PILOT_ESTIMATE_NOT_ACTIONABLE"
    )
    if not model_status["promotable"]:
        base["reason_codes"].append("insufficient_forward_evidence")
    return base


def selftest() -> None:
    assert _row({column: 1.0 for column in BTC_FEATURE_COLUMNS}) is not None
    assert _row({}) is None
    print("btc_path_serving self-test: ALL PASS")


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