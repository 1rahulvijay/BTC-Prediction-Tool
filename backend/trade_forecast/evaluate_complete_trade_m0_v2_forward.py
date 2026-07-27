"""THE definitive forward evaluation of COMPLETE_TRADE_M0_V2. Scored once.

This script reads immutable ledgers and nothing else. It does not train, does not fit, does not
derive a threshold, and does not re-score a historical candidate grid. Those are three separate
commands for a reason: an evaluator that can fit is an evaluator that can be tuned until it
passes, and the tuning leaves no trace.

    train_complete_trade_dev_model        development data only, no promotion authority
    freeze threshold artifact             calibration only, immutable, hashed
    THIS SCRIPT                           frozen predictions + resolved outcomes only

IMPORT BOUNDARY (enforced by test, not by intention):
    this module must not reach train_share_path_model, fit_* or derive_entry_threshold.

What it does, in order - and it refuses at the first failure rather than degrading:

     1  load the frozen preregistration and verify its LF-canonical SHA-256
     2  load the frozen ThresholdArtifact and verify its hash
     3  read ONLY ledger V2 predictions
     4  read ONLY resolved official outcomes
     5  build the ForwardEvidenceManifest and require admissibility
        (post-freeze, singleton hashes, own-recorder class, week coverage)
     6  select causally: first checkpoint clearing the FROZEN threshold, earliest -> latest
     7  at most one trade per round
     8  settle the exact frozen plan
     9  matched-random control, per round
    10  empirical p-values -> Benjamini-Hochberg at the frozen q
    11  day-block lower bound, profit factor, profit concentration by hour AND week
    12  1000ms latency stress
    13  write ONE immutable result manifest

    python backend/trade_forecast/evaluate_complete_trade_m0_v2_forward.py --dry-run
    python backend/trade_forecast/evaluate_complete_trade_m0_v2_forward.py --score-once
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from .forward_evidence import ThresholdArtifact, build_forward_manifest
from .m0_gates import (
    benjamini_hochberg,
    day_block_lower_bound,
    matched_random_difference,
    profit_concentration,
    profit_factor,
)
from .trade_forecast_logger import read_forward_rows, read_resolved_outcomes
from .trade_schema import M0_V2

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
PREREG = ROOT / "docs" / "active" / M0_V2["prereg"]
THRESHOLD_PATH = DATA / "research" / "complete_trade_forecast" / "entry_threshold.json"
RESULT_PATH = DATA / "research" / "complete_trade_forecast" / "m0_v2_forward_result.json"


class Refused(RuntimeError):
    """Evaluation cannot proceed. Never downgraded to a warning."""


def _canonical_sha256(path: Path) -> str:
    """LF-canonical text hash - the contract recorded in PREREG_HASH.txt."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _verify_protocol() -> dict[str, Any]:
    if not PREREG.is_file():
        raise Refused(f"preregistration not found: {PREREG}")
    actual = _canonical_sha256(PREREG)
    if actual != M0_V2["prereg_sha256"]:
        raise Refused(
            f"preregistration hash mismatch: recorded {M0_V2['prereg_sha256'][:16]}, "
            f"found {actual[:16]} - the protocol was edited after freezing"
        )
    return {"prereg_sha256": actual, "prereg": str(PREREG)}


def _load_threshold() -> ThresholdArtifact:
    if not THRESHOLD_PATH.is_file():
        raise Refused(
            f"no frozen threshold artifact at {THRESHOLD_PATH}. The evaluator does NOT derive "
            f"one - a threshold computed on the evidence period is the leak this design removes."
        )
    return ThresholdArtifact.load(THRESHOLD_PATH)


RESULT_HASH_MODE = "canonical_json_sha256_v1"


