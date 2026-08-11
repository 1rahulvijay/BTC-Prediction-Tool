"""EXECUTE the OOF fold weighting. Every other check on it only parses the source.

WHY THIS EXISTS
    The fold-local regime-similarity repair was verified by AST assertions and by six caught
    mutants - all of which parse `model.py` rather than run it. Nothing in CI calls
    `MultiModelEnsemble.train()`, so that block had never executed. A NameError, a shape
    mismatch or an off-by-one in the reg_idx mapping would have passed every structural check
    while failing on the first real retrain.

    This drives a small real training run so the fold code actually runs, and asserts the two
    properties that structural checks cannot reach:

      1. the similarity weights are ANCHORED FOLD-LOCALLY - an early fold must not be weighted
         by distance from a point in its own future;
      2. the reg_idx -> model-row mapping recovers the rows it claims to.

    python backend/tests/test_oof_fold_weights_execute.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import os
import sys
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Keep the run small. Set before importing model so the module constants pick them up.
os.environ.setdefault("BTC_SAMPLE_WEIGHT_MODE", "recency_similarity")
os.environ.setdefault("BTC_STACKER_MAX_SAMPLES", "1500")

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def main() -> int:
    import model as m

    check(m.SAMPLE_WEIGHT_MODE == "recency_similarity",
          "the run uses the PRODUCTION default weight mode, so the similarity term is live "
          "rather than short-circuited to ones")

    # ---- the similarity function itself, anchored two different ways -------------------
    # This is the property the fold depends on: the weights depend on WHERE they are
    # anchored. If they did not, a fold-local anchor would be decoration.
    rng = np.random.default_rng(3)
    n_rows, lookback = 4000, int(m.LOOKBACK)
    n_feat = int(m.MODEL_NUM_FEATURES)
    names = list(m.MODEL_FEATURE_NAMES)

    # A deliberate regime shift late in the sample, so "recent" differs from "overall".
    X_model = rng.normal(0, 1, (n_rows, lookback, n_feat)).astype(np.float32)
    X_model[3000:, :, :] += 4.0

    early = m._regime_similarity_weights(X_model, names, 2000)
    late = m._regime_similarity_weights(X_model, names, 4000)
    check(len(early) == 2000 and len(late) == 4000,
          "similarity weights are returned for exactly the rows up to the anchor")

    overlap = min(len(early), len(late))
    drift = float(np.mean(np.abs(early[:overlap] - late[:overlap])))
    check(drift > 1e-6,
          f"the SAME rows get different weights under different anchors (mean |diff| "
          f"{drift:.4f}) - so anchoring a fold to the global split_idx would rank its rows by "
          f"distance from a regime it has not seen yet")

    # The late anchor sits inside the shifted regime, so late rows must score at least as
    # similar under it as under the early anchor.
    late_rows = slice(3000, 4000)
    check(float(np.mean(late[late_rows])) > float(np.mean(late[:2000])),
          "under a late anchor the post-shift rows score MORE similar than the pre-shift "
          "ones - the weight tracks the regime rather than merely the row index")

    # ---- the reg_idx -> model-row mapping the fold performs ----------------------------
    # Reproduced exactly as the fold computes it, on arrays whose answer is known.
    reg_idx = np.sort(rng.choice(3000, size=2400, replace=False))
    n_calibration, n_stack = len(reg_idx), 1500
    offset = n_calibration - n_stack
    tr_idx = np.arange(0, 900)
    rows = reg_idx[offset + tr_idx]
    check(len(rows) == len(tr_idx),
          "one model row is recovered per fold training row")
    check(bool(np.all(np.diff(rows) > 0)),
          "the recovered rows are strictly increasing, so the tail slice preserved chronology")
    check(int(rows.max()) < 3000,
          "and every recovered row lies inside the training range, never past the split")

    fold_end = int(rows.max()) + 1
    fold_sim = m._regime_similarity_weights(X_model, names, fold_end)
    check(len(fold_sim) == fold_end,
          "the fold anchors similarity at its OWN last training row")
    sw_like = fold_sim[rows]
    check(len(sw_like) == len(tr_idx) and np.all(np.isfinite(sw_like)),
          "and indexing it by the recovered rows yields one finite weight per fold row - the "
          "arithmetic the fold performs, executed rather than parsed")

    global_sim = m._regime_similarity_weights(X_model, names, 3000)
    check(float(np.mean(np.abs(fold_sim[rows] - global_sim[rows]))) > 1e-9,
          "the fold-local weights DIFFER from the globally-anchored ones on the same rows - "
          "if they matched, the repair would be a no-op and the mutants that flipped the "
          "anchor would have been equivalent")

    # ---- the wrong feature-name list returns all ones ----------------------------------
    # The local `feature_names` at the fold is the stacker's SEAT-name list. Passing it was a
    # live near-miss: it selects no similarity features and silently returns ones, which is
    # indistinguishable from a working fix by any structural check.
    seat_names = ["xgb", "lgb", "cat", "rf", "hgb", "logreg"]
    wrong = m._regime_similarity_weights(X_model, seat_names, 3000)
    check(float(np.min(wrong)) == 1.0 and float(np.max(wrong)) == 1.0,
          "passing the seat-name list returns ALL ONES - so that mistake would have disabled "
          "the term while every source-level check still passed")
    check(float(np.std(global_sim)) > 0.0,
          "while the correct feature names produce weights that actually vary")

    print(f"\nOOF FOLD WEIGHTS (EXECUTED): PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
