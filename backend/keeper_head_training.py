"""
Shared utilities for small parity-safe keeper heads.

These heads intentionally use a tiny feature set that exists both in research and
live serving. They are not broad direction models; they are specialist filters
for the champion decision layer.
"""
from __future__ import annotations

import os
from typing import Callable

import numpy as np
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier, VotingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

FEATURES = ["rv_15m", "rv_30m", "compression_ratio", "shock_magnitude"]
HORIZONS = (1, 3, 5, 7, 10, 15, 30)

# Training window the heads were built for. Baked into each head's HEAD_VERSION so that
# changing BTC_HISTORICAL_DAYS (e.g. 100 -> 180) makes the version differ and train_heads
# retrains every head on the new window. (The matrix itself is rebuilt to this window by
# build_research_matrix.py --days before train_heads runs.)
TRAIN_DAYS_TAG = (os.environ.get("BTC_HISTORICAL_DAYS")
                  or os.environ.get("BTC_BACKFILL_DAYS") or "60").strip() + "d"


def train_split_frac() -> float:
    """Fraction of rows used to FIT; the remaining temporal tail is the honest test holdout.
    Shares the BTC_TRAIN_SPLIT_FRAC knob with the main ensemble; clamped to [0.50, 0.98]
    (a holdout is mandatory, exactly like the ensemble — literal 1.0 is not allowed)."""
    try:
        frac = float(os.environ.get("BTC_TRAIN_SPLIT_FRAC", "0.98"))
    except (TypeError, ValueError):
        frac = 0.98
    return min(0.98, max(0.50, frac))
DEFAULT_MOVE_BUCKETS_USD_BY_HORIZON = {
    # (meaningful, large, extreme) dollar move boundaries.
    # quiet: < meaningful
    # meaningful: meaningful..large
    # large: large..extreme
    # extreme: >= extreme
    1: (10.0, 20.0, 40.0),
    3: (20.0, 35.0, 70.0),
    5: (30.0, 60.0, 100.0),
    7: (40.0, 80.0, 140.0),
    10: (50.0, 100.0, 180.0),
    15: (60.0, 120.0, 300.0),
    30: (100.0, 200.0, 600.0),
}


def _parse_threshold_map(raw: str) -> dict[int, float]:
    out = {}
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        k, v = part.split(":", 1)
        try:
            out[int(k.strip())] = float(v.strip())
        except ValueError:
            continue
    return out


def _parse_bucket_map(raw: str) -> dict[int, tuple[float, float, float]]:
    out = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        k, v = part.split(":", 1)
        vals = [x.strip() for x in v.replace(",", "|").replace("-", "|").split("|") if x.strip()]
        if len(vals) != 3:
            continue
        try:
            a, b, c = sorted(float(x) for x in vals)
            out[int(k.strip())] = (a, b, c)
        except ValueError:
            continue
    return out


def move_buckets_by_horizon() -> dict[int, tuple[float, float, float]]:
    """Return dollar move buckets by horizon.

    Preferred override:
      BTC_MOVE_BUCKETS_USD_BY_HORIZON="1:10|20|40;3:20|35|70;5:30|60|100;..."

    Legacy threshold-map override:
      BTC_BIG_MOVE_USD_BY_HORIZON="1:10,3:25,5:50,7:70,10:90,15:120,30:200"

    Legacy scalar fallback:
      BTC_BIG_MOVE_USD=40
    """
    raw_buckets = os.environ.get("BTC_MOVE_BUCKETS_USD_BY_HORIZON", "")
    parsed_buckets = _parse_bucket_map(raw_buckets)
    if parsed_buckets:
        out = dict(DEFAULT_MOVE_BUCKETS_USD_BY_HORIZON)
        out.update(parsed_buckets)
        return out
    raw_map = os.environ.get("BTC_BIG_MOVE_USD_BY_HORIZON", "")
    parsed = _parse_threshold_map(raw_map)
    if parsed:
        return {h: (float(v), float(v) * 2.0, float(v) * 4.0) for h, v in {
            **{k: v[0] for k, v in DEFAULT_MOVE_BUCKETS_USD_BY_HORIZON.items()},
            **parsed,
        }.items()}
    if os.environ.get("BTC_BIG_MOVE_USD"):
        val = float(os.environ["BTC_BIG_MOVE_USD"])
        return {h: (val, val * 2.0, val * 4.0) for h in HORIZONS}
    return dict(DEFAULT_MOVE_BUCKETS_USD_BY_HORIZON)


def move_thresholds_by_horizon() -> dict[int, float]:
    """Return the meaningful-move lower boundary by horizon."""
    return {h: float(v[0]) for h, v in move_buckets_by_horizon().items()}


def move_threshold_for(horizon: int) -> float:
    return float(move_thresholds_by_horizon().get(int(horizon), DEFAULT_MOVE_BUCKETS_USD_BY_HORIZON[5][0]))


def move_bucket_for(horizon: int, abs_move_usd: float) -> str:
    meaningful, large, extreme = move_buckets_by_horizon().get(
        int(horizon), DEFAULT_MOVE_BUCKETS_USD_BY_HORIZON[5]
    )
    x = abs(float(abs_move_usd))
    if x >= extreme:
        return "extreme"
    if x >= large:
        return "large"
    if x >= meaningful:
        return "meaningful"
    return "quiet"


