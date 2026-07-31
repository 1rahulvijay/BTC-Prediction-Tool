"""Load the shipped P(hold) calibrator and give it an honest leakage boundary.

WHY fitted_through_ms COMES FROM THE FILE'S OWN GENERATION TIME
    backend/phold_challenger.py fits its calibrators on every resolved round in the analytics
    DB at the moment it runs. So every round that existed when the file was written took part
    in fitting it, and scoring any of them with it is leakage.

    Rounds that happen AFTER the file was written are clean - the calibrator could not have
    seen them. `generated_utc` is therefore exactly the boundary, and this loader hands it to
    Calibration.fitted_through_ms, where decide() enforces it by raising.

    That makes the live wiring safe by construction: forward rounds score, historical rounds
    refuse. It also means a backtest cannot quietly reuse this calibrator on the sample it was
    fitted on, which is the error that would make any evaluation of it meaningless.

REFUSALS
    Returns None when the file is missing, unparseable, marked not deployable, or when the
    horizon has no calibrator. A missing calibrator must disable the strategy, never fall back
    to raw p_hold - raw p_hold is overconfident by ~8.7 points and every "cheap" call it makes
    is biased, which is the whole reason this path exists.

    python -m backend.polymarket_paper.calibration_loader --selftest
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .calibrated_fair_value import Calibration

ROOT = Path(__file__).resolve().parents[2]
CALIBRATOR_PATH = ROOT / "data" / "research" / "phold_challenger" / "phold_calibrators.json"


def _to_ms(stamp: str) -> int:
    text = str(stamp).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def load(horizon: int, path: Path | None = None) -> Calibration | None:
    """Return a Calibration for `horizon`, or None. Never raises on bad input."""
    source = Path(path) if path is not None else CALIBRATOR_PATH
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    entry = ((payload.get("horizons") or {}).get(str(int(horizon))) or {})
    if not entry or not entry.get("deployable"):
        return None
    params = entry.get("params") or {}
    boundary_text = payload.get("generated_utc")
    if not boundary_text:
        return None
    try:
        boundary_ms = _to_ms(boundary_text)
    except ValueError:
        return None

    kind = params.get("kind")
    if kind == "isotonic":
        x, y = params.get("x") or [], params.get("y") or []
        if len(x) < 2 or len(x) != len(y):
            return None
        knots_x, knots_y = list(map(float, x)), list(map(float, y))
    elif kind == "logistic":
        # Materialise Platt as monotone knots so the strategy has one representation to reason
        # about, and so Calibration's monotonicity check covers both kinds identically.
        import math
        a, b = float(params.get("a", 1.0)), float(params.get("b", 0.0))
        if a <= 0.0:
            return None                      # a non-positive slope would invert the ranking
        knots_x, knots_y = [], []
        for step in range(0, 101):
            probability = min(max(step / 100.0, 1e-6), 1 - 1e-6)
            odds = math.log(probability / (1 - probability))
            knots_x.append(probability)
            knots_y.append(1.0 / (1.0 + math.exp(-(a * odds + b))))
    else:
        return None

    try:
        return Calibration(x=tuple(knots_x), y=tuple(knots_y),
                           fitted_through_ms=boundary_ms, horizon=int(horizon))
    except ValueError:
        return None                          # non-monotone or malformed: refuse, never serve


def _selftest() -> int:
    import tempfile
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    check(load(5, Path("does-not-exist.json")) is None, "missing file returns None, not a crash")

    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        check(load(5, bad) is None, "unparseable file returns None")

        not_deployable = Path(tmp) / "nd.json"
        not_deployable.write_text(json.dumps({
            "generated_utc": "2026-07-31T20:28:15+00:00",
            "horizons": {"5": {"deployable": False,
                               "params": {"kind": "isotonic", "x": [0, 1], "y": [0, 1]}}}
        }), encoding="utf-8")
        check(load(5, not_deployable) is None, "a not-deployable calibrator is refused")

        inverted = Path(tmp) / "inv.json"
        inverted.write_text(json.dumps({
            "generated_utc": "2026-07-31T20:28:15+00:00",
            "horizons": {"5": {"deployable": True,
                               "params": {"kind": "logistic", "a": -1.0, "b": 0.0}}}
        }), encoding="utf-8")
        check(load(5, inverted) is None, "a negative Platt slope is refused (it inverts ranking)")

    live = load(5)
    if live is not None:
        check(live.horizon == 5, "loads the shipped 5m calibrator")
        check(live.fitted_through_ms > 0, "boundary comes from the file's generated_utc")
        check(all(b >= a for a, b in zip(live.y, live.y[1:])), "loaded knots are monotone")
        earlier = live.fitted_through_ms - 1
        from .calibrated_fair_value import Quote, decide, CalibrationRefused
        try:
            decide(Quote("r", earlier, 5, 60, "UP", 0.95, 0.70, 0.68, 0.01), live)
            check(False, "unreachable")
        except CalibrationRefused:
            check(True, "a round before the boundary REFUSES with the shipped calibrator")
    else:
        print("  NOTE  no shipped calibrator on disk; live-path checks skipped")

    check(load(15) is None or load(15).horizon == 15, "15m loads as 15m or refuses")
    print(f"\nCALIBRATION LOADER SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return _selftest()
    for horizon in (5, 15):
        calibration = load(horizon)
        if calibration is None:
            print(f"{horizon}m: NO CALIBRATOR (strategy disabled for this horizon)")
        else:
            stamp = datetime.fromtimestamp(calibration.fitted_through_ms / 1000, timezone.utc)
            print(f"{horizon}m: loaded, {len(calibration.x)} knots, "
                  f"refuses rounds at or before {stamp:%Y-%m-%d %H:%M:%S} UTC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
