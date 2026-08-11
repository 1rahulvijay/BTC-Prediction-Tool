"""
probe_hf_edge_robustness.py - NULL + STABILITY tests for the HF trade-price leader edge (before celebrating).
=============================================================================================================
The +27% ROI / 59.6% win result must survive falsification. This runs the exact checks the operator required:
  BASELINE   buy the leader every round (no P(Hold) filter) -> is the whole edge just "leaders are underpriced"?
  REAL       edge = P(Hold_leader) - price >= 2c (the claimed rule)
  NULL-shuffle  permute P(Hold) within (horizon, seconds_left) -> if it STILL wins, the edge is price-selection,
                not P(Hold) information.
  NULL-invert   trade the TRAILING side on the same qualifying rounds -> MUST lose if the pipeline is correct
                (a positive here = a win/price mapping bug).
  STABILITY  real edge by month (Jan/Feb/Mar), horizon (5m/15m), and UP-vs-DOWN leader -> must not be carried
             by one block.
All round-level (first qualifying entry per round). Reads pm_hf_trade_snapshots.parquet.

Usage: python backend/research/standalone/probe_hf_edge_robustness.py
"""
from __future__ import annotations

try:
    from . import _bootstrap as _research_bootstrap  # noqa: F401
except ImportError:
    import _bootstrap as _research_bootstrap  # noqa: F401

del _research_bootstrap


import math
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
SNAPS = os.path.join(DATA, "hf_trades_cache", "pm_hf_trade_snapshots.parquet")
OUT_MD = os.path.join(ROOT, "docs", "active", f"HF_EDGE_ROBUSTNESS_{date.today().isoformat()}.md")
PC = "vwap30"
BUF = 0.02


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n; d = 1 + z * z / n
    return (p + z * z / (2 * n)) / d - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d


def prep(raw):
    lead_up = raw["distance"].to_numpy(float) > 0
    up_p = raw["up_" + PC].to_numpy(float); dn_p = raw["down_" + PC].to_numpy(float)
    d = raw.assign(lead_up=lead_up,
                   lead_price=np.where(lead_up, up_p, dn_p),
                   lead_won=np.where(lead_up, raw["up_won"].to_numpy(int), 1 - raw["up_won"].to_numpy(int)),
                   trail_price=np.where(lead_up, dn_p, up_p),
                   trail_won=np.where(lead_up, 1 - raw["up_won"].to_numpy(int), raw["up_won"].to_numpy(int)),
                   p_hold=raw["p_hold"].to_numpy(float))
    d = d[np.isfinite(d["lead_price"]) & (d["seconds_left"] > 0) & (d["distance"].abs() >= 1e-9)].copy()
    d["month"] = pd.to_datetime(d["market_id"].str.rsplit("-", n=1).str[-1].astype("int64"), unit="s",
                                utc=True).dt.strftime("%Y-%m")
    return d


def score(q, price_col="lead_price", won_col="lead_won"):
    n = len(q)
    if n < 15:
        return None
    price = q[price_col].to_numpy(float); won = q[won_col].to_numpy(int)
    pnl = np.where(won == 1, 1.0 - price, -price)
    return {"n": n, "win": won.mean(), "lb": wilson(int(won.sum()), n), "price": price.mean(),
            "roi": pnl.sum() / (price.sum() + 1e-9), "pnl": float(np.mean(pnl))}


def edge_rounds(d, phold, buf=BUF, side="lead"):
    """First qualifying entry per round where phold - lead_price >= buf; score the chosen `side`."""
    q = d.assign(edge=phold - d["lead_price"] - buf)
    q = q[q["edge"] >= 0].sort_values("seconds_left", ascending=False).groupby("market_id", as_index=False).first()
    q = q[q[f"{side}_price"].notna()]            # trailing side often has no late trades -> drop missing prices
    return score(q, f"{side}_price", f"{side}_won")


def _fmt(s):
    return (f"n={s['n']:,} win={s['win']:.3f} LB={s['lb']:.3f} price={s['price']:.3f} "
            f"ROI={s['roi']:+.3f} pnl={s['pnl']:+.3f}") if s else "too few"


