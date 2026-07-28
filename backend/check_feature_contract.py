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

try:
    from model import TRAINING_SEMANTICS_VERSION  # noqa: E402
except Exception:
    TRAINING_SEMANTICS_VERSION = None

# Typed refusal reasons. Serving must be able to say WHICH kind of failure occurred
# rather than emitting an unexplained absence of prediction.
MODEL_UNAVAILABLE_MISSING = "MODEL_UNAVAILABLE_MISSING"
MODEL_UNAVAILABLE_UNKNOWN_IDENTITY = "MODEL_UNAVAILABLE_UNKNOWN_IDENTITY"
MODEL_UNAVAILABLE_STALE_ARTIFACT = "MODEL_UNAVAILABLE_STALE_ARTIFACT"
MODEL_UNAVAILABLE_TAMPERED = "MODEL_UNAVAILABLE_TAMPERED"

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


def _manifest(path: str) -> dict | None:
    side = os.path.splitext(path)[0] + ".manifest.json"
    if not os.path.exists(side):
        return None
    try:
        with open(side, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def artifact_semantics(path: str) -> int | None:
    m = _manifest(path)
    return m.get("feature_semantics_version") if m else None


def _sha256(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            h.update(blk)
    return h.hexdigest()


def verdict_for(path: str) -> tuple[str | None, str]:
    """(refusal_code, detail). None means the artifact is serviceable.

    This is the function serving should consult. It fails CLOSED: anything it cannot
    positively verify is refused, because an unverifiable model is exactly the case that
    silently produced predictions from stale feature semantics.
    """
    if not os.path.exists(path):
        return MODEL_UNAVAILABLE_MISSING, "artifact file absent"
    m = _manifest(path)
    if not m:
        return MODEL_UNAVAILABLE_UNKNOWN_IDENTITY, "no manifest - provenance unprovable"
    for key in ("artifact_sha256", "feature_semantics_version", "training_semantics_version",
                "feature_schema_sha256", "training_cutoff", "training_dataset_sha256",
                "code_commit"):
        if m.get(key) in (None, ""):
            return MODEL_UNAVAILABLE_UNKNOWN_IDENTITY, f"manifest missing '{key}'"
    try:
        if _sha256(path) != m["artifact_sha256"]:
            return MODEL_UNAVAILABLE_TAMPERED, "artifact bytes do not match manifest hash"
    except Exception as exc:
        return MODEL_UNAVAILABLE_TAMPERED, f"cannot hash artifact ({type(exc).__name__})"
    if m["feature_semantics_version"] != FEATURE_SEMANTICS_VERSION:
        return (MODEL_UNAVAILABLE_STALE_ARTIFACT,
                f"feature semantics v{m['feature_semantics_version']} != "
                f"current v{FEATURE_SEMANTICS_VERSION}")
    if (TRAINING_SEMANTICS_VERSION is not None
            and m["training_semantics_version"] != TRAINING_SEMANTICS_VERSION):
        return (MODEL_UNAVAILABLE_STALE_ARTIFACT,
                f"training semantics v{m['training_semantics_version']} != "
                f"current v{TRAINING_SEMANTICS_VERSION}")
    return None, "ok"


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


def enforce_serving() -> int:
    """Serving gate. Exits nonzero unless EVERY required artifact is fully provable.

    `main()` is a human report and stays informational. This mode is the one a launcher
    or CI step should call, because "reported but not blocked" is exactly how stale
    models kept serving predictions.
    """
    print("=" * 84)
    print("ARTIFACT ENFORCEMENT (serving gate)")
    print("=" * 84)
    print(f"  feature semantics  : v{FEATURE_SEMANTICS_VERSION}")
    print(f"  training semantics : v{TRAINING_SEMANTICS_VERSION}")
    print()
    counts: dict[str, int] = {}
    print(f"  {'artifact':<32}{'verdict':<36}detail")
    print("  " + "-" * 80)
    for name in ARTIFACTS:
        code, detail = verdict_for(os.path.join(MODELS, name))
        counts[code or "OK"] = counts.get(code or "OK", 0) + 1
        print(f"  {name:<32}{(code or 'OK'):<36}{detail[:30]}")

    total = sum(counts.values())
    ok = counts.get("OK", 0)
    print()
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print()
    if ok == total:
        print(f"  PASS - all {total} required artifacts prove their identity. Serving may load.")
        return 0
    print(f"  BLOCKED - only {ok}/{total} artifacts are serviceable.")
    print("  Serving must return MODEL_UNAVAILABLE_* and produce NO prediction for the rest.")
    print("  Retrain with manifests; this script never retrains or promotes.")
    return 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="human readiness report (default)")
    ap.add_argument("--enforce-serving", action="store_true",
                    help="serving gate: nonzero unless every artifact is provable")
    a = ap.parse_args()
    raise SystemExit(enforce_serving() if a.enforce_serving else main())
