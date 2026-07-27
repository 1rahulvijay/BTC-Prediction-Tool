"""Single resolver for the active model bundle. Every loader must go through this.

`promote_challenger.py` writes immutable content-hashed bundles and atomically swaps
`champion.json`. That pointer is INERT unless serving actually reads it - a pointer swap no loader
consults changes nothing, which is worse than having no pointer, because it looks like a
deployment happened.

This module is the one place that resolves the pointer, and it VERIFIES the bundle it points at
before returning a path. Under `BTC_FREEZE_MODEL` the pointer is read once and pinned: a promotion
during an evidence run must not take effect until a controlled restart, or the run silently spans
two different champions and its ledger describes neither.

    python backend/trade_forecast/champion_resolver.py --selftest
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

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
POINTER = DATA / "champion.json"
LEGACY = DATA / "saved_models"

_PINNED: dict[str, Any] = {}


def evidence_mode() -> bool:
    """BTC_EVIDENCE_MODE=1 forbids every unverified path.

    During an evidence run the legacy `saved_models/` fallback is PROHIBITED: serving artifacts
    with no proven bundle identity would make the whole collection unattributable. The app stays
    online and shows market data; the models simply report unavailable and actions become
    NO_DATA."""
    return str(os.environ.get("BTC_EVIDENCE_MODE") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def resolve_artifact(artifact_name: str, legacy_path: Path) -> tuple[Path | None, dict]:
    """Resolve ONE artifact through the champion bundle.

    Returns (path_or_None, status). A None path means NO MODEL - the caller must serve nothing
    rather than fall back to unverified bytes. Outside evidence mode the legacy path is returned
    with `verified: False` so migration is possible, but the status always says which happened."""
    active = active_model_bundle()
    if active.get("verified"):
        candidate = Path(active["path"]) / artifact_name
        if candidate.is_file():
            return candidate, {**active, "artifact": artifact_name}
        status = {**active, "verified": False,
                  "note": f"{artifact_name} missing from bundle {active['path']}"}
    else:
        status = dict(active)

    if evidence_mode():
        # Fail closed: no verified bundle means no model, full stop.
        return None, {**status, "evidence_mode": True,
                      "note": f"EVIDENCE MODE: refusing unverified artifact ({status.get('note')})"}
    return legacy_path, {**status, "evidence_mode": False,
                         "note": f"migration fallback to legacy path ({status.get('note')})"}


def _freeze_enabled() -> bool:
    return str(os.environ.get("BTC_FREEZE_MODEL") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def bundle_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_bundle_manifest(
    directory: Path,
    expected_sha256: str,
) -> tuple[bool, str]:
    """Verify the promoted file inventory, including absence of undeclared files."""
    path = directory / "bundle_manifest.json"
    if not path.is_file():
        return False, "bundle_manifest.json missing"
    actual_manifest_hash = _file_hash(path)
    if not expected_sha256 or actual_manifest_hash != expected_sha256:
        return False, (
            "bundle manifest hash mismatch: "
            f"expected {expected_sha256[:16]}, found {actual_manifest_hash[:16]}"
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return False, f"bundle manifest unreadable: {type(exc).__name__}"
    if manifest.get("manifest_version") != 1:
        return False, "unsupported bundle manifest version"
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return False, "bundle manifest entries missing"
    declared: dict[str, dict] = {}
    for entry in entries:
        relative = str((entry or {}).get("path") or "")
        if (
            not relative
            or relative in declared
            or relative == "bundle_manifest.json"
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            return False, f"invalid bundle manifest path: {relative!r}"
        declared[relative] = entry
    actual = {
        item.relative_to(directory).as_posix()
        for item in directory.rglob("*")
        if item.is_file() and item != path
    }
    if actual != set(declared):
        missing = sorted(set(declared) - actual)
        extra = sorted(actual - set(declared))
        return False, f"bundle inventory mismatch missing={missing} extra={extra}"
    for relative, entry in declared.items():
        item = directory / relative
        if item.stat().st_size != int(entry.get("size") or -1):
            return False, f"bundle file size mismatch: {relative}"
        if _file_hash(item) != str(entry.get("sha256") or ""):
            return False, f"bundle file hash mismatch: {relative}"
    return True, ""


def _legacy(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "path": str(LEGACY),
        "bundle_hash": extra.pop("bundle_hash", None),
        "source": "legacy_saved_models",
        "verified": False,
        "note": reason,
        **extra,
    }


def active_model_bundle(pointer: Path | None = None, *, verify: bool = True) -> dict[str, Any]:
    """Resolve the active bundle directory, verifying its content hash.

    Falls back to the legacy `saved_models/` directory when no pointer exists, so this can be
    adopted incrementally rather than needing a flag day - but the fallback is always REPORTED in
    `source`/`note`, never silent, so nobody can mistake it for a verified promotion."""
    path = Path(pointer) if pointer else POINTER
    if _freeze_enabled() and _PINNED.get("path"):
        return {**_PINNED, "source": "pinned"}

    if not path.is_file():
        result = _legacy("no champion.json; serving the legacy directory")
    else:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:                                # noqa: BLE001
            return _legacy(f"unreadable champion.json: {type(exc).__name__}")
        target = Path(str(payload.get("path") or ""))
        expected = str(payload.get("bundle_hash") or "")
        expected_manifest = str(payload.get("bundle_manifest_sha256") or "")
        if not target.is_dir():
            return _legacy(f"champion.json points at a missing bundle: {target}",
                           bundle_hash=expected)
        if verify:
            actual = bundle_hash(target)
            if actual != expected:
                # Refuse rather than serve content that is not what was promoted.
                return _legacy(
                    f"bundle hash mismatch: expected {expected[:16]}, found {actual[:16]}",
                    bundle_hash=expected,
                )
            manifest_ok, manifest_reason = verify_bundle_manifest(
                target, expected_manifest
            )
            if not manifest_ok:
                return _legacy(
                    f"bundle manifest rejected: {manifest_reason}",
                    bundle_hash=expected,
                    bundle_manifest_sha256=expected_manifest,
                )
        result = {
            "path": str(target),
            "bundle_hash": expected,
            "source": "champion_pointer",
            "verified": True,
            "promoted_at": payload.get("promoted_at"),
            "bundle_manifest_sha256": expected_manifest,
        }

    if _freeze_enabled() and not _PINNED.get("path"):
        _PINNED.update(result)
        print(
            f"[champion] FROZEN: pinned {result['source']} "
            f"{(result.get('bundle_hash') or 'legacy')[:16]} until restart",
            flush=True,
        )
    return result


def selftest() -> int:
    import tempfile

    ok = True

    def chk(cond: bool, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok &= bool(cond)

    previous = os.environ.get("BTC_FREEZE_MODEL")
    try:
        os.environ["BTC_FREEZE_MODEL"] = "0"
        _PINNED.clear()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "bundle_x"
            bundle.mkdir()
            (bundle / "m.pkl").write_bytes(b"weights")
            manifest = {
                "manifest_version": 1,
                "entries": [{
                    "path": "m.pkl",
                    "size": (bundle / "m.pkl").stat().st_size,
                    "sha256": _file_hash(bundle / "m.pkl"),
                }],
            }
            manifest_path = bundle / "bundle_manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True), encoding="utf-8"
            )
            manifest_hash = _file_hash(manifest_path)
            digest = bundle_hash(bundle)

            res = active_model_bundle(root / "none.json")
            chk(res["source"] == "legacy_saved_models" and not res["verified"],
                "no pointer -> legacy directory, and the fallback is REPORTED")

            pointer = root / "champion.json"
            pointer.write_text(
                json.dumps({
                    "bundle_hash": digest,
                    "bundle_manifest_sha256": manifest_hash,
                    "path": str(bundle),
                    "promoted_at": 123.0,
                }),
                encoding="utf-8")
            res = active_model_bundle(pointer)
            chk(res["source"] == "champion_pointer" and res["verified"],
                "a valid pointer resolves to the immutable bundle")
            chk(Path(res["path"]) == bundle, "the resolved path IS the bundle directory")

            (bundle / "m.pkl").write_bytes(b"TAMPERED")
            res = active_model_bundle(pointer)
            chk(not res["verified"] and "mismatch" in (res.get("note") or ""),
                "a tampered bundle is REFUSED, not served")
            (bundle / "m.pkl").write_bytes(b"weights")

            pointer.write_text(
                json.dumps({"bundle_hash": digest, "path": str(root / "gone")}),
                encoding="utf-8")
            res = active_model_bundle(pointer)
            chk(not res["verified"] and "missing bundle" in (res.get("note") or ""),
                "a pointer to a missing bundle is refused")

            pointer.write_text("{ broken", encoding="utf-8")
            res = active_model_bundle(pointer)
            chk(not res["verified"], "unreadable champion.json is refused, never a crash")

            # Freeze: pointer read once; a later swap must not take effect mid-run.
            _PINNED.clear()
            os.environ["BTC_FREEZE_MODEL"] = "1"
            pointer.write_text(
                json.dumps({
                    "bundle_hash": digest,
                    "bundle_manifest_sha256": manifest_hash,
                    "path": str(bundle),
                    "promoted_at": 123.0,
                }), encoding="utf-8")
            first = active_model_bundle(pointer)
            chk(first["verified"], "frozen serving pins the first verified bundle")
            other = root / "bundle_y"
            other.mkdir()
            (other / "m.pkl").write_bytes(b"NEW")
            other_manifest = other / "bundle_manifest.json"
            other_manifest.write_text(json.dumps({
                "manifest_version": 1,
                "entries": [{
                    "path": "m.pkl",
                    "size": (other / "m.pkl").stat().st_size,
                    "sha256": _file_hash(other / "m.pkl"),
                }],
            }), encoding="utf-8")
            pointer.write_text(
                json.dumps({
                    "bundle_hash": bundle_hash(other),
                    "bundle_manifest_sha256": _file_hash(other_manifest),
                    "path": str(other),
                    "promoted_at": 456.0,
                }),
                encoding="utf-8")
            after = active_model_bundle(pointer)
            chk(after["path"] == first["path"] and after["source"] == "pinned",
                "under freeze a mid-run promotion does NOT take effect until restart")
            _PINNED.clear()
    finally:
        if previous is None:
            os.environ.pop("BTC_FREEZE_MODEL", None)
        else:
            os.environ["BTC_FREEZE_MODEL"] = previous

    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
