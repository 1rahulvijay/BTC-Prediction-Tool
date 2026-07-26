"""Real DuckDB round trip: write -> read -> resolve -> evaluate. No hand-built rows.

WHY THIS FILE EXISTS
    The forward evaluator's own selftest built complete row dictionaries by hand and passed. The
    actual `read_forward_rows()` returned only hashes and timestamps - no `seconds_left`, no
    `score` - so `causal_selection()` defaulted both to 0 and NO database-loaded row could ever
    clear a positive threshold. The evaluator was green and could not read its own database.

    Synthetic fixtures test the function. Only a round trip tests the SYSTEM. Every row here goes
    through `log_forward_prediction_v2()` and comes back through `read_forward_rows()`.

    python -m backend.trade_forecast.test_ledger_v2_end_to_end
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

# A private data dir BEFORE importing anything that resolves DB paths at import time.
_TMP = tempfile.mkdtemp(prefix="ledger_v2_e2e_")
os.environ["BTC_DATA_DIR"] = _TMP

from .evaluate_complete_trade_m0_v2_forward import evaluate            # noqa: E402
from .forward_evidence import ThresholdArtifact                        # noqa: E402
from .trade_forecast_logger import (                                   # noqa: E402
    connect,
    log_forward_prediction_v2,
    read_forward_rows,
    read_resolved_outcomes,
)
from .trade_outcome_resolver import resolve_v2                         # noqa: E402
from .trade_schema import M0_V2                                        # noqa: E402

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


MODEL = "m" * 64
POLICY = "p" * 64
SCHEMA = "f" * 64


def _threshold() -> ThresholdArtifact:
    return ThresholdArtifact(
        threshold=0.70, objective="P(plan net>0)", target_entry_rate=0.20,
        calibration_start_ts=1.0, calibration_end_ts=2.0, calibration_rows=5000,
        dataset_sha256="d" * 64, model_sha256=MODEL, policy_sha256=POLICY,
        code_sha256="c" * 64,
    )


def _write_ledger(rounds: int, artifact: ThresholdArtifact) -> tuple[str, dict]:
    """Write a full evidence run through the REAL writer, on one shared connection."""
    shared = connect()
    freeze = float(M0_V2["frozen_at_s"])
    run_id = "run_" + artifact.threshold_hash()[:16]
    settled: dict[str, dict] = {}
    span = 63 * 86400.0
    for i in range(rounds):
        stamp = freeze + 3600.0 + (i / rounds) * span
        # Three checkpoints; the 90s one is the first to clear 0.70.
        for left, score in ((120, 0.90 if i % 3 else 0.10), (90, 0.90), (60, 0.99)):
            fid = f"fc_{i}_{left}"
            row = {
                "forecast_id": fid, "round_id": f"rd_{i}",
                "exposure_id": f"rd_{i}@{left}", "seconds_left": left,
                "side": "UP", "requested_qty": 5.0,
                "prediction_ts_ms": int(stamp * 1000), "prediction_ts_s": stamp,
                "model_bundle_sha256": MODEL, "feature_schema_sha256": SCHEMA,
                "policy_sha256": POLICY, "threshold_sha256": artifact.threshold_hash(),
                "prereg_sha256": M0_V2["prereg_sha256"],
                "feature_values_sha256": f"v{i}_{left}",
                "prereg_frozen_at_s": freeze, "model_frozen_at_s": freeze,
                "threshold_frozen_at_s": artifact.created_at,
                "entry_threshold": artifact.threshold, "score": score,
                "action": "BUY_UP", "predicted_entry_vwap": 0.60,
                "exit_plan": "TAKE_3C_OR_STOP_3C", "reason_codes_json": "[]",
                "evidence_source": "l2_recorder", "evidence_run_id": run_id,
            }
            assert log_forward_prediction_v2(row, conn=shared), f"write failed for {fid}"
        net = 0.03 if i % 3 else -0.01
        settled[f"rd_{i}"] = {
            "resolution_source": "polymarket_clob", "settled_side": 1,
            "entry_filled": True, "entry_vwap": 0.60, "plan_net": net,
            "plan_exit_kind": "TARGET", "plan_holding_s": 12.0,
            "stress_1000ms_plan_net": net * 0.9,
            "candidate_pnls": [net, -0.02, 0.01, -0.01],
            "resolved_at_s": stamp + 300.0,
        }
    shared.close()
    return run_id, settled


def run() -> int:
    artifact = _threshold()
    rounds = 1200

    print("write -> read")
    run_id, settled = _write_ledger(rounds, artifact)
    rows = read_forward_rows()
    chk(len(rows) == rounds * 3, f"all {rounds * 3} predictions read back ({len(rows)})")

    # THE BUG THIS FILE EXISTS FOR.
    first = rows[0]
    for field in ("seconds_left", "score", "side", "requested_qty", "action",
                  "entry_threshold", "exit_plan", "exposure_id", "evidence_run_id"):
        chk(first.get(field) is not None,
            f"reader returns '{field}' (the policy executes on it)")

    print("causal selection over DATABASE rows")
    from .evaluate_complete_trade_m0_v2_forward import causal_selection

    picked = causal_selection(rows, artifact.threshold)
    chk(len(picked) == rounds, f"one trade per round from the DB ({len(picked)})")
    # The fixture is deliberately mixed: 2/3 of rounds have a qualifying 120s checkpoint, 1/3
    # do not. The causal rule must take 120s where it qualifies and fall through to 90s where it
    # does not - and must NEVER take 60s, which always carries the round's HIGHEST score (0.99).
    # Picking 60s would be the hindsight maximum this rule exists to prevent.
    chosen = {int(p["seconds_left"]) for p in picked}
    chk(chosen == {120, 90}, f"entries occur at 120s or 90s ({sorted(chosen)})")
    chk(60 not in chosen,
        "NEVER enters at 60s despite it holding the highest score in every round")
    by_left = {}
    for p in picked:
        by_left[int(p["seconds_left"])] = by_left.get(int(p["seconds_left"]), 0) + 1
    chk(by_left.get(90) == rounds // 3,
        f"the {rounds // 3} rounds whose 120s did not qualify fell through to 90s "
        f"({by_left.get(90)})")
    chk(by_left.get(120) == rounds - rounds // 3,
        f"the rest entered at the earliest qualifying checkpoint ({by_left.get(120)})")

    print("resolve")
    report = resolve_v2(settled)
    chk(report["written"] == rounds * 3, f"outcomes written ({report['written']})")
    outcomes = read_resolved_outcomes()
    chk(len(outcomes) == rounds * 3, "outcomes read back")

    print("evaluate")
    result = evaluate(rows, outcomes, artifact)
    chk(result["status"] == "SCORED",
        f"a complete evidence run SCORES from the database ({result['status']}) "
        f"{result.get('blockers') or ''}")
    chk(result.get("passed") is True, "and this profitable fixture passes")
    chk(result.get("trades") == rounds, f"{rounds} trades scored")

    print("refusals, end to end")
    partial = dict(outcomes)
    partial.pop(picked[0]["forecast_id"])
    verdict = evaluate(rows, partial, artifact)
    chk(verdict["status"] == "NOT_READY",
        "ONE unresolved selected trade -> NOT_READY, never a partial pass")

    broken = {k: dict(v) for k, v in outcomes.items()}
    broken[picked[1]["forecast_id"]]["plan_net"] = None
    chk(evaluate(rows, broken, artifact)["status"] == "NOT_READY",
        "a missing plan_net is NOT_READY, never defaulted to 0.0")

    foreign = ThresholdArtifact(
        threshold=0.70, objective="other", target_entry_rate=0.20,
        calibration_start_ts=1.0, calibration_end_ts=2.0, calibration_rows=5000,
        dataset_sha256="d" * 64, model_sha256="z" * 64, policy_sha256=POLICY,
        code_sha256="c" * 64)
    chk(evaluate(rows, outcomes, foreign)["status"] == "IDENTITY_MISMATCH",
        "a threshold derived from a DIFFERENT model is refused")

    mixed = [dict(r) for r in rows]
    mixed[0]["evidence_run_id"] = "another_run"
    chk(evaluate(mixed, outcomes, artifact)["status"] == "IDENTITY_MISMATCH",
        "evidence spanning two run ids is refused")

    print("zero-value and spend guards")
    # A stress PnL of exactly 0.00 was replaced by the UNSTRESSED PnL through `or net`, so a
    # trade earning +0.03 normally and nothing under latency scored as +0.03.
    zeroed = {k: dict(v) for k, v in outcomes.items()}
    for fid in [p["forecast_id"] for p in picked]:
        zeroed[fid]["stress_1000ms_plan_net"] = 0.0
    z = evaluate(rows, zeroed, artifact)
    chk(z["status"] == "SCORED", "a zero stress PnL still scores (it is real data)")
    chk(abs(float(z["stress_1000ms_mean"])) < 1e-9,
        "stress mean is 0.0, NOT silently replaced by the unstressed PnL")
    chk(z["gates"]["survives_latency_stress"] is False,
        "and the latency gate correctly FAILS - the bug inverted exactly this")

    empty_pool = {k: dict(v) for k, v in outcomes.items()}
    empty_pool[picked[0]["forecast_id"]]["candidate_pnls_json"] = "[]"
    chk(evaluate(rows, empty_pool, artifact)["status"] == "NOT_READY",
        "an EMPTY candidate pool is refused, not turned into a self-comparison")

    # DIRECT test of the write decision. The previous version drove main() with no threshold
    # artifact present, so it exited at the earlier Refused branch and never reached the status
    # guard - it passed without exercising the mechanism its assertion named.
    import tempfile as _tf
    from pathlib import Path as _P
    from .evaluate_complete_trade_m0_v2_forward import (
        Refused, canonical_result_hash, write_scored_result_once)

    for status in ("NOT_READY", "INADMISSIBLE", "IDENTITY_MISMATCH", "NO_RESOLVED_TRADES"):
        target = _P(_tf.mkdtemp()) / "r.json"
        try:
            write_scored_result_once({"status": status, "passed": False}, target)
            chk(False, status + " must be refused")
        except Refused:
            chk(not list(target.parent.iterdir()),
                status + " -> REFUSED and NOTHING written")

    target = _P(_tf.mkdtemp()) / "r.json"
    scored = {"status": "SCORED", "passed": True, "mean_net": 0.02}
    marker = write_scored_result_once(scored, target)
    chk(marker.exists(), "SCORED commits a spend marker")
    files = list(target.parent.glob("result_*.json"))
    chk(len(files) == 1, "exactly one content-addressed result file")
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    chk(stored["result_sha256"] == canonical_result_hash(scored),
        "recorded hash is the canonical hash of the pre-hash result")
    chk(marker.read_text(encoding="utf-8").strip() == stored["result_sha256"],
        "the marker records the result hash")
    try:
        write_scored_result_once(scored, target)
        chk(False, "a second spend must be refused")
    except Refused:
        chk(True, "a SECOND SCORED write is refused - spent once")

    target = _P(_tf.mkdtemp()) / "r.json"
    marker2 = write_scored_result_once(
        {"status": "SCORED", "passed": False, "mean_net": -0.05}, target)
    chk(marker2.exists(), "SCORED+FAILED still writes - a negative verdict IS a result")

    # CRASH RECOVERY: a truncated file at the final path must not spend the experiment.
    target = _P(_tf.mkdtemp()) / "r.json"
    (target.parent / "result_deadbeef.json").write_text("{trunc", encoding="utf-8")
    recovered = write_scored_result_once(
        {"status": "SCORED", "passed": True, "mean_net": 0.01}, target)
    chk(recovered.exists(),
        "a leftover truncated result does NOT block a later genuine commit")

    print("\nLEDGER V2 END-TO-END", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    sys.exit(run())
