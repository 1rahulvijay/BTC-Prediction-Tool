"""
run_ceiling_break_experiments.py — the 5 ceiling-break experiments in ONE script.
==================================================================================
Runs on the last N days of the 1m research matrix (default 30) with a leak-free TEMPORAL 70/30
train/test split (+ embargo), and computes top-1/5/10% precision, barrier-hit precision, AUC, and
profit-after-spread. Honest about what cannot be run on historical data:

  Exp 1  Triple-barrier model        RUNNABLE (matrix close/high/low) — upper/lower/neutral, 5m & 15m
  Exp 2  L2 / new-info value-add     PROXY ONLY — true L2 (microprice/OFI/depth) needs RECORD-FORWARD
                                     data (no free historical depth). We test the order-flow + cross-
                                     venue columns that DO exist (cvd/vpin/basis/perp) vs candle-only.
  Exp 3  Market-lag (PM ask)         SKIPPED unless the recorder DB has resolved rounds (needs the
                                     Polymarket 5m/15m ask history; analytics PM markets are long-dated)
  Exp 4  Maker-fill simulation       SKIPPED — needs Polymarket book history (same gap as Exp 3)
  Exp 5  Meta-skip model             RUNNABLE if champion_snapshots ⋈ price_to_beat has enough resolved
                                     rows: predict "the acted side held", 70/30, Logistic/HistGB/CatBoost

Models (whichever are installed): Logistic, ExtraTrees, LightGBM, XGBoost, CatBoost.
Output: docs/active/CEILING_BREAK_EXPERIMENTS_<date>.md + console summary.

Usage:  python backend/run_ceiling_break_experiments.py [--days 30] [--split 0.70]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
MATRIX = os.path.join(DATA, "research_matrix_1m.parquet")
OUT_MD = os.path.join(ROOT, "docs", "active", f"CEILING_BREAK_EXPERIMENTS_{date.today().isoformat()}.md")

BARRIER_BPS = {5: 10.0, 15: 20.0}     # triple-barrier ±threshold per horizon
SPREAD_BPS = 2.0                      # assumed round-trip cost subtracted from profit
TOP_FRACS = (0.01, 0.05, 0.10)
EMBARGO_BARS = 30                     # gap between train and test (> max horizon) to kill leakage

# Feature groups (from the matrix). CANDLE = OHLCV/kline-derived (always live). FLOW = trade-flow +
# cross-venue (the available proxy for "new information"; NOT true L2 depth).
CANDLE = ["rv_15m", "rv_30m", "rv_60m", "rv_term", "log_count", "log_vol", "count_accel_5m",
          "vol_accel", "compression_ratio", "range_15m", "shock_magnitude", "micro_range_15m"]
FLOW = ["vpin_15m", "vpin_30m", "vpin_50m", "cvd_1m", "cvd_5m", "delta", "vpin", "large_trade_delta",
        "large_trade_imbalance", "funding_velocity", "cvd_spot", "cvd_perp", "cvd_divergence",
        "perp_spot_basis_bps", "vol_spot", "vol_perp"]


# ───────────────────────── models + metrics ─────────────────────────
def model_zoo():
    from sklearn.ensemble import ExtraTreesClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    z = {
        "logistic": Pipeline([("sc", StandardScaler()),
                              ("clf", LogisticRegression(class_weight="balanced", max_iter=1000))]),
        "extratrees": ExtraTreesClassifier(n_estimators=250, max_depth=10, min_samples_leaf=50,
                                           class_weight="balanced", n_jobs=2, random_state=0),
    }
    try:
        from lightgbm import LGBMClassifier
        z["lightgbm"] = LGBMClassifier(n_estimators=250, max_depth=6, learning_rate=0.05,
                                       class_weight="balanced", n_jobs=2, random_state=0, verbose=-1)
    except Exception:
        pass
    try:
        from xgboost import XGBClassifier
        z["xgboost"] = XGBClassifier(n_estimators=250, max_depth=5, learning_rate=0.05, n_jobs=2,
                                     random_state=0, eval_metric="logloss", tree_method="hist")
    except Exception:
        pass
    try:
        from catboost import CatBoostClassifier
        z["catboost"] = CatBoostClassifier(iterations=250, depth=5, learning_rate=0.05, verbose=0,
                                           random_seed=0, allow_writing_files=False)
    except Exception:
        pass
    return z


def _bin_metrics(y, p, outcome_bps):
    from sklearn.metrics import roc_auc_score
    y = np.asarray(y); p = np.asarray(p); outcome_bps = np.asarray(outcome_bps)
    m = {"n": int(len(y)), "base": round(float(y.mean()), 4),
         "auc": round(float(roc_auc_score(y, p)), 3) if len(np.unique(y)) > 1 else None}
    order = np.argsort(p)[::-1]
    for f in TOP_FRACS:
        k = max(1, int(len(p) * f))
        sel = order[:k]
        m[f"top{int(f*100)}_prec"] = round(float(y[sel].mean()), 4)
        m[f"top{int(f*100)}_profit_bps"] = round(float(outcome_bps[sel].mean()) - SPREAD_BPS, 2)
    return m


def _fit_eval(models, Xtr, ytr, Xte, yte, out_bps_te):
    rows = {}
    for name, mdl in models.items():
        try:
            mdl.fit(Xtr, ytr)
            p = mdl.predict_proba(Xte)[:, 1]
            rows[name] = _bin_metrics(yte, p, out_bps_te)
        except Exception as e:
            rows[name] = {"error": str(e)[:60]}
    return rows


def _split(n):
    cut = int(n * SPLIT)
    tr = np.arange(0, cut - EMBARGO_BARS)
    te = np.arange(cut, n)
    return tr, te


# ───────────────────────── triple barrier ─────────────────────────
def triple_barrier(close, high, low, h, T_bps):
    n = len(close)
    up = close * (1 + T_bps / 1e4); dn = close * (1 - T_bps / 1e4)
    upper = np.zeros(n, bool); lower = np.zeros(n, bool); done = np.zeros(n, bool)
    for j in range(1, h + 1):
        end = n - j
        if end <= 0:
            break
        i = np.arange(end)
        act = ~done[i]
        hu = act & (high[i + j] >= up[i])
        hl = act & (low[i + j] <= dn[i])
        nu = hu & ~hl; nl = hl & ~hu; both = hu & hl
        upper[i[nu]] = True; lower[i[nl]] = True
        done[i[nu | nl | both]] = True
    neutral = ~(upper | lower)
    valid = np.arange(n) < (n - h)
    return upper, lower, neutral, valid


# ───────────────────────── EXPERIMENTS ─────────────────────────
def exp1_triple_barrier(df, L):
    L.append("## Experiment 1 — Triple-barrier model (RUNNABLE)")
    models = model_zoo()
    L.append(f"Models: {', '.join(models)} · barriers {BARRIER_BPS} bps · spread {SPREAD_BPS} bps · "
             f"70/30 temporal split (embargo {EMBARGO_BARS} bars).")
    close = df["close"].to_numpy(float); high = df["high"].to_numpy(float); low = df["low"].to_numpy(float)
    feats = [c for c in (CANDLE + FLOW) if c in df.columns]
    X = df[feats].to_numpy(float)
    for h in (5, 15):
        T = BARRIER_BPS[h]
        up, dn, neu, valid = triple_barrier(close, high, low, h, T)
        dist = f"upper {up.mean()*100:.1f}% / lower {dn.mean()*100:.1f}% / neutral {neu.mean()*100:.1f}%"
        L.append(f"\n### {h}m barrier ±${close[-1]*T/1e4:.0f} (~{T:.0f} bps) — label dist: {dist}")
        # outcome bps if you BET the predicted barrier: +T hit, -T opposite, 0 neutral.
        out_up = np.where(up, T, np.where(dn, -T, 0.0))
        out_dn = np.where(dn, T, np.where(up, -T, 0.0))
        for side, y, obps in (("UPPER-first", up.astype(int), out_up), ("LOWER-first", dn.astype(int), out_dn)):
            m = valid & np.isfinite(X).all(axis=1)
            idx = np.where(m)[0]
            tr, te = _split(len(idx))
            Xtr, Xte = X[idx[tr]], X[idx[te]]
            ytr, yte = y[idx[tr]], y[idx[te]]
            obps_te = obps[idx[te]]
            res = _fit_eval(models, Xtr, ytr, Xte, yte, obps_te)
            L.append(f"\n**{side}** (test n={len(te):,}, base {yte.mean()*100:.1f}%):")
            L.append("| model | AUC | top1% prec | top5% prec | top10% prec | top5% profit (bps, after spread) |")
            L.append("|---|---:|---:|---:|---:|---:|")
            for name, r in res.items():
                if "error" in r:
                    L.append(f"| {name} | err: {r['error']} | | | | |"); continue
                L.append(f"| {name} | {r['auc']} | {r['top1_prec']*100:.1f}% | {r['top5_prec']*100:.1f}% "
                         f"| {r['top10_prec']*100:.1f}% | {r['top5_profit_bps']:+.1f} |")
    L.append("")


def exp2_newinfo_valueadd(df, L):
    L.append("## Experiment 2 — New-information value-add (PROXY — not true L2)")
    L.append("> True L2 (microprice / OFI / depth / book pull-refill) has **no free historical depth** — "
             "it must be **record-forward**. Here we test the order-flow + cross-venue columns that DO "
             "exist (cvd/vpin/large-trade/basis/perp) as a stand-in: does adding them lift the TOP buckets "
             "over candle-only? Target = 5m UPPER-first barrier (the Exp-1 label).")
    flow = [c for c in FLOW if c in df.columns]
    candle = [c for c in CANDLE if c in df.columns]
    if not flow:
        L.append("\n_No flow/cross-venue columns present in the matrix — cannot run. (Rebuild matrix with "
                 "trade-features + crossvenue.)_\n"); return
    close = df["close"].to_numpy(float); high = df["high"].to_numpy(float); low = df["low"].to_numpy(float)
    up, dn, neu, valid = triple_barrier(close, high, low, 5, BARRIER_BPS[5])
    y = up.astype(int); out_up = np.where(up, BARRIER_BPS[5], np.where(dn, -BARRIER_BPS[5], 0.0))
    try:
        from lightgbm import LGBMClassifier
        mk = lambda: LGBMClassifier(n_estimators=250, max_depth=6, learning_rate=0.05,
                                    class_weight="balanced", n_jobs=2, random_state=0, verbose=-1)
    except Exception:
        from sklearn.ensemble import ExtraTreesClassifier
        mk = lambda: ExtraTreesClassifier(n_estimators=250, max_depth=10, min_samples_leaf=50,
                                          class_weight="balanced", n_jobs=2, random_state=0)
    L.append("\n| feature set | n_feats | AUC | top1% prec | top5% prec | top5% profit (bps) |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for label, feats in (("candle-only", candle), ("candle + flow/cross-venue", candle + flow)):
        cols = [c for c in feats if c in df.columns]
        X = df[cols].to_numpy(float)
        m = valid & np.isfinite(X).all(axis=1)
        idx = np.where(m)[0]; tr, te = _split(len(idx))
        mdl = mk(); mdl.fit(X[idx[tr]], y[idx[tr]])
        p = mdl.predict_proba(X[idx[te]])[:, 1]
        r = _bin_metrics(y[idx[te]], p, out_up[idx[te]])
        L.append(f"| {label} | {len(cols)} | {r['auc']} | {r['top1_prec']*100:.1f}% | "
                 f"{r['top5_prec']*100:.1f}% | {r['top5_profit_bps']:+.1f} |")
    L.append("\n_Read: improvement should show in the TOP buckets, not average AUC. True L2 still needs the "
             "record-forward clock (Exp-2 proper)._\n")


def exp3_4_market_microstructure(L):
    import duckdb
    edb = os.path.join(DATA, "execution_layer.duckdb")
    L.append("## Experiment 3 — Market-lag (ask underreaction) & Experiment 4 — Maker-fill (SKIPPED)")
    n = 0
    if os.path.exists(edb):
        try:
            c = duckdb.connect(edb, read_only=True)
            try:
                n = c.execute("SELECT count(*) FROM pm_round_settlements").fetchone()[0]
            except Exception:
                n = 0
            c.close()
        except Exception as e:
            L.append(f"\n_execution_layer.duckdb is locked (recorder running) or unreadable: {str(e)[:70]}_")
    L.append(f"\nBoth need the **Polymarket 5m/15m ask history** from the recorder "
             f"(`execution_layer.duckdb` → pm_round_snapshots/settlements). Settled rounds found: **{n}**.")
    L.append("- Exp 3 target: `market_ask underreacts to fair_value movement` (fair/ask change 1s/3s, "
             "book age, spread, depth, seconds_left, distance, line-cross risk → buy only if "
             "`fair − ask − buffer > required_edge` and edge persists).")
    L.append("- Exp 4: simulate maker bids below the ask; measure fill rate, toxic-fill rate, PnL after fill.")
    L.append("**Cannot run on historical data** — Polymarket short-term depth is not archived (project "
             "stop-list). Leave the recorder running; re-run once ≥30 rounds settle, then "
             "`analyze_recorder_edge.py` / a market-lag fit becomes possible.\n")


def exp5_meta_skip(L):
    import duckdb
    L.append("## Experiment 5 — Meta-skip model (champion decision quality)")
    adb = os.path.join(DATA, "analytics.duckdb")
    try:
        c = duckdb.connect(adb, read_only=True)
        df = c.execute("""
            SELECT cs.horizon, cs.seconds_left, cs.current_move, cs.p_hold, cs.p_big_move, cs.p_big_drop,
                   cs.p_big_up, cs.p_big_down, cs.p_activity, cs.champion_confidence,
                   cs.current_position, cs.regime, cs.big_move_tier, cs.big_drop_risk, cs.activity_tier,
                   cs.champion_action, p.actual_direction
            FROM champion_snapshots cs JOIN price_to_beat p ON p.id = cs.round_id
            WHERE p.resolved = TRUE AND cs.current_position IN ('UP','DOWN')
              AND p.actual_direction IN ('UP','DOWN') AND cs.p_hold IS NOT NULL
            ORDER BY cs.ts
        """).df()
        c.close()
    except Exception as e:
        L.append(f"\n_Could not read champion_snapshots ⋈ price_to_beat: {str(e)[:80]}_\n"); return
    if len(df) < 400:
        L.append(f"\n_Insufficient resolved snapshots ({len(df)} < 400). Keep the app running; re-run later._\n")
        return
    y = (df["current_position"].astype(str) == df["actual_direction"].astype(str)).astype(int).values
    num = ["horizon", "seconds_left", "current_move", "p_hold", "p_big_move", "p_big_drop",
           "p_big_up", "p_big_down", "p_activity", "champion_confidence"]
    cat = ["current_position", "regime", "big_move_tier", "big_drop_risk", "activity_tier", "champion_action"]
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    pre = ColumnTransformer([
        ("num", Pipeline([("imp", SimpleImputer(strategy="median")), ("sc", StandardScaler())]), num),
        ("cat", Pipeline([("imp", SimpleImputer(strategy="most_frequent")),
                          ("oh", OneHotEncoder(handle_unknown="ignore"))]), cat)])
    cands = {"logistic": LogisticRegression(max_iter=1000, class_weight="balanced"),
             "histgb": HistGradientBoostingClassifier(max_iter=250, random_state=0)}
    try:
        from catboost import CatBoostClassifier
        cands["catboost"] = CatBoostClassifier(iterations=250, depth=5, learning_rate=0.05, verbose=0,
                                               random_seed=0, allow_writing_files=False)
    except Exception:
        pass
    cut = int(len(df) * SPLIT)
    Xtr, Xte = df.iloc[:cut], df.iloc[cut:]
    ytr, yte = y[:cut], y[cut:]
    base = float(yte.mean())
    L.append(f"\nTarget = 'the acted side held to resolution'. n={len(df):,} resolved · test base hold "
             f"**{base*100:.1f}%** · 70/30 temporal. Lift over base = the value of a skip filter.")
    L.append("\n| model | test AUC | top10% kept → hold | top25% kept → hold | base hold |")
    L.append("|---|---:|---:|---:|---:|")
    from sklearn.metrics import roc_auc_score
    for name, clf in cands.items():
        try:
            pipe = Pipeline([("pre", pre), ("clf", clf)]).fit(Xtr[num + cat], ytr)
            p = pipe.predict_proba(Xte[num + cat])[:, 1]
            auc = roc_auc_score(yte, p) if len(np.unique(yte)) > 1 else float("nan")
            order = np.argsort(p)[::-1]
            t10 = yte[order[:max(1, int(len(p) * 0.10))]].mean()
            t25 = yte[order[:max(1, int(len(p) * 0.25))]].mean()
            L.append(f"| {name} | {auc:.3f} | {t10*100:.1f}% | {t25*100:.1f}% | {base*100:.1f}% |")
        except Exception as e:
            L.append(f"| {name} | err: {str(e)[:50]} | | | |")
    L.append("\n_Read: if 'top-kept hold' clearly beats base hold, the meta-skip adds real decision value "
             "(act only on the kept fraction). Spread / bucket-quality features need the recorder + bucket "
             "join — not in this snapshot set yet._\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--split", type=float, default=0.70)
    a = ap.parse_args()
    global SPLIT
    SPLIT = min(0.9, max(0.5, a.split))
    if not os.path.exists(MATRIX):
        print(f"ERROR: {MATRIX} not found."); return
    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    rows = min(len(df), a.days * 1440)
    df = df.tail(rows).reset_index(drop=True)
    L = [f"# Ceiling-Break Experiments — {date.today().isoformat()}", "",
         f"Last **{a.days} days** ({len(df):,} 1m rows) · **{int(SPLIT*100)}/{int((1-SPLIT)*100)}** "
         f"temporal split (embargo {EMBARGO_BARS} bars) · spread {SPREAD_BPS} bps. "
         f"Honest scope: Exp 1 & 5 run on real data; Exp 2 is an order-flow PROXY (true L2 needs "
         f"record-forward); Exp 3 & 4 need recorder Polymarket history (skipped until rounds settle).", ""]
    for fn in (exp1_triple_barrier, exp2_newinfo_valueadd):
        try:
            fn(df, L)
        except Exception as e:
            L.append(f"\n_{fn.__name__} failed: {str(e)[:120]}_\n")
    for fn in (exp3_4_market_microstructure, exp5_meta_skip):
        try:
            fn(L)
        except Exception as e:
            L.append(f"\n_{fn.__name__} failed: {str(e)[:120]}_\n")
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    main()
