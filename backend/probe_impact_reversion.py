"""
probe_impact_reversion.py - does IMPACT / absorption predict REVERSAL & big-drop?  (v2, corrected)
==================================================================================================
v1 returned a null, but the construction was flawed (the user was right to push back):
  - `absorption`/`fragility` were exact negatives of each other AND dominated by |bar_move|, which is
    already in the baseline → the model had no new info to extract → a fake null;
  - the impact scale k was hard-coded, not fit → the "residual" was rescaled noise;
  - the test was UNCONDITIONAL with a 1-bar anchor → the absorption→reversal effect (which only exists
    AFTER a real impulse) was drowned by 518k mostly-flat bars.

v2 fixes all three:
  1. MULTI-BAR IMPULSE anchor: impulse = close[t] - close[t-K] with its flow_K / vol_K over the same window;
  2. FITTED square-root law: |impulse| ~ b·(sigma·sqrt(|flow_K|/vol_K)) by OLS ON TRAIN ONLY → leak-free
     predicted magnitude → `impact_resid` (signed) and `absorbed_ratio` = |impulse| / pred_mag (low=absorbed);
  3. ELASTICITY = |impulse| / |flow_K| ($ moved per unit net flow) — NOT collinear with |impulse| alone;
  4. CONDITIONAL test: reversal is scored ONLY on the top-q impulse subset (where the hypothesis lives),
     and unconditionally for contrast.

Hypothesis: a flow-ABSORBED impulse (moved less than its flow predicts / low elasticity) REVERTS; a FRAGILE
impulse (moved more than flow predicts / high elasticity) snaps back too. Either way the |impact_resid| /
elasticity should add reversal AUC over an rv baseline ON THE CONDITIONED SUBSET. Targets proxy the user's
line-cross / P(Hold)-failure (reversal) and big-drop (downside flush).

Discipline: leak-free (features at close[t], k fit on train only, labels strictly future), temporal 70/30,
incremental AUC over the rv baseline, shuffled-null on the impact block. Honest prior: the effect is strongest
sub-second; if even the corrected 1m test is flat, the real test is probe_l2_linecross.py on the recorder.

Usage:
  python backend/probe_impact_reversion.py --horizon 5 --impulse-bars 3 --cond-pct 70 --drop 50
  python backend/probe_impact_reversion.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
except Exception as e:  # pragma: no cover
    print(f"sklearn required: {e}")
    sys.exit(1)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
EPS = 1e-9
BASELINE = ["rv_15m", "rv_30m", "rv_60m", "impulse_abs"]   # incumbent: vol + how big the impulse was
IMPACT = ["impact_resid", "impact_resid_abs", "absorbed_ratio", "elasticity",
          "vpin", "lt_imbalance_abs", "cvd_div_abs"]


def _signed_flow(df):
    if "taker_buy" in df and "taker_sell" in df:
        return df["taker_buy"].astype(float) - df["taker_sell"].astype(float)
    if "delta" in df:
        return df["delta"].astype(float)
    return df.get("cvd_1m", pd.Series(0.0, index=df.index)).astype(float)


def build_features(df: pd.DataFrame, K: int, split: int) -> pd.DataFrame:
    """All known at close[t] — leak-free. Square-root-law scale b is FIT ON TRAIN ROWS ONLY (< split)."""
    out = pd.DataFrame(index=df.index)
    close = df["close"].astype(float)
    vol = df["volume"].astype(float) if "volume" in df else pd.Series(np.nan, index=df.index)
    sf = _signed_flow(df)
    rv15 = df.get("rv_15m", pd.Series(np.nan, index=df.index)).astype(float)

    impulse = close - close.shift(K)                              # signed K-bar move (the thing that may revert)
    flow_K = sf.rolling(K).sum()                                  # net signed flow over the same K bars
    vol_K = vol.rolling(K).sum()
    sqrt_term = np.sqrt((flow_K.abs() / (vol_K + EPS)).clip(upper=1.0))   # dimensionless flow intensity
    sigma_usd = rv15 * close / 1e4                               # rv (bps) -> $ scale of a typical move
    drive = sigma_usd * sqrt_term                                 # square-root-law predictor of |impulse| (signed by flow sign handled below)

    # FIT b on TRAIN ONLY: |impulse| ~ b * drive  (OLS through origin, leak-free)
    yj = impulse.abs().to_numpy(float); xj = drive.to_numpy(float)
    m = np.isfinite(yj) & np.isfinite(xj)
    m[split:] = False                                            # train rows only
    b = float(np.dot(xj[m], yj[m]) / (np.dot(xj[m], xj[m]) + EPS)) if m.sum() > 500 else 1.0
    pred_mag = (b * drive).clip(lower=EPS)                        # predicted |impulse| from flow

    out["impact_resid"] = impulse - (b * drive) * np.sign(flow_K)  # signed: moved more(+)/less(-) than flow predicts
    out["impact_resid_abs"] = out["impact_resid"].abs()
    out["absorbed_ratio"] = (impulse.abs() / pred_mag)            # <1 = absorbed (moved less than flow predicts)
    out["elasticity"] = (impulse.abs() / (flow_K.abs() + EPS))    # $ moved per unit net flow (thin book = high)
    out["vpin"] = df.get("vpin", pd.Series(np.nan, index=df.index)).astype(float)
    out["lt_imbalance_abs"] = df.get("large_trade_imbalance", pd.Series(np.nan, index=df.index)).astype(float).abs()
    out["cvd_div_abs"] = df.get("cvd_divergence", pd.Series(np.nan, index=df.index)).astype(float).abs()
    out["impulse_abs"] = impulse.abs()
    out["rv_15m"] = rv15
    out["rv_30m"] = df.get("rv_30m", pd.Series(np.nan, index=df.index)).astype(float)
    out["rv_60m"] = df.get("rv_60m", pd.Series(np.nan, index=df.index)).astype(float)
    out["_impulse"] = impulse
    out["_b"] = b
    return out


def forward_labels(df: pd.DataFrame, impulse: pd.Series, H: int, drop_usd: float, min_move: float):
    """REVERSAL = next-H move opposite the K-bar impulse (>= min_move). BIG-DROP = H-bar low excursion <= -drop."""
    close = df["close"].astype(float).to_numpy()
    low = df["low"].astype(float).to_numpy() if "low" in df else close.copy()
    n = len(close)
    fut_close = np.full(n, np.nan); fut_close[:n - H] = close[H:]
    imp = impulse.to_numpy()
    fut_move = fut_close - close
    rev = ((np.sign(fut_move) != np.sign(imp)) & (np.abs(fut_move) >= min_move) & (imp != 0)).astype(float)
    rev[~np.isfinite(fut_move) | ~np.isfinite(imp)] = np.nan
    fut_low = pd.Series(low).rolling(H).min().shift(-H).to_numpy()
    drop = ((fut_low - close) <= -drop_usd).astype(float)
    drop[~np.isfinite(fut_low)] = np.nan
    return rev, drop


def _auc(y, s):
    y = np.asarray(y, float); s = np.asarray(s, float)
    m = np.isfinite(s) & np.isfinite(y)
    if m.sum() < 50 or len(np.unique(y[m])) < 2:
        return float("nan")
    try:
        return float(roc_auc_score(y[m], s[m]))
    except Exception:
        return float("nan")


def _fit_auc(X: pd.DataFrame, cols, y, split, mask=None):
    idx = np.ones(len(X), bool) if mask is None else mask
    Xtr = X.iloc[:split][cols].to_numpy(float); Xte = X.iloc[split:][cols].to_numpy(float)
    ytr = y[:split].copy(); yte = y[split:].copy()
    itr = idx[:split]; ite = idx[split:]
    mtr = np.isfinite(Xtr).all(1) & np.isfinite(ytr) & itr
    mte = np.isfinite(Xte).all(1) & np.isfinite(yte) & ite
    if mtr.sum() < 500 or mte.sum() < 500 or len(np.unique(ytr[mtr])) < 2 or len(np.unique(yte[mte])) < 2:
        return float("nan")
    mu = Xtr[mtr].mean(0); sd = Xtr[mtr].std(0) + EPS
    clf = LogisticRegression(max_iter=200).fit((Xtr[mtr] - mu) / sd, ytr[mtr])
    p = clf.predict_proba((Xte[mte] - mu) / sd)[:, 1]
    return _auc(yte[mte], p)


def _block(X, y, split, mask, n_null, rng):
    base = _fit_auc(X, BASELINE, y, split, mask)
    aug = _fit_auc(X, BASELINE + IMPACT, y, split, mask)
    lift = (aug - base) if (np.isfinite(aug) and np.isfinite(base)) else float("nan")
    null = []
    if np.isfinite(lift) and n_null:
        sub = min(len(X), 90000); idx0 = np.linspace(0, len(X) - 1, sub).astype(int)
        fs = X.iloc[idx0].reset_index(drop=True); ys = y[idx0]
        ms = (mask[idx0] if mask is not None else np.ones(sub, bool)); ss = int(sub * 0.7)
        b0 = _fit_auc(fs, BASELINE, ys, ss, ms)
        for _ in range(n_null):
            perm = fs.copy(); perm[IMPACT] = perm[IMPACT].values[rng.permutation(len(perm))]
            a0 = _fit_auc(perm, BASELINE + IMPACT, ys, ss, ms)
            if np.isfinite(a0) and np.isfinite(b0):
                null.append(a0 - b0)
    null = np.array(null)
    return {"base": base, "aug": aug, "lift": lift,
            "p": float((null >= lift).mean()) if len(null) and np.isfinite(lift) else float("nan"),
            "null95": float(np.quantile(null, 0.95)) if len(null) else float("nan")}


def evaluate(df, H, K, cond_pct, drop_usd, min_move, n_null=100):
    n = len(df); split = int(n * 0.7)
    feats = build_features(df, K, split)
    rev, drop = forward_labels(df, feats["_impulse"], H, drop_usd, min_move)
    b = float(feats["_b"].iloc[0])
    imp_abs = feats["impulse_abs"].to_numpy(float)
    thr = float(np.nanquantile(imp_abs, cond_pct / 100.0))
    cond = imp_abs >= thr                                          # top-(100-cond_pct)% impulse subset
    feats = feats.drop(columns=["_impulse", "_b"])
    rng = np.random.default_rng(0)
    res = {"n": n, "H": H, "K": K, "b": b, "cond_thr": thr, "cond_pct": cond_pct, "targets": {}}
    # reversal: unconditional + conditional; big_drop: unconditional
    res["targets"]["reversal_all"] = {"rate": float(np.nanmean(rev)),
                                      "uni": {c: _auc(rev, feats[c].to_numpy(float)) for c in IMPACT},
                                      **_block(feats, rev, split, None, n_null, rng)}
    res["targets"][f"reversal_top{100-cond_pct}pct"] = {
        "rate": float(np.nanmean(rev[cond])), "n_sub": int(cond.sum()),
        "uni": {c: _auc(rev[cond], feats[c].to_numpy(float)[cond]) for c in IMPACT},
        **_block(feats, rev, split, cond, n_null, rng)}
    res["targets"]["big_drop"] = {"rate": float(np.nanmean(drop)),
                                  "uni": {c: _auc(drop, feats[c].to_numpy(float)) for c in IMPACT},
                                  **_block(feats, drop, split, None, n_null, rng)}
    return res


def _f(x, nd=3):
    return f"{x:.{nd}f}" if isinstance(x, float) and np.isfinite(x) else "  -  "


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--impulse-bars", type=int, default=3)
    ap.add_argument("--cond-pct", type=int, default=70, help="condition reversal on impulses above this percentile")
    ap.add_argument("--drop", type=float, default=50.0)
    ap.add_argument("--min-move", type=float, default=20.0)
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--shuffle-null", type=int, default=100)
    a = ap.parse_args()
    if not os.path.exists(MATRIX):
        print(f"matrix not found: {MATRIX}"); return
    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    if a.days and "ts_ms" in df:
        df = df[df["ts_ms"] >= df["ts_ms"].max() - a.days * 86400000].reset_index(drop=True)
    r = evaluate(df, a.horizon, a.impulse_bars, a.cond_pct, a.drop, a.min_move, a.shuffle_null)
    print("=" * 88)
    print(f"IMPACT → REVERSAL / BIG-DROP PROBE v2  (n={r['n']:,} 1m bars, H={a.horizon}m, impulse K={a.impulse_bars}m, "
          f"fitted b={r['b']:.3g}, leak-free)")
    print("=" * 88)
    print("Corrected: fitted square-root-law scale, K-bar impulse, elasticity (not collinear), CONDITIONAL on")
    print(f"the top-{100-a.cond_pct}% impulse (|impulse| >= ${r['cond_thr']:.0f}). Does impact lift reversal AUC over rv?\n")
    for tname, t in r["targets"].items():
        sub = f", n_sub={t['n_sub']:,}" if "n_sub" in t else ""
        print(f"── {tname.upper()}  (rate {100*t['rate']:.1f}%{sub})")
        print(f"   rv baseline {_f(t['base'])}  +impact {_f(t['aug'])}  LIFT "
              f"{('%+.3f'%t['lift']) if np.isfinite(t['lift']) else ' - '}  "
              f"(null95 {('%+.3f'%t['null95']) if np.isfinite(t['null95']) else ' - '}, p={_f(t['p'])})")
        best = sorted(t["uni"].items(), key=lambda kv: -(kv[1] if np.isfinite(kv[1]) else 0))[:3]
        print("   top univariate: " + ", ".join(f"{k}={_f(v)}" for k, v in best))
        sig = np.isfinite(t["lift"]) and t["lift"] >= 0.005 and np.isfinite(t["p"]) and t["p"] < 0.05
        print(f"   → {'SIGNAL — impact adds real, significant lift' if sig else 'no lift over rv'}\n")
    print("If the CONDITIONAL reversal still shows no lift, the corrected 1m construction is genuinely flat →")
    print("the absorption effect lives sub-second (probe_l2_linecross.py, gated on the recorder).")


def selftest():
    rng = np.random.default_rng(0); n = 9000
    close = 60000 + np.cumsum(rng.normal(0, 8, n))
    df = pd.DataFrame({"ts_ms": np.arange(n) * 60000, "close": close,
                       "low": close - np.abs(rng.normal(0, 6, n)), "volume": np.abs(rng.normal(120, 30, n)),
                       "taker_buy": np.abs(rng.normal(60, 12, n)), "taker_sell": np.abs(rng.normal(60, 12, n)),
                       "rv_15m": np.abs(rng.normal(0, 1, n)) + 0.5, "rv_30m": np.abs(rng.normal(0, 1, n)) + 0.5,
                       "rv_60m": np.abs(rng.normal(0, 1, n)) + 0.5, "vpin": rng.uniform(0, 1, n),
                       "large_trade_imbalance": rng.normal(0, 1, n), "cvd_divergence": rng.normal(0, 1, n)})
    r = evaluate(df, 5, 3, 70, 50.0, 20.0, n_null=15)
    assert "reversal_all" in r["targets"] and "big_drop" in r["targets"]
    for tn, t in r["targets"].items():
        if np.isfinite(t["lift"]):
            assert t["lift"] < 0.06, f"{tn} fake lift {t['lift']}"
    print("probe_impact_reversion v2 self-test: fitted-k + conditional labels build, no fake lift on noise. ALL PASS")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
