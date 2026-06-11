"""PRE-FLIGHT: miniature end-to-end train -> save -> load -> predict on the NEW 126-feature
pipeline, in an ISOLATED temp data dir (real DB/models untouched). If this passes, the real
retrain exercises the same code paths with just more data."""
import os, sys, tempfile, time, traceback

TMP = tempfile.mkdtemp(prefix="btc_preflight_")
os.environ["BTC_DATA_DIR"] = TMP
# Speed caps so the mini-train finishes in minutes, exercising the same code.
os.environ["BTC_SGD_MAX_ITER"] = "100"
os.environ["BTC_STACKER_MAX_SAMPLES"] = "400"
os.environ["BTC_QUANTILE_MAX_SAMPLES"] = "400"
os.environ["BTC_MOVE_SIZE_MAX_SAMPLES"] = "400"
os.environ["BTC_LINEAR_MAX_SAMPLES"] = "600"
os.environ["BTC_TRAIN_THREADS"] = "4"
os.environ["OMP_NUM_THREADS"] = "4"

sys.path.insert(0, "backend")
import numpy as np

print(f"[1/6] building synthetic market ({TMP})", flush=True)
import features as F
np.random.seed(11)
n = 900
p = 63000.0
ks = []
t0 = 1780790400  # seconds, spans multiple UTC days
for i in range(n):
    drift = 8 * np.sin(i / 60.0)
    r = np.random.randn() * 16 + drift
    o = p; c = p + r
    h_ = max(o, c) + abs(np.random.randn() * 6); l_ = min(o, c) - abs(np.random.randn() * 6)
    v = abs(np.random.randn() * 30) + 5
    ks.append({"time": t0 + i * 60, "open": o, "high": h_, "low": l_, "close": c, "volume": v})
    p = c

sig = {
    "cvd_1m": np.cumsum(np.random.randn(n)) * 0.5,
    "cvd_5m": np.cumsum(np.random.randn(n)) * 0.7,
    "cvd_change": np.random.randn(n) * 0.3,
    "oi_change": np.cumsum(np.random.randn(n)) * 0.02,
    "funding_rate": 0.0001 + np.random.randn(n) * 1e-5,
    "liq_imbalance": np.random.randn(n) * 1e5,
    "vpin": np.clip(np.abs(np.random.randn(n)) * 0.2, 0, 1),
    "large_trade_delta": np.clip(np.random.randn(n) * 0.3, -1, 1),
    "large_trade_imbalance": np.clip(np.random.randn(n) * 0.4, -1, 1),
    "imbalance": np.clip(np.random.randn(n) * 0.2, -1, 1),
    "obi_5": np.clip(np.random.randn(n) * 0.2, -1, 1),
}
X2 = F.build_features_from_klines(ks, signal_history=sig)
assert X2.shape == (n - 1, 126), X2.shape
assert not np.isnan(X2).any() and not np.isinf(X2).any()
closes = np.array([k["close"] for k in ks]); highs = np.array([k["high"] for k in ks]); lows = np.array([k["low"] for k in ks])
Xs, Y, Ymag = F.build_sequences(X2, closes, lookback=60, horizons=[1, 3, 5, 7, 10, 15],
                                atr_arr=F.atr(highs, lows, closes), highs=highs, lows=lows,
                                return_magnitude=True)
print(f"   sequences: {Xs.shape}  labels: {[ (h, Y[h].shape) for h in sorted(Y) ]}", flush=True)

print("[2/6] importing model (torch load ~25s)", flush=True)
import model as M
print("   arch:", M.MODEL_ARCH_VERSION, flush=True)

print("[3/6] FULL ensemble train on 126 features (mini, all 6 horizons)...", flush=True)
ens = M.MultiModelEnsemble()
t = time.time()
ens.train(Xs, Y, Ymag)
print(f"   train OK in {time.time()-t:.1f}s | is_trained={ens.is_trained}", flush=True)
assert ens.is_trained

print("[4/6] save -> load round trip (new schema)...", flush=True)
ens.save_models()
ens2 = M.MultiModelEnsemble()
loaded = ens2.load_models()
print(f"   load_models() -> {loaded}", flush=True)
assert loaded, "saved 126-feature bundle failed to reload!"

print("[5/6] inference: generate_ensemble_prediction on 1m/5m/15m...", flush=True)
data_state = {
    "klines": ks,
    "order_flow": {"cvd_1m": 2.0, "imbalance": 0.1, "spread_expansion_ratio": 1.0},
    "derivatives": {}, "sentiment": {},
    "regime_info": {"regime": "RANGE", "confidence_vector": {"RANGE": 0.7, "TREND": 0.2}},
    "kronos_accuracy": {},
}
seq = Xs[-1:]
ok_dirs = []
for h in (1, 5, 15):
    pr = ens2.generate_ensemble_prediction(h, seq, data_state)
    pu, pd_, pn = pr.get("probUp"), pr.get("probDown"), pr.get("probNeutral")
    print(f"   {h:>2}m dir={pr.get('direction'):>7} probs=({pu:.3f},{pn:.3f},{pd_:.3f}) "
          f"conf={pr.get('confidence'):.3f} conviction={pr.get('conviction')} "
          f"kronosProven={pr.get('kronosProven')}", flush=True)
    assert pr.get("direction") in ("UP", "DOWN", "NEUTRAL")
    s = (pu or 0) + (pd_ or 0) + (pn or 0)
    assert 0.95 < s < 1.05, f"probs don't sum to 1: {s}"
    assert not (pu == 0 and pd_ == 0), "NEUTRAL-collapse signature (0/0 probs)!"
    ok_dirs.append(pr.get("direction"))

print("[6/6] cleanup", flush=True)
import shutil
del ens, ens2
shutil.rmtree(TMP, ignore_errors=True)
print("\nPRE-FLIGHT: PASS — train/save/load/predict all work on the 126-feature pipeline.")
