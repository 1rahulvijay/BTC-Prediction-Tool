"""Feature-contract readiness - do the saved models still match how features are computed?

WHY THIS EXISTS

`artifact_identity` hashes features.py into `code_hash`, so editing a feature formula does
invalidate artifacts - but only when BTC_STRICT_ARTIFACT_IDENTITY=1, and that flag is
currently 0 because no artifact carries a manifest yet. In that state a formula change is
completely silent: old models keep loading and quietly consume a feature whose numeric
meaning has changed. Train/serve skew with no error and no log line.

A hash also only says "something changed". FEATURE_SEMANTICS_VERSION says WHAT changed and
whether it can alter model inputs.

This script answers one question in plain words:

    "Were the models on disk trained under the feature semantics running right now?"

    python backend/check_feature_contract.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from features import FEATURE_SEMANTICS_CHANGELOG, FEATURE_SEMANTICS_VERSION  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
MODELS = os.path.join(DATA, "saved_models")

ARTIFACTS = [
    "persistence_model.pkl", "path_forecaster.pkl", "fade_model.pkl",
    "signed_quantile_model.pkl", "round_state_heads.pkl",
    "bigmove_keeper_model.pkl", "bigdrop_keeper_model.pkl",
    "directional_keeper_model.pkl", "activity_keeper_model.pkl",
    "selectivity_models.pkl", "champion_meta_model.pkl", "magnitude_model.pkl",
]


def artifact_semantics(path: str) -> int | None:
    """Read the semantics version a bundle was trained under, if it records one."""
    side = os.path.splitext(path)[0] + ".manifest.json"
    if os.path.exists(side):
        try:
            with open(side, encoding="utf-8") as fh:
                return json.load(fh).get("feature_semantics_version")
        except Exception:
            return None
    return None


def main() -> int:
    strict = os.environ.get("BTC_STRICT_ARTIFACT_IDENTITY", "1").lower() not in ("0", "false", "no")
    print("=" * 84)
    print("FEATURE-CONTRACT READINESS")
    print("=" * 84)
    print(f"  running FEATURE_SEMANTICS_VERSION : {FEATURE_SEMANTICS_VERSION}")
    print(f"  BTC_STRICT_ARTIFACT_IDENTITY      : {'1 (enforcing)' if strict else '0 (NOT enforcing)'}")
    print()
    print("  changelog:")
    for v in sorted(FEATURE_SEMANTICS_CHANGELOG, reverse=True):
        print(f"    v{v}: {FEATURE_SEMANTICS_CHANGELOG[v]}")
    print()

    present, stale, unknown = 0, 0, 0
    print(f"  {'artifact':<32}{'trained under':<16}status")
    print("  " + "-" * 70)
    for name in ARTIFACTS:
        p = os.path.join(MODELS, name)
        if not os.path.exists(p):
            continue
        present += 1
        got = artifact_semantics(p)
        if got is None:
            unknown += 1
            print(f"  {name:<32}{'(unrecorded)':<16}UNKNOWN - cannot prove it matches")
        elif got != FEATURE_SEMANTICS_VERSION:
            stale += 1
            print(f"  {name:<32}{'v' + str(got):<16}STALE - retrain required")
        else:
            print(f"  {name:<32}{'v' + str(got):<16}ok")

    print()
    print("VERDICT")
    if present == 0:
        print("  No artifacts on disk. Nothing can be stale; a full train is required anyway.")
        return 0
    if stale == 0 and unknown == 0:
        print("  READY - every artifact was trained under the current feature semantics.")
        return 0

    print(f"  {stale} STALE, {unknown} UNKNOWN of {present} present artifacts.")
    print()
    print("  The VWAP formula changed in v2 (cumulative -> trailing time-anchored). Any model")
    print("  trained under v1 learned from a near-constant VWAP column and is now being fed a")
    print("  materially different one. That is train/serve skew: it will not raise, it will")
    print("  just be quietly wrong.")
    print()
    if not strict:
        print("  BTC_STRICT_ARTIFACT_IDENTITY=0, so nothing will refuse to load. This report is")
        print("  the ONLY thing that will tell you. Retrain before trusting any prediction, or")
        print("  set the flag to 1 once artifacts carry manifests.")
    print()
    print("  Required: retrain a challenger bundle. This script does NOT retrain - promotion")
    print("  stays a deliberate, gated act.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
