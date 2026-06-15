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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
SM = os.path.join(DATA_DIR, "saved_models")
PY = sys.executable
DAYS = os.environ.get("BTC_BACKFILL_DAYS") or os.environ.get("BTC_HISTORICAL_DAYS") or "60"


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

    sel_ver = _import_version("backend/decision", "train_selectivity_models")
    sq_ver = _import_version("backend", "train_signed_quantiles")
    pers_ver = _import_version("backend", "train_persistence_model")

    heads = [
        # versioned heads — retrain on MISSING or version change
        {"name": "selectivity", "out": os.path.join(SM, "selectivity_models.pkl"), "ver": sel_ver,
         "cmd": [PY, os.path.join("backend", "decision", "train_selectivity_models.py")]},
        {"name": "signed_quantile", "out": os.path.join(SM, "signed_quantile_model.pkl"), "ver": sq_ver,
         "cmd": [PY, os.path.join("backend", "train_signed_quantiles.py")]},
        {"name": "persistence", "out": os.path.join(SM, "persistence_model.pkl"), "ver": pers_ver,
         "cmd": [PY, os.path.join("backend", "train_persistence_model.py")]},
        # legacy heads — retrain only if MISSING (no version tag)
        {"name": "beat", "out": os.path.join(SM, "beat_model.pkl"), "ver": None,
         "cmd": [PY, os.path.join("backend", "train_beat_classifier.py"), "--days", DAYS]},
        {"name": "magnitude", "out": os.path.join(SM, "magnitude_model.pkl"), "ver": None,
         "cmd": [PY, os.path.join("backend", "train_magnitude_quantiles.py"), "--days", DAYS]},
        {"name": "path", "out": os.path.join(SM, "path_model.pkl"), "ver": None,
         "cmd": [PY, os.path.join("backend", "build_path_labels.py"), "--days", DAYS]},
        {"name": "fingerprints", "out": os.path.join(DATA_DIR, "fingerprint_evidence.parquet"), "ver": None,
         "cmd": [PY, os.path.join("backend", "build_fingerprints_historical.py"), "--days", DAYS]},
    ]

    print(f"[heads] version-aware head training (days={DAYS}, force={args.force})")
    for h in heads:
        exists = os.path.exists(h["out"])
        if args.force:
            need, why = True, "forced"
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
        try:
            subprocess.run(h["cmd"], cwd=ROOT, check=False)
        except Exception as e:
            print(f"[heads] {h['name']} training error (continuing): {e}")
    print("[heads] done.")


if __name__ == "__main__":
    main()
