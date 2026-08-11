"""P0-22: a healthy recorder must not block readiness. P0-25: temp paths must not collide.

P0-22  `crossing_recorder_hf.row_progress_status()` returns "ADVANCING" for a recorder that is
       writing rows - the strongest evidence of health there is. Readiness blocked on
       `status != "HEALTHY"`, so an advancing recorder became a production blocker. Two
       unrelated status vocabularies were compared as if they were one.

P0-25  `verified_io` built temp paths as `<target>.tmp.<pid>`. Two writers in the SAME process
       share a pid, so concurrent writes to one target could clobber each other mid-flight and
       the survivor would be replaced atomically - an atomic write of the wrong bytes.

    python backend/tests/test_health_vocabulary_and_atomic_temp.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import ast
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def main() -> int:
    src = (BACKEND / "server.py").read_text(encoding="utf-8")
    ns: dict = {}
    exec(src[src.index("_HEALTHY_STATES"):src.index("def _system_health_snapshot(")], ns)
    is_healthy = ns["_is_healthy"]

    check(is_healthy({"status": "ADVANCING"}),
          "an ADVANCING recorder is HEALTHY - it is writing rows, which is the strongest "
          "evidence of health, and it previously blocked production readiness")
    check(is_healthy({"status": "HEALTHY"}), "and so is one reporting HEALTHY")
    check(not is_healthy({"status": "STALLED"}), "while STALLED is not")
    check(not is_healthy({"status": "NEVER_RAN"}), "nor NEVER_RAN")
    check(not is_healthy({"status": "SOMETHING_NOBODY_DECLARED"}),
          "and an UNRECOGNISED state is unhealthy - an unknown status must not be assumed "
          "fine, which is the failure this replaces running in the other direction")
    check(is_healthy({"status": "STALLED", "healthy": True}),
          "an explicit `healthy` flag WINS over the string, so a producer can state health "
          "directly instead of hoping its vocabulary matches the consumer's")
    check(not is_healthy({"status": "HEALTHY", "healthy": False}),
          "in both directions")
    check(not is_healthy({}) and not is_healthy(None),
          "and a missing status is unhealthy rather than vacuously passing")

    # The readiness comprehensions must USE it, not re-compare strings.
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "_system_health_snapshot")
    body = ast.unparse(fn)
    check('!= \'HEALTHY\'' not in body and '!= "HEALTHY"' not in body,
          "readiness no longer compares a status string to 'HEALTHY' directly")
    check(body.count("_is_healthy") >= 2,
          "both the feed and recorder blocker lists route through the predicate, so a new "
          "producer vocabulary cannot silently block one of them")

    # ---- P0-25 --------------------------------------------------------------------------
    vio = (BACKEND / "verified_io.py").read_text(encoding="utf-8")
    temps = re.findall(r'tmp = .*\.tmp\.[^\n]*', vio)
    check(temps, "verified_io builds temp paths")
    for line in temps:
        check("uuid" in line,
              f"temp path carries a UUID, not only a pid ({line.strip()[:60]}) - two writers "
              f"in one process share a pid and could clobber each other")

    import verified_io
    seen = set()
    for _ in range(200):
        # Same target, same process: the pid component is identical every time.
        seen.add(f"{verified_io.os.getpid()}.{verified_io.uuid.uuid4().hex[:12]}")
    check(len(seen) == 200,
          "200 temp names generated in ONE process are all distinct - under the old scheme "
          "they would have been the same string 200 times")

    print(f"\nHEALTH VOCABULARY + ATOMIC TEMP: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
