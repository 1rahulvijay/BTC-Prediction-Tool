"""
train_fade_model.py - ENSEMBLE model for the fade ENTRY decision (when to buy) + accuracy test.
================================================================================================
Predicts P(FADE WINS) = at the moment price touches anchor+/-$50, will it revert to the anchor
(take-profit) BEFORE extending to the 2x stop? That IS the buy signal: fade when P is high.
Exit is deterministic (TP=anchor, stop=2x). Polymarket "buy both ways": the model scores the
up-touch fade (BUY DOWN) and the down-touch fade (BUY UP) with the same features + a side flag.

HONEST LABEL (v5, 2026-07-01): fade_win uses _fade_strict -- price must ACTUALLY reach the anchor
TP before the stop; unresolved-by-expiry = LOSS. The old label reused _first_passage_fade's
settle-by-close fallback, which counted any tick back off the level as a "win" and grossly inflated
LATE touches (a $50 last-minute touch "won" 71% by settle but only 6.9% strictly reaches anchor).
On the honest label the base reach-anchor rate is ~0.27 and the top-decile P(fade) reaches ~0.69 --
real signal, but nowhere near the fake 0.99. Early touch is NECESSARY (late can't revert in time),
touch-context is what makes it SUFFICIENT. See probe_roundtrip_and_timing.py for the timing audit.

Features = the parity-proven vol keepers (rv_15m/rv_30m/rv_60m/compression_ratio/shock_magnitude,
known at window open) + touch_frac (time-left fraction at the touch: high=EARLY) + side_up + THREE
causal touch-context features based only on completed bars strictly before the 1m touch candle:
  overshoot_bps  = 0 at the exact barrier crossing (true overshoot needs tick/1s data)
  pre_opp_bps    = furthest completed OPPOSITE excursion before the touch
  pre_range_bps  = completed pre-touch high-low range
If the touch candle also contains the anchor TP or 2x stop, it is excluded because 1m OHLC cannot
tell which event happened first. Earlier A/B scores that used the full touch candle are retracted.
(ret_5m was rejected: it is a FORWARD 5m return in the matrix = a leak, corr 0.976 w/ future ret.)
Target = fade_win (first-passage: anchor reached before the 2x stop), leak-free from the 1-min path.

ENSEMBLE = CatBoost + LightGBM + HistGBM (averaged), isotonic-calibrated, temporal 98/2 via
BTC_TRAIN_SPLIT_FRAC. Saves data/saved_models/fade_model.pkl. Reports AUC + calibration +
PRECISION-AT-COVERAGE (the real accuracy test: at the top P(fade), what win% do you actually get?).

Usage:
  python backend/train_fade_model.py
  python backend/train_fade_model.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_fade_entry_exit as FE  # noqa: E402  (touch detection / first-passage entry)
from probe_roundtrip_and_timing import _fade_strict  # noqa: E402  (HONEST label: reach anchor before stop)

# Manifest written in the same step as the artifact: without it the artifact reads as
# UNKNOWN identity, and phold_challenger refuses to deploy any calibrator while a source
# artifact fails identity enforcement - which disables
# PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1.
from verified_io import write_manifest as write_integrity_manifest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
# BTC_MODEL_OUTPUT_DIR is honoured here for the same reason as the other four cheap-head
# trainers: auto_finetune.py points it at a candidate directory so a nightly run cannot
# overwrite a serving artifact. This was the ONLY trainer of the five that hardcoded the
# serving path, so a redirected run still replaced fade_model.pkl under the live app.
OUT = os.path.join(
    os.environ.get("BTC_MODEL_OUTPUT_DIR") or os.path.join(DATA_DIR, "saved_models"),
    "fade_model.pkl")
KEEPERS = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude"]
TOUCH_CTX = ["overshoot_bps", "pre_opp_bps", "pre_range_bps"]
FEATURES = KEEPERS + ["touch_frac", "side_up"] + TOUCH_CTX
HORIZONS = (5, 15)
# Multi-barrier (v4, 2026-07-01): train a SEPARATE fade model per barrier. $30 matches how Polymarket UP/DOWN
# shares actually reprice (a $20-30 move near the anchor already swings the share price) -> ~2x more setups than
# the $50 barrier. Each barrier has its own touch level (anchor+/-L), TP (anchor), and stop (2L). $50 stays the
# backward-compatible default (bundle['horizons'] == the $50 barrier) so older callers keep working unchanged.
BARRIERS = (30.0, 50.0)
L = 50.0                       # legacy default barrier (back-compat; per-barrier training uses BARRIERS)
ENSEMBLE = ("catboost", "lightgbm", "histgbm")
HEAD_VERSION = "2026-07-03-fade-v5-research-only-gate-failed"


def _touch_ctx(H_i, Lo_i, anc, side, tm, L):
    """Causal context at first touch using completed bars strictly before the touch bar.

    A 1m OHLC touch bar contains prices observed after the first barrier crossing. Using its
    final high/low leaked future post-entry movement into overshoot, opposite excursion, and
    range. The exact crossing is therefore represented by the barrier itself (zero overshoot),
    plus only completed pre-touch bars. Tick/1s training can restore true live overshoot later.
    """
    lvl = anc + L if side == "down" else anc - L
    prior_hi = list(np.asarray(H_i[:tm], dtype=float))
    prior_lo = list(np.asarray(Lo_i[:tm], dtype=float))
    known_hi = max([anc, lvl] + prior_hi)
    known_lo = min([anc, lvl] + prior_lo)
    overshoot = 0.0
    pre_opp = ((anc - known_lo) if side == "down" else (known_hi - anc)) / anc * 1e4
    pre_range = (known_hi - known_lo) / anc * 1e4
    return overshoot, pre_opp, pre_range


def _ambiguous_touch_bar(H_i, Lo_i, anc, side, tm, L):
    """True when 1m OHLC cannot order entry versus TP/stop inside the touch bar."""
    if side == "down":
        return bool(Lo_i[tm] <= anc or H_i[tm] >= anc + 2 * L)
    return bool(H_i[tm] >= anc or Lo_i[tm] <= anc - 2 * L)


def build_dataset(df, w, L, stride=1):
    c = df["close"].values
    H = np.column_stack([df["high"].shift(-k).values for k in range(1, w + 1)])
    Lo = np.column_stack([df["low"].shift(-k).values for k in range(1, w + 1)])
    kv = df[KEEPERS].values
    ok = (~np.isnan(H).any(1)) & (~np.isnan(Lo).any(1)) & (~np.isnan(kv).any(1))
    rows = []
    for i in np.where(ok)[0][::stride]:
        anc = c[i]
        for side, su in (("down", 1), ("up", 0)):
            e, win, xm, tm = FE._first_passage_fade(H[i], Lo[i], anc, L, side)  # touch detection + tm at barrier L
            if e:
                # Most 1m touch bars contain an anchor/stop crossing whose intrabar order is unknowable.
                # Excluding them is conservative and prevents the model from learning a fabricated outcome.
                if _ambiguous_touch_bar(H[i], Lo[i], anc, side, tm, L):
                    continue
                _, win, _ = _fade_strict(H[i], Lo[i], anc, L, side)   # HONEST label: reached anchor TP before 2L stop
                ov, po, pr = _touch_ctx(H[i], Lo[i], anc, side, tm, L)
                rows.append(list(kv[i]) + [(w - tm) / w, su, ov, po, pr, int(win)])
    return pd.DataFrame(rows, columns=FEATURES + ["fade_win"])


def _clf_models():
    from catboost import CatBoostClassifier
    import lightgbm as lgb
    from sklearn.ensemble import HistGradientBoostingClassifier
    return [CatBoostClassifier(iterations=300, depth=4, learning_rate=0.05, random_seed=0,
                               verbose=0, allow_writing_files=False),
            lgb.LGBMClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, verbose=-1, n_jobs=2),
            HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=4, random_state=0)]


def _proba(models, X):
    return np.mean([m.predict_proba(X)[:, 1] for m in models], axis=0)


def train():
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import roc_auc_score
    import joblib
    df = pd.read_parquet(MATRIX).sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True)
    _sf = min(max(float(os.environ.get("BTC_TRAIN_SPLIT_FRAC", "0.98")), 0.5), 0.98)
    bundle = {"version": HEAD_VERSION, "features": FEATURES, "keepers": KEEPERS, "ensemble": list(ENSEMBLE),
              "live_supported": False, "research_only": True,
              "promotion_gate": {"test_auc_min": 0.70, "test_top10_precision_min": 0.55},
              "causal_touch_context": True, "ambiguous_touch_bars_excluded": True,
              "L": L, "barriers": {}, "horizons": {}, "trained": time.time()}
    for Lb in BARRIERS:
        bhz = {}
        for w in HORIZONS:
            d = build_dataset(df, w, Lb).replace([np.inf, -np.inf], np.nan).dropna()
            X = d[FEATURES].values; y = d["fade_win"].values.astype(int)
            n = len(d); a, b = int(n * (2 * _sf - 1)), int(n * _sf)   # 98/2: fit / cal / test
            models = [m.fit(X[:a], y[:a]) for m in _clf_models()]
            iso = IsotonicRegression(out_of_bounds="clip").fit(_proba(models, X[a:b]), y[a:b])
            pte = iso.transform(_proba(models, X[b:])); yte = y[b:]
            try:
                auc = roc_auc_score(yte, _proba(models, X[b:]))
            except ValueError:
                auc = float("nan")
            # PRECISION-AT-COVERAGE: bet only the top-p fades -> realized win% (the money metric)
            cov = {}
            order = np.argsort(-pte)
            for c_ in (1.0, 0.5, 0.25, 0.10):
                k = max(20, int(len(pte) * c_))
                cov[c_] = float(yte[order[:k]].mean())
            bhz[w] = {"models": models, "iso": iso, "auc": float(auc),
                      "base_win": float(yte.mean()), "coverage_win": cov, "n": int(n)}
            print(f"[${int(Lb)} {w}m] n={n} base_win={yte.mean():.3f} AUC={auc:.3f}  win@top: "
                  + " ".join(f"{int(c_*100)}%={cov[c_]:.3f}" for c_ in (1.0, 0.5, 0.25, 0.10)), flush=True)
        bundle["barriers"][Lb] = {"horizons": bhz}
    bundle["horizons"] = bundle["barriers"][50.0]["horizons"]   # back-compat: default lookup == the $50 barrier
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = f"{OUT}.tmp.{os.getpid()}"
    try:
        joblib.dump(bundle, tmp)
        write_integrity_manifest(tmp)
        os.replace(tmp, OUT)
        write_integrity_manifest(OUT)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    print(f"saved -> {OUT} ({os.path.getsize(OUT)//1024} KB)  barriers={BARRIERS}  ensemble={ENSEMBLE}")
    print("READ: win@10% >> base_win => the model concentrates the fade edge; bet only high-P(fade) touches.")
    print("      $30 gives ~2x the setups of $50 (matches Polymarket share sensitivity) -- compare base_win/AUC.")
    return bundle


def predict_fade(bundle, horizon, keepers, touch_frac, side_up,
                 overshoot_bps=0.0, pre_opp_bps=0.0, pre_range_bps=0.0, L=50.0):
    """Live: P(this fade wins) at the moment price touches anchor+/-$L. side_up=1 for an UP-touch
    (BUY DOWN). L selects the per-barrier model ($30 or $50); falls back to the legacy $50 horizons
    map for older single-barrier bundles. The 3 touch-context values are computed live from the running
    path up TO the touch (overshoot beyond $L, furthest opposite pre-move, pre-touch range -- all bps of
    anchor). Absent -> 0.0. Vector is built by bundle['features'] order so it stays correct across versions."""
    _bh = (bundle.get("barriers") or {}).get(float(L)) or {}
    hzmap = _bh.get("horizons") or bundle.get("horizons") or {}
    # A 5m fade model is not calibrated for a different settlement window.
    hz = hzmap.get(horizon)
    if hz is None:
        return None
    vals = {**{f: float(keepers[f]) for f in bundle["keepers"]},
            "touch_frac": float(touch_frac), "side_up": float(side_up),
            "overshoot_bps": float(overshoot_bps), "pre_opp_bps": float(pre_opp_bps),
            "pre_range_bps": float(pre_range_bps)}
    x = np.array([[vals[f] for f in bundle["features"]]])
    return float(hz["iso"].transform(_proba(hz["models"], x))[0])


def selftest():
    rng = np.random.default_rng(0); n = 2000
    df = pd.DataFrame({k: np.abs(rng.normal(1, .3, n)) for k in KEEPERS})
    df["close"] = 60000 + np.cumsum(rng.normal(0, 5, n))
    df["high"] = df["close"] + np.abs(rng.normal(0, 30, n)); df["low"] = df["close"] - np.abs(rng.normal(0, 30, n))
    df["ts_ms"] = np.arange(n) * 60000
    d30 = build_dataset(df, 5, 30.0, stride=1)
    d50 = build_dataset(df, 5, 50.0, stride=1)
    ok = (len(d30) > 100 and len(d50) > 100 and set(d30["fade_win"].unique()) <= {0, 1}
          and len(d30) >= len(d50))   # $30 barrier is touched at least as often as $50
    print(f"selftest: built $30={len(d30)} / $50={len(d50)} fade events, cols={list(d30.columns)}")
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not os.path.exists(MATRIX):
        print(f"missing {MATRIX}"); sys.exit(2)
    train()


if __name__ == "__main__":
    main()
