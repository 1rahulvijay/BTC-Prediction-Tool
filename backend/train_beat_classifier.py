"""
train_beat_classifier.py — the Polymarket "BEAT" head (offline, historical).
=============================================================================
Per-horizon calibrated binary classifier: P(window-close >= window-open) — the EXACT Polymarket
settlement question (strict up/down, no NEUTRAL, clock-aligned). Trained offline on history
reconstructed from SPOT aggTrades, like the A1 persistence head.

WHY IT EXISTS / HOW IT HELPS THE APP:
  • Its calibrated output P(beat) IS the proper `p_up` / FAIR VALUE for a Polymarket window — a real
    measured probability, not the rejected flat-$40 formula. Bet only when market_ask < P(beat).
  • It is a DECORRELATED second opinion: a LEAN, fully-backfillable feature set (price/vol/flow at the
    window open) + a different label + the exact betting framing — so it can also be ensembled with the
    main direction stack (as a feature or an agreement vote) once it proves out.
  • Primary role = the BETTING/fair-value layer; secondary = a second opinion. It does NOT escape the
    information ceiling (same 5m problem, cleaner label) — its value is the calibrated probability.

HOW WE KNOW IT'S SIGNAL, NOT NOISE (gates baked in — it REFUSES to save if it fails):
  1. TEMPORAL out-of-sample split (train past → test unseen future; no leakage).
  2. AUC must clear NOISE_AUC (~0.55, the bettable floor) on the unseen test — AUC≈0.5 = no signal.
  3. CALIBRATION + a USABLE confident subset: >=20 calls at >=0.6 that realize >=55% (not just
     "calibrated when it abstains" — a model that almost never commits is not signal, §5bt).
  4. Must BEAT the base-rate baseline (predicting the majority class).
  Fails any gate → printed "NOISE — not saved". Then live paper-tracking before real money.

Usage:  python backend/train_beat_classifier.py --start 2026-03-15 --end 2026-06-12
        python backend/train_beat_classifier.py --validate 2026-06-12     # 1-day smoke (no save)
"""
import argparse
import os

import numpy as np

from backfill_trade_features import download_day, load_aggtrades, _daterange as daterange

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
OUT_PATH = os.path.join(DATA_DIR, "saved_models", "beat_model.pkl")
HORIZONS = (1, 3, 5, 7, 10, 15)
NOISE_AUC = 0.55           # bettable floor (SPEC §6); below this on unseen test = no signal → not saved
FEATURE_NAMES = ["ret_1", "ret_5", "ret_15", "rv_short", "rv_long", "variance_ratio",
                 "range_pos", "atr_norm", "mom_20", "hour_sin", "hour_cos"]


