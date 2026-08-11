"""How many artifacts can the SERVING path actually load? Measured, not assumed.

WHY THIS EXISTS - a gate of mine was passing vacuously
    backend/tests/test_trainers_write_manifests.py reports "0 offenders: every artifact-writing
    trainer also writes a manifest", and that sentence is true. It is also nearly useless,
    because this repository has TWO different things called a manifest:

        verified_io.write_manifest        -> NAME.pkl.integrity.json   {sha256, size,
                                                                        integrity_only: true}
        artifact_identity.write_artifact_manifest
                                          -> NAME.pkl.manifest.json    full provenance

    check_feature_contract._manifest() reads only the second, and EXPLICITLY SKIPS any file
    carrying `integrity_only: true`. So a trainer can satisfy the manifest gate in full and
    still emit an artifact that serving refuses with MODEL_UNAVAILABLE_UNKNOWN_IDENTITY.

    That is the same defect class as the non-causal join: a check that passes while the thing
    it was supposed to guarantee is false. The manifest gate answers "did the trainer call a
    manifest writer?". This one answers the question anybody actually cares about:

        can the serving path load this artifact, right now, yes or no?

WHAT verdict_for() REQUIRES - all of it, or the artifact is refused
    a NAME.pkl.manifest.json that is not integrity-only, carrying every one of
        artifact_sha256, feature_semantics_version, training_semantics_version,
        feature_schema_sha256, training_cutoff, training_dataset_sha256,
        code_commit, code_dirty, runtime_dependency_hash
    with code_dirty == False, artifact_sha256 matching the bytes on disk, and semantics
    versions equal to the current ones.

    backend/train_heads.py is the only place that writes this correctly - and it does so in the
    ORCHESTRATOR, after each per-head trainer returns. A trainer invoked directly writes the
    integrity sidecar and nothing else. That is an operational fact about how a retrain must be
    launched, not a detail: running the trainers individually produces artifacts that cannot be
    served.

THE GATE - a ratchet, so this can only improve
    Fails if serviceable artifacts drop below SERVICEABLE_FLOOR. The floor is what was measured
    when this file was written, so the number is allowed to rise and never to fall silently.
    A floor of zero is not a passing grade; it is the honest starting line, and it makes the
    claim "the overnight retrain will unblock serving" a testable one instead of a hope.

    python backend/tests/test_artifact_serviceability.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[2]
MODEL_DIR = Path(os.environ.get("BTC_DATA_DIR") or REPO / "data") / "saved_models"

# Measured 2026-08-02 against data/saved_models: 25 artifacts, 0 loadable.
# Raise this the moment a retrain makes it true. Never lower it to make CI pass.
SERVICEABLE_FLOOR = 0


def classify() -> dict:
    """One row per artifact: which manifest it has, and whether serving would accept it."""
    from check_feature_contract import verdict_for

    rows = []
    for artifact in sorted(MODEL_DIR.glob("*.pkl")):
        provenance = (Path(f"{artifact}.manifest.json").is_file()
                      or artifact.with_suffix(".manifest.json").is_file())
        integrity = Path(f"{artifact}.integrity.json").is_file()
        code, detail = verdict_for(str(artifact))
        rows.append({"name": artifact.name, "provenance": provenance,
                     "integrity_only": integrity and not provenance,
                     "serviceable": code is None, "code": code or "OK", "detail": detail})
    return {
        "artifacts": len(rows),
        "serviceable": sum(1 for r in rows if r["serviceable"]),
        "provenance_manifest": sum(1 for r in rows if r["provenance"]),
        "integrity_only": sum(1 for r in rows if r["integrity_only"]),
        "no_manifest": sum(1 for r in rows if not r["provenance"] and not r["integrity_only"]),
        "rows": rows,
    }


def selftest() -> None:
    """The classifier must be able to say YES, or its NO means nothing.

    A checker that has only ever returned "unserviceable" could be broken in a way no amount of
    passing would reveal - which is exactly how the retracted studies survived. So build a
    manifest that satisfies every requirement and confirm the verdict flips."""
    import json
    import tempfile

    from check_feature_contract import (FEATURE_SEMANTICS_VERSION,
                                        TRAINING_SEMANTICS_VERSION, verdict_for)
    from verified_io import file_sha256

    with tempfile.TemporaryDirectory() as tmp:
        artifact = Path(tmp) / "probe.pkl"
        artifact.write_bytes(b"not really a model, but it hashes")

        code, _ = verdict_for(str(artifact))
        assert code is not None, "an artifact with no manifest must be refused"

        # The integrity sidecar alone must NOT be enough - the whole point of this file.
        Path(f"{artifact}.manifest.json").write_text(
            json.dumps({"sha256": file_sha256(artifact), "size": 1, "integrity_only": True}),
            encoding="utf-8")
        code, detail = verdict_for(str(artifact))
        assert code is not None, ("an integrity-only manifest must NOT make an artifact "
                                  "serviceable - if this passes, the gate is vacuous")

        Path(f"{artifact}.manifest.json").write_text(json.dumps({
            "artifact_sha256": file_sha256(artifact),
            "feature_semantics_version": FEATURE_SEMANTICS_VERSION,
            "training_semantics_version": TRAINING_SEMANTICS_VERSION,
            "feature_schema_sha256": "a" * 64,
            "training_cutoff": "2026-08-01T00:00:00Z",
            "training_dataset_sha256": "b" * 64,
            "code_commit": "c" * 40,
            "code_dirty": False,
            "runtime_dependency_hash": "d" * 64,
        }), encoding="utf-8")
        code, detail = verdict_for(str(artifact))
        assert code is None, f"a complete provenance manifest must be accepted, got {code} {detail}"
    print("  SELFTEST PASS - the classifier can return both YES and NO")


def main() -> int:
    print("=" * 96)
    print("ARTIFACT SERVICEABILITY - can the serving path load what the trainers wrote?")
    print("=" * 96)
    selftest()
    if not MODEL_DIR.is_dir():
        print(f"  no artifact directory at {MODEL_DIR} - nothing to classify")
        return 0

    summary = classify()
    print()
    print(f"  artifacts (.pkl)                : {summary['artifacts']}")
    print(f"  with a PROVENANCE manifest      : {summary['provenance_manifest']}")
    print(f"  with ONLY an integrity manifest : {summary['integrity_only']}"
          "   <- passes the manifest gate, still refused by serving")
    print(f"  with neither                    : {summary['no_manifest']}")
    print(f"  SERVICEABLE                     : {summary['serviceable']}"
          f"  (floor {SERVICEABLE_FLOOR})")

    blocked = [r for r in summary["rows"] if not r["serviceable"]]
    if blocked:
        reasons: dict[str, int] = {}
        for row in blocked:
            reasons[f"{row['code']}: {row['detail']}"] = (
                reasons.get(f"{row['code']}: {row['detail']}", 0) + 1)
        print("\n  why each refusal happens:")
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {count:>3}  {reason}")

    if summary["serviceable"] < SERVICEABLE_FLOOR:
        print(f"\n  FAIL - serviceable artifacts fell from {SERVICEABLE_FLOOR} to "
              f"{summary['serviceable']}. Something that used to load no longer does.")
        return 1

    if summary["serviceable"] == 0:
        print("\n  0 artifacts are serviceable. Every model-backed strategy is therefore")
        print("  UNAVAILABLE, not merely unprofitable - including the frozen Polymarket")
        print("  forward benchmark, which will keep writing UNAVAILABLE ledger rows.")
        print("  A retrain fixes this ONLY if it writes provenance manifests. Launch it")
        print("  through backend/train_heads.py, which writes both sidecars; the individual")
        print("  trainers write the integrity one alone and leave the artifact unloadable.")
    print("\n  PASS - serviceability is at or above the recorded floor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
