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
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
REPO = BACKEND.parent
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DATA = os.environ.get("BTC_DATA_DIR") or str(REPO / "data")
MODELS = Path(os.environ.get("BTC_MODEL_OUTPUT_DIR") or (Path(DATA) / "saved_models"))

#: head name -> the artifact file that SERVES it. One place, so a rename cannot silently
#: leave a head bound to a file nothing loads.
HEAD_ARTIFACTS: dict[str, str] = {
    "p_hold": "phold_calibrator.pkl",
    "flip_risk": "phold_calibrator.pkl",
    "path": "path_forecaster.pkl",
    "big_move": "bigmove_keeper.pkl",
    "big_drop": "bigdrop_keeper.pkl",
    "directional": "directional_keeper.pkl",
    "activity": "activity_keeper.pkl",
    "selectivity": "selectivity_models.pkl",
    "champion_meta": "champion_meta_model.pkl",
}

_CHUNK = 1 << 20


def artifact_path(head: str) -> Path | None:
    name = HEAD_ARTIFACTS.get(head)
    return (MODELS / name) if name else None


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_serving_sha(head: str) -> str | None:
    """sha256 of the artifact serving `head`, or None when it cannot be determined.

    Content hash rather than a recorded manifest field on purpose: the question is what is
    ON DISK about to be loaded, not what some sidecar claims. A swapped file changes the
    answer even if its manifest was copied along with it."""
    path = artifact_path(head)
    if path is None or not path.exists() or not path.is_file():
        return None
    try:
        return file_sha256(path)
    except OSError:
        return None


def selftest() -> int:
    import json
    import tempfile
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, f"FAILED: {text}"
        checks += 1
        print(f"  PASS  {text}")

    global MODELS
    original = MODELS
    with tempfile.TemporaryDirectory() as tmp:
        MODELS = Path(tmp)
        check(resolve_serving_sha("p_hold") is None,
              "a head whose artifact does not exist resolves to None, and None DENIES rather "
              "than allowing - absence of an artifact is not evidence about one")
        check(resolve_serving_sha("not_a_head") is None,
              "an unmapped head name resolves to None rather than guessing a filename")

        art = MODELS / HEAD_ARTIFACTS["p_hold"]
        art.write_bytes(b"artifact-A")
        sha_a = resolve_serving_sha("p_hold")
        check(sha_a is not None and len(sha_a) == 64, "an existing artifact resolves to a sha256")
        check(resolve_serving_sha("p_hold") == sha_a, "and the sha is stable across calls")

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
            rep.write_text(json.dumps(payload), encoding="utf-8")
            hp._CACHE["val"], hp._CACHE["ts"] = None, 0.0

        usable = {"state": "USABLE", "artifact_sha": sha_a,
                  "permissions": {"may_price": True, "may_rank": True}}
        publish({"heads": {"p_hold": usable}})

        ok, why = hp.may_price("p_hold", artifact_sha=sha_a)
        check(ok, f"the MEASURED artifact may price ({why[:44]}...)")
        ok_b, why_b = hp.may_price("p_hold", artifact_sha=sha_b)
        check(not ok_b and "ARTIFACT_MISMATCH" in why_b,
              f"the RETRAINED artifact may not - it starts from zero evidence ({why_b[:52]}...)")

        unbound = {"state": "USABLE", "permissions": {"may_price": True, "may_rank": True}}
        publish({"heads": {"p_hold": unbound}})
        ok_u, why_u = hp.may_price("p_hold", artifact_sha=sha_a)
        check(not ok_u and "UNBOUND_EVIDENCE" in why_u,
              "a report naming NO artifact certifies nothing, even for a head it calls USABLE "
              "- otherwise the old unbound reports keep granting authority forever")
        ok_n, _ = hp.may_price("p_hold")
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
    for head in sorted(HEAD_ARTIFACTS):
        sha = resolve_serving_sha(head)
        path = artifact_path(head)
        print(f"  {head:<15}{(sha[:16] if sha else '-- not on disk --'):<20}{path.name}")
    print("\nA head with no sha is UNBOUND_EVIDENCE at the permission gate: denied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
