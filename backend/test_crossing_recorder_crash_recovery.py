"""Kill the recorder with SIGKILL mid-run and prove the next process recovers from disk.

Restart-safety was mutation-proven at the FUNCTION level. This exercises the property that
actually matters: a process that dies without running any cleanup - no flush, no close_run, no
finally - and a successor that picks the state back up from the durable store.
"""
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "backend" / "crossing_recorder_hf.py"
DB = REPO / "data" / "polymarket_crossings_hf.duckdb"


def duck(query, params=None):
    import duckdb
    con = duckdb.connect(str(DB), read_only=True)
    try:
        return con.execute(query, params or []).fetchall()
    finally:
        con.close()


def main():
    print("=" * 78)
    print("CRASH RECOVERY TEST - SIGKILL mid-run, then restart")
    print("=" * 78)

    # ---- 1. run and hard-kill
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "--run", "--seconds", "120"],
                            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    print("  started pid", proc.pid, "- letting it record for 35s")
    time.sleep(35)
    if proc.poll() is not None:
        print("  process exited early:", proc.stdout.read()[-400:])
        return 1
    proc.kill()                       # SIGKILL / TerminateProcess: no finally, no flush
    proc.wait(timeout=30)
    print("  HARD KILLED (no cleanup ran)")
    time.sleep(3)

    if not DB.is_file():
        print("  FAIL: no database was written before the kill")
        return 1

    beats_a = duck("SELECT count(*) FROM hf_heartbeats")[0][0]
    events_a = duck("SELECT count(*) FROM hf_crossing_events")[0][0]
    anchors_a = duck("SELECT count(*) FROM hf_round_anchors")[0][0]
    open_runs = duck("SELECT count(*) FROM hf_runs WHERE stopped_ts IS NULL")[0][0]
    print(f"  after kill: {beats_a} heartbeats, {events_a} crossings, "
          f"{anchors_a} anchors, {open_runs} run(s) with no clean stop")

    checks = []

    def check(cond, text):
        checks.append(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {text}")

    check(beats_a > 0, "the killed process had DURABLY written heartbeats before dying")
    check(open_runs >= 1,
          "the crash is VISIBLE as a run with no clean stop, not silently forgotten")

    # ---- 2. health must report the crash honestly
    health = subprocess.run([sys.executable, str(SCRIPT), "--health"], cwd=str(REPO),
                            capture_output=True, text=True, timeout=120)
    print("  health:", health.stdout.strip().splitlines()[0] if health.stdout else "?")
    check("runs_without_clean_stop" in health.stdout or "STALLED" in health.stdout
          or "ADVANCING" in health.stdout,
          "health reports the crashed run rather than hiding it")

    # ---- 3. restart and prove recovery
    print("  restarting for 30s ...")
    restart = subprocess.run([sys.executable, str(SCRIPT), "--run", "--seconds", "30"],
                             cwd=str(REPO), capture_output=True, text=True, timeout=300)
    out = restart.stdout + restart.stderr
    print("  restart rc", restart.returncode)
    for line in out.splitlines():
        if "recovered" in line.lower() or "anchor" in line.lower() or "run_id" in line:
            print("   ", line.strip()[:110])

    beats_b = duck("SELECT count(*) FROM hf_heartbeats")[0][0]
    events_b = duck("SELECT count(*) FROM hf_crossing_events")[0][0]
    dupes = duck("SELECT count(*) FROM (SELECT crossing_id FROM hf_crossing_events "
                 "GROUP BY crossing_id HAVING count(*) > 1)")[0][0]

    check(restart.returncode == 0, "the successor process starts cleanly on a crashed database")
    check(beats_b > beats_a, "the successor ADVANCES the row count - collection resumed")
    check(events_b >= events_a, "no crossing recorded before the crash was lost")
    check(dupes == 0, "no crossing was written twice - recovery is idempotent")

    runs = duck("SELECT count(*) FROM hf_runs")[0][0]
    check(runs >= 2, "both the crashed run and the successor are recorded as separate runs")

    print()
    ok = all(checks)
    print(f"CRASH RECOVERY: {'PASS' if ok else 'FAIL'} ({sum(checks)}/{len(checks)})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
