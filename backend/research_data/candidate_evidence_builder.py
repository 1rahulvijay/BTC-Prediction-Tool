"""Build the canonical Phase 5 candidate-evidence parquet from the atomic ledger.

One output row is one immutable decision. Exact state/quote/model context comes only from the
decision row; future settlement and markouts come only from append-only outcome rows. The builder
never performs an as-of join and never reconstructs a missing quote.

Unresolved decisions remain in the dataset for recorder-coverage analysis, but are explicitly
ineligible for economics. WAIT/BLOCKED/NO_QUOTE/UNAVAILABLE have zero selected-action PnL when
resolved because no position was opened. Where the original decision had an executable ask, a
separate research-only ENTER counterfactual is included.

    python backend/research_data/candidate_evidence_builder.py --selftest
    python backend/research_data/candidate_evidence_builder.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pandas as pd


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from opportunity_ledger.ledger import Action, Decision, OpportunityLedger, stable_hash


DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or REPO / "data")
DEFAULT_SOURCE = DATA_DIR / "opportunity_ledger.duckdb"
DEFAULT_OUTPUT = DATA_DIR / "research" / "phase5_candidate_evidence.parquet"
SCHEMA_VERSION = "phase5-candidate-evidence-v1"
SETTLEMENT_PRIORITY = {"SETTLEMENT_OFFICIAL": 2, "SETTLEMENT_PROXY": 1}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _parse_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _settlement(outcomes: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [row for row in outcomes if row["outcome_kind"] in SETTLEMENT_PRIORITY]
    if not candidates:
        return None
    return max(candidates, key=lambda row: (
        SETTLEMENT_PRIORITY[row["outcome_kind"]], int(row["outcome_ts"])
    ))


def _future_payload(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        row["outcome_kind"]: {
            "outcome_ts": int(row["outcome_ts"]),
            "filled_size": row["filled_size"],
            "fill_price": row["fill_price"],
            "exit_price": row["exit_price"],
            "settled_direction": row["settled_direction"],
            "fees_paid": row["fees_paid"],
            "net_pnl": row["net_pnl"],
            "detail": _parse_json(row["detail"], {}),
        }
        for row in sorted(outcomes, key=lambda item: (item["outcome_ts"], item["outcome_kind"]))
    }


def _build_row(decision: dict[str, Any], outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    context = _parse_json(decision.get("decision_context_json"), {})
    features = context.get("feature_values") if isinstance(context, dict) else {}
    model_outputs = context.get("model_outputs") if isinstance(context, dict) else {}
    if not isinstance(features, dict):
        features = {}
    if not isinstance(model_outputs, dict):
        model_outputs = {}

    selected_action = str(decision.get("action") or "UNAVAILABLE").upper()
    side = str(decision.get("side") or "").upper()
    ask = _finite(decision.get("ask"))
    bid = _finite(decision.get("bid"))
    fee = max(0.0, _finite(decision.get("fee")) or 0.0)
    probability = _finite(decision.get("probability"))
    settlement = _settlement(outcomes)
    resolved = bool(settlement and settlement.get("settled_direction") in {"UP", "DOWN"})
    payout = None
    enter_gross = None
    enter_net = None
    if resolved and side in {"UP", "DOWN"} and ask is not None:
        payout = 1.0 if side == settlement["settled_direction"] else 0.0
        enter_gross = payout - ask
        enter_net = enter_gross - fee

    took_position = selected_action == Action.ENTER.value
    selected_net = enter_net if took_position and resolved else (0.0 if resolved else None)
    selected_gross = enter_gross if took_position and resolved else (0.0 if resolved else None)
    selected_cost = fee if took_position else 0.0
    counterfactuals: dict[str, Any] = {"WAIT": {"net_pnl": 0.0}}
    if enter_net is not None:
        counterfactuals["ENTER_AT_DECISION_ASK"] = {
            "gross_pnl": enter_gross,
            "fees": fee,
            "net_pnl": enter_net,
            "research_only": not took_position,
            "fill_assumption": "one share at the recorded decision ask",
        }
    for outcome in outcomes:
        if outcome["outcome_kind"].startswith("COUNTERFACTUAL_"):
            counterfactuals[outcome["outcome_kind"]] = _parse_json(outcome["detail"], {})

    future_markouts = {
        outcome["outcome_kind"]: _future_payload([outcome])[outcome["outcome_kind"]]
        for outcome in outcomes
        if outcome["outcome_kind"].startswith(("MARKOUT_", "LATENCY_"))
    }
    spread = max(0.0, ask - bid) if ask is not None and bid is not None else None
    predicted_ev = (
        probability - ask - fee
        if probability is not None and ask is not None and side in {"UP", "DOWN"}
        else None
    )
    regime = str(features.get("regime") or context.get("regime") or "UNKNOWN")

    exact_prediction = {
        "side": side or None,
        "probability": probability,
        "model_outputs": model_outputs,
    }
    exact_quote = {
        "exchange_ts": decision.get("quote_exchange_ts"),
        "receive_ts": decision.get("quote_recv_ts"),
        "ask": ask,
        "bid": bid,
        "fee": fee,
        "side": side or None,
    }
    exact_state = {
        "state_snapshot_id": decision.get("state_snapshot_id"),
        "state_snapshot_ts": decision.get("state_snapshot_ts"),
        "feature_cutoff_ts": decision.get("feature_cutoff_ts"),
        "feature_values": features,
        "feature_values_hash": decision.get("feature_values_hash"),
    }
    future = _future_payload(outcomes)
    future_outcome = {
        "resolved": resolved,
        "selected_settlement": (
            future.get(settlement["outcome_kind"]) if settlement else None
        ),
        "all_outcomes": future,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "decision_id": decision["decision_id"],
        "alpha_id": decision["strategy_id"],
        "strategy_id": decision["strategy_id"],
        "configuration_id": decision.get("policy_hash"),
        "venue": decision["venue"],
        "round_id": decision["round_id"],
        "market_id": decision.get("market_id"),
        "ts_ms": int(decision["decision_ts"]),
        "decision_ts": int(decision["decision_ts"]),
        # Numeric exposure is required by Phase 5 randomization controls. The descriptive action
        # remains in selected_action; never coerce strings downstream and accidentally make NaN.
        "action": 1.0 if took_position else 0.0,
        "selected_action": selected_action,
        "skip_reason": str(decision.get("reason") or ""),
        "side": side or None,
        "regime": regime,
        "exact_prediction": _json(exact_prediction),
        "exact_quote": _json(exact_quote),
        "exact_state": _json(exact_state),
        "exact_features": _json(features),
        "all_model_outputs": _json(model_outputs),
        "counterfactual_action_values": _json(counterfactuals),
        "future_executable_markouts": _json(future_markouts),
        "future_outcome": _json(future_outcome),
        "settlement": _json(future_outcome["selected_settlement"]),
        "resolved": resolved,
        "eligible_for_economics": resolved and enter_net is not None,
        "predicted_ev": predicted_ev,
        "market_return": enter_gross,
        "gross_pnl": selected_gross,
        "fees": fee if took_position and resolved else 0.0,
        "cost": selected_cost if resolved else None,
        "current_cost": selected_cost if resolved else None,
        "net_pnl": selected_net,
        "observed_spread": spread,
        "expected_costs": _json({
            "entry_fee": fee,
            "observed_bid_ask_spread": spread,
            "spread_already_embedded_in_ask_fill": True,
        }),
        "model_hash": decision.get("model_artifact_hash"),
        "calibrator_hash": decision.get("calibrator_hash"),
        "policy_hash": decision.get("policy_hash"),
        "feature_hash": decision.get("feature_values_hash"),
        "risk_state": decision.get("risk_state"),
        "state_age_ms": decision.get("state_age_ms"),
        "quote_age_ms": decision.get("quote_age_ms"),
        "exchange_skew_ms": decision.get("exchange_skew_ms"),
    }


def build_candidate_evidence(source: Path, output: Path, *, require_rows: bool = True) -> dict[str, Any]:
    import duckdb

    if not source.is_file():
        raise FileNotFoundError(f"atomic opportunity ledger not found: {source}")
    before = source.stat()
    con = duckdb.connect(str(source), read_only=True)
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        required_tables = {"opportunity_decisions", "opportunity_outcomes"}
        missing = sorted(required_tables - tables)
        if missing:
            raise RuntimeError(f"source ledger is missing tables: {missing}")
        decisions = con.execute(
            "SELECT * FROM opportunity_decisions ORDER BY decision_ts, decision_id"
        ).fetchdf()
        outcomes = con.execute(
            "SELECT * FROM opportunity_outcomes ORDER BY outcome_ts, outcome_kind"
        ).fetchdf()
    finally:
        con.close()
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("source ledger changed during export; stop/retry instead of mixing snapshots")
    if decisions.empty and require_rows:
        raise RuntimeError(
            "source ledger contains zero decisions; run the recorder/app before exporting evidence"
        )

    outcome_groups: dict[str, list[dict[str, Any]]] = {}
    for row in outcomes.to_dict("records"):
        outcome_groups.setdefault(str(row["decision_id"]), []).append(row)
    records = [
        _build_row(row, outcome_groups.get(str(row["decision_id"]), []))
        for row in decisions.to_dict("records")
    ]
    frame = pd.DataFrame(records)
    if not frame.empty:
        if frame["decision_id"].duplicated().any():
            raise RuntimeError("candidate evidence has duplicate decision_id rows")
        causal_bad = frame[
            (pd.to_numeric(frame["state_age_ms"], errors="coerce") < 0)
            | (pd.to_numeric(frame["quote_age_ms"], errors="coerce") < 0)
        ]
        if len(causal_bad):
            raise RuntimeError(f"candidate evidence contains {len(causal_bad)} future-state/quote rows")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(f".{output.name}.{os.getpid()}.tmp.parquet")
    try:
        frame.to_parquet(temp_output, index=False)
        os.replace(temp_output, output)
    finally:
        if temp_output.exists():
            temp_output.unlink()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "built_ts": int(time.time() * 1000),
        "source": str(source.resolve()),
        "source_sha256": _sha256(source),
        "source_size": after.st_size,
        "output": str(output.resolve()),
        "output_sha256": _sha256(output),
        "rows": int(len(frame)),
        "resolved_rows": int(frame["resolved"].sum()) if not frame.empty else 0,
        "economic_rows": int(frame["eligible_for_economics"].sum()) if not frame.empty else 0,
        "by_action": (
            frame["selected_action"].value_counts().sort_index().to_dict()
            if not frame.empty else {}
        ),
        "causal_join": "none; exact decision row plus outcomes keyed by decision_id",
        "unresolved_rows_retained": True,
        "counterfactual_fill_assumption": "one share at exact decision ask when available",
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _selftest() -> int:
    checks = 0

    def check(condition: bool, label: str) -> None:
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "opportunity.duckdb"
        output = root / "phase5_candidate_evidence.parquet"
        ledger = OpportunityLedger(source)
        feature_values = {"regime": "RANGE", "seconds_left": 30, "ask": 0.60}
        context = {
            "feature_values": feature_values,
            "model_outputs": {"calibrated_probability": 0.70, "market_probability": 0.61},
        }
        common = dict(
            strategy_id="PM_TEST", market_id="m1", venue="POLYMARKET",
            quote_exchange_ts=9_900, quote_recv_ts=9_950,
            state_snapshot_id="state-1", state_snapshot_ts=9_940,
            feature_cutoff_ts=9_940, side="UP", ask=0.60, bid=0.59, fee=0.01,
            probability=0.70, model_artifact_hash="model", calibrator_hash="cal",
            policy_hash="policy", feature_values_hash=stable_hash(feature_values),
            decision_context=context, reason="test", requested_size=1.0,
        )
        enter_id = ledger.record(Decision(
            round_id="r1", decision_ts=10_000, action=Action.ENTER, **common), now_ms=10_001)
        wait_id = ledger.record(Decision(
            round_id="r2", decision_ts=20_000, action=Action.WAIT, **common), now_ms=20_001)
        ledger.append_settlement_for_round(
            "r1", settled_direction="UP", outcome_ts=30_000,
            kind="SETTLEMENT_OFFICIAL", source="selftest")
        ledger.append_settlement_for_round(
            "r2", settled_direction="DOWN", outcome_ts=30_000,
            kind="SETTLEMENT_OFFICIAL", source="selftest")

        manifest = build_candidate_evidence(source, output)
        frame = pd.read_parquet(output).sort_values("decision_id")
        check(manifest["rows"] == 2 and len(frame) == 2, "one row is exported per decision")
        enter = frame[frame["decision_id"] == enter_id].iloc[0]
        wait = frame[frame["decision_id"] == wait_id].iloc[0]
        check(abs(float(enter["net_pnl"]) - 0.39) < 1e-12,
              "ENTER PnL uses payout minus exact ask and fee")
        check(float(wait["net_pnl"]) == 0.0 and float(wait["action"]) == 0.0,
              "WAIT keeps zero realized exposure and PnL")
        wait_cf = json.loads(wait["counterfactual_action_values"])
        check(abs(float(wait_cf["ENTER_AT_DECISION_ASK"]["net_pnl"]) + 0.61) < 1e-12,
              "WAIT retains the losing ENTER counterfactual separately")
        check(json.loads(enter["exact_state"])["feature_values"] == feature_values,
              "the exact feature preimage is preserved")
        check(bool(frame["resolved"].all()) and bool(frame["eligible_for_economics"].all()),
              "resolved executable rows are economics-eligible")
        check(Path(output.with_suffix(".manifest.json")).is_file()
              and manifest["causal_join"].startswith("none"),
              "the immutable export manifest records the no-asof causal contract")

    print(f"\nCANDIDATE EVIDENCE BUILDER SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return _selftest()
    manifest = build_candidate_evidence(args.source, args.output)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
