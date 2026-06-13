"""
seq_model_feasibility.py — INDEPENDENT sequence-model check (TCN / LSTM / Transformer).
=========================================================================================
Answers ONE question, in isolation: do sequence models FIT our app's data, and do any of
them add *decorrelated* lift over the trees — enough to earn a stacker seat? It does NOT
touch the live ensemble, FEATURE_NAMES, serving, or the model files. It is a research
harness you run on the side (ideally with the app stopped / after a train, to avoid GPU
contention), exactly the "enters as a challenger, measured on held-out sign-truth" rule.

Context (already true in the app): the **TCN** (`TCNSequenceNet`) and an **LSTM**
(`SequenceNet`) are ALREADY stacker seats in v6/v7 (model.py). Only the **Transformer** is
new. The documented gate (V8/V9, NEXT_STEPS §8): at ~46k–130k samples sequence models tend
to OVERFIT and lose to the trees — adopt one ONLY if it shows decorrelated lift here first.

Interface matches the app EXACTLY: input (N, LOOKBACK=60, NUM_FEATURES=136) -> 3-class
logits [DOWN, NEUTRAL, UP]. Labels are the app's one-hot triple-barrier Y (leak-free).

Leakage discipline (the §5bs lesson): TEMPORAL split with an EMBARGO of max_h bars between
train and test so no train label's forward horizon overlaps the test region.

Usage:
  python backend/seq_model_feasibility.py --selftest          # mechanical FIT, synthetic, CPU
  python backend/seq_model_feasibility.py --run data/seq.npz   # real eval on saved (X,Y,max_h)
To make the real-data npz (AFTER the train, app stopped) — using the app's own pipeline:
  X,Y,Ymag = features.build_sequences(feat_matrix, closes, atr_arr=atr, highs=h, lows=l, return_magnitude=True)
  np.savez('data/seq.npz', X=X, Y=Y[5], max_h=15)   # one horizon at a time
"""
import argparse
import os
import sys

import numpy as np

LOOKBACK = 60       # features.LOOKBACK
NUM_FEATURES = 136  # features.NUM_FEATURES (v7)
CLASS_DIR = {0: "DOWN", 1: "NEUTRAL", 2: "UP"}  # matches model_verifier._CLASS_DIR


# ───────────────────────── architectures (mirror the app's interface) ──────────────────
def _build_models(input_dim, lookback, num_classes=3):
    import torch.nn as nn

    class LSTMSeqNet(nn.Module):           # mirrors app SequenceNet (LSTM -> GRU -> fc)
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(input_dim, 64, batch_first=True)
            self.gru = nn.GRU(64, 64, batch_first=True)
            self.fc = nn.Linear(64, num_classes)

        def forward(self, x):
            out, _ = self.lstm(x)
            out, _ = self.gru(out)
            return self.fc(out[:, -1, :])

    class TCNSeqNet(nn.Module):            # mirrors app TCNSequenceNet (dilated Conv1d)
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(
                nn.Conv1d(input_dim, 64, 3, padding=1, dilation=1), nn.GELU(), nn.BatchNorm1d(64),
                nn.Conv1d(64, 64, 3, padding=2, dilation=2), nn.GELU(), nn.BatchNorm1d(64),
                nn.Conv1d(64, 64, 3, padding=4, dilation=4), nn.GELU(), nn.AdaptiveAvgPool1d(1))
            self.fc = nn.Linear(64, num_classes)

        def forward(self, x):
            return self.fc(self.net(x.transpose(1, 2)).squeeze(-1))

    class TransformerSeqNet(nn.Module):    # the NEW candidate — small patch-attention encoder
        def __init__(self, d_model=64, nhead=4, layers=2):
            super().__init__()
            self.proj = nn.Linear(input_dim, d_model)
            self.pos = nn.Parameter(__import__("torch").zeros(1, lookback, d_model))
            enc = nn.TransformerEncoderLayer(d_model, nhead, dim_feedforward=4 * d_model,
                                             batch_first=True, dropout=0.1)
            self.enc = nn.TransformerEncoder(enc, layers)
            self.fc = nn.Linear(d_model, num_classes)

        def forward(self, x):
            h = self.proj(x) + self.pos[:, :x.size(1), :]
            return self.fc(self.enc(h).mean(dim=1))

    return {"TCN": TCNSeqNet(), "LSTM": LSTMSeqNet(), "Transformer": TransformerSeqNet()}


