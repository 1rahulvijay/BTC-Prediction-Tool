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
import hashlib
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
MAX_HEAD_HEALTH_AGE_S = 14 * 24 * 3600

ARTIFACT_SUFFIXES = (".pkl", ".joblib")
COMPLETE_TRADE_MANIFEST_FIELDS = (
    "artifact_type",
    "artifact_sha256",
    "dataset_sha256",
    "dataset_version",
    "feature_schema_hash",
    "policy_hash",
    "code_hash",
)
MAIN_ENSEMBLE_MANIFEST_FIELDS = (
    "artifact_type",
    "artifact_hash",
    "requested_days",
    "matrix_requested_days",
    "actual_start_ts_ms",
    "actual_end_ts_ms",
    "actual_span_days",
    "row_count",
    "matrix_coverage_ok",
    "matrix_monthly_quality_passed",
    "training_data_hash",
    "source_manifest_hash",
    "feature_schema_hash",
    "code_hash",
)


def _load_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_candidates(artifact: Path) -> list[Path]:
    return [
        path for path in (
            artifact.with_suffix(artifact.suffix + ".manifest.json"),
            artifact.with_suffix(".manifest.json"),
        )
        if path.is_file()
    ]


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
    unmanifested, ambiguous, invalid = [], [], []
    policy_hashes: set[str] = set()
    dataset_hashes: set[str] = set()
    schema_hashes: set[str] = set()
    manifest_families: set[str] = set()
    for p in artifacts:
        candidates = _manifest_candidates(p)
        if not candidates:
            unmanifested.append(p.name)
            continue
        if len(candidates) != 1:
            ambiguous.append(p.name)
            continue
        manifest_path = candidates[0]
        manifest = _load_json(manifest_path)
        complete_trade = "artifact_sha256" in manifest
        required_fields = (
            COMPLETE_TRADE_MANIFEST_FIELDS
            if complete_trade
            else MAIN_ENSEMBLE_MANIFEST_FIELDS
        )
        missing = [
            field for field in required_fields
            if manifest.get(field) in (None, "")
        ]
        problems = list(missing)
        recorded_artifact_hash = manifest.get(
            "artifact_sha256" if complete_trade else "artifact_hash"
        )
        if recorded_artifact_hash != _hash_file(p):
            problems.append("artifact_sha256_mismatch")
        if not complete_trade:
            if int(manifest.get("requested_days") or 0) != int(
                manifest.get("matrix_requested_days") or -1
            ):
                problems.append("requested_days_matrix_mismatch")
            if not bool(manifest.get("matrix_coverage_ok")):
                problems.append("matrix_coverage_failed")
            if not bool(manifest.get("matrix_monthly_quality_passed")):
                problems.append("matrix_monthly_quality_failed")
            if requested_days is not None and int(
                manifest.get("requested_days") or 0
            ) != int(requested_days):
                problems.append("artifact_requested_days_mismatch")
        if problems:
            invalid.append(f"{p.name}:{sorted(set(problems))}")
            continue
        manifest_families.add(
            "complete_trade" if complete_trade else "main_ensemble"
        )
        if manifest.get("policy_hash"):
            policy_hashes.add(str(manifest["policy_hash"]))
        dataset_hashes.add(str(
            manifest.get("dataset_sha256") or manifest.get("training_data_hash")
        ))
        schema_hashes.add(str(manifest["feature_schema_hash"]))
    if unmanifested:
        blockers.append(f"artifacts without a manifest: {sorted(unmanifested)}")
    if ambiguous:
        blockers.append(
            f"artifacts with both manifest conventions present: {sorted(ambiguous)}"
        )
    if invalid:
        blockers.append(f"invalid artifact manifests: {invalid}")
    if len(policy_hashes) > 1:
        blockers.append(
            f"bundle artifacts carry different policy hashes: {sorted(policy_hashes)}"
        )

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
    evidence_last_ts_ms = health.get("evidence_last_ts_ms")
    if evidence_last_ts_ms is None:
        blockers.append(
            "head-health report has no attributable resolved-outcome timestamp"
        )
    else:
        evidence_age_s = time.time() - (float(evidence_last_ts_ms) / 1000.0)
        if evidence_age_s < -300:
            blockers.append("head-health evidence timestamp is in the future")
        elif evidence_age_s > MAX_HEAD_HEALTH_AGE_S:
            blockers.append(
                f"head-health resolved outcomes are stale ({evidence_age_s / 86400:.1f}d old)"
            )
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
        "policy_hashes": sorted(policy_hashes),
        "dataset_hashes": sorted(dataset_hashes),
        "feature_schema_hashes": sorted(schema_hashes),
        "manifest_families": sorted(manifest_families),
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


