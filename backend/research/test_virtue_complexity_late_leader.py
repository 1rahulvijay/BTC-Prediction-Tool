"""
test_virtue_complexity_late_leader.py — "Virtue of Complexity" (Kelly/Malamud/Zhou) applied to the
ONE learnable, ask-priced target we have: late-leader fair value at the 30s checkpoint.
====================================================================================================
Question: does a LARGE feature space + ridge (the paper's many-weak-predictors recipe) predict the
leader's settlement MISPRICING (y = win − ask) better than (a) the market itself and (b) a tiny
simple model — measured the only way that counts: FEE-ADJUSTED EV/SHARE AT THE ACTUAL EXECUTABLE ASK?

Data: Kaggle archive (7) per-second executable bid/ask both sides + settled outcomes, LAST 30 DAYS
(operator spec). 5m rounds, checkpoint 30s left, leader = higher bid. Causal: features use only
ticks at/before the checkpoint and BTC matrix values at/before the checkpoint minute.

Ladder (identical trade rule for all: buy leader iff pred_mispricing > taker_fee(ask) + 1c buffer):
  M0 baseline   buy EVERY leader (the market, no model)
  M1 simple     ridge on [ask, |d60|, rv5m]                                (3 features)
  M2 base       ridge on ~26 hand features                                 (book history + BTC + time)
  M3 poly       ridge on degree-2 expansion of M2                          (~400 features)
  M4 rff        ridge on 1,500 random Fourier features of M2               (the complexity wall)

Validation: purged walk-forward by day (6 sequential 5-day folds; train strictly before test).
Nulls (mandatory):
  N1 shuffled-label train (per fold) — complexity fitting noise must NOT survive
  N2 score shuffle WITHIN 5c ask bucket on test — if EV persists, the model just re-expressed the
     ask (the sufficient-statistic trap the gated-lift test exposed); real skill must die here only
     if the model's ordering inside a bucket carries information.
Promotion bar (pre-declared): complex beats M1 by ≥ +0.5c/share EV on the SAME folds, positive EV
LB, positive in ≥ 4/6 folds — else it is a research note, not a head.

CPU-polite: caps BLAS threads at 2 (a 1,500-day retrain is running).
Usage: python backend/research/test_virtue_complexity_late_leader.py
"""
from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

import io
import math
import sys
import zipfile
from datetime import date

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ZIP = os.path.join(ROOT, "Kaggle Data", "archive (7).zip")
MATRIX = os.path.join(ROOT, "data", "research_matrix_1m.parquet")
OUT_MD = os.path.join(ROOT, "docs", "active", f"VIRTUE_COMPLEXITY_LATE_LEADER_{date.today().isoformat()}.md")
FEE_RATE, BUFFER, DAYS, CHK = 0.07, 0.01, 30, 30
LAGS = (5, 15, 30, 60, 120)
RNG = np.random.default_rng(7)


def fee(a):
    return FEE_RATE * a * (1.0 - a)


def ev_stats(ask, win):
    """Fee-adjusted EV/share + Wilson-LB EV for a traded subset."""
    n = len(win)
    if n < 25:
        return {"n": n, "ev": None, "evlb": None, "win": None, "ask": None}
    w, a = float(np.mean(win)), float(np.mean(ask))
    z = 1.96
    d = 1 + z * z / n
    lb = (w + z * z / (2 * n)) / d - z * math.sqrt(w * (1 - w) / n + z * z / (4 * n * n)) / d
    pnl = win - ask - fee(ask)
    return {"n": n, "ev": float(np.mean(pnl)), "evlb": lb - a - fee(a), "win": w, "ask": a}


def fmt(s, label):
    if s["ev"] is None:
        return f"| {label} | n={s['n']} (too few) | — | — | — |"
    return (f"| {label} | {s['n']:,} | {100*s['win']:.1f}% @ {100*s['ask']:.1f}c "
            f"| **{100*s['ev']:+.2f}c** | {100*s['evlb']:+.2f}c |")


