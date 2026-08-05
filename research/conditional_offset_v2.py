#!/usr/bin/env python
"""CONDITIONAL_OFFSET_V2 - does a log-odds correction to anchor geometry survive its controls?

    python research/conditional_offset_v2.py --selftest
    python research/conditional_offset_v2.py --run
    python research/conditional_offset_v2.py --run --rounds 15 12 --rows 200000

WHAT V1 GOT WRONG, AND THIS FIXES

  1. V1's "ML residual" arm was p_base appended as a FEATURE COLUMN, which leaves the model
     free to override geometry. It was not a residual model, which is why it printed numbers
     identical to the unconstrained model.

  2. V1 reported point estimates only. A dBrier of +0.0013 is not a result without a paired
     interval; it might be deterioration or sampling noise.

  3. V1 called a mean |log-odds correction| of 0.25 "real work". MEASURED HERE: with labels
     drawn exactly from p_base - where the correct correction is identically zero - this
     configuration still produces mean |correction| ~0.23. So 0.25 is the NOISE FLOOR, not
     evidence of signal. Correction magnitude is not a diagnostic; only paired loss is.

  4. V1 led with AUC. A log-odds additive model is fitted through a log-loss objective, so log
     loss is primary here, with Brier, ECE and calibration slope beside it. AUC is secondary:
     ranking can hold while calibration rots, and this repository has repeatedly shown ranking
     and value are different questions.

THE ARMS
    baseline        Phi(z) - anchor geometry, no parameters
    offset          logit(p_base) + f(X)          via LightGBM init_score
    offset_permuted logit(p_base) + f(shuffle(X)) the control that answers "is f learning
                                                  structure, or fitting a flexible intercept?"
    zero_correction logit(p_base) + 0             an arithmetic guard, must equal baseline

CAUSALITY
    sigma comes from bars strictly BEFORE the round. Splits are chronological with a purge gap
    of one full round. The baseline is a closed form and is never fitted, so it cannot be
    fitted on evaluation data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "research"))
RESULTS = REPO / "research" / "results"

BOOST_ROUNDS = 400
PARAMS = {"objective": "binary", "learning_rate": 0.03, "num_leaves": 31,
          "min_data_in_leaf": 200, "lambda_l2": 5.0, "feature_fraction": 0.8,
          "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1, "seed": 42,
          # Irrelevant when init_score is supplied - VERIFIED in the selftest rather than
          # assumed, because the docs do not state the interaction plainly.
          "boost_from_average": True}


# ---------------------------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------------------------
def logit(p):
    p = np.clip(np.asarray(p, dtype=np.float64), 1e-6, 1 - 1e-6)
    return np.log(p / (1.0 - p))


def expit(x):
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=np.float64)))


def log_loss(p, y):
    p = np.clip(np.asarray(p, dtype=np.float64), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype=np.float64)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(p, y):
    return float(np.mean((np.asarray(p, dtype=np.float64) - np.asarray(y, dtype=np.float64)) ** 2))


def auc(scores, labels) -> float:
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels).astype(int)
    pos, neg = int(labels.sum()), int(len(labels) - labels.sum())
    if pos == 0 or neg == 0:
        return float("nan")
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=np.float64)
    s = scores[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        ranks[order[i:j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return float((ranks[labels == 1].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def ece(p, y, bins: int = 10) -> float:
    p = np.asarray(p, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = 0.0
    for i in range(bins):
        hi = p <= edges[i + 1] if i == bins - 1 else p < edges[i + 1]
        m = (p >= edges[i]) & hi
        if m.any():
            total += float(m.mean()) * abs(float(p[m].mean()) - float(y[m].mean()))
    return total


def calibration_slope(p, y) -> float:
    """OLS slope of y on p. Calibrated means E[y|p] = p, so the slope is 1.

    Deliberately NOT the slope of y on logit(p): that does not equal 1 for a calibrated
    forecast, and using it would have reported every arm as badly miscalibrated. Below 1 is
    over-confident (the forecast spreads wider than outcomes justify); above 1 is
    under-confident.
    """
    p = np.asarray(p, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if np.var(p) < 1e-18:
        return float("nan")
    return float(np.cov(p, y, ddof=0)[0, 1] / np.var(p))


def day_block_ci(stat_fn, days, iterations: int = 400, seed: int = 17):
    """Percentile CI resampling whole DAYS.

    Lattice cells inside a round, and rounds inside a day, share a regime. An IID row bootstrap
    would report an interval several times too tight and turn noise into a finding.
    """
    days = np.asarray(days)
    unique = np.unique(days)
    if len(unique) < 5:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx_by_day = {d: np.flatnonzero(days == d) for d in unique}
    out = []
    for _ in range(iterations):
        pick = rng.choice(unique, size=len(unique), replace=True)
        idx = np.concatenate([idx_by_day[d] for d in pick])
        v = stat_fn(idx)
        if np.isfinite(v):
            out.append(v)
    if len(out) < 20:
        return (float("nan"), float("nan"))
    return (float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5)))


# ---------------------------------------------------------------------------------------------
def fit_offset(X_tr, y_tr, init_tr, X_te, init_te, permute_seed=None):
    """logit(p_final) = logit(p_base) + f(X), reconstructed EXPLICITLY.

    LightGBM's predict_proba has no per-row init_score for new rows, so the offset MUST be
    re-added by hand at prediction time. Using predict_proba here would silently return the
    probability implied by the trees alone and drop each test row's baseline.
    """
    import lightgbm as lgb

    Xtr = np.asarray(X_tr, dtype=np.float64)
    if permute_seed is not None:
        rng = np.random.default_rng(permute_seed)
        Xtr = Xtr.copy()
        for c in range(Xtr.shape[1]):
            rng.shuffle(Xtr[:, c])
    booster = lgb.train(PARAMS,
                        lgb.Dataset(Xtr, label=np.asarray(y_tr).astype(int),
                                    init_score=np.asarray(init_tr, dtype=np.float64)),
                        num_boost_round=BOOST_ROUNDS)
    correction = booster.predict(np.asarray(X_te, dtype=np.float64), raw_score=True)
    return expit(np.asarray(init_te, dtype=np.float64) + correction), correction


def evaluate(p, y):
    return {"log_loss": log_loss(p, y), "brier": brier(p, y), "auc": auc(p, y),
            "ece": ece(p, y), "calibration_slope": calibration_slope(p, y)}


# ---------------------------------------------------------------------------------------------
def run(round_lengths, rows) -> int:
    import conditional_path_forecast_v1 as v1

    RESULTS.mkdir(parents=True, exist_ok=True)
    report = {"created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "boost_rounds": BOOST_ROUNDS, "params": PARAMS, "rounds": {}}

    print("=" * 100)
    print("CONDITIONAL OFFSET V2 - log-odds correction to anchor geometry, with controls")
    print("=" * 100)

    for minutes in round_lengths:
        v1.configure_round(minutes)
        frame = v1.load_btc(rows)
        rounds = v1.build_rounds(frame)
        lattice = v1.build_lattice_dataset(rounds, verbose=False)
        if lattice is None or lattice.empty:
            print(f"  {minutes}m: no lattice"); continue

        # Chronological split with a one-round purge, on ROUND index (not row).
        cut = int(lattice["round_idx"].max() * v1.TRAIN_FRACTION)
        train = lattice[lattice["round_idx"] <= cut]
        test = lattice[lattice["round_idx"] > cut + 1]
        feats = list(v1.ALL_FEATURES)
        Xtr, ytr = train[feats].to_numpy(float), train["anchor_up"].to_numpy(int)
        Xte, yte = test[feats].to_numpy(float), test["anchor_up"].to_numpy(int)
        pb_tr = np.clip(v1.baseline_probabilities(train), 1e-6, 1 - 1e-6)
        pb_te = np.clip(v1.baseline_probabilities(test), 1e-6, 1 - 1e-6)
        # Rounds are ~`minutes` apart; a day-block is 1440/minutes rounds.
        days = (test["round_idx"].to_numpy() // max(1, 1440 // minutes))

        print(f"\n{minutes}-MINUTE ROUNDS   train {len(train):,} rows / test {len(test):,} rows "
              f"/ {len(np.unique(days))} day-blocks")

        arms = {"baseline": pb_te}
        p_off, corr = fit_offset(Xtr, ytr, logit(pb_tr), Xte, logit(pb_te))
        arms["offset"] = p_off
        p_perm, corr_perm = fit_offset(Xtr, ytr, logit(pb_tr), Xte, logit(pb_te),
                                       permute_seed=99)
        arms["offset_permuted"] = p_perm
        arms["zero_correction"] = expit(logit(pb_te) + 0.0)

        # GUARD: the reconstruction arithmetic must be exact.
        assert np.allclose(arms["zero_correction"], pb_te, atol=1e-10), \
            "zero-correction offset does not reproduce the baseline - the reconstruction is wrong"

        real_mag, perm_mag = float(np.abs(corr).mean()), float(np.abs(corr_perm).mean())
        ratio = real_mag / perm_mag if perm_mag > 0 else float("inf")
        print(f"  mean |correction|          real {real_mag:.4f}   permuted {perm_mag:.4f}"
              f"   ratio {ratio:.1f}x")
        print("  A ratio near 1 would mean the correction is a flexible intercept fitting "
              "noise.")
        print("  A ratio well above 1 means it IS responding to real feature structure - which "
              "says")
        print("  nothing about whether acting on that structure helps. Read the paired "
              "intervals.")

        print(f"\n  {'arm':<18}{'logloss':>10}{'brier':>10}{'AUC':>9}{'ECE':>9}{'calib':>9}")
        print("  " + "-" * 65)
        scored = {}
        for name, p in arms.items():
            m = evaluate(p, yte)
            scored[name] = m
            print(f"  {name:<18}{m['log_loss']:>10.4f}{m['brier']:>10.4f}{m['auc']:>9.4f}"
                  f"{m['ece']:>9.4f}{m['calibration_slope']:>9.3f}")

        print(f"\n  PAIRED vs baseline, day-block bootstrap 95% CI (negative = better)")
        deltas = {}
        for name in ("offset", "offset_permuted"):
            p = arms[name]
            row = {}
            for label, fn in (("d_log_loss", log_loss), ("d_brier", brier)):
                point = fn(p, yte) - fn(pb_te, yte)
                lo, hi = day_block_ci(
                    lambda idx, _p=p, _f=fn: _f(_p[idx], yte[idx]) - _f(pb_te[idx], yte[idx]),
                    days)
                row[label] = {"point": point, "lo": lo, "hi": hi}
                verdict = ("WORSE" if lo > 0 else "BETTER" if hi < 0 else "inconclusive")
                print(f"    {name:<16} {label:<11} {point:+.5f}  "
                      f"[{lo:+.5f}, {hi:+.5f}]  {verdict}")
            point_auc = auc(p, yte) - auc(pb_te, yte)
            lo, hi = day_block_ci(
                lambda idx, _p=p: auc(_p[idx], yte[idx]) - auc(pb_te[idx], yte[idx]), days)
            row["d_auc"] = {"point": point_auc, "lo": lo, "hi": hi}
            verdict = ("WORSE" if hi < 0 else "BETTER" if lo > 0 else "inconclusive")
            print(f"    {name:<16} {'d_auc':<11} {point_auc:+.5f}  "
                  f"[{lo:+.5f}, {hi:+.5f}]  {verdict}")
            deltas[name] = row

        report["rounds"][str(minutes)] = {
            "n_train": int(len(train)), "n_test": int(len(test)),
            "day_blocks": int(len(np.unique(days))),
            "mean_abs_correction": float(np.abs(corr).mean()),
            "mean_abs_correction_permuted": float(np.abs(corr_perm).mean()),
            "arms": scored, "paired_vs_baseline": deltas,
        }

    out = RESULTS / "conditional_offset_v2.json"
    payload = json.dumps(report, indent=2, sort_keys=True, default=float)
    out.write_text(payload, encoding="utf-8")
    print(f"\nwrote {out.relative_to(REPO).as_posix()}  "
          f"sha256={hashlib.sha256(payload.encode()).hexdigest()[:16]}")
    print("\nNOTE: every number above is measured against the OUTCOME. None is measured against")
    print("the Polymarket PRICE, which is the only comparison that decides tradeability.")
    return 0


# ---------------------------------------------------------------------------------------------
def selftest() -> int:
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
        ok = ok and bool(cond)

    print("conditional_offset_v2 selftest")

    print("\n metric sanity")
    chk(abs(log_loss([0.9, 0.1], [1, 0]) - (-math.log(0.9))) < 1e-9, "log loss is correct")
    chk(abs(brier([1.0, 0.0], [1, 0])) < 1e-12, "a perfect forecast has Brier 0")
    chk(abs(auc([0.9, 0.1], [1, 0]) - 1.0) < 1e-12, "AUC ranks correctly")
    chk(abs(expit(logit(0.73)) - 0.73) < 1e-12, "logit and expit round-trip")
    _rng = np.random.default_rng(11)
    _p = _rng.uniform(0.05, 0.95, 40000)
    _y = (_rng.uniform(size=_p.size) < _p).astype(int)      # honestly calibrated by construction
    chk(abs(calibration_slope(_p, _y) - 1.0) < 0.05,
        f"a genuinely calibrated forecast has slope ~1 ({calibration_slope(_p, _y):.3f})")
    chk(calibration_slope(np.clip(0.5 + (_p - 0.5) * 2.0, 0.01, 0.99), _y) < 0.85,
        "an over-confident forecast (spread doubled) has slope well below 1")

    print("\n THE RECONSTRUCTION GUARD")
    pb = np.array([0.2, 0.5, 0.8, 0.95])
    chk(np.allclose(expit(logit(pb) + 0.0), pb, atol=1e-12),
        "a zero correction reproduces the baseline EXACTLY - this is the arithmetic that would "
        "silently break if predict_proba were used instead of raw_score + init")

    print("\n init_score is honoured, and boost_from_average does not interfere")
    # Labels drawn exactly from p_base: the correct correction is identically zero, so whatever
    # magnitude appears here is this configuration's NOISE FLOOR.
    import lightgbm as lgb
    rng = np.random.default_rng(0)
    n = 20000
    X = rng.normal(size=(n, 5))
    pbase = expit(0.8 * X[:, 0])
    y = (rng.uniform(size=n) < pbase).astype(int)
    init = logit(pbase)
    mags = {}
    for bfa in (True, False):
        p = dict(PARAMS, boost_from_average=bfa)
        b = lgb.train(p, lgb.Dataset(X, label=y, init_score=init), num_boost_round=200)
        mags[bfa] = float(np.abs(b.predict(X, raw_score=True)).mean())
    b_no = lgb.train(PARAMS, lgb.Dataset(X, label=y), num_boost_round=200)
    without = float(np.abs(b_no.predict(X, raw_score=True)).mean())
    chk(abs(mags[True] - mags[False]) < 1e-6,
        f"boost_from_average changes nothing when init_score is set "
        f"({mags[True]:.4f} vs {mags[False]:.4f})")
    chk(mags[True] < without * 0.6,
        f"init_score IS the starting point: correction {mags[True]:.4f} vs {without:.4f} "
        f"without it")
    chk(mags[True] > 0.1,
        f"and yet the correction is {mags[True]:.4f} where the TRUE correction is zero - that "
        f"is the noise floor, which is why correction magnitude is not evidence of signal")

    print("\n day-block CI refuses too few blocks")
    lo, hi = day_block_ci(lambda idx: 0.0, np.array([1, 1, 2, 2]))
    chk(math.isnan(lo) and math.isnan(hi),
        "fewer than five day-blocks cannot produce an interval")
    lo, hi = day_block_ci(lambda idx: 1.0, np.arange(40))
    chk(abs(lo - 1.0) < 1e-9 and abs(hi - 1.0) < 1e-9,
        "a constant statistic yields a degenerate interval, not noise")

    print(f"\nCONDITIONAL OFFSET V2 SELFTEST: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--rounds", type=int, nargs="+", default=[15, 12])
    ap.add_argument("--rows", type=int, default=200_000)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.run:
        return run(a.rounds, a.rows)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
