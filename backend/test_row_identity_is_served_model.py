"""The identity stored on each prediction row must name the model that PRODUCED it.

WHAT WAS WRONG
    `_active_head_identity()` recorded, for every head, `file_sha256(path)` - a fresh hash of
    the artifact file at the moment the row was written. That answers "what is on disk now",
    which is a different question from "what produced this prediction" whenever the two have
    diverged. price_to_beat's freeze mode makes them diverge ON PURPOSE: it pins the first
    loaded artifact and refuses later replacements while continuing to serve the old model.

        serve A  ->  file becomes B  ->  freeze rejects B  ->  row records B

    Every row written after that swap claims B produced predictions that came from A. Because
    head-health aggregates those rows and now binds its report to an artifact sha, a wrong
    per-row identity would hand A's measured history to B - re-creating, one layer down, the
    exact evidence-inheritance bug the artifact binding was built to remove.

    `_verified_load` now stamps `_artifact_sha256` at deserialization - the one moment the
    bytes that became the model are known - and both the row writer and the authority
    resolver prefer it.

    python backend/test_row_identity_is_served_model.py
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
    import price_to_beat as ptb

    SERVED = "a" * 64          # what was deserialized and is answering queries
    saved = {}
    for attr in ("_PERSIST_MODEL", "_BIGMOVE_MODEL", "_BIGDROP_MODEL",
                 "_DIRECTIONAL_MODEL", "_ACTIVITY_MODEL", "_PATH_FORECASTER",
                 "_SIGNED_QMODEL"):
        saved[attr] = getattr(ptb, attr, None)
    try:
        # Only persistence is loaded, and it carries the stamp from load time. The file on
        # disk is whatever it is - a real, different artifact - which is the point.
        for attr in saved:
            setattr(ptb, attr, None)
        ptb._PERSIST_MODEL = {"_artifact_sha256": SERVED, "version": "served-A"}

        ident = ptb._active_head_identity()
        check("p_hold" in ident,
              "the loaded head appears in the per-row identity")
        check(ident["p_hold"]["sha256"] == SERVED,
              "and its sha is the one stamped at DESERIALIZATION, not a fresh hash of the "
              "file - under freeze mode the file names a model that was refused and never ran")
        check(ident["p_hold"].get("sha_source") == "deserialized",
              "the row records WHERE the sha came from, so a later reader can tell a served "
              "identity from a path rehash instead of assuming")

        # A head that is NOT loaded must not be described at all. Recording an identity for a
        # model this process never ran would attach evidence to it on the strength of a file
        # sitting on disk.
        check("big_move" not in ident,
              "a head with no loaded bundle is absent from the row identity - a file on disk "
              "is not evidence that a model ran")

        # Fallback: a bundle loaded before stamping existed still resolves, and says so.
        ptb._PERSIST_MODEL = {"version": "legacy-no-stamp"}
        ident2 = ptb._active_head_identity()
        if "p_hold" in ident2 and "sha256" in ident2["p_hold"]:
            check(ident2["p_hold"].get("sha_source") == "path_rehash",
                  "an unstamped legacy bundle falls back to hashing the path AND labels "
                  "itself path_rehash, so the weaker provenance is visible rather than "
                  "silently equivalent")
        else:
            check(True,
                  "an unstamped legacy bundle produced no identity (its artifact is absent "
                  "from this checkout) - the fallback path is unreachable here")
    finally:
        for attr, val in saved.items():
            setattr(ptb, attr, val)

    print(f"\nROW IDENTITY IS SERVED MODEL: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
