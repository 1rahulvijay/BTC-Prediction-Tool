"""
auto_finetune.py - nightly REFIT + RECALIBRATE of the cheap heads (no GPU, no feed freeze).
=============================================================================================
Keeps the conformal band + P(Hold) + selectivity calibrated to the RECENT regime as new data flows
in. Reruns only the cheap head trainers (seconds-minutes each, single-thread, no GPU).

OUTPUT IS A CANDIDATE, NOT A DEPLOY (2026-08-05):
  Every trainer is redirected to data/model_candidates/<UTC-stamp>/ - a SIBLING of the serving
  directory, not a child of it - and the serving artifacts are left untouched. Until this
  change the job REWROTE the five serving .pkls in place and the live app hot-reloaded them
  within ~30s with no challenger gate, so a position could open under one artifact and be
  managed under another, both recorded under a single logical name.

  The redirect is verified, not trusted, and verified AFTER EVERY TRAINER (2026-08-06).
  Checking only in `finally` proved serving was correct when the job finished; it did not
  bound how long a wrong artifact was reachable by the ~30s reloader while later trainers ran.
  Any mutation now aborts the run immediately after the trainer that caused it.

  One run at a time, enforced by an exclusive lock: two concurrent jobs would each snapshot
  the other's intermediate bytes as "the original" and restore each other's mistakes.

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

from artifact_identity import resolve_history_days
from training_pipeline_lease import acquire as acquire_pipeline_lease
from training_pipeline_lease import release as release_pipeline_lease

BACKEND = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BACKEND)
DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
SERVING_DIR = os.path.join(DATA_DIR, "saved_models")
#: Where a nightly run puts its OUTPUT. Deliberately OUTSIDE saved_models: the live app
#: reloads artifacts by scanning the serving directory, so a candidate written beneath it
#: could be picked up by a path glob or an operator copy. A sibling directory cannot be.
CANDIDATE_ROOT = os.path.join(DATA_DIR, "model_candidates")
#: Held for the whole run so two nightly jobs cannot interleave trainers over one serving dir.
LOCK_PATH = os.path.join(DATA_DIR, "model_candidates", ".refit.lock")
PY = sys.executable

# The matrix + cheap heads recalibrate on the SAME window as the main retrain, so every head stays
# consistent with the canonical matrix and serving bundle. The scheduled task has no launcher
# environment, so a literal fallback (formerly 360) can overwrite a wider matrix during a retrain.
# The shared resolver uses the explicit model/history environment first and otherwise the matrix
# manifest, which is the only durable statement of the currently requested training window.
FULL_DAYS = resolve_history_days()

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


def _acquire_lock():
    """Exclusive local candidate lock with dead-owner recovery."""
    return acquire_pipeline_lease(
        "nightly_candidate_bundle", days=FULL_DAYS, path=LOCK_PATH
    )


def _release_lock(lock) -> None:
    if lock:
        release_pipeline_lease(lock, path=LOCK_PATH)


def serving_mutations(before) -> list:
    """Serving artifacts whose bytes differ from the pre-run snapshot. Cheap enough to run
    after every trainer, which is the point - a check that only runs at the end cannot bound
    how long a wrong artifact was reachable by the live reloader."""
    return [name for name in REFIT_ARTIFACTS
            if _digest(os.path.join(SERVING_DIR, name)) != before["digests"].get(name)]


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
    lock = _acquire_lock()
    if lock is None:
        print(f"  REFUSED: another refit holds {LOCK_PATH}. Two concurrent runs would each "
              f"snapshot the other's intermediate bytes as the original.")
        return 2
    pipeline_lease = acquire_pipeline_lease(
        "nightly_recalibration", days=FULL_DAYS
    )
    if pipeline_lease is None:
        _release_lock(lock)
        print(
            "  SKIPPED: a full retrain or another canonical-data job owns the training "
            "pipeline lease. Nightly calibration will try again at its next schedule."
        )
        return 0
    before = None
    guard = {"captured": [], "restored": [], "unchanged": []}
    ok = run = 0
    try:
        before = snapshot_serving()
        os.environ["BTC_MODEL_OUTPUT_DIR"] = candidate_dir
        print(f"  output -> {candidate_dir}")
        print("  serving artifacts are NOT modified; promotion is a separate, gated act.")
        for label, script, extra, sd in steps:
            res = _run(label, script, extra, sd, a.days)
            if res is not None:
                run += 1
                ok += int(bool(res))
            # VERIFY AFTER EVERY TRAINER, not once at the end.
            #
            # Restoring in `finally` proved serving was correct when the job FINISHED. It did
            # not prove the live process never saw the intermediate bytes: the app reloads on
            # mtime within ~30s, and the remaining trainers can run for minutes. A temporary
            # overwrite was therefore a temporarily SERVED model. Checking here shrinks the
            # exposure to one trainer and aborts instead of continuing.
            _touched = serving_mutations(before)
            if _touched:
                guard_now = protect_serving(before, candidate_dir)
                raise SystemExit(
                    f"ABORTED: '{label}' wrote to the serving directory ({', '.join(_touched)})"
                    f" despite BTC_MODEL_OUTPUT_DIR. Serving was restored immediately "
                    f"({', '.join(guard_now['restored']) or 'nothing to restore'}) and the run "
                    f"stopped rather than leaving further trainers to run against a serving "
                    f"directory that has already been mutated once.")
    finally:
        # Runs on failure and on interrupt too: a half-finished job must never leave a
        # partially-rewritten serving directory behind.
        if before is not None:
            guard = protect_serving(before, candidate_dir)
        release_pipeline_lease(pipeline_lease)
        _release_lock(lock)

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
