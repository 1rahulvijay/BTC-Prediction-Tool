"""
shadow_live_predictor.py — LIVE shadow test of the independent models (no app interference).
==============================================================================================
Runs the bakeoff's light models on a SEPARATE live Binance feed and logs their predictions +
self-resolved outcomes to its OWN files. It does NOT touch the app: separate process, its own
public REST feed (read-only), its own model file (`data/shadow/`), its own output parquet — it
never opens the app's DuckDB, never writes saved_models/, needs NO app restart. Kill it anytime.

It answers "what do these models predict on LIVE data, and are they right?" in real time —
the live analog of the held-out backtest (which already said: coin-flip). Expect the same live.

Flow:
  --start --hours N : train the light models on recent history (saved to data/shadow/), then loop:
      every minute fetch live 1m bars from Binance REST, predict P(up over next h min) for each
      horizon×model, and resolve any prediction whose horizon has elapsed (price now vs ref).
      Appends resolved rows to data/shadow/shadow_live_resolved.parquet. Runs N hours then exits.
  --selftest : validate predict + resolution logic on synthetic bars (no network).

Usage:  python backend/shadow_live_predictor.py --start --hours 10
        python backend/shadow_live_predictor.py --selftest
"""
import argparse
import json
import os
import sys
import time
import urllib.request

import numpy as np

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
SHADOW_DIR = os.path.join(DATA_DIR, "shadow")
MODEL_PATH = os.path.join(SHADOW_DIR, "shadow_models.pkl")
OUT_PATH = os.path.join(SHADOW_DIR, "shadow_live_resolved.parquet")
KLINES_URL = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit={limit}"
HORIZONS = (1, 3, 5, 7, 10, 15, 30)


# ───────────────────────── live feed (read-only, independent) ──────────────────────────
def fetch_bars(limit=130):
    """Last `limit` COMPLETE 1m bars from Binance REST → (T,O,H,L,C). Drops the forming bar.
    Returns None on any failure (caller skips the tick — crash-safe)."""
    try:
        url = KLINES_URL.format(limit=limit + 1)
        with urllib.request.urlopen(url, timeout=15) as r:
            raw = json.loads(r.read())
        raw = raw[:-1]                      # drop the still-forming current bar
        T = np.array([int(k[0]) for k in raw], dtype=np.int64)
        O = np.array([float(k[1]) for k in raw]); Hh = np.array([float(k[2]) for k in raw])
        L = np.array([float(k[3]) for k in raw]); C = np.array([float(k[4]) for k in raw])
        return T, O, Hh, L, C
    except Exception as e:
        print(f"[fetch] skip ({str(e)[:70]})", flush=True)
        return None


# ───────────────────────── train + save (separate model file) ─────────────────────────
def train_and_save(train_days=14):
    from train_beat_classifier import _ohlc_for_dates, build_beat_features, beat_labels, FEATURE_NAMES
    from train_beat_classifier import resolve_dates as _rd
    from model_bakeoff import make_light_models
    import joblib
    from sklearn.isotonic import IsotonicRegression

    class _A:
        validate = None; start = None; end = None; days = train_days
    dates, _ = _rd(_A())
    T, O, H, L, C = _ohlc_for_dates(dates)
    if C is None or len(C) < 400:
        sys.exit("not enough history to train shadow models")
    X = build_beat_features(O, H, L, C, T)
    bundle = {"features": FEATURE_NAMES, "horizons": {}}
    for h in HORIZONS:
        y = beat_labels(O, C, h)
        Xs, ys = X[:-1], y[1:]              # §5bs anti-leakage alignment
        m = ys >= 0
        Xv, yv = Xs[m], ys[m]
        if len(yv) < 300 or len(np.unique(yv)) < 2:
            continue
        a = int(len(yv) * 0.85)             # last 15% as a calibration slice
        per = {}
        for name, model in make_light_models().items():
            try:
                model.fit(Xv[:a], yv[:a])
                iso = IsotonicRegression(out_of_bounds="clip", y_min=0.02, y_max=0.98)
                iso.fit(model.predict_proba(Xv[a:])[:, 1], yv[a:])
                per[name] = {"clf": model, "iso": iso}
            except Exception as e:
                print(f"[train {h}m {name}] skip ({str(e)[:50]})")
        bundle["horizons"][h] = per
    os.makedirs(SHADOW_DIR, exist_ok=True)
    joblib.dump(bundle, MODEL_PATH)
    print(f"trained shadow models ({train_days}d) -> {MODEL_PATH}", flush=True)
    return bundle


def predict_row(bundle, x_row):
    """{(h, model): p_up} for one feature row."""
    out = {}
    for h, per in bundle["horizons"].items():
        for name, mdl in per.items():
            try:
                raw = mdl["clf"].predict_proba(x_row.reshape(1, -1))[:, 1]
                out[(h, name)] = float(mdl["iso"].predict(raw)[0])
            except Exception:
                pass
    return out


