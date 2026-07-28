"""Serving-side P(hold) calibration - the adoption half of phold_challenger.

THE GAP THIS CLOSES

`phold_challenger.py` has been fitting and scoring calibrators for a while and winning:
on the Oracle snapshot the 5m isotonic calibrator beats raw on all three metrics, cutting
log-loss 0.940 -> 0.343 and ECE 0.0883 -> 0.0136, and pulling predicted 95.5% down to
87.4% against a realized 86.8%. None of that reached serving. The challenger reported a
`calibrator_hash` but persisted only the knot COUNT, so even a winning calibrator could
not be rebuilt. It was a measurement with no path to use.

WHY THIS MATTERS BEYOND ACCURACY

`monitoring/head_health.py` marks p_hold CALIBRATION_ONLY because ECE 0.0678 > 0.05, and
`head_permissions.may_price("p_hold")` therefore refuses to let it set a fair value. A
calibrator measuring ECE 0.0136 is precisely the thing that can return that head to
USABLE. This module is the bridge between the two.

SAFETY

  - DEFAULT OFF (`BTC_PHOLD_CALIBRATION_MODE=off`).
  - Adoption ALSO requires beats_raw_on_all_three=True. A flag cannot promote a loser.
  - `optional` mode falls back to raw (research/shadow). `required` mode does NOT: an
    invalid calibrator yields NO probability and revokes may_price/may_size. Falling back
    to raw would mean falling back to the estimator the calibration exists to correct.
  - Identity binding is the real validity gate: a calibrator maps ONE model bundle's score
    distribution, so it is refused when feature/training semantics differ. Wall-clock age
    is only a warning.
  - EVERY calibrator generated today is deployable=false with reason
    SOURCE_MODEL_REQUIRES_RETRAINING, because all 12 source artifacts fail identity
    enforcement. Research-valid, deployment-invalid.
  - Calibration only ever LOWERS an overconfident probability here; it cannot manufacture
    edge. But it does change fair value, so it stays an explicit operator decision.

DOWNSTREAM THRESHOLDS GO STALE THE MOMENT THIS IS ENABLED

    Measured on the current artifact, 5m: raw 0.930 -> 0.812, raw 0.955 -> 0.821,
    raw 0.990 -> 0.828. Calibrated P(hold) SATURATES around 0.83.

    `decision_champion.PHOLD_STRONG = 0.93` and `DEFAULT_ENTRY_FAIR_CAP = 0.91` were
    chosen against RAW, overconfident probabilities. Under calibration nothing ever
    reaches 0.93, so those gates would simply stop firing - the system would go quiet
    rather than wrong. That is the safe failure direction, but it is not a no-op.

    The wrong response is to lower the thresholds until signals reappear: that
    re-introduces exactly the overconfidence the calibration removed. The right response
    is to RE-DERIVE the thresholds from calibrated probabilities against realized
    outcomes, as a separate, evidenced change. Until that is done, leave the flag at 0.

    python backend/phold_calibrator.py            # show current adoption state
    python backend/phold_calibrator.py --selftest
"""
from __future__ import annotations

import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
ARTIFACT = os.path.join(DATA, "research", "phold_challenger", "phold_calibrators.json")

# Three modes, because "on/off" cannot express the important case.
#   off       - raw P(hold), current behaviour, no calibration attempted
#   optional  - use the calibrator when valid, else raw (research/shadow only)
#   required  - the head MAY NOT price from raw. An invalid calibrator produces NO
#               probability at all and revokes may_price / may_size.
#
# The earlier design failed OPEN to raw in every case. That is unsafe: raw is precisely
# what the calibrator measured as overconfident (5m predicted 95.5% vs realized 86.8%),
# so "fall back to raw" means "fall back to the known-bad estimator" at exactly the
# moment the safeguard failed.
MODE = os.environ.get("BTC_PHOLD_CALIBRATION_MODE", "off").strip().lower()
if os.environ.get("BTC_APPLY_PHOLD_CALIBRATION", "0") == "1" and MODE == "off":
    # The legacy flag maps to REQUIRED, not optional. An operator who switches
    # calibration on has declared that raw is not acceptable for pricing; falling back to
    # raw on failure would hand them the exact estimator they were correcting, silently.
    # `optional` must be asked for explicitly and is for shadow/research only.
    MODE = "required"