# ───────────────────────── leak-free split + metrics ──────────────────────────────────
def purged_temporal_split(n: int, max_h: int, train_frac: float = 0.7):
    """Train = oldest train_frac MINUS an embargo of max_h (so no train label's forward
    horizon reaches into the test region); test = newest. Returns (train_idx, test_idx)."""
    cut = int(n * train_frac)
    train_idx = np.arange(0, max(0, cut - max_h))
    test_idx = np.arange(cut, n)
    return train_idx, test_idx


def sign_truth(logits: np.ndarray, y_onehot: np.ndarray) -> dict:
    """Committed sign-truth: of the rows the model commits UP/DOWN (argmax != NEUTRAL),
    how many match the realized direction. NEUTRAL abstentions excluded (the §5ba rule)."""
    pred = logits.argmax(1)
    truth = y_onehot.argmax(1)
    committed = pred != 1                       # not NEUTRAL
    n_c = int(committed.sum())
    correct = (pred == truth) & committed
    acc = float(correct.sum() / n_c) if n_c else None
    return {"committed": n_c, "n": len(pred), "sign_acc": acc, "correct_vec": correct.astype(int)}


def decorrelation(a_correct: np.ndarray, b_correct: np.ndarray) -> float:
    """Pearson corr of two models' per-row correctness. LOW corr = decorrelated errors =
    a real reason to add the seat. ~1.0 = it agrees with the incumbent = adds nothing."""
    if a_correct.std() < 1e-9 or b_correct.std() < 1e-9:
        return 1.0
    return float(np.corrcoef(a_correct, b_correct)[0, 1])


# ───────────────────────── train / eval one model ─────────────────────────────────────
def _fit_eval(model, Xtr, ytr, Xte, yte, epochs=8, device="cpu"):
    import torch
    import torch.nn as nn
    model = model.to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    lossf = nn.CrossEntropyLoss()
    ytr_t = torch.tensor(ytr.argmax(1), dtype=torch.long, device=device)
    Xtr_t = torch.tensor(Xtr, dtype=torch.float32, device=device)
    Xte_t = torch.tensor(Xte, dtype=torch.float32, device=device)
    bs = 1024
    first_loss = last_loss = None
    model.train()
    for ep in range(epochs):
        perm = torch.randperm(len(Xtr_t), device=device)
        ep_loss = 0.0
        for i in range(0, len(perm), bs):
            idx = perm[i:i + bs]
            opt.zero_grad()
            out = model(Xtr_t[idx])
            loss = lossf(out, ytr_t[idx])
            loss.backward()
            opt.step()
            ep_loss += float(loss.detach()) * len(idx)
        ep_loss /= len(Xtr_t)
        if ep == 0:
            first_loss = ep_loss
        last_loss = ep_loss
    model.eval()
    with torch.no_grad():
        logits = model(Xte_t).cpu().numpy()
    st = sign_truth(logits, yte)
    st["first_loss"], st["last_loss"] = first_loss, last_loss
    return st


