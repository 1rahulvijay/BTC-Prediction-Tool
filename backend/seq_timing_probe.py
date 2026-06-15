"""
seq_timing_probe.py — do SEQUENTIAL models beat the TABULAR P(big_move) baseline? (offline)
============================================================================================
Question (operator-requested): on our STRONG timing features (the validated volatility keepers),
does a sequence model (LSTM / GRU / Transformer / TCN) that sees the *temporal pattern* beat the
tabular tree/logistic baseline (~0.67 AUC) at the 5m and 15m horizons? If the rolling features
already summarize the sequence, the deep models will only MATCH (and risk overfit).

HONEST CONTEXT — this is the SELECTIVITY gate, not a money-maker. The timing edge (AUC ~0.67) is
REAL but NOT directionally tradeable: the cost-survival test (probe_expected_move_cost_gate) showed
-21.63 bps net EV because P(big_move) tells you WHEN, not WHICH WAY, and direction is dead. A better
timing AUC here improves *selectivity* only; it does not create a directional edge.

Reuses the app's architectures from seq_model_feasibility (parity with live TCN/LSTM). Loads the
already-built data/research_matrix_1m.parquet (no tick reprocessing). Binary label big_move =
|future move over h| > TRAIN-median (train-only threshold — fixes the regime base-rate inflation).
Leak-free: features end at bar t (past L bars); label is the forward move; purged embargo of h bars.

Usage:  python backend/seq_timing_probe.py                 # 5m + 15m
        python backend/seq_timing_probe.py --selftest
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from seq_model_feasibility import _build_models, purged_temporal_split  # reuse app architectures

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
HORIZONS = (5, 15)
LOOKBACK = 30                     # 30 min of context — vol features are slow; plenty
# the validated volatility/timing keepers (NOT the directional basis/cvd — this is the WHEN gate)
STRONG = ["rv_15m", "rv_30m", "rv_60m", "log_count", "compression_ratio", "shock_magnitude",
          "vpin_15m", "vpin"]


def make_sequences(feat, lookback):
    """(T, F) -> (T-lookback+1, lookback, F): each row = the trailing `lookback` bars ending at t."""
    n = len(feat)
    idx = np.arange(lookback - 1, n)
    seqs = np.stack([feat[i - lookback + 1:i + 1] for i in idx]).astype(np.float32)
    return seqs, idx           # idx[k] = the bar index the k-th sequence ENDS on


def big_move_label(close, h, train_end):
    """big_move[t] = |close[t+h] - close[t]| / close[t] > TRAIN-median. -1 tail. Train-median only."""
    n = len(close); raw = np.full(n, -1.0)
    end = n - h
    if end > 0:
        raw[:end] = np.abs(close[h:h + end] - close[:end]) / np.where(close[:end] > 0, close[:end], 1.0)
    med = np.median(raw[:train_end][raw[:train_end] >= 0])     # threshold from TRAIN region only
    y = np.full(n, -1)
    valid = raw >= 0
    y[valid] = (raw[valid] > med).astype(int)
    return y


def _fit_eval_auc(model, Xtr, ytr, Xte, yte, epochs=12, device="cpu"):
    """Train 2-class CrossEntropy on sequences; return test AUC (softmax prob of class 1)."""
    import torch
    import torch.nn as nn
    from sklearn.metrics import roc_auc_score
    model = model.to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    lossf = nn.CrossEntropyLoss()
    Xtr_t = torch.tensor(Xtr, dtype=torch.float32, device=device)
    ytr_t = torch.tensor(ytr, dtype=torch.long, device=device)
    Xte_t = torch.tensor(Xte, dtype=torch.float32, device=device)
    bs = 1024
    model.train()
    first = last = None
    for ep in range(epochs):
        perm = torch.randperm(len(Xtr_t), device=device)
        tot = 0.0
        for i in range(0, len(perm), bs):
            b = perm[i:i + bs]
            opt.zero_grad()
            loss = lossf(model(Xtr_t[b]), ytr_t[b])
            loss.backward(); opt.step()
            tot += float(loss.detach()) * len(b)
        tot /= len(Xtr_t)
        first = tot if ep == 0 else first
        last = tot
    model.eval()
    with torch.no_grad():
        logits = model(Xte_t).cpu().numpy()
    p1 = np.exp(logits - logits.max(1, keepdims=True))
    p1 = p1[:, 1] / p1.sum(1)
    return float(roc_auc_score(yte, p1)), first, last


def _tabular_auc(Xtr_last, ytr, Xte_last, yte):
    """Tabular baseline on the LAST timestep only (the ~0.67 reference: RF + logistic)."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score
    rf = RandomForestClassifier(n_estimators=200, max_depth=6,
                                n_jobs=int(os.environ.get("OMP_NUM_THREADS", "2")), random_state=0)
    rf.fit(Xtr_last, ytr)
    rf_auc = roc_auc_score(yte, rf.predict_proba(Xte_last)[:, 1])
    sc = StandardScaler().fit(Xtr_last)
    lr = LogisticRegression(max_iter=300).fit(sc.transform(Xtr_last), ytr)
    lr_auc = roc_auc_score(yte, lr.predict_proba(sc.transform(Xte_last))[:, 1])
    return rf_auc, lr_auc


