"""
train_fade_model_1s.py - fade model v6 at 1-SECOND resolution (the honest fix for the 1m ceiling).
====================================================================================================
WHY: v5 (1m bars) had to exclude ambiguous touch-bars and zero the overshoot feature because a
1-minute candle hides the event order inside the touch minute -- the audited-honest ceiling was
top-decile ~0.44, below the live 0.55 gate, leaving the fade feature dormant. At 1-SECOND bars the
sequence is observable: TRUE overshoot at the touch, unambiguous TP-vs-stop ordering, touch timing
in seconds. This RESTORES train/serve parity: the live tracker already watches per-tick (~1s)
running extremes -- training finally matches it.

DATA: 1s OHLC built from the cached Binance spot aggTrade CSVs (data/backfill_cache, 400d on disk).
Default window = the most recent 150 days (price-consistent; also sidesteps the $-barrier bps drift).
Keepers at window open join from research_matrix_1m.parquet (same parity-proven 5 features).

Windows: 5m (stride 120s) and 15m (stride 300s), barriers $30 and $50 (matches live FADE_L=30 + legacy).
Label  : STRICT first-passage from touch_second+1 -- anchor TP before the 2L stop, else LOSS.
         Touch seconds whose own 1s bar already contains the TP or stop are skipped (now rare).
Features (all known at the touch): keepers(5) + touch_frac + side_up + overshoot_bps (touch-second
         extreme beyond the barrier) + pre_opp_bps + pre_range_bps (window-open..touch extremes).
Ensemble: CatBoost+LightGBM+HistGBM, isotonic on the middle slice, temporal fit/cal/test via
         BTC_TRAIN_SPLIT_FRAC (3-way: fit=2sf-1, cal=sf-..., test=1-sf).

PRE-DECLARED ADOPTION GATES (frozen before the first run; no post-hoc tuning):
         OOS AUC >= 0.70  AND  strict top-decile win >= 0.55  (per barrier/horizon to be wired).
Output : data/saved_models/fade_model.pkl ONLY with --save (never silently clobbers v5);
         bundle flags: resolution='1s', overshoot_live=True, version=HEAD_VERSION.

Usage:  python backend/train_fade_model_1s.py [--days 150] [--save]
        python backend/train_fade_model_1s.py --selftest
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
CACHE = os.path.join(DATA_DIR, "backfill_cache")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
OUT = os.path.join(DATA_DIR, "saved_models", "fade_model.pkl")
KEEPERS = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude"]
FEATURES = KEEPERS + ["touch_frac", "side_up", "overshoot_bps", "pre_opp_bps", "pre_range_bps"]
HEAD_VERSION = "2026-07-03-fade-v6-1s"
BARRIERS = (30.0, 50.0)
HORIZONS = {5: 120, 15: 300}          # horizon minutes -> window stride seconds
GATE_AUC, GATE_TOP10 = 0.70, 0.55


def day_1s_bars(csv_path):
    """One cached aggTrade CSV -> 1s bars (sec_of_day -> high/low/close as dense arrays)."""
    df = pd.read_csv(csv_path, header=None, usecols=[1, 5], names=["price", "ts"],
                     dtype={1: np.float64, 5: np.int64})
    if len(df) and df["ts"].iloc[0] > 10**14:          # some archives use microseconds
        df["ts"] = df["ts"] // 1000
    sec = ((df["ts"] // 1000) % 86400).values
    px = df["price"].values
    g = pd.DataFrame({"s": sec, "p": px}).groupby("s")["p"]
    agg = g.agg(["max", "min", "last"])
    hi = np.full(86400, np.nan); lo = np.full(86400, np.nan); cl = np.full(86400, np.nan)
    idx = agg.index.values.astype(int)
    hi[idx] = agg["max"].values; lo[idx] = agg["min"].values; cl[idx] = agg["last"].values
    # forward-fill empty seconds with the last close (no trades = price unchanged)
    for arr in (cl,):
        m = pd.Series(arr).ffill().values
        cl[:] = m
    hi = np.where(np.isnan(hi), cl, hi)
    lo = np.where(np.isnan(lo), cl, lo)
    return hi, lo, cl


def scan_windows(hi, lo, cl, day_ms0, keepers_by_min, rows):
    """All windows/barriers for one day of 1s bars. Appends event rows in place."""
    for h_min, stride in HORIZONS.items():
        dur = h_min * 60
        for start in range(0, 86400 - dur, stride):
            anc = cl[start]
            if not np.isfinite(anc) or anc <= 0:
                continue
            kp = keepers_by_min.get(day_ms0 + start * 1000)
            if kp is None:
                continue
            whi = hi[start + 1: start + dur + 1]
            wlo = lo[start + 1: start + dur + 1]
            if np.isnan(whi).any() or np.isnan(wlo).any():
                continue
            for L in BARRIERS:
                for side, su in (("down", 1), ("up", 0)):     # side=down => fade an UP-touch
                    lvl = anc + L if side == "down" else anc - L
                    crossed = (whi >= lvl) if side == "down" else (wlo <= lvl)
                    if not crossed.any():
                        continue
                    tm = int(np.argmax(crossed))
                    # ambiguity guard at 1s (rare): the touch second already contains TP or stop
                    if side == "down":
                        if wlo[tm] <= anc or whi[tm] >= anc + 2 * L:
                            continue
                        overshoot = (whi[tm] - lvl) / anc * 1e4
                        pre_opp = (anc - np.min(wlo[:tm + 1])) / anc * 1e4
                        post_hi, post_lo = whi[tm + 1:], wlo[tm + 1:]
                        tp_hit = post_lo <= anc
                        st_hit = post_hi >= anc + 2 * L
                    else:
                        if whi[tm] >= anc or wlo[tm] <= anc - 2 * L:
                            continue
                        overshoot = (lvl - wlo[tm]) / anc * 1e4
                        pre_opp = (np.max(whi[:tm + 1]) - anc) / anc * 1e4
                        post_hi, post_lo = whi[tm + 1:], wlo[tm + 1:]
                        tp_hit = post_hi >= anc
                        st_hit = post_lo <= anc - 2 * L
                    t_tp = int(np.argmax(tp_hit)) if tp_hit.any() else 10**9
                    t_st = int(np.argmax(st_hit)) if st_hit.any() else 10**9
                    win = int(t_tp < t_st)            # strict: must reach TP first; neither => loss
                    if t_tp == t_st == 10**9:
                        win = 0
                    pre_range = (np.max(whi[:tm + 1]) - np.min(wlo[:tm + 1])) / anc * 1e4
                    rows.append((h_min, L, day_ms0 + start * 1000,
                                 *[kp[k] for k in KEEPERS],
                                 (dur - (tm + 1)) / dur, su, overshoot, pre_opp, pre_range, win))


def build(days):
    files = sorted(glob.glob(os.path.join(CACHE, "BTCUSDT-aggTrades-????-??-??.csv")))
    files = [f for f in files if "perp" not in os.path.basename(f)][-days:]
    if not files:
        raise SystemExit(f"no aggTrade CSVs in {CACHE}")
    mx = pd.read_parquet(MATRIX, columns=["ts_ms"] + KEEPERS).drop_duplicates("ts_ms")
    mx = mx.dropna()
    keepers_by_min = {int(r.ts_ms): {k: float(getattr(r, k)) for k in KEEPERS}
                      for r in mx.itertuples()}
    rows = []
    t0 = time.time()
    for i, f in enumerate(files):
        date = os.path.basename(f)[len("BTCUSDT-aggTrades-"):-4]
        day_ms0 = int(pd.Timestamp(date, tz="UTC").timestamp() * 1000)
        try:
            hi, lo, cl = day_1s_bars(f)
        except Exception as e:
            print(f"  [{date}] parse failed: {str(e)[:60]} -- skipped")
            continue
        scan_windows(hi, lo, cl, day_ms0, keepers_by_min, rows)
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(files)} days  events={len(rows):,}  ({time.time()-t0:.0f}s)", flush=True)
    cols = ["h", "L", "ts_ms"] + FEATURES + ["fade_win"]
    d = pd.DataFrame(rows, columns=cols)
    print(f"built {len(d):,} events from {len(files)} days in {time.time()-t0:.0f}s")
    return d


def _clf_models():
    from catboost import CatBoostClassifier
    import lightgbm as lgb
    from sklearn.ensemble import HistGradientBoostingClassifier
    return [CatBoostClassifier(iterations=300, depth=4, learning_rate=0.05, random_seed=0,
                               verbose=0, allow_writing_files=False, thread_count=4),
            lgb.LGBMClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, verbose=-1, n_jobs=4),
            HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=4, random_state=0)]


def _proba(models, X):
    return np.mean([m.predict_proba(X)[:, 1] for m in models], axis=0)


def train(d, save=False):
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import roc_auc_score
    _sf = min(max(float(os.environ.get("BTC_TRAIN_SPLIT_FRAC", "0.98")), 0.5), 0.98)
    bundle = {"version": HEAD_VERSION, "features": FEATURES, "keepers": KEEPERS,
              "resolution": "1s", "overshoot_live": True, "L": 50.0,
              "barriers": {}, "horizons": {}, "trained": time.time()}
    all_pass = {}
    for Lb in BARRIERS:
        bhz = {}
        for h in HORIZONS:
            g = d[(d["h"] == h) & (d["L"] == Lb)].sort_values("ts_ms")
            g = g.replace([np.inf, -np.inf], np.nan).dropna()
            X = g[FEATURES].values
            y = g["fade_win"].values.astype(int)
            n = len(g)
            a, b = int(n * (2 * _sf - 1)), int(n * _sf)
            if n < 5000 or y[:a].sum() < 50:
                print(f"[${int(Lb)} {h}m] insufficient events ({n}) -- skipped")
                continue
            models = [m.fit(X[:a], y[:a]) for m in _clf_models()]
            iso = IsotonicRegression(out_of_bounds="clip").fit(_proba(models, X[a:b]), y[a:b])
            pte_raw = _proba(models, X[b:])
            pte = iso.transform(pte_raw)
            yte = y[b:]
            try:
                auc = roc_auc_score(yte, pte_raw)
            except ValueError:
                auc = float("nan")
            order = np.argsort(-pte)
            cov = {c: float(yte[order[:max(20, int(len(pte) * c))]].mean()) for c in (1.0, 0.25, 0.10)}
            gate = (auc >= GATE_AUC) and (cov[0.10] >= GATE_TOP10)
            all_pass[(Lb, h)] = gate
            print(f"[${int(Lb)} {h}m] n={n:,} base={yte.mean():.3f} AUC={auc:.3f} "
                  f"win@top25%={cov[0.25]:.3f} win@top10%={cov[0.10]:.3f}  "
                  f"GATE(auc>={GATE_AUC}, top10>={GATE_TOP10}): {'PASS' if gate else 'FAIL'}")
            bhz[h] = {"models": models, "iso": iso, "auc": float(auc),
                      "base_win": float(yte.mean()), "coverage_win": cov, "n": int(n),
                      "gate_pass": bool(gate)}
        bundle["barriers"][Lb] = {"horizons": bhz}
    bundle["horizons"] = bundle["barriers"].get(50.0, {}).get("horizons", {})
    if save:
        import joblib
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        tmp = f"{OUT}.tmp.{os.getpid()}"
        try:
            joblib.dump(bundle, tmp)
            os.replace(tmp, OUT)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        print(f"saved -> {OUT} ({os.path.getsize(OUT)//1024} KB)  version={HEAD_VERSION}")
    else:
        print("(dry run -- NOT saved; rerun with --save to write fade_model.pkl)")
    return bundle, all_pass


def selftest():
    # synthetic: 1s path that touches +30 then reverts -> one clean win event
    rng = np.random.default_rng(0)
    hi = np.full(86400, np.nan); lo = np.full(86400, np.nan); cl = np.full(86400, np.nan)
    base = 60000 + np.cumsum(rng.normal(0, 1.5, 86400))
    spike = np.zeros(86400); spike[100:130] = np.linspace(0, 35, 30); spike[130:220] = np.linspace(35, -10, 90)
    cl[:] = base + spike; hi[:] = cl + 1; lo[:] = cl - 1
    kb = {0 + s * 1000: {k: 1.0 for k in KEEPERS} for s in range(0, 86400, 60)}
    rows = []
    scan_windows(hi, lo, cl, 0, kb, rows)
    d = pd.DataFrame(rows, columns=["h", "L", "ts_ms"] + FEATURES + ["fade_win"])
    ok = len(d) > 100 and d["fade_win"].between(0, 1).all() and (d["overshoot_bps"] >= 0).all()
    print(f"selftest: {len(d)} events, wins={int(d['fade_win'].sum())}, "
          f"overshoot>=0 ok={bool((d['overshoot_bps']>=0).all())}")
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=150)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    d = build(a.days)
    train(d, save=a.save)


if __name__ == "__main__":
    main()