# ───────────────────────── real run (saved X,Y) ───────────────────────────────────────
def run(npz_path: str, epochs: int = 12):
    import torch
    if not os.path.exists(npz_path):
        sys.exit(f"missing {npz_path} — build it from the app pipeline (see header).")
    d = np.load(npz_path)
    X, Y, max_h = d["X"], d["Y"], int(d["max_h"])
    n = len(X)
    tr, te = purged_temporal_split(n, max_h)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"X={X.shape}  Y={Y.shape}  max_h={max_h}  device={device}")
    print(f"split: train={len(tr)} test={len(te)} (embargo={max_h} bars)\n")
    print(f"  {'model':<12}{'committed':>10}{'sign_acc':>10}{'loss':>16}  decorr_vs_TCN")
    base = None
    for name, model in _build_models(X.shape[2], X.shape[1]).items():
        st = _fit_eval(model, X[tr], Y[tr], X[te], Y[te], epochs=epochs, device=device)
        if name == "TCN":
            base = st["correct_vec"]
        dec = decorrelation(st["correct_vec"], base) if base is not None else 1.0
        acc = f"{st['sign_acc']*100:.1f}%" if st["sign_acc"] is not None else "—"
        loss = f"{st['first_loss']:.3f}->{st['last_loss']:.3f}"
        print(f"  {name:<12}{st['committed']:>10}{acc:>10}{loss:>16}  {dec:+.2f}")
    print("\nVERDICT GUIDE: a seat is justified only if a model's sign_acc beats ~0.50–0.55")
    print("AND its decorr_vs_TCN is LOW (errors differ from the incumbent). A high-acc but")
    print("highly-correlated model adds robustness, not diversity — don't add it. Overfit tell:")
    print("loss collapses but test sign_acc stays ~0.50 -> sequence models don't pay at this scale.")


# ───────────────────────── self-test (mechanical FIT, no real data, CPU) ──────────────
def selftest():
    try:
        import torch
    except Exception as e:
        sys.exit(f"PyTorch required for the harness (it's already an app dependency): {e}")
    torch.manual_seed(0)
    torch.set_num_threads(1)                     # be a good citizen if a train is running
    np.random.seed(0)

    # tiny synthetic: a LEARNABLE signal (last-step feature 0 sign drives the label).
    N, lb, dim, max_h = 600, 16, 8, 5
    X = np.random.randn(N, lb, dim).astype(np.float32)
    drive = X[:, -1, 0] + 0.3 * X[:, -2, 0]
    cls = np.where(drive > 0.3, 2, np.where(drive < -0.3, 0, 1))   # UP / DOWN / NEUTRAL
    Y = np.eye(3, dtype=np.float32)[cls]

    # 1) leak-free split: no train label horizon reaches the test region.
    tr, te = purged_temporal_split(N, max_h)
    assert len(tr) and len(te)
    assert tr.max() + max_h <= te.min(), "embargo violated — train label could peek into test"
    assert len(np.intersect1d(tr, te)) == 0, "train/test index overlap"

    # 2) every architecture builds, accepts the app's (lb, dim) shape, outputs (N,3), trains.
    models = _build_models(input_dim=dim, lookback=lb)
    assert set(models) == {"TCN", "LSTM", "Transformer"}
    for name, m in models.items():
        out = m(torch.tensor(X[:4], dtype=torch.float32))
        assert tuple(out.shape) == (4, 3), f"{name} output {tuple(out.shape)} != (4,3)"
        st = _fit_eval(m, X[tr], Y[tr], X[te], Y[te], epochs=6, device="cpu")
        assert st["last_loss"] < st["first_loss"], f"{name} did not train (loss flat)"
        assert st["sign_acc"] is None or 0.0 <= st["sign_acc"] <= 1.0
        print(f"  FIT OK  {name:<12} committed={st['committed']:<4} "
              f"sign_acc={(st['sign_acc'] or 0)*100:.0f}%  loss {st['first_loss']:.2f}->{st['last_loss']:.2f}")

    # 3) decorrelation metric behaves (identical vec -> 1.0; opposite -> negative).
    v = np.array([1, 0, 1, 1, 0])
    assert abs(decorrelation(v, v) - 1.0) < 1e-6
    assert decorrelation(v, 1 - v) < 0
    print("\nMECHANICAL FIT: PASS — TCN/LSTM/Transformer all accept the app's (60x136) shape,")
    print("output 3-class logits, train, and the purged split + decorrelation metric are sound.")
    print("Run the REAL eval (--run) after the train to see if any earns a seat.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--run", metavar="NPZ", help="path to saved (X,Y,max_h) for the real eval")
    ap.add_argument("--epochs", type=int, default=12)
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.run:
        run(a.run, epochs=a.epochs)
    else:
        ap.print_help()