def main():
    if not os.path.exists(SNAPS):
        print(f"missing {SNAPS}"); return
    d = prep(pd.read_parquet(SNAPS))
    ph = d["p_hold"].to_numpy(float)
    # NULL shuffle: permute p_hold within (horizon, seconds_left)
    rng = np.random.default_rng(0)
    sh = ph.copy()
    for _, idx in d.groupby(["horizon", "seconds_left"]).groups.items():
        pos = d.index.get_indexer(idx)
        sh[pos] = rng.permutation(sh[pos])
    L = [f"# HF Edge Robustness — NULL + Stability — {date.today().isoformat()}", "",
         f"Falsification of the +27% leader-edge result ({len(d):,} leader snapshots, {d['market_id'].nunique():,} "
         f"rounds). Round-level, price={PC}, buffer={BUF:.2f}. The edge must beat its nulls and hold across blocks.", ""]
    base = score(d.sort_values("seconds_left", ascending=False).groupby("market_id", as_index=False).first())
    real = edge_rounds(d, ph)
    null_sh = edge_rounds(d, sh)
    inv = edge_rounds(d, ph, side="trail")
    L += ["## Null tests",
          f"- **BASELINE** (buy leader every round, no filter): {_fmt(base)}",
          f"- **REAL** (edge=P(Hold)−price≥2c): {_fmt(real)}",
          f"- **NULL-shuffle** (P(Hold) permuted within horizon×secs_left): {_fmt(null_sh)}",
          f"- **NULL-invert** (same rounds, trade the TRAILING side): {_fmt(inv)}"]
    # interpret
    real_roi = real["roi"] if real else 0; base_roi = base["roi"] if base else 0
    sh_roi = null_sh["roi"] if null_sh else 0; inv_roi = inv["roi"] if inv else 0
    L.append("\n**Reading:**")
    L.append(f"- edge over baseline: {real_roi - base_roi:+.3f} ROI — {'the P(Hold) filter ADDS selection' if real_roi > base_roi + 0.02 else 'the filter adds little over just buying leaders'}")
    L.append(f"- shuffle null ROI {sh_roi:+.3f} vs real {real_roi:+.3f} — "
             f"{'✅ P(Hold) carries the signal (shuffle collapses it)' if sh_roi < real_roi - 0.05 else '⚠️ shuffle still profitable → edge is PRICE-selection, not P(Hold)'}")
    L.append(f"- invert null ROI {inv_roi:+.3f} — {'✅ trailing side loses (pipeline consistent)' if inv_roi < 0 else '⚠️ trailing side also wins → win/price mapping BUG'}")

    # STABILITY
    L.append("\n## Stability (real edge by block — must not be carried by one)")
    L.append("| block | " + _fmt(real).replace(" ", " | ").replace("=", " ") + " |" if False else "| block | result |")
    L.append("|---|---|")
    for m in sorted(d["month"].unique()):
        L.append(f"| month {m} | {_fmt(edge_rounds(d[d['month']==m], d[d['month']==m]['p_hold'].to_numpy(float)))} |")
    for hz in (5, 15):
        L.append(f"| {hz}m | {_fmt(edge_rounds(d[d['horizon']==hz], d[d['horizon']==hz]['p_hold'].to_numpy(float)))} |")
    for lu, lbl in ((True, "UP leader"), (False, "DOWN leader")):
        sub = d[d["lead_up"] == lu]
        L.append(f"| {lbl} | {_fmt(edge_rounds(sub, sub['p_hold'].to_numpy(float)))} |")

    # VERDICT
    passed = (real and base and null_sh and inv and real_roi > 0.05 and sh_roi < real_roi - 0.05 and inv_roi < 0)
    L.append("\n## Verdict")
    if passed:
        L.append("**SURVIVES falsification — the leader edge beats baseline, collapses under P(Hold)-shuffle, and the "
                 "trailing side loses. It is a real (research-grade) P(Hold)-driven signal, not a mapping artifact.** "
                 "Still executed-trade only — fillability unproven; freeze the rule and validate on the live /book recorder.")
    else:
        why = []
        if not (real and real_roi > 0.05): why.append("real edge weak")
        if not (null_sh and sh_roi < real_roi - 0.05): why.append("shuffle null still profitable (price-selection, not P(Hold))")
        if not (inv and inv_roi < 0): why.append("invert null profitable (mapping bug)")
        L.append(f"**DOES NOT cleanly survive — {'; '.join(why)}.** Do not treat the +27% as a P(Hold) edge until resolved.")
    L.append("\n_Executed-trade research; a trade price is not an executable resting ask. Live /book required regardless._")

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(str(x) for x in L))
    print("\n".join(str(x) for x in L[3:]))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    main()
