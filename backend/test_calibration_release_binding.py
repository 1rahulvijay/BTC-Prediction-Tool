"""P0-14 (part): a release change must CLEAR calibration, and an unlabelled map must refuse.

THE DEFECT
    PrecisionEngine defines correct as `raw_direction='UP' AND actual_move > 0` - ENDPOINT
    sign - while the ensemble trains on FIRST_TOUCH_TRIPLE_BARRIER_V1. First-touch confidence
    was calibrated by a different rule than the model was trained on, and the module had zero
    references to target_contract.

    Switching active_bundle_id alone left the previous release's maps serving: the refresh
    could wait hours, and a release with no rows yet left the old map in place entirely.

WHAT IS AND IS NOT FIXED HERE
    FIXED   bind_release() clears every map and reports unavailable until refitted.
    FIXED   is_admissible_for() refuses while provenance is UNRECORDED - "we do not know"
            must not read as "yes".
    OPEN    the actual contract filter. `predictions_{h}m` has NO target_contract column, so
            no query can separate first-touch rows from endpoint rows. Recording that column
            is the real fix; until then the map declares what it is instead of implying it
            answers the training contract.

    python backend/test_calibration_release_binding.py
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def main() -> int:
    from calibration import PrecisionEngine

    e = PrecisionEngine()
    e.global_rate = {5: 0.61}
    e.bins = {5: [1, 2, 3]}
    e.isotonic = {5: "fitted-map"}
    e.active_bundle_id = "release_A"

    report = e.bind_release("release_B", "first_touch_triple_barrier_v1")
    check(not e.global_rate and not e.bins and not e.isotonic,
          "binding a new release CLEARS every calibration map - the old release's map is not "
          "a prior for a different model, it is a stale opinion wearing the new model's name")
    check(e.active_bundle_id == "release_B", "and the bundle id is updated")
    check(report["available"] is False,
          "the calibrator reports UNAVAILABLE until refitted - previously a release with no "
          "rows yet simply kept serving the old map")
    check(sum(report["cleared"].values()) == 3,
          f"and reports what it discarded {report['cleared']} rather than clearing silently")
    check(getattr(e, "last_fit_ts", None) == 0.0,
          "the fit timestamp is reset, so a six-hour refresh timer cannot treat the cleared "
          "state as freshly fitted")

    # ---- an unlabelled map must refuse ------------------------------------------------
    check(e.contract_provenance == "UNRECORDED",
          "provenance is UNRECORDED, because predictions_{h}m has no target_contract column - "
          "the module states this rather than implying the map answers the training contract")
    check(e.is_admissible_for("first_touch_triple_barrier_v1") is False,
          "so it is INADMISSIBLE even for the contract it was just bound to - 'we do not "
          "know' must not read as 'yes', which is the contract layer's rule applied to "
          "calibration")

    e.contract_provenance = "RECORDED"
    check(e.is_admissible_for("first_touch_triple_barrier_v1") is True,
          "once provenance is RECORDED and the contract matches, it is admissible")
    check(e.is_admissible_for("endpoint_settlement_v1") is False,
          "and a DIFFERENT contract is refused - the map is bound to one question")

    # ---- the defect is documented where a reader will hit it ---------------------------
    src = (BACKEND / "calibration.py").read_text(encoding="utf-8")
    check("target_contract" in src,
          "calibration.py now mentions the target contract at all - it previously had zero "
          "references while grading by endpoint sign")
    check("actual_move > 0" in src and "ENDPOINT" in src,
          "and the endpoint-sign definition is labelled as such at the point of use, so the "
          "next reader does not have to rediscover that it disagrees with training")

    print(f"\nCALIBRATION RELEASE BINDING: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
