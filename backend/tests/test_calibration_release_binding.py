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

    python backend/tests/test_calibration_release_binding.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
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
    from calibration import REFIT_SEC, PrecisionEngine

    e = PrecisionEngine()
    # THE ATTRIBUTES INFERENCE ACTUALLY READS. The first version of this test wrote
    # `e.isotonic = {5: "fitted-map"}` - an attribute PrecisionEngine does not have - and then
    # asserted that invented dict was cleared. It passed while `self.calibrators`, the map
    # `calibrated()` reads, survived every release change untouched. A test that verifies state
    # the subject does not possess is the same defect class as the code it guards.
    check(not hasattr(PrecisionEngine, "isotonic") and "isotonic" not in vars(e),
          "PrecisionEngine has no `isotonic` attribute at all - the name the old clear list "
          "and the old test agreed on existed in neither")

    e.calibrators = {5: "fitted-isotonic-map"}
    e.calib_n = {5: 4242}
    e.bins = {5: {("RANGE", "high"): (10, 6)}}
    e.global_rate = {5: 0.61}
    e._last_fit = time.time()          # a fit that just happened, so the 6h timer is armed
    e.active_bundle_id = "release_A"

    report = e.bind_release("release_B", "first_touch_triple_barrier_v1")
    check(not e.calibrators and not e.calib_n and not e.bins and not e.global_rate,
          "binding a new release CLEARS every map inference reads - INCLUDING `calibrators`, "
          "which the previous implementation never touched, so the new model served the "
          "previous model's isotonic maps")
    check(set(report["cleared"]) == set(PrecisionEngine.RELEASE_SCOPED_MAPS),
          f"and the report names exactly the release-scoped maps {report['cleared']} - the old "
          f"one reported clearing 'isotonic', which did not exist")
    check(e.active_bundle_id == "release_B", "and the bundle id is updated")
    check(report["available"] is False,
          "the calibrator reports UNAVAILABLE until refitted - previously a release with no "
          "rows yet simply kept serving the old map")
    check(e._last_fit == 0.0,
          "and `_last_fit` - the attribute refresh_if_stale actually compares against - is "
          "reset. The old code zeroed `last_fit_ts`, which is written twice and read nowhere, "
          "so the new release inherited up to six hours of the old release's refresh age")
    check(e.refresh_if_stale.__self__ is e and (time.time() - e._last_fit) >= REFIT_SEC,
          "so the very next refresh_if_stale() is due immediately rather than deferred")

    # A name in the clear list that is not a real dict must RAISE, not be skipped - silent
    # skipping is precisely how the previous version cleared nothing and reported success.
    broken = PrecisionEngine()
    try:
        broken.RELEASE_SCOPED_MAPS = tuple(PrecisionEngine.RELEASE_SCOPED_MAPS) + ("not_a_map",)
        broken.bind_release("release_C")
        raised = False
    except AttributeError as exc:
        raised = "not a dict" in str(exc)
    check(raised,
          "a release-scoped name that is not a dict raises instead of being skipped, so the "
          "list cannot silently drift away from the real state again")

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
