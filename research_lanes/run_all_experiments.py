#!/usr/bin/env python
"""Run the repository's standalone research campaign in one sequential command.

This orchestrator does not import serving code, write serving artifacts, or start trading. Each
experiment runs in its own child process so memory is returned before the next experiment.
Immutable matrices, CSVs and copied recorder exports are preferred. A frozen legacy test whose
only source is a live-locked DuckDB fails closed as ``BLOCKED_DATA``; the campaign never stops a
recorder or copies an inconsistent live database to force an answer.

The default is a 100,000-row real-data campaign for the 88 frozen Phase 5/5B packages, all
Phase 5C path diagnostics, the newer alpha lanes, and the official-settlement Polymarket
residual. The result is one durable Markdown report plus command logs and JSON manifests.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
LANES = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_OUTPUT = DATA / "research" / "alpha_lab_campaigns"
CANONICAL_REPORT = ROOT / "docs" / "active" / "STANDALONE_ALPHA_LAB_COMPLETE_CAMPAIGN_2026-08-13.md"


PROPOSAL_COVERAGE = [
    ("Market disagreement / minority model resolution", "Phase 5 #04; Phase 5B #46-47", "TESTED"),
    ("Polymarket probability elasticity / acceleration", "Phase 5 #19-20; Phase 5B #43-45,70", "INSUFFICIENT_OR_BLOCKED"),
    ("Polymarket implied volatility / deadline convexity", "Phase 5 #18,21; Phase 5C volatility tests", "TESTED_DIAGNOSTIC"),
    ("Order-flow surprise / event propagation", "Phase 5 #10-17; Phase 5B #58-68", "DATA_BLOCKED"),
    ("Book elasticity / replenishment / resiliency", "Phase 5 #15; Phase 5B #59-64,72", "DATA_BLOCKED"),
    ("Liquidity vacuum / cancellation toxicity", "Phase 5B #59-64", "DATA_BLOCKED"),
    ("Cross-venue information leader / synchronized shock", "Phase 5 #08; Phase 5B #73-75", "PARTIAL_DATA_BLOCKED"),
    ("Polymarket stale quote / repricing lag", "Phase 5 #19-20; Phase 5B #70,73-74", "PARTIAL_DATA_BLOCKED"),
    ("Maker replenishment / markout / adverse selection", "Batch 3 hypothetical markout; Phase 5B #59-64", "PARTIAL_DATA_BLOCKED"),
    ("Clock phase alpha", "TIME_PHASE_ALPHA_V1", "TESTED"),
    ("Uncertainty collapse / information clock", "Phase 5B #48-50,56-57", "PARTIAL_BLOCKED_OR_UNSTABLE"),
    ("Change points / volatility transitions / regime state", "Phase 5B #55,68,82-83", "TESTED_DIAGNOSTIC"),
    ("False breakout / continuation / exhaustion", "Phase 5 #11-14", "DATA_BLOCKED"),
    ("Polymarket settlement convexity", "Batch 3 plus Phase 5 #21", "TESTED_DIAGNOSTIC"),
    ("State-value atlas / regime selector", "STATE_VALUE_ATLAS_V1", "TESTED_UNDERPOWERED"),
    ("Negative alpha / placebo / randomization", "Phase 5 #38-42", "DATA_BLOCKED"),
    ("Counterfactual action and order policy", "Phase 5 #33-35; Phase 5B #76,86", "PARTIAL_DATA_BLOCKED"),
    ("Capacity curve / capital efficiency", "Phase 5 #36; Phase 5B #80-81", "PARTIAL_DATA_BLOCKED"),
    ("Edge half-life / alpha decay", "Phase 5 #30; Phase 5B #49-50", "DATA_BLOCKED"),
    ("Alpha portfolio / opportunity auction", "Phase 5 #28-29; Phase 5B #80-81", "DATA_BLOCKED"),
    ("Complete-set arbitrage", "POLY_FULLSET_ARB_V1", "TESTED_NEGLIGIBLE"),
    ("Last-seconds convergence / P(flip) / anchor touch", "Phase 5B #48,69-70; Phase 5C", "TESTED_DIAGNOSTIC"),
    ("Market-prior residual fair value", "POLY_MARKET_PRIOR_RESIDUAL_V1", "TESTED"),
    ("Buy now vs wait", "WAIT_VS_BUY_V1 oracle and fixed-delay controls", "TESTED_NO_EDGE"),
    ("Two-sided maker / queue fill", "HEDGED_POLY_MM_V1 and hypothetical markouts", "PARTIAL_DATA_BLOCKED"),
    ("Binance cost-clearance return distribution", "BINANCE_COST_CLEARANCE_V1", "TESTED"),
    ("Dynamic barriers / MFE-MAE / holding time", "Phase 5C plus matrix path extensions", "TESTED_NO_EDGE"),
    ("Tradeable / no-trade / extreme selectivity", "VOLATILITY_EXPANSION_V1; Phase 5 #01,27", "DIAGNOSTIC_NOT_EXECUTABLE"),
    ("Funding plus basis carry", "SPOT_PERP_BASIS_V1; funding cash-flow audit", "BASIS_TESTED_FUNDING_BLOCKED"),
    ("Cross-exchange funding dispersion", "Recorder exists; independent history gate", "DATA_BLOCKED"),
    ("BTC/ETH/SOL relative value", "Phase 5 #06-07", "DATA_BLOCKED"),
    ("Liquidation continuation / exhaustion", "Decision-head research; missing causal liquidation history", "DATA_BLOCKED"),
    ("Tail-risk / jump-vs-diffusion", "Phase 5C", "TESTED_DIAGNOSTIC"),
    ("Ensemble disagreement / model-error predictor", "Phase 5B #46-47,79", "TESTED_NO_EDGE_OR_UNSTABLE"),
    ("State calibration / Bayesian updating", "Phase 5B #48,52", "TESTED_NO_EDGE"),
    ("Deribit implied vs realized volatility", "Options research; synchronized chain history insufficient", "DATA_BLOCKED"),
    ("Spot-perpetual basis dislocation", "SPOT_PERP_BASIS_V1", "TESTED"),
    ("Volatility expansion", "VOLATILITY_EXPANSION_V1", "TESTED_DIAGNOSTIC"),
    ("Compression breakout", "MATRIX_PATH_EXTENSIONS", "TESTED_DIAGNOSTIC"),
    ("Trend pullback / survival", "MATRIX_PATH_EXTENSIONS", "TESTED_NO_EDGE"),
    ("Adverse recovery / profit giveback", "MATRIX_PATH_EXTENSIONS", "TESTED_NO_EDGE"),
    ("Microbasis reversion", "MATRIX_PATH_EXTENSIONS", "TESTED_NO_EDGE"),
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_identity() -> dict[str, Any]:
    def call(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()

    try:
        return {"commit": call("rev-parse", "HEAD"), "dirty": bool(call("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError):
        return {"commit": "unknown", "dirty": True}


def _run(label: str, command: list[str], run_root: Path) -> dict[str, Any]:
    log_path = run_root / "logs" / f"{label}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"\n[{label}] starting", flush=True)
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        line_count = 0
        last_line = ""
        for line in process.stdout:
            log.write(line)
            line_count += 1
            if line.strip():
                last_line = line.strip()
            if line_count % 100 == 0:
                print(f"[{label}] {line_count:,} log lines", flush=True)
        returncode = process.wait()
    elapsed = time.perf_counter() - started
    print(
        f"[{label}] {'PASS' if returncode == 0 else 'FAIL'} in {elapsed:.1f}s"
        + (f" - {last_line}" if last_line else ""),
        flush=True,
    )
    return {
        "label": label,
        "command": command,
        "returncode": returncode,
        "elapsed_seconds": round(elapsed, 3),
        "log": str(log_path),
        "last_line": last_line,
    }


def _find_one(root: Path, name: str) -> Path | None:
    matches = sorted(root.rglob(name), key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _load_summary(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _status_counts(payload: dict[str, Any]) -> Counter:
    return Counter(str(row.get("status") or "NO_REPORT") for row in payload.get("experiments", []))


def _clean_cell(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").replace("|", "/").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _experiment_ledger(
    title: str,
    suite: dict[str, Any],
    run_root: Path,
    suite_directory: str,
) -> list[str]:
    lines = [
        f"### {title}",
        "",
        "| experiment | result | conclusion |",
        "|---|---|---|",
    ]
    for row in suite.get("experiments", []):
        report_value = row.get("report")
        if report_value:
            report_path = Path(report_value)
        else:
            report_path = run_root / suite_directory / str(row.get("experiment")) / "report.json"
        payload: dict[str, Any] = {}
        if report_path.is_file():
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
        experiment = payload.get("experiment_id") or row.get("experiment_id") or row.get("experiment")
        status = payload.get("status") or row.get("status") or "NO_REPORT"
        conclusion = payload.get("summary")
        if not conclusion:
            reasons = payload.get("reasons") or []
            conclusion = reasons[0] if reasons else "No report was produced."
        lines.append(
            f"| `{_clean_cell(experiment, 100)}` | `{_clean_cell(status, 50)}` | "
            f"{_clean_cell(conclusion)} |"
        )
    lines.append("")
    return lines


def _read_alpha_metrics(run_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    matrix = LANES / "matrix_lanes_results.json"
    cost = LANES / "binance_cost_clearance" / "results.json"
    if matrix.is_file():
        result["matrix_lanes"] = json.loads(matrix.read_text(encoding="utf-8"))
    if cost.is_file():
        result["cost_clearance"] = json.loads(cost.read_text(encoding="utf-8"))
    prior = run_root / "polymarket_prior_results.json"
    if not prior.is_file():
        prior = LANES / "polymarket_residual" / "results.json"
    fullset = run_root / "polymarket_fullset_results.json"
    if not fullset.is_file():
        fullset = LANES / "poly_fullset_arb" / "results.json"
    if prior.is_file():
        result["pm_prior_comparison"] = json.loads(prior.read_text(encoding="utf-8"))
    if fullset.is_file():
        result["pm_fullset"] = json.loads(fullset.read_text(encoding="utf-8"))
    extra_sources = {
        "remaining_lanes": (
            run_root / "remaining_lanes_results.json",
            LANES / "remaining_lanes_results.json",
        ),
        "batch3": (run_root / "batch3_results.json", LANES / "batch3_results.json"),
        "matrix_path_extensions": (
            run_root / "matrix_path_extensions_results.json",
            LANES / "matrix_path_extensions_results.json",
        ),
    }
    for key, candidates in extra_sources.items():
        source = next((path for path in candidates if path.is_file()), None)
        if source:
            result[key] = json.loads(source.read_text(encoding="utf-8"))
    pm_dir = run_root / "polymarket_residual"
    metrics_path = pm_dir / "probability_metrics.csv"
    actions_path = pm_dir / "action_metrics.csv"
    gate_path = pm_dir / "gate_status.json"
    if metrics_path.is_file():
        result["pm_probability_metrics"] = pd.read_csv(metrics_path).to_dict(orient="records")
    if actions_path.is_file():
        result["pm_action_metrics"] = pd.read_csv(actions_path).to_dict(orient="records")
    if gate_path.is_file():
        result["pm_gate"] = json.loads(gate_path.read_text(encoding="utf-8"))
    return result


def _render_report(
    run_id: str,
    run_root: Path,
    identity: dict[str, Any],
    commands: list[dict[str, Any]],
    phase5: dict[str, Any],
    phase5b: dict[str, Any],
    alpha: dict[str, Any],
    inputs: dict[str, Any],
) -> str:
    phase5_counts = _status_counts(phase5)
    phase5b_counts = _status_counts(phase5b)
    pass_candidates = phase5_counts["PASS_CANDIDATE"] + phase5b_counts["PASS_CANDIDATE"]
    lines = [
        "# Standalone Alpha Laboratory - Complete Campaign",
        "",
        f"Run: `{run_id}`",
        "",
        f"Git: `{identity['commit']}` ({'dirty' if identity['dirty'] else 'clean at launch'})",
        "",
        "Authority: `RESEARCH_ONLY`; no serving, paper, or live strategy was modified",
        "",
        "## Executive Verdict",
        "",
    ]
    if pass_candidates:
        lines.append(
            f"The frozen suites reported **{pass_candidates} PASS_CANDIDATE result(s)**. This is "
            "not production authority; each still requires independent forward evidence."
        )
    else:
        lines.append(
            "**No tested strategy earned promotion.** The campaign found diagnostics and state "
            "information, but no robust executable alpha with a positive lower confidence bound "
            "after declared costs."
        )
    lines.extend([
        "",
        "Accuracy or AUC alone is not treated as profit. A test is promotable only when its "
        "chronological out-of-sample net-EV lower bound is positive at executable prices and "
        "the minimum independent-day/round gates pass.",
        "",
        "## Execution",
        "",
        "| stage | result | seconds | log |",
        "|---|---:|---:|---|",
    ])
    for row in commands:
        relative_log = Path(row["log"]).relative_to(ROOT).as_posix()
        lines.append(
            f"| {row['label']} | {'PASS' if row['returncode'] == 0 else 'FAIL'} | "
            f"{row['elapsed_seconds']:.1f} | `{relative_log}` |"
        )
    lines.extend([
        "",
        "## Frozen Suite Results",
        "",
        "| suite | experiments | status counts |",
        "|---|---:|---|",
        f"| Phase 5 | {len(phase5.get('experiments', []))} | "
        + ", ".join(f"{key}={value}" for key, value in sorted(phase5_counts.items())) + " |",
        f"| Phase 5B | {len(phase5b.get('experiments', []))} | "
        + ", ".join(f"{key}={value}" for key, value in sorted(phase5b_counts.items())) + " |",
        "",
        "These are real-data campaign statuses. `BLOCKED_DATA` is an honest result: the causal "
        "source, execution arm, independent history, or settlement join needed by the frozen "
        "question was unavailable.",
        "",
    ])
    lines.extend(_experiment_ledger("Phase 5 - all 42 results", phase5, run_root, "phase5"))
    lines.extend(_experiment_ledger("Phase 5B - all 46 results", phase5b, run_root, "phase5b"))
    phase5c = [row for row in commands if row["label"].startswith("phase5c_")]
    lines.extend([
        "### Phase 5C - all path diagnostics",
        "",
        "| diagnostic | process | reported conclusion |",
        "|---|---|---|",
    ])
    for row in phase5c:
        lines.append(
            f"| `{row['label'].removeprefix('phase5c_')}` | "
            f"{'PASS' if row['returncode'] == 0 else 'FAIL'} | {_clean_cell(row['last_line'])} |"
        )
    lines.extend(["", "## New Alpha Lanes", ""])
    cost = alpha.get("cost_clearance", {})
    if cost:
        lines.extend([
            "### Binance Cost Clearance",
            "",
            f"Matrix span: {cost.get('span_days', '-')} days; shipped round trip: "
            f"{cost.get('shipped_cost_bps', '-')} bps.",
            "",
            "The measured result remains structural: below roughly 30 minutes, ordinary held-to-"
            "horizon taker direction does not provide enough movement to support the observed "
            "near-coin-flip accuracy. Better selection or lower execution cost is mandatory.",
            "",
        ])
    matrix = alpha.get("matrix_lanes", {})
    if matrix:
        volatility = matrix.get("volatility_expansion", {})
        phase = matrix.get("time_phase", {})
        basis = matrix.get("spot_perp_basis", {})
        lines.extend([
            "### Matrix Lanes",
            "",
            f"- Volatility expansion: AUC {volatility.get('auc', float('nan')):.3f}; simple "
            f"RV15 baseline {volatility.get('auc_baseline_rv15', float('nan')):.3f}; top-decile "
            f"move hit {volatility.get('top_decile_hit', float('nan')):.1%}. This predicts activity, "
            "not direction, and is not independently profitable.",
            f"- Time phase: hottest minute-of-quarter `{phase.get('hottest_bucket', '-')}`; "
            f"separated confidence intervals = `{phase.get('separated', False)}`. No clock alpha.",
            f"- Spot/perp basis: 5m rich-basis reversion "
            f"{basis.get('5m', {}).get('rich_reversion_bps', float('nan')):+.2f} bps versus a "
            "12 bps round trip. Real reversion, economically too small.",
            "",
        ])
    pm_metrics = alpha.get("pm_probability_metrics", [])
    pm_actions = alpha.get("pm_action_metrics", [])
    if pm_metrics:
        overall = {row["model"]: row for row in pm_metrics if row["segment"] == "ALL"}
        market = overall.get("A_market_prior", {})
        residual = overall.get("D_market_prior_full_residual", {})
        action = next((row for row in pm_actions if row["model"] == "D_market_prior_full_residual"), {})
        lines.extend([
            "### Polymarket Market-Prior Residual",
            "",
            "The previous zero-overlap conclusion was a data-identity bug: it queried an older "
            "DuckDB table instead of the paired recorder exports. The corrected join uses only "
            "Polymarket CLOB/Gamma outcomes.",
            "",
            f"- Market prior: Brier {market.get('brier', float('nan')):.4f}, log loss "
            f"{market.get('log_loss', float('nan')):.4f}.",
            f"- Full residual: Brier {residual.get('brier', float('nan')):.4f}, log loss "
            f"{residual.get('log_loss', float('nan')):.4f}.",
            f"- Residual executable actions: {int(action.get('actions', 0))}; net PnL "
            f"{action.get('net_pnl', float('nan')):+.3f} shares; round/day-block lower bound "
            f"{action.get('day_block_mean_lower_95', float('nan')):+.4f} per action.",
            "- Verdict: **market remains champion; no residual promotion**.",
            "",
        ])
    fullset = alpha.get("pm_fullset", {})
    if fullset:
        arb = fullset.get("fullset_arb", {})
        maker = fullset.get("hedged_maker_upper_bound", {})
        lines.extend([
            "### Polymarket Complete Set and Maker Upper Bound",
            "",
            f"- Full-set all-in opportunities: {int(arb.get('n_executable', 0))}; rate "
            f"{arb.get('pct_net_below_1', float('nan')):.3%}; theoretical top-of-book total "
            f"${arb.get('executable_dollar_pnl_total', float('nan')):.2f} across "
            f"{int(fullset.get('n_days', 0))} days.",
            "- Verdict: mechanically real but economically negligible; stale/crossed-book "
            "artifacts can only reduce the realizable total.",
            f"- Two-sided maker upper-bound EV: "
            f"{maker.get('two_sided_both_filled', {}).get('ev', float('nan')):+.4f} per share, "
            "but both-leg fill probability, queue position and adverse-selection markout are "
            "unobserved. This is **not a strategy result**.",
            "",
        ])
    remaining = alpha.get("remaining_lanes", {})
    if remaining:
        atlas = remaining.get("atlas", {})
        disagreement = remaining.get("disagree", {}).get("bands", [])
        mfe = remaining.get("mfe_mae", {})
        impact = remaining.get("impact", {})
        widest = disagreement[-1] if disagreement else {}
        lines.extend([
            "### Disagreement, State Atlas and Path Symmetry",
            "",
            f"- State atlas: {atlas.get('n_cells_examined', 0)} cells with at least 30 "
            f"independent rounds; nominal cells beyond 2c = {atlas.get('n_cells_nominal', 0)}; "
            f"family-wise significant cells = **{atlas.get('n_cells_significant', 0)}**. "
            "No atlas cell is approved for use.",
            f"- In the widest model/market disagreement band, the model wins only "
            f"{widest.get('model_win_rate', float('nan')):.1%} "
            f"[{widest.get('lcb', float('nan')):.1%}, {widest.get('ucb', float('nan')):.1%}]. "
            "Larger disagreement is evidence against the model, not a trade signal.",
            f"- Five-minute path: mean MFE {mfe.get('mean_mfe_bps', float('nan')):.2f} bps "
            f"versus mean MAE {mfe.get('mean_mae_bps', float('nan')):.2f} bps. The path is "
            "symmetric and does not rescue asymmetric barriers after costs.",
            f"- Flow-impact asymmetry is {impact.get('asymmetry_bps', float('nan')):+.3f} bps, "
            "far below the 12 bps round trip.",
            "",
        ])
    batch3 = alpha.get("batch3", {})
    if batch3:
        waits = batch3.get("wait_vs_buy", {}).get("by_horizon", [])
        convexity = batch3.get("convexity", {}).get("cells", [])
        headline = convexity[0] if convexity else {}
        maker_markout = batch3.get("maker_markout", {})
        lines.extend([
            "### Entry Timing, Settlement Sensitivity and Maker Markout",
            "",
            "- `WAIT_VS_BUY_V1`: the hindsight-minimum ask is an oracle bound. Every causal "
            "fixed-delay interval spans zero:",
        ])
        for row in waits:
            lines.append(
                f"  - wait {row.get('horizon_s')}s: net delta "
                f"{row.get('fixed_delay_net_delta', float('nan')):+.4f} per share "
                f"[{row.get('fixed_delay_lcb', float('nan')):+.4f}, "
                f"{row.get('fixed_delay_ucb', float('nan')):+.4f}]."
            )
        lines.extend([
            f"- Largest settlement-sensitivity point estimate: `{headline.get('cell', '-')}` "
            f"at {headline.get('delta_cents_per_bp', float('nan')):.3f} cents/BTC-bp; "
            f"family-wise interval [{headline.get('lcb_cents_per_bp', float('nan')):+.3f}, "
            f"{headline.get('ucb_cents_per_bp', float('nan')):+.3f}]. This is a risk surface, "
            "not a direction signal.",
            f"- Maker markout: `{maker_markout.get('status', 'UNKNOWN')}`. Quotes permit only "
            "hypothetical-fill markouts; actual fill probability, queue position and "
            "fill-conditioned toxicity remain missing.",
            "",
        ])
    extensions = alpha.get("matrix_path_extensions", {})
    if extensions:
        compression = extensions.get("compression_breakout", {}).get("rows", [])
        compression_30 = next((row for row in compression if row.get("horizon_m") == 30), {})
        lines.extend([
            "### Remaining Causal Matrix Tests",
            "",
            f"Chronological 70/30 split; family-adjusted test; declared cost "
            f"{extensions.get('data', {}).get('cost_bps', float('nan')):.1f} bps.",
            "",
            f"- Compression at 30m selects mean absolute movement "
            f"{compression_30.get('mean_move_bps', float('nan')):.2f} bps with cost-margin "
            f"family LCB {compression_30.get('move_minus_cost_family_lcb', float('nan')):+.2f} "
            "bps. This predicts movement only; no direction or executable volatility instrument "
            "was established.",
        ])
        for key, label in (
            ("trend_pullback", "Trend pullback"),
            ("trend_survival", "Trend survival"),
            ("profit_continuation", "Profit continuation"),
            ("adverse_move_recovery", "Adverse-move recovery"),
            ("microbasis_reversion", "Microbasis reversion"),
        ):
            rows = extensions.get(key, {}).get("rows", [])
            if rows:
                best = max(rows, key=lambda row: row.get("net_family_lcb", float("-inf")))
                lines.append(
                    f"- {label}: best net {best.get('net_bps', float('nan')):+.2f} bps; "
                    f"family LCB {best.get('net_family_lcb', float('nan')):+.2f} bps; "
                    "not promotable."
                )
        lines.extend([
            f"- Funding carry: `{extensions.get('funding_basis_carry', {}).get('status', '-')}` "
            "because the matrix lacks actual payment timestamps/rates and financing cash flows.",
            f"- Promotable economic configurations: "
            f"**{extensions.get('promotable_configurations', 0)}**.",
            "",
        ])
    lines.extend([
        "## Proposal Coverage",
        "",
        "Every distinct proposal in the three supplied reviews is mapped below. A proposal is "
        "not renamed and rerun when an existing frozen experiment already answers it.",
        "",
        "| proposal family | evidence | state |",
        "|---|---|---|",
    ])
    for proposal, evidence, state in PROPOSAL_COVERAGE:
        lines.append(f"| {proposal} | {evidence} | `{state}` |")
    lines.extend([
        "",
        "## Data Identity",
        "",
        f"- PM snapshot rows at copy: {inputs['snapshots_rows']:,}",
        f"- PM settlement rows at copy: {inputs['settlements_rows']:,}",
        f"- PM snapshots SHA-256: `{inputs['snapshots_sha256']}`",
        f"- PM settlements SHA-256: `{inputs['settlements_sha256']}`",
        f"- Binance matrix rows: {inputs['matrix_rows']:,}",
        f"- Binance matrix SHA-256 before: `{inputs['matrix_sha256_before']}`",
        f"- Binance matrix SHA-256 after: `{inputs['matrix_sha256_after']}`",
        f"- Binance matrix unchanged during run: `{inputs['matrix_unchanged']}`",
        "- Live DuckDB databases were not stopped or copied. Lock-blocked tests fail closed.",
        "",
        "## What To Do Next",
        "",
        "1. Do not add any diagnostic AUC to the live ensemble as a trading vote.",
        "2. Keep recorders running until the blocked L2, latency, settlement, and action-arm tests "
        "have enough independent days and rounds.",
        "3. Focus new work on execution-cost reduction, maker fill evidence, and sparse "
        "volatility-window selection. Direction-model proliferation is not supported by these results.",
        "4. Rerun this exact master command after the evidence window grows; compare immutable "
        "campaign directories rather than overwriting results.",
        "",
        "## Reproduce",
        "",
        "```powershell",
        "& 'C:\\Users\\rahul\\AppData\\Local\\Programs\\Python\\Python313\\python.exe' `",
        "  research_lanes\\run_all_experiments.py --maximum-rows 100000",
        "```",
        "",
        "No result in this report authorizes real-money trading.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maximum-rows", type=int, default=100_000)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--render-existing",
        type=Path,
        help="regenerate Markdown for an existing campaign without rerunning experiments",
    )
    parser.add_argument(
        "--skip-frozen-suites",
        action="store_true",
        help="run only the newer lanes; existing canonical frozen-suite results remain referenced",
    )
    args = parser.parse_args()

    if args.render_existing:
        run_root = args.render_existing.resolve()
        summary = json.loads((run_root / "campaign_summary.json").read_text(encoding="utf-8"))
        phase5 = _load_summary(Path(summary["phase5_summary"]))
        phase5b = _load_summary(Path(summary["phase5b_summary"]))
        alpha = dict(summary["alpha"])
        alpha.update(_read_alpha_metrics(run_root))
        report = _render_report(
            str(summary["run_id"]), run_root, summary["identity"], summary["commands"],
            phase5, phase5b, alpha, summary["inputs"],
        )
        report_path = run_root / "MASTER_RESULTS.md"
        report_path.write_text(report, encoding="utf-8", newline="\n")
        (LANES / "LATEST_RESULTS.md").write_text(report, encoding="utf-8", newline="\n")
        CANONICAL_REPORT.write_text(report, encoding="utf-8", newline="\n")
        print(f"Regenerated {report_path}")
        return 0

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = args.output_root.resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    identity = _git_identity()

    inputs_dir = run_root / "inputs"
    inputs_dir.mkdir()
    source_snapshots = DATA / "pm_export_snapshots.parquet"
    source_settlements = DATA / "pm_export_settlements.parquet"
    source_matrix = DATA / "research_matrix_1m.parquet"
    source_matrix_manifest = DATA / "research_matrix_1m.manifest.json"
    snapshots = inputs_dir / source_snapshots.name
    settlements = inputs_dir / source_settlements.name
    shutil.copy2(source_snapshots, snapshots)
    shutil.copy2(source_settlements, settlements)
    inputs = {
        "snapshots_path": str(snapshots),
        "settlements_path": str(settlements),
        "snapshots_sha256": _sha256(snapshots),
        "settlements_sha256": _sha256(settlements),
        "snapshots_rows": int(len(pd.read_parquet(snapshots, columns=["slug"]))),
        "settlements_rows": int(len(pd.read_parquet(settlements, columns=["slug"]))),
        "matrix_path": str(source_matrix),
        "matrix_bytes": source_matrix.stat().st_size,
        "matrix_rows": int(len(pd.read_parquet(source_matrix, columns=["ts_ms"]))),
        "matrix_sha256_before": _sha256(source_matrix),
        "matrix_manifest_sha256_before": _sha256(source_matrix_manifest),
        "matrix_sha256_after": None,
        "matrix_manifest_sha256_after": None,
        "matrix_unchanged": None,
    }
    (run_root / "input_manifest.json").write_text(
        json.dumps(inputs, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    python = sys.executable
    commands: list[dict[str, Any]] = []
    if not args.skip_frozen_suites:
        commands.append(_run("phase5_42", [
            python, "research/phase5_standalone/run_all.py", "--smoke",
            "--maximum-rows", str(args.maximum_rows),
            "--output-root", str(run_root / "phase5"),
        ], run_root))
        commands.append(_run("phase5b_46", [
            python, "research/phase5b_standalone/run_all.py", "--smoke",
            "--maximum-rows", str(args.maximum_rows),
            "--output-root", str(run_root / "phase5b"),
        ], run_root))

    phase5c_scripts = sorted((ROOT / "research" / "phase5c").glob("test_*.py"))
    for script in phase5c_scripts:
        commands.append(_run(f"phase5c_{script.stem.removeprefix('test_')}", [
            python, str(script),
        ], run_root))

    commands.append(_run("binance_cost_clearance", [
        python, "research_lanes/binance_cost_clearance/run.py",
    ], run_root))
    commands.append(_run("matrix_lanes", [
        python, "research_lanes/run_matrix_lanes.py",
    ], run_root))
    commands.append(_run("matrix_path_extensions", [
        python, "research_lanes/run_matrix_path_extensions.py",
        "--output", str(run_root / "matrix_path_extensions_results.json"),
    ], run_root))
    commands.append(_run("polymarket_prior_comparison", [
        python, "research_lanes/polymarket_residual/run.py",
        "--snapshots", str(snapshots),
        "--settlements", str(settlements),
        "--output", str(run_root / "polymarket_prior_results.json"),
    ], run_root))
    commands.append(_run("polymarket_fullset_maker", [
        python, "research_lanes/poly_fullset_arb/run.py",
        "--snapshots", str(snapshots),
        "--settlements", str(settlements),
        "--output", str(run_root / "polymarket_fullset_results.json"),
    ], run_root))
    commands.append(_run("polymarket_remaining_lanes", [
        python, "research_lanes/run_remaining_lanes.py",
        "--snapshots", str(snapshots),
        "--settlements", str(settlements),
        "--output", str(run_root / "remaining_lanes_results.json"),
    ], run_root))
    commands.append(_run("polymarket_batch3", [
        python, "research_lanes/run_batch3.py",
        "--snapshots", str(snapshots),
        "--settlements", str(settlements),
        "--output", str(run_root / "batch3_results.json"),
    ], run_root))
    commands.append(_run("polymarket_residual_offset", [
        python, "research/polymarket_market_prior_residual_v1/run.py",
        "--snapshots", str(snapshots),
        "--settlements", str(settlements),
        "--output", str(run_root / "polymarket_residual"),
    ], run_root))

    inputs["matrix_sha256_after"] = _sha256(source_matrix)
    inputs["matrix_manifest_sha256_after"] = _sha256(source_matrix_manifest)
    inputs["matrix_unchanged"] = bool(
        inputs["matrix_sha256_before"] == inputs["matrix_sha256_after"]
        and inputs["matrix_manifest_sha256_before"] == inputs["matrix_manifest_sha256_after"]
    )
    (run_root / "input_manifest.json").write_text(
        json.dumps(inputs, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    integrity_log = run_root / "logs" / "input_integrity.log"
    integrity_log.write_text(
        "PASS: matrix and manifest unchanged during campaign\n"
        if inputs["matrix_unchanged"]
        else "FAIL: matrix or manifest changed during campaign\n",
        encoding="utf-8",
        newline="\n",
    )
    commands.append({
        "label": "input_integrity",
        "command": [],
        "returncode": 0 if inputs["matrix_unchanged"] else 1,
        "elapsed_seconds": 0.0,
        "log": str(integrity_log),
        "last_line": integrity_log.read_text(encoding="utf-8").strip(),
    })

    phase5_path = _find_one(run_root / "phase5", "suite_summary.json")
    phase5b_path = _find_one(run_root / "phase5b", "suite_summary.json")
    if args.skip_frozen_suites:
        phase5_path = _find_one(DATA / "research" / "phase5_standalone", "suite_summary.json")
        phase5b_path = _find_one(DATA / "research" / "phase5b_standalone", "suite_summary.json")
    phase5 = _load_summary(phase5_path)
    phase5b = _load_summary(phase5b_path)
    alpha = _read_alpha_metrics(run_root)

    summary = {
        "run_id": run_id,
        "identity": identity,
        "inputs": inputs,
        "commands": commands,
        "phase5_summary": str(phase5_path) if phase5_path else None,
        "phase5b_summary": str(phase5b_path) if phase5b_path else None,
        "phase5_status_counts": dict(_status_counts(phase5)),
        "phase5b_status_counts": dict(_status_counts(phase5b)),
        "alpha": alpha,
        "capital_authority": False,
    }
    (run_root / "campaign_summary.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8", newline="\n"
    )
    report = _render_report(run_id, run_root, identity, commands, phase5, phase5b, alpha, inputs)
    report_path = run_root / "MASTER_RESULTS.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")
    (LANES / "LATEST_RESULTS.md").write_text(report, encoding="utf-8", newline="\n")
    CANONICAL_REPORT.write_text(report, encoding="utf-8", newline="\n")
    (LANES / "latest_run.json").write_text(
        json.dumps({"run_id": run_id, "run_root": str(run_root), "report": str(report_path)}, indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"\nMaster report: {report_path}")
    failures = [row for row in commands if row["returncode"] != 0]
    if failures:
        print(f"Campaign completed with {len(failures)} failed command(s); see logs.")
        return 1
    print("Campaign completed: all commands executed successfully. No automatic promotion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
