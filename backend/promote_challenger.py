"""Gated promotion of a challenger model bundle into the live `saved_models` directory.

A long-window build finishing is NOT a reason to replace the models currently driving decisions.
Promotion is a separate, explicit, reviewable step that must pass every gate below:

    1. the challenger directory exists and contains artifacts
    2. every artifact carries a manifest (no unmanifested bytes are promoted)
    3. the matrix the challenger was trained on PASSED its monthly data-quality gate
    4. the claimed training window matches the matrix actually admitted
    5. head health does not mark a promoted head DISABLED_NO_SKILL
    6. the incumbent is snapshotted first, so rollback is always one copy away

Refusal is the default. `--apply` performs the copy only when every gate passes.

    python backend/promote_challenger.py --challenger data/saved_models_challenger_1265d
    python backend/promote_challenger.py --challenger <dir> --apply
    python backend/promote_challenger.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
LIVE = DATA / "saved_models"
MATRIX_QUALITY = DATA / "research_matrix_monthly_quality.json"
MATRIX_MANIFEST = DATA / "research_matrix_1m.manifest.json"
HEAD_HEALTH = DATA / "research" / "head_health" / "head_health.json"

ARTIFACT_SUFFIXES = (".pkl", ".joblib")


def _load_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def gate_report(challenger: Path, requested_days: int | None = None) -> dict:
    """Evaluate every promotion gate. Pure-ish (reads disk); returns reasons, never raises."""
    blockers: list[str] = []
    notes: list[str] = []

    artifacts = (
        [p for p in challenger.iterdir() if p.suffix in ARTIFACT_SUFFIXES]
        if challenger.is_dir()
        else []
    )
    if not challenger.is_dir():
        blockers.append(f"challenger directory does not exist: {challenger}")
    elif not artifacts:
        blockers.append("challenger directory contains no model artifacts")

    # 2. every artifact must carry a manifest - unmanifested bytes have no provenance at all.
    # TWO CONVENTIONS EXIST IN THIS REPO and accepting either silently hides a real mismatch:
    #   model_common.artifact_issues()          -> x.manifest.json      (suffix REPLACED)
    #   artifact_identity.artifact_manifest_path -> x.pkl.manifest.json (suffix APPENDED)
    # An artifact whose manifest uses the convention its own LOADER does not read is
    # effectively unmanifested at serving time, so the mix is reported, not smoothed over.
    unmanifested, appended, replaced = [], [], []
    for p in artifacts:
        has_appended = p.with_suffix(p.suffix + ".manifest.json").is_file()
        has_replaced = p.with_suffix(".manifest.json").is_file()
        if has_appended:
            appended.append(p.name)
        if has_replaced:
            replaced.append(p.name)
        if not (has_appended or has_replaced):
            unmanifested.append(p.name)
    if unmanifested:
        blockers.append(f"artifacts without a manifest: {sorted(unmanifested)}")
    if appended and replaced:
        blockers.append(
            f"MIXED manifest naming in one bundle - appended={sorted(appended)} "
            f"replaced={sorted(replaced)}. A loader reads only one convention, so part of this "
            f"bundle would serve unmanifested."
        )
    if appended:
        notes.append(f"manifest naming (appended .pkl.manifest.json): {sorted(appended)}")

    # 3/4. the matrix must have passed its own monthly gate, and match the claimed window.
    quality = _load_json(MATRIX_QUALITY)
    if not quality:
        blockers.append("no monthly data-quality report for the training matrix")
    elif not quality.get("passed"):
        failed = [m["month"] for m in quality.get("months", []) if not m.get("passed")]
        blockers.append(
            f"training matrix FAILED its monthly data-quality gate (months={failed or 'unknown'})"
        )

    manifest = _load_json(MATRIX_MANIFEST)
    admitted = manifest.get("requested_days")
    if requested_days is not None:
        if admitted is None:
            blockers.append("matrix manifest does not record requested_days")
        elif int(admitted) != int(requested_days):
            blockers.append(
                f"window mismatch: promotion claims {requested_days}d but the admitted matrix "
                f"is {admitted}d - a head may not be named for a window it was not trained on"
            )
        else:
            notes.append(f"admitted matrix window = {admitted}d")

    # 5. a head live outcomes call skill-less must not be promoted into the live bundle.
    # head_health.py writes the verdict under "state". Reading "status" silently found nothing,
    # so a DISABLED_NO_SKILL head would never have blocked promotion - a fail-open in the exact
    # gate meant to be fail-closed. Both keys are read now so a future rename cannot re-open it.
    health = _load_json(HEAD_HEALTH)
    heads = health.get("heads") or {}
    disabled = [
        name for name, entry in heads.items()
        if "DISABLED_NO_SKILL" in {
            str((entry or {}).get("state") or ""),
            str((entry or {}).get("status") or ""),
        }
    ]
    if disabled:
        blockers.append(f"heads measured DISABLED_NO_SKILL: {sorted(disabled)}")
    # A MISSING report is a blocker for promotion. Nothing measured is not evidence of health,
    # and promotion is exactly the moment where absence of evidence must not read as a pass.
    if not heads:
        blockers.append(
            "no head-health report (promotion requires measured health; absence is not a pass)"
        )

    return {
        "challenger": str(challenger),
        "artifacts": sorted(p.name for p in artifacts),
        "blockers": blockers,
        "notes": notes,
        "promotable": not blockers,
    }


def _bundle_hash(directory: Path) -> str:
    """Hash of every file in the bundle, by name and content. Order-independent."""
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def promote(challenger: Path, requested_days: int | None, apply: bool) -> int:
    """ATOMIC bundle promotion.

    Copying files one by one into the live directory means an interruption - a crash, a full
    disk, a reboot - leaves a MIXED bundle: some incumbent artifacts, some challenger. That
    bundle was never tested as a unit and its manifests describe neither model.

    Instead each bundle lives in its own immutable directory keyed by content hash, and the live
    pointer is a single small file swapped with an atomic replace. Rollback is the same operation
    against the previous hash - no copying, nothing half-applied.
    """
    report = gate_report(challenger, requested_days)
    print(json.dumps(report, indent=2))
    if not report["promotable"]:
        print("\nPROMOTION REFUSED - the champion pointer is unchanged.")
        return 1
    if not apply:
        print("\nAll gates pass. Re-run with --apply to promote.")
        return 0

    source_hash = _bundle_hash(challenger)
    bundles = DATA / "saved_model_bundles"
    target = bundles / f"bundle_{source_hash}"
    if target.exists():
        # A pre-existing directory is NOT proof of correct content. Re-hash it; a truncated or
        # tampered bundle from an earlier interrupted run must not be adopted silently.
        existing = _bundle_hash(target)
        if existing != source_hash:
            print(f"[promote] ABORT: existing bundle content {existing[:16]} != "
                  f"expected {source_hash[:16]}")
            return 1
        print(f"[promote] bundle already materialised and re-verified: {target}")
    else:
        staging = bundles / f".staging_{source_hash}_{os.getpid()}"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(challenger, staging)
        # Re-verify AFTER the copy: a truncated or corrupted write must not become the champion.
        copied_hash = _bundle_hash(staging)
        if copied_hash != source_hash:
            shutil.rmtree(staging, ignore_errors=True)
            print(f"[promote] ABORT: copied bundle hash {copied_hash[:16]} != "
                  f"source {source_hash[:16]}")
            return 1
        staging.replace(target)          # atomic on the same filesystem
        print(f"[promote] bundle materialised and re-verified: {target}")

    pointer = DATA / "champion.json"
    previous = None
    if pointer.is_file():
        try:
            previous = json.loads(pointer.read_text(encoding="utf-8"))
        except Exception:
            previous = None
    payload = {
        "bundle_hash": source_hash,
        "path": str(target),
        "promoted_at": time.time(),
        "requested_days": requested_days,
        "previous_bundle_hash": (previous or {}).get("bundle_hash"),
    }
    # Atomic pointer swap: write beside the target, then replace in one operation. A reader either
    # sees the old champion or the new one, never a partially written file.
    temporary = pointer.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(pointer)
    print(f"[promote] champion -> {source_hash[:16]} ({target})")
    if payload["previous_bundle_hash"]:
        print(f"[promote] rollback: point champion.json back at "
              f"{payload['previous_bundle_hash'][:16]}")
    return 0


def selftest() -> int:
    import tempfile

    ok = True

    def chk(cond: bool, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok &= bool(cond)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        empty = root / "empty"
        r = gate_report(empty)
        chk(not r["promotable"], "a missing challenger directory is refused")

        chal = root / "chal"
        chal.mkdir()
        r = gate_report(chal)
        chk(not r["promotable"], "an empty challenger directory is refused")

        (chal / "model_a.pkl").write_bytes(b"x")
        r = gate_report(chal)
        chk(
            any("without a manifest" in b for b in r["blockers"]),
            "an artifact with no manifest is refused (no provenance)",
        )
        (chal / "model_a.pkl.manifest.json").write_text("{}", encoding="utf-8")
        r = gate_report(chal)
        chk(
            not any("without a manifest" in b for b in r["blockers"]),
            "a manifested artifact clears the manifest gate",
        )

    # The wrong-key fail-open, pinned. head_health writes "state"; reading "status" found
    # nothing, so DISABLED_NO_SKILL never blocked promotion.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as tmp2:
        root2 = Path(tmp2)
        chal2 = root2 / "c"
        chal2.mkdir()
        (chal2 / "m.pkl").write_bytes(b"x")
        (chal2 / "m.pkl.manifest.json").write_text("{}", encoding="utf-8")
        global HEAD_HEALTH
        original = HEAD_HEALTH
        try:
            hh = root2 / "hh.json"
            hh.write_text(
                json.dumps({"heads": {"p_hold": {"state": "DISABLED_NO_SKILL"}}}),
                encoding="utf-8",
            )
            HEAD_HEALTH = hh
            r = gate_report(chal2)
            chk(
                any("DISABLED_NO_SKILL" in b for b in r["blockers"]),
                'a head disabled under the "state" key BLOCKS promotion (was silently ignored)',
            )
            hh.write_text(json.dumps({"heads": {}}), encoding="utf-8")
            r = gate_report(chal2)
            chk(
                any("no head-health report" in b for b in r["blockers"]),
                "a MISSING health report blocks promotion (absence is not a pass)",
            )
            hh.write_text(
                json.dumps({"heads": {"p_hold": {"state": "USABLE"}}}), encoding="utf-8"
            )
            r = gate_report(chal2)
            chk(
                not any("DISABLED" in b or "no head-health" in b for b in r["blockers"]),
                "a measured-USABLE head clears the health gate",
            )
        finally:
            HEAD_HEALTH = original

    # Atomic promotion: content-hashed immutable bundle + single pointer swap.
    import tempfile as _tf3
    with _tf3.TemporaryDirectory() as tmp3:
        root3 = Path(tmp3)
        a = root3 / "a"
        a.mkdir()
        (a / "m.pkl").write_bytes(b"model-bytes")
        h1 = _bundle_hash(a)
        chk(len(h1) == 64, "bundle hash covers every file by name and content")
        (a / "m.pkl").write_bytes(b"model-bytes-CHANGED")
        chk(_bundle_hash(a) != h1, "changing one byte changes the bundle hash")
        (a / "m.pkl").write_bytes(b"model-bytes")
        chk(_bundle_hash(a) == h1, "the hash is stable for identical content")
        b = root3 / "b"
        b.mkdir()
        (b / "m.pkl").write_bytes(b"model-bytes")
        chk(_bundle_hash(b) == h1, "identical bundles in different paths hash the same")
        (b / "extra.pkl").write_bytes(b"x")
        chk(_bundle_hash(b) != h1, "an EXTRA file changes the bundle hash (no partial bundles)")

    # Live state: the real 1265d attempt FAILED its monthly gate on 2023-03, so a bundle claiming
    # that window must be refused right now. This is the gate doing its job, not a bug.
    quality = _load_json(MATRIX_QUALITY)
    if quality:
        failed = [m["month"] for m in quality.get("months", []) if not m.get("passed")]
        chk(
            quality.get("passed") is False and bool(failed),
            f"LIVE: matrix monthly gate currently FAILS (months={failed}) so promotion is blocked",
        )
    manifest = _load_json(MATRIX_MANIFEST)
    if manifest.get("requested_days"):
        r = gate_report(LIVE, requested_days=1265)
        chk(
            any("window mismatch" in b for b in r["blockers"]),
            f"LIVE: a 1265d claim is refused against the admitted "
            f"{manifest.get('requested_days')}d matrix",
        )

    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--challenger", type=Path)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not args.challenger:
        print("usage: --challenger <dir> [--days N] [--apply]")
        return 2
    return promote(args.challenger.resolve(), args.days, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
