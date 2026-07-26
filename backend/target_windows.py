"""Predeclared training-window policy by prediction target.

Longer is not automatically better. Direction, volatility, calibration, and
execution targets decay at different rates, so experiments must compare only
the windows declared here and must not silently choose a window on the test set.
"""
from __future__ import annotations


TARGET_WINDOW_DAYS = {
    "direction": (90, 400, 1265),
    "big_move": (400, 1265),
    "path_quantile": (400, 1265),
    "regime_transition": (400, 1265),
    "tail_risk": (400, 1265),
    "p_hold_calibration": (30, 90),
    "execution": (7, 30),
}

WINDOW_EXPERTS = {
    "W90": {"days": 90, "weight_mode": "uniform"},
    "W400": {"days": 400, "weight_mode": "uniform"},
    "W1265_RECENCY": {"days": 1265, "weight_mode": "recency"},
    "W1265_SIMILARITY": {"days": 1265, "weight_mode": "recency_similarity"},
}


def windows_for_target(target: str) -> tuple[int, ...]:
    normalized = str(target).strip().lower()
    prefix_map = (
        ("p_hold", "p_hold_calibration"),
        ("path", "path_quantile"),
        ("big_move", "big_move"),
        ("regime", "regime_transition"),
        ("tail", "tail_risk"),
        ("direction", "direction"),
        ("execution", "execution"),
    )
    family = next(
        (mapped for prefix, mapped in prefix_map if normalized.startswith(prefix)),
        normalized,
    )
    return TARGET_WINDOW_DAYS.get(family, (400,))


def expert_specs_for_target(target: str) -> dict[str, dict]:
    allowed = set(windows_for_target(target))
    return {
        name: dict(spec)
        for name, spec in WINDOW_EXPERTS.items()
        if int(spec["days"]) in allowed
    }


def selftest() -> None:
    assert set(expert_specs_for_target("direction_5m")) == {
        "W90",
        "W400",
        "W1265_RECENCY",
        "W1265_SIMILARITY",
    }
    assert set(expert_specs_for_target("big_move_5m")) == {
        "W400",
        "W1265_RECENCY",
        "W1265_SIMILARITY",
    }
    assert windows_for_target("p_hold_calibration_5m") == (30, 90)
    assert windows_for_target("regime_transition_15m") == (400, 1265)
    assert windows_for_target("tail_risk_15m") == (400, 1265)
    assert windows_for_target("execution_fill") == (7, 30)
    print("target_windows self-test: ALL PASS")


if __name__ == "__main__":
    selftest()