def run():
    import pandas as pd
    import torch
    if not os.path.exists(MATRIX):
        sys.exit(f"missing {MATRIX} — build it via build_research_matrix.py first.")
    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    have = [c for c in STRONG if c in df.columns]
    if "close" not in df.columns or len(have) < 4:
        sys.exit(f"matrix missing close or strong features (have {have})")
    df = df.dropna(subset=have + ["close"]).reset_index(drop=True)
    feat = df[have].to_numpy(np.float32)
    # standardize features globally (fit later per-split would be cleaner; vol scale is ~stationary)
    mu, sd = feat.mean(0), feat.std(0) + 1e-9
    feat = (feat - mu) / sd
    close = df["close"].to_numpy(np.float64)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"matrix {df.shape} | features {have} | lookback {LOOKBACK} | device {device}")

    seqs, idx = make_sequences(feat, LOOKBACK)
    for h in HORIZONS:
        tr_raw, te_raw = purged_temporal_split(len(seqs), max_h=h, train_frac=0.7)
        # label uses the ENDING bar index of each sequence; train-median from the train region
        train_end_bar = int(idx[tr_raw[-1]]) if len(tr_raw) else len(close)
        y_full = big_move_label(close, h, train_end_bar)
        y = y_full[idx]                          # align labels to sequence end-bars
        keep = y >= 0
        tr = tr_raw[keep[tr_raw]]; te = te_raw[keep[te_raw]]
        if len(tr) < 500 or len(te) < 200 or len(np.unique(y[tr])) < 2:
            print(f"\n[{h}m] insufficient"); continue
        Xtr, ytr = seqs[tr], y[tr]
        Xte, yte = seqs[te], y[te]
        rf_auc, lr_auc = _tabular_auc(Xtr[:, -1, :], ytr, Xte[:, -1, :], yte)
        print(f"\n[{h}m] train={len(tr)} test={len(te)} base_rate={yte.mean():.2f}")
        print(f"  {'model':<14}{'AUC':>8}{'vs_tabular':>12}   loss")
        print(f"  {'tabular_rf':<14}{rf_auc:>8.3f}{'(baseline)':>12}")
        print(f"  {'tabular_logit':<14}{lr_auc:>8.3f}{'':>12}")
        base = max(rf_auc, lr_auc)
        for name, model in _build_models(seqs.shape[2], LOOKBACK, num_classes=2).items():
            try:
                auc, f0, f1 = _fit_eval_auc(model, Xtr, ytr, Xte, yte, device=device)
                tag = f"{auc - base:+.3f}" + ("  BEATS" if auc - base >= 0.01 else "")
                print(f"  {name:<14}{auc:>8.3f}{tag:>12}   {f0:.3f}->{f1:.3f}")
            except Exception as e:
                print(f"  {name:<14} ERROR {str(e)[:50]}")
    print("\nVERDICT GUIDE: a sequence model is worth it ONLY if it BEATS the tabular baseline by")
    print(">=0.01 AUC at 5m/15m. If it merely matches (~0.67), the rolling features already capture")
    print("the temporal info -> stay tabular. Either way this is the SELECTIVITY gate, NOT a")
    print("directional edge: better timing AUC does not survive the cost-survival test (-21.63 bps).")


def selftest():
    try:
        import torch
    except Exception as e:
        sys.exit(f"PyTorch required: {e}")
    torch.manual_seed(0); torch.set_num_threads(1); np.random.seed(0)
    # make_sequences shape + alignment
    feat = np.arange(20 * 3).reshape(20, 3).astype(np.float32)
    s, idx = make_sequences(feat, 4)
    assert s.shape == (17, 4, 3) and idx[0] == 3 and idx[-1] == 19
    assert np.allclose(s[0], feat[0:4]) and np.allclose(s[-1], feat[16:20])
    # big_move_label: train-median threshold, leak-free tail
    close = 100 + np.cumsum(np.random.randn(500))
    y = big_move_label(close, 5, train_end=350)
    assert set(np.unique(y)).issubset({-1, 0, 1}) and (y[-5:] == -1).all()
    # _fit_eval_auc learns a planted temporal signal (sum of feature 0 over the window -> class 1)
    N, L, F = 1500, 6, 3
    X = np.random.randn(N, L, F).astype(np.float32)
    drive = X[:, :, 0].sum(1) + 0.3 * np.random.randn(N)
    yb = (drive > np.median(drive)).astype(int)
    a = int(N * 0.7)
    m = _build_models(F, L, num_classes=2)["LSTM"]
    auc, f0, f1 = _fit_eval_auc(m, X[:a], yb[:a], X[a:], yb[a:], epochs=8, device="cpu")
    assert auc > 0.65, f"should learn planted temporal signal, got AUC {auc:.3f}"
    assert f1 < f0, "model did not train"
    # tabular baseline runs + in range
    rf_auc, lr_auc = _tabular_auc(X[:a, -1, :], yb[:a], X[a:, -1, :], yb[a:])
    assert 0.0 <= rf_auc <= 1.0 and 0.0 <= lr_auc <= 1.0
    print(f"seq_timing_probe self-test: ALL PASS (planted-signal LSTM AUC {auc:.2f}, "
          f"tabular rf {rf_auc:.2f}/logit {lr_auc:.2f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    selftest() if a.selftest else run()
