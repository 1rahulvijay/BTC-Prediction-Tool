"""
probe_bookdepth_liquidity.py - does free Binance futures bookDepth add predictive LIFT? (measure before wiring)
==============================================================================================================
Binance futures `bookDepth` (data.binance.vision, free, back to 2023) is 30s AGGREGATE cumulative depth at
+/-0.2/1/2/3/4/5% bands -- NOT tick L2. It gives resting-liquidity / book-imbalance context, which is genuinely
DIFFERENT information from realized vol/flow (it's the standing book, not the trades). This probe tests the honest
question the user posed: "is the book thin/fragile / is liquidity vanishing before a big move or drop?" -- i.e.
does the liquidity state add lift OVER a realized-vol baseline on clean causal targets.

Discipline (same as the flow probes, and after the fade-leak humbling): causal features at t only, matrix-native
FORWARD labels (no first-passage/touch-candle ambiguity), temporal 70/30, incremental AUC over an rv baseline,
and a shuffled-null on the bookDepth block. Research only -- nothing is wired live unless it clears the gate.
Honest prior: 30s aggregate depth is the same resolution as the flow/impact probes that all came back null, so
expect marginal; DEPTH is new info though, so it's a legitimate (not repeat) test.

Usage:
  python backend/probe_bookdepth_liquidity.py --days 90
  python backend/probe_bookdepth_liquidity.py --selftest
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
except Exception as e:
    print(f"sklearn required: {e}"); sys.exit(1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
MATRIX = os.path.join(DATA, "research_matrix_1m.parquet")
CACHE = os.path.join(DATA, "bookdepth_cache")
BASE = "https://data.binance.vision/data/futures/um/daily/bookDepth/BTCUSDT/BTCUSDT-bookDepth-{d}.zip"
OUT_MD = os.path.join(ROOT, "docs", "active", f"BOOKDEPTH_LIQUIDITY_PROBE_{date.today().isoformat()}.md")
EPS = 1e-9
LIQ = ["imb_0p2", "imb_1", "imb_2", "near_depth", "total_depth", "depth_slope", "near_notional",
       "depth_chg_30s", "depth_chg_2m", "vacuum_z", "imb_chg_30s"]
BASELINE = ["rv_15m", "rv_30m", "rv_60m", "range_15m"]


def _download_day(d: str) -> pd.DataFrame | None:
    os.makedirs(CACHE, exist_ok=True)
    pq = os.path.join(CACHE, f"bd-{d}.parquet")
    if os.path.exists(pq):
        try:
            return pd.read_parquet(pq)
        except Exception:
            pass
    try:
        with urllib.request.urlopen(BASE.format(d=d), timeout=90) as r:
            blob = r.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            with z.open(z.namelist()[0]) as f:
                df = pd.read_csv(f)
        df.to_parquet(pq, index=False)
        return df
    except Exception as e:
        print(f"  [skip {d}] {str(e)[:60]}")
        return None


def load_bookdepth(days: int, end: date) -> pd.DataFrame:
    """Return a WIDE per-timestamp frame: ts_ms + depth_/notional_ per signed % band."""
    parts = []
    for i in range(days):
        d = (end - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        day = _download_day(d)
        if day is not None and len(day):
            parts.append(day)
        if (i + 1) % 20 == 0:
            print(f"  fetched {i+1}/{days} bookDepth days ...", flush=True)
    if not parts:
        raise SystemExit("no bookDepth fetched")
    raw = pd.concat(parts, ignore_index=True)
    raw["ts_ms"] = pd.to_datetime(raw["timestamp"], utc=True).astype("int64") // 10**6
    wide = raw.pivot_table(index="ts_ms", columns="percentage", values=["depth", "notional"], aggfunc="last")
    wide.columns = [f"{a}_{str(b).replace('-','n').replace('.','p')}" for a, b in wide.columns]
    return wide.sort_index().reset_index()


def build_liquidity(wide: pd.DataFrame) -> pd.DataFrame:
    """Per-30s-snapshot liquidity-context features (all known at t). Cumulative depth: band 0.2 < 1 < ... < 5."""
    def col(kind, pct):
        c = f"{kind}_{pct}"
        return wide[c].astype(float) if c in wide.columns else pd.Series(np.nan, index=wide.index)
    out = pd.DataFrame({"ts_ms": wide["ts_ms"].values})
    bid02, ask02 = col("depth", "n0p2"), col("depth", "0p2")
    bid1, ask1 = col("depth", "n1p0"), col("depth", "1p0")
    bid2, ask2 = col("depth", "n2p0"), col("depth", "2p0")
    bid5, ask5 = col("depth", "n5p0"), col("depth", "5p0")
    out["imb_0p2"] = ((bid02 - ask02) / (bid02 + ask02 + EPS)).values      # >0 = bid-heavy (support)
    out["imb_1"] = ((bid1 - ask1) / (bid1 + ask1 + EPS)).values
    out["imb_2"] = ((bid2 - ask2) / (bid2 + ask2 + EPS)).values
    out["near_depth"] = (bid02 + ask02).values                             # near-mid resting liquidity
    out["total_depth"] = (bid5 + ask5).values                              # broad book support
    out["depth_slope"] = ((bid5 + ask5) / (bid02 + ask02 + EPS)).values    # how much deeper the far book is
    out["near_notional"] = (col("notional", "n0p2") + col("notional", "0p2")).values
    nd = out["near_depth"]
    out["depth_chg_30s"] = nd.pct_change().values                          # liquidity appearing(+)/vanishing(-)
    out["depth_chg_2m"] = nd.pct_change(4).values
    out["imb_chg_30s"] = out["imb_0p2"].diff().values
    roll = nd.rolling(120, min_periods=20)                                 # ~1h of 30s snaps
    out["vacuum_z"] = ((nd - roll.mean()) / (roll.std() + EPS)).values     # <0 = thin near book (vacuum)
    return out


def build_targets(mx: pd.DataFrame) -> pd.DataFrame:
    """Clean matrix-native FORWARD labels (no touch-candle/first-passage ambiguity)."""
    d = pd.DataFrame({"ts_ms": mx["ts_ms"].values})
    if "future_abs_move_5m" in mx:
        am = mx["future_abs_move_5m"].astype(float)
    else:
        am = (mx["ret_5m"].abs() if "ret_5m" in mx else pd.Series(np.nan, index=mx.index)).astype(float)
    d["big_move"] = (am >= am.quantile(0.75)).astype(float)
    if "future_low_5m" in mx and "close" in mx:
        d["big_drop"] = ((mx["future_low_5m"].astype(float) - mx["close"].astype(float)) <= -50.0).astype(float)
    else:
        d["big_drop"] = np.nan
    return d


def _fit_auc(X, cols, y, split):
    Xtr, Xte = X.iloc[:split][cols].to_numpy(float), X.iloc[split:][cols].to_numpy(float)
    ytr, yte = y[:split], y[split:]
    mtr = np.isfinite(Xtr).all(1) & np.isfinite(ytr); mte = np.isfinite(Xte).all(1) & np.isfinite(yte)
    if mtr.sum() < 500 or mte.sum() < 500 or len(np.unique(ytr[mtr])) < 2 or len(np.unique(yte[mte])) < 2:
        return float("nan")
    mu, sd = Xtr[mtr].mean(0), Xtr[mtr].std(0) + EPS
    clf = LogisticRegression(max_iter=200).fit((Xtr[mtr] - mu) / sd, ytr[mtr])
    p = clf.predict_proba((Xte[mte] - mu) / sd)[:, 1]
    return float(roc_auc_score(yte[mte], p))


def _uni_auc(y, s):
    m = np.isfinite(s) & np.isfinite(y)
    if m.sum() < 50 or len(np.unique(y[m])) < 2:
        return float("nan")
    try:
        return float(roc_auc_score(y[m], s[m]))
    except Exception:
        return float("nan")


def probe(df, target, n_null=100):
    d = df.replace([np.inf, -np.inf], np.nan).dropna(subset=BASELINE + LIQ + [target]).reset_index(drop=True)
    n = len(d); split = int(n * 0.7)
    y = d[target].to_numpy(int)
    base = _fit_auc(d, BASELINE, y, split)
    aug = _fit_auc(d, BASELINE + LIQ, y, split)
    lift = (aug - base) if np.isfinite(aug) and np.isfinite(base) else float("nan")
    uni = {c: _uni_auc(y, d[c].to_numpy(float)) for c in LIQ}
    rng = np.random.default_rng(0); null = []
    if np.isfinite(lift) and n_null:
        for _ in range(n_null):
            perm = d.copy(); perm[LIQ] = perm[LIQ].values[rng.permutation(len(perm))]
            a0 = _fit_auc(perm, BASELINE + LIQ, y, split)
            if np.isfinite(a0) and np.isfinite(base):
                null.append(a0 - base)
    null = np.array(null)
    return {"n": n, "base_rate": float(y.mean()), "base_auc": base, "aug_auc": aug, "lift": lift,
            "p": float((null >= lift).mean()) if len(null) and np.isfinite(lift) else float("nan"),
            "null95": float(np.quantile(null, 0.95)) if len(null) else float("nan"), "uni": uni}


def _f(x, nd=3):
    return f"{x:.{nd}f}" if isinstance(x, float) and np.isfinite(x) else "  -  "


def run(days):
    mx = pd.read_parquet(MATRIX, columns=["ts_ms", "close", "rv_15m", "rv_30m", "rv_60m", "range_15m",
                                          "future_abs_move_5m", "future_low_5m", "ret_5m"])
    mx = mx.sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True)
    end = datetime.fromtimestamp(mx["ts_ms"].max() / 1000, tz=timezone.utc).date()
    print(f"downloading {days} days of bookDepth ending {end} (within the matrix window) ...")
    wide = load_bookdepth(days, end)
    liq = build_liquidity(wide)
    # join 30s bookDepth -> 1m matrix (nearest snapshot at/before the bar), then targets
    liq = liq.sort_values("ts_ms").reset_index(drop=True)
    m = mx.merge_asof if False else pd.merge_asof(mx.sort_values("ts_ms"), liq, on="ts_ms",
                                                  direction="backward", tolerance=90_000)
    tg = build_targets(mx)
    df = m.merge(tg, on="ts_ms", how="left")
    cov = df[LIQ[0]].notna().mean()
    L = [f"# Binance bookDepth Liquidity Probe — {date.today().isoformat()}", "",
         f"Free Binance futures **bookDepth** (30s aggregate depth bands) joined to the 1m matrix "
         f"({days}d, join coverage {cov*100:.0f}%). Does resting-liquidity state add lift OVER an rv baseline on "
         f"clean forward labels? Causal, temporal 70/30, shuffled-null. Research only — nothing wired unless it clears.", ""]
    for tgt in ("big_move", "big_drop"):
        if df[tgt].notna().sum() < 2000:
            L.append(f"\n## {tgt}: too few labels — skipped"); continue
        r = probe(df, tgt)
        L.append(f"\n## Target: {tgt}  (n={r['n']:,}, base rate {100*r['base_rate']:.1f}%)")
        L.append(f"- rv baseline AUC **{_f(r['base_auc'])}**  ·  +bookDepth AUC **{_f(r['aug_auc'])}**  ·  "
                 f"LIFT **{('%+.3f'%r['lift']) if np.isfinite(r['lift']) else ' - '}**  "
                 f"(null95 {('%+.3f'%r['null95']) if np.isfinite(r['null95']) else ' - '}, p={_f(r['p'])})")
        best = sorted(r["uni"].items(), key=lambda kv: -(kv[1] if np.isfinite(kv[1]) else 0))[:4]
        L.append("- top univariate bookDepth features: " + ", ".join(f"{k}={_f(v)}" for k, v in best))
        sig = np.isfinite(r["lift"]) and r["lift"] >= 0.005 and np.isfinite(r["p"]) and r["p"] < 0.05
        L.append(f"- → **{'SIGNAL — bookDepth adds real lift' if sig else 'no lift over rv (liquidity state redundant here)'}**")
    L += ["\n## Verdict", "- bookDepth is free, real, 30s aggregate depth — a *liquidity-context* layer, not tick L2.",
          "- Wire the features into the live model ONLY if a target shows a significant lift above; otherwise keep as "
          "optional display context. The true microstructure edge still needs the record-forward diff-depth WS.",
          "- If SIGNAL: the live model would need the equivalent live depth feed (Binance depth WS) to deploy — the "
          "30s historical files are for backtesting/probing, not live serving."]
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(str(x) for x in L))
    print("\n".join(str(x) for x in L[3:]))
    print(f"\nWrote {OUT_MD}")


def selftest():
    rng = np.random.default_rng(0); n = 5000
    wide = pd.DataFrame({"ts_ms": np.arange(n) * 30000})
    for pct in ("n5p0", "n2p0", "n1p0", "n0p2", "0p2", "1p0", "2p0", "5p0"):
        wide[f"depth_{pct}"] = np.abs(rng.normal(1000, 200, n))
        wide[f"notional_{pct}"] = wide[f"depth_{pct}"] * 60000
    liq = build_liquidity(wide)
    assert set(LIQ).issubset(liq.columns) and len(liq) == n
    print(f"selftest: built {len(LIQ)} liquidity features from {n} snapshots, cols OK. PASS")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest(); return
    run(a.days)


if __name__ == "__main__":
    main()
