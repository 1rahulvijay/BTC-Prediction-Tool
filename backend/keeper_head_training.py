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
HORIZONS = (5, 15)   # pruned 2026-06-21: dropped 3/7/10/30 (no market, coin-flip direction)

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


def move_bucket_pcts() -> tuple[float, float, float]:
    """Percentiles for (meaningful, large, extreme) when AUTO-deriving buckets from data.
    Override with BTC_MOVE_BUCKET_PCTS="0.75,0.90,0.97" (default = top-quartile / top-10% / top-3%)."""
    raw = os.environ.get("BTC_MOVE_BUCKET_PCTS", "0.75,0.90,0.97")
    try:
        p = tuple(float(x) for x in raw.split(",")[:3])
        if len(p) == 3 and 0.0 < p[0] < p[1] < p[2] < 1.0:
            return p
    except (TypeError, ValueError):
        pass
    return (0.75, 0.90, 0.97)


def _round_nice(x: float) -> float:
    """Round a dollar boundary to a clean step so the buckets read nicely."""
    x = float(x)
    if x >= 200:
        return float(round(x / 10) * 10)
    if x >= 50:
        return float(round(x / 5) * 5)
    return float(round(x))


def derive_buckets(close, horizons=HORIZONS, pcts=None) -> dict[int, tuple[float, float, float]]:
    """Self-calibrating dollar buckets from the ACTUAL |close[t+h]-close[t]| distribution:
    meaningful=pcts[0] (default p75), large=pcts[1] (p90), extreme=pcts[2] (p97). Returns {} if the
    series is too short. Keeps "big move" a genuinely notable event at any price level — absolute
    dollar thresholds otherwise go stale as BTC re-prices (a $30 5m move is top-quartile at $65k,
    noise at $130k)."""
    close = np.asarray(close, dtype=float)
    pcts = pcts or move_bucket_pcts()
    out = {}
    for h in horizons:
        h = int(h)
        if h >= len(close):
            continue
        d = np.abs(close[h:] - close[:-h])
        d = d[np.isfinite(d)]
        if len(d) < 100:
            continue
        a, b, c = sorted(_round_nice(float(np.quantile(d, p))) for p in pcts)
        b = max(b, a + 1.0)            # guarantee strictly-increasing boundaries
        c = max(c, b + 1.0)
        out[h] = (float(a), float(b), float(c))
    return out


def derive_buckets_bps(close, horizons=HORIZONS, pcts=None, *,
                       fit_frac) -> dict[int, tuple[float, float, float]]:
    """PRICE-LEVEL-PROOF buckets: quantiles of the RELATIVE move |close[t+h]-close[t]| / close[t]
    in BASIS POINTS. Rationale (2026-07-03 audit): a single DOLLAR quantile over a long window mixes
    price regimes -- $-label base rates already swing ~2x per quarter at 400d, and a 1200-1500d
    window spans BTC $15.5k..$115k (7.5x), where a fixed $100 means anything from noise to a crash.
    Labeling each row against a bps threshold keeps 'big move' equally notable at every price level.
    Returns {h: (meaningful, large, extreme)} in bps; {} if the series is too short.

    `fit_frac` is REQUIRED and keyword-only. These percentiles DEFINE the label - they decide
    what counts as a big move - so taking them over the whole series lets the held-out span
    help define the target it is then scored on. Measured on the live 1,440,000-row matrix at
    the default split of 0.98:

        h=5   p75  full-span 12.33 bps -> train-only 12.43 bps  (+0.79%)
        h=15  p75  full-span 21.13 bps -> train-only 21.30 bps  (+0.79%)
        ~0.26% of labels flip between the two thresholds

    Small at 0.98 because the test tail is 2% of the rows. The severity is a function of the
    knob, not a constant: BTC_TRAIN_SPLIT_FRAC is settable down to 0.50, where a third of the
    series would be defining its own labels. Passing the fit fraction explicitly removes the
    latent version of the defect rather than the currently-mild one.
    """
    close = np.asarray(close, dtype=float)
    pcts = pcts or move_bucket_pcts()
    fit_frac = float(fit_frac)
    if not 0.0 < fit_frac <= 1.0:
        raise ValueError(f"fit_frac must be in (0, 1]; got {fit_frac}")
    out = {}
    for h in horizons:
        h = int(h)
        if h >= len(close):
            continue
        base = close[:-h]
        d = np.abs(close[h:] - base) / np.where(base > 0, base, np.nan) * 1e4
        d = d[np.isfinite(d)]
        # Threshold from the FITTING span only, minus the label horizon: rows in the last h
        # of that span are themselves labelled from prices past the boundary.
        n_fit = max(1, int(len(d) * fit_frac) - h)
        d = d[:n_fit]
        if len(d) < 100:
            continue
        a, b, c = sorted(float(np.quantile(d, p)) for p in pcts)
        b = max(b, a + 0.1)
        c = max(c, b + 0.1)
        out[h] = (round(a, 2), round(b, 2), round(c, 2))
    return out


