"""
auto_finetune.py - nightly REFIT + RECALIBRATE of the cheap heads (no GPU, no feed freeze).
=============================================================================================
Keeps the conformal band + P(Hold) + selectivity calibrated to the RECENT regime as new data flows
in. Reruns only the cheap head trainers (seconds–minutes each, single-thread, no GPU). The live app
HOT-RELOADS the refreshed .pkls within ~30s (mtime reload in price_to_beat) - **no restart needed**.

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
import os
import subprocess
import sys
import time

BACKEND = os.path.dirname(os.path.abspath(__file__))
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
    print("  direction ensemble NOT touched (6h FREEZE=0 job; at the ceiling). Live app hot-reloads pkls <=30s.")
    steps = (BACKFILL if a.with_backfill else []) + REFIT
    if a.dry_run:
        for label, script, extra, sd in steps:
            d = f" --days {a.days}" if (sd and a.with_backfill) else ""
            print(f"  would run: {label}  ->  {script} {' '.join(extra)}{d}")
        return 0
    ok = run = 0
    for label, script, extra, sd in steps:
        res = _run(label, script, extra, sd, a.days)
        if res is not None:
            run += 1
            ok += int(bool(res))
    print(f"\nDONE: {ok}/{run} steps succeeded. Heads refreshed -> live app hot-reloads within 30s "
          "(no restart). Recalibration keeps the band/P(Hold) honest; accuracy is unchanged by design.")
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