ENABLED = MODE in ("optional", "required")

# Typed refusals. A caller must be able to say WHY there is no calibrated probability.
CALIBRATOR_MISSING = "CALIBRATOR_MISSING"
CALIBRATOR_NOT_DEPLOYABLE = "CALIBRATOR_NOT_DEPLOYABLE"
CALIBRATOR_SOURCE_MODEL_MISMATCH = "CALIBRATOR_SOURCE_MODEL_MISMATCH"
CALIBRATOR_STALE_SEMANTICS = "CALIBRATOR_STALE_SEMANTICS"
CALIBRATOR_TAMPERED = "CALIBRATOR_TAMPERED"
CALIBRATOR_INSUFFICIENT_EVIDENCE = "CALIBRATOR_INSUFFICIENT_EVIDENCE"
CALIBRATOR_DRIFTED = "CALIBRATOR_DRIFTED"

# Wall-clock age is a WARNING, not the validity test. A 31-day-old calibrator bound to an
# unchanged model is fine; a 7-day-old one whose source model was replaced is invalid.
# Identity binding below is the real gate.
MAX_AGE_S = 30 * 24 * 3600
_CACHE: dict = {"ts": 0.0, "val": None}
_CACHE_TTL_S = 60.0


def _load() -> dict | None:
    now = time.time()
    if _CACHE["val"] is not None and now - _CACHE["ts"] < _CACHE_TTL_S:
        return _CACHE["val"]
    _CACHE["ts"] = now
    try:
        with open(ARTIFACT, encoding="utf-8") as fh:
            art = json.load(fh)
        art["_age_s"] = now - os.path.getmtime(ARTIFACT)
        _CACHE["val"] = art
    except Exception:
        _CACHE["val"] = None
    return _CACHE["val"]


def _apply_params(p: float, params: dict) -> float:
    """Reconstruct the mapping from stored parameters. Pure arithmetic - no sklearn."""
    kind = params.get("kind")
    if kind == "logistic":
        import math
        q = min(max(float(p), 1e-6), 1 - 1e-6)
        z = params["a"] * math.log(q / (1 - q)) + params["b"]
        z = min(max(z, -30.0), 30.0)
        return 1.0 / (1.0 + math.exp(-z))
    if kind == "isotonic":
        xs, ys = params.get("x") or [], params.get("y") or []
        if not xs:
            return float(p)
        q = min(max(float(p), 0.0), 1.0)
        if q <= xs[0]:
            return float(min(max(ys[0], 0.0), 1.0))
        if q >= xs[-1]:
            return float(min(max(ys[-1], 0.0), 1.0))
        for i in range(1, len(xs)):                     # linear interp between knots
            if q <= xs[i]:
                x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
                t = 0.0 if x1 == x0 else (q - x0) / (x1 - x0)
                return float(min(max(y0 + t * (y1 - y0), 0.0), 1.0))
    return float(p)


