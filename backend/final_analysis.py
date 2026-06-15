"""
final_analysis.py — THE capstone sweep for the price-to-beat use case (offline, 60d, leak-free).
=================================================================================================
Runs the FULL 14-model field (reused from model_bakeoff) on the FULL research matrix feature set
(derived + keeper + flow + the recorded-but-UNWIRED cross-venue features) against EVERY price-to-beat
sub-target, at 5m and 15m only. One table to see what is precisely predictable and what is a coin-flip.

Targets (line = close[t], the price to beat):
  * dir_beat   : close[t+h] >= line            -> "up/down" AND "beats or not" (identical when line=entry)
  * big_move   : |move| > TRAIN-median          -> timing / volatility expansion (the selectivity gate)
  * dwell_side : >50% of next h bars above line -> "more time up than down?"
  * committed  : one-sided >= 80% (vs chop)     -> does the line get decisively held/broken vs whipsawed
  * MAGNITUDE  : signed move quantiles q10/q50/q90 -> expected DROP / median / expected HIGH (+80% band)

Leak-free: features are all known at bar t; every target is built from FORWARD close. The matrix's
future_*/ret_5m/*_label columns are EXCLUDED from features (asserted). Classification reuses
model_bakeoff.run_horizon (temporal 60/20/20 + isotonic calibration + AUC/Brier/ECE).

Usage:  python backend/final_analysis.py            # 5m + 15m, all targets, all models
        python backend/final_analysis.py --selftest
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
HORIZONS = (5, 15)

# Curated leak-free feature set: engineered + flow + cross-venue (incl. recorded-but-UNWIRED).
# Deliberately NO raw OHLC price LEVELS (non-stationary) and NO future_*/label/meta columns.
FEATURES = [
    "volume", "trade_count", "taker_buy", "taker_sell",
    "rv_15m", "rv_30m", "rv_60m", "rv_term", "log_count", "log_vol", "count_accel_5m", "vol_accel",
    "vpin_15m", "vpin_30m", "vpin_50m", "compression_ratio", "range_15m", "shock_magnitude",
    "micro_range_15m",
    "cvd_change", "cvd_1m", "cvd_5m", "delta", "vpin", "large_trade_delta", "large_trade_imbalance",
    "funding_velocity",
    # --- recorded-but-UNWIRED cross-venue ---
    "cvd_spot", "cvd_perp", "cvd_divergence", "perp_spot_basis_bps", "vol_spot", "vol_perp",
]
_BANNED = ("future_", "ret_5m", "_label", "ts_ms", "timestamp")     # never allowed as a feature


def build_targets(close, h, train_frac=0.6):
    """All price-to-beat targets from forward close. -1 / nan tails. big_move threshold = TRAIN median."""
    n = len(close)
    fc = np.full(n, np.nan); fc[:n - h] = close[h:]
    move_bps = (fc - close) / np.where(close > 0, close, 1.0) * 1e4
    valid = np.isfinite(move_bps)
    dir_beat = np.full(n, -1); dir_beat[valid] = (move_bps[valid] >= 0).astype(int)
    abs_bps = np.abs(move_bps)
    # big_move: threshold = median of abs move over the TRAIN region only (leak-clean)
    tr_end = int(n * train_frac)
    tr_abs = abs_bps[:tr_end][np.isfinite(abs_bps[:tr_end])]
    thr = np.median(tr_abs) if len(tr_abs) else np.inf
    big = np.full(n, -1); big[valid] = (abs_bps[valid] > thr).astype(int)
    # dwell: fraction of next h bars strictly above the line
    above = np.zeros(n)
    for j in range(1, h + 1):
        cj = np.full(n, np.nan); cj[:n - j] = close[j:]
        above += np.where(np.isfinite(cj) & (cj > close), 1.0, 0.0)
    dwell_up = np.where(np.arange(n) < n - h, above / h, np.nan)
    dside = np.full(n, -1); dcommit = np.full(n, -1)
    dv = np.isfinite(dwell_up)
    dside[dv] = (dwell_up[dv] > 0.5).astype(int)
    dcommit[dv] = (np.maximum(dwell_up[dv], 1.0 - dwell_up[dv]) >= 0.8).astype(int)
    return {"dir_beat": dir_beat, "big_move": big, "dwell_side": dside, "committed": dcommit}, move_bps


def magnitude(X, move_bps, frac=0.7):
    """Quantile regression q10/q50/q90 (expected DROP / median / HIGH) + 80% band coverage + pinball."""
    from sklearn.ensemble import GradientBoostingRegressor
    m = np.isfinite(move_bps) & np.all(np.isfinite(X), axis=1)
    X, y = X[m], move_bps[m]
    n = len(y); cut = int(n * frac)
    if n < 500:
        return None
    Xtr, ytr, Xte, yte = X[:cut], y[:cut], X[cut:], y[cut:]
    q = {}
    for a in (0.1, 0.5, 0.9):
        g = GradientBoostingRegressor(loss="quantile", alpha=a, n_estimators=120, max_depth=3,
                                      learning_rate=0.05, subsample=0.7, random_state=0)
        g.fit(Xtr, ytr); q[a] = g.predict(Xte)
    def pinball(yt, p, a):
        d = yt - p
        return float(np.mean(np.maximum(a * d, (a - 1) * d)))
    base50 = np.full(len(yte), np.median(ytr))
    cover80 = float(np.mean((yte >= q[0.1]) & (yte <= q[0.9])))
    return {"n_test": n - cut, "pinball_model": pinball(yte, q[0.5], 0.5),
            "pinball_base": pinball(yte, base50, 0.5), "coverage80": cover80,
            "exp_drop_bps": float(np.mean(q[0.1])), "exp_high_bps": float(np.mean(q[0.9])),
            "realized_drop_bps": float(np.percentile(yte, 10)),
            "realized_high_bps": float(np.percentile(yte, 90))}


def run():
    import pandas as pd
    from model_bakeoff import make_light_models, run_horizon
    if not os.path.exists(MATRIX):
        sys.exit(f"missing {MATRIX} — build via build_research_matrix.py")
    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    feats = [c for c in FEATURES if c in df.columns]
    bad = [c for c in feats if any(b in c for b in _BANNED)]
    assert not bad, f"LEAKAGE: banned columns in features: {bad}"
    df = df.dropna(subset=feats + ["close"]).reset_index(drop=True)
    X = df[feats].to_numpy(np.float64)
    close = df["close"].to_numpy(np.float64)
    n_models = len(make_light_models())
    print(f"matrix {df.shape} | {len(feats)} leak-free features (incl. cross-venue: "
          f"cvd_divergence/perp_spot_basis_bps/funding_velocity/...) | {n_models} models")

    for h in HORIZONS:
        targets, move_bps = build_targets(close, h)
        print(f"\n{'='*78}\n  HORIZON {h}m  (60-day matrix, {len(close):,} bars)\n{'='*78}")
        for tname, y in targets.items():
            mask = y >= 0
            Xv, yv = X[mask], y[mask]
            if len(yv) < 400 or len(np.unique(yv)) < 2:
                print(f"\n  [{tname}] insufficient"); continue
            res = run_horizon(Xv, yv, make_light_models(), feats, calibrate=True)
            rows = [(k, v.get("auc")) for k, v in res.items() if isinstance(v, dict) and "auc" in v]
            rows.sort(key=lambda r: -(r[1] or 0))
            best = rows[0]
            sig = "SIGNAL" if (best[1] or 0) >= 0.55 else "all NOISE"
            base = next((v for k, v in res.items() if k == "majority"), {})
            print(f"\n  [{tname}]  base_rate={base.get('base_rate', 0):.2f}  -> {sig}  "
                  f"(best {best[0]} AUC {best[1]:.3f})")
            print(f"      {'model':<15}{'AUC':>7}   {'model':<15}{'AUC':>7}")
            for i in range(0, len(rows), 2):
                a = rows[i]; b = rows[i + 1] if i + 1 < len(rows) else ("", None)
                bs = f"{b[1]:.3f}" if b[1] is not None else ""
                print(f"      {a[0]:<15}{a[1]:>7.3f}   {b[0]:<15}{bs:>7}")
        mg = magnitude(X, move_bps)
        if mg:
            edge = "BEATS flat" if mg["pinball_model"] < mg["pinball_base"] else "no better than flat"
            print(f"\n  [MAGNITUDE expected drop/high]  pinball {mg['pinball_model']:.3f} vs flat "
                  f"{mg['pinball_base']:.3f} ({edge})")
            print(f"      80% band coverage : {mg['coverage80']*100:.1f}%  (target 80%)")
            print(f"      expected DROP (q10): {mg['exp_drop_bps']:+.1f} bps   realized p10 "
                  f"{mg['realized_drop_bps']:+.1f} bps")
            print(f"      expected HIGH (q90): {mg['exp_high_bps']:+.1f} bps   realized p90 "
                  f"{mg['realized_high_bps']:+.1f} bps")
    print("\nREAD: dir_beat / dwell_side ~0.50 = direction is a coin-flip (expected, proven). big_move")
    print(">=.55 = the real timing/selectivity edge. MAGNITUDE: a calibrated band (~80% coverage) is")
    print("REAL precision for 'expected drop/high'. Precision = predict the band + P(hold); abstain on side.")


def selftest():
    rng = np.random.default_rng(0)
    # build_targets correctness
    close = np.array([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110.0])
    t, mv = build_targets(close, 5)
    assert t["dir_beat"][0] == 1 and t["dwell_side"][0] == 1 and t["committed"][0] == 1
    assert abs(mv[0] - (105 - 100) / 100 * 1e4) < 1e-6 and t["dir_beat"][-1] == -1
    down = 110 - np.arange(11, dtype=float)
    td, _ = build_targets(down, 5)
    assert td["dir_beat"][0] == 0 and td["dwell_side"][0] == 0
    # leakage guard: no banned col slips into FEATURES
    assert not [c for c in FEATURES if any(b in c for b in _BANNED)]
    # magnitude: planted feature -> move; q10<q50<q90 and coverage sane
    N = 2000
    f = rng.normal(0, 1, (N, 3))
    move = 20 * f[:, 0] + rng.normal(0, 10, N)
    mg = magnitude(f, move)
    assert mg and mg["exp_drop_bps"] < mg["exp_high_bps"], f"q10<q90 expected, got {mg}"
    assert 0.6 <= mg["coverage80"] <= 0.95, f"coverage off: {mg['coverage80']}"
    assert mg["pinball_model"] < mg["pinball_base"], "should beat flat on a learnable signal"
    print("final_analysis self-test: ALL PASS")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    selftest() if a.selftest else run()
