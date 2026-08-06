"""Four defects where a real event left no durable, truthful record.

    python backend/test_evidence_durability_and_provenance.py

Verified in source before being fixed. Each is the same shape as the rest of this backlog: the
system did the RIGHT thing and then failed to write down that it had.

1. A TERMINAL VERIFICATION STATE WAS NEVER PERSISTED  (scan-2 #17)
   A prediction past MAX_RESOLUTION_LATENESS_MS is correctly refused rather than graded against
   a price from the wrong moment. But `continue` skipped BOTH `still_pending` and
   `newly_verified`, and `newly_verified` is the only thing the server persists. The row left
   the verifier's memory and stayed PENDING in DuckDB - reconciled only by the next restart's
   orphan sweep, which stamps RESTART_MISSED_BOUNDARY: a cause that never applied.

2. THE ARTIFACT CODE HASH OMITTED SEMANTIC DEPENDENCIES  (scan-2 #21)
   The bundle hashed model.py, features.py and model_contract.py. A change to
   target_contract.py (which QUESTION the labels answer), regime.py (which expert routes a row)
   or calibration.py (what the served probability becomes) left an old artifact reading as
   "code compatible" - so the hash certified compatibility across exactly the edits most likely
   to invalidate it.

3. SPECIALIST LOADERS GAVE UP AFTER ONE TRANSIENT FAILURE  (scan-2 #29)
   `_BIGMOVE_CHECKED = True` was set BEFORE the load. A file not yet written by the trainer, a
   momentary lock or a partial write permanently disabled that head for the life of the
   process, and the only symptom was a head that quietly stopped contributing.

4. Recorded, not fixed: CoinGecko is stored under Chainlink names (scan-2 #30). See the
   assertion at the end - it pins the CURRENT state so the mislabel cannot spread further while
   the rename is pending.
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

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def code_only(path: Path) -> str:
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
    print("1. a late resolution becomes a TERMINAL record, not a silent disappearance")
    from prediction_verifier import PredictionVerifier

    v = PredictionVerifier()
    chk(isinstance(v.terminal_invalid, list) and not v.terminal_invalid,
        "the verifier exposes a terminal-invalid queue, empty at construction")

    late = {
        "id": "pred-late-1", "horizon": 5, "verify_at": 1_000_000,
        "direction": "UP", "confidence": 0.6, "predicted_price": 60000.0,
        "neutral_band": 0.0008, "timestamp": 999_000,
    }
    v.pending_predictions = [late]
    # A resolution far beyond the declared lateness bound.
    now = 1_000_000 + v.MAX_RESOLUTION_LATENESS_MS + 60_000
    verified = v.check_and_verify(60_100.0, now, klines=[])
    chk(not verified,
        "the row is NOT graded - refusing a price from the wrong moment is the correct half, "
        "and was never the defect")
    chk(not v.pending_predictions,
        "and it leaves the pending list, as before")
    chk(len(v.terminal_invalid) == 1
        and v.terminal_invalid[0]["id"] == "pred-late-1"
        and v.terminal_invalid[0]["horizon"] == 5
        and v.terminal_invalid[0]["reason"].startswith("INVALID_LATE"),
        f"but it is now QUEUED as terminal with its id, horizon and real reason "
        f"({v.terminal_invalid}) - previously it existed in neither list, so nothing persisted "
        f"it and the row stayed PENDING on disk")

    server_code = code_only(BACKEND / "server.py")
    chk("database.mark_predictions_terminal_invalid(" in server_code,
        "the server drains that queue into a terminal-state writer")
    chk("verifier.terminal_invalid = []" in server_code,
        "and empties it unconditionally, so the queue cannot grow without bound")

    import database
    chk(callable(getattr(database, "mark_predictions_terminal_invalid", None)),
        "the writer exists")
    chk(database.mark_predictions_terminal_invalid(5, [], "x") == 0
        and database.mark_predictions_terminal_invalid(7, ["a"], "x") == 0,
        "it is a no-op for an empty id list or an unknown horizon, and SAYS so by returning 0 "
        "rather than reporting success")

    print("2. the code hash covers every file that can change a prediction")
    import model
    from artifact_identity import hash_paths

    paths = model.SEMANTIC_CODE_PATHS()
    names = {Path(p).name for p in paths}
    for required in ("model.py", "features.py", "model_contract.py",
                     "target_contract.py", "regime.py", "calibration.py"):
        chk(required in names, f"{required} is hashed")
    chk(len(paths) >= 6, f"{len(paths)} files hashed, was 3")

    # DEMONSTRATED, not asserted: editing target_contract.py must move the hash. Under the old
    # three-file list it could not, which is the whole defect.
    before = hash_paths(paths)
    target = [p for p in paths if p.endswith("target_contract.py")][0]
    original = Path(target).read_bytes()
    try:
        Path(target).write_bytes(original + b"\n# provenance probe\n")
        after = hash_paths(paths)
    finally:
        Path(target).write_bytes(original)
    chk(before != after,
        "changing target_contract.py CHANGES the code hash - under the old list, altering "
        "which question the labels answer left the artifact reading as code compatible")
    chk(hash_paths(model.SEMANTIC_CODE_PATHS()) == before,
        "and the hash is restored once the probe is reverted, so this test leaves no trace")

    model_code = code_only(BACKEND / "model.py")
    chk(model_code.count("code_paths=SEMANTIC_CODE_PATHS()") >= 3,
        "all three identity sites (train and both save paths) share ONE declared list - each "
        "previously restated it and could drift from the others")

    print("3. a specialist head is pinned on a successful load, not on an attempt")
    ptb = code_only(BACKEND / "price_to_beat.py")
    tree = ast.parse((BACKEND / "price_to_beat.py").read_text(encoding="utf-8"))
    premature = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("_load_"):
            continue
        # A flag set at the FUNCTION BODY's top level (not inside the try) is the defect: it
        # fires whether or not the load succeeded.
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant) \
                    and stmt.value.value is True:
                for t in stmt.targets:
                    if isinstance(t, ast.Name) and t.id.endswith("_CHECKED"):
                        premature.append(f"{node.name}:{t.id}")
    chk(not premature,
        f"no _load_* function pins its _CHECKED flag before attempting the load ({premature})")
    chk(ptb.count("_CHECKED = True") >= 4,
        "the flags are still set - the fix moved them, it did not delete the load-once guard")
    for flag in ("_BIGMOVE_CHECKED", "_BIGDROP_CHECKED", "_DIRECTIONAL_CHECKED",
                 "_ACTIVITY_CHECKED"):
        idx = ptb.find(f"{flag} = True")
        chk(idx > 0 and "_verified_load(" in ptb[max(0, idx - 400):idx],
            f"{flag} is set only after a _verified_load call succeeds")

    print("4. the CoinGecko/Chainlink mislabel is pinned, pending rename")
    ingestion = (BACKEND / "data_ingestion.py").read_text(encoding="utf-8")
    chk("api.coingecko.com" in ingestion and "ChainlinkRESTClient" in ingestion,
        "RECORDED, NOT FIXED: a class named ChainlinkRESTClient fetches CoinGecko and the "
        "value lands in chainlink_price. Renaming touches persisted column names and every "
        "consumer, so it is left as one deliberate change rather than a drive-by edit here")
    chk("proxy" in ingestion.lower(),
        "the source at least DECLARES itself a proxy in the code, so the next reader is not "
        "misled into thinking this is a settlement-grade Chainlink observation")

    print("\nEVIDENCE DURABILITY AND PROVENANCE:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
