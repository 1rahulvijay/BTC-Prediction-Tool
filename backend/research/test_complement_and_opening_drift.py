"""
test_complement_and_opening_drift.py — two structural (non-conditional) edge hunts
==================================================================================
Both target STRUCTURE, not round-picking — the only species that has ever survived here.

TEST 1 — COMPLEMENT ARBITRAGE (riskless by construction)
  If UP_ask + DOWN_ask + both fees < $1.00, buying BOTH sides locks a guaranteed profit:
  exactly one leg pays $1 at settlement. No model, no direction, no hold risk. Question is
  purely: how OFTEN does the book cross that line, and by how much?

TEST 2 — NEXT-ROUND OPENING DRIFT (cross-round momentum)
  After a strong one-way round N, round N+1 opens with a fresh ~50/50 book. Does the market
  "reset" too completely — is the continuation side underpriced at the open? Same species as
  the late-leader edge (a structural repricing lag at a boundary). Never tested before.
  Entry at the real opening ask (earliest tick ≥ 200s left), hold to settlement, fees included.
  Controls: the OPPOSITE (reversal) side is the mirror; a random-side control bounds noise.

Data: Kaggle archive (7) — per-second executable bid/ask both sides + UMA outcomes, 5m rounds.
Round-level, one decision per round, Wilson lower bounds, fee = 0.07·p·(1−p) per leg.
CPU-polite (2 threads) — a 1,500-day retrain is running.

Usage: python backend/research/test_complement_and_opening_drift.py
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
OUT_MD = os.path.join(ROOT, "docs", "active", f"STRUCTURAL_EDGE_HUNT_{date.today().isoformat()}.md")
FEE = 0.07


def fee(a):
    return FEE * a * (1.0 - a)


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    return (p + z * z / (2 * n)) / d - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d


def ev_row(label, ask, win):
    n = len(win)
    if n < 25:
        return f"| {label} | {n} (too few) | — | — | — |"
    w, a = float(np.mean(win)), float(np.mean(ask))
    lb = wilson(int(np.sum(win)), n)
    ev = w - a - fee(a)
    evlb = lb - a - fee(a)
    return (f"| {label} | {n:,} | {100*w:.1f}% (LB {100*lb:.1f}%) | {100*a:.1f}c "
            f"| **{100*ev:+.2f}c** (LB {100*evlb:+.2f}c) |")


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
    mk = mk[(mk["end_ms"] - mk["start_ms"]).between(290_000, 310_000)].copy()   # 5m only
    mk["end_t"] = mk["end_ms"] // 1000
    tk = tk.merge(mk[["condition_id", "end_t"]], on="condition_id", how="inner")
    tk["secs_left"] = tk["end_t"] - tk["t"]
    tk = tk[(tk["secs_left"] >= 0) & (tk["secs_left"] <= 300)]
    return mk, tk


def test_complement(mk, tk, L):
    """Scan EVERY tick for UP_ask + DOWN_ask + fees < 1 (guaranteed-profit crossings)."""
    au, ad = tk["au"].to_numpy(float), tk["ad"].to_numpy(float)
    ok = (au > 0.01) & (au < 0.99) & (ad > 0.01) & (ad < 0.99)
    cost = au + ad + fee(au) + fee(ad)
    prof = 1.0 - cost
    hit = ok & (prof > 0)
    n_tick, n_hit = int(ok.sum()), int(hit.sum())
    L += ["## TEST 1 — Complement arbitrage (riskless by construction)", "",
          "Buy BOTH sides when `UP_ask + DOWN_ask + fees < $1.00`; exactly one leg pays $1 at "
          "settlement, so the profit is locked at entry. No model, no direction, no hold risk.", "",
          f"- Ticks scanned (both sides quoted): **{n_tick:,}**",
          f"- Ticks where the book crossed into guaranteed profit: **{n_hit:,}** "
          f"({100.0*n_hit/max(1,n_tick):.4f}%)"]
    if n_hit:
        p = prof[hit]
        rounds_hit = tk.loc[hit, "condition_id"].nunique()
        secs = tk.loc[hit, "secs_left"]
        L += [f"- Distinct rounds with ≥1 crossing: **{rounds_hit:,}** of {mk.shape[0]:,} "
              f"({100.0*rounds_hit/max(1,mk.shape[0]):.2f}%)",
              f"- Locked profit per share-pair: mean **{100*p.mean():.2f}c**, "
              f"median {100*np.median(p):.2f}c, max {100*p.max():.2f}c",
              f"- Total if every crossing were taken once at 1 pair: **${p.sum():.2f}**",
              f"- When they happen: median {secs.median():.0f}s left "
              f"(25th–75th pct {secs.quantile(.25):.0f}s–{secs.quantile(.75):.0f}s)", ""]
        big = p[p > 0.02]
        L.append(f"- Crossings worth >2c: **{len(big):,}** (mean {100*big.mean() if len(big) else 0:.2f}c)"
                 if len(big) else "- Crossings worth >2c: **0** — all crossings are sub-2c dust.")
        L += ["", "**Read:** these are real, riskless-at-entry prints, but the honest caveats are "
              "(a) top-of-book only — size at those asks may be tiny, (b) you must fill BOTH legs "
              "within the same second or the lock breaks, and (c) 1s cadence may miss/overstate "
              "transient crossings. Treat the rate as an upper bound on opportunity and the size "
              "as unknown until the live L2 recorder measures depth at the crossing moment."]
    else:
        L += ["", "**Read: the book never crosses into guaranteed profit.** The market makers keep "
              "UP+DOWN ≥ $1 after fees at all times in this sample — no complement arbitrage exists "
              "here. Clean negative; do not build a scanner."]
    L.append("")


def test_opening_drift(mk, tk, L):
    """Round N's move → is round N+1's continuation side underpriced at the open?"""
    mk = mk.sort_values("start_ms").reset_index(drop=True)
    first = (tk[tk["secs_left"] >= 200].sort_values(["condition_id", "secs_left"], ascending=[True, False])
             .drop_duplicates("condition_id").set_index("condition_id"))
    mk = mk.join(first[["au", "ad", "bu", "bd", "secs_left"]], on="condition_id").dropna(subset=["au", "ad"])
    prev_out = mk["outcome"].shift(1)
    prev_gap = (mk["start_ms"] - mk["end_ms"].shift(1)).abs()
    valid = prev_out.notna() & (prev_gap <= 120_000)          # consecutive rounds only
    d = mk[valid].copy()
    d["prev"] = prev_out[valid]
    cont_up = d["prev"].eq("up")
    d["cont_ask"] = np.where(cont_up, d["au"], d["ad"])       # continuation side (same as prev winner)
    d["rev_ask"] = np.where(cont_up, d["ad"], d["au"])        # reversal side (mirror control)
    d["cont_win"] = np.where(cont_up, d["outcome"].eq("up"), d["outcome"].eq("down")).astype(int)
    d = d[(d["cont_ask"] > 0.02) & (d["cont_ask"] < 0.98)
          & (d["rev_ask"] > 0.02) & (d["rev_ask"] < 0.98)]
    L += ["## TEST 2 — Next-round opening drift (cross-round momentum)", "",
          "After round N settles, does round N+1's OPENING book underprice the continuation side "
          "(the side that just won)? Entry at the real opening ask (first tick ≥200s left), hold to "
          "settlement, fees included, one decision per round. The reversal side is the exact mirror; "
          "if the market is efficient at the open, BOTH should sit at ≈0 EV.", "",
          f"Consecutive round pairs: **{len(d):,}**", "",
          "| arm | rounds | win% (Wilson LB) | avg ask | EV/share |", "|---|---|---|---|---|"]
    L.append(ev_row("CONTINUATION (buy prev winner's side)", d["cont_ask"].to_numpy(),
                    d["cont_win"].to_numpy()))
    L.append(ev_row("REVERSAL (mirror control)", d["rev_ask"].to_numpy(),
                    1 - d["cont_win"].to_numpy()))
    rng = np.random.default_rng(11)
    pick = rng.random(len(d)) < 0.5
    L.append(ev_row("RANDOM side (noise control)",
                    np.where(pick, d["cont_ask"], d["rev_ask"]).astype(float),
                    np.where(pick, d["cont_win"], 1 - d["cont_win"]).astype(int)))
    # split by how decisive the previous round was (opening asks tell us the market's own view)
    L += ["", "### Split by the market's own opening confidence", "",
          "| opening ask on the continuation side | rounds | win% (LB) | EV/share |", "|---|---|---|---|"]
    for lo, hi in ((0.02, 0.45), (0.45, 0.50), (0.50, 0.55), (0.55, 0.98)):
        m = d[(d["cont_ask"] >= lo) & (d["cont_ask"] < hi)]
        if len(m) < 25:
            L.append(f"| {lo:.2f}–{hi:.2f} | {len(m)} (too few) | — | — |")
            continue
        a, w = m["cont_ask"].to_numpy(), m["cont_win"].to_numpy()
        wr, am = float(w.mean()), float(a.mean())
        lb = wilson(int(w.sum()), len(w))
        L.append(f"| {lo:.2f}–{hi:.2f} | {len(m):,} | {100*wr:.1f}% (LB {100*lb:.1f}%) | "
                 f"**{100*(wr-am-fee(am)):+.2f}c** |")
    a, w = d["cont_ask"].to_numpy(), d["cont_win"].to_numpy()
    ev = float(w.mean()) - float(a.mean()) - fee(float(a.mean()))
    lbev = wilson(int(w.sum()), len(w)) - float(a.mean()) - fee(float(a.mean()))
    L += ["", "### Verdict"]
    if lbev > 0:
        L.append(f"**POSITIVE with a positive lower bound** (EV {100*ev:+.2f}c, LB {100*lbev:+.2f}c). "
                 "A structural opening-lag edge would be a genuinely new find — but before ANY use it "
                 "needs: week-by-week stability, a latency model (the open is a fast tape), depth at "
                 "the opening ask, and live replication. Do not wire on this alone.")
    elif ev > 0:
        L.append(f"**Positive point estimate, lower bound NOT clear** (EV {100*ev:+.2f}c, "
                 f"LB {100*lbev:+.2f}c) — indistinguishable from noise at this n. Not an edge yet; "
                 "worth re-checking when the live recorder has its own opening quotes.")
    else:
        L.append(f"**NEGATIVE — the opening book is efficient** (EV {100*ev:+.2f}c, LB {100*lbev:+.2f}c). "
                 "The market fully resets between rounds: no cross-round momentum to harvest. Clean "
                 "kill; the boundary-lag species does NOT generalize from the expiry boundary to the "
                 "round-open boundary.")
    L.append("")


def main():
    if not os.path.exists(ZIP):
        print(f"missing {ZIP}")
        return
    mk, tk = load()
    L = [f"# Structural Edge Hunt — Complement Arb & Opening Drift ({date.today().isoformat()})", "",
         f"Two STRUCTURAL (non-conditional) hunts on Kaggle archive (7): {len(mk):,} settled BTC 5m "
         f"rounds, {len(tk):,} executable tick observations. Both ask about market MECHANICS, not "
         "round-picking — the only species that has survived testing here. Fees "
         "`0.07·p·(1−p)` per leg; round-level; Wilson lower bounds.", ""]
    test_complement(mk, tk, L)
    test_opening_drift(mk, tk, L)
    L += ["## Honest limits",
          "- Top-of-book only: no size/depth, so fillability at the quoted asks is unproven.",
          "- 1-second cadence: sub-second crossings and opening prints are invisible.",
          "- No latency model: the round open is the fastest tape of the round.",
          "- Historical window; live replication on the recorder remains the only real proof.",
          "- Nothing here is wired to any live behavior. PAPER research only."]
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    main()
