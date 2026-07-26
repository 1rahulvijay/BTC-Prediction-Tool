"""
train_heads.py — version-aware trainer for ALL standalone heads (start.bat entry point).
=========================================================================================
Same discipline as the main ensemble's MODEL_ARCH_VERSION: each head carries a HEAD_VERSION.
On start.bat this script retrains a head ONLY when it is (a) MISSING, or (b) its saved
`version` != the trainer's current HEAD_VERSION. If the head exists and the version matches,
it is SKIPPED — so a normal restart does NOT retrain (nothing changed). Bump a trainer's
HEAD_VERSION to force exactly that head to rebuild on the next restart.

Heads:
  • versioned (retrain on version change): selectivity, signed_quantile, persistence(keeper)
  • legacy (retrain only if missing — no version tag): beat, magnitude, path, fingerprints

The heads fit on ALL available research-matrix days (the whole window) with their own small
calibration holdouts (CQR / isotonic) — that is their "use ~all the data" equivalent; they
cannot use 100% because the calibration split is mandatory (same reason the ensemble caps at 0.98).

Crash-safe: one head failing never blocks the others or the app boot.

Usage:  python backend/train_heads.py        (start.bat calls this)
        python backend/train_heads.py --force (retrain every head regardless)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from artifact_identity import (
    artifact_compatibility,
    current_training_identity,
    training_identity_issues,
    write_artifact_manifest,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
SM = os.path.join(DATA_DIR, "saved_models")
PY = sys.executable
DAYS = os.environ.get("BTC_BACKFILL_DAYS") or os.environ.get("BTC_HISTORICAL_DAYS") or "60"
TRAIN_LEGACY_MISSING = os.environ.get("BTC_TRAIN_LEGACY_HEADS", "0") == "1"


def _requested_days() -> int:
    try:
        return max(1, int(DAYS))
    except (TypeError, ValueError):
        return 60


def _head_identity(head: dict) -> dict:
    trainer_path = head["cmd"][1] if len(head.get("cmd") or []) > 1 else __file__
    return current_training_identity(
        requested_days=_requested_days(),
        code_paths=[trainer_path],
        full_refit=False,
    )


def _identity_status(head: dict) -> tuple[bool, list[str]]:
    if not os.path.exists(head["out"]):
        return False, ["artifact is missing"]
    return artifact_compatibility(
        head["out"], _head_identity(head), strict=True
    )


def _identity_changes(before: dict, after: dict) -> list[str]:
    keys = (
        "requested_days",
        "matrix_requested_days",
        "actual_start_ts_ms",
        "actual_end_ts_ms",
        "actual_span_days",
        "row_count",
        "training_data_hash",
        "source_manifest_hash",
        "feature_schema_hash",
        "code_hash",
        "matrix_monthly_quality_passed",
    )
    return [key for key in keys if before.get(key) != after.get(key)]


def _import_version(rel_dir: str, module: str) -> str | None:
    try:
        sys.path.insert(0, os.path.join(ROOT, rel_dir))
        mod = __import__(module)
        return getattr(mod, "HEAD_VERSION", None)
    except Exception:
        return None


def _saved_version(path: str):
    try:
        import joblib
        b = joblib.load(path)
        return b.get("version") if isinstance(b, dict) else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="retrain every head regardless of version")
    ap.add_argument("--dry-run", action="store_true", help="print decisions only; do not train")
    args = ap.parse_args()

    current_identity = current_training_identity(requested_days=_requested_days())
    identity_issues = training_identity_issues(current_identity)
    if identity_issues:
        print("[heads] ERROR: research-matrix identity contract failed:")
        for issue in identity_issues:
            print(f"[heads]   - {issue}")
        print("[heads] No standalone model will be trained or stamped.")
        return 2

    sel_ver = _import_version("backend/decision", "train_selectivity_models")
    sq_ver = _import_version("backend", "train_signed_quantiles")
    pers_ver = _import_version("backend", "train_persistence_model")
    bm_ver = _import_version("backend", "train_bigmove_keeper")
    bd_ver = _import_version("backend", "train_bigdrop_keeper")
    dir_ver = _import_version("backend", "train_directional_keeper")
    act_ver = _import_version("backend", "train_activity_keeper")
    champ_ver = _import_version("backend", "train_champion_meta")
    path_forecaster_ver = _import_version("backend", "train_path_forecaster")
    round_state_ver = _import_version("backend", "train_round_state_heads")

    heads = [
        # versioned heads — retrain on MISSING or version change
        {"name": "selectivity", "out": os.path.join(SM, "selectivity_models.pkl"), "ver": sel_ver,
         "cmd": [PY, os.path.join("backend", "decision", "train_selectivity_models.py")]},
        {"name": "signed_quantile", "out": os.path.join(SM, "signed_quantile_model.pkl"), "ver": sq_ver,
         "cmd": [PY, os.path.join("backend", "train_signed_quantiles.py")]},
        {"name": "persistence", "out": os.path.join(SM, "persistence_model.pkl"), "ver": pers_ver,
         "cmd": [PY, os.path.join("backend", "train_persistence_model.py")]},
        # Fade is deliberately NOT in the production retrain. The causal 1m head missed the
        # frozen precision gate and the honest 1s challenger also failed its joint AUC/top-decile
        # gate. Keep both as research artifacts; do not let --force silently reactivate them.
        {"name": "path_forecaster", "out": os.path.join(SM, "path_forecaster.pkl"),
         "ver": path_forecaster_ver,
         "cmd": [PY, os.path.join("backend", "train_path_forecaster.py")]},
        {"name": "round_state", "out": os.path.join(SM, "round_state_heads.pkl"),
         "ver": round_state_ver,
         "cmd": [PY, os.path.join("backend", "train_round_state_heads.py")]},
        {"name": "bigmove", "out": os.path.join(SM, "bigmove_keeper_model.pkl"), "ver": bm_ver,
         "cmd": [PY, os.path.join("backend", "train_bigmove_keeper.py")]},
        {"name": "bigdrop", "out": os.path.join(SM, "bigdrop_keeper_model.pkl"), "ver": bd_ver,
         "cmd": [PY, os.path.join("backend", "train_bigdrop_keeper.py")]},
        {"name": "directional", "out": os.path.join(SM, "directional_keeper_model.pkl"), "ver": dir_ver,
         "cmd": [PY, os.path.join("backend", "train_directional_keeper.py")]},
        {"name": "activity", "out": os.path.join(SM, "activity_keeper_model.pkl"), "ver": act_ver,
         "cmd": [PY, os.path.join("backend", "train_activity_keeper.py")]},
        # legacy heads — retrain only if MISSING (no version tag)
        {"name": "champion_meta", "out": os.path.join(SM, "champion_meta_model.pkl"), "ver": champ_ver,
         "cmd": [PY, os.path.join("backend", "train_champion_meta.py")]},
        {"name": "beat", "out": os.path.join(SM, "beat_model.pkl"), "ver": None,
         "cmd": [PY, os.path.join("backend", "train_beat_classifier.py"), "--days", DAYS]},
        {"name": "magnitude", "out": os.path.join(SM, "magnitude_model.pkl"), "ver": None,
         "cmd": [PY, os.path.join("backend", "train_magnitude_quantiles.py"), "--days", DAYS]},
        {"name": "path", "out": os.path.join(SM, "path_model.pkl"), "ver": None,
         "cmd": [PY, os.path.join("backend", "build_path_labels.py"), "--days", DAYS]},
        {"name": "fingerprints", "out": os.path.join(DATA_DIR, "fingerprint_evidence.parquet"), "ver": None,
         "cmd": [PY, os.path.join("backend", "build_fingerprints_historical.py"), "--days", DAYS]},
    ]

    # These heads have NOISE / insufficient-data GATES: they legitimately exit 0 WITHOUT writing an
    # artifact when the signal fails the gate (beat/magnitude/path) or data is too thin
    # (champion_meta/fingerprints). For them a missing/stale artifact is a valid "no signal, not saved"
    # outcome and must NOT be counted as a failure — otherwise the full-retrain completion marker never
    # gets written and start.bat re-runs the entire (18-36h) cycle on every boot. Only a NONZERO EXIT
    # (a real crash) fails an optional head. (Bug found 2026-06-22 during the pre-360d-run audit.)
    OPTIONAL_HEADS = {"round_state", "champion_meta", "beat", "magnitude", "path", "fingerprints"}
    print(f"[heads] version-aware head training (days={DAYS}, force={args.force})")
    failures = []
    for h in heads:
        exists = os.path.exists(h["out"])
        identity_ok, identity_reasons = _identity_status(h) if exists else (False, [])
        if args.force:
            need, why = True, "forced"
        elif exists and not identity_ok:
            need, why = True, "identity mismatch: " + "; ".join(identity_reasons)
        elif not exists and h["ver"] is None and not TRAIN_LEGACY_MISSING:
            need, why = False, "missing legacy skipped (set BTC_TRAIN_LEGACY_HEADS=1 to build)"
        elif not exists:
            need, why = True, "missing"
        elif h["ver"] is not None:
            sv = _saved_version(h["out"])
            need = (sv != h["ver"])
            why = f"version {sv} != {h['ver']}" if need else "up-to-date"
        else:
            need, why = False, "present (legacy, no version)"
        if not need:
            print(f"[heads] SKIP  {h['name']:16} ({why})")
            continue
        print(f"[heads] {'WOULD TRAIN' if args.dry_run else 'TRAIN'} {h['name']:16} ({why})")
        if args.dry_run:
            continue
        before_mtime = os.path.getmtime(h["out"]) if os.path.exists(h["out"]) else None
        training_identity = _head_identity(h)
        optional = h["name"] in OPTIONAL_HEADS
        try:
            result = subprocess.run(h["cmd"], cwd=ROOT, check=False)
            after_mtime = os.path.getmtime(h["out"]) if os.path.exists(h["out"]) else None
            if result.returncode != 0:
                failures.append((h["name"], f"exit={result.returncode}"))
            elif optional and (after_mtime is None or (before_mtime is not None and after_mtime <= before_mtime)):
                # exit 0 but no fresh artifact: a noise-gated head that correctly declined to save.
                print(f"[heads] OK    {h['name']:16} (exit 0; noise/data gate -> not saved, valid)")
            elif after_mtime is None:
                failures.append((h["name"], "expected output missing"))
            elif args.force and before_mtime is not None and after_mtime <= before_mtime:
                failures.append((h["name"], "forced run did not refresh output"))
            else:
                identity_changes = _identity_changes(
                    training_identity, _head_identity(h)
                )
                if identity_changes:
                    failures.append((
                        h["name"],
                        "training identity changed during fit: "
                        + ", ".join(identity_changes),
                    ))
                    print(
                        f"[heads] FAIL  {h['name']:16} "
                        "(data/code identity changed during fit; artifact not stamped)"
                    )
                    continue
                manifest_path = write_artifact_manifest(
                    h["out"],
                    training_identity,
                    artifact_type=f"specialist_head:{h['name']}",
                    extra={
                        "head_name": h["name"],
                        "head_version": h["ver"],
                        "trainer_command": h["cmd"],
                    },
                )
                print(
                    f"[heads] ID    {h['name']:16} "
                    f"(manifest={os.path.basename(manifest_path)})"
                )
        except Exception as e:
            print(f"[heads] {h['name']} training error (continuing): {e}")
            failures.append((h["name"], str(e)))
    if failures:
        print(f"[heads] completed with {len(failures)} failure(s):")
        for name, reason in failures:
            print(f"[heads] FAIL  {name:16} ({reason})")
        return 1
    print("[heads] done: all requested heads completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
