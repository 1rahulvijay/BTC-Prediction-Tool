"""Artifact identity status - can strict mode be turned on yet?

`BTC_STRICT_ARTIFACT_IDENTITY=1` refuses any model artifact that cannot prove which data trained
it. That is the right end state. It is fatal to enable it before artifacts carry manifests: every
head is refused at load and the app serves nothing, silently, with one ERROR line per artifact.

This script answers the only question that matters operationally:

    "If I set strict=1 right now, which heads would still load?"

Run it after a full retrain. When every artifact reports OK, flip the flag in start.bat.

    python backend/verify_artifact_identity.py
    python backend/verify_artifact_identity.py --strict     # force strict regardless of env
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from artifact_identity import (  # noqa: E402
    artifact_manifest_path,
    artifact_matches_current_training,
    configured_model_training_days,
    current_training_identity,
    resolve_history_days_verbose,
    training_identity_issues,
)
from model_registry import REGISTRY  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
MODELS = os.path.join(DATA, "saved_models")

# Artifacts the serving path gates on identity (price_to_beat._identity_blocks_load) plus the
# main bundle. Anything absent is reported rather than skipped silently.
ARTIFACTS = [
    entry.filename
    for entry in REGISTRY
    if entry.required_for_serving or entry.may_price or entry.may_rank or entry.may_size
]
REQUIRED_ARTIFACTS = {
    entry.filename for entry in REGISTRY if entry.required_for_serving
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="evaluate as if BTC_STRICT_ARTIFACT_IDENTITY=1")
    ap.add_argument(
        "--training-only",
        action="store_true",
        help="validate the current matrix for a forced retrain without requiring old artifacts",
    )
    a = ap.parse_args()
    env_strict = os.environ.get("BTC_STRICT_ARTIFACT_IDENTITY", "1").lower() not in ("0", "false", "no")
    strict = True if a.strict else env_strict

    # Was labelled BTC_HISTORICAL_DAYS while reading BTC_MODEL_TRAINING_DAYS - the wrong
    # variable named in the one tool an operator opens to debug a window mismatch. It now
    # resolves the window the same way save and load do, and prints WHERE it came from.
    days, days_source = resolve_history_days_verbose()
    print("=" * 88)
    print("ARTIFACT IDENTITY STATUS")
    print(f"  training window                {days}d (source: {days_source})")
    print(f"  BTC_MODEL_TRAINING_DAYS        "
          f"{configured_model_training_days() or '(unset)'}")
    print(f"  BTC_STRICT_ARTIFACT_IDENTITY   {'1 (enforcing)' if env_strict else '0 (not enforcing)'}")
    print(f"  evaluating as                  strict={strict}")
    print("=" * 88)

    # Is the CURRENT matrix even able to satisfy a training run?
    training_ok = False
    try:
        ident = current_training_identity(requested_days=days)
        issues = training_identity_issues(ident)
        print("\nTRAINING contract (blocks a retrain from starting):")
        if issues:
            for i in issues:
                print(f"  FAIL  {i}")
            print("  -> a retrain will RuntimeError until the matrix is rebuilt to the requested window")
        else:
            print("  OK    the current matrix satisfies the training identity contract")
            training_ok = True
    except Exception as exc:
        print(f"\nTRAINING contract: could not evaluate ({type(exc).__name__}: {exc})")

    if a.training_only:
        print("\nVERDICT")
        if training_ok:
            print("  READY TO RETRAIN - current data identity satisfies the requested window.")
            return 0
        print("  RETRAIN REFUSED - rebuild the matrix/data identity before fitting models.")
        return 1

    print("\nSERVING gate (which heads would load):")
    print(f"  {'artifact':<32}{'manifest':<10}{'loads':<8} reason")
    print("  " + "-" * 84)
    loadable = missing = missing_required = no_manifest = 0
    for name in ARTIFACTS:
        p = os.path.join(MODELS, name)
        if not os.path.exists(p):
            required = name in REQUIRED_ARTIFACTS
            print(f"  {name:<32}{'-':<10}{('MISSING' if required else 'OPTIONAL'):<8}")
            missing += 1
            missing_required += int(required)
            continue
        has_man = os.path.exists(artifact_manifest_path(p))
        no_manifest += (not has_man)
        ok, reasons = artifact_matches_current_training(p, strict=strict)
        loadable += bool(ok)
        print(f"  {name:<32}{('yes' if has_man else 'NO'):<10}"
              f"{('yes' if ok else 'REFUSED'):<8} {'; '.join(reasons)[:44]}")

    total = len(ARTIFACTS) - missing
    print(f"\n  {loadable}/{total} present artifacts would load"
          f"   ({no_manifest} lack a manifest; {missing_required} required missing)")

    print("\nVERDICT")
    if missing_required == 0 and no_manifest == 0 and loadable == total:
        print("  READY - every artifact proves its training data.")
        print("  Set BTC_STRICT_ARTIFACT_IDENTITY=1 in start.bat; the gate is now meaningful.")
        return 0
    if strict and loadable == 0:
        print("  DO NOT ENABLE STRICT MODE. Zero heads would load; the app would serve blind")
        print("  while logging one ERROR per artifact. Keep BTC_STRICT_ARTIFACT_IDENTITY=0")
        print("  until a full retrain has written manifests.")
        return 1
    print("  PARTIAL - some artifacts prove their identity and some do not.")
    print("  Enabling strict mode now would silently disable the ones that cannot.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
