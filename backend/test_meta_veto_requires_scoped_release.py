"""A meta-model that documents its own pooling may not veto the current generation.

WHAT WAS WRONG
    train_champion_meta records, honestly:

        release_scoped   = False
        release_pooling  = UNMITIGATED_NO_IDENTITY_COLUMNS

    because champion_snapshots carries no per-row model identity, so its training rows mix
    every head generation that has ever run while its live inputs come from the current
    stack. decision_champion loaded it anyway and let it turn PAPER_BET / SETUP / LEAN /
    WATCH_UP / WATCH_DOWN into WAIT below a 0.55 gate. An artifact whose own manifest says
    its evidence is incompatible with the running system was still authorized over it.

    Separately, that 0.55 gate reads predict_proba as a literal hold probability. The
    trainer validates AUC and a grouped AUC lower bound - both statements about RANKING.
    Neither establishes that 0.55 means 55%, and class_weight="balanced" actively distorts
    the probability scale.

    Both facts must now be asserted by the trainer (release_scoped and
    probability_validated) before the model may veto anything. Until then it loads for
    diagnostics and changes no decision.

    python backend/test_meta_veto_requires_scoped_release.py
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, f"FAILED: {text}"
    CHECKS += 1
    print(f"  PASS  {text}")


def main() -> int:
    import decision_champion as dc

    def _reset():
        dc._META_MODEL = None
        dc._META_CHECKED = False
        dc._META_ERROR = ""

    real_load = dc._verified_load
    real_identity = dc.artifact_matches_current_training
    try:
        dc.artifact_matches_current_training = lambda p: (True, [])

        # The bundle train_champion_meta actually writes today.
        dc._verified_load = lambda p: {
            "model": object(), "release_scoped": False,
            "release_pooling": "UNMITIGATED_NO_IDENTITY_COLUMNS",
        }
        _reset()
        # _load_meta_model only reaches the manifest checks when the file exists; if this
        # checkout has no artifact the loader returns None for a different reason, which
        # would make the assertions below pass for the wrong cause.
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(dc.__file__)))
        data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(root, "data")
        path = os.path.join(data_dir, "saved_models", "champion_meta_model.pkl")
        if not os.path.exists(path):
            print(f"  SKIP  no champion_meta_model.pkl at {path}; the refusal path needs a "
                  f"real file to reach")
            print("\nMETA VETO REQUIRES SCOPED RELEASE: SKIPPED (no artifact)")
            return 0

        check(dc._load_meta_model() is None,
              "a bundle declaring release_scoped=False is REFUSED - its own manifest says it "
              "pools model generations, so it cannot judge the current one")
        check("release_scoped" in dc._META_ERROR,
              f"and the refusal names the reason ({dc._META_ERROR[:56]}...) rather than "
              f"failing silently into 'no meta model'")

        # Release-scoped but with no probability validation: still refused, because the live
        # gate compares against 0.55 as a probability.
        dc._verified_load = lambda p: {"model": object(), "release_scoped": True}
        _reset()
        check(dc._load_meta_model() is None and "probability_validated" in dc._META_ERROR,
              "release scoping ALONE is not enough - the 0.55 gate reads predict_proba as a "
              "calibrated probability, which AUC does not establish")

        # Both declared: the model is allowed through. Without this the test would also pass
        # against a champion that had simply deleted the meta-model entirely.
        dc._verified_load = lambda p: {
            "model": object(), "release_scoped": True, "probability_validated": True,
        }
        _reset()
        check(dc._load_meta_model() is not None,
              "a bundle declaring BOTH release scoping and probability validation loads - the "
              "gate withholds authority pending evidence, it does not delete the feature")
    finally:
        dc._verified_load = real_load
        dc.artifact_matches_current_training = real_identity
        _reset()

    print(f"\nMETA VETO REQUIRES SCOPED RELEASE: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
