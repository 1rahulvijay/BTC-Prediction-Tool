"""
auto_finetune.py - nightly REFIT + RECALIBRATE of the cheap heads (no GPU, no feed freeze).
=============================================================================================
Keeps the conformal band + P(Hold) + selectivity calibrated to the RECENT regime as new data flows
in. Reruns only the cheap head trainers (seconds-minutes each, single-thread, no GPU).

OUTPUT IS A CANDIDATE, NOT A DEPLOY (2026-08-05):
  Every trainer is redirected to data/saved_models/candidates/<UTC-stamp>/ and the serving
  artifacts are left untouched. Until this change the job REWROTE the five serving .pkls in
  place and the live app hot-reloaded them within ~30s with no challenger gate - so a position
  could open under one artifact and be managed under another, both recorded under a single
  logical name, with nothing in the record showing a swap had occurred.

  The redirect is verified, not trusted: serving artifacts are hashed before the run and
  restored from backup if anything changed. Promotion is a separate, gated, recorded act.

What it does NOT do (by design):
  • NOT the 6h direction-ensemble retrain (that's a deliberate FREEZE=0 job; direction is at the
    ceiling, so auto-tuning it gains ~0).
  • NOT touch the live DB (the backfill writes parquet; the trainers write .pkl only).

Payload = RECALIBRATION, not accuracy: with `--with-backfill` it appends new days first, so the heads
recalibrate on the newer recent slice (the conformal cqr / isotonic that actually drift with vol).

Usage:
  python backend/auto_finetune.py                    # refit the cheap heads on existing data
  python backend/auto_finetune.py --with-backfill --days 90   # append new days, then refit (nightly)
  python backend/auto_finetune.py --dry-run          # show the plan, run nothing
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

BACKEND = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BACKEND)
DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
SERVING_DIR = os.path.join(DATA_DIR, "saved_models")
#: Where a nightly run puts its OUTPUT. Never the serving directory.
CANDIDATE_ROOT = os.path.join(SERVING_DIR, "candidates")
PY = sys.executable

# The matrix + cheap heads recalibrate on the SAME window as the main retrain, so every head stays
# consistent (operator choice 2026-06-23). Defaults to 360d. The scheduled task does NOT set
# BTC_HISTORICAL_DAYS, so without this explicit window the matrix step silently fell back to
# build_research_matrix's 60d default and recalibrated the band/selectivity/P(Hold) on 60d while the
# main model trained on 360d -- the bug this fixes. (The task's --days flag only sizes the incremental
# backfill append; it never reached the matrix step, which has supports_days=False.)
FULL_DAYS = int(os.environ.get("BTC_HISTORICAL_DAYS") or 360)

# (label, script-relative-to-backend, extra-args, supports_--auto/--days)
BACKFILL = [
    ("backfill trade-features", "backfill_trade_features.py", ["--auto"], True),
    ("backfill persistence", "build_persistence_dataset.py", ["--auto"], True),
    ("backfill cross-venue", "build_crossvenue_flow.py", ["--auto"], True),
    ("rebuild research matrix", "build_research_matrix.py", ["--days", str(FULL_DAYS)], False),
]
REFIT = [
    ("signed-quantile band (recalibrate cqr)", "train_signed_quantiles.py", [], False),
    # selectivity ensemble REMOVED from the nightly refit (2026-07-02 wiring audit): it is trained
    # version-aware at boot (train_heads.py) but NOTHING in the live serving path loads it -- the
    # timing edge is carried live by the big-move keeper head. Nightly retraining an unserved model
    # was pure wasted compute. Re-add here only when decision_composer actually serves it.
    ("persistence P(Hold)", "train_persistence_model.py", [], False),
    ("path forecaster (high/low band + touch)", "train_path_forecaster.py", [], False),
    ("fade entry model (touch-context)", "train_fade_model.py", [], False),
    # round-state shadow heads (flip risk / late-shock / next-opportunity) train from the SAME research
    # matrix as the heads above; without this entry a full-window retrain refreshed every other cheap
    # head but silently left the round-state panel on the old window (train_heads.py only retrains on
    # VERSION change, not data change).
    ("round-state shadow heads", "train_round_state_heads.py", [], False),
]

#: The artifacts this nightly job REWRITES, declared here because this job owns them.
#:
#: The release freeze imports this list rather than keeping its own copy. Without that, the
#: freeze pinned all five as immutable and reported them DRIFTED after every single nightly
#: run - a check that failed by design, every day, for doing exactly what it was built to do.
#: A check that always fails teaches people to ignore it, which is worse than not having it.
#:
#: Anything added to REFIT above must be added here, or the freeze will call it drift.
REFIT_ARTIFACTS = (
    "signed_quantile_model.pkl",
    "persistence_model.pkl",
    "path_forecaster.pkl",
    "fade_model.pkl",
    "round_state_heads.pkl",
)


def _digest(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def snapshot_serving():
    """Hash and back up every artifact this job may touch, BEFORE it runs."""
    backup = os.path.join(CANDIDATE_ROOT, ".serving_backup")
    os.makedirs(backup, exist_ok=True)
    digests = {}
    for name in REFIT_ARTIFACTS:
        live = os.path.join(SERVING_DIR, name)
        digests[name] = _digest(live)
        if digests[name] is not None:
            shutil.copy2(live, os.path.join(backup, name))
    return {"backup_dir": backup, "digests": digests}


def protect_serving(before, candidate_dir):
    """Restore any serving artifact a trainer overwrote, keeping its output as a CANDIDATE.

    All five trainers now honour BTC_MODEL_OUTPUT_DIR, but the redirect is VERIFIED rather
    than trusted: `train_fade_model.py` hardcoded the serving path until this change, and a
    future trainer added to REFIT could do the same. The failure mode is silent - the artifact
    is replaced under its serving name and the live app hot-reloads it within ~30s - so the
    guard checks the bytes instead of assuming cooperation.

    That silence is the whole reason for this change. A position could open under one artifact
    and be managed under another, both recorded under one logical name, with no challenger gate
    and nothing in the record showing a swap had happened.
    """
    os.makedirs(candidate_dir, exist_ok=True)
    report = {"restored": [], "captured": [], "unchanged": []}
    for name in REFIT_ARTIFACTS:
        live = os.path.join(SERVING_DIR, name)
        now, was = _digest(live), before["digests"].get(name)
        if now == was:
            report["unchanged"].append(name)
            continue
        # The trainer ignored the redirect. Keep its work as a candidate, then put the
        # serving artifact back exactly as it was.
        if now is not None:
            shutil.copy2(live, os.path.join(candidate_dir, name))
            report["captured"].append(name)
        backup = os.path.join(before["backup_dir"], name)
        if os.path.exists(backup):
            shutil.copy2(backup, live)
            report["restored"].append(name)
        elif os.path.exists(live):
            # Nothing was serving under this name before. Leaving the new file would be a
            # silent promotion, so it is removed once captured.
            os.remove(live)
            report["restored"].append(name + " (removed; none existed before)")
    return report


def _run(label, script, extra, supports_days, days, timeout=3600):
    path = os.path.join(BACKEND, script)
    if not os.path.exists(path):
        print(f"  -- {label}: SKIP (no {script})")
        return None
    cmd = [PY, path] + list(extra)
    if supports_days and days:
        cmd += ["--days", str(days)]
    print(f"\n=== {label} ===\n  $ {' '.join(cmd)}", flush=True)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=BACKEND, capture_output=True, text=True, timeout=timeout)
        tail = [ln for ln in (r.stdout or "").splitlines() if ln.strip()][-6:]
        for ln in tail:
            print("   " + ln)
        if r.returncode != 0:
            err = [ln for ln in (r.stderr or "").splitlines() if ln.strip()][-3:]
            print(f"  !! exit {r.returncode}: {' / '.join(err)[:200]}")
            return False
        print(f"  OK ({time.time() - t0:.0f}s)")
        return True
    except subprocess.TimeoutExpired:
        print(f"  !! {label}: TIMEOUT (>{timeout}s)")
        return False
    except Exception as e:
        print(f"  !! {label}: {str(e)[:160]}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-backfill", action="store_true", help="append new days before refitting")
    ap.add_argument("--days", type=int, default=90, help="backfill window (with --with-backfill)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(f"AUTO-FINETUNE {time.strftime('%Y-%m-%d %H:%M:%S')} - cheap-head refit (recalibration payload)")
    print(f"  recalibration window = {FULL_DAYS}d (matrix + cheap heads match the main retrain).")
    print("  direction ensemble NOT touched (6h FREEZE=0 job; at the ceiling).")
    steps = (BACKFILL if a.with_backfill else []) + REFIT
    if a.dry_run:
        for label, script, extra, sd in steps:
            d = f" --days {a.days}" if (sd and a.with_backfill) else ""
            print(f"  would run: {label}  ->  {script} {' '.join(extra)}{d}")
        return 0
    # CANDIDATE-ONLY. This job builds an immutable candidate bundle; it does not promote.
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    candidate_dir = os.path.join(CANDIDATE_ROOT, stamp)
    os.makedirs(candidate_dir, exist_ok=True)
    before = snapshot_serving()
    os.environ["BTC_MODEL_OUTPUT_DIR"] = candidate_dir
    print(f"  output -> {candidate_dir}")
    print("  serving artifacts are NOT modified; promotion is a separate, gated act.")

    ok = run = 0
    try:
        for label, script, extra, sd in steps:
            res = _run(label, script, extra, sd, a.days)
            if res is not None:
                run += 1
                ok += int(bool(res))
    finally:
        # Runs on failure and on interrupt too: a half-finished job must never leave a
        # partially-rewritten serving directory behind.
        guard = protect_serving(before, candidate_dir)

    if guard["captured"]:
        print(f"  !! {len(guard['captured'])} artifact(s) ignored BTC_MODEL_OUTPUT_DIR and wrote to "
              f"serving: {', '.join(guard['captured'])}")
        print(f"     output captured as a candidate; serving RESTORED: {', '.join(guard['restored'])}")
    manifest = {
        "created_utc": stamp,
        "candidate_dir": candidate_dir,
        "steps_ok": ok,
        "steps_run": run,
        "serving_digests_before": before["digests"],
        "serving_digests_after": {n: _digest(os.path.join(SERVING_DIR, n))
                                  for n in REFIT_ARTIFACTS},
        "candidate_digests": {n: _digest(os.path.join(candidate_dir, n))
                              for n in REFIT_ARTIFACTS},
        "guard": guard,
        "promoted": False,
        "promotion_note": ("A candidate is not a champion. Promotion requires the forward "
                           "evidence gate and an explicit, recorded decision."),
    }
    with open(os.path.join(candidate_dir, "candidate_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)

    print(f"\nDONE: {ok}/{run} steps succeeded. CANDIDATE written to {candidate_dir}.")
    print("  The live app is UNCHANGED. It previously hot-reloaded these five artifacts within")
    print("  ~30s with no challenger gate, so a position could open under one artifact and be")
    print("  managed under another, both recorded under a single logical name.")
    return 0 if run > 0 and ok == run else 1


def selftest():
    # dry-run plan builds without running anything; step scripts resolve to real paths or SKIP.
    steps = BACKFILL + REFIT
    assert len(BACKFILL) == 4
    assert {s for _, s, _, _ in REFIT} == {
        "train_signed_quantiles.py",
        "train_persistence_model.py",
        "train_path_forecaster.py",
        "train_fade_model.py",
        "train_round_state_heads.py",
    }, "cheap-head refit plan changed; review this self-test and the operator docs"
    present = [s for _, s, _, _ in steps if os.path.exists(os.path.join(BACKEND, s))]
    assert os.path.exists(os.path.join(BACKEND, "train_signed_quantiles.py")), "missing band trainer"
    print(f"auto_finetune self-test: ALL PASS ({len(present)}/{len(steps)} step scripts present)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        sys.exit(main())