def canonical_result_hash(result: dict[str, Any]) -> str:
    """Hash over canonical JSON, computed BEFORE the hash field is added."""
    payload = json.dumps(result, sort_keys=True, separators=(",", ":"),
                         allow_nan=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


SPENT_DIRNAME = "COMPLETE_TRADE_M0_V2_SPENT"


def write_scored_result_once(result: dict[str, Any], result_path: Path) -> dict[str, Any]:
    """Commit a SCORED result exactly once, as an ATOMIC DIRECTORY.

    Extracted from main() so the decision can be tested directly - the earlier regression test
    drove this through the CLI with no threshold artifact, exited at that Refused branch, and
    never reached the status guard at all.

    Why a directory rather than a file plus a marker: a separately written marker has its own
    crash window. os.open(O_EXCL) creates the marker path BEFORE the digest is written, so a
    crash in between leaves an empty marker - the experiment reads as spent while identifying
    no winning result, and if two scorers raced, no way to tell which result won.

    Here everything is assembled in a uniquely named staging directory, fsynced and verified,
    and then ONE atomic rename publishes it. The spending event is the existence of the
    directory, and the directory cannot exist without its complete contents inside it.

        absent  -> unspent
        present -> the complete result and its hash are necessarily within
    """
    if result.get("status") != "SCORED":
        raise Refused(
            "status=" + str(result.get("status")) + " is a refusal, not a verdict; "
            "only SCORED may spend M0. Fix the blockers and re-run.")
    base = result_path.parent
    base.mkdir(parents=True, exist_ok=True)
    spent = base / SPENT_DIRNAME
    if spent.exists():
        raise Refused("M0 V2 is already spent: " + str(spent))
    digest = canonical_result_hash(result)
    payload = json.dumps({**result, "result_sha256": digest,
                          "result_hash_mode": RESULT_HASH_MODE},
                         indent=2, sort_keys=True, default=str)
    # Unique per process AND per attempt: two concurrent identical scorers must not share a
    # staging path and corrupt each other before either publishes.
    staging = base / ("." + SPENT_DIRNAME + ".staging-" + str(os.getpid())
                      + "-" + uuid.uuid4().hex[:12])
    staging.mkdir(parents=True)
    files = {"result.json": payload, "RESULT_SHA256": digest + chr(10)}
    for name, content in files.items():
        target = staging / name
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    # Verify from disk before publishing: a silently truncated stage must never be committed.
    for name, content in files.items():
        if (staging / name).read_text(encoding="utf-8") != content:
            shutil.rmtree(staging, ignore_errors=True)
            raise Refused("staged " + name + " did not verify on read-back; nothing spent")
    try:
        # Atomic publish. Renaming onto an existing directory fails on Windows always and on
        # POSIX when non-empty, so a racing scorer loses here rather than overwriting.
        os.rename(str(staging), str(spent))
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        raise Refused("M0 V2 was spent concurrently: " + str(spent)) from None
    return {"spent_dir": spent, "result_path": spent / "result.json",
            "result_sha256": digest}


def read_spent_result(base: Path) -> dict[str, Any]:
    """Load a committed result, verifying its hash. Raises if the spend is incomplete."""
    spent = base / SPENT_DIRNAME
    if not spent.is_dir():
        raise Refused("not spent: " + str(spent))
    result_file = spent / "result.json"
    digest_file = spent / "RESULT_SHA256"
    if not (result_file.is_file() and digest_file.is_file()):
        # Only reachable if something outside this writer created the directory.
        raise Refused("INTEGRITY FAILURE: spend directory is incomplete: " + str(spent))
    stored = json.loads(result_file.read_text(encoding="utf-8"))
    recorded = digest_file.read_text(encoding="utf-8").strip()
    if stored.get("result_sha256") != recorded:
        raise Refused("INTEGRITY FAILURE: result hash disagrees with the spend marker")
    return stored


def causal_selection(rows: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    """First checkpoint per round whose score clears the FROZEN threshold, earliest -> latest.

    Nothing later in the round can influence the decision. A round with no qualifying checkpoint
    is NO_TRADE and contributes no trade - it is still counted as an observed round."""
    by_round: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        by_round.setdefault(row.get("round_id"), []).append(row)
    selected = []
    for _, candidates in by_round.items():
        # Earliest checkpoint first = largest seconds_left.
        ordered = sorted(
            candidates,
            key=lambda r: (-float(r.get("seconds_left") or 0), -float(r.get("score") or 0)),
        )
        for candidate in ordered:
            score = candidate.get("score")
            if score is not None and float(score) >= float(threshold):
                selected.append(candidate)
                break
    return selected


def evaluate(rows: list[dict[str, Any]], outcomes: dict[str, dict[str, Any]],
             threshold: ThresholdArtifact) -> dict[str, Any]:
    """Score the frozen policy. Pure over its inputs so it is testable without a database."""
    # The REAL boundary, not the prereg date twice over. Evidence must post-date every freeze
    # that defines the policy, and the latest of them is what binds.
    def _max_field(name: str, fallback: float) -> float:
        values = [float(r[name]) for r in rows if r.get(name) is not None]
        return max(values) if values else fallback
    prereg_at = float(M0_V2["frozen_at_s"])
    model_at = _max_field("model_frozen_at_s", prereg_at)
    threshold_at = _max_field("threshold_frozen_at_s", threshold.created_at)
    boundary = max(prereg_at, model_at, threshold_at)
    manifest = build_forward_manifest(
        rows,
        prereg_sha256=M0_V2["prereg_sha256"],
        prereg_frozen_at=boundary,
        model_frozen_at=boundary,
        min_rounds=int(M0_V2["min_forward_rounds"]),
        min_weeks=int(M0_V2["min_forward_weeks"]),
    )
    if not manifest.get("admissible"):
        return {"status": "INADMISSIBLE", "passed": False, "manifest": manifest}

    # IDENTITY RELATIONSHIPS. Each of these is a way the evidence can look internally
    # consistent while describing a different experiment than the one being claimed.
    problems: list[str] = []
    digest = threshold.threshold_hash()
    if [r for r in rows if str(r.get("threshold_sha256")) != digest]:
        problems.append(
            f"rows were produced under a DIFFERENT threshold than the frozen artifact "
            f"({digest[:16]})")
    models = {str(r.get("model_sha256")) for r in rows}
    policies = {str(r.get("policy_sha256")) for r in rows}
    if len(models) == 1 and threshold.model_sha256 not in models:
        problems.append(
            f"the threshold was derived from model {threshold.model_sha256[:16]} but the "
            f"evidence was produced by {list(models)[0][:16]} - the threshold does not "
            f"belong to this model")
    if len(policies) == 1 and threshold.policy_sha256 not in policies:
        problems.append(
            f"the threshold was derived under policy {threshold.policy_sha256[:16]} but the "
            f"evidence ran under {list(policies)[0][:16]}")
    preregs = {str(r.get("prereg_sha256")) for r in rows}
    if preregs != {M0_V2["prereg_sha256"]}:
        problems.append(f"rows do not all carry the frozen preregistration hash ({preregs})")
    runs = {r.get("evidence_run_id") for r in rows}
    if len(runs) != 1 or None in runs:
        problems.append(f"evidence spans {len(runs)} run id(s) {runs} - exactly one is required")
    if problems:
        return {"status": "IDENTITY_MISMATCH", "passed": False,
                "manifest": manifest, "blockers": problems}

    selected = causal_selection(rows, threshold.threshold)
    if not selected:
        return {"status": "NO_RESOLVED_TRADES", "passed": False, "manifest": manifest}
    # COMPLETE RESOLUTION OR NOTHING. Evaluating only the trades that happen to have resolved is
    # outcome-availability bias: the quickest or cleanest trades resolve first, and the sample
    # silently becomes the easy half. Missing values are also never defaulted - a missing
    # plan_net is not 0.0, a missing stress result is not the unstressed one, and a missing
    # candidate pool is not "the trade competed against itself".
    missing = [r["forecast_id"] for r in selected if r.get("forecast_id") not in outcomes]
    incomplete = []
    for row in selected:
        outcome = outcomes.get(row.get("forecast_id"))
        if not outcome:
            continue
        for field in ("plan_net", "stress_1000ms_plan_net", "candidate_pnls_json"):
            value = outcome.get(field)
            if value is None or (
                field != "candidate_pnls_json" and not np.isfinite(float(value))
            ):
                incomplete.append(f"{row['forecast_id']}:{field}")
    if missing or incomplete:
        return {
            "status": "NOT_READY", "passed": False, "manifest": manifest,
            "selected_trades": len(selected),
            "unresolved": len(missing), "incomplete": len(incomplete),
            "blockers": [
                f"{len(missing)} selected trade(s) have no official outcome and "
                f"{len(incomplete)} have incomplete outcome fields; a definitive result "
                f"requires EVERY selected trade resolved"
            ][:1] + [f"example: {x}" for x in (missing[:2] + incomplete[:2])],
        }
    if len(selected) < int(M0_V2.get("min_selected_trades", 100)):
        return {"status": "NOT_READY", "passed": False, "manifest": manifest,
                "selected_trades": len(selected),
                "blockers": [f"{len(selected)} selected trades < "
                             f"{M0_V2.get('min_selected_trades', 100)} required"]}
    resolved = selected
    unresolved = 0

    pnl, hours, weeks, days, by_round = [], [], [], [], {}
    stress = []
    for row in resolved:
        outcome = outcomes[row["forecast_id"]]
        # NEVER truthiness on numeric evidence. `or net` substituted the UNSTRESSED PnL
        # whenever the stress result was exactly 0.0, so a trade that earned +0.03 normally and
        # 0.00 under 1000ms latency was scored as +0.03 - inverting the latency gate on exactly
        # the trades it exists to catch. Same for an empty pool becoming a self-comparison.
        net = float(outcome["plan_net"])
        stress_net = float(outcome["stress_1000ms_plan_net"])
        pool = json.loads(outcome["candidate_pnls_json"])
        if not isinstance(pool, list) or not pool:
            return {"status": "NOT_READY", "passed": False, "manifest": manifest,
                    "blockers": ["empty or malformed candidate pool for "
                                 + str(row["forecast_id"])]}
        pool = [float(v) for v in pool]
        if not all(np.isfinite(v) for v in pool + [net, stress_net]):
            return {"status": "NOT_READY", "passed": False, "manifest": manifest,
                    "blockers": ["non-finite outcome value for "
                                 + str(row["forecast_id"])]}
        pnl.append(net)
        stress.append(stress_net)
        stamp = time.gmtime(float(row["prediction_ts"]))
        hours.append(stamp.tm_hour)
        weeks.append(time.strftime("%G-%V", stamp))
        days.append(time.strftime("%F", stamp))
        by_round[row["round_id"]] = {"selected_pnl": net, "candidate_pnls": pool}

    control = matched_random_difference(by_round)
    lower_bound = day_block_lower_bound(pnl, days)
    factor = profit_factor(pnl)
    hour_share = profit_concentration(pnl, hours)
    week_share = profit_concentration(pnl, weeks)
    weekly_mean = {}
    for week, value in zip(weeks, pnl):
        weekly_mean.setdefault(week, []).append(value)
    positive_weeks = sum(1 for v in weekly_mean.values() if float(np.mean(v)) > 0)

    # The declared multiplicity family, corrected together.
    family = {"matched_random": control.get("p_value")}
    p_values = [v for v in family.values() if v is not None]
    correction = benjamini_hochberg(p_values, q=float(M0_V2["multiplicity_q"]))
    # A FAMILY OF ONE IS NOT A CORRECTION. Reporting "survives BH" over a single p-value implies
    # multiplicity protection that was never applied. Clarification 001 requires the family size
    # to be stated with the result.
    correction["family_size"] = len(p_values)
    correction["family"] = {k: v for k, v in family.items()}
    correction["multiplicity_protection"] = len(p_values) > 1

    gates = {
        "admissible": True,
        "mean_net_positive": float(np.mean(pnl)) > 0,
        "day_block_lb_positive": lower_bound is not None and lower_bound > float(
            M0_V2["day_block_lb_min"]),
        "profit_factor": factor is not None and factor > float(M0_V2["min_profit_factor"]),
        "beats_matched_random": bool(control.get("beats_random")),
        "survives_multiplicity": len(correction["rejected"]) > 0,
        "most_weeks_positive": positive_weeks > len(weekly_mean) / 2 if weekly_mean else False,
        "hour_concentration": hour_share <= float(M0_V2["max_hour_profit_share"]),
        "week_concentration": week_share <= float(M0_V2["max_week_profit_share"]),
        "survives_latency_stress": float(np.mean(stress)) > 0,
    }
    return {
        "status": "SCORED",
        "passed": all(gates.values()),
        "gates": gates,
        "manifest": manifest,
        "threshold": threshold.to_json(),
        "trades": len(resolved),
        "unresolved_selected": unresolved,
        "rounds_observed": manifest.get("independent_rounds"),
        "mean_net": float(np.mean(pnl)),
        "day_block_lb": lower_bound,
        "profit_factor": factor,
        "matched_random": control,
        "multiplicity": correction,
        "clarification_001_sha256": M0_V2.get("clarification_001_sha256"),
        "hour_profit_share": hour_share,
        "week_profit_share": week_share,
        "positive_weeks": positive_weeks,
        "total_weeks": len(weekly_mean),
        "stress_1000ms_mean": float(np.mean(stress)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--score-once", action="store_true",
                        help="write the immutable result manifest")
    parser.add_argument("--dry-run", action="store_true", help="report readiness, write nothing")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not (args.score_once or args.dry_run):
        print("refusing to run without --dry-run or --score-once")
        return 2

    try:
        protocol = _verify_protocol()
        threshold = _load_threshold()
        rows = read_forward_rows()
        outcomes = read_resolved_outcomes()
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return 1
    except Exception as exc:                                   # noqa: BLE001
        print(f"REFUSED: {type(exc).__name__}: {exc}")
        return 1

    result = {**evaluate(rows, outcomes, threshold), **protocol,
              "evaluated_at": time.time(), "ledger_rows": len(rows),
              "resolved_outcomes": len(outcomes)}
    print(json.dumps({k: v for k, v in result.items() if k != "manifest"},
                     indent=2, default=str))
    blockers = (result.get("manifest") or {}).get("blockers") or []
    for blocker in blockers:
        print(f"  BLOCKER: {blocker}")

    if args.dry_run:
        print("\nDRY RUN - nothing written. The scoring run is spent only with --score-once.")
        return 0
    try:
        commit = write_scored_result_once(result, RESULT_PATH)
    except Refused as exc:
        print("NOT SPENT: " + str(exc))
        return 1
    # Print what was ACTUALLY created. RESULT_PATH is the fixed name the writer no longer uses;
    # reporting it would send an operator looking for a file that does not exist.
    print("spent      : " + str(commit["spent_dir"]))
    print("result     : " + str(commit["result_path"]))
    print("result hash: " + commit["result_sha256"])
    print("PASS" if result.get("passed") else
          "FAIL -> COMPLETE_TRADE_M0_V2 CLOSED. MODELS FITTED = NONE.")
    return 0


def selftest() -> int:
    ok = True

    def chk(cond: bool, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok &= bool(cond)

    print("protocol integrity")
    chk(_canonical_sha256(PREREG) == M0_V2["prereg_sha256"],
        "the frozen preregistration verifies against its recorded hash")

    print("causal selection")
    rows = [
        {"round_id": "r1", "seconds_left": 120, "score": 0.30, "forecast_id": "a"},
        {"round_id": "r1", "seconds_left": 90, "score": 0.72, "forecast_id": "b"},
        {"round_id": "r1", "seconds_left": 60, "score": 0.95, "forecast_id": "c"},
        {"round_id": "r2", "seconds_left": 120, "score": 0.10, "forecast_id": "d"},
    ]
    picked = causal_selection(rows, 0.70)
    chk(len(picked) == 1, "a round with no qualifying checkpoint is NO_TRADE")
    chk(picked[0]["forecast_id"] == "b",
        "enters at the FIRST qualifying checkpoint (90s), not the round maximum (60s)")
    chk(
        all(len([p for p in picked if p["round_id"] == r]) <= 1 for r in ("r1", "r2")),
        "at most one trade per round",
    )

    print("refusals")
    try:
        _load_threshold() if not THRESHOLD_PATH.is_file() else None
        chk(not THRESHOLD_PATH.is_file(),
            "no frozen threshold exists yet, so scoring cannot proceed")
    except Refused:
        chk(True, "a missing threshold artifact REFUSES rather than deriving one")

    empty = evaluate([], {}, ThresholdArtifact(
        threshold=0.7, objective="x", target_entry_rate=0.2,
        calibration_start_ts=1.0, calibration_end_ts=2.0, calibration_rows=100,
        dataset_sha256="d" * 64, model_sha256="m" * 64,
        policy_sha256="p" * 64, code_sha256="c" * 64))
    chk(empty["status"] == "INADMISSIBLE" and not empty["passed"],
        "an empty evidence set is INADMISSIBLE, never a vacuous pass")

    print("import boundary")
    # AST, not a text scan: the forbidden names appear in this very check and in the docstring,
    # so grepping the source can only ever fail. What matters is what the module IMPORTS and
    # CALLS, which is a structural property.
    import ast

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)
    forbidden_modules = {"train_share_path_model", "train_btc_path_model",
                         "train_execution_heads", "build_complete_trade_dataset"}
    hit = sorted(
        name for name in imported
        if any(bad in str(name) for bad in forbidden_modules)
    )
    chk(not hit, f"imports no training module (found {hit})")

    forbidden_calls = {"fit_classifier_members", "fit_quantile_members",
                       "derive_entry_threshold", "clean_xy", "evaluate_m0"}
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    bad_calls = sorted(forbidden_calls & called)
    chk(not bad_calls, f"calls no fitting or threshold-deriving function (found {bad_calls})")

    loaded = [m for m in sys.modules if "train_" in m and "trade_forecast" in m]
    chk(not loaded, f"no training module is imported at runtime ({loaded})")

    print("end-to-end scoring")
    art = ThresholdArtifact(
        threshold=0.70, objective="P(plan net>0)", target_entry_rate=0.2,
        calibration_start_ts=1.0, calibration_end_ts=2.0, calibration_rows=5000,
        dataset_sha256="d" * 64, model_sha256="m" * 64,
        policy_sha256="p" * 64, code_sha256="c" * 64)
    digest = art.threshold_hash()
    # The fixture must satisfy the identity RELATIONSHIPS too: the threshold records the model
    # and policy it was derived from, and the evidence must have been produced by those.
    freeze = float(M0_V2["frozen_at_s"])

    def synth(n=1200, span_days=63, pnl=lambda i: 0.03 if i % 3 else -0.01):
        rows, outs = [], {}
        for i in range(n):
            stamp = freeze + 3600 + (i / n) * span_days * 86400
            for left, score in ((120, 0.9 if i % 3 else 0.1), (90, 0.9), (60, 0.99)):
                fid = f"f{i}_{left}"
                rows.append({
                    "forecast_id": fid, "round_id": f"r{i}", "seconds_left": left,
                    "prediction_ts": stamp, "score": score, "model_sha256": "m" * 64,
                    "feature_schema_sha256": "f" * 64, "policy_sha256": "p" * 64,
                    "threshold_sha256": digest, "evidence_source": "l2_recorder",
                    "prereg_sha256": M0_V2["prereg_sha256"],
                    "evidence_run_id": "unit_run",
                    "model_frozen_at_s": freeze,
                    "threshold_frozen_at_s": art.created_at})
                outs[fid] = {"plan_net": pnl(i), "stress_1000ms_plan_net": pnl(i) * 0.9,
                             "candidate_pnls_json": json.dumps([pnl(i), -0.02, 0.01, -0.01])}
        return rows, outs

    rows, outs = synth()
    result = evaluate(rows, outs, art)
    chk(result["status"] == "SCORED" and result["passed"],
        f"a profitable, well-spread set SCORES and passes ({result['status']})")
    chk(result.get("trades") == 1200, "one trade per round across 1200 rounds")
    chk(result["matched_random"]["p_value"] < 0.05, "matched-random p-value is significant")
    chk(len(result["multiplicity"]["rejected"]) > 0, "and it survives BH correction")

    leaked = [dict(rows[0], forecast_id="leak", prediction_ts=freeze - 1.0)] + rows
    chk(evaluate(leaked, outs, art)["status"] == "INADMISSIBLE",
        "ONE pre-freeze row makes the whole evidence set inadmissible")
    other = [dict(r, threshold_sha256="0" * 64) for r in rows]
    # IDENTITY_MISMATCH, not the generic INADMISSIBLE: the set is well-formed evidence of a
    # DIFFERENT experiment, and saying so precisely tells an operator what to fix.
    chk(evaluate(other, outs, art)["status"] == "IDENTITY_MISMATCH",
        "rows produced under a DIFFERENT threshold are refused as an identity mismatch")
    foreign_model = ThresholdArtifact(
        threshold=0.70, objective="x", target_entry_rate=0.2,
        calibration_start_ts=1.0, calibration_end_ts=2.0, calibration_rows=5000,
        dataset_sha256="d" * 64, model_sha256="z" * 64,
        policy_sha256="p" * 64, code_sha256="c" * 64)
    chk(evaluate([dict(r, threshold_sha256=foreign_model.threshold_hash()) for r in rows],
                 outs, foreign_model)["status"] == "IDENTITY_MISMATCH",
        "a threshold derived from a model that did not produce the evidence is refused")
    third = [dict(r, evidence_source="pmxt") for r in rows]
    chk(evaluate(third, outs, art)["status"] == "INADMISSIBLE",
        "third-party historical rows cannot support promotion")

    losing, lose_outs = synth(pnl=lambda i: -0.02 if i % 3 else 0.005)
    verdict = evaluate(losing, lose_outs, art)
    chk(verdict["status"] == "SCORED" and not verdict["passed"],
        "a LOSING strategy scores and FAILS - the gate can say no")

    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
