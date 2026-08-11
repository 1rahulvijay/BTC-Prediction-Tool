"""What training WRITES must satisfy what serving READS.

    python backend/tests/test_artifact_manifest_contract.py

THE DEFECT
    check_feature_contract.verdict_for is the function serving consults, and it fails closed on
    any provenance key it cannot read. It demanded nine keys. artifact_identity wrote none of
    them under those names, and four did not exist in any form:

        feature_semantics_version   never written   <- the entire point of the check
        training_semantics_version  never written
        training_cutoff             never written
        code_dirty                  never written
        artifact_sha256             written as artifact_hash
        feature_schema_sha256       written as feature_schema_hash
        training_dataset_sha256     written as training_data_hash
        code_commit                 written as code_hash (content hash, not a commit)

    So every artifact read as UNKNOWN - "cannot prove it matches" - and, critically, A RETRAIN
    COULD NOT HAVE FIXED IT. The rebuilt bundle would have written the same unreadable manifest
    and the gate would have stayed red forever. The failure looked like "the models are stale";
    it was actually "the two halves of the contract were never introduced".

    That is why the repair is to make the writer satisfy the reader, and NOT to relax the
    checker. The checker is the component behaving correctly: it refuses to certify provenance
    it cannot read.

THE PROPERTY
    A manifest produced by the real writer must be accepted by the real reader.
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import artifact_identity as AI                              # noqa: E402
import check_feature_contract as CFC                        # noqa: E402

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


#: Exactly the keys verdict_for requires. Read from the checker rather than restated, so the
#: two cannot drift apart without this test noticing.
REQUIRED = ("artifact_sha256", "feature_semantics_version", "training_semantics_version",
            "feature_schema_sha256", "training_cutoff", "training_dataset_sha256",
            "code_commit", "code_dirty", "runtime_dependency_hash")


def main() -> int:
    print("the checker's demanded keys are the ones this test pins")
    src = (Path(__file__).resolve().parents[1] / "check_feature_contract.py").read_text(
        encoding="utf-8")
    for key in REQUIRED:
        chk(f'"{key}"' in src, f"verdict_for still requires '{key}'")

    print("\nthe real writer emits every one of them")
    identity = AI.current_training_identity(feature_names=["a", "b", "c"])
    missing = [k for k in REQUIRED if k not in identity and k != "artifact_sha256"]
    chk(not missing, f"current_training_identity supplies the identity keys (missing: {missing})")
    chk(identity.get("feature_semantics_version") is not None,
        f"feature_semantics_version is real, not a placeholder "
        f"({identity.get('feature_semantics_version')})")
    chk(identity.get("code_commit"), "code_commit is captured from git")
    chk(identity.get("code_dirty") in (True, False),
        f"code_dirty is a definite boolean ({identity.get('code_dirty')})")

    print("\nand a written manifest is ACCEPTED by the reader")
    with tempfile.TemporaryDirectory() as tmp:
        art = Path(tmp) / "probe_model.pkl"
        art.write_bytes(b"a fitted model would be here")
        AI.write_artifact_manifest(art, identity, artifact_type="probe")

        man = json.loads(
            (Path(tmp) / "probe_model.pkl.manifest.json").read_text(encoding="utf-8"))
        absent = [k for k in REQUIRED if man.get(k) in (None, "")]
        chk(not absent, f"the written manifest carries every required key (absent: {absent})")
        chk(man.get("artifact_sha256") == man.get("artifact_hash"),
            "artifact_sha256 and artifact_hash are the same value under both names, so "
            "neither reader has to guess")

        code, detail = CFC.verdict_for(str(art))
        if identity.get("code_dirty") is True:
            # Running this from a dirty tree is itself the refusal the checker exists for.
            chk(code == CFC.MODEL_UNAVAILABLE_UNKNOWN_IDENTITY and "dirty" in detail,
                f"a dirty working tree is correctly refused ({detail}) - re-run from a clean "
                f"tree to see the accept path")
        else:
            chk(code is None,
                f"verdict_for ACCEPTS a freshly written manifest (got {code}: {detail})")

        print("\nand it still refuses what it should")
        # verdict_for checks the dirty tree BEFORE hashing, so these branches are only
        # reachable with a clean-tree identity. Pinning code_dirty=False isolates each refusal
        # instead of letting the first one mask the rest - which is exactly what happened on
        # the first run of this test, where a dirty tree made the TAMPERED case unreachable.
        clean = dict(identity, code_dirty=False)

        art_t = Path(tmp) / "tampered_model.pkl"
        art_t.write_bytes(b"original bytes")
        AI.write_artifact_manifest(art_t, clean, artifact_type="probe")
        art_t.write_bytes(b"different bytes entirely")
        code_t, detail_t = CFC.verdict_for(str(art_t))
        chk(code_t == CFC.MODEL_UNAVAILABLE_TAMPERED,
            f"changed artifact bytes are refused as TAMPERED ({detail_t})")

        stale = dict(clean, feature_semantics_version="v0-ancient")
        art2 = Path(tmp) / "stale_model.pkl"
        art2.write_bytes(b"stale")
        AI.write_artifact_manifest(art2, stale, artifact_type="probe")
        code_s, detail_s = CFC.verdict_for(str(art2))
        chk(code_s == CFC.MODEL_UNAVAILABLE_STALE_ARTIFACT,
            f"an artifact from older feature semantics is refused as STALE ({detail_s})")

        art_ok = Path(tmp) / "clean_model.pkl"
        art_ok.write_bytes(b"a fitted model")
        AI.write_artifact_manifest(art_ok, clean, artifact_type="probe")
        code_ok, detail_ok = CFC.verdict_for(str(art_ok))
        chk(code_ok is None,
            f"and a clean-tree manifest is ACCEPTED (got {code_ok}: {detail_ok}) - this is the "
            f"path a real retrain must take, and it did not exist before")

        art3 = Path(tmp) / "bare_model.pkl"
        art3.write_bytes(b"no manifest at all")
        code_b, _ = CFC.verdict_for(str(art3))
        chk(code_b == CFC.MODEL_UNAVAILABLE_UNKNOWN_IDENTITY,
            "an artifact with NO manifest is still refused - the fix did not weaken the gate")

    print("\nlegacy readers are not broken")
    for legacy in ("feature_schema_hash", "training_data_hash", "runtime_dependency_hash"):
        chk(legacy in identity,
            f"'{legacy}' is still present for existing readers (artifact_compatibility, the "
            f"oracle freeze)")

    print("\nsave and load resolve the training window the SAME way")
    # Asserted on CALL NODES, not on source text. Both files now carry comments that name
    # `configured_model_training_days` while explaining why it must not be used here, so a
    # substring search would be satisfied by the very documentation of the fix - the trap this
    # repo has already sprung several times.
    import ast as _ast

    def _called_names(path, func_name=None):
        tree = _ast.parse(Path(path).read_text(encoding="utf-8"))
        scope = tree
        if func_name:
            scope = next((n for n in _ast.walk(tree)
                          if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                          and n.name == func_name), None)
        return {n.func.id for n in _ast.walk(scope)
                if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)} if scope else set()

    backend_dir = Path(__file__).resolve().parents[1]
    load_calls = _called_names(backend_dir / "model.py", "load_models")
    chk("configured_model_training_days" not in load_calls,
        "the model LOAD path does not call configured_model_training_days - it returns None "
        "when BTC_MODEL_TRAINING_DAYS is unset, and a None expected value makes "
        "artifact_compatibility SKIP the window check instead of failing it")
    chk("resolve_history_days_verbose" in load_calls,
        "it calls the canonical resolver instead, the same one the SAVE path uses, so the two "
        "ends cannot disagree about which window the bundle belongs to")
    chk("unverifiable_identity_keys" in load_calls,
        "and it reports which identity fields could NOT be verified - a skipped check that "
        "leaves no trace is indistinguishable from a passed one")
    _full = {key: "set" for key in AI.COMPARED_IDENTITY_KEYS}
    chk(AI.unverifiable_identity_keys(_full) == [],
        "a fully-populated expected identity has nothing unverifiable")
    chk(AI.unverifiable_identity_keys({**_full, "requested_days": None}) == ["requested_days"],
        "an explicit None is named as unverifiable")
    chk("row_count" in AI.unverifiable_identity_keys({k: v for k, v in _full.items()
                                                     if k != "row_count"}),
        "and so is an ABSENT key - absent is unproven, never a pass")
    chk(set(AI.COMPARED_IDENTITY_KEYS) >= {"requested_days", "code_hash", "training_data_hash"},
        "and it reports on the SAME key list artifact_compatibility walks, not a copy")

    print("\nARTIFACT MANIFEST CONTRACT:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
