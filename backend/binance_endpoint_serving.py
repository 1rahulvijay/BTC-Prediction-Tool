"""Verified serving adapter for the Binance endpoint-direction paper head.

The main ensemble predicts first barrier touched. Binance directional EV needs the
exchange price sign at the horizon endpoint. This adapter loads the separately
trained settlement_head artifact and never substitutes the main probability.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Callable

import numpy as np

from artifact_identity import artifact_matches_current_training
from settlement_head import settlement_probability
import target_contract as tc
from verified_io import verified_load


_BUNDLE: dict | None = None
_BUNDLE_PATH: Path | None = None
_BUNDLE_SHA256 = ""
_BUNDLE_SIGNATURE: tuple[int, int] | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return int(stat.st_mtime_ns), int(stat.st_size)
    except OSError:
        return None


def _load(model_dir: str | Path) -> tuple[dict | None, dict[str, Any]]:
    global _BUNDLE, _BUNDLE_PATH, _BUNDLE_SHA256, _BUNDLE_SIGNATURE
    path = Path(model_dir).resolve() / "settlement_head.pkl"
    signature = _signature(path)
    if (
        _BUNDLE is not None
        and _BUNDLE_PATH == path
        and _BUNDLE_SIGNATURE == signature
    ):
        return _BUNDLE, {
            "status": "READY",
            "artifact": str(path),
            "artifact_sha256": _BUNDLE_SHA256,
        }
    if not path.exists():
        return None, {"status": "ARTIFACT_MISSING", "artifact": str(path)}
    identity_ok, reasons = artifact_matches_current_training(str(path))
    if not identity_ok:
        return None, {
            "status": "IDENTITY_MISMATCH",
            "artifact": str(path),
            "reasons": list(reasons),
        }
    try:
        bundle = verified_load(path)
    except Exception as exc:
        return None, {
            "status": "LOAD_FAILED",
            "artifact": str(path),
            "error": f"{type(exc).__name__}: {exc}",
        }
    if not isinstance(bundle, dict):
        return None, {"status": "INVALID_BUNDLE", "artifact": str(path)}
    loaded_signature = _signature(path)
    if loaded_signature is None or loaded_signature != signature:
        return None, {
            "status": "ARTIFACT_CHANGED_DURING_LOAD",
            "artifact": str(path),
        }
    contract = str(bundle.get("target_contract") or "")
    try:
        tc.assert_admissible(tc.BINANCE_DIRECTIONAL_PAPER_EV, contract)
    except tc.ContractMisuse as exc:
        return None, {
            "status": "TARGET_CONTRACT_MISMATCH",
            "artifact": str(path),
            "error": str(exc),
        }
    loaded_sha256 = _sha256(path)
    if _signature(path) != loaded_signature:
        return None, {
            "status": "ARTIFACT_CHANGED_DURING_HASH",
            "artifact": str(path),
        }
    _BUNDLE = bundle
    _BUNDLE_PATH = path
    _BUNDLE_SHA256 = loaded_sha256
    _BUNDLE_SIGNATURE = loaded_signature
    return bundle, {
        "status": "READY",
        "artifact": str(path),
        "artifact_sha256": _BUNDLE_SHA256,
    }


def predict(
    model_dir: str | Path,
    sequence: np.ndarray,
    feature_selector: Callable[[np.ndarray], np.ndarray],
    magnitude_prediction: dict[str, Any] | None,
    *,
    horizon: int = 5,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    bundle, status = _load(model_dir)
    if bundle is None:
        return None, status
    metrics = (bundle.get("metrics") or {}).get(horizon) or {}
    if metrics.get("beats_prior") is not True:
        return None, {
            **status,
            "status": "HOLDOUT_GATE_FAILED",
            "metrics": metrics,
        }
    try:
        selected = np.asarray(feature_selector(np.asarray(sequence)), dtype=float)
        scored = settlement_probability(bundle, selected, horizon)
    except Exception as exc:
        return None, {
            **status,
            "status": "INFERENCE_FAILED",
            "error": f"{type(exc).__name__}: {exc}",
        }
    p_up = float(scored["p_up"])
    p_down = float(scored["p_down"])
    if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in (p_up, p_down)):
        return None, {**status, "status": "NON_FINITE_PROBABILITY"}
    if abs((p_up + p_down) - 1.0) > 1e-6:
        return None, {**status, "status": "PROBABILITY_MASS_INVALID"}
    source = magnitude_prediction or {}
    expected_move = source.get("expectedMove")
    move_range = source.get("expectedMoveRange")
    if expected_move is None or not isinstance(move_range, dict):
        return None, {**status, "status": "MAGNITUDE_HEAD_UNAVAILABLE"}
    direction = "UP" if p_up >= p_down else "DOWN"
    probability = p_up if direction == "UP" else p_down
    prediction = {
        "horizon": int(horizon),
        "direction": direction,
        "finalDirection": direction,
        "calibratedConfidence": probability,
        "probUp": p_up,
        "probDown": p_down,
        "expectedMove": expected_move,
        "expectedMoveRange": dict(move_range),
        "stopLoss": source.get("stopLoss"),
        "model_bundle_id": f"settlement:{_BUNDLE_SHA256}",
        "targetContract": scored["target_contract"],
        "endpointHeadReady": True,
        "probability_calibrated": True,
        "research_only": True,
        "holdout_metrics": dict(metrics),
        "independence_validated": bool(bundle.get("independence_validated")),
    }
    return prediction, {**status, "metrics": metrics}


def reset_cache_for_tests() -> None:
    global _BUNDLE, _BUNDLE_PATH, _BUNDLE_SHA256, _BUNDLE_SIGNATURE
    _BUNDLE = None
    _BUNDLE_PATH = None
    _BUNDLE_SHA256 = ""
    _BUNDLE_SIGNATURE = None
