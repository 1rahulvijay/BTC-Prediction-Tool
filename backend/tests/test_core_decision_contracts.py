"""Five core-lifecycle defects that let a wrong number reach a live decision.

    python backend/tests/test_core_decision_contracts.py

Each was verified in source before being fixed, and each is the same shape: a guard that exists
and is correct, sitting beside a path that does not consult it.

1. PRODUCTION STARTUP WAS IMPOSSIBLE
   `preflight_issues()` called `environment_issues(env, mode="production")`. `mode` is the
   EXECUTION mode - paper vs real money - and "production" is not even a valid `--mode` choice,
   so `environment_issues` appended "only paper mode is implemented" every time. The lifespan
   hook raises on any issue, so BTC_DEPLOYMENT_ENV=production could never start, and no
   environment variable could clear it. The selftest exercised mode="paper" and mode="live",
   never the value the function actually passed.

2. TRAINING AND LIVE GRADING USED DIFFERENT NEUTRAL BANDS
   Labels come from `compute_adaptive_threshold_series` - 0.15 * EWMA(ATR) / EWMA(price), span
   100, 10-bar warm-up. Serving recomputed the band inline from the INSTANTANEOUS ATR and price.
   Same floor, same cap, different series, so the model could predict its own training label
   correctly and be graded at a different barrier width. The comment above the inline expression
   claimed it was the "SAME cost-floored adaptive formula as the training labels".

3. CALIBRATION ADMISSIBILITY WAS NEVER CONSULTED
   `is_admissible_for()` was correct and had ZERO production callers. The serving path attached
   an endpoint-move-sign map with UNRECORDED provenance to every first-touch prediction, and the
   live quality gate consumed it.

4. A SURROGATE'S SCORE GATED THE REAL ENSEMBLE
   `walk_forward_validate` defaults to a standalone RandomForest - no seats, no stacker, no HMM
   routing, no policy - and five of its outputs fed `build_meta_context`, which decides whether
   a prediction executes.

5. PROMOTION FLOORS ADMITTED WORSE THAN NOTHING
   directional precision >= 0.48 is below a coin flip. Multiclass Brier <= 0.80 sits above the
   uniform 2/3, so a model could be promoted while being beaten by predicting the base rates.
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import ast
import sys
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
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
    """Source minus docstrings and comments - this file quotes the removed expressions."""
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


PROD_ENV = {
    "BTC_DEPLOYMENT_ENV": "production", "BTC_STRICT_ARTIFACT_IDENTITY": "1",
    "BTC_FREEZE_MODEL": "1", "BTC_RUN_STARTUP_BACKTEST": "0",
    "BTC_REQUIRE_ADMIN_TOKEN": "1", "BTC_SERVE_FRONTEND": "1", "BTC_EVIDENCE_MODE": "1",
    "BTC_REQUIRE_POLYMARKET_FEED": "1", "BTC_REQUIRE_PROTOCOL_HEALTH": "1",
    "BTC_BIND_HOST": "127.0.0.1", "BTC_ADMIN_TOKEN": "t" * 40,
    "BTC_CONTROL_TOKEN": "c" * 40, "BTC_ALLOWED_ORIGINS": "http://127.0.0.1:3000",
}


def main() -> int:
    print("1. a correct production environment can actually start")
    import production_readiness as pr

    issues = pr.environment_issues(dict(PROD_ENV), mode=pr.PRODUCTION_EXECUTION_MODE)
    chk(not issues,
        f"a fully-correct production env yields NO issues under the mode preflight passes "
        f"({issues})")
    chk(any("only paper mode" in i
            for i in pr.environment_issues(dict(PROD_ENV), mode="production")),
        "while the old mode= value still produces the irremovable issue - proving the block "
        "was real and that this test would have caught it")
    chk(any("real-money" in i for i in pr.environment_issues(dict(PROD_ENV), mode="live")),
        "and mode='live' is STILL refused - real-money execution was not quietly enabled")
    # The exact lifespan call, not a paraphrase of it. The old selftest's gap was that it never
    # invoked the function the server actually invokes.
    chk("mode=PRODUCTION_EXECUTION_MODE" in code_only(BACKEND / "production_readiness.py"),
        "preflight_issues() passes the named execution-mode constant, not a deployment name")
    chk(pr.PRODUCTION_EXECUTION_MODE == "paper",
        "and that constant is 'paper' - a production DEPLOYMENT still runs paper EXECUTION")

    print("2. the live neutral band is the training band")
    from features import atr, compute_adaptive_threshold, compute_adaptive_threshold_series
    from model import MultiModelEnsemble

    rng = np.random.default_rng(11)
    n = 2500
    closes = 60000.0 * np.exp(np.cumsum(rng.normal(0, 0.0006, n)))
    # The volatility jump is placed in the LAST 30 bars deliberately. A jump at the midpoint
    # lets the span-100 EWMA converge long before the final bar, so the two formulas agree
    # again and the fixture proves nothing - the first version of this check passed by 1e-6
    # against a 1e-6 threshold. Divergence between an instantaneous reading and a smoothed one
    # is largest exactly when volatility has just changed, which is when the grading barrier
    # matters most.
    span = np.full(n, 0.0006)
    span[-30:] = 0.010
    highs, lows = closes * (1 + span), closes * (1 - span)
    klines = [{"close": float(c), "high": float(h), "low": float(lo)}
              for c, h, lo in zip(closes, highs, lows)]

    m = MultiModelEnsemble.__new__(MultiModelEnsemble)          # no training needed for this method
    live_band = m.causal_neutral_band(klines)
    training_band = float(compute_adaptive_threshold_series(closes, atr(highs, lows, closes))[-1])
    chk(abs(live_band - training_band) < 1e-12,
        f"serving reproduces the training threshold EXACTLY on the same bars "
        f"({live_band:.8f} vs {training_band:.8f})")

    # The old inline expression, for contrast. It is not a rounding difference.
    atr_last = float(atr(highs, lows, closes)[-1])
    old_band = max(0.0008, min(0.003, atr_last / float(closes[-1]) * 0.15))
    chk(old_band > training_band * 1.5,
        f"whereas the instantaneous expression it replaced gives {old_band:.8f} against the "
        f"training band's {training_band:.8f} - {old_band/training_band:.1f}x wider, so the "
        f"same move was graded DIRECTIONAL by training and NEUTRAL at verify time")
    chk(compute_adaptive_threshold(closes, atr(highs, lows, closes)) == training_band,
        "and the function serving now calls is the same one training uses, not a copy that "
        "can drift from it")
    chk(m.causal_neutral_band([]) == 0.0008 and m.causal_neutral_band(None) == 0.0008,
        "with too little history it returns the cost floor - the series' own warm-up value - "
        "rather than inventing a thinner band")

    print("3. calibration refuses an inadmissible contract at the API, not at the caller")
    from calibration import PrecisionEngine

    e = PrecisionEngine()

    class _Iso:
        @staticmethod
        def predict(xs):
            return [0.9]

    e.calibrators = {5: _Iso()}
    e.global_rate = {5: 0.6}
    e.bins = {5: {}}
    chk(e.contract_provenance == "UNRECORDED", "provenance starts UNRECORDED")
    chk(e.calibrated(5, 0.7) is not None,
        "an undeclared consumer still gets a value - the change is opt-in, not a silent "
        "removal of calibration for callers that genuinely do not care")
    chk(e.calibrated(5, 0.7, required_contract="first_touch_triple_barrier_v1") is None,
        "but a consumer that DECLARES its contract gets None while provenance is UNRECORDED, "
        "without having to remember a separate is_admissible_for() call")
    chk(e.expected_precision(5, "RANGE", 0.5,
                             required_contract="first_touch_triple_barrier_v1") is None,
        "and the precision bins refuse on the same grounds - they are fitted by the same rule")
    e.contract_provenance = "RECORDED"
    e.fitted_under_contract = "first_touch_triple_barrier_v1"
    chk(e.calibrated(5, 0.7, required_contract="first_touch_triple_barrier_v1") is not None,
        "once provenance is RECORDED and the contract matches, the map is served again")
    chk(e.calibrated(5, 0.7, required_contract="endpoint_settlement_v1") is None,
        "and a different contract is still refused")

    server_code = code_only(BACKEND / "server.py")
    chk("required_contract=_need" in server_code,
        "the serving path declares the contract it needs")
    chk("precision_engine.active_bundle_id = str(" not in server_code
        and "precision_engine.bind_release(" in server_code,
        "and it calls bind_release() rather than assigning active_bundle_id - the direct "
        "assignment meant the clearing logic had NO production caller at all")

    print("4. a surrogate walk-forward cannot gate the live ensemble")
    from backtester import VALID_WF_MODEL_KINDS, walk_forward_validate

    X = rng.normal(size=(900, 6))
    y = (X[:, 0] + rng.normal(0, 1.0, 900) > 0).astype(int) * 2
    res = walk_forward_validate(X, y, n_folds=3, embargo=5)
    chk(res.get("model_kind") == "SURROGATE_RESEARCH_ONLY",
        "the default RandomForest run DECLARES itself a surrogate in its own result")
    chk(walk_forward_validate(X[:10], y[:10], n_folds=3).get("model_kind")
        == "SURROGATE_RESEARCH_ONLY",
        "including on the insufficient-data early return, so a consumer never sees a result "
        "with no declaration")
    try:
        walk_forward_validate(X, y, n_folds=3, model_kind="LOOKS_LEGIT")
        bad = False
    except ValueError as exc:
        bad = "not one of" in str(exc)
    chk(bad, "an unrecognised kind raises rather than passing through unclassified")
    chk("SURROGATE_RESEARCH_ONLY" in VALID_WF_MODEL_KINDS
        and "PRODUCTION_BUNDLE_REPLAY" in VALID_WF_MODEL_KINDS,
        "and the vocabulary is declared in one place")
    chk('_wf_kind not in ("PRODUCTION_BUNDLE_REPLAY", "PRODUCTION_PIPELINE_WALK_FORWARD")'
        in server_code and "wf_res = {}" in server_code,
        "build_meta_context DROPS the walk-forward fields unless the run declares a production "
        "kind - an absent declaration is treated as surrogate, which is what the default is")

    print("5. promotion floors are baseline-relative, not below chance")
    import model_promotion as mp

    gates = mp.promotion_gates()
    chk(gates["min_directional_precision"] >= 0.50,
        f"directional precision floor is at least a coin flip "
        f"({gates['min_directional_precision']}) - it was 0.48")
    chk(gates["max_multiclass_brier"] <= mp.UNIFORM_3CLASS_BRIER + 1e-12,
        f"and the Brier ceiling no longer admits worse than uniform "
        f"({gates['max_multiclass_brier']:.4f} vs uniform {mp.UNIFORM_3CLASS_BRIER:.4f}) - "
        f"it was 0.80")
    # The arithmetic, demonstrated rather than asserted: a uniform forecast scores exactly the
    # ceiling under this module's own metric function.
    uniform = np.full((600, 3), 1 / 3.0)
    labels = rng.integers(0, 3, 600)
    chk(abs(mp.probability_metrics(uniform, labels)["multiclass_brier"]
            - mp.UNIFORM_3CLASS_BRIER) < 1e-9,
        "measured through probability_metrics itself, a uniform forecast scores exactly 2/3")
    # And the prior baseline adapts to imbalance where a literal cannot.
    skewed = np.where(rng.uniform(size=4000) < 0.8, 1, rng.integers(0, 3, 4000))
    prior_b = mp.class_prior_brier(skewed)
    chk(prior_b < mp.UNIFORM_3CLASS_BRIER - 0.05,
        f"on a NEUTRAL-heavy holdout the class-prior bar is {prior_b:.4f}, far below uniform "
        f"{mp.UNIFORM_3CLASS_BRIER:.4f} - which is exactly why a fixed ceiling cannot express "
        f"'better than knowing nothing'")
    chk(abs(mp.class_prior_brier(np.array([0, 1, 2] * 100)) - mp.UNIFORM_3CLASS_BRIER) < 1e-9,
        "and it reduces to the uniform value when the priors ARE uniform")
    chk("brier_not_better_than_class_prior" in code_only(BACKEND / "model_promotion.py"),
        "and a candidate that fails to beat that bar is refused by name")

    print("6. the promotion holdout is purged")
    from features import LOOKBACK

    class _Always:
        """Votes UP on every row - the interface evaluate_candidate actually calls."""
        horizons = [5]
        is_trained = True

        @staticmethod
        def predict_base(_row, _horizon, _context):
            return np.array([0.0, 0.0, 1.0], dtype=np.float64)

    rows = LOOKBACK + 5 + 400
    Xf = rng.normal(size=(rows, 4)).astype(np.float32)
    Yf = {5: np.tile([0.0, 0.0, 1.0], (rows, 1)).astype(np.float32)}
    rep = mp.evaluate_candidate(_Always(), None, Xf, Yf, 0)
    scored = rep["horizons"][5]["candidate"]["samples"]
    chk(scored == rows - (LOOKBACK + 5),
        f"the holdout starts LOOKBACK+horizon after split_idx: {scored} scored of {rows} rows, "
        f"a {LOOKBACK + 5}-row gap. It previously began AT split_idx, so training sequences "
        f"sharing lookback bars - with labels reaching into the holdout - were graded")
    tiny = mp.evaluate_candidate(_Always(), None, Xf[:LOOKBACK + 3], {5: Yf[5][:LOOKBACK + 3]}, 0)
    chk(any("holdout_too_small_after_purge" in r
            for r in tiny["horizons"][5]["reasons"]),
        "and a holdout too small to survive the gap REFUSES rather than silently shrinking it - "
        "the same rule test_oof_serving_parity enforces on the stacker folds")

    print("\nCORE DECISION CONTRACTS:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