def calibration_state(horizon: int = 5) -> tuple[bool, str, str | None]:
    """(active, reason, refusal_code). Never raises.

    `refusal_code` is None when active, otherwise one of the CALIBRATOR_* constants so a
    caller can distinguish "not configured" from "configured and broken".
    """
    if MODE == "off":
        return False, "BTC_PHOLD_CALIBRATION_MODE=off (raw P(hold) in use)", None
    art = _load()
    if not art:
        return (False, "no calibrator artifact - run phold_challenger to produce one",
                CALIBRATOR_MISSING)
    entry = (art.get("horizons") or {}).get(str(horizon))
    if not entry:
        return False, f"no calibrator fitted for the {horizon}m horizon", CALIBRATOR_MISSING
    if not entry.get("params"):
        return (False, f"{horizon}m entry records no parameters - cannot rebuild",
                CALIBRATOR_TAMPERED)

    # A calibrator maps the score distribution of ONE specific model bundle. If that
    # bundle is replaced, the mapping is meaningless even though the file still parses.
    if not entry.get("deployable", False):
        return (False, f"{horizon}m calibrator is NOT deployable: "
                       f"{entry.get('not_deployable_reason', 'unspecified')}",
                CALIBRATOR_NOT_DEPLOYABLE)
    if not entry.get("beats_raw_on_all_three"):
        return (False, f"{horizon}m challenger did NOT beat raw on all three metrics",
                CALIBRATOR_INSUFFICIENT_EVIDENCE)

    src = entry.get("source_identity") or {}
    cur = _current_source_identity()
    for k, v in cur.items():
        if v is None:
            continue
        if src.get(k) != v:
            return (False, f"{horizon}m calibrator was fitted against {k}="
                           f"{src.get(k)!r}, serving has {v!r}",
                    CALIBRATOR_SOURCE_MODEL_MISMATCH)

    age_note = ""
    if art.get("_age_s", 0) > MAX_AGE_S:
        age_note = f"  [warning: artifact {art['_age_s'] / 86400:.0f}d old]"
    return (True, f"{horizon}m {entry['params'].get('kind')} active "
                  f"(ece {entry.get('raw_ece', 0):.4f} -> {entry.get('cal_ece', 0):.4f})"
                  f"{age_note}", None)


def _current_source_identity() -> dict:
    """The identity a calibrator must have been fitted against to remain valid."""
    ident = {}
    try:
        from features import FEATURE_SEMANTICS_VERSION
        ident["feature_semantics_version"] = FEATURE_SEMANTICS_VERSION
    except Exception:
        ident["feature_semantics_version"] = None
    try:
        from model import TRAINING_SEMANTICS_VERSION
        ident["training_semantics_version"] = TRAINING_SEMANTICS_VERSION
    except Exception:
        ident["training_semantics_version"] = None
    return ident


def may_price_from_calibration(horizon: int = 5) -> tuple[bool, str]:
    """Head-permission input. In `required` mode an invalid calibrator REVOKES pricing.

    This is the fail-closed half. Once a head is declared calibration-required it must
    never price from raw, because raw is the estimator the calibration exists to correct.
    """
    active, reason, code = calibration_state(horizon)
    if MODE != "required":
        return True, f"calibration not required for pricing (mode={MODE})"
    if active:
        return True, reason
    return False, f"may_price REVOKED: {code or 'CALIBRATOR_MISSING'} - {reason}"


def calibrate(p_raw: float, horizon: int = 5) -> tuple[float | None, bool, str]:
    """(probability, was_calibrated, reason).

    mode=off/optional : returns RAW unchanged when calibration is unavailable, so existing
                        behaviour is preserved exactly.
    mode=required     : returns None. There is NO probability rather than a known-bad one.
                        Callers must treat None as "do not price", not as zero.
    """
    try:
        active, reason, code = calibration_state(horizon)
        if not active:
            if MODE == "required":
                return None, False, f"NO_CALIBRATED_PROBABILITY ({code}): {reason}"
            return float(p_raw), False, reason
        art = _load() or {}
        entry = (art.get("horizons") or {}).get(str(horizon)) or {}
        out = _apply_params(float(p_raw), entry["params"])
        if not (out == out) or out in (float("inf"), float("-inf")):   # NaN / Inf guard
            if MODE == "required":
                return None, False, f"NO_CALIBRATED_PROBABILITY ({CALIBRATOR_TAMPERED})"
            return float(p_raw), False, "calibrator produced a non-finite value, using raw"
        return out, True, reason
    except Exception as exc:                            # never take serving down
        if MODE == "required":
            return None, False, f"NO_CALIBRATED_PROBABILITY ({type(exc).__name__})"
        return float(p_raw), False, f"calibration error, using raw ({type(exc).__name__})"


