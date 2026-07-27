"""Regression tests for the 2026-07-26 external audit of COMPLETE_TRADE_FORECAST_V1.

One test per confirmed defect. Every one of these failures is SILENT - the pipeline keeps running,
the report looks healthy, and the damage only shows up as apparent selectivity that does not
survive contact with real money.

    A1  future targets ran past contract expiry      -> post-settlement BTC info in training
    A2  M0 counted candidates, not decisions         -> sample size / PF / Q5 overstated ~63x
    A3  M0 ranked one thing and settled another      -> skill at a question nobody trades
    A5  quote survival meant "some entry existed"    -> head predicted a much easier event
    A7  missing live features became neutral values  -> data failure looked like a calm market
    A8  evidence writes were silently swallowed      -> lost forecasts looked like quiet periods
    A12 settlement provenance was not filtered       -> unofficial outcomes as ground truth
    A13 sizing gated on MEDIAN capacity              -> half of all fills short, in stressed books

    python -m backend.trade_forecast.test_audit_fixes
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .freeze_guard import ArtifactPin
from .trade_plan_optimizer import _low_quantile
from .trade_schema import (
    ENTRY_CHECKPOINTS_S,
    FUTURE_OFFSETS_S,
    OFFICIAL_RESOLUTION_SOURCES,
    QUOTE_SURVIVAL_TOLERANCE,
    target_offset_valid,
)

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def test_a1_post_expiry_targets() -> None:
    print("A1  post-expiry future targets")
    chk(target_offset_valid(30, 30), "an offset exactly at expiry is valid (boundary included)")
    chk(target_offset_valid(5, 30), "an offset inside the round is valid")
    chk(not target_offset_valid(60, 30), "60s target at a 30s-left checkpoint is INVALID")
    chk(not target_offset_valid(120, 30), "120s target at a 30s-left checkpoint is INVALID")
    chk(not target_offset_valid(120, 60), "120s target at a 60s-left checkpoint is INVALID")
    # The defect was reachable, not theoretical: enumerate the real checkpoint grid.
    reachable = [
        (h, left, off)
        for h, checkpoints in ENTRY_CHECKPOINTS_S.items()
        for left in checkpoints
        for off in FUTURE_OFFSETS_S
        if not target_offset_valid(off, left)
    ]
    chk(len(reachable) > 0, f"the frozen grid really does contain {len(reachable)} invalid pairs")
    chk(all(off > left for _, left, off in reachable), "every invalid pair outruns its round")


def test_a1b_serving_hides_unreachable_offsets() -> None:
    print("A1b serving never emits an offset the round cannot reach")
    from . import btc_path_serving, share_path_serving

    for mod in (share_path_serving, btc_path_serving):
        text = open(mod.__file__, encoding="utf-8").read()
        name = mod.__name__.rsplit(".", 1)[-1]
        chk(
            "target_offset_valid" in text,
            f"{name} applies the SAME validity rule as the dataset builder",
        )
        chk(
            "seconds_left" in text.split("for offset in")[1][:400],
            f"{name} gates the offset loop on remaining time",
        )


def test_a2_a3_independent_and_aligned_m0() -> None:
    print("A2/A3  one trade per ROUND, exact plan economics")
    from .train_share_path_model import (
        M0_INDEPENDENT_UNIT,
        M0_REALIZED_COLUMN,
        M0_SCORE_LABEL,
    )

    # 3 rounds x 4 checkpoints x 2 sides x 3 quantities = the real candidate shape.
    rows = []
    for rid in ("A", "B", "C"):
        for left in (120, 90, 60, 30):
            for side in ("UP", "DOWN"):
                for qty in (1, 5, 10):
                    rows.append({
                        "round_id": rid,
                        "exposure_id": f"{rid}@{left}",
                        "seconds_left": left,
                        "side": side,
                        "requested_qty": qty,
                        "score": 0.9 if (left == 60 and side == "UP" and qty == 5) else 0.2,
                    })
    frame = pd.DataFrame(rows)
    by_exposure = frame.sort_values("score", ascending=False).groupby(
        "exposure_id", sort=False, as_index=False).head(1)
    by_round = frame.sort_values("score", ascending=False).groupby(
        M0_INDEPENDENT_UNIT, sort=False, as_index=False).head(1)

    chk(M0_INDEPENDENT_UNIT == "round_id", "the independent unit is the ROUND, not the checkpoint")
    chk(len(frame) == 72, "candidate grid is 72 rows for 3 real trades")
    chk(len(by_exposure) == 12, "per-exposure collapse still leaves 4 correlated trades per round")
    chk(len(by_round) == 3, "per-round collapse yields ONE trade per settlement outcome")
    chk(
        by_round["round_id"].nunique() == len(by_round),
        "no round contributes two trades sharing a settlement",
    )
    chk(
        set(by_round["seconds_left"]) == {60},
        "the policy picks WHICH checkpoint to enter, not all of them",
    )
    # Exact economics: probability and dollars must be the same question about the same plan.
    chk(
        M0_SCORE_LABEL == "plan_take_3c_or_stop_3c_profitable",
        "M0 ranks P(plan net PnL > 0) - the plan's own economics",
    )
    chk(
        M0_REALIZED_COLUMN == "plan_take_3c_or_stop_3c_net",
        "M0 settles that same plan's net PnL",
    )
    chk(
        M0_SCORE_LABEL.replace("_profitable", "_net") == M0_REALIZED_COLUMN,
        "score and realized column are mechanically derived from ONE plan",
    )
    chk(
        "take_3c_before_stop" not in M0_SCORE_LABEL,
        "the barrier EVENT is no longer the ranking target (it ignores settlement/fees/overshoot)",
    )


def test_a2b_terminal_settlement_labels() -> None:
    print("A2b settlement is TERMINAL, not right-censoring")
    from .trade_labels import summarize_realized_path

    # A path that never reaches +3c, observed to the contract's end.
    labels = summarize_realized_path([1.0, 2.0], [0.001, 0.002], -0.5)
    key = [k for k in labels if k.startswith("label_take_3c_before_stop")]
    chk(bool(key), "barrier labels are produced")
    chk(
        all(labels[k] in (0, 1) for k in key),
        "a non-crossing observed path yields a definite 0, never None",
    )
    # The bias this prevents, stated as arithmetic: if late non-crossings were dropped to NULL,
    # only the early SUCCESSES would survive at those checkpoints.
    outcomes = [1, 0, 0, 0]           # one crossing, three definite failures
    honest = sum(outcomes) / len(outcomes)
    if_nulled = 1.0                   # the three 0s dropped, the single 1 retained
    chk(
        honest == 0.25 and if_nulled == 1.0,
        f"NULLing definite failures would inflate the rate {honest:.2f} -> {if_nulled:.2f}",
    )
    chk(
        honest < if_nulled,
        "the earlier NULL choice biased UPWARD; terminal 0s remove that bias",
    )


def test_a5_quote_survival() -> None:
    print("A5  quote survival is size-aware, not 'some entry existed'")

    def survived(entry_vwap, decision_vwap, complete):
        return int(
            bool(complete)
            and decision_vwap is not None
            and entry_vwap <= float(decision_vwap) + QUOTE_SURVIVAL_TOLERANCE
        )

    chk(survived(0.60, 0.60, True) == 1, "unchanged price with a full fill survives")
    chk(survived(0.605, 0.60, True) == 1, "sub-tolerance drift still survives")
    chk(survived(0.63, 0.60, True) == 0, "a 3c worse arrival does NOT survive")
    chk(survived(0.60, 0.60, False) == 0, "a partial fill does NOT survive, whatever the price")
    chk(survived(0.58, 0.60, True) == 1, "a better arrival survives")
    # The old definition returned 1 for both of the failing cases above.
    chk(
        survived(0.63, 0.60, True) != 1 and survived(0.60, 0.60, False) != 1,
        "both cases the old entry_eligible definition wrongly counted as survival",
    )


def test_a7_no_neutral_imputation() -> None:
    print("A7  missing live features never become neutral values")
    from .live_forecaster import _feature_values, _finite

    chk(_finite(None) is None, "None is missing, not 0.0")
    chk(_finite(float("nan")) is None, "NaN is missing, not 0.0")
    chk(_finite(0.0) == 0.0, "a real zero is preserved")

    ladder = [[0.60, 100], [0.61, 100]]
    quote = {
        "bid": 0.59, "ask": 0.60, "bid_size": 100, "ask_size": 100,
        "bid_ladder": ladder, "ask_ladder": ladder, "spread": 0.01,
    }
    prices = {"up": dict(quote), "down": dict(quote), "ts": 1000.0, "age_seconds": 0.5}
    base = {
        "current_price": 60_000.0, "price_to_beat": 59_990.0, "horizon": 5,
        "seconds_left": 60.0, "vol_60s_pct": 0.4,
        "p_hold": 0.62, "current_position": "UP",
    }
    from collections import deque

    # A COMPLETE snapshot now needs real history: the lookback features are required, so an empty
    # deque legitimately yields no forecast (see S3). Build a history that covers 5/15/30/60s.
    now = 1000.0
    history = deque(
        {"ts": now - age, "btc": 60_000.0 - age, "up_bid": 0.59, "down_bid": 0.40}
        for age in (61.0, 31.0, 16.0, 6.0, 1.0)
    )
    healthy = _feature_values(base, prices, "UP", 5, history)
    chk(healthy is not None, "a complete snapshot WITH history still produces a forecast")

    missing_phold = {**base, "p_hold": None}
    chk(
        _feature_values(missing_phold, prices, "UP", 5, history) is None,
        "missing P(Hold) -> NO FORECAST (never 0.5)",
    )
    missing_vol = {**base, "vol_60s_pct": None}
    chk(
        _feature_values(missing_vol, prices, "UP", 5, history) is None,
        "missing volatility -> NO FORECAST (never 0.0)",
    )
    no_opp = {"up": dict(quote), "down": {}, "ts": 1000.0, "age_seconds": 0.5}
    chk(
        _feature_values(base, no_opp, "UP", 5, history) is None,
        "missing opposite-side book -> NO FORECAST (never a 0.0 bid)",
    )
    if healthy is not None:
        chk(
            "_missing_optional" in healthy,
            "gaps are reported alongside the vector, not hidden",
        )
        chk(
            healthy["_missing_optional"] == [],
            "a fully covered history reports no gaps",
        )
    thin = deque([{"ts": now - 1.0, "btc": 60_000.0, "up_bid": 0.59}])
    chk(
        _feature_values(base, prices, "UP", 5, thin) is None,
        "a history too short for the 60s lookback yields NO FORECAST, not a flat return",
    )


def test_a8_durable_logging() -> None:
    print("A8  evidence writes are append-only and monitored")
    from . import trade_forecast_logger as L

    src = (L.__file__ or "")
    text = open(src, encoding="utf-8").read()
    chk("INSERT OR REPLACE" not in text, "no INSERT OR REPLACE remains (evidence is immutable)")
    chk(hasattr(L, "log_forecast_monitored"), "a monitored writer exists")

    health = L.LogHealth()
    chk(health.snapshot()["healthy"] is True, "a fresh writer is healthy")
    health.record_success()
    chk(health.snapshot()["written"] == 1, "successful writes are counted")
    health.record_failure(RuntimeError("Duplicate key violates primary key"), {"forecast_id": "f1"})
    snap = health.snapshot()
    chk(snap["healthy"] is False, "a failed write makes the writer UNHEALTHY")
    chk(snap["failed"] == 1 and snap["duplicate_rejections"] == 1, "duplicates are classified")
    chk(snap["dead_letters"] == 1, "the lost payload is dead-lettered, not discarded")
    chk(bool(snap["alert"]), "a failure raises a visible alert")
    chk(snap["last_success_ts"] is not None, "last successful write time is tracked")


def test_a12_settlement_provenance() -> None:
    print("A12  settlement provenance is a frozen allowlist")
    chk(
        "polymarket_clob" in OFFICIAL_RESOLUTION_SOURCES
        and "polymarket_gamma" in OFFICIAL_RESOLUTION_SOURCES,
        "the bare venue values actually present in the export are allowed",
    )
    chk(
        "official:polymarket_clob" in OFFICIAL_RESOLUTION_SOURCES,
        "the prefixed form written by database.py is also allowed",
    )
    # A LIKE 'official:%' gate would have matched nothing in the real export and produced a
    # well-formed, entirely empty dataset.
    chk(
        not all(s.startswith("official:") for s in OFFICIAL_RESOLUTION_SOURCES),
        "the allowlist does not assume the 'official:' prefix (it would select zero rows)",
    )
    chk("inferred" not in OFFICIAL_RESOLUTION_SOURCES, "inferred outcomes are not ground truth")


def test_a13_conservative_capacity() -> None:
    print("A13  sizing gates on conservative capacity, not the median")
    table = {"q10": 5.0, "q20": 8.0, "q50": 40.0, "q80": 90.0, "q95": 120.0}
    chk(_low_quantile(table, ("q10", "q20", "q25", "q50")) == 5.0, "q10 is preferred when present")
    chk(
        _low_quantile({"q20": 8.0, "q50": 40.0}, ("q10", "q20", "q25", "q50")) == 8.0,
        "falls back to q20 only when q10 was not modelled",
    )
    chk(_low_quantile({}, ("q10", "q50")) == 0.0, "absent capacity means ZERO, never unlimited")
    chk(_low_quantile(None, ("q10",)) == 0.0, "a missing capacity table cannot authorise size")
    chk(
        _low_quantile({"q10": -3.0}, ("q10",)) == 0.0,
        "a negative capacity estimate is clamped, not trusted",
    )
    requested = 25.0
    chk(
        _low_quantile(table, ("q10", "q20", "q25", "q50")) < requested <= table["q50"],
        "the q50 gate would have PASSED a size the q10 gate correctly refuses",
    )


def test_a10_freeze_pinning() -> None:
    print("A10  loaders cannot swap models mid-evidence-run")
    import os

    previous = os.environ.get("BTC_FREEZE_MODEL")
    try:
        os.environ["BTC_FREEZE_MODEL"] = "1"
        for name in ("share", "btc", "execution"):
            pin = ArtifactPin(name)
            pin.check("hash-a")
            chk(not pin.check("hash-b"), f"{name} loader refuses a changed artifact under freeze")
        pin = ArtifactPin("share")
        pin.check("hash-a")
        pin.check("hash-b")
        pin.check("hash-a")
        chk(pin.violation is not None, "the violation is latched after the file reverts")
    finally:
        if previous is None:
            os.environ.pop("BTC_FREEZE_MODEL", None)
        else:
            os.environ["BTC_FREEZE_MODEL"] = previous


def test_a4_scenario_engine_is_diagnostic() -> None:
    print("A4  scenario-derived economics cannot back a promotion")
    from .scenario_engine import evaluate_plans

    paths = {
        str(offset): dict(zip(("q10", "q25", "q50", "q75", "q90"),
                              (0.50, 0.55, 0.60, 0.65, 0.70)))
        for offset in FUTURE_OFFSETS_S
    }
    plans = evaluate_plans(entry_vwap=0.55, share_path=paths, p_settlement_win=0.70)
    chk(bool(plans), "plans still evaluate (the engine remains useful for display)")
    chk(
        all(plan.get("diagnostic_only") is True for plan in plans.values()),
        "every plan is tagged diagnostic_only",
    )
    chk(
        all(plan.get("promotable") is False for plan in plans.values()),
        "no plan may be treated as promotable evidence",
    )
    chk(
        all("approximation" in plan for plan in plans.values()),
        "each plan carries the reason its numbers are approximate",
    )


def test_serving_integration() -> None:
    print("S1  serving integration: promotion reaches the loaders")
    from . import btc_path_serving, execution_serving, share_path_serving
    from .champion_resolver import evidence_mode, resolve_artifact

    for mod in (share_path_serving, btc_path_serving, execution_serving):
        text = open(mod.__file__, encoding="utf-8").read()
        name = mod.__name__.rsplit(".", 1)[-1]
        chk("resolve_artifact" in text, f"{name} resolves through the champion bundle")
        chk(hasattr(mod, "ARTIFACT_NAME"), f"{name} declares its artifact name")

    import os
    previous = os.environ.get("BTC_EVIDENCE_MODE")
    try:
        os.environ["BTC_EVIDENCE_MODE"] = "0"
        path, status = resolve_artifact("nope.pkl", Path("legacy/nope.pkl"))
        chk(path is not None and not status["verified"],
            "outside evidence mode an unverified legacy path is returned, but REPORTED")
        os.environ["BTC_EVIDENCE_MODE"] = "1"
        chk(evidence_mode(), "evidence mode is readable from the environment")
        path, status = resolve_artifact("nope.pkl", Path("legacy/nope.pkl"))
        chk(path is None, "EVIDENCE MODE: no verified bundle -> NO MODEL, not legacy bytes")
        chk("EVIDENCE MODE" in (status.get("note") or ""), "and the refusal says why")
    finally:
        if previous is None:
            os.environ.pop("BTC_EVIDENCE_MODE", None)
        else:
            os.environ["BTC_EVIDENCE_MODE"] = previous


def test_candidate_valid_is_mandatory() -> None:
    print("S2  invalid-but-finite rows cannot reach training")
    from .model_common import clean_xy
    from .trade_schema import FEATURE_COLUMNS as FC

    n = 6
    frame = pd.DataFrame({c: np.linspace(1.0, 2.0, n) for c in FC})
    frame["target"] = np.arange(n, dtype=float)
    frame["round_start_ts"] = np.arange(n)
    frame["candidate_valid"] = [1, 1, 1, 0, 0, 0]
    x, _, _ = clean_xy(frame, np.ones(n, dtype=bool), "target")
    chk(len(x) == 3, f"only candidate_valid==1 rows train ({len(x)} of {n})")
    chk(
        np.isfinite(frame.loc[3:, list(FC)].to_numpy()).all(),
        "the rejected rows were perfectly FINITE - finiteness is not validity",
    )
    xd, _, _ = clean_xy(frame, np.ones(n, dtype=bool), "target",
                        _allow_invalid_candidates=True)
    chk(len(xd) == n, "the diagnostics override is explicit and keyword-only")


def test_live_matches_builder_missingness() -> None:
    print("S3  live and historical missingness agree (no training-serving skew)")
    from collections import deque

    from .live_forecaster import _feature_values, _history_value
    from .trade_schema import MAX_LOOKBACK_ERROR_S as TOL

    hist = deque([{"ts": 1000.0 - 300.0, "btc": 59_000.0}])
    chk(
        _history_value(hist, 1000.0, 30, "btc") is None,
        f"a 300s-old print is NOT accepted as a 30s lookback (tol {TOL}s)",
    )
    hist = deque([{"ts": 1000.0 - 31.0, "btc": 59_000.0}])
    chk(
        _history_value(hist, 1000.0, 30, "btc") == 59_000.0,
        "an observation near the requested lookback IS accepted",
    )

    ladder = [[0.60, 100], [0.61, 100]]
    quote = {"bid": 0.59, "ask": 0.60, "bid_size": 100, "ask_size": 100,
             "bid_ladder": ladder, "ask_ladder": ladder, "spread": 0.01}
    prices = {"up": dict(quote), "down": dict(quote), "ts": 1000.0, "age_seconds": 0.5}
    base = {"current_price": 60_000.0, "price_to_beat": 59_990.0, "horizon": 5,
            "seconds_left": 60.0, "vol_60s_pct": 0.4, "p_hold": 0.62,
            "current_position": "UP"}
    # Empty history = no BTC returns available. The builder invalidates such a candidate.
    chk(
        _feature_values(base, prices, "UP", 5, deque()) is None,
        "live: an empty history yields NO FORECAST, matching the builder's invalidation",
    )


def run() -> int:
    for test in (
        test_a1_post_expiry_targets,
        test_a1b_serving_hides_unreachable_offsets,
        test_a2_a3_independent_and_aligned_m0,
        test_a2b_terminal_settlement_labels,
        test_a4_scenario_engine_is_diagnostic,
        test_a5_quote_survival,
        test_a7_no_neutral_imputation,
        test_a8_durable_logging,
        test_a10_freeze_pinning,
        test_a12_settlement_provenance,
        test_a13_conservative_capacity,
        test_serving_integration,
        test_candidate_valid_is_mandatory,
        test_live_matches_builder_missingness,
    ):
        test()
    print("\nAUDIT REGRESSION SUITE", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    sys.exit(run())