def _write_bundle_manifest(directory: Path, report: dict) -> tuple[Path, str]:
    """Commit the complete non-circular inventory of an immutable bundle."""
    path = directory / "bundle_manifest.json"
    entries = []
    for item in sorted(directory.rglob("*")):
        if item.is_file() and item != path:
            entries.append({
                "path": item.relative_to(directory).as_posix(),
                "size": item.stat().st_size,
                "sha256": _hash_file(item),
            })
    payload = {
        "manifest_version": 1,
        "entries": entries,
        "policy_hashes": report.get("policy_hashes") or [],
        "dataset_hashes": report.get("dataset_hashes") or [],
        "feature_schema_hashes": report.get("feature_schema_hashes") or [],
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path, _hash_file(path)


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
    bundles.mkdir(parents=True, exist_ok=True)
    staging = bundles / f".staging_{source_hash}_{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(challenger, staging)
    # Verify the copied source before adding the bundle-level inventory.
    copied_source_hash = _bundle_hash(staging)
    if copied_source_hash != source_hash:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"[promote] ABORT: copied source hash {copied_source_hash[:16]} != "
              f"source {source_hash[:16]}")
        return 1
    manifest_path, manifest_hash = _write_bundle_manifest(staging, report)
    final_hash = _bundle_hash(staging)
    target = bundles / f"bundle_{final_hash}"
    if target.exists():
        existing = _bundle_hash(target)
        if existing != final_hash:
            shutil.rmtree(staging, ignore_errors=True)
            print(f"[promote] ABORT: existing bundle content {existing[:16]} != "
                  f"expected {final_hash[:16]}")
            return 1
        if _hash_file(target / manifest_path.name) != manifest_hash:
            shutil.rmtree(staging, ignore_errors=True)
            print("[promote] ABORT: existing bundle manifest differs")
            return 1
        shutil.rmtree(staging, ignore_errors=True)
        print(f"[promote] bundle already materialised and re-verified: {target}")
    else:
        staging.replace(target)
        print(f"[promote] bundle materialised and re-verified: {target}")

    pointer = DATA / "champion.json"
    previous = None
    if pointer.is_file():
        try:
            previous = json.loads(pointer.read_text(encoding="utf-8"))
        except Exception:
            previous = None
    payload = {
        "bundle_hash": final_hash,
        "bundle_manifest_sha256": manifest_hash,
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
    print(f"[promote] champion -> {final_hash[:16]} ({target})")
    if payload["previous_bundle_hash"]:
        print(f"[promote] rollback: point champion.json back at "
              f"{payload['previous_bundle_hash'][:16]}")
    return 0


def selftest() -> int:
    import tempfile
    global DATA, MATRIX_QUALITY, MATRIX_MANIFEST, HEAD_HEALTH

    ok = True

    def chk(cond: bool, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok &= bool(cond)

    def write_test_manifest(artifact: Path) -> None:
        artifact.with_suffix(artifact.suffix + ".manifest.json").write_text(
            json.dumps({
                "artifact_type": "selftest",
                "artifact_sha256": _hash_file(artifact),
                "dataset_sha256": "d" * 64,
                "dataset_version": "selftest",
                "feature_schema_hash": "f" * 64,
                "policy_hash": "p" * 64,
                "code_hash": "c" * 64,
            }),
            encoding="utf-8",
        )

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
        write_test_manifest(chal / "model_a.pkl")
        r = gate_report(chal)
        chk(
            not any(
                "without a manifest" in b or "invalid artifact manifests" in b
                for b in r["blockers"]
            ),
            "a valid manifested artifact clears the manifest gate",
        )
        generic = root / "generic"
        generic.mkdir()
        generic_artifact = generic / "ensemble.pkl"
        generic_artifact.write_bytes(b"ensemble")
        generic_artifact.with_suffix(
            generic_artifact.suffix + ".manifest.json"
        ).write_text(json.dumps({
            "artifact_type": "multi_model_ensemble",
            "artifact_hash": _hash_file(generic_artifact),
            "requested_days": 30,
            "matrix_requested_days": 30,
            "actual_start_ts_ms": 1,
            "actual_end_ts_ms": 2,
            "actual_span_days": 30.0,
            "row_count": 100,
            "matrix_coverage_ok": True,
            "matrix_monthly_quality_passed": True,
            "training_data_hash": "d" * 64,
            "source_manifest_hash": "s" * 64,
            "feature_schema_hash": "f" * 64,
            "code_hash": "c" * 64,
        }), encoding="utf-8")
        r = gate_report(generic, requested_days=30)
        chk(
            not any("invalid artifact manifests" in b for b in r["blockers"]),
            "the existing main-ensemble artifact_identity manifest remains supported",
        )

    # The wrong-key fail-open, pinned. head_health writes "state"; reading "status" found
    # nothing, so DISABLED_NO_SKILL never blocked promotion.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as tmp2:
        root2 = Path(tmp2)
        chal2 = root2 / "c"
        chal2.mkdir()
        (chal2 / "m.pkl").write_bytes(b"x")
        write_test_manifest(chal2 / "m.pkl")
        original = HEAD_HEALTH
        try:
            hh = root2 / "hh.json"
            hh.write_text(
                json.dumps({
                    "evidence_last_ts_ms": int(time.time() * 1000),
                    "heads": {"p_hold": {"state": "DISABLED_NO_SKILL"}},
                }),
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
                json.dumps({
                    "evidence_last_ts_ms": int((time.time() - MAX_HEAD_HEALTH_AGE_S - 60) * 1000),
                    "heads": {"p_hold": {"state": "USABLE"}},
                }), encoding="utf-8"
            )
            r = gate_report(chal2)
            chk(
                any("resolved outcomes are stale" in b for b in r["blockers"]),
                "rewriting a report cannot make old head evidence fresh",
            )
            hh.write_text(
                json.dumps({
                    "evidence_last_ts_ms": int(time.time() * 1000),
                    "heads": {"p_hold": {"state": "USABLE"}},
                }), encoding="utf-8"
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

    print("promotion publishes a verified bundle manifest")
    with tempfile.TemporaryDirectory() as tmp4:
        root4 = Path(tmp4)
        challenger = root4 / "challenger"
        challenger.mkdir()
        artifact = challenger / "model.pkl"
        artifact.write_bytes(b"model")
        write_test_manifest(artifact)
        quality_path = root4 / "quality.json"
        quality_path.write_text(json.dumps({"passed": True, "months": []}), encoding="utf-8")
        matrix_path = root4 / "matrix.json"
        matrix_path.write_text(json.dumps({"requested_days": 30}), encoding="utf-8")
        health_path = root4 / "health.json"
        health_path.write_text(
            json.dumps({
                "evidence_last_ts_ms": int(time.time() * 1000),
                "heads": {"p_hold": {"state": "USABLE"}},
            }), encoding="utf-8"
        )
        originals = (DATA, MATRIX_QUALITY, MATRIX_MANIFEST, HEAD_HEALTH)
        try:
            DATA = root4 / "data"
            MATRIX_QUALITY = quality_path
            MATRIX_MANIFEST = matrix_path
            HEAD_HEALTH = health_path
            chk(promote(challenger, 30, True) == 0, "a fully gated bundle promotes")
            pointer = json.loads((DATA / "champion.json").read_text(encoding="utf-8"))
            target = Path(pointer["path"])
            chk(
                _hash_file(target / "bundle_manifest.json")
                == pointer["bundle_manifest_sha256"],
                "pointer commits the bundle-manifest hash",
            )
            try:
                from trade_forecast.champion_resolver import verify_bundle_manifest
            except ImportError:
                from backend.trade_forecast.champion_resolver import verify_bundle_manifest

            verified, reason = verify_bundle_manifest(
                target, pointer["bundle_manifest_sha256"]
            )
            chk(verified, f"serving verifies the complete inventory ({reason})")
        finally:
            DATA, MATRIX_QUALITY, MATRIX_MANIFEST, HEAD_HEALTH = originals

    # The monthly gate is enforced in BOTH directions, and neither direction is allowed to depend
    # on what today's matrix happens to contain.
    #
    # This previously asserted `passed is False` because the 1265d attempt had failed on 2023-03.
    # That is an observation, not an invariant: rebuilding a clean matrix turned the assertion
    # false and made the launch gate refuse to start on GOOD data. A selftest inside start.bat
    # must assert the RULE (verdict blocks promotion) and merely report the current state.
    quality = _load_json(MATRIX_QUALITY)
    if quality:
        failed = [m["month"] for m in quality.get("months", []) if not m.get("passed")]
        passed = quality.get("passed") is True
        blocked_now = any(
            "monthly data-quality gate" in b for b in gate_report(LIVE)["blockers"]
        )
        chk(
            blocked_now == (not passed),
            f"LIVE: the matrix verdict and the promotion gate agree "
            f"(passed={passed}, failed_months={failed}, blocked={blocked_now})",
        )

    # Enforcement direction, proven on synthetic data so it holds whatever the live matrix says:
    # a FAILING monthly report must block promotion. Without this, a matrix that silently starts
    # reporting `passed: true` would face no test at all.
    import tempfile as _tempfile

    _saved = MATRIX_QUALITY
    try:
        _scratch = Path(_tempfile.mkdtemp()) / "quality.json"
        _scratch.write_text(json.dumps({
            "passed": False,
            "months": [{"month": "2023-03", "passed": False}],
        }), encoding="utf-8")
        MATRIX_QUALITY = _scratch
        _blockers = gate_report(LIVE)["blockers"]
        chk(
            any("monthly data-quality gate" in b and "2023-03" in b for b in _blockers),
            "a FAILING monthly report blocks promotion and names the offending month",
        )
    finally:
        MATRIX_QUALITY = _saved
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