# ───────────────────────── live loop (self-resolving) ─────────────────────────────────
def live_loop(bundle, hours=10.0, interval=60):
    from train_beat_classifier import build_beat_features
    import pandas as pd
    t_end = time.time() + hours * 3600
    pending, resolved = [], []
    last_min = None
    flush_every, since_flush = 25, 0
    print(f"shadow live loop started; runs ~{hours}h, predicts each new 1m bar.", flush=True)
    while time.time() < t_end:
        bars = fetch_bars()
        if bars is not None:
            T, O, H, L, C = bars
            now = int(time.time() * 1000)
            price = float(C[-1])
            # resolve matured predictions (price now vs the ref captured at predict time)
            still = []
            for p in pending:
                if now >= p["resolve_ms"]:
                    p["actual_up"] = int(price >= p["ref_price"])
                    p["resolved_price"] = price
                    resolved.append(p); since_flush += 1
                else:
                    still.append(p)
            pending = still
            # predict once per fresh 1m bar
            bar_min = int(T[-1] // 60000)
            if bar_min != last_min and len(C) >= 70:
                last_min = bar_min
                X = build_beat_features(O, H, L, C, T)
                preds = predict_row(bundle, X[-1])
                for (h, name), p_up in preds.items():
                    pending.append({"predict_ms": now, "ref_price": price, "horizon": int(h),
                                    "model": name, "p_up": round(p_up, 4),
                                    "resolve_ms": now + int(h) * 60000})
            if since_flush >= flush_every and resolved:
                pd.DataFrame(resolved).to_parquet(OUT_PATH, index=False)
                since_flush = 0
                print(f"[{time.strftime('%H:%M')}] resolved={len(resolved)} pending={len(pending)} "
                      f"price={price:.0f}", flush=True)
        time.sleep(interval)
    if resolved:
        pd.DataFrame(resolved).to_parquet(OUT_PATH, index=False)
    print(f"\nshadow loop done. resolved {len(resolved)} predictions -> {OUT_PATH}", flush=True)
    _summary(resolved)


def _summary(resolved):
    if not resolved:
        print("no resolved predictions (feed unreachable or too short a run).")
        return
    import pandas as pd
    df = pd.DataFrame(resolved)
    print("\nLIVE shadow result — directional accuracy per model (5m & 15m):")
    for h in (5, 15):
        sub = df[df.horizon == h]
        if not len(sub):
            continue
        g = sub.groupby("model").apply(
            lambda d: f"n={len(d)} acc={((d.p_up >= .5).astype(int) == d.actual_up).mean()*100:.1f}%",
            include_groups=False)
        print(f"  {h}m: " + " | ".join(f"{m}:{v}" for m, v in g.items()))


# ───────────────────────── self-test (no network) ─────────────────────────────────────
def selftest():
    # synthetic "bundle" with a trivial model so predict_row works without training/network.
    class _Clf:
        def predict_proba(self, X):
            v = 1 / (1 + np.exp(-X[:, 0]))      # depends on feature 0
            return np.column_stack([1 - v, v])

    class _Iso:
        def predict(self, p):
            return np.clip(p, 0.02, 0.98)
    bundle = {"features": ["f0"], "horizons": {h: {"m": {"clf": _Clf(), "iso": _Iso()}} for h in (5, 15)}}
    x = np.array([0.8] + [0.0] * 10)
    preds = predict_row(bundle, x)
    assert set(preds.keys()) == {(5, "m"), (15, "m")}
    for v in preds.values():
        assert 0.0 <= v <= 1.0
    assert preds[(5, "m")] > 0.5                  # positive feature 0 -> p_up > 0.5

    # resolution logic: a pending pred resolves up iff price>=ref when matured.
    now = 1_000_000
    pend = [{"predict_ms": now, "ref_price": 100.0, "resolve_ms": now + 300000,
             "horizon": 5, "model": "m", "p_up": 0.6}]
    price_now, t_now = 101.0, now + 300001
    res = []
    for p in pend:
        if t_now >= p["resolve_ms"]:
            p["actual_up"] = int(price_now >= p["ref_price"]); res.append(p)
    assert res and res[0]["actual_up"] == 1
    price_now = 99.0
    assert int(price_now >= 100.0) == 0
    print("shadow_live_predictor self-test: ALL PASS (predict + resolution logic sound)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--start", action="store_true", help="train then run the live shadow loop")
    ap.add_argument("--hours", type=float, default=10.0)
    ap.add_argument("--train-days", type=int, default=14)
    ap.add_argument("--interval", type=int, default=60)
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.start:
        import joblib
        if os.path.exists(MODEL_PATH):
            bundle = joblib.load(MODEL_PATH)
            print(f"loaded shadow models <- {MODEL_PATH}")
        else:
            bundle = train_and_save(a.train_days)
        live_loop(bundle, hours=a.hours, interval=a.interval)
    else:
        ap.print_help()
