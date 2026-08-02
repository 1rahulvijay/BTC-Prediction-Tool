"""Application evidence, PnL attribution, horizon consistency and readiness tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from research.phase5_standalone.common.engine_types import EngineContext, EngineResult
from research.phase5_standalone.common.metrics import EMPTY_ECONOMICS

from .data import load_contract, load_db_table


def _artifact_path(context: EngineContext, relative: str) -> Path:
    candidates = [context.data_dir / relative, context.data_dir.parent / relative]
    return next((path for path in candidates if path.exists()), candidates[-1])


def run_readiness(context: EngineContext) -> EngineResult:
    method = context.protocol.payload["method"]
    requirements = list(method.get("required_artifacts", []))
    resolved = {item: _artifact_path(context, item) for item in requirements}
    missing = [item for item, path in resolved.items() if not path.exists()]
    diagnostics: dict[str, Any] = {
        "required_artifacts": requirements,
        "resolved_paths": {item: str(path) for item, path in resolved.items()},
        "missing_artifacts": missing,
    }
    if not missing:
        path = next(iter(resolved.values()))
        required_columns = list(method.get("required_columns", []))
        if path.suffix.lower() != ".parquet":
            return EngineResult("BLOCKED_SCHEMA", "Prerequisite audit", diagnostics,
                                dict(EMPTY_ECONOMICS),
                                [f"unsupported readiness artifact type: {path.suffix}"], {}, {})
        con = duckdb.connect()
        try:
            description = con.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
            columns = [str(row[0]) for row in description]
            rows = int(con.execute(
                "SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()[0])
        finally:
            con.close()
        missing_columns = sorted(set(required_columns) - set(columns))
        diagnostics.update({
            "rows": rows,
            "columns": columns,
            "required_columns": required_columns,
            "missing_columns": missing_columns,
        })
        if missing_columns:
            return EngineResult("BLOCKED_SCHEMA", "Prerequisite audit", diagnostics,
                                dict(EMPTY_ECONOMICS),
                                [f"missing required columns: {missing_columns}"], {}, {})
        minimum_rows = int(method.get("minimum_rows", 1))
        if rows < minimum_rows:
            return EngineResult("INSUFFICIENT_SAMPLE", "Prerequisite audit", diagnostics,
                                dict(EMPTY_ECONOMICS),
                                [f"only {rows} rows; requires {minimum_rows}"], {}, {})
        return EngineResult("FAIL_UNSTABLE", "Prerequisite exists but needs its declared analysis",
                            diagnostics, dict(EMPTY_ECONOMICS),
                            ["data and schema presence alone are not evidence of edge"], {}, {})
    reason = str(method.get("blocked_reason") or "required causal dataset is unavailable")
    return EngineResult("BLOCKED_DATA", "Prerequisite audit", diagnostics,
                        dict(EMPTY_ECONOMICS), [reason], {}, {})


def _skip_reason_value(context: EngineContext) -> EngineResult:
    loaded = load_contract(context.data_dir, context.protocol.payload["data_contract"],
                           context.maximum_rows)
    frame = loaded.frame.copy()
    frame = frame[frame["resolved"].fillna(False).astype(bool)].copy()
    rows: dict[str, dict[str, float | int]] = {}
    for _, row in frame.iterrows():
        try:
            reasons = json.loads(row.get("no_trade_reasons_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            reasons = ["invalid_reason_payload"]
        if isinstance(reasons, dict):
            reasons = list(reasons)
        if not reasons:
            reasons = ["no_skip_reason"]
        for reason in reasons:
            key = str(reason)
            bucket = rows.setdefault(key, {"decisions": 0, "avoided_loss_usd": 0.0,
                                           "opportunity_cost_usd": 0.0,
                                           "realized_net_pnl_usd": 0.0})
            bucket["decisions"] += 1
            bucket["avoided_loss_usd"] += float(row.get("avoided_loss_usd") or 0.0)
            bucket["opportunity_cost_usd"] += float(row.get("opportunity_cost_usd") or 0.0)
            bucket["realized_net_pnl_usd"] += float(row.get("net_pnl_usd") or 0.0)
    for bucket in rows.values():
        bucket["net_gate_value_usd"] = float(
            bucket["avoided_loss_usd"] - bucket["opportunity_cost_usd"])
    diagnostics = {"resolved_decisions": int(len(frame)), "skip_reason_value": rows}
    if not len(frame):
        status, reasons = "INSUFFICIENT_SAMPLE", ["zero resolved opportunity rows"]
    else:
        status, reasons = "FAIL_UNSTABLE", [
            "counterfactual fields are logged estimates, not executable alternate fills"
        ]
    return EngineResult(status, "Economic value of recorded skip reasons", diagnostics,
                        dict(EMPTY_ECONOMICS), reasons, loaded.identity, loaded.causal_summary)


def _error_taxonomy(context: EngineContext) -> EngineResult:
    loaded = load_contract(context.data_dir, context.protocol.payload["data_contract"],
                           context.maximum_rows)
    frame = loaded.frame.copy()
    frame = frame[frame["resolved"].fillna(False).astype(bool)].copy()
    labels = []
    for _, row in frame.iterrows():
        direction_hit = bool(row.get("direction_hit"))
        gross = float(row.get("gross_pnl_usd") or 0.0)
        net = float(row.get("net_pnl_usd") or 0.0)
        if not direction_hit:
            label = "wrong_direction"
        elif gross > 0 and net <= 0:
            label = "correct_gross_lost_after_costs"
        elif direction_hit and gross <= 0:
            label = "correct_direction_wrong_timing_or_size"
        elif net > 0:
            label = "profitable"
        else:
            label = "unclassified_failure"
        labels.append(label)
    frame["error_type"] = labels
    counts = frame["error_type"].value_counts().to_dict()
    diagnostics = {
        "resolved_decisions": int(len(frame)),
        "taxonomy": counts,
        "unsupported_labels": ["late_exit", "premature_exit", "liquidity_failure",
                               "unexpected_burst", "out_of_distribution_state"],
    }
    return EngineResult("FAIL_UNSTABLE", "Resolved decision error taxonomy", diagnostics,
                        dict(EMPTY_ECONOMICS),
                        ["several requested failure labels require causal path and fill telemetry"],
                        loaded.identity, loaded.causal_summary)


def _pnl_attribution(context: EngineContext, capital_efficiency: bool = False) -> EngineResult:
    loaded = load_contract(context.data_dir, context.protocol.payload["data_contract"],
                           context.maximum_rows)
    frame = loaded.frame.copy()
    frame["pnl"] = pd.to_numeric(frame["pnl"], errors="coerce")
    frame["fee"] = pd.to_numeric(frame["fee"], errors="coerce").fillna(0.0)
    frame["spread"] = pd.to_numeric(frame["spread"], errors="coerce").fillna(0.0)
    frame["exit_gross"] = pd.to_numeric(frame["exit_gross"], errors="coerce").fillna(0.0)
    frame["seconds_locked"] = np.where(frame["settled_ts"].notna(),
                                        pd.to_numeric(frame["settled_ts"], errors="coerce") -
                                        pd.to_numeric(frame["ts"], errors="coerce"),
                                        pd.to_numeric(frame["horizon"], errors="coerce") * 60)
    frame["seconds_locked"] = frame["seconds_locked"].clip(lower=1)
    settled = frame.dropna(subset=["pnl"]).copy()
    if capital_efficiency:
        settled["pnl_per_capital_minute"] = settled["pnl"] / (settled["seconds_locked"] / 60)
        diagnostics = {
            "trades": int(len(settled)),
            "mean_pnl_per_capital_minute": float(settled["pnl_per_capital_minute"].mean()),
            "median_pnl_per_capital_minute": float(settled["pnl_per_capital_minute"].median()),
            "expected_shortfall_per_capital_minute": float(
                settled.nsmallest(max(1, int(len(settled) * 0.05)), "pnl_per_capital_minute")[
                    "pnl_per_capital_minute"].mean()),
            "by_rule": settled.groupby("rule")["pnl_per_capital_minute"].agg(
                ["count", "mean", "median"]).to_dict("index"),
        }
        summary = "Capital-duration-normalized paper PnL"
    else:
        diagnostics = {
            "trades": int(len(settled)),
            "total_pnl": float(settled["pnl"].sum()),
            "direction_or_settlement_component": float((settled["pnl"] + settled["fee"]).sum()),
            "fees": float(settled["fee"].sum()),
            "spread_paid_proxy": float(settled["spread"].sum()),
            "early_exit_gross": float(settled["exit_gross"].sum()),
            "by_exit_reason": settled.groupby("exit_reason")["pnl"].agg(
                ["count", "sum", "mean"]).to_dict("index"),
            "unavailable_components": ["market_mispricing", "accidental_exposure",
                                       "funding", "Shapley attribution"],
        }
        summary = "Paper PnL source accounting"
    return EngineResult("FAIL_UNSTABLE", summary, diagnostics, dict(EMPTY_ECONOMICS),
                        ["paper samples and attribution fields are incomplete for scaling"],
                        loaded.identity, loaded.causal_summary)


def _horizon_consistency(context: EngineContext) -> EngineResult:
    horizons = [5, 15, 30]
    frames = []
    identities = {}
    causal = {}
    for horizon in horizons:
        loaded = load_db_table(
            context.data_dir,
            database="analytics.duckdb",
            table=f"predictions_{horizon}m",
            columns=["final_direction", "actual_move", "confidence", "resolved"],
            timestamp="timestamp",
            maximum_rows=context.maximum_rows,
        )
        data = loaded.frame.copy()
        data = data.sort_values("_ts_ms").drop_duplicates("_ts_ms", keep="last")
        data = data.rename(columns={
            "_ts_ms": "ts_ms",
            "final_direction": f"direction_{horizon}",
            "actual_move": f"actual_move_{horizon}",
        })[["ts_ms", f"direction_{horizon}", f"actual_move_{horizon}"]]
        frames.append(data)
        identities[str(horizon)] = loaded.identity
        causal[str(horizon)] = loaded.causal_summary
    merged = frames[-1]
    for other in reversed(frames[:-1]):
        merged = pd.merge_asof(
            merged.sort_values("ts_ms"), other.sort_values("ts_ms"), on="ts_ms",
            direction="nearest", tolerance=120_000)
    direction = merged[[f"direction_{horizon}" for horizon in horizons]].copy()
    direction.columns = horizons
    actual = pd.DataFrame(index=merged.index)
    for horizon in horizons:
        move = pd.to_numeric(merged[f"actual_move_{horizon}"], errors="coerce")
        actual[horizon] = np.where(move > 0, "UP", np.where(move < 0, "DOWN", None))
    complete = direction.dropna(subset=horizons)
    complete = complete.map(lambda value: str(value).upper())
    actual = actual.reindex(complete.index)
    directional_mask = complete.isin({"UP", "DOWN"}).all(axis=1)
    directional = complete[directional_mask]
    actual = actual.reindex(directional.index)
    diagnostics = {
        "near_aligned_snapshots": int(len(complete)),
        "directional_snapshots": int(len(directional)),
        "all_neutral_snapshots": int((complete == "NEUTRAL").all(axis=1).sum()),
        "mixed_neutral_snapshots": int((~directional_mask & ~(complete == "NEUTRAL").all(axis=1)).sum()),
        "available_horizons": horizons,
        "missing_requested_horizons": [60, 120],
        "alignment_tolerance_ms": 120_000,
    }
    if len(directional) < 50:
        return EngineResult(
            "INSUFFICIENT_SAMPLE", "Cross-horizon directional-divergence diagnostic",
            diagnostics, dict(EMPTY_ECONOMICS), [
                "fewer than 50 aligned snapshots had UP/DOWN forecasts at every horizon",
                "NEUTRAL is abstention and cannot be counted as directional agreement",
            ], identities, causal)
    encoded = directional.map(lambda value: 1 if value == "UP" else -1)
    inconsistent = encoded.nunique(axis=1) > 1
    valid_actual = actual.map(lambda value: str(value).upper() in {"UP", "DOWN"})
    errors = pd.DataFrame(index=directional.index)
    for horizon in horizons:
        errors[horizon] = np.where(valid_actual[horizon],
                                   directional[horizon] !=
                                   actual[horizon].astype(str).str.upper(), np.nan)
    inconsistent_values = errors[inconsistent].to_numpy(float)
    consistent_values = errors[~inconsistent].to_numpy(float)
    inconsistent_error = (float(np.nanmean(inconsistent_values))
                          if np.isfinite(inconsistent_values).any() else None)
    consistent_error = (float(np.nanmean(consistent_values))
                        if np.isfinite(consistent_values).any() else None)
    diagnostics.update({
        "directional_divergence_rate": float(inconsistent.mean()),
        "error_rate_when_inconsistent": inconsistent_error,
        "error_rate_when_consistent": consistent_error,
    })
    lift = ((inconsistent_error - consistent_error)
            if inconsistent_error is not None and consistent_error is not None else 0.0)
    status = "FAIL_UNSTABLE" if lift > 0 else "FAIL_NO_EDGE"
    return EngineResult(status, "Cross-horizon directional-divergence diagnostic", diagnostics,
                        dict(EMPTY_ECONOMICS),
                        ["different horizons have different terminal times; divergence is diagnostic, not a logical error",
                         "1h and 2h forecasts are not produced by the application"], identities, causal)


def _candidate_completeness(context: EngineContext) -> EngineResult:
    required = list(context.protocol.payload["method"]["required_columns"])
    path = context.data_dir / "research" / "phase5_candidate_evidence.parquet"
    diagnostics: dict[str, Any] = {"path": str(path), "required_columns": required,
                                  "exists": path.is_file()}
    if not path.is_file():
        return EngineResult("BLOCKED_DATA", "Candidate-evidence completeness gate", diagnostics,
                            dict(EMPTY_ECONOMICS),
                            ["canonical per-decision candidate evidence does not exist"], {}, {})
    con = duckdb.connect()
    try:
        columns = [row[0] for row in con.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()]
        total = int(con.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()[0])
    finally:
        con.close()
    missing = sorted(set(required) - set(columns))
    diagnostics.update({"rows": total, "columns": columns, "missing_columns": missing})
    status = "BLOCKED_SCHEMA" if missing else "FAIL_UNSTABLE"
    reasons = ([f"missing required columns: {missing}"] if missing else
               ["schema presence still requires joined-coverage and causality validation"])
    return EngineResult(status, "Candidate-evidence completeness gate", diagnostics,
                        dict(EMPTY_ECONOMICS), reasons, {}, {})


def _gap_bias(context: EngineContext) -> EngineResult:
    loaded = load_contract(context.data_dir, context.protocol.payload["data_contract"],
                           context.maximum_rows)
    frame = loaded.frame.copy()
    qualifying = frame["qualifying"].fillna(False).astype(bool)
    diagnostics = {
        "episodes": int(len(frame)),
        "qualifying_episodes": int(qualifying.sum()),
        "excluded_episodes": int((~qualifying).sum()),
        "exclusion_reasons": frame.loc[~qualifying, "exclusion_reason"].fillna(
            "unknown").value_counts().to_dict(),
        "mean_max_ws_age_ms_excluded": float(pd.to_numeric(
            frame.loc[~qualifying, "max_ws_age_ms"], errors="coerce").mean()),
        "mean_reconnects_excluded": float(pd.to_numeric(
            frame.loc[~qualifying, "reconnects"], errors="coerce").mean()),
    }
    if qualifying.sum() == 0:
        status, reasons = "INSUFFICIENT_SAMPLE", [
            "zero qualifying episodes; healthy-versus-gap selection bias cannot be estimated"
        ]
    else:
        status, reasons = "FAIL_UNSTABLE", [
            "episode health lacks causally joined volatility and strategy outcomes"
        ]
    return EngineResult(status, "Recorder-gap selection-bias audit", diagnostics,
                        dict(EMPTY_ECONOMICS), reasons, loaded.identity, loaded.causal_summary)


def run_system_research(context: EngineContext) -> EngineResult:
    mode = str(context.protocol.payload["method"]["mode"])
    if mode == "skip_reason_value":
        return _skip_reason_value(context)
    if mode == "error_taxonomy":
        return _error_taxonomy(context)
    if mode == "pnl_attribution":
        return _pnl_attribution(context)
    if mode == "capital_efficiency":
        return _pnl_attribution(context, capital_efficiency=True)
    if mode == "horizon_consistency":
        return _horizon_consistency(context)
    if mode == "candidate_completeness":
        return _candidate_completeness(context)
    if mode == "gap_bias":
        return _gap_bias(context)
    raise ValueError(f"unknown system-research mode {mode}")