def _deployability() -> tuple[bool, str]:
    """Can a calibrator fitted right now be deployed at all?

    No, while the source models themselves are unserviceable. Calibrating a model you
    already know must be replaced produces a mapping that will be wrong the moment the
    replacement lands.
    """
    try:
        from check_feature_contract import ARTIFACTS, MODELS, verdict_for
        bad = [n for n in ARTIFACTS
               if verdict_for(os.path.join(MODELS, n))[0] is not None]
        if bad:
            return False, (f"SOURCE_MODEL_REQUIRES_RETRAINING - {len(bad)}/{len(ARTIFACTS)} "
                           f"source artifacts fail identity enforcement")
    except Exception as exc:
        return False, f"SOURCE_MODEL_UNVERIFIABLE ({type(exc).__name__})"
    return True, ""


def export_from_report(report: dict, out_path: str = ARTIFACT) -> dict:
    """Turn a phold_challenger report into the DEPLOYABLE artifact.

    Only groups that beat raw on all three metrics are exported, and only the `overall`
    group - a per-time-bucket calibrator would need its own forward evidence.
    """
    out = {"protocol": report.get("protocol"), "source_db": report.get("db"),
           "generated_utc": report.get("generated_utc"), "horizons": {}}
    for hz, groups in (report.get("horizons") or {}).items():
        g = (groups or {}).get("overall")
        if not g or not g.get("beats_raw_on_all_three"):
            continue
        ch = g.get("challenger") or {}
        params = ch.get("params_full") or ch.get("params")
        if not params or not params.get("kind"):
            continue
        # A calibrator maps the score distribution of ONE model bundle. Every current
        # artifact is stale under the feature/training contracts, so the calibrators
        # fitted from their scores CANNOT be deployed - after the retrain the raw
        # distribution changes and this mapping becomes wrong, not merely stale.
        # Research-valid, deployment-invalid. Exported so it is inspectable, flagged so
        # it cannot be switched on.
        deployable, why = _deployability()
        out["horizons"][str(hz)] = {
            "params": params,
            "deployable": deployable,
            "not_deployable_reason": None if deployable else why,
            "source_identity": _current_source_identity(),
            "beats_raw_on_all_three": True,
            "n": g.get("n"),
            "raw_ece": (g.get("raw") or {}).get("ece"),
            "cal_ece": ch.get("ece"),
            "raw_logloss": (g.get("raw") or {}).get("log_loss"),
            "cal_logloss": ch.get("log_loss"),
            "raw_brier": (g.get("raw") or {}).get("brier"),
            "cal_brier": ch.get("brier"),
            "raw_mean_pred": (g.get("raw") or {}).get("mean_pred"),
            "realized": (g.get("raw") or {}).get("realized"),
            "calibrator_hash": g.get("calibrator_hash"),
        }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    return out