def ticks_to_ohlc(ts, price):
    """Tick (ms, price) -> per-1m-bar open/high/low/close + minute-start ms (sorted, gap-free index)."""
    minute = (ts // 60_000)
    uniq, idx = np.unique(minute, return_inverse=True)
    n = len(uniq)
    o = np.zeros(n); h = np.full(n, -np.inf); l = np.full(n, np.inf); c = np.zeros(n)
    seen_o = np.zeros(n, dtype=bool)
    for k in range(len(price)):
        b = idx[k]; p = price[k]
        if not seen_o[b]:
            o[b] = p; seen_o[b] = True
        h[b] = max(h[b], p); l[b] = min(l[b], p); c[b] = p
    return uniq * 60_000, o, h, l, c


def build_beat_features(o, h, l, c, tmin_ms):
    """Lean, fully-backfillable features AT each bar (window open). Index i uses ONLY data <= i."""
    n = len(c)
    logc = np.log(np.where(c > 0, c, 1.0))
    ret1 = np.zeros(n); ret1[1:] = np.diff(logc)
    def lag_ret(k):
        r = np.zeros(n); r[k:] = logc[k:] - logc[:-k]; return r
    ret5, ret15 = lag_ret(5), lag_ret(15)
    def roll_std(x, w):
        out = np.zeros(n)
        for i in range(n):
            j = max(0, i - w + 1); out[i] = np.std(x[j:i + 1]) if i - j >= 2 else 0.0
        return out
    rv_short, rv_long = roll_std(ret1, 15), roll_std(ret1, 60)
    # Lo-MacKinlay variance ratio (q=5)
    vr = np.zeros(n)
    var1 = rv_short ** 2
    ret5_var = roll_std(ret5, 30) ** 2
    _safe_var1 = np.where(var1 > 1e-12, var1, 1.0)
    vr = np.where(var1 > 1e-12, ret5_var / (5.0 * _safe_var1) - 1.0, 0.0)
    # range position over last 30
    range_pos = np.zeros(n)
    for i in range(n):
        j = max(0, i - 29); hi = np.max(h[j:i + 1]); lo = np.min(l[j:i + 1])
        range_pos[i] = (c[i] - lo) / (hi - lo) if hi > lo else 0.5
    # ATR(14) normalized
    tr = np.zeros(n); tr[0] = h[0] - l[0]
    tr[1:] = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])
    atr = np.zeros(n)
    for i in range(n):
        j = max(0, i - 13); atr[i] = np.mean(tr[j:i + 1])
    atr_norm = np.where(c > 0, atr / c, 0.0)
    mom20 = np.zeros(n)
    for i in range(n):
        j = max(0, i - 19); m = np.mean(c[j:i + 1]); mom20[i] = c[i] / m - 1.0 if m > 0 else 0.0
    hour = ((tmin_ms // 3_600_000) % 24).astype(float)
    hsin, hcos = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    X = np.column_stack([ret1, ret5, ret15, rv_short, rv_long, vr, range_pos, atr_norm,
                         mom20, hsin, hcos])
    return np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)


def beat_labels(o, c, horizon):
    """beat[t] = 1 if window-close (c[t+h-1]) >= window-open (o[t]). NaN-guarded tail."""
    n = len(c); y = np.full(n, -1)
    end = n - horizon
    if end <= 0:
        return y
    y[:end] = (c[horizon - 1:horizon - 1 + end] >= o[:end]).astype(int)
    return y


def _ohlc_for_dates(dates):
    O = H = L = C = T = None
    for d in dates:
        try:
            ts, price, _q, _m = load_aggtrades(download_day(d))
        except Exception as e:
            print(f"[{d}] skip ({str(e)[:60]})"); continue
        order = np.argsort(ts, kind="stable"); ts, price = ts[order], price[order]
        tm, o, h, l, c = ticks_to_ohlc(ts, price)
        print(f"[{d}] {len(ts):,} trades -> {len(c)} 1m bars", flush=True)
        O = o if O is None else np.concatenate([O, o])
        H = h if H is None else np.concatenate([H, h]); L = l if L is None else np.concatenate([L, l])
        C = c if C is None else np.concatenate([C, c]); T = tm if T is None else np.concatenate([T, tm])
    return T, O, H, L, C


def resolve_dates(args):
    """Shared date resolution for every head builder: --validate DATE (smoke, no save) |
    --start/--end | --days N (last N full days to yesterday). Returns (dates, save)."""
    if getattr(args, "validate", None):
        return [args.validate], False
    if getattr(args, "start", None) and getattr(args, "end", None):
        return list(daterange(args.start, args.end)), True
    if getattr(args, "days", None):
        from datetime import datetime, timezone, timedelta
        end = datetime.now(timezone.utc).date() - timedelta(days=1)
        start = end - timedelta(days=args.days - 1)
        return list(daterange(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))), True
    return None, False


