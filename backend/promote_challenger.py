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
    unmanifested = [
        p.name for p in artifacts
        if not (p.with_suffix(p.suffix + ".manifest.json").is_file()
                or p.with_suffix(".manifest.json").is_file())
    ]
    if unmanifested:
        blockers.append(f"artifacts without a manifest: {sorted(unmanifested)}")

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
    health = _load_json(HEAD_HEALTH)
    disabled = [
        name for name, entry in (health.get("heads") or {}).items()
        if str((entry or {}).get("status") or "") == "DISABLED_NO_SKILL"
    ]
    if disabled:
        blockers.append(f"heads measured DISABLED_NO_SKILL: {sorted(disabled)}")
    if not health:
        notes.append("no head-health report found (not blocking; nothing measured yet)")

    return {
        "challenger": str(challenger),
        "artifacts": sorted(p.name for p in artifacts),
        "blockers": blockers,
        "notes": notes,
        "promotable": not blockers,
    }


def promote(challenger: Path, requested_days: int | None, apply: bool) -> int:
    report = gate_report(challenger, requested_days)
    print(json.dumps(report, indent=2))
    if not report["promotable"]:
        print("\nPROMOTION REFUSED - the incumbent bundle is unchanged.")
        return 1
    if not apply:
        print("\nAll gates pass. Re-run with --apply to promote (incumbent is snapshotted first).")
        return 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = DATA / f"saved_models_incumbent_{stamp}"
    if LIVE.is_dir():
        shutil.copytree(LIVE, backup)
        print(f"[promote] incumbent snapshotted -> {backup}")
    LIVE.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in challenger.iterdir():
        if item.is_file():
            shutil.copy2(item, LIVE / item.name)
            copied += 1
    print(f"[promote] copied {copied} file(s) into {LIVE}")
    print(f"[promote] rollback: copy {backup} back over {LIVE}")
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
