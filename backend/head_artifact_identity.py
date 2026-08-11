"""Which artifact is SERVING a given head - answered from the file on disk, or None.

WHY THIS EXISTS
    `head_permissions.may_price("p_hold")` keyed authority on a head's NAME. Names survive
    retraining; evidence does not. So:

        p_hold artifact A -> 5,000 live outcomes -> USABLE
        retrain
        p_hold artifact B -> zero live outcomes  -> may_price("p_hold") still True

    because the health report was under its 14-day freshness bar. Freshness of the FILE is
    not freshness of the EVIDENCE. Binding authority to an artifact sha makes a retrain
    revoke permission automatically: the new artifact simply is not the one that was measured.

    `phold_calibrator` recorded this gap in prose - "exact binding needs fields that do not
    exist yet (no artifact carries a manifest)". Artifacts now carry manifests, so the sha is
    computable and the binding is enforceable.

WHAT None MEANS
    Not "allow". `head_permissions` treats a health entry with no artifact_sha as
    UNBOUND_EVIDENCE and denies it. A measurement that cannot name what it measured must not
    certify whatever happens to be loaded later.

    python backend/head_artifact_identity.py            # what is serving, and its sha
    python backend/head_artifact_identity.py --selftest
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_registry  # noqa: E402

BACKEND = Path(__file__).resolve().parent
REPO = BACKEND.parent
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DATA = os.environ.get("BTC_DATA_DIR") or str(REPO / "data")
MODELS = Path(os.environ.get("BTC_MODEL_OUTPUT_DIR") or (Path(DATA) / "saved_models"))

#: Health/champion head name -> the registry TARGET that names its artifact.
#:
#: DERIVED FROM model_registry, never hand-maintained. The first version of this file carried
#: its own filename dictionary and six of nine entries were wrong - `bigmove_keeper.pkl` where
#: the trained artifact is `bigmove_keeper_model.pkl`, `phold_calibrator.pkl` where it is
#: `persistence_model.pkl`. Every one of those heads resolved to no sha at all, so the
#: authority gate denied them permanently: fail-closed, and completely useless, while looking
#: like it worked. A second hand-maintained mapping of the same facts is how that happens.
#:
#: Only names that DIFFER from their registry target need an entry here.
HEAD_ALIASES: dict[str, str] = {
    "flip_risk": "p_hold",          # same persistence artifact produces both outputs
    "directional": "direction",
    "path": "path_quantiles",
    "champion_meta": "champion_decision",
}


def registry_entry(head: str):
    """The registry row that owns this head's artifact, or None."""
    target = HEAD_ALIASES.get(head, head)
    for entry in model_registry.REGISTRY:
        if entry.target == target:
            return entry
    return model_registry.lookup(head)


def head_artifacts() -> dict[str, str]:
    """head -> canonical filename, resolved through the registry."""
    out = {}
    for head in list(HEAD_ALIASES) + [e.target for e in model_registry.REGISTRY]:
        entry = registry_entry(head)
        if entry is not None:
            out[head] = entry.filename
    return out


def registry_authority(head: str) -> dict[str, bool]:
    """The STATIC ceiling on what this head may ever do.

    Live health must never grant more than this. The registry says persistence/P(Hold) may
    RANK but may not PRICE - live P(Hold) is overconfident - while head-health hands
    may_price to anything it classifies USABLE. Two sources of authority disagreeing means
    the more permissive one wins by accident, so they are intersected at the gate.
    """
    entry = registry_entry(head)
    if entry is None:
        # Unregistered: no contract to check against, so no authority. Never a default-allow.
        return {"may_price": False, "may_rank": False, "may_size": False}
    return {"may_price": entry.may_price, "may_rank": entry.may_rank,
            "may_size": entry.may_size}


_CHUNK = 1 << 20


def artifact_path(head: str) -> Path | None:
    entry = registry_entry(head)
    return (MODELS / entry.filename) if entry is not None else None


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


#: head -> the price_to_beat module global holding its LOADED bundle.
_SERVING_GLOBALS = {
    "p_hold": "_PERSIST_MODEL", "flip_risk": "_PERSIST_MODEL",
    "big_move": "_BIGMOVE_MODEL", "big_drop": "_BIGDROP_MODEL",
    "directional": "_DIRECTIONAL_MODEL", "activity": "_ACTIVITY_MODEL",
    "path": "_PATH_FORECASTER", "signed_quantile": "_SIGNED_QMODEL",
}


