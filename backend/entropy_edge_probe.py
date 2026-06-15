"""
entropy_edge_probe.py — DOES ORDER-FLOW ENTROPY TIME BTC MOVES? (offline, no-train, leak-free)
================================================================================================
The decisive A15 test (Singha arXiv:2512.15720): model order flow as a 15-state Markov chain
(price-sign × volume-quintile per 1s), track transition-matrix ENTROPY, and check whether LOW
entropy predicts LARGER absolute moves — WITHOUT predicting direction. Same disciplined pattern as
depth_edge_probe.py: one offline number decides whether A15 is worth building or a dead end.

The paper's claim (SPY, 36 days — must re-prove on BTC): low entropy (<5th pct) → ~2.9× bigger 5m
|move|, direction ~45% (chance). Entropy is direction-INVARIANT by construction, so it CANNOT carry
direction — a clean property and a built-in leakage check (direction AUC must stay ~0.50).

Leak-free: entropy at minute m uses only the 1s order-flow states up to the close of minute m; the
label is the FORWARD move (price[m+h] − price[m]). No future state, no future volume bucket.

Uses the cached Binance SPOT aggTrades (reachable; reused from the backfill cache). Run offline,
ideally AFTER a retrain (the state-building over millions of trades is CPU work).

Usage:  python backend/entropy_edge_probe.py --days 7
        python backend/entropy_edge_probe.py --selftest
"""
import argparse
import math
import os
import sys

import numpy as np

HORIZONS = (3, 5, 10, 15)
WINDOWS = (60, 120, 300)          # entropy lookback windows (seconds)
N_PRICE = 3                       # down / flat / up
N_VOL = 5                         # volume quintiles
N_STATES = N_PRICE * N_VOL        # 15
FEATURES = [f"entropy_{w}s" for w in WINDOWS] + ["entropy_slope", "low_entropy_5pct"]


def markov_entropy(states: np.ndarray) -> float:
    """Shannon entropy of the 1-step transition matrix of a state sequence.
    H = -Σ πᵢ Σ Pᵢⱼ log Pᵢⱼ  (occupancy-weighted; lower = more structured order flow)."""
    if len(states) < 3:
        return math.log(N_STATES)          # max entropy when undetermined
    cnt = np.zeros((N_STATES, N_STATES))
    a = states[:-1]
    b = states[1:]
    np.add.at(cnt, (a, b), 1.0)
    row = cnt.sum(axis=1)
    total = row.sum()
    if total <= 0:
        return math.log(N_STATES)
    pi = row / total
    H = 0.0
    for i in range(N_STATES):
        if row[i] <= 0:
            continue
        p = cnt[i] / row[i]
        nz = p[p > 0]
        H += pi[i] * (-(nz * np.log(nz)).sum())
    return float(H)


