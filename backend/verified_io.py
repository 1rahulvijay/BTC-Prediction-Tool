"""Verify BEFORE deserializing. The migration path from raw joblib to artifact identity.

WHY THIS EXISTS
    39 raw save calls and 53 raw load calls sit outside model_artifacts. Every one of those
    loads deserializes before any hash is checked, and `joblib.load()` on an unknown pickle
    executes arbitrary code while unpickling - so "load it, then validate it" has already lost.
    That is why BTC_STRICT_ARTIFACT_IDENTITY cannot honestly default to 1.

    Migrating all of them to full artifact bundles at once would mean plumbing complete
    provenance - dataset hash, cutoff, feature schema, commit - through every trainer in one
    change. This module is the intermediate step that buys the SECURITY property immediately
    without that rewrite:

        every dump writes a sidecar manifest with the artifact's sha256 and size
        every load hashes the file and compares BEFORE handing bytes to a deserializer

    A full bundle (model_artifacts.publish_bundle) additionally carries identity and
    provenance. This carries integrity only, and says so.

BACKWARDS COMPATIBILITY IS THE WHOLE DIFFICULTY
    Artifacts already on disk have no sidecar. Refusing them outright would brick every
    existing model, so:

        sidecar present + matches   -> load
        sidecar present + mismatch  -> REFUSE, always, in every mode
        sidecar absent              -> load in permissive mode and COUNT it
                                       REFUSE in strict mode

    The count of sidecar-less loads is therefore the exact remaining migration debt, and it is
    what makes strict mode a measurable milestone rather than a hope.

    python backend/verified_io.py --selftest
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Integrity and provenance are different contracts and must not share a filename.
# `artifact_identity` owns `<artifact>.manifest.json` (data/code/schema provenance).
# This module owns `<artifact>.integrity.json` (bytes checked before deserializing).
#
# Older integrity-only sidecars used `.manifest.json`. They remain readable when their
# payload explicitly says `integrity_only=true`, but every new write uses the unambiguous
# `.integrity.json` suffix.
INTEGRITY_SUFFIX = ".integrity.json"
LEGACY_MANIFEST_SUFFIX = ".manifest.json"
CHUNK = 1 << 20

# Counters, so the migration debt is observable at runtime rather than inferred.
STATS = {"verified": 0, "unmanifested": 0, "refused": 0}


class ArtifactIntegrityError(RuntimeError):
    """Raised when a file does not match its recorded hash, or is unmanifested in strict mode."""


def strict_mode() -> bool:
    return os.environ.get("BTC_STRICT_ARTIFACT_IDENTITY", "0").lower() not in ("0", "false", "no")


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_path(path: str | Path) -> Path:
    return Path(str(path) + INTEGRITY_SUFFIX)


def _legacy_integrity_path(path: str | Path) -> Path:
    return Path(str(path) + LEGACY_MANIFEST_SUFFIX)


def write_manifest(path: str | Path) -> dict[str, Any]:
    record = {
        "sha256": file_sha256(path),
        "size": os.path.getsize(path),
        "integrity_only": True,      # NOT a provenance bundle; see model_artifacts for that
    }
    target = manifest_path(path)
    tmp = Path(f"{target}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)
    return record


def atomic_dump(value: Any, path: str | Path) -> None:
    """Write an artifact atomically, then record its hash alongside it.

    Order matters: the artifact is put in place first, then hashed from disk. Hashing the
    in-memory object instead would record what we MEANT to write rather than what landed."""
    import joblib

    path = str(path)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        joblib.dump(value, tmp)
        os.replace(tmp, path)
        write_manifest(path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def verified_load(path: str | Path, *, loader=None) -> Any:
    """Hash-check the file, THEN deserialize. Never the other way round."""
    import joblib

    path = str(path)
    record_path = manifest_path(path)
    record_kind = "integrity"
    if not record_path.is_file():
        legacy_path = _legacy_integrity_path(path)
        if legacy_path.is_file():
            try:
                candidate = json.loads(legacy_path.read_text(encoding="utf-8"))
            except Exception:
                candidate = None
            # A full provenance manifest may use the legacy filename. Never interpret it as
            # an integrity record unless it explicitly declares the old integrity-only schema.
            if isinstance(candidate, dict) and candidate.get("integrity_only") is True:
                record_path = legacy_path
    if not record_path.is_file():
        # Full provenance manifests also bind the artifact bytes. Two conventions exist:
        # artifact_identity appends `.manifest.json`; complete-trade artifacts replace `.pkl`.
        # Either is sufficient for verify-before-deserialize when it carries a sha256.
        provenance_candidates = [
            Path(f"{path}.manifest.json"),
            Path(path).with_suffix(".manifest.json"),
        ]
        for candidate_path in provenance_candidates:
            if not candidate_path.is_file():
                continue
            try:
                candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            except Exception:
                STATS["refused"] += 1
                raise ArtifactIntegrityError(
                    f"{Path(path).name}: provenance manifest is unreadable. "
                    "Nothing has been unpickled."
                ) from None
            if isinstance(candidate, dict) and (
                candidate.get("artifact_sha256") or candidate.get("artifact_hash")
            ):
                record_path = candidate_path
                record_kind = "provenance"
                break

    if record_path.is_file():
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except Exception as exc:                                   # noqa: BLE001
            STATS["refused"] += 1
            raise ArtifactIntegrityError(
                f"{Path(path).name}: manifest is unreadable ({type(exc).__name__}). "
                f"An unreadable manifest is treated as a failed check, never as an absent one."
            ) from None

        actual_size = os.path.getsize(path)
        if record_kind == "integrity" and actual_size != record.get("size"):
            STATS["refused"] += 1
            raise ArtifactIntegrityError(
                f"{Path(path).name}: size {actual_size} != recorded {record.get('size')}")
        actual_hash = file_sha256(path)
        expected_hash = (
            record.get("sha256")
            if record_kind == "integrity"
            else record.get("artifact_sha256") or record.get("artifact_hash")
        )
        if actual_hash != expected_hash:
            STATS["refused"] += 1
            raise ArtifactIntegrityError(
                f"{Path(path).name}: sha256 does not match its manifest - refusing to "
                f"deserialize. Nothing has been unpickled.")
        STATS["verified"] += 1
    else:
        # No sidecar: a pre-migration artifact. This is the remaining debt, and it is counted.
        if strict_mode():
            STATS["refused"] += 1
            raise ArtifactIntegrityError(
                f"{Path(path).name}: no integrity manifest and "
                f"BTC_STRICT_ARTIFACT_IDENTITY is on. Re-save the artifact to record one.")
        STATS["unmanifested"] += 1

    return (loader or joblib.load)(path)


def stats() -> dict[str, Any]:
    return dict(STATS, strict=strict_mode(),
                migration_complete=STATS["unmanifested"] == 0)


def selftest() -> int:  # noqa: C901
    import tempfile

    ok = True

    def chk(cond: object, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok = ok and bool(cond)

    saved = os.environ.get("BTC_STRICT_ARTIFACT_IDENTITY")
    root = Path(tempfile.mkdtemp())
    try:
        os.environ["BTC_STRICT_ARTIFACT_IDENTITY"] = "0"

        print("a dump records an integrity manifest")
        target = root / "model.pkl"
        atomic_dump({"weights": [1, 2, 3]}, target)
        chk(target.is_file(), "artifact written")
        chk(manifest_path(target).is_file(), "manifest written alongside it")
        record = json.loads(manifest_path(target).read_text(encoding="utf-8"))
        chk(record["sha256"] == file_sha256(target), "the manifest hash matches the bytes on disk")

        print("a matching artifact loads")
        chk(verified_load(target) == {"weights": [1, 2, 3]}, "round trip returns the object")

        print("integrity and provenance sidecars coexist")
        from artifact_identity import write_artifact_manifest
        write_artifact_manifest(
            target,
            {
                "requested_days": 90,
                "training_data_hash": "data",
                "source_manifest_hash": "sources",
                "feature_schema_hash": "schema",
                "code_hash": "code",
            },
            artifact_type="selftest",
        )
        chk(manifest_path(target).is_file(), "integrity remains in .integrity.json")
        chk(
            Path(f"{target}.manifest.json").is_file(),
            "provenance uses the separate .manifest.json",
        )
        chk(
            verified_load(target) == {"weights": [1, 2, 3]},
            "a provenance sidecar cannot shadow the verified integrity record",
        )
        provenance_only = root / "provenance-only.pkl"
        atomic_dump({"verified": True}, provenance_only)
        manifest_path(provenance_only).unlink()
        write_artifact_manifest(
            provenance_only,
            {"training_data_hash": "data", "feature_schema_hash": "schema"},
            artifact_type="selftest",
            extra={"artifact_sha256": file_sha256(provenance_only)},
        )
        old_strict_for_provenance = os.environ.get("BTC_STRICT_ARTIFACT_IDENTITY")
        os.environ["BTC_STRICT_ARTIFACT_IDENTITY"] = "1"
        try:
            chk(
                verified_load(provenance_only) == {"verified": True},
                "a full provenance hash is sufficient in strict mode",
            )
        finally:
            if old_strict_for_provenance is None:
                os.environ.pop("BTC_STRICT_ARTIFACT_IDENTITY", None)
            else:
                os.environ["BTC_STRICT_ARTIFACT_IDENTITY"] = old_strict_for_provenance

        print("A TAMPERED ARTIFACT IS REFUSED WITHOUT DESERIALIZING")
        deserialized = []

        def tracking_loader(_path):
            deserialized.append(_path)
            return "SHOULD NEVER HAPPEN"

        target.write_bytes(target.read_bytes() + b"tampered")
        try:
            verified_load(target, loader=tracking_loader)
            chk(False, "a tampered artifact must be refused")
        except ArtifactIntegrityError as exc:
            chk("does not match" in str(exc) or "size" in str(exc),
                "refused with the integrity reason")
        chk(not deserialized,
            "THE LOADER WAS NEVER CALLED - nothing was unpickled from tampered bytes")

        print("an unreadable manifest is a failed check, not an absent one")
        broken = root / "broken.pkl"
        atomic_dump({"a": 1}, broken)
        manifest_path(broken).write_text("{not json", encoding="utf-8")
        try:
            verified_load(broken, loader=tracking_loader)
            chk(False, "an unreadable manifest must be refused")
        except ArtifactIntegrityError:
            chk(True, "refused rather than falling through to the permissive path")

        print("pre-migration artifacts still load, and are COUNTED")
        legacy = root / "legacy.pkl"
        atomic_dump({"old": True}, legacy)
        manifest_path(legacy).unlink()
        before = STATS["unmanifested"]
        chk(verified_load(legacy) == {"old": True}, "a sidecar-less artifact loads permissively")
        chk(STATS["unmanifested"] == before + 1, "and the migration debt is counted")

        print("strict mode refuses exactly the unmanifested ones")
        os.environ["BTC_STRICT_ARTIFACT_IDENTITY"] = "1"
        chk(strict_mode() is True, "strict mode is on")
        try:
            verified_load(legacy, loader=tracking_loader)
            chk(False, "strict mode must refuse an unmanifested artifact")
        except ArtifactIntegrityError as exc:
            chk("no integrity manifest" in str(exc), "refused, naming the missing manifest")
        good = root / "good.pkl"
        atomic_dump({"new": True}, good)
        chk(verified_load(good) == {"new": True},
            "a manifested artifact still loads under strict mode")
        chk(not deserialized, "no refusal anywhere reached a deserializer")

        print("migration progress is observable")
        chk(stats()["migration_complete"] is False,
            f"unmanifested loads remain, so migration is incomplete ({stats()['unmanifested']})")
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)
        if saved is None:
            os.environ.pop("BTC_STRICT_ARTIFACT_IDENTITY", None)
        else:
            os.environ["BTC_STRICT_ARTIFACT_IDENTITY"] = saved

    print("\nSTATUS: integrity only. Full identity and provenance live in model_artifacts.")
    print("VERIFIED IO", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