def in_memory_sha(head: str) -> str | None:
    """sha of the bundle this process actually DESERIALIZED for `head`, if one is loaded.

    `_verified_load` stamps `_artifact_sha256` at load time. That is the only sha describing
    the model producing predictions right now: price_to_beat's freeze mode pins the first
    loaded artifact and refuses later file replacements, so after a swap the path and the
    served model disagree by design."""
    attr = _SERVING_GLOBALS.get(head)
    if not attr:
        return None
    try:
        import price_to_beat as ptb
    except Exception:      # noqa: BLE001
        return None
    bundle = getattr(ptb, attr, None)
    if isinstance(bundle, dict):
        sha = bundle.get("_artifact_sha256")
        return str(sha) if sha else None
    return None


def resolve_serving_sha(head: str) -> str | None:
    """sha256 of the artifact serving `head`, or None when it cannot be determined.

    IN-MEMORY FIRST. Hashing the path answers "what would be loaded next", which is a
    different question from "what produced this decision" whenever the two diverge - and
    freeze mode makes them diverge deliberately:

        serve A -> file becomes B -> freeze rejects B -> path hashes to B

    Reporting B there would attach health and authority to a model that never ran. The disk
    fallback covers processes that have loaded nothing (a health run, a CLI), where no
    in-memory model exists to contradict the file."""
    served = in_memory_sha(head)
    if served:
        return served
    path = artifact_path(head)
    if path is None or not path.exists() or not path.is_file():
        return None
    try:
        return file_sha256(path)
    except OSError:
        return None