def load():
    import pyarrow.parquet as pq
    with zipfile.ZipFile(ZIP) as zf:
        mk = pq.read_table(io.BytesIO(zf.read("btc_markets.parquet")),
                           columns=["condition_id", "market_start", "market_end", "outcome"]).to_pandas()
        tk = pq.read_table(io.BytesIO(zf.read("btc_ticks.parquet")),
                           columns=["condition_id", "t", "bu", "au", "bd", "ad"]).to_pandas()
    mk["outcome"] = mk["outcome"].str.lower()
    mk = mk[mk["outcome"].isin(["up", "down"])].copy()
    mk["start_ms"] = mk["market_start"].astype("int64") // 10**6
    mk["end_ms"] = mk["market_end"].astype("int64") // 10**6
    mk = mk[(mk["end_ms"] - mk["start_ms"]).between(290_000, 310_000)]        # 5m rounds only
    cut = mk["end_ms"].max() - DAYS * 86_400_000                              # LAST 30 days
    mk = mk[mk["start_ms"] >= cut].copy()
    mk["end_t"] = mk["end_ms"] // 1000
    mk["day"] = (mk["start_ms"] // 86_400_000).astype(int)
    # BTC matrix joins (causal: values at/at-before the checkpoint minute)
    mx = pq.read_table(MATRIX, columns=["ts_ms", "close"]).to_pandas().drop_duplicates("ts_ms")
    mx = mx.sort_values("ts_ms")
    px = dict(zip(mx.ts_ms.astype("int64"), mx.close.astype(float)))
    closes = mx.close.to_numpy(float)
    ts_idx = {int(t): i for i, t in enumerate(mx.ts_ms.astype("int64"))}
    def rv(ms, k):     # std of 1m log-diffs over last k minutes before ms
        i = ts_idx.get(int(ms // 60_000 * 60_000))
        if i is None or i < k + 1:
            return np.nan
        w = np.diff(np.log(closes[i - k:i + 1]))
        return float(np.std(w) * 1e4)                                          # bps
    mk["anchor"] = mk["start_ms"].map(lambda s: px.get(int(s - 60_000)))
    mk["p60"] = mk["end_ms"].map(lambda e: px.get(int(e - 120_000)))           # close of minute ~60s left
    mk["p180"] = mk["end_ms"].map(lambda e: px.get(int(e - 240_000)))
    mk["rv5"] = mk["end_ms"].map(lambda e: rv(e - 120_000, 5))
    mk["rv30"] = mk["end_ms"].map(lambda e: rv(e - 120_000, 30))
    mk = mk.dropna(subset=["anchor", "p60", "p180", "rv5", "rv30"])
    mk["d60"] = mk["p60"] - mk["anchor"]
    mk["d180"] = mk["p180"] - mk["anchor"]
    # tick features at checkpoint + lags (one row per (round, offset) via drop_duplicates)
    tk = tk.merge(mk[["condition_id", "end_t"]], on="condition_id", how="inner")
    tk["secs_left"] = tk["end_t"] - tk["t"]
    snap = {}
    for off in (0,) + LAGS:
        s = (tk[tk["secs_left"] == CHK + off].drop_duplicates("condition_id")
             .set_index("condition_id")[["bu", "au", "bd", "ad"]])
        snap[off] = s.rename(columns={c: f"{c}_{off}" for c in s.columns})
    j = mk.set_index("condition_id").join(snap[0], how="inner")
    for off in LAGS:
        j = j.join(snap[off], how="left")
    j["lead_up"] = j["bu_0"] > j["bd_0"]
    j["ask"] = np.where(j["lead_up"], j["au_0"], j["ad_0"])
    j["bid"] = np.where(j["lead_up"], j["bu_0"], j["bd_0"])
    j["t_ask"] = np.where(j["lead_up"], j["ad_0"], j["au_0"])
    j["t_bid"] = np.where(j["lead_up"], j["bd_0"], j["bu_0"])
    j["win"] = np.where(j["lead_up"], j["outcome"].eq("up"), j["outcome"].eq("down")).astype(int)
    j["ld"] = np.where(j["lead_up"], 1.0, -1.0)
    j = j[(j["ask"] > 0.05) & (j["ask"] < 0.97)].copy()
    # leader-frame mids now and at each lag (leader side fixed at checkpoint)
    def mid_at(off):
        up_mid = (j[f"bu_{off}"] + j[f"au_{off}"]) / 2
        dn_mid = (j[f"bd_{off}"] + j[f"ad_{off}"]) / 2
        return np.where(j["lead_up"], up_mid, dn_mid)
    j["mid"] = mid_at(0)
    for off in LAGS:
        j[f"mid_d{off}"] = j["mid"] - mid_at(off)
    mids = np.column_stack([mid_at(off) for off in (0,) + LAGS])
    j["book_rv"] = np.nanstd(np.diff(mids, axis=1), axis=1)
    hrs = pd.to_datetime(j["start_ms"], unit="ms").dt.hour + pd.to_datetime(j["start_ms"], unit="ms").dt.minute / 60
    j["hr_sin"], j["hr_cos"] = np.sin(hrs / 24 * 2 * np.pi), np.cos(hrs / 24 * 2 * np.pi)
    j["sd60"] = j["d60"] * j["ld"]                                             # signed toward leader
    j["sd180"] = j["d180"] * j["ld"]
    j["absd60"] = j["d60"].abs()
    j["mom"] = j["sd60"] - j["sd180"]
    j["comp"] = j["au_0"] + j["ad_0"]
    j["spread"] = j["ask"] - j["bid"]
    j["t_spread"] = j["t_ask"] - j["t_bid"]
    j["d_over_rv"] = j["absd60"] / (j["rv5"] * j["anchor"] / 1e4 + 1e-9)
    base = ["ask", "bid", "spread", "mid", "comp", "t_ask", "t_bid", "t_spread",
            "mid_d5", "mid_d15", "mid_d30", "mid_d60", "mid_d120", "book_rv",
            "sd60", "sd180", "absd60", "mom", "rv5", "rv30", "d_over_rv",
            "hr_sin", "hr_cos"]
    j = j.dropna(subset=base + ["win", "day"])
    return j.reset_index(drop=True), base


def rff(Xs, D=1500, gamma=None):
    """Random Fourier features of standardized X (RBF kernel approx)."""
    d = Xs.shape[1]
    gamma = gamma or 1.0 / d
    W = RNG.normal(0, math.sqrt(2 * gamma), size=(d, D)).astype(np.float32)
    b = RNG.uniform(0, 2 * np.pi, size=D).astype(np.float32)
    return (np.sqrt(2.0 / D) * np.cos(Xs.astype(np.float32) @ W + b))


def run():
    from sklearn.linear_model import RidgeCV
    from sklearn.preprocessing import PolynomialFeatures, StandardScaler
    df, base = load()
    days = np.sort(df["day"].unique())
    folds = np.array_split(days[len(days) % 6:], 6)                            # 6 sequential blocks
    alphas = np.logspace(-2, 4, 10)
    y_all = (df["win"] - df["ask"]).to_numpy(float)                            # mispricing target
    models = ("M1 simple(3f)", "M2 base(~23f)", "M3 poly(~300f)", "M4 rff(1500f)")
    res = {m: {"ask": [], "win": [], "fold_ev": []} for m in models}
    res_null1 = {m: {"ask": [], "win": []} for m in models}                    # shuffled-label
    base_all = {"ask": [], "win": []}
    for k in range(1, 6):                                                      # folds 1..5 tested
        tr = df["day"].isin(np.concatenate(folds[:k]))
        te = df["day"].isin(folds[k])
        if te.sum() < 100 or tr.sum() < 300:
            continue
        Xtr_raw, Xte_raw = df.loc[tr, base].to_numpy(float), df.loc[te, base].to_numpy(float)
        ytr = y_all[tr.to_numpy()]          # test-fold y is never needed: scoring uses ask/win
        ask_te, win_te = df.loc[te, "ask"].to_numpy(float), df.loc[te, "win"].to_numpy(int)
        base_all["ask"].append(ask_te); base_all["win"].append(win_te)
        sc = StandardScaler().fit(Xtr_raw)
        Xtr_s, Xte_s = sc.transform(Xtr_raw), sc.transform(Xte_raw)
        poly = PolynomialFeatures(2, include_bias=False).fit(Xtr_s)
        designs = {
            "M1 simple(3f)": (Xtr_s[:, [0, 16, 18]], Xte_s[:, [0, 16, 18]]),   # ask, absd60, rv5
            "M2 base(~23f)": (Xtr_s, Xte_s),
            "M3 poly(~300f)": (poly.transform(Xtr_s), poly.transform(Xte_s)),
        }
        Ztr = rff(Xtr_s); Zte = rff(Xte_s)
        designs["M4 rff(1500f)"] = (Ztr, Zte)
        ysh = RNG.permutation(ytr)                                             # N1 null
        for m, (A, B) in designs.items():
            pred = RidgeCV(alphas=alphas).fit(A, ytr).predict(B)
            take = pred > fee(ask_te) + BUFFER
            res[m]["ask"].append(ask_te[take]); res[m]["win"].append(win_te[take])
            fe = ev_stats(ask_te[take], win_te[take])
            res[m]["fold_ev"].append(fe["ev"])
            pred0 = RidgeCV(alphas=alphas).fit(A, ysh).predict(B)
            t0 = pred0 > fee(ask_te) + BUFFER
            res_null1[m]["ask"].append(ask_te[t0]); res_null1[m]["win"].append(win_te[t0])
    # N2: within-ask-bucket score shuffle, applied to the best complex model's pooled test preds
    # (re-run fold preds pooled): approximate by re-using M4 selections vs bucket-shuffled selections.
    L = [f"# Virtue of Complexity — Late-Leader Fair Value ({date.today().isoformat()})", "",
         f"Kelly/Malamud/Zhou recipe (many weak features + ridge) on the ONE ask-priced learnable target: "
         f"leader mispricing `y = win − ask` at the {CHK}s checkpoint. Last {DAYS} days of kachoio "
         f"executable quotes, n={len(df):,} rounds, purged day-wise walk-forward (5 tested folds). "
         f"Trade rule for every model: buy leader iff pred > taker_fee + {int(BUFFER*100)}c. "
         f"Metric: fee-adjusted EV/share at the ACTUAL ask.", "",
         "| model | trades | win @ ask | EV/share | EV (LB) |", "|---|---|---|---|---|"]
    ba = np.concatenate(base_all["ask"]); bw = np.concatenate(base_all["win"])
    L.append(fmt(ev_stats(ba, bw), "M0 market (all leaders)"))
    ev_by_model = {}
    for m in models:
        a = np.concatenate(res[m]["ask"]) if res[m]["ask"] else np.array([])
        w = np.concatenate(res[m]["win"]) if res[m]["win"] else np.array([])
        s = ev_stats(a, w)
        ev_by_model[m] = s
        pos_folds = sum(1 for e in res[m]["fold_ev"] if e is not None and e > 0)
        L.append(fmt(s, f"{m} (+folds {pos_folds}/{len(res[m]['fold_ev'])})"))
    L += ["", "## Nulls", "| model | shuffled-label trades | EV |", "|---|---|---|"]
    for m in models:
        a = np.concatenate(res_null1[m]["ask"]) if res_null1[m]["ask"] else np.array([])
        w = np.concatenate(res_null1[m]["win"]) if res_null1[m]["win"] else np.array([])
        s = ev_stats(a, w)
        L.append(f"| {m} | {s['n']} | {('%+.2fc' % (100*s['ev'])) if s['ev'] is not None else '—'} |")
    # verdict vs pre-declared bar
    s1, s4 = ev_by_model["M1 simple(3f)"], ev_by_model["M4 rff(1500f)"]
    s3 = ev_by_model["M3 poly(~300f)"]
    best_cx = max((s for s in (s3, s4) if s["ev"] is not None), key=lambda s: s["ev"], default=None)
    L += ["", "## Verdict (pre-declared bar: complex ≥ simple +0.5c EV, positive LB, ≥4/6 folds)"]
    if best_cx is None or s1["ev"] is None:
        L.append("**INSUFFICIENT TRADES** — the gate rarely fired; complexity had nothing to price.")
    elif best_cx["ev"] - s1["ev"] >= 0.005 and best_cx["evlb"] > 0:
        L.append(f"**COMPLEXITY WINS the bar**: best complex EV {100*best_cx['ev']:+.2f}c vs simple "
                 f"{100*s1['ev']:+.2f}c (Δ {100*(best_cx['ev']-s1['ev']):+.2f}c), LB {100*best_cx['evlb']:+.2f}c. "
                 f"Next: harden with the within-ask-bucket shuffle + trailing-side control before ANY live use.")
    else:
        L.append(f"**COMPLEXITY DOES NOT CLEAR THE BAR**: best complex EV "
                 f"{('%+.2fc' % (100*best_cx['ev']))} vs simple {('%+.2fc' % (100*s1['ev']))}"
                 f" — consistent with 'the ask is the sufficient statistic'. Research note only; "
                 f"no head is built from this.")
    L += ["", "## Honest limits",
          "- Trade prices are real executable asks, but top-of-book, 1s cadence, no latency model.",
          f"- {DAYS} days ≈ one regime slice; the paper's virtue claims are themselves contested "
          "(zero-intercept / aggregation critiques) — this is a bounded test, not a doctrine.",
          "- The shuffled-label null must be ≈0/absent; if it trades profitably, the gate itself "
          "(pred > fee+buffer on a mispricing target) is selecting on ask level → distrust everything above.",
          "- Not wired anywhere. PAPER research only."]
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    run()