def ensemble():
    lr = Pipeline([
        ("sc", StandardScaler()),
        ("clf", LogisticRegression(class_weight="balanced", max_iter=1000)),
    ])
    rf = RandomForestClassifier(
        n_estimators=300, max_depth=8, min_samples_leaf=50,
        class_weight="balanced", n_jobs=2, random_state=0,
    )
    et = ExtraTreesClassifier(
        n_estimators=300, max_depth=10, min_samples_leaf=50,
        class_weight="balanced", n_jobs=2, random_state=0,
    )
    estimators = [("lr", lr), ("rf", rf), ("et", et)]
    try:
        from catboost import CatBoostClassifier
        estimators.append(("cat", CatBoostClassifier(
            iterations=300, depth=5, learning_rate=0.05, verbose=0,
            random_seed=0, allow_writing_files=False,
        )))
    except Exception:
        pass
    return VotingClassifier(estimators, voting="soft", n_jobs=1)


def top_n_precision(y, p, frac=0.05):
    k = max(1, int(len(y) * frac))
    idx = np.argsort(p)[-k:]
    return float(y[idx].mean())


def fit_binary_head(X, y, split_frac=None):
    """Fit one calibrated keeper head with a TEMPORAL 98/2 train/test split.

    The first `split_frac` of rows (oldest) FIT the model; the final tail is a held-out test
    set that is never seen during fitting, isotonic calibration, or tier construction — so
    `test_auc` / `test_top5` are honest out-of-sample. The served model is the train-only fit,
    consistent with the main ensemble (which also caps at 0.98 and keeps a 2% tail).
    """
    X = np.asarray(X)
    y = np.asarray(y).astype(int)
    if len(y) < 1000 or len(np.unique(y)) < 2:
        return None

    if split_frac is None:
        split_frac = train_split_frac()
    cut = int(len(y) * split_frac)
    X_tr, y_tr, X_te, y_te = X[:cut], y[:cut], X[cut:], y[cut:]
    # Degenerate-tail guard: only score a holdout if it is big enough and two-class; otherwise
    # fall back to fitting on all rows (no test report) rather than emit a meaningless test_auc.
    holdout = (len(y_te) >= 200 and len(np.unique(y_te)) == 2 and len(np.unique(y_tr)) == 2)
    if not holdout:
        X_tr, y_tr = X, y

    oof = np.zeros(len(y_tr))
    seen = np.zeros(len(y_tr), dtype=bool)
    for tr, te in TimeSeriesSplit(n_splits=5).split(X_tr):
        clf = ensemble()
        clf.fit(X_tr[tr], y_tr[tr])
        oof[te] = clf.predict_proba(X_tr[te])[:, 1]
        seen[te] = True

    raw_auc = float(roc_auc_score(y_tr[seen], oof[seen]))
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(oof[seen], y_tr[seen])
    cal_oof = iso.predict(oof[seen])
    cal_auc = float(roc_auc_score(y_tr[seen], cal_oof))
    top5 = top_n_precision(y_tr[seen], cal_oof, 0.05)

    pipe = ensemble()
    pipe.fit(X_tr, y_tr)
    score = iso.predict(pipe.predict_proba(X_tr)[:, 1])
    tiers = {
        "t1": float(np.quantile(score, 0.60)),
        "t2": float(np.quantile(score, 0.80)),
        "t3": float(np.quantile(score, 0.90)),
    }

    test_auc = test_top5 = None
    if holdout:
        score_te = iso.predict(pipe.predict_proba(X_te)[:, 1])
        test_auc = float(roc_auc_score(y_te, score_te))
        test_top5 = top_n_precision(y_te, score_te, 0.05)

    return {
        "pipe": pipe,
        "iso": iso,
        "features": FEATURES,
        "auc": raw_auc,
        "calibrated_auc": cal_auc,
        "top5_prec": top5,
        "test_auc": test_auc,
        "test_top5_prec": test_top5,
        "base_rate": float(y.mean()),
        "tiers": tiers,
        "n_train": int(len(y_tr)),
        "n_test": int(len(y_te)) if holdout else 0,
        "split_frac": float(split_frac),
        "train_days_tag": TRAIN_DAYS_TAG,
    }


def future_close_delta(close: np.ndarray, horizon: int) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if horizon < len(close):
        out[:-horizon] = close[horizon:] - close[:-horizon]
    return out


def future_window(values: np.ndarray, horizon: int, reducer: Callable[[np.ndarray], float]) -> np.ndarray:
    out = np.full(len(values), np.nan)
    end = len(values) - horizon
    for i in range(max(0, end)):
        out[i] = reducer(values[i + 1:i + 1 + horizon])
    return out


def model_summary(model: dict | None) -> str:
    if not model:
        return "missing"
    test = ""
    if model.get("test_auc") is not None:
        test = (f" | test_AUC={model['test_auc']:.3f} "
                f"test_top5={model['test_top5_prec'] * 100:.1f}% (n={model.get('n_test', 0):,})")
    return (
        f"base={model['base_rate'] * 100:.1f}% "
        f"AUC={model['auc']:.3f} cal_AUC={model['calibrated_auc']:.3f} "
        f"top5={model['top5_prec'] * 100:.1f}%{test}"
    )