def selftest() -> int:
    global MODELS                 # declared first: Python forbids `global` after any use
    import json
    import tempfile
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, f"FAILED: {text}"
        checks += 1
        print(f"  PASS  {text}")

    # EVERY head the champion and health actually use must resolve to a registry entry, and
    # to a file that is really there. The first version of this module carried a hand-written
    # filename dict in which six of nine entries named files that do not exist - so those
    # heads resolved to no sha, and the authority gate denied them permanently while looking
    # like it worked. This check is what makes that class of rot fail loudly.
    LIVE_HEADS = ("p_hold", "flip_risk", "big_move", "big_drop", "directional",
                  "activity", "path", "selectivity", "champion_meta")
    for head in LIVE_HEADS:
        entry = registry_entry(head)
        check(entry is not None,
              f"'{head}' resolves to a model_registry entry ({entry.filename if entry else '-'})")
    on_disk = {q.name for q in MODELS.glob("*.pkl")} if MODELS.exists() else set()
    if on_disk:
        missing = [h for h in LIVE_HEADS if registry_entry(h).filename not in on_disk]
        check(not missing,
              f"and every live head's artifact is present in {MODELS.name} (missing: "
              f"{missing or 'none'}) - a name that matches no file yields no sha at all")

    original = MODELS
    with tempfile.TemporaryDirectory() as tmp:
        MODELS = Path(tmp)
        check(resolve_serving_sha("p_hold") is None,
              "a head whose artifact does not exist resolves to None, and None DENIES rather "
              "than allowing - absence of an artifact is not evidence about one")
        check(resolve_serving_sha("not_a_head") is None,
              "an unmapped head name resolves to None rather than guessing a filename")

        art = MODELS / head_artifacts()["p_hold"]
        art.write_bytes(b"artifact-A")
        sha_a = resolve_serving_sha("p_hold")
        check(sha_a is not None and len(sha_a) == 64, "an existing artifact resolves to a sha256")
        check(resolve_serving_sha("p_hold") == sha_a, "and the sha is stable across calls")

        # THE LOADER MUST DO THE STAMPING. The injection below proves the resolver PREFERS a
        # stamp; it does not prove one is ever produced. Without this, removing the stamp from
        # _verified_load changes nothing that any test can see.
        try:
            import joblib
            import price_to_beat as _p2b
            from verified_io import write_manifest
            probe = MODELS / "stamp_probe.pkl"
            joblib.dump({"version": "probe"}, probe)
            write_manifest(str(probe))     # named, not aliased: the manifest invariant
                                           # check scans for this call by name
            loaded = _p2b._verified_load(str(probe))
            check(isinstance(loaded, dict)
                  and loaded.get("_artifact_sha256") == file_sha256(probe),
                  "_verified_load STAMPS the sha of what it deserialized onto the bundle - "
                  "that stamp is the only record of which artifact actually ran")
        except ImportError:
            pass

        # IN-MEMORY WINS OVER DISK. This is the freeze-mode case: the process is serving a
        # bundle it deserialized earlier while the file underneath has been replaced.
        import price_to_beat as _ptb
        _saved = getattr(_ptb, "_PERSIST_MODEL", None)
        try:
            _ptb._PERSIST_MODEL = {"_artifact_sha256": "d" * 64}
            check(resolve_serving_sha("p_hold") == "d" * 64,
                  "the sha of the DESERIALIZED bundle wins over the file on disk - under "
                  "freeze mode the path names a model that was refused and never ran")
            _ptb._PERSIST_MODEL = {"no_sha_here": 1}
            check(resolve_serving_sha("p_hold") == sha_a,
                  "and a loaded bundle carrying no stamp falls back to the file rather than "
                  "returning nothing, so older artifacts stay resolvable")
        finally:
            _ptb._PERSIST_MODEL = _saved

        art.write_bytes(b"artifact-B")           # a retrain
        sha_b = resolve_serving_sha("p_hold")
        check(sha_b != sha_a,
              "RETRAINING THE ARTIFACT CHANGES THE SHA - this is the whole mechanism: the new "
              "model is not the one the health report measured, so it inherits no authority")

        # End-to-end against the real gate: an entry bound to A must not authorize B.
        sys.path.insert(0, str(BACKEND))
        import head_permissions as hp
        rep_dir = Path(tmp) / "research" / "head_health"
        rep_dir.mkdir(parents=True, exist_ok=True)
        rep = rep_dir / "head_health.json"
        hp.REPORT = str(rep)

        def publish(payload):
            """Write a report AND drop the reader's cache.

            head_permissions memoises for 60s, so without this the second report of the test
            is never read and every later assertion silently scores the first one."""
            payload = dict(payload)
            payload.setdefault("evidence_last_ts_ms", int(time.time() * 1000))
            rep.write_text(json.dumps(payload), encoding="utf-8")
            hp._CACHE["val"], hp._CACHE["ts"] = None, 0.0

        usable = {"state": "USABLE", "artifact_sha": sha_a,
                  "permissions": {"may_price": True, "may_rank": True}}
        publish({"heads": {"p_hold": usable}})

        ok, why = hp.may_rank("p_hold", artifact_sha=sha_a)
        check(ok, f"the MEASURED artifact may RANK ({why[:44]}...)")
        # PRICE is not merely unmeasured here - the registry caps it. model_registry declares
        # persistence/P(Hold) may_rank and NOT may_price, because live P(Hold) is
        # overconfident. Health saying USABLE must not override that.
        ok_p, why_p = hp.may_price("p_hold", artifact_sha=sha_a)
        check(not ok_p and "registry" in why_p,
              "and may NOT price even when health says USABLE - the registry is a ceiling, so "
              "live evidence can revoke authority but never grant more than the contract")
        ok_b, why_b = hp.may_rank("p_hold", artifact_sha=sha_b)
        check(not ok_b and "ARTIFACT_MISMATCH" in why_b,
              f"the RETRAINED artifact may not rank - it starts from zero evidence ({why_b[:44]}...)")

        unbound = {"state": "USABLE", "permissions": {"may_price": True, "may_rank": True}}
        publish({"heads": {"p_hold": unbound}})
        ok_u, why_u = hp.may_rank("p_hold", artifact_sha=sha_a)
        check(not ok_u and "UNBOUND_EVIDENCE" in why_u,
              "a report naming NO artifact certifies nothing, even for a head it calls USABLE "
              "- otherwise the old unbound reports keep granting authority forever")
        ok_n, _ = hp.may_rank("p_hold")
        check(not ok_n,
              "and it is denied even when the caller asks WITHOUT a sha, so the fix cannot be "
              "sidestepped by omitting the argument")

    MODELS = original
    print(f"\nHEAD ARTIFACT IDENTITY SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    print("=" * 74)
    print(f"SERVING ARTIFACTS  ({MODELS})")
    print("=" * 74)
    for head in sorted(head_artifacts()):
        sha = resolve_serving_sha(head)
        path = artifact_path(head)
        print(f"  {head:<15}{(sha[:16] if sha else '-- not on disk --'):<20}{path.name}")
    print("\nA head with no sha is UNBOUND_EVIDENCE at the permission gate: denied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
