"""The only module permitted to serialize or deserialize a model artifact.

STATUS: FOUNDATION_ONLY.
    This provides the mechanism. It does NOT migrate any production save or load path, and it
    does not enforce anything at serving time - those are separate later commits. Nothing here
    changes current behaviour.

THE ORDER MATTERS, AND ONE STEP IS NON-NEGOTIABLE
    Verification happens BEFORE deserialization. `joblib.load()` on an unknown pickle executes
    arbitrary code during unpickling, so "load it, then check whether it was valid" has already
    lost. Every load here hashes the bytes and checks the manifest first, and only then
    deserializes.

PUBLISH IS ATOMIC, AND CANNOT BE HALF-DONE
    A bundle is assembled in a uniquely named staging directory, each member fsynced and hashed,
    a manifest written, a MANIFEST_SHA256 written, the whole thing read back and verified, and
    only then published by ONE directory rename. A crash at any earlier point leaves staging
    behind - never a partially visible bundle. Serving bundles are never overwritten in place.

    absent          -> nothing was published
    present         -> every member, the manifest and its checksum are inside and verified

    python backend/model_artifacts.py --selftest
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_registry import (  # noqa: E402
    MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE,
    MODEL_UNAVAILABLE_MISSING,
    MODEL_UNAVAILABLE_MIXED_BUNDLE,
    MODEL_UNAVAILABLE_TAMPERED,
    MODEL_UNAVAILABLE_UNAUTHORIZED,
    MODEL_UNAVAILABLE_UNKNOWN_IDENTITY,
    MODEL_UNAVAILABLE_WRONG_TARGET,
    REFUSAL_CODES,
    require,
)

MANIFEST_NAME = "MANIFEST.json"
CHECKSUM_NAME = "MANIFEST_SHA256"
POINTER_NAME = "champion.json"
BUNDLE_FORMAT_VERSION = 2

# A bundle without these cannot be placed in time, tied to data, or reproduced, so it is not
# serviceable no matter how well its bytes verify. provenance={} is refused, not accepted.
REQUIRED_PROVENANCE = (
    "feature_schema_hash", "feature_semantics_version", "training_semantics_version",
    "dataset_sha256", "training_cutoff", "target_contract_hash", "protocol_hash",
    "source_commit",
)
CHUNK = 1 << 20


class ArtifactRefusal(RuntimeError):
    """A typed refusal. `code` is one of model_registry's MODEL_UNAVAILABLE_* constants."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def artifact_content_sha256(entries: list[dict[str, Any]]) -> str:
    """Hash of the serialized MEMBERS alone. Answers "are these the same bytes?"."""
    payload = json.dumps(
        sorted(({"path": e["path"], "sha256": e["sha256"]} for e in entries),
               key=lambda e: e["path"]),
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def bundle_identity_sha256(name: str, target: str, entries: list[dict[str, Any]],
                           provenance: dict[str, Any]) -> str:
    """Identity of the model RELEASE: bytes plus the contract they were produced under.

    MODEL BYTES AND MODEL IDENTITY ARE NOT THE SAME THING.
        Addressing a bundle by member hashes alone meant two models trained on different
        datasets, to different cutoffs, from different commits resolved to the SAME directory.
        The second publish found the path occupied, returned the FIRST bundle's manifest, and
        silently discarded its own provenance - so the caller believed it had published a
        release that does not exist on disk. Demonstrated before this change: dataset AAA and
        dataset ZZZ both landed on 67ef425ee1ef006b, and the ZZZ manifest read AAA.

        Identical bytes under identical contracts are genuinely the same release and still
        deduplicate. Identical bytes under DIFFERENT contracts are two releases.

    Only volatile publication metadata (created_at) is excluded."""
    canonical = {key: provenance.get(key) for key in REQUIRED_PROVENANCE}
    payload = json.dumps(
        {"registry_name": name, "target": target,
         "artifact_content_sha256": artifact_content_sha256(entries),
         "provenance": canonical},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_member_path(path: str) -> None:
    """A manifest is untrusted input. A member name is a FILENAME, never a path.

    Without this an entry of "../../.ssh/authorized_keys" or "C:/Windows/System32/x" would be
    resolved relative to the bundle and written or read outside it."""
    if not path or path != path.strip():
        raise ArtifactRefusal(MODEL_UNAVAILABLE_TAMPERED, f"member name is blank or padded: {path!r}")
    if "/" in path or chr(92) in path or path in (".", ".."):
        raise ArtifactRefusal(MODEL_UNAVAILABLE_TAMPERED,
                              f"member name must be a bare filename, got {path!r}")
    if Path(path).is_absolute() or (len(path) > 1 and path[1] == ":"):
        raise ArtifactRefusal(MODEL_UNAVAILABLE_TAMPERED, f"absolute member path: {path!r}")


def _fsync_dir(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _write_verified(path: Path, payload: bytes) -> None:
    """Write, flush, fsync, then read back. A short write must never survive to publication."""
    with open(path, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    if path.read_bytes() != payload:
        raise ArtifactRefusal(MODEL_UNAVAILABLE_TAMPERED,
                              f"{path.name} did not verify on read-back")


def publish_bundle(
    root: Path,
    name: str,
    members: dict[str, bytes],
    *,
    target: str,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble and atomically publish an immutable bundle. Returns its manifest.

    `members` maps filename -> raw bytes. Callers serialize; this module owns durability and
    identity. Bytes rather than objects is deliberate: it keeps pickling at the caller and keeps
    this module free of any implicit deserialization."""
    entry = require(name)
    if entry.target != target:
        raise ArtifactRefusal(
            MODEL_UNAVAILABLE_WRONG_TARGET,
            f"'{name}' is registered for target '{entry.target}', bundle claims '{target}'")
    if entry.filename not in members:
        raise ArtifactRefusal(
            MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE,
            f"bundle for '{name}' lacks its registered artifact '{entry.filename}'")

    missing = [k for k in REQUIRED_PROVENANCE if not (provenance or {}).get(k)]
    if missing:
        raise ArtifactRefusal(
            MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE,
            f"provenance is missing {missing}. A bundle that cannot be placed in time, tied "
            f"to a dataset or traced to a commit is not serviceable, however well its bytes "
            f"verify")
    for filename in members:
        validate_member_path(filename)

    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".staging-{name}-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    staging.mkdir(parents=True)
    try:
        entries = []
        for filename in sorted(members):
            member_path = staging / filename
            _write_verified(member_path, members[filename])
            entries.append({
                "path": filename,
                "size": member_path.stat().st_size,
                "sha256": file_sha256(member_path),
            })
        manifest = {
            "bundle_format_version": BUNDLE_FORMAT_VERSION,
            "registry_name": entry.name,
            "artifact": entry.filename,
            "target": entry.target,
            "owner": entry.owner,
            "authority": {"may_price": entry.may_price, "may_rank": entry.may_rank,
                          "may_size": entry.may_size},
            "entries": entries,
            "created_at": time.time(),
            "provenance": provenance or {},
        }
        # The bundle ADDRESS is derived from content alone - identity plus member hashes.
        # Hashing the whole manifest would fold `created_at` into the address, so two
        # byte-identical models would publish to two different directories and content
        # addressing would silently stop being content addressing.
        manifest["artifact_content_sha256"] = artifact_content_sha256(entries)
        manifest["bundle_identity_sha256"] = bundle_identity_sha256(
            entry.name, entry.target, entries, provenance or {})
        manifest_bytes = canonical_manifest_bytes(manifest)
        _write_verified(staging / MANIFEST_NAME, manifest_bytes)
        checksum = hashlib.sha256(manifest_bytes).hexdigest()
        _write_verified(staging / CHECKSUM_NAME, (checksum + "\n").encode("utf-8"))
        _fsync_dir(staging)

        # Full verification of the staged bundle before it becomes visible.
        verify_bundle(staging, expect_name=name)

        published = root / f"bundle_{manifest['bundle_identity_sha256']}"
        if published.exists():
            # Identical content already published; staging is redundant.
            shutil.rmtree(staging, ignore_errors=True)
            return read_manifest(published)
        try:
            os.rename(str(staging), str(published))
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise ArtifactRefusal(MODEL_UNAVAILABLE_TAMPERED,
                                  f"atomic publish failed: {exc}") from None
        _fsync_dir(root)
        return read_manifest(published)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def read_manifest(bundle: Path) -> dict[str, Any]:
    manifest_path = bundle / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ArtifactRefusal(MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE,
                              f"{bundle} has no {MANIFEST_NAME}")
    try:
        return json.loads(manifest_path.read_bytes().decode("utf-8"))
    except Exception as exc:
        raise ArtifactRefusal(MODEL_UNAVAILABLE_TAMPERED,
                              f"unreadable manifest in {bundle}: {exc}") from None


def verify_bundle(bundle: Path, *, expect_name: str | None = None) -> dict[str, Any]:
    """Verify a bundle completely. Raises ArtifactRefusal with a typed code. NO deserialization."""
    if not bundle.is_dir():
        raise ArtifactRefusal(MODEL_UNAVAILABLE_MISSING, f"no bundle at {bundle}")
    checksum_path = bundle / CHECKSUM_NAME
    if not checksum_path.is_file():
        raise ArtifactRefusal(MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE,
                              f"{bundle} has no {CHECKSUM_NAME}")
    manifest_bytes = (bundle / MANIFEST_NAME).read_bytes() if (
        bundle / MANIFEST_NAME).is_file() else None
    if manifest_bytes is None:
        raise ArtifactRefusal(MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE,
                              f"{bundle} has no {MANIFEST_NAME}")
    recorded = checksum_path.read_text(encoding="utf-8").strip()
    if hashlib.sha256(manifest_bytes).hexdigest() != recorded:
        raise ArtifactRefusal(MODEL_UNAVAILABLE_TAMPERED,
                              f"manifest checksum mismatch in {bundle}")
    manifest = json.loads(manifest_bytes.decode("utf-8"))

    if manifest.get("bundle_format_version") != BUNDLE_FORMAT_VERSION:
        raise ArtifactRefusal(
            MODEL_UNAVAILABLE_UNKNOWN_IDENTITY,
            f"bundle format {manifest.get('bundle_format_version')} != {BUNDLE_FORMAT_VERSION}")
    entry = require(manifest.get("registry_name", ""))
    if expect_name is not None and manifest.get("registry_name") != require(expect_name).name:
        raise ArtifactRefusal(
            MODEL_UNAVAILABLE_MIXED_BUNDLE,
            f"bundle declares '{manifest.get('registry_name')}', expected '{expect_name}'")
    if manifest.get("target") != entry.target:
        raise ArtifactRefusal(
            MODEL_UNAVAILABLE_WRONG_TARGET,
            f"bundle target '{manifest.get('target')}' != registered '{entry.target}'")

    declared = {item["path"]: item for item in manifest.get("entries", [])}
    if entry.filename not in declared:
        raise ArtifactRefusal(MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE,
                              f"manifest does not declare '{entry.filename}'")
    present = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() and path.name not in (MANIFEST_NAME, CHECKSUM_NAME)
    }
    missing = sorted(set(declared) - present)
    if missing:
        raise ArtifactRefusal(MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE,
                              f"declared members absent: {missing}")
    # An UNDECLARED file is a mixed bundle: something else was published alongside this model.
    stray = sorted(present - set(declared))
    if stray:
        raise ArtifactRefusal(MODEL_UNAVAILABLE_MIXED_BUNDLE,
                              f"undeclared files present: {stray}")
    for name, item in declared.items():
        path = bundle / name
        if path.stat().st_size != item["size"] or file_sha256(path) != item["sha256"]:
            raise ArtifactRefusal(MODEL_UNAVAILABLE_TAMPERED, f"member '{name}' does not match")
    for item in manifest.get("entries", []):
        validate_member_path(str(item.get("path", "")))
    recorded_content = manifest.get("artifact_content_sha256")
    if recorded_content and artifact_content_sha256(
            manifest.get("entries", [])) != recorded_content:
        raise ArtifactRefusal(MODEL_UNAVAILABLE_TAMPERED,
                              "artifact content hash disagrees with the declared members")
    recorded_identity = manifest.get("bundle_identity_sha256")
    if recorded_identity:
        actual = bundle_identity_sha256(entry.name, entry.target,
                                        manifest.get("entries", []),
                                        manifest.get("provenance") or {})
        if actual != recorded_identity:
            raise ArtifactRefusal(
                MODEL_UNAVAILABLE_TAMPERED,
                "bundle identity disagrees with its members or provenance - the recorded "
                "release does not match what the manifest describes")
    absent = [k for k in REQUIRED_PROVENANCE if not (manifest.get("provenance") or {}).get(k)]
    if absent:
        raise ArtifactRefusal(MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE,
                              f"bundle provenance is missing {absent}")
    # AUTHORITY COMES FROM THE REGISTRY, never from the manifest. A bundle that grants
    # itself pricing rights must not be believed just because its bytes verify.
    declared_authority = manifest.get("authority") or {}
    expected_authority = {"may_price": entry.may_price, "may_rank": entry.may_rank,
                          "may_size": entry.may_size}
    if declared_authority != expected_authority:
        raise ArtifactRefusal(
            MODEL_UNAVAILABLE_UNAUTHORIZED,
            f"manifest authority {declared_authority} disagrees with the registry "
            f"{expected_authority}")
    return manifest


def load_verified(bundle: Path, name: str, loader: Any) -> Any:
    """Verify FIRST, then hand the verified path to `loader`.

    `loader` is supplied by the caller (joblib.load, torch.load, ...) so this module never
    imports a deserializer and can never be tempted to run one before verification."""
    manifest = verify_bundle(bundle, expect_name=name)
    entry = require(name)
    return loader(bundle / entry.filename), manifest


def publish_champion(root: Path, bundle: Path, *, name: str) -> Path:
    """Atomically repoint the champion. Verifies the bundle before pointing at it."""
    verify_bundle(bundle, expect_name=name)
    pointer = root / POINTER_NAME
    current = {}
    if pointer.is_file():
        try:
            current = json.loads(pointer.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    payload = json.dumps({
        "registry_name": name,
        "bundle": bundle.name,
        "path": str(bundle),
        "manifest_sha256": (bundle / CHECKSUM_NAME).read_text(encoding="utf-8").strip(),
        "promoted_at": time.time(),
        "previous_bundle": current.get("bundle"),
    }, indent=2, sort_keys=True).encode("utf-8")
    staging = pointer.with_name(f".{POINTER_NAME}.{uuid.uuid4().hex[:8]}")
    _write_verified(staging, payload)
    staging.replace(pointer)          # atomic on the same filesystem
    _fsync_dir(root)
    return pointer


def _demo_provenance(**overrides) -> dict[str, Any]:
    """A complete provenance block, so tests exercise the serviceable path by default."""
    base = {
        "feature_schema_hash": "a" * 64, "feature_semantics_version": "v3",
        "training_semantics_version": "v2", "dataset_sha256": "d" * 64,
        "training_cutoff": "2026-07-01T00:00:00Z", "target_contract_hash": "c" * 64,
        "protocol_hash": "p" * 64, "source_commit": "133a903",
    }
    base.update(overrides)
    return base


def selftest() -> int:
    ok = True

    def chk(cond: object, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok = ok and bool(cond)

    def refuses(code: str, fn: Any, msg: str) -> None:
        try:
            fn()
            chk(False, msg + " (no refusal raised)")
        except ArtifactRefusal as exc:
            chk(exc.code == code, f"{msg} -> {exc.code}")

    name, target = "persistence", "p_hold"
    members = {"persistence_model.pkl": b"MODEL-BYTES", "extra_weights.npy": b"WEIGHTS"}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        print("publish is atomic and verified")
        manifest = publish_bundle(root, name, members, target=target,
                                  provenance=_demo_provenance())
        published = [p for p in root.iterdir() if p.name.startswith("bundle_")]
        chk(len(published) == 1, "exactly one bundle published")
        bundle = published[0]
        chk(not [p for p in root.iterdir() if p.name.startswith(".staging")],
            "no staging directory survives a successful publish")
        chk((bundle / MANIFEST_NAME).is_file() and (bundle / CHECKSUM_NAME).is_file(),
            "manifest and checksum are inside the published bundle")
        chk(manifest["target"] == target, "manifest records the registered target")
        chk(verify_bundle(bundle, expect_name=name)["registry_name"] == name,
            "a freshly published bundle verifies")

        print("verification precedes deserialization")
        calls: list[Path] = []

        def loader(path: Path) -> bytes:
            calls.append(path)
            return path.read_bytes()

        payload, _ = load_verified(bundle, name, loader)
        chk(payload == b"MODEL-BYTES" and len(calls) == 1,
            "load_verified verified, then called the loader exactly once")
        tampered = bundle / "persistence_model.pkl"
        tampered.write_bytes(b"EVIL")
        before = len(calls)
        refuses(MODEL_UNAVAILABLE_TAMPERED,
                lambda: load_verified(bundle, name, loader),
                "a tampered member is refused")
        chk(len(calls) == before,
            "and the loader was NEVER called - no unpickling of unverified bytes")
        tampered.write_bytes(b"MODEL-BYTES")

        print("typed refusals")
        refuses(MODEL_UNAVAILABLE_MISSING,
                lambda: verify_bundle(root / "bundle_nope", expect_name=name),
                "an absent bundle")
        incomplete = root / "incomplete"
        incomplete.mkdir()
        refuses(MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE,
                lambda: verify_bundle(incomplete, expect_name=name),
                "a bundle with no checksum")
        refuses(MODEL_UNAVAILABLE_WRONG_TARGET,
                lambda: publish_bundle(root, name, members, target="something_else"),
                "a bundle claiming the wrong target")
        refuses(MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE,
                lambda: publish_bundle(root, name, {"unrelated.bin": b"x"}, target=target, provenance=_demo_provenance()),
                "a bundle missing its registered artifact")
        try:
            publish_bundle(root, "not_registered", members, target=target, provenance=_demo_provenance())
            chk(False, "an unregistered model must be refused")
        except KeyError as exc:
            chk(MODEL_UNAVAILABLE_UNKNOWN_IDENTITY in str(exc),
                "an unregistered model -> UNKNOWN_IDENTITY")

        stray = bundle / "smuggled.bin"
        stray.write_bytes(b"x")
        refuses(MODEL_UNAVAILABLE_MIXED_BUNDLE,
                lambda: verify_bundle(bundle, expect_name=name),
                "an undeclared file inside a bundle")
        stray.unlink()
        refuses(MODEL_UNAVAILABLE_MIXED_BUNDLE,
                lambda: verify_bundle(bundle, expect_name="magnitude"),
                "a bundle asked for under another model's name")

        checksum = bundle / CHECKSUM_NAME
        original = checksum.read_text(encoding="utf-8")
        checksum.write_text("0" * 64 + "\n", encoding="utf-8")
        refuses(MODEL_UNAVAILABLE_TAMPERED,
                lambda: verify_bundle(bundle, expect_name=name),
                "an edited manifest checksum")
        checksum.write_text(original, encoding="utf-8")

        print("crash and concurrency")
        orphan = root / f".staging-{name}-1-deadbeef"
        orphan.mkdir()
        (orphan / "half").write_bytes(b"x")
        again = publish_bundle(root, name, members, target=target,
                               provenance=_demo_provenance())
        chk(again["registry_name"] == name,
            "an orphaned staging dir from a crash does not block a later publish")
        chk(len([p for p in root.iterdir() if p.name.startswith("bundle_")]) == 1,
            "identical content republishes to the SAME content-addressed bundle")
        different = publish_bundle(root, name, {**members, "extra_weights.npy": b"CHANGED"},
                                   target=target, provenance=_demo_provenance())
        chk(different["entries"] != manifest["entries"],
            "changed content publishes a distinct bundle")

    
        print("identity: bytes are not a release")
        identical = {"persistence_model.pkl": b"IDENTICAL MODEL BYTES"}
        old_release = publish_bundle(root, "persistence", identical, target="p_hold",
                                     provenance=_demo_provenance(dataset_sha256="A" * 64,
                                                                 training_cutoff="2024-01-01T00:00:00Z",
                                                                 source_commit="aaaaaaa"))
        new_release = publish_bundle(root, "persistence", identical, target="p_hold",
                                     provenance=_demo_provenance(dataset_sha256="Z" * 64,
                                                                 training_cutoff="2026-07-28T00:00:00Z",
                                                                 source_commit="zzzzzzz"))
        chk(old_release["artifact_content_sha256"] == new_release["artifact_content_sha256"],
            "identical BYTES share one artifact_content_sha256")
        chk(old_release["bundle_identity_sha256"] != new_release["bundle_identity_sha256"],
            "but a different dataset/cutoff/commit is a DIFFERENT RELEASE with its own identity")
        chk(new_release["provenance"]["dataset_sha256"] == "Z" * 64,
            "and the second publish keeps ITS OWN provenance, not the first one's")

        repeat = publish_bundle(root, "persistence", identical, target="p_hold",
                                provenance=_demo_provenance(dataset_sha256="Z" * 64,
                                                            training_cutoff="2026-07-28T00:00:00Z",
                                                            source_commit="zzzzzzz"))
        chk(repeat["bundle_identity_sha256"] == new_release["bundle_identity_sha256"],
            "the SAME bytes under the SAME contract still deduplicate to one bundle")

        print("provenance is mandatory, not decorative")
        try:
            publish_bundle(root, "fade", {"fade_model.pkl": b"x"}, target="fade_roundtrip",
                           provenance={})
            chk(False, "provenance={} must be refused")
        except ArtifactRefusal as exc:
            chk(exc.code == MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE,
                "provenance={} is refused as an INCOMPLETE bundle, not silently serviced")

        print("a manifest is untrusted input")
        for hostile in ("../../escape.pkl", "/etc/passwd", "C:/Windows/x.pkl",
                        "sub" + chr(92) + "dir.pkl", " padded.pkl", ""):
            try:
                validate_member_path(hostile)
                chk(False, f"member path {hostile!r} must be refused")
            except ArtifactRefusal:
                chk(True, f"member path {hostile!r} is refused")

        malformed = root / "bundle_malformed"
        malformed.mkdir(parents=True, exist_ok=True)
        (malformed / MANIFEST_NAME).write_bytes(b"{not json at all")
        try:
            read_manifest(malformed)
            chk(False, "malformed JSON must raise a typed refusal")
        except ArtifactRefusal as exc:
            chk(exc.code in REFUSAL_CODES, f"malformed JSON -> typed refusal ({exc.code})")
        except Exception as exc:                                       # noqa: BLE001
            chk(False, f"malformed JSON raised an UNTYPED {type(exc).__name__}")

        print("authority comes from the registry, never the manifest")
        forged = root / f"bundle_{old_release['bundle_identity_sha256']}"
        forged_manifest = json.loads((forged / MANIFEST_NAME).read_bytes())
        forged_manifest["authority"] = {"may_price": True, "may_rank": True, "may_size": True}
        tampered_dir = root / "bundle_forged_authority"
        shutil.copytree(forged, tampered_dir, dirs_exist_ok=True)
        payload = canonical_manifest_bytes(forged_manifest)
        (tampered_dir / MANIFEST_NAME).write_bytes(payload)
        (tampered_dir / CHECKSUM_NAME).write_bytes(
            (hashlib.sha256(payload).hexdigest() + chr(10)).encode("utf-8"))
        try:
            verify_bundle(tampered_dir, expect_name="persistence")
            chk(False, "a bundle granting itself pricing rights must be refused")
        except ArtifactRefusal as exc:
            chk(exc.code == MODEL_UNAVAILABLE_UNAUTHORIZED,
                f"self-granted authority -> MODEL_UNAVAILABLE_UNAUTHORIZED ({exc.code})")

        print("REAL concurrency, not sequential republishing")
        import threading

        barrier = threading.Barrier(6)
        outcomes: list = []
        errors: list = []

        def race(index: int) -> None:
            try:
                barrier.wait(timeout=10)
                # Three publishers of IDENTICAL content, three with DIFFERENT provenance.
                prov = (_demo_provenance(dataset_sha256="R" * 64)
                        if index % 2 == 0
                        else _demo_provenance(dataset_sha256=f"{index:064d}"))
                outcomes.append(publish_bundle(
                    root, "magnitude", {"magnitude_model.pkl": b"RACE BYTES"},
                    target="move_magnitude", provenance=prov)["bundle_identity_sha256"])
            except Exception as exc:                                   # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=race, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        chk(not errors, f"six simultaneous publishers all succeeded ({errors[:2]})")
        chk(len(outcomes) == 6, f"every publisher returned a manifest ({len(outcomes)}/6)")
        chk(len(set(outcomes)) == 4,
            f"identical-content publishers collapsed to ONE identity, differing ones did not "
            f"({len(set(outcomes))} distinct identities from 6 publishers)")

        concurrent_readers: list = []
        target_bundle = root / f"bundle_{new_release['bundle_identity_sha256']}"
        stop_publishing = threading.Event()

        def publish_forever() -> None:
            index = 100
            while not stop_publishing.is_set():
                index += 1
                try:
                    publish_bundle(root, "magnitude", {"magnitude_model.pkl": b"RACE BYTES"},
                                   target="move_magnitude",
                                   provenance=_demo_provenance(dataset_sha256=f"{index:064d}"))
                except Exception:                                  # noqa: BLE001
                    pass

        def reader() -> None:
            try:
                for _ in range(40):
                    verify_bundle(target_bundle, expect_name="persistence")
                concurrent_readers.append(True)
            except Exception as exc:                               # noqa: BLE001
                concurrent_readers.append(f"{type(exc).__name__}: {exc}")

        publisher = threading.Thread(target=publish_forever, daemon=True)
        publisher.start()
        readers = [threading.Thread(target=reader) for _ in range(3)]
        for t in readers:
            t.start()
        for t in readers:
            t.join(timeout=30)
        stop_publishing.set()
        publisher.join(timeout=10)
        chk(all(r is True for r in concurrent_readers),
            f"readers verified cleanly DURING continuous publication ({concurrent_readers})")

        print("champion pointer")
        pointer = publish_champion(root, bundle, name=name)
        chk(pointer.is_file(), "pointer written")
        first = json.loads(pointer.read_text(encoding="utf-8"))
        chk(first["previous_bundle"] is None, "first promotion records no predecessor")
        # Filter by DECLARED IDENTITY. Taking the first bundle_* directory silently picked a
        # magnitude bundle once other models were published into the same root.
        second_bundle = next(
            p for p in sorted(root.iterdir())
            if p.name.startswith("bundle_") and p != bundle
            and (p / MANIFEST_NAME).is_file()
            and json.loads((p / MANIFEST_NAME).read_bytes()).get("registry_name") == name)
        publish_champion(root, second_bundle, name=name)
        second = json.loads(pointer.read_text(encoding="utf-8"))
        chk(second["previous_bundle"] == bundle.name,
            "a later promotion records its predecessor, so rollback has a target")
        broken = root / "bundle_broken"
        broken.mkdir()
        refuses(MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE,
                lambda: publish_champion(root, broken, name=name),
                "the champion cannot point at an unverifiable bundle")

    print("\nSTATUS: FOUNDATION_ONLY - no production save or load path uses this yet.")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
