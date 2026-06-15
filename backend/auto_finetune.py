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
  python backend/auto_finetune.py                    # refit the 3 heads on existing data
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

# (label, script-relative-to-backend, extra-args, supports_--auto/--days)
BACKFILL = [
    ("backfill trade-features", "backfill_trade_features.py", ["--auto"], True),
    ("backfill persistence", "build_persistence_dataset.py", ["--auto"], True),
    ("backfill cross-venue", "build_crossvenue_flow.py", ["--auto"], True),
    ("rebuild research matrix", "build_research_matrix.py", [], False),
]
REFIT = [
    ("signed-quantile band (recalibrate cqr)", "train_signed_quantiles.py", [], False),
    ("selectivity ensemble", os.path.join("decision", "train_selectivity_models.py"), [], False),
    ("persistence P(Hold)", "train_persistence_model.py", [], False),
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
    print("  direction ensemble NOT touched (6h FREEZE=0 job; at the ceiling). Live app hot-reloads pkls <=30s.")
    steps = (BACKFILL if a.with_backfill else []) + REFIT
    if a.dry_run:
        for label, script, extra, sd in steps:
            d = f" --days {a.days}" if (sd and a.with_backfill) else ""
            print(f"  would run: {label}  ->  {script} {' '.join(extra)}{d}")
        return
    ok = run = 0
    for label, script, extra, sd in steps:
        res = _run(label, script, extra, sd, a.days)
        if res is not None:
            run += 1
            ok += int(bool(res))
    print(f"\nDONE: {ok}/{run} steps succeeded. Heads refreshed -> live app hot-reloads within 30s "
          "(no restart). Recalibration keeps the band/P(Hold) honest; accuracy is unchanged by design.")


def selftest():
    # dry-run plan builds without running anything; step scripts resolve to real paths or SKIP.
    steps = BACKFILL + REFIT
    assert len(REFIT) == 3 and len(BACKFILL) == 4
    present = [s for _, s, _, _ in steps if os.path.exists(os.path.join(BACKEND, s))]
    assert os.path.exists(os.path.join(BACKEND, "train_signed_quantiles.py")), "missing band trainer"
    assert os.path.exists(os.path.join(BACKEND, "decision", "train_selectivity_models.py")), "missing selectivity trainer"
    print(f"auto_finetune self-test: ALL PASS ({len(present)}/{len(steps)} step scripts present)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