def selftest() -> int:
    import tempfile
    global ARTIFACT, MODE, _CACHE
    ok = True

    def chk(c, m):
        nonlocal ok
        print(f"  {'PASS' if c else 'FAIL'}  {m}")
        ok = ok and bool(c)

    print("phold-calibrator selftest")
    tmp = tempfile.mkdtemp()
    ARTIFACT = os.path.join(tmp, "cal.json")

    # default OFF
    MODE = "off"
    _CACHE = {"ts": 0.0, "val": None}
    p, was, why = calibrate(0.955, 5)
    chk(p == 0.955 and not was and "off" in why,
        "default OFF returns RAW unchanged, with the reason stated")

    # flag on but no artifact -> still raw, and says why
    MODE = "optional"
    _CACHE = {"ts": 0.0, "val": None}
    p, was, why = calibrate(0.955, 5)
    chk(p == 0.955 and not was and "no calibrator artifact" in why,
        "flag ON but no artifact -> RAW, never a silent pass")

    # a LOSING calibrator cannot be promoted by the flag
    with open(ARTIFACT, "w", encoding="utf-8") as fh:
        json.dump({"horizons": {"5": {"params": {"kind": "logistic", "a": 0.5, "b": 0.0},
                                      "deployable": True,
                                      "source_identity": _current_source_identity(),
                                      "beats_raw_on_all_three": False}}}, fh)
    _CACHE = {"ts": 0.0, "val": None}
    p, was, why = calibrate(0.955, 5)
    chk(p == 0.955 and not was and "did NOT beat raw" in why,
        "a calibrator that lost cannot be switched on by the operator flag")

    # a WINNING isotonic calibrator applies, and pulls overconfidence down
    with open(ARTIFACT, "w", encoding="utf-8") as fh:
        json.dump({"horizons": {"5": {
            "params": {"kind": "isotonic", "x": [0.5, 0.9, 0.96, 1.0],
                       "y": [0.5, 0.82, 0.875, 0.9]},
            "beats_raw_on_all_three": True, "deployable": True,
            "source_identity": _current_source_identity(),
            "raw_ece": 0.0883, "cal_ece": 0.0136}}}, fh)
    _CACHE = {"ts": 0.0, "val": None}
    p, was, why = calibrate(0.96, 5)
    chk(was and abs(p - 0.875) < 1e-9,
        f"a winning isotonic calibrator APPLIES at a knot ({p:.4f})")
    chk(p < 0.96, "and it LOWERS an overconfident probability (95.5% -> ~87%)")
    mid, _, _ = calibrate(0.93, 5)
    chk(0.82 < mid < 0.875, f"between knots it interpolates monotonically ({mid:.4f})")
    lo, _, _ = calibrate(0.1, 5)
    hi, _, _ = calibrate(1.0, 5)
    chk(lo == 0.5 and hi == 0.9, "outside the fitted range it CLIPS, never extrapolates")
    chk(all(calibrate(v, 5)[0] <= calibrate(v + 0.01, 5)[0] + 1e-12
            for v in [0.5, 0.6, 0.7, 0.8, 0.9]),
        "the mapping stays monotone (a higher raw p never calibrates lower)")

    # logistic path
    with open(ARTIFACT, "w", encoding="utf-8") as fh:
        json.dump({"horizons": {"15": {"params": {"kind": "logistic", "a": 1.0, "b": 0.0},
                                       "beats_raw_on_all_three": True, "deployable": True,
                                       "source_identity": _current_source_identity()}}}, fh)
    _CACHE = {"ts": 0.0, "val": None}
    p, was, _ = calibrate(0.8, 15)
    chk(was and abs(p - 0.8) < 1e-9, "identity logistic (a=1,b=0) is a no-op, as it must be")
    p2, _, _ = calibrate(0.8, 5)
    chk(p2 == 0.8, "a horizon with no fitted calibrator falls back to RAW")

    # stale artifact
    old = time.time() - (MAX_AGE_S + 3600)
    os.utime(ARTIFACT, (old, old))
    _CACHE = {"ts": 0.0, "val": None}
    p, was, why = calibrate(0.8, 15)
    chk(was, "wall-clock age alone is a WARNING, not a refusal (identity is the gate)")

    # corrupt artifact must not raise
    with open(ARTIFACT, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    _CACHE = {"ts": 0.0, "val": None}
    p, was, why = calibrate(0.77, 5)
    chk(p == 0.77 and not was, "a corrupt artifact fails OPEN to raw, without raising")

    # ---------------------------------------------- REQUIRED mode: never fall back to raw
    print("  -- required mode: raw is NOT an acceptable fallback --")
    MODE = "required"
    with open(ARTIFACT, "w", encoding="utf-8") as fh:
        json.dump({"horizons": {}}, fh)
    _CACHE = {"ts": 0.0, "val": None}
    p, was, why = calibrate(0.955, 5)
    chk(p is None and not was and "NO_CALIBRATED_PROBABILITY" in why,
        "required + missing calibrator -> NO probability, NOT the known-bad raw value")
    okp, whyp = may_price_from_calibration(5)
    chk(not okp and "REVOKED" in whyp, "and may_price is REVOKED, not merely warned")

    # A non-deployable calibrator (source models pending retrain) must also fail closed.
    with open(ARTIFACT, "w", encoding="utf-8") as fh:
        json.dump({"horizons": {"5": {
            "params": {"kind": "logistic", "a": 1.0, "b": 0.0},
            "beats_raw_on_all_three": True, "deployable": False,
            "not_deployable_reason": "SOURCE_MODEL_REQUIRES_RETRAINING",
            "source_identity": _current_source_identity()}}}, fh)
    _CACHE = {"ts": 0.0, "val": None}
    p, was, why = calibrate(0.955, 5)
    chk(p is None and CALIBRATOR_NOT_DEPLOYABLE in why,
        "a calibrator fitted on models pending retrain is refused (NOT_DEPLOYABLE)")

    # Identity binding: a calibrator fitted under different semantics is invalid even
    # though the file is well-formed and the metrics look good.
    bad_ident = dict(_current_source_identity())
    bad_ident["feature_semantics_version"] = 1
    with open(ARTIFACT, "w", encoding="utf-8") as fh:
        json.dump({"horizons": {"5": {
            "params": {"kind": "logistic", "a": 1.0, "b": 0.0},
            "beats_raw_on_all_three": True, "deployable": True,
            "source_identity": bad_ident}}}, fh)
    _CACHE = {"ts": 0.0, "val": None}
    p, was, why = calibrate(0.955, 5)
    chk(p is None and CALIBRATOR_SOURCE_MODEL_MISMATCH in why,
        "a calibrator bound to OLD feature semantics is refused (SOURCE_MODEL_MISMATCH)")

    # And the happy path still works in required mode.
    with open(ARTIFACT, "w", encoding="utf-8") as fh:
        json.dump({"horizons": {"5": {
            "params": {"kind": "logistic", "a": 1.0, "b": 0.0},
            "beats_raw_on_all_three": True, "deployable": True,
            "source_identity": _current_source_identity()}}}, fh)
    _CACHE = {"ts": 0.0, "val": None}
    p, was, _ = calibrate(0.8, 5)
    chk(was and p is not None and abs(p - 0.8) < 1e-9,
        "a fully bound, deployable calibrator DOES price in required mode")
    okp, _ = may_price_from_calibration(5)
    chk(okp, "and may_price is granted only in that case")

    # The real artifact on disk must currently be non-deployable.
    MODE = "required"
    ARTIFACT = os.path.join(DATA, "research", "phold_challenger", "phold_calibrators.json")
    _CACHE = {"ts": 0.0, "val": None}
    if os.path.exists(ARTIFACT):
        _, _, code = calibration_state(5)
        chk(code == CALIBRATOR_NOT_DEPLOYABLE,
            "the REAL artifact is currently NOT_DEPLOYABLE (source models pending retrain)")

    print("phold-calibrator:", "ALL PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    print(f"artifact : {ARTIFACT}")
    print(f"flag     : BTC_APPLY_PHOLD_CALIBRATION={'1' if ENABLED else '0'}")
    for hz in (5, 15):
        active, reason, _ = calibration_state(hz)
        print(f"  {hz:>2}m  {'ACTIVE' if active else 'inactive'}  {reason}")

    art = _load()
    if art and (art.get("horizons") or {}).get("5"):
        prm = art["horizons"]["5"]["params"]
        print("\nIMPACT IF ENABLED (5m) - downstream thresholds were set against RAW:")
        for raw in (0.90, 0.93, 0.955, 0.99):
            cal = _apply_params(raw, prm)
            note = "  <- PHOLD_STRONG gate" if abs(raw - 0.93) < 1e-9 else ""
            print(f"  raw {raw:.3f} -> {cal:.3f}  ({(cal - raw) * 100:+.1f}pp){note}")
        top = _apply_params(0.999, prm)
        print(f"  calibrated P(hold) saturates near {top:.3f}, so PHOLD_STRONG=0.93 would")
        print("  never fire. Re-derive thresholds from calibrated probabilities BEFORE")
        print("  enabling; do not lower them until signals reappear.")
    raise SystemExit(0)
