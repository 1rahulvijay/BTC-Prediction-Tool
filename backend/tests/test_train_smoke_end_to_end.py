"""EXECUTE MultiModelEnsemble.train() end to end on a miniature dataset.

WHY THIS EXISTS
    Nothing in CI called `train()`. The OOF fold weighting - including the fold-local
    regime-similarity repair - was verified by parsing `model.py` and by exercising its helper
    functions. Neither can catch an integration error: a NameError in the fold block, a shape
    mismatch, a seat that silently vanishes, or a bundle that does not survive a save/load
    round trip would have passed every check and failed on the first real retrain.

    This is deliberately SMALL - one horizon, a few thousand rows, two folds. It is not a
    performance test and asserts nothing about accuracy. It answers one question: does the
    real training path run, and does what it produces reload and predict identically?

    Slow by the standards of the other invariants (minutes, not seconds) because it fits real
    estimators. That is the point; the fast checks already exist and were not sufficient.

    python backend/tests/test_train_smoke_end_to_end.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import os
import sys
import tempfile
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Keep the run miniature. Set BEFORE importing model so module constants pick them up.
os.environ.setdefault("BTC_SAMPLE_WEIGHT_MODE", "recency_similarity")
# The OOF fold size is governed by THIS, not by the row count: X_stack is a tail slice of
# at most STACKER_MAX_SAMPLES rows, split into 5 folds. At 1200 the folds were ~235 rows and
# the purged calibration protocol refused every seat - raising the dataset size did nothing.
# Kept at the production default so the folds the test exercises are the folds production uses.
os.environ.setdefault("BTC_STACKER_MAX_SAMPLES", "6000")
os.environ.setdefault("BTC_TCN_MAX_SAMPLES", "0")          # skip the sequence seat
os.environ.setdefault("BTC_TRAIN_SPLIT_FRAC", "0.8")
os.environ.setdefault("BTC_TRAIN_THREADS", "2")
# train() refuses to start unless the requested window matches the research matrix's own
# recorded identity. That guard is correct and stays ON: the smoke test aligns to the matrix
# rather than bypassing the check, so this run exercises the same precondition a real retrain
# must satisfy. Read from the matrix so the test does not hardcode a window that will age.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from artifact_identity import current_training_identity as _cti
    _probe = _cti(requested_days=0, feature_names=["probe"], code_paths=[], full_refit=False)
    _matrix_days = _probe.get("matrix_requested_days") or _probe.get("requested_days")
    if _matrix_days:
        os.environ.setdefault("BTC_HISTORICAL_DAYS", str(int(_matrix_days)))
except Exception:
    pass

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def build_dataset(n_rows: int, lookback: int, n_feat: int, horizon: int):
    """A learnable, causal synthetic set with both classes present throughout."""
    rng = np.random.default_rng(11)
    X = rng.normal(0, 1, (n_rows, lookback, n_feat)).astype(np.float32)
    # Direction driven by one feature of the LAST timestep, with real noise, so the seats have
    # something to fit and no class is degenerate.
    score = X[:, -1, 0] + rng.normal(0, 0.9, n_rows)
    labels = np.where(score > 0.40, 2, np.where(score < -0.40, 0, 1))
    Y = {horizon: np.eye(3)[labels].astype(np.float32)}
    Ymag = {horizon: np.abs(score).astype(np.float32) * 0.001}
    valid_mask = {horizon: np.ones(n_rows, dtype=bool)}
    # One named regime plus the GLOBAL bucket.
    regime_labels = ["TREND" if i % 2 == 0 else "RANGE" for i in range(n_rows)]
    return X, Y, Ymag, valid_mask, regime_labels


def main() -> int:
    import model as m

    check(m.SAMPLE_WEIGHT_MODE == "recency_similarity",
          "the smoke run uses the PRODUCTION weight mode, so the fold-local similarity term "
          "is actually exercised rather than short-circuited to ones")

    horizon = 5
    lookback = int(m.LOOKBACK)
    n_feat = int(m.NUM_FEATURES)
    # Sized so the stacker slice fills at the production cap and its folds are viable. A thin
    # run refuses every seat - correct behaviour, and worth knowing before a real retrain, but
    # it means the fold block under test never reaches a weighted fit.
    X, Y, Ymag, valid_mask, regime_labels = build_dataset(14000, lookback, n_feat, horizon)

    with tempfile.TemporaryDirectory() as tmp:
        ensemble = m.MultiModelEnsemble(horizons=[horizon], model_dir=tmp)
        # Production binds a fitted MarketRegime state before train(). The smoke test supplies
        # the same persistence contract explicitly so bundle-completeness validation is tested
        # rather than bypassed (the numeric values are not used by this model-only test).
        ensemble.hmm_state = {
            "hmm_ready": True,
            "_means": [[0.0]],
            "_inv_covs": [[[1.0]]],
            "_logdets": [0.0],
            "_transmat": [[1.0]],
            "_k": 1,
            "_median_volume": 1.0,
            "state_labels": {0: "RANGE"},
        }

        # ---- THE THING THAT HAD NEVER RUN --------------------------------------------
        ensemble.train(X, Y, Ymag=Ymag, valid_mask=valid_mask,
                       regime_labels=regime_labels)
        check(True, "train() COMPLETES on the real path - the fold block that was only ever "
                    "parsed has now executed")

        # ---- PROOF the OOF fold block actually executed ---------------------------------
        # "train() completed" and "no errors logged" are both consistent with the fold block
        # never running. `_oof_class_set_rate` is written INSIDE that loop, one entry per
        # (regime, horizon, seat), so its presence is a direct witness - not an inference from
        # silence. Three earlier readings of this test inferred from silence and were wrong.
        rates = getattr(ensemble, "_oof_class_set_rate", None)
        check(rates,
              "the OOF fold loop EXECUTED - proven by the per-seat record it writes from "
              "inside the loop, not inferred from an absence of error messages")
        sample = next(iter(rates.values()))
        check(set(sample) >= {"short_folds", "total_folds", "rate", "tolerance",
                              "within_tolerance"},
              f"and each seat carries its class-set rate against the preregistered tolerance "
              f"{sample.get('tolerance')} - the gate that was previously a counter nobody read")
        check(all(v["total_folds"] > 0 for v in rates.values()),
              f"with a real denominator on every one of {len(rates)} seat/bucket entries, so "
              f"a rate exists to compare rather than a bare count")

        # ---- seats survived -----------------------------------------------------------
        trained = getattr(ensemble, "models", None) or getattr(ensemble, "models_by_regime", {})
        check(bool(trained),
              "the run produced fitted models rather than an empty bundle")

        # ---- predictions are usable ---------------------------------------------------
        probe = X[-1]
        out = ensemble.predict_base(probe, horizon)
        check(isinstance(out, tuple) and len(out) == 3,
              "predict_base returns its documented triple")
        values = [float(v) for v in out if v is not None]
        check(all(np.isfinite(v) for v in values),
              f"and every returned value is finite {tuple(round(v, 4) for v in values)} - a NaN "
              f"here would propagate into an EV calculation as a number-shaped absence")
        p_up, p_down = float(out[0]), float(out[1])
        check(0.0 <= p_up <= 1.0 and 0.0 <= p_down <= 1.0,
              f"probabilities lie in [0,1] (up={p_up:.4f} down={p_down:.4f})")

        # ---- save / load round trip ---------------------------------------------------
        before = [float(v) for v in ensemble.predict_base(probe, horizon) if v is not None]
        saved = ensemble.save_models() if hasattr(ensemble, "save_models") else None
        if saved is None and hasattr(ensemble, "save"):
            saved = ensemble.save()
        artifacts = sorted(Path(tmp).glob("*.pkl"))
        check(artifacts,
              f"the bundle WROTE artifacts to disk ({len(artifacts)} .pkl) - a model that "
              f"cannot be persisted cannot be served")

        reloaded = m.MultiModelEnsemble(horizons=[horizon], model_dir=tmp)
        ok = reloaded.load_models()
        check(bool(ok), "and load_models() reads them back")

        after = [float(v) for v in reloaded.predict_base(probe, horizon) if v is not None]
        check(len(after) == len(before),
              "the reloaded bundle answers with the same shape")
        drift = max((abs(a - b) for a, b in zip(after, before)), default=0.0)
        check(drift < 1e-6,
              f"and the RELOADED predictions match the pre-save ones (max drift {drift:.2e}) - "
              f"a bundle that predicts differently after a round trip is serving something "
              f"other than what was evaluated")

    print(f"\nTRAIN SMOKE (EXECUTED END TO END): PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