def rel_bps(values_usd: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Convert a per-row dollar quantity into bps of that ROW's own price (label-side twin of
    derive_buckets_bps; keeps every label price-level-relative)."""
    close = np.asarray(close, dtype=float)
    return np.asarray(values_usd, dtype=float) / np.where(close > 0, close, np.nan) * 1e4


def buckets_for_training(close=None) -> dict[int, tuple[float, float, float]]:
    """Buckets to train on: an explicit env override (BTC_MOVE_BUCKETS_USD_BY_HORIZON) wins; else
    AUTO-DERIVE from `close` (the self-calibrating default); else the static defaults. Serving reads
    the result back from the saved bundle, so train/serve parity is automatic."""
    parsed = _parse_bucket_map(os.environ.get("BTC_MOVE_BUCKETS_USD_BY_HORIZON", ""))
    if parsed:
        out = dict(DEFAULT_MOVE_BUCKETS_USD_BY_HORIZON)
        out.update(parsed)
        return out
    if close is not None:
        derived = derive_buckets(close)
        if derived:
            return derived
    return dict(DEFAULT_MOVE_BUCKETS_USD_BY_HORIZON)


def _bucket_tag() -> str:
    """Short tag for HEAD_VERSION so changing the bucket policy forces a one-shot retrain."""
    if os.environ.get("BTC_MOVE_BUCKETS_USD_BY_HORIZON"):
        return "envbkt"
    return "p" + "-".join(str(int(round(x * 100))) for x in move_bucket_pcts())


BUCKET_TAG = _bucket_tag()


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


def fit_binary_head(X, y, split_frac=None, *, horizon_bars):
    """Fit one calibrated keeper head with a PURGED temporal train/test split.

    `horizon_bars` is REQUIRED and keyword-only, deliberately. Every caller labels rows from
    a FUTURE h-minute move, so a row at index i encodes prices through i+h. Chronological
    ordering alone does not make the sides independent: without a gap the last h training
    rows of every fold, and the last h rows before the 98/2 cut, are built from prices that
    live inside the very set used to score them. Enforcing the purge HERE rather than in each
    caller is the point - four keeper trainers share this function, and a per-caller rule is
    one a new head can silently omit. (Rows are 1-minute bars, so horizon_bars == horizon.)

    The gap was previously absent at BOTH TimeSeriesSplit sites while the comment below the
    refit branch already described the OOF as "purged". It now is.

    The first `split_frac` of rows FIT the model, minus a purge; the final tail is held out
    from fitting, isotonic calibration and tier construction, so `test_auc` / `test_top5` are
    out-of-sample.
    """
    X = np.asarray(X)
    y = np.asarray(y).astype(int)
    horizon_bars = int(horizon_bars)
    if horizon_bars < 1:
        raise ValueError("horizon_bars must be >= 1; labels look forward and need a purge")
    if len(y) < 1000 or len(np.unique(y)) < 2:
        return None

    if split_frac is None:
        split_frac = train_split_frac()
    cut = int(len(y) * split_frac)
    # PURGE the boundary: rows in [cut-horizon_bars, cut) are labelled from prices at or past
    # `cut`, i.e. from inside the test set. They are dropped from TRAIN; TEST keeps every row,
    # because shrinking the scored side is how a purge turns into a better-looking number.
    tr_end = max(1, cut - horizon_bars)
    X_tr, y_tr, X_te, y_te = X[:tr_end], y[:tr_end], X[cut:], y[cut:]
    holdout = (len(y_te) >= 200 and len(np.unique(y_te)) == 2 and len(np.unique(y_tr)) == 2)
    if not holdout:
        # No valid holdout means NO EVIDENCE, which must not read as a normal head. The head
        # is still fit (it can be useful once forward outcomes accumulate) but is marked
        # SHADOW below and carries test_auc=None rather than looking merely unreported.
        X_tr, y_tr = X, y

    oof = np.zeros(len(y_tr))
    seen = np.zeros(len(y_tr), dtype=bool)
    for tr, te in TimeSeriesSplit(n_splits=5, gap=horizon_bars).split(X_tr):
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
    # TIERS STAY IN-SAMPLE, and this is a MEASURED decision rather than an oversight.
    #
    # A 2026-08-10 audit called these thresholds in-sample and therefore optimistic, so they
    # were switched to quantiles of `cal_oof`. Measuring the tier firing rate on untouched
    # rows showed the switch made t3 WORSE, not better:
    #
    #     t3 from in-sample scores  0.9688  -> fires 11.0% of untouched rows (nominal 10%)
    #     t3 from OOF scores        0.8864  -> fires 17.9%                   (nominal 10%)
    #     (t1/t2 were identical under both, isotonic clipping flattens them)
    #
    # The reason is that `cal_oof` comes from FOLD models, each fit on a fraction of the
    # data, so its scores are less extreme than the served full-data `pipe`. Its q90 is thus
    # too low and the tier over-fires. The in-sample distribution is over-confident in the
    # opposite direction, and on this fixture the two errors leave it closer to nominal.
    #
    # Neither is the right object: tiers should come from the SERVED model scored on rows it
    # did not train on, which the refit branch has none of by construction. Left as-is
    # because the available evidence points against the change, not because it is correct.
    # See test_keeper_head_purge.py, which pins the measurement so this is not re-"fixed"
    # from the same reasoning.
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

    # ── VALIDATED PRODUCTION REFIT ON ALL ROWS (2026-07-03) ─────────────────────────────────
    # The 98% candidate above supplies the honest OOS report (test_auc/test_top5, preserved
    # verbatim below). If it clears the predeclared sanity gate, the SERVED model is refit on
    # ALL rows -- so live inference has also learned the most recent tail -- with a fresh
    # purged TimeSeriesSplit-OOF isotonic over the full span and tiers from the full-span score
    # distribution. A failed gate serves the measured candidate unchanged (fail-safe). Disable
    # with BTC_HEAD_REFIT_ALL=0. Post-refit honesty comes from the permanent live shadow layer
    # (scorecards / calibration monitor), since a full-data fit has no untouched test by design.
    REFIT_GATE_AUC = 0.55
    refit_on_all = bool(holdout and test_auc is not None and test_auc >= REFIT_GATE_AUC
                        and os.environ.get("BTC_HEAD_REFIT_ALL", "1") != "0")
    if refit_on_all:
        oof_f = np.zeros(len(y))
        seen_f = np.zeros(len(y), dtype=bool)
        for tr, te in TimeSeriesSplit(n_splits=5, gap=horizon_bars).split(X):
            clf = ensemble()
            clf.fit(X[tr], y[tr])
            oof_f[te] = clf.predict_proba(X[te])[:, 1]
            seen_f[te] = True
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(oof_f[seen_f], y[seen_f])
        pipe = ensemble()
        pipe.fit(X, y)
        # In-sample here too - same measurement as the candidate branch above.
        score = iso.predict(pipe.predict_proba(X)[:, 1])
        tiers = {
            "t1": float(np.quantile(score, 0.60)),
            "t2": float(np.quantile(score, 0.80)),
            "t3": float(np.quantile(score, 0.90)),
        }

    return {
        "pipe": pipe,
        "iso": iso,
        "features": FEATURES,
        "auc": raw_auc,
        "calibrated_auc": cal_auc,
        "top5_prec": top5,
        "test_auc": test_auc,
        "test_top5_prec": test_top5,
        # "No valid holdout" is not the same as "not reported yet". A head fit without an
        # untouched test has no out-of-sample evidence at all, and must be distinguishable
        # from one that earned its numbers - otherwise it becomes an artifact that looks
        # normal. Consumers gate on this rather than inferring from test_auc being None.
        "evidence_status": "MEASURED" if holdout else "SHADOW_NO_VALID_HOLDOUT",
        "purge_bars": horizon_bars,
        # Named honestly. An OOF basis was tried and measured WORSE for tier coverage
        # (t3 fired 17.9% vs 11.0% against a 10% nominal); see fit_binary_head.
        "tier_basis": "in_sample_measured_better_than_oof",
        "base_rate": float(y.mean()),
        "tiers": tiers,
        "n_train": int(len(y) if refit_on_all else len(y_tr)),
        "n_test": int(len(y_te)) if holdout else 0,
        "split_frac": float(split_frac),
        "refit_on_all": refit_on_all,
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
