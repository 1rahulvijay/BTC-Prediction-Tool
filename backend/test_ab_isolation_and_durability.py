"""
A/B isolation and durability - scan-5 claims 5.13, 5.14, 5.15.

The three defects share one root: the A/B test was an experiment whose CONDITIONS were
not controlled. The challenger was conditioned on the incumbent's output and scored with
the incumbent's track record (5.13); the calendar clock the promotion gate measures
restarted with the process (5.14); and the map that attributes an outcome to the variant
that predicted it lived only in memory (5.15).

Run directly:  python backend/test_ab_isolation_and_durability.py
"""

import sys
import time
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FAILURES = []


def chk(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


class _RecordingModel:
    """Records exactly what each variant was handed at inference time."""

    def __init__(self, label, bundle_id="bundle_x"):
        self.label = label
        self.is_trained = True
        self.model_bundle_id = bundle_id
        self.calls = []

    def generate_ensemble_prediction(self, h, seq, data_state, acc_cache=None,
                                     cascade_data=None):
        self.calls.append({
            "h": h,
            "acc_cache": acc_cache,
            "cascade_data": cascade_data,
            "cascade_seen": dict(cascade_data) if isinstance(cascade_data, dict) else None,
        })
        return {"direction": "UP" if self.label == "chal" else "DOWN",
                "confidence": 0.6, "horizon": h}


class _FakeDB(types.SimpleNamespace):
    """Stands in for `database` - the runner must read its evidence from a store."""

    def __init__(self, evidence):
        super().__init__()
        self.evidence = evidence
        self.logged = []
        self.resolved = []

    def fetch_ab_variant_evidence(self, variant, model_bundle_id=""):
        return self.evidence.get((variant, model_bundle_id)) or self.evidence.get(
            variant, {"verified": 0, "hits": 0, "first_ts_ms": None, "unresolved": [],
                      "by_horizon": {}, "bundle_scoped": False, "scope_reason": "no_rows"})

    def fetch_ab_variant_stats(self):
        return {}

    def fetch_ab_paired_outcomes(self, *args):
        return []

    def fetch_ab_variant_profit_stats(self, *args):
        return {}

    def log_ab_prediction(self, *a, **k):
        self.logged.append((a, k))

    def resolve_ab_results(self, pred_id, actual):
        self.resolved.append((pred_id, actual))


def main():
    print("=" * 78)
    print("A/B ISOLATION AND DURABILITY (5.13 / 5.14 / 5.15)")
    print("=" * 78)

    import ab_testing
    from ab_testing import ABTestRunner, ModelVariant, RAW_MODEL_COMPARISON

    real_db = ab_testing.database

    # ------------------------------------------------------------------ 5.13
    print("\n5.13 the challenger is conditioned on ITSELF, not on the incumbent")
    prim_model = _RecordingModel("prim", "bundle_incumbent")
    chal_model = _RecordingModel("chal", "bundle_challenger")
    runner = ABTestRunner(primary=ModelVariant("prim", prim_model),
                          challenger=ModelVariant("chal", chal_model))
    runner.variant_accuracy["chal"] = {5: {"lean_accuracy": 0.55, "lean_total": 400}}

    # One server cycle: the server owns `cascade_data`, fills it AFTER policy, and passes
    # the SAME object to the runner for every horizon.
    server_cascade = {}
    primary_acc = {5: {"lean_accuracy": 0.99, "lean_total": 99999}}
    for h in (5, 15):
        p = runner.predict(h, None, {}, primary_acc, server_cascade)
        p = dict(p)
        p["finalDirection"] = "POST_POLICY"     # the server mutates in place, then:
        server_cascade[h] = p

    chal_15 = [c for c in chal_model.calls if c["h"] == 15][0]
    chk(chal_15["cascade_data"] is not server_cascade,
        "the challenger is not handed the server's cascade object")
    seen = chal_15["cascade_seen"] or {}
    chk(seen.get(5, {}).get("direction") == "UP",
        "its 15m input is its OWN 5m call (UP), not the incumbent's DOWN - the cascade "
        "reads cascade_data[5]['direction'] to bias 15m probabilities, so sharing the "
        "dict made the challenger's forecast partly the incumbent's")
    chk("finalDirection" not in seen.get(5, {}),
        "and it is the challenger's RAW output, not a post-policy record")
    chk(chal_15["acc_cache"] is not primary_acc
        and (chal_15["acc_cache"] or {}).get(5, {}).get("lean_total") == 400,
        "the cascade's skill gate reads the CHALLENGER's own directional record (400 "
        "leans), not the incumbent's 99999 - the gate only fires on demonstrated skill, "
        "and skill is not transferable between models")

    prim_15 = [c for c in prim_model.calls if c["h"] == 15][0]
    chk(prim_15["cascade_data"] is server_cascade and prim_15["acc_cache"] is primary_acc,
        "the primary's own inputs are untouched")

    print("\n     ... and a challenger with no record of its own goes INERT, not borrowed")
    chal2 = _RecordingModel("chal2", "b2")
    r2 = ABTestRunner(primary=ModelVariant("prim", _RecordingModel("prim", "b1")),
                      challenger=ModelVariant("chal2", chal2))
    r2.predict(5, None, {}, primary_acc, {})
    chk(chal2.calls[0]["acc_cache"] is None,
        "absent evidence is passed as absent - the model reads a missing lean_accuracy "
        "as unknown and declines to cascade, which is the true answer")
    chk(r2.last_by_horizon[5]["challenger_evidence"] == "none_cascade_inert",
        "and the asymmetry is RECORDED - a promotion decision must know it compared "
        "cascade-active against cascade-inert rather than assume parity")

    print("\n     ... and the challenger's cascade expires on the SERVER's cycle boundary")
    runner.predict(15, None, {}, primary_acc, {})    # a NEW cycle dict
    chk(5 not in runner.challenger_cascade,
        "a new cycle clears it - otherwise a cycle where the challenger's 5m call failed "
        "would leave the PREVIOUS cycle's 5m conditioning its 15m, a staleness the "
        "primary can never suffer because the server rebuilds its dict each cycle")

    # ------------------------------------------------------------------ 5.14
    print("\n5.14 the evidence clock survives a restart - when the evidence is identified")
    now_ms = int(time.time() * 1000)
    forty_days_ago = now_ms - 40 * 86400 * 1000
    ab_testing.database = _FakeDB({
        ("chal", "bundle_challenger"): {
            "verified": 900, "hits": 500, "first_ts_ms": forty_days_ago,
            "unresolved": [], "by_horizon": {}, "bundle_scoped": True,
            "scope_reason": "bundle"},
    })
    try:
        v = ModelVariant("chal", _RecordingModel("chal", "bundle_challenger"))
        r3 = ABTestRunner(primary=None, challenger=v)
        before = v.get_stats()["live_days"]
        r3.restore_from_db()
        after = v.get_stats()["live_days"]
        chk(before < 0.01 and after > 39.0,
            f"a restart began the clock at 0.0 days and it is restored to {after:.1f} - "
            f"`simulated_live_days` is measured from `started_at`, so a min_live_days=30 "
            f"gate could never be reached by a process restarted more often than monthly")
        chk(v.started_at_source == "db_first_prediction",
            "and the stats record that the clock was RESTORED rather than started now")

        v.started_at = forty_days_ago / 1000.0 - 100.0
        r3.restore_from_db()
        chk(v.started_at < forty_days_ago / 1000.0,
            "restore never moves the clock forward - a later restart must not shorten an "
            "evidence window a caller already established")
    finally:
        ab_testing.database = real_db

    print("\n     ... but an UNIDENTIFIED record earns no calendar credit")
    ab_testing.database = _FakeDB({
        ("chal", "bundle_new"): {
            "verified": 900, "hits": 500, "first_ts_ms": forty_days_ago,
            "unresolved": [], "by_horizon": {}, "bundle_scoped": False,
            "scope_reason": "legacy_rows_unstamped"},
    })
    try:
        v2 = ModelVariant("chal", _RecordingModel("chal", "bundle_new"))
        ABTestRunner(primary=None, challenger=v2).restore_from_db()
        chk(v2.get_stats()["live_days"] < 0.01,
            "a record that cannot be tied to THIS model does not date it - crediting a "
            "fresh challenger with a predecessor's age is worse than making it wait")
        chk(v2.evidence_scope == "legacy_rows_unstamped",
            "and the scope is reported, so an unscoped number is never read as a scoped one")
    finally:
        ab_testing.database = real_db

    # ------------------------------------------------------------------ 5.15
    print("\n5.15 in-flight attribution survives a restart")
    ab_testing.database = _FakeDB({
        ("prim", "b1"): {"verified": 0, "hits": 0, "first_ts_ms": None,
                         "unresolved": [("pred_open", "DOWN")], "by_horizon": {},
                         "bundle_scoped": True, "scope_reason": "bundle"},
        ("chal", "b2"): {"verified": 0, "hits": 0, "first_ts_ms": None,
                         "unresolved": [("pred_open", "UP")], "by_horizon": {},
                         "bundle_scoped": True, "scope_reason": "bundle"},
    })
    try:
        pv = ModelVariant("prim", _RecordingModel("prim", "b1"))
        cv = ModelVariant("chal", _RecordingModel("chal", "b2"))
        r4 = ABTestRunner(primary=pv, challenger=cv)
        r4.restore_from_db()
        chk(r4.pending.get("pred_open") == {"prim": "DOWN", "chal": "UP"},
            "predictions open at shutdown are reopened from the store, both sides")
        r4.resolve("pred_open", "UP")
        chk(len(cv.verified) == 1 and cv.total_correct == 1 and len(pv.verified) == 1
            and pv.total_correct == 0,
            "so the outcome still lands on the variant that made the call - before this, "
            "`pending` was memory-only: DuckDB resolved the row and the in-memory "
            "counters the min_verified gate reads never saw it")
    finally:
        ab_testing.database = real_db

    # ------------------------------------------------------------ P1-C memory
    print("\nP1-C the A/B buffers are bounded, and the COUNTS stay exact")
    from ab_testing import RECENT_RETAINED

    class _Trivial:
        is_trained = True
        model_bundle_id = "b"

        def generate_ensemble_prediction(self, *a, **k):
            return {"direction": "UP", "confidence": 0.6}

    mv = ModelVariant("v", _Trivial())
    for i in range(5000):
        mv.predict(5, None, {}, None, None)
        mv.record_outcome(i % 3 == 0)
    chk(len(mv.predictions) == RECENT_RETAINED and len(mv.verified) == RECENT_RETAINED,
        f"5,000 cycles retain {RECENT_RETAINED} - `predictions` held the FULL prediction "
        f"dict per cycle per horizon per variant (~4 KB each, ~19 MB/hour, ~3.2 GB/week) "
        f"and nothing ever read it")
    chk(mv.total_predictions == 5000 and mv.total_verified == 5000,
        "while the counts remain exact and unbounded - the code only ever wanted len()")
    chk(abs(mv.accuracy - 1667 / 5000) < 1e-6,
        f"and accuracy divides by EVERY outcome ({mv.accuracy:.4f}), not by the retained "
        f"tail - bounding a buffer must not silently narrow the denominator a promotion "
        f"gate reads")

    r6 = ABTestRunner(primary=mv, challenger=None)
    r6.total_comparisons, r6.total_agreements = 900, 700
    r6.reset_comparisons()
    chk(r6.total_comparisons == 0 and r6.total_agreements == 0 and not r6.comparison_log,
        "and replacing a challenger resets the aggregates with the log - clearing only the "
        "list would carry the previous challenger's agreement rate into the new one, which "
        "is 5.14's identity defect one attribute over")
    srv6 = (BACKEND / "server.py").read_text(encoding="utf-8")
    chk("comparison_log.clear()" not in srv6 and "reset_comparisons()" in srv6,
        "so both call sites use the reset rather than the list operation")

    # ------------------------------------------------------------- disclosure
    print("\nthe verdict carries what the experiment was NOT")
    ab_testing.database = _FakeDB({})
    try:
        cv2 = ModelVariant("chal", _RecordingModel("chal", "b2"))
        r5 = ABTestRunner(primary=ModelVariant("prim", _RecordingModel("prim", "b1")),
                          challenger=cv2)
        comp = r5.get_comparison()
        ei = comp.get("evidence_integrity") or {}
        chk(ei.get("comparison_basis") == RAW_MODEL_COMPARISON
            and ei.get("challenger_cascade") == "isolated_from_primary"
            and "challenger_cascade_evidence" in ei
            and "challenger_clock_source" in ei,
            "every parity the test does NOT have is named beside the numbers, because a "
            "promotion decision that reads the verdict alone claims an experiment that "
            "was never run")
    finally:
        ab_testing.database = real_db

    print("\n" + "=" * 78)
    if FAILURES:
        print(f"A/B ISOLATION AND DURABILITY: FAIL ({len(FAILURES)})")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("A/B ISOLATION AND DURABILITY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