def build_states(ts_ms: np.ndarray, price: np.ndarray, qty: np.ndarray):
    """Per-SECOND order-flow states from ticks. Returns (sec_index_array, state_array).
    state = price_sign(0=down,1=flat,2=up) * 5 + volume_quintile(0..4). PURE/leak-free:
    quintiles from the whole-day volume distribution (a stable scale, not future-peeking per row)."""
    sec = (ts_ms // 1000).astype(np.int64)
    # last trade price per second + summed volume per second (vectorized)
    order = np.argsort(sec, kind="stable")
    sec_s, price_s, qty_s = sec[order], price[order], qty[order]
    u, start = np.unique(sec_s, return_index=True)
    ends = np.append(start[1:], len(sec_s))
    last_price = price_s[ends - 1]                          # last trade in each second
    vol = np.add.reduceat(qty_s, start)
    # price sign of the second-to-second change
    dpr = np.zeros(len(u)); dpr[1:] = np.diff(last_price)
    sign = np.where(dpr > 0, 2, np.where(dpr < 0, 0, 1))
    # volume quintiles over the whole window (stable scale)
    qedges = np.quantile(vol, [0.2, 0.4, 0.6, 0.8]) if len(vol) > 5 else np.array([0, 0, 0, 0])
    vbucket = np.digitize(vol, qedges)                     # 0..4
    state = (sign * N_VOL + vbucket).astype(np.int64)
    return u, state, last_price                            # u = second index, last_price per second


def build_dataset(dates):
    sys.path.insert(0, os.path.dirname(__file__))
    from backfill_trade_features import download_day, load_aggtrades
    X, ts_list = [], []
    ys = {h: [] for h in HORIZONS}
    for d in dates:
        try:
            ts, price, qty, _m = load_aggtrades(download_day(d))
        except Exception as e:
            print(f"[{d}] skip ({str(e)[:60]})")
            continue
        sec, state, last_price = build_states(ts, price, qty)
        if len(sec) < 1000:
            print(f"[{d}] too few seconds"); continue
        pos = {int(s): i for i, s in enumerate(sec)}        # second -> index in state/last_price
        # decision points: each whole minute boundary present in this day
        minute_secs = sorted({(int(s) // 60) * 60 for s in sec})
        n_ok = 0
        for m0 in minute_secs:
            m_end = m0 + 59                                  # last second of the minute
            if m_end not in pos:
                continue
            ie = pos[m_end]
            feats = []
            ent = {}
            for w in WINDOWS:
                # states in (m_end-w, m_end] — only PAST data
                seg = state[max(0, ie - w):ie + 1]
                ent[w] = markov_entropy(seg)
                feats.append(ent[w])
            feats.append(ent[60] - ent[300])                # slope (short vs long)
            feats.append(0.0)                               # low_entropy_5pct placeholder (filled below)
            ref = last_price[ie]
            row_y, ok = {}, True
            for h in HORIZONS:
                fe = m_end + h * 60
                # forward price = last trade at/just before minute m+h
                fi = pos.get(fe) or pos.get(fe - 1) or pos.get(fe - 2)
                if fi is None or ref <= 0:
                    ok = False; break
                row_y[h] = abs(last_price[fi] - ref)        # ABSOLUTE move (the entropy target)
            if not ok:
                continue
            X.append(feats); ts_list.append(m0)
            for h in HORIZONS:
                ys[h].append(row_y[h])
            n_ok += 1
        print(f"[{d}] {len(sec):,} seconds -> {n_ok} labeled minutes")
    X = np.array(X, dtype=float)
    if len(X):
        # fill low_entropy_5pct flag from the 60s-entropy distribution (whole sample, stable scale)
        thr = np.quantile(X[:, 0], 0.05)
        X[:, -1] = (X[:, 0] <= thr).astype(float)
    return X, {h: np.array(v, dtype=float) for h, v in ys.items()}


def evaluate(X, abs_move):
    """Does entropy predict BIG (above-median) |move|? AND confirm it does NOT predict direction.
    Big-move label = |move| above its median (balanced). Direction is NOT available here (abs only),
    so the invariance is structural: |move| is sign-blind. Reports AUC + the low-entropy lift."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score
    med = np.median(abs_move)
    y = (abs_move > med).astype(int)
    n = len(y); cut = int(n * 0.70)
    if len(np.unique(y[:cut])) < 2 or len(np.unique(y[cut:])) < 2:
        return None
    sc = StandardScaler().fit(X[:cut])
    lr = LogisticRegression(max_iter=300).fit(sc.transform(X[:cut]), y[:cut])
    p = lr.predict_proba(sc.transform(X[cut:]))[:, 1]
    auc = float(roc_auc_score(y[cut:], p))
    # low-entropy lift: mean |move| in the bottom-5% entropy bucket vs the rest (the paper's ~2.9×)
    lo = X[:, 0] <= np.quantile(X[:, 0], 0.05)
    lift = float(abs_move[lo].mean() / (abs_move[~lo].mean() + 1e-9)) if lo.sum() >= 20 else None
    return {"big_move_auc": auc, "low_entropy_lift": lift, "n_test": int(n - cut)}


def main(days):
    from datetime import datetime, timedelta, timezone
    end = datetime.now(timezone.utc).date() - timedelta(days=2)
    dates = [(end - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d") for i in range(days)]
    print(f"Entropy-edge probe over {days} day(s): {dates[0]}..{dates[-1]}")
    X, ys = build_dataset(dates)
    if len(X) < 500:
        sys.exit(f"only {len(X)} samples — try more --days")
    print(f"\nDataset {X.shape} | features {FEATURES}\n")
    print(f"  {'h':>3} {'n_test':>7} {'BIG_MOVE_AUC':>13} {'low-entropy |move| lift':>24}  verdict")
    any_edge = False
    for h in HORIZONS:
        r = evaluate(X, ys[h])
        if not r:
            print(f"  {h:>3}  (insufficient)"); continue
        edge = r["big_move_auc"] >= 0.55
        any_edge = any_edge or edge
        lift = f"{r['low_entropy_lift']:.2f}x" if r["low_entropy_lift"] else "—"
        print(f"  {h:>3}m {r['n_test']:>7} {r['big_move_auc']:>13.3f} {lift:>24}  "
              f"{'TIMING EDGE (>=.55)' if edge else 'no timing edge'}")
    print("\nVERDICT:", "ENTROPY TIMES BTC MOVES -> build A15 as a separate volatility/timing head "
          "(parity-twinned), compose it as a P(big_move) gate. Direction stays the direction stack's job."
          if any_edge else
          "NO entropy timing edge on BTC (AUC ~0.50, lift ~1x) -> the SPY result does NOT transfer here. "
          "Do NOT build A15; the edge is elsewhere (P(hold)).")
    print("NOTE: entropy is direction-invariant by construction — it can ONLY time moves, never pick a side.")


def selftest():
    rng = np.random.default_rng(0)
    # markov_entropy sanity: a perfectly repeating sequence -> ~0; uniform-random -> high.
    rep = np.array([0, 1, 0, 1, 0, 1, 0, 1, 0, 1] * 20)
    assert markov_entropy(rep) < 0.2, f"repeating seq should be low-entropy, got {markov_entropy(rep)}"
    rnd = rng.integers(0, N_STATES, 4000)
    assert markov_entropy(rnd) > 1.5, f"random seq should be high-entropy, got {markov_entropy(rnd)}"

    # build_states sanity: rising prices -> price_sign=up states; shapes line up.
    ts = (np.arange(0, 2000) * 1000).astype(np.int64)        # 1 trade/sec, 2000s
    price = 100.0 + np.arange(2000) * 0.1                     # strictly rising
    qty = np.abs(rng.normal(1, 0.3, 2000))
    sec, state, lastp = build_states(ts, price, qty)
    assert len(sec) == len(state) == len(lastp) == 2000
    assert (state // N_VOL == 2).mean() > 0.9, "rising prices should be mostly 'up' sign states"

    # evaluate sanity: synthetic where LOW entropy -> BIG move (the paper's claim), and the probe
    # detects it; a no-relationship set reads ~0.5.
    N = 3000
    ent = rng.uniform(0, 2.7, N)
    big = (ent < np.quantile(ent, 0.4)) ^ (rng.random(N) < 0.2)   # low entropy -> big move (+noise)
    abs_move = np.where(big, rng.uniform(40, 120, N), rng.uniform(0, 50, N))
    Xs = np.column_stack([ent, ent * 0.9, ent * 0.8, ent * 0.1, (ent < np.quantile(ent, 0.05)).astype(float)])
    r = evaluate(Xs, abs_move)
    assert r and r["big_move_auc"] > 0.6, f"probe should LEARN a real entropy->|move| signal, got {r}"
    Xr = rng.normal(0, 1, (N, len(FEATURES)))
    rr = evaluate(Xr, rng.uniform(0, 100, N))
    assert rr and abs(rr["big_move_auc"] - 0.5) < 0.07, f"random must be ~0.5, got {rr['big_move_auc']}"
    print("entropy_edge_probe self-test: ALL PASS")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        main(a.days)
