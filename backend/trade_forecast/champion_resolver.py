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
        result = {
            "path": str(target),
            "bundle_hash": expected,
            "source": "champion_pointer",
            "verified": True,
            "promoted_at": payload.get("promoted_at"),
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
            digest = bundle_hash(bundle)

            res = active_model_bundle(root / "none.json")
            chk(res["source"] == "legacy_saved_models" and not res["verified"],
                "no pointer -> legacy directory, and the fallback is REPORTED")

            pointer = root / "champion.json"
            pointer.write_text(
                json.dumps({"bundle_hash": digest, "path": str(bundle)}), encoding="utf-8")
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
                json.dumps({"bundle_hash": digest, "path": str(bundle)}), encoding="utf-8")
            first = active_model_bundle(pointer)
            chk(first["verified"], "frozen serving pins the first verified bundle")
            other = root / "bundle_y"
            other.mkdir()
            (other / "m.pkl").write_bytes(b"NEW")
            pointer.write_text(
                json.dumps({"bundle_hash": bundle_hash(other), "path": str(other)}),
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