def _evaluate(clf, iso, Xte, yte):
    from sklearn.metrics import roc_auc_score
    p = iso.predict(clf.predict_proba(Xte)[:, 1])
    auc = roc_auc_score(yte, p) if len(np.unique(yte)) > 1 else 0.5
    acc = ((p >= 0.5).astype(int) == yte).mean()
    base = max(yte.mean(), 1 - yte.mean())          # majority-class baseline
    # calibration: high-confidence bin realized rate
    hi = p >= 0.6
    hi_real = yte[hi].mean() if hi.sum() >= 20 else None
    return auc, acc, base, (int(hi.sum()), hi_real)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start"); ap.add_argument("--end"); ap.add_argument("--validate")
    ap.add_argument("--days", type=int, help="last N days to yesterday (for start.bat)")
    args = ap.parse_args()
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.isotonic import IsotonicRegression
    import joblib

    dates, save = resolve_dates(args)
    if not dates:
        ap.error("provide --start/--end, --validate DATE, or --days N")

    T, O, H, L, C = _ohlc_for_dates(dates)
    if C is None or len(C) < 400:
        print("Not enough bars."); return
    X = build_beat_features(O, H, L, C, T)
    print(f"\nFeature matrix: {X.shape}; {len(C)} bars over {len(dates)} day(s)")

    models, passed = {}, []
    print(f"\n{'h':>3} {'n':>7} {'AUC':>6} {'acc':>6} {'base':>6} {'calib@.6':>14}  verdict")
    for h in HORIZONS:
        y = beat_labels(O, C, h)
        # ANTI-LEAKAGE: features[t] use close[t], but a window opening at bar t anchors on open[t]
        # (same bar) → close[t] leaks the label. Predict the window opening NEXT bar (t+1): its
        # anchor open[t+1]≈close[t] is known and its outcome is strictly future. (features[t] -> y[t+1])
        Xs, ys = X[:-1], y[1:]
        m = ys >= 0
        Xv, yv = Xs[m], ys[m]
        if len(yv) < 300 or len(np.unique(yv)) < 2:
            print(f"{h:>3} {len(yv):>7}  (insufficient)"); continue
        # TEMPORAL split (no shuffle — train past, test unseen future)
        n = len(yv); a, b = int(n * 0.6), int(n * 0.8)
        Xtr, ytr, Xca, yca, Xte, yte = Xv[:a], yv[:a], Xv[a:b], yv[a:b], Xv[b:], yv[b:]
        clf = HistGradientBoostingClassifier(max_iter=300, max_depth=4, learning_rate=0.05,
                                             l2_regularization=1.0, random_state=0)
        clf.fit(Xtr, ytr)
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.02, y_max=0.98)
        iso.fit(clf.predict_proba(Xca)[:, 1], yca)
        auc, acc, base, (hin, hireal) = _evaluate(clf, iso, Xte, yte)
        # §5bt: require a USABLE confident subset (>=20 calls realizing >=55%), not a vacuous
        # "None -> pass" — a 0.53-AUC model that almost never commits is noise, not a saveable head.
        ok = (auc >= NOISE_AUC) and (acc >= base - 0.005) and (hin >= 20 and (hireal or 0.0) >= 0.55)
        cal = f"{hireal*100:.0f}%({hin})" if hireal is not None else "thin"
        verdict = "SIGNAL" if ok else "** NOISE **"
        print(f"{h:>3} {len(yv):>7} {auc:>6.3f} {acc*100:>5.1f}% {base*100:>5.1f}% {cal:>14}  {verdict}")
        if ok:
            models[h] = {"clf": clf, "iso": iso, "features": FEATURE_NAMES}
            passed.append(h)

    print(f"\n{len(passed)}/{len(HORIZONS)} horizons cleared the noise gates: {passed}")
    if save and models:
        os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
        joblib.dump({"models": models, "features": FEATURE_NAMES, "horizons": passed}, OUT_PATH)
        print(f"Saved {OUT_PATH} ({len(passed)} horizons). Wire P(beat) into the Polymarket card next.")
    elif save:
        print("No horizon cleared the gates — NOT saved (would be noise). Need more data / new info.")
    else:
        print("[validate] not saved (smoke run).")


if __name__ == "__main__":
    main()
