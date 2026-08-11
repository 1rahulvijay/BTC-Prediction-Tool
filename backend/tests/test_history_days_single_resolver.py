"""The training window is resolved ONCE, or the app cannot train at all.

    python backend/tests/test_history_days_single_resolver.py

THE DEFECT, OBSERVED RUNNING - not inferred from source

A direct `python backend/server.py` came up healthy, served 38 routes, and could never produce
a model:

    [BOOT] Startup warm-up begins. historical_days=30
    [BOOT 5/7] No compatible saved models found. Startup training is required.
    [BOOT] Background startup training failed: training-data identity contract failed before
           training: requested_days is missing; matrix requested_days=60 does not match
           requested_days=0

Three values for one setting inside one process:

    server.py       30    its own literal default
    matrix manifest 60    what the training data on disk actually is
    model.train()    0    _env_int("BTC_HISTORICAL_DAYS", _env_int("BTC_BACKFILL_DAYS", 0))

The same setting was read at nine sites with five different defaults (30, 0, 60, 360, "na") and
three different precedences. `model.py` even read HISTORICAL-then-BACKFILL when TRAINING and
BACKFILL-then-HISTORICAL when SAVING, so a single run could stamp two disagreeing identities.

`start.bat` hid it:

    if not defined BTC_MODEL_TRAINING_DAYS set "BTC_MODEL_TRAINING_DAYS=%BTC_HISTORICAL_DAYS%"

Alignment that only holds when someone uses the right launcher is a convention, not a control -
the same lesson as P0-21, where preflight lived only in the .bat.

The identity contract was never the bug and is not weakened here. Refusing to train when the
declared window disagrees with the matrix is correct; the repair is that nothing disagrees.
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import artifact_identity as ai  # noqa: E402

_OK = True
BACKEND = Path(__file__).resolve().parents[1]


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


class _Env:
    """Set/clear BTC_* window vars and restore them exactly, including absence."""

    def __init__(self, **kw):
        self.kw = kw
        self.old: dict[str, str | None] = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


ALL_OFF = dict.fromkeys(ai.HISTORY_DAYS_ENV_ORDER)


def code_only(path: Path) -> str:
    """Source with docstrings and comments stripped - this file's own prose quotes the removed
    expressions verbatim, and a substring check would match the fix's documentation."""
    src = path.read_text(encoding="utf-8")
    doc: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr):
            v = body[0].value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                doc.update(range(v.lineno, (v.end_lineno or v.lineno) + 1))
    return chr(10).join(ln for i, ln in enumerate(src.splitlines(), start=1)
                        if i not in doc and not ln.strip().startswith("#"))


def main() -> int:
    print("precedence is ONE order, and the first variable set wins")
    with _Env(**{**ALL_OFF, "BTC_BACKFILL_DAYS": 11}):
        chk(ai.resolve_history_days() == 11, "BACKFILL alone is honoured (11)")
    with _Env(**{**ALL_OFF, "BTC_HISTORICAL_DAYS": 22, "BTC_BACKFILL_DAYS": 11}):
        chk(ai.resolve_history_days() == 22,
            "HISTORICAL outranks BACKFILL (22, not 11) - the old save path had this backwards")
    with _Env(**{**ALL_OFF, "BTC_MODEL_TRAINING_DAYS": 33, "BTC_HISTORICAL_DAYS": 22,
                  "BTC_BACKFILL_DAYS": 11}):
        chk(ai.resolve_history_days() == 33, "MODEL_TRAINING_DAYS outranks both (33)")

    print("an unset environment falls back to the MANIFEST, never to a literal")
    manifest_days = int((ai.load_json(ai.MATRIX_MANIFEST_PATH) or {}).get("requested_days", 0) or 0)
    with _Env(**ALL_OFF):
        days, source = ai.resolve_history_days_verbose()
        if manifest_days > 0:
            chk(days == manifest_days and source.startswith("manifest:"),
                f"with nothing set, the window is the matrix's own {manifest_days}d "
                f"(source {source!r}) - NOT a hardcoded number that contradicts it")
            # THE REGRESSION ITSELF. 30 was server.py's literal and 0 was model.py's; either
            # one reproduces the failure that left the app permanently modelless.
            chk(days not in (0, 30) or manifest_days in (0, 30),
                "and it is neither of the values that broke it (0 from model.py, 30 from "
                "server.py) unless the manifest genuinely says so")
        else:
            chk(days == ai.HISTORY_DAYS_LAST_RESORT and source == "last_resort_default",
                "with no manifest either, the documented last resort is used and SAYS so")

    print("a malformed override refuses instead of silently becoming something else")
    with _Env(**{**ALL_OFF, "BTC_HISTORICAL_DAYS": "sixty"}):
        try:
            ai.resolve_history_days()
            raised = False
        except ValueError as exc:
            raised = "not an integer" in str(exc)
        chk(raised,
            "a non-numeric window raises rather than falling through to a different window "
            "than the operator asked for")
    with _Env(**{**ALL_OFF, "BTC_HISTORICAL_DAYS": ""}):
        chk(ai.resolve_history_days() > 0,
            "while an EMPTY value means 'unset' and falls through, as the shell produces it")

    print("the identity contract now passes where it previously refused")
    with _Env(**ALL_OFF):
        days = ai.resolve_history_days()
        identity = {
            "requested_days": days,
            "matrix_requested_days": manifest_days or days,
            "actual_span_days": days,
            "row_count": int(days * 1440),
        }
        # Scoped to the WINDOW issues on purpose. training_identity_issues also gates coverage,
        # monthly quality and four hashes, none of which a synthetic dict can satisfy and none of
        # which this fix touches. Asserting "no issues at all" would make this test fail for
        # reasons unrelated to the defect - which is how a green suite stops meaning anything.
        def window_issues(ident: dict) -> list[str]:
            return [i for i in ai.training_identity_issues(ident)
                    if "requested" in i or "span" in i or "row_count" in i]

        issues = window_issues(identity)
        chk(not issues,
            f"an unset environment yields a self-consistent WINDOW - this is the exact check "
            f"that aborted startup training, and it now has nothing to report ({issues})")
        chk(any("missing" in i for i in window_issues(dict(identity, requested_days=0))),
            "and the contract STILL refuses requested_days=0 - the guard was not weakened, "
            "the callers were fixed")
        chk(any("does not match" in i
                for i in window_issues(dict(identity, requested_days=(manifest_days or days) + 1))),
            "and a window that genuinely disagrees with the matrix is still caught")

    print("no consumer re-derives the window with its own default")
    server_code = code_only(BACKEND / "server.py")
    model_code = code_only(BACKEND / "model.py")
    chk('_env_int("BTC_HISTORICAL_DAYS", 30)' not in server_code,
        "server.py no longer carries a literal 30-day default")
    chk("resolve_history_days_verbose()" in server_code,
        "it uses the shared resolver, and keeps the SOURCE for the boot log")
    chk("HISTORICAL_DAYS_SOURCE" in server_code,
        "which is logged, so a window disagreeing with the manifest is visible at boot rather "
        "than 90 seconds later inside a worker thread")
    chk('_env_int(\n            "BTC_HISTORICAL_DAYS", _env_int("BTC_BACKFILL_DAYS", 0)\n        )'
        not in model_code and "BTC_BACKFILL_DAYS" not in model_code,
        "model.py reads no window environment variable directly, in either train() or save()")
    chk(model_code.count("resolve_history_days()") >= 2,
        "both the train path and the save path go through the same call, so what is SAVED "
        "describes the window that was TRAINED")

    print("\nHISTORY-DAYS SINGLE RESOLVER:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
