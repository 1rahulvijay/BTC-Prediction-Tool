"""P0-21: the server runs preflight itself. P0-23: a dead-but-open socket is detected.

P0-21  Preflight lived only in start_production.bat, while server.py creates the data
       directory and initialises the database on its own. `uvicorn backend.server:app` came
       up against whatever datastore it found with every safety check skipped, and reported
       healthy. A check that only runs when someone uses the right launcher is a convention,
       not a control.

P0-23  `ping_interval=None, ping_timeout=None` disables websockets' keepalive entirely. TCP
       can stay established while the venue sends nothing; with no ping there is nothing to
       notice. The task stays pending, the supervisor sees a live coroutine, and a dead feed
       looks like a quiet market.

    python backend/test_preflight_and_feed_keepalive.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
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
    import production_readiness as pr

    # ---- P0-21 --------------------------------------------------------------------------
    check(callable(getattr(pr, "preflight_issues", None)),
          "production_readiness exposes preflight_issues() - a FAST in-process check the "
          "server can run itself, distinct from main()'s subprocess gates")
    bad = pr.preflight_issues({"BTC_DEPLOYMENT_ENV": "development"})
    check(bad, "a non-production environment produces issues rather than passing silently")

    src = (BACKEND / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    lifespan = next((n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == "lifespan"), None)
    check(lifespan is not None, "the FastAPI lifespan exists")
    body = ast.unparse(lifespan)
    check("preflight_issues" in body,
          "and CALLS preflight_issues - so a direct uvicorn start cannot bypass it, which "
          "the .bat-only arrangement allowed")
    check(any(isinstance(n, ast.Raise) for n in ast.walk(lifespan)),
          "a failing preflight RAISES rather than logging and serving anyway - fail closed")
    # PARSED, and the test must be EXACTLY the production comparison. A substring check
    # passes when the branch is neutered to `DEPLOYMENT_ENV == "production" and False` -
    # the names all survive while the preflight never runs. That mutant survived the first
    # version of this test.
    guard = None
    for node in ast.walk(lifespan):
        if isinstance(node, ast.If) and "preflight_issues" in ast.unparse(node):
            if guard is None or node.lineno < guard.lineno:
                guard = node
    check(guard is not None, "the preflight call sits inside an `if`")
    test_src = ast.unparse(guard.test)
    check(isinstance(guard.test, ast.Compare),
          f"whose condition is a bare comparison ({test_src}) - a conjunction such as "
          f"`... and False` would disable preflight while every name still reads correctly")
    check("DEPLOYMENT_ENV" in test_src and "production" in test_src,
          f"comparing DEPLOYMENT_ENV to production ({test_src}), so development and research "
          f"runs are unaffected")

    # The preflight must run BEFORE the database is initialised - initialising first is how
    # a wrong datastore gets created and then declared fine.
    lines = body.splitlines()
    pre_at = next(i for i, ln in enumerate(lines) if "preflight_issues" in ln)
    db_at = next((i for i, ln in enumerate(lines) if "init_db" in ln), len(lines))
    check(pre_at < db_at,
          "and runs BEFORE database.init_db() - checking after the server has already "
          "created a datastore validates its own side effect")

    # ---- P0-23 --------------------------------------------------------------------------
    import data_ingestion as di
    check(getattr(di, "WS_PING_INTERVAL_S", 0) > 0 and getattr(di, "WS_PING_TIMEOUT_S", 0) > 0,
          f"websocket keepalive is ENABLED (ping {di.WS_PING_INTERVAL_S}s / timeout "
          f"{di.WS_PING_TIMEOUT_S}s) - both were None, which disables detection entirely")

    feed_src = (BACKEND / "data_ingestion.py").read_text(encoding="utf-8")
    check("ping_interval=None" not in feed_src and "ping_timeout=None" not in feed_src,
          "no connect site disables keepalive any more")
    check(feed_src.count("ping_interval=WS_PING_INTERVAL_S") >= 4,
          f"every websocket connect uses the shared constants "
          f"({feed_src.count('ping_interval=WS_PING_INTERVAL_S')} sites) - one site left on "
          f"None would be a silently undetectable feed")

    print(f"\nPREFLIGHT + FEED KEEPALIVE: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
