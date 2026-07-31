"""Fail-closed round-state shadow scoring and plain decision-support payload."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import numpy as np
from artifact_identity import artifact_matches_current_training


ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("BTC_DATA_DIR", ROOT / "data"))
MODEL_PATH = DATA / "saved_models" / "round_state_heads.pkl"
EXPECTED_VERSION = "2026-07-02-round-state-shadow-v1"
OPPORTUNITY_TARGET = "next_opportunity_within_3_rounds"

_MODEL = None
_MTIME = -2.0
_CHECKED = 0.0
_ERROR = ""


def load_model() -> dict | None:
    global _MODEL, _MTIME, _CHECKED, _ERROR
    now = time.time()
    if _CHECKED and now - _CHECKED < 30.0:
        return _MODEL
    _CHECKED = now
    try:
        mtime = MODEL_PATH.stat().st_mtime if MODEL_PATH.exists() else -1.0
        if mtime == _MTIME:
            return _MODEL
        if mtime < 0:
            _MODEL, _MTIME, _ERROR = None, mtime, "artifact missing"
            return None
        identity_ok, reasons = artifact_matches_current_training(MODEL_PATH)
        if not identity_ok:
            _MODEL, _ERROR = None, "artifact identity mismatch: " + "; ".join(reasons)
            return None
        loaded = _verified_load(MODEL_PATH)
        if loaded.get("version") != EXPECTED_VERSION:
            _MODEL, _ERROR = None, f"incompatible version {loaded.get('version')}"
            return None
        _MODEL, _MTIME, _ERROR = loaded, mtime, ""
    except Exception as exc:
        # Never retain a previously loaded model after its replacement fails
        # identity verification or deserialization.
        _MODEL, _ERROR = None, str(exc)
    return _MODEL


def status() -> dict:
    model = load_model()
    return {
        "loaded": model is not None,
        "version": model.get("version") if model else None,
        "error": _ERROR or None,
        "artifact": str(MODEL_PATH),
    }


def _score(head: dict | None, values: dict[str, Any]) -> dict:
    metrics = (head or {}).get("metrics") or {}
    base = {
        "probability": None,
        "status": "unavailable" if not head else "rejected",
        "test_auc": metrics.get("test_auc"),
        "test_n": metrics.get("test_n"),
        "auc_gate": metrics.get("auc_gate"),
        "source": "round_state_shadow_v1",
    }
    if not head or not head.get("supported"):
        return base
    features = head.get("features") or []
    try:
        row = np.asarray([[float(values[name]) for name in features]], dtype=np.float32)
        if not np.isfinite(row).all():
            return {**base, "status": "missing_features"}
        raw = np.mean([model.predict_proba(row)[:, 1][0] for _, model in head["members"]])
        probability = float(head["calibrator"].predict([raw])[0])
        return {**base, "probability": round(probability, 4), "status": "shadow"}
    except Exception:
        return {**base, "status": "missing_features"}


def score_snapshot(horizon: int, values: dict[str, Any]) -> dict[str, dict]:
    model = load_model()
    heads = ((model or {}).get("heads") or {}).get(int(horizon), {})
    return {
        target: _score(heads.get(target), values)
        for target in ("future_side_flip", "late_shock_20", "late_shock_50", "late_shock_100")
    }


def score_opportunity(horizon: int, keepers: dict[str, Any] | None) -> dict:
    model = load_model()
    head = (((model or {}).get("heads") or {}).get(int(horizon), {}) or {}).get(OPPORTUNITY_TARGET)
    return _score(head, keepers or {})


def _risk_level(probability: float | None) -> str:
    if probability is None:
        return "UNKNOWN"
    if probability >= 0.55:
        return "HIGH"
    if probability >= 0.30:
        return "MEDIUM"
    return "LOW"


def _round_type(plan: dict) -> str:
    return {
        "quiet": "QUIET",
        "two_sided": "CHOP",
        "one_sided": "TREND",
        "mixed": "ACTIVE",
    }.get(str(plan.get("style") or "").lower(), "WAITING")


def compose(round_data: dict, snapshot_scores: dict[str, dict], opportunity: dict) -> dict:
    """Create one synchronized, child-readable state without changing any decision."""
    leader = round_data.get("current_position")
    p_hold = round_data.get("p_hold")
    plan = round_data.get("trade_plan") or {}
    champion = round_data.get("champion") or {}
    quote = round_data.get("market_quote") or {}
    flip = snapshot_scores.get("future_side_flip") or {}
    p_flip = flip.get("probability")
    if p_flip is None and p_hold is not None:
        flip = {
            **flip,
            "probability": round(1.0 - float(p_hold), 4),
            "status": "settlement_failure_proxy",
            "source": "1 - calibrated P(hold); not the any-recross head",
        }
        p_flip = flip["probability"]

    champion_action = str(champion.get("action") or "WAIT")
    late_anchor = bool(round_data.get("ref_captured_late_ms"))
    if late_anchor:
        action = "AVOID"
    elif champion.get("bet_candidate"):
        action = "PAPER"
    elif champion_action.startswith("AVOID"):
        action = "AVOID"
    else:
        action = "WAIT"

    if quote:
        execution_status = "PAPER_EDGE" if champion.get("bet_candidate") else "NO_EDGE"
        execution_text = (
            f"Live {leader or ''} ask {float(quote.get('ask', 0)) * 100:.1f}c; "
            + (f"paper net edge {float(champion.get('edge')) * 100:.1f}c."
               if champion.get("edge") is not None else "does not clear the conservative edge gate.")
        )
    else:
        execution_status = "WAITING_FOR_LIVE_BOOK"
        execution_text = "No fresh matching Polymarket ask/depth. Profitability cannot be checked."

    p_move_50 = plan.get("p_move_50")
    path_score = "UNKNOWN"
    if p_move_50 is not None:
        path_score = "HIGH" if float(p_move_50) >= 0.70 else "MEDIUM" if float(p_move_50) >= 0.40 else "LOW"

    reasons = []
    if leader in ("UP", "DOWN"):
        reasons.append(f"{leader} currently leads the round by ${abs(float(round_data.get('current_move') or 0)):.0f}.")
    if p_hold is not None:
        reasons.append(f"Calibrated P(hold) is {float(p_hold) * 100:.0f}% for the already-ahead side.")
    if p_flip is not None:
        reasons.append(f"Remaining flip risk is {_risk_level(float(p_flip)).lower()} at {float(p_flip) * 100:.0f}%.")
    reasons.append(execution_text)

    action_reason = (
        "This round was captured after its clock boundary, so its anchor and path history are incomplete. "
        "Wait for the next clean round."
        if late_anchor else
        champion.get("reason") or "Wait for synchronized model and executable-price evidence."
    )
    return {
        "version": EXPECTED_VERSION,
        "mode": "SHADOW_INFO_ONLY",
        "horizon": int(round_data.get("horizon") or 0),
        "action": action,
        "action_reason": action_reason,
        "leader": leader,
        "leader_move_usd": round(float(round_data.get("current_move") or 0.0), 2),
        "price_to_beat": round_data.get("price_to_beat"),
        "current_price": round_data.get("current_price"),
        "seconds_left": round_data.get("seconds_left"),
        "p_leader_holds": p_hold,
        "flip_risk": {**flip, "level": _risk_level(float(p_flip)) if p_flip is not None else "UNKNOWN"},
        "late_shock": {
            "20": snapshot_scores.get("late_shock_20"),
            "50": snapshot_scores.get("late_shock_50"),
            "100": snapshot_scores.get("late_shock_100"),
            "meaning": "Chance BTC moves at least this many dollars before this round expires.",
        },
        "time_to_touch": {
            "seconds_available": round_data.get("seconds_left"),
            "barrier_probabilities": {
                "20": (snapshot_scores.get("late_shock_20") or {}).get("probability"),
                "50": (snapshot_scores.get("late_shock_50") or {}).get("probability"),
                "100": (snapshot_scores.get("late_shock_100") or {}).get("probability"),
            },
            "status": "remaining-window probability; exact touch second is not forecast",
        },
        "round_type": _round_type(plan),
        "path_opportunity": {
            "score": path_score,
            "p_move_50": p_move_50,
            "p_roundtrip_50": plan.get("p_roundtrip"),
            "play": plan.get("play"),
        },
        "next_three_rounds": opportunity,
        "execution": {
            "status": execution_status,
            "text": execution_text,
            "quote_age_seconds": quote.get("age_seconds"),
            "ask": quote.get("ask"),
            "bid": quote.get("bid"),
            "depth": quote.get("depth"),
            "paper_edge": champion.get("edge"),
        },
        "reasons": reasons,
        "champion_unchanged": True,
    }


def selftest() -> None:
    global MODEL_PATH, _MODEL, _MTIME, _CHECKED, _ERROR
    payload = compose(
        {"horizon": 5, "current_position": "UP", "current_move": 25, "p_hold": 0.9,
         "trade_plan": {"style": "one_sided", "p_move_50": 0.7, "p_roundtrip": 0.2},
         "champion": {"action": "WAIT", "reason": "No executable quote."}},
        {},
        {"probability": None, "status": "unavailable"},
    )
    assert payload["action"] == "WAIT"
    assert payload["flip_risk"]["status"] == "settlement_failure_proxy"
    assert payload["champion_unchanged"] is True
    late = compose(
        {"horizon": 5, "ref_captured_late_ms": 8_000, "champion": {"action": "PAPER_BET",
         "bet_candidate": True}}, {}, {"probability": None, "status": "unavailable"})
    assert late["action"] == "AVOID"

    original_path = MODEL_PATH
    original_loader = globals()["_verified_load"]
    original_identity = globals()["artifact_matches_current_training"]
    original_state = (_MODEL, _MTIME, _CHECKED, _ERROR)
    try:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "round_state_heads.pkl"
            candidate.write_bytes(b"not-a-model")
            MODEL_PATH = candidate
            globals()["artifact_matches_current_training"] = lambda _: (True, [])

            def _fail_load(_):
                raise ValueError("simulated corrupt artifact")

            globals()["_verified_load"] = _fail_load
            _MODEL = {"version": EXPECTED_VERSION}
            _MTIME = -2.0
            _CHECKED = 0.0
            _ERROR = ""
            assert load_model() is None
            assert _MODEL is None
            assert "simulated corrupt artifact" in _ERROR
    finally:
        MODEL_PATH = original_path
        globals()["_verified_load"] = original_loader
        globals()["artifact_matches_current_training"] = original_identity
        _MODEL, _MTIME, _CHECKED, _ERROR = original_state
    print("ROUND STATE PANEL SELFTEST PASS")


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
