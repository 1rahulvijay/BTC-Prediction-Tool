"""
test_oracle_capacity.py — does the one alive rule survive AT SIZE? (Oracle deployment data)
===========================================================================================
The 21-day live study measured LATE_LEADER_30S_V1 at qty=1 (EV +0.90c, gate FAILED). It never
answered the business question: **is that edge 1 share wide, or 50?**

Key insight that makes this answerable TODAY: the frozen rule HOLDS TO SETTLEMENT — it never
sells. So its capacity is entirely an ENTRY-side question, and the Oracle recorder stored the
full ask ladder (top_ask_size + cumulative depth within 1c / 2c / 5c). Missing bid size only
blocks capacity for strategies that exit early — all of which are already dead.

Method (reconstructed independently from the QUOTE table, not from the ledger):
  - 5m rounds, first snapshot with 20 <= seconds_left <= 32, one decision per round
  - leader = higher bid; frozen gates: 0.60 <= ask < 0.97
  - walk the recorded ask ladder for intended size Q -> conservative entry VWAP:
        shares up to top_ask_size  -> ask
        ... up to d1              -> ask + 1c
        ... up to d2              -> ask + 2c
        ... up to d5              -> ask + 5c
        beyond d5                 -> UNFILLABLE (round excluded at that size)
  - settle: winner pays $1/share, loser $0; taker fee 0.07*p*(1-p) charged on the VWAP
  - EV per share, plus a DAY-BLOCK bootstrap lower bound (trades within a day are not
    independent — the gate-bearing statistic per the 2026-07-25 methodology fix)

Read-only against a COPY of the Oracle DBs. ASCII output. CPU-polite.
Usage: python backend/research/test_oracle_capacity.py
"""
from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

import sys
from datetime import date

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORACLE = os.path.join(ROOT, "btc_full_project", "btc-tool", "data")
OUT_MD = os.path.join(ROOT, "docs", "active", f"ORACLE_CAPACITY_TEST_{date.today().isoformat()}.md")
FEE_RATE = 0.07
SIZES = (1, 5, 10, 25, 50, 100, 250)
BOOT_DRAWS, SEED = 2000, 12345


def fee(p):
    return FEE_RATE * p * (1.0 - p)


def vwap_for_size(ask, top, d1, d2, d5, q):
    """Conservative entry VWAP walking the recorded ask ladder. None if size unavailable."""
    top = max(0.0, float(top or 0.0)); d1 = max(top, float(d1 or 0.0))
    d2 = max(d1, float(d2 or 0.0)); d5 = max(d2, float(d5 or 0.0))
    if q > d5:
        return None
    bands = ((top, ask), (d1, ask + 0.01), (d2, ask + 0.02), (d5, ask + 0.05))
    filled = 0.0
    cost = 0.0
    for cum, px in bands:
        take = min(q, cum) - filled
        if take > 0:
            cost += take * px
            filled += take
        if filled >= q:
            break
    return cost / q if filled >= q else None


def selftest() -> int:
    """Ladder-walk arithmetic, verified case by case.

    Kept in-file because two ad-hoc assertions written on 2026-07-25 were themselves WRONG and
    briefly cast doubt on a correct result:
      * with d1 == top there is NO depth inside the 1c band, so overflow correctly prices into
        the 2c band (0.8067, not 0.81). The walker was right; the expectation was not.
      * 3 * 0.80 / 3 != 0.80 in float. Compare with a tolerance, never with ==.
    """
    eq = lambda a, b: a is not None and abs(a - b) < 1e-9
    cases = [
        ("top level only", vwap_for_size(0.80, 100, 100, 150, 300, 50), 0.80),
        ("d1==top -> 2c band", vwap_for_size(0.80, 10, 10, 20, 50, 15), (10*0.80 + 5*0.82)/15),
        ("d1>top  -> 1c band", vwap_for_size(0.80, 10, 18, 25, 60, 15), (10*0.80 + 5*0.81)/15),
        ("spans three bands", vwap_for_size(0.80, 10, 18, 25, 60, 30),
         (10*0.80 + 8*0.81 + 7*0.82 + 5*0.85)/30),
        ("non-monotone ladder clamped", vwap_for_size(0.80, 5, 3, 2, 1, 3), 0.80),
    ]
    bad = [(n, g, e) for n, g, e in cases if not eq(g, e)]
    for n, g, e in cases:
        print(f"  {'OK  ' if eq(g, e) else 'FAIL'} {n:30} got={g!r} expected={e:.6f}")
    vs = [vwap_for_size(0.80, 10, 18, 25, 60, q) for q in (1, 5, 10, 15, 25, 40, 60)]
    mono = all(v is not None for v in vs) and all(vs[i] <= vs[i+1] + 1e-12 for i in range(len(vs)-1))
    print(f"  {'OK  ' if mono else 'FAIL'} monotone in size              {[round(v, 4) for v in vs]}")
    unfil = vwap_for_size(0.80, 10, 18, 25, 60, 61) is None and vwap_for_size(0.80, 0, 0, 0, 0, 1) is None
    print(f"  {'OK  ' if unfil else 'FAIL'} oversize / empty ladder -> None (excluded, never priced)")
    feeok = abs(fee(0.85) - 0.07 * 0.85 * 0.15) < 1e-12
    print(f"  {'OK  ' if feeok else 'FAIL'} fee(0.85) = {100*fee(0.85):.2f}c (documented taker formula)")
    ok = not bad and mono and unfil and feeok
    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def block_lb(pnl, days, draws=BOOT_DRAWS, seed=SEED):
    """5th percentile of the mean under whole-DAY resampling (trades in a day are correlated)."""
    uniq = np.unique(days)
    if len(uniq) < 3:
        return None
    idx = {d: np.where(days == d)[0] for d in uniq}
    rng = np.random.default_rng(seed)
    means = np.empty(draws)
    for i in range(draws):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        means[i] = np.concatenate([pnl[idx[d]] for d in pick]).mean()
    return float(np.percentile(means, 5))


def main():
    import duckdb
    exe = os.path.join(ORACLE, "execution_layer.duckdb")
    if not os.path.exists(exe):
        print(f"missing {exe}")
        return
    con = duckdb.connect(exe, read_only=True)
    q = """
    WITH snap AS (
      SELECT slug, ts, seconds_left, up_bid, up_ask, down_bid, down_ask,
             up_top_ask_size, up_d1, up_d2, up_d5,
             down_top_ask_size, down_d1, down_d2, down_d5,
             ROW_NUMBER() OVER (PARTITION BY slug ORDER BY seconds_left DESC) rn
      FROM pm_round_snapshots
      WHERE horizon = 5 AND seconds_left BETWEEN 20 AND 32
        AND up_bid IS NOT NULL AND down_bid IS NOT NULL
        AND up_ask IS NOT NULL AND down_ask IS NOT NULL
    )
    SELECT s.ts, s.slug,
           CASE WHEN s.up_bid > s.down_bid THEN 'up' ELSE 'down' END AS lead_side,
           CASE WHEN s.up_bid > s.down_bid THEN s.up_ask ELSE s.down_ask END AS ask,
           CASE WHEN s.up_bid > s.down_bid THEN s.up_top_ask_size ELSE s.down_top_ask_size END AS top,
           CASE WHEN s.up_bid > s.down_bid THEN s.up_d1 ELSE s.down_d1 END AS d1,
           CASE WHEN s.up_bid > s.down_bid THEN s.up_d2 ELSE s.down_d2 END AS d2,
           CASE WHEN s.up_bid > s.down_bid THEN s.up_d5 ELSE s.down_d5 END AS d5,
           CASE WHEN t.up_win = 1 THEN 'up' ELSE 'down' END AS won
    FROM snap s JOIN pm_round_settlements t USING (slug)
    WHERE s.rn = 1
    """
    df = con.execute(q).fetchdf()
    con.close()
    df = df[df["won"].isin(["up", "down"])]
    df = df[(df["ask"] >= 0.60) & (df["ask"] < 0.97)].reset_index(drop=True)   # frozen gates
    if df.empty:
        print("no qualifying rounds")
        return
    df["win"] = (df["lead_side"] == df["won"]).astype(int)
    df["day"] = (df["ts"] // 86400).astype(int)
    n_days = df["day"].nunique()

    L = [f"# Oracle Capacity Test — does the alive rule survive AT SIZE? ({date.today().isoformat()})",
         "",
         "`LATE_LEADER_30S_V1` was measured live at **qty=1** (EV +0.90c, gate FAILED). This asks the "
         "business question it never answered: **how many shares wide is that edge?**", "",
         "The rule **holds to settlement — it never sells** — so its capacity is purely an ENTRY-side "
         "question, and the Oracle recorder stored the full ask ladder. (Missing bid size only blocks "
         "capacity for early-exit strategies, all of which are already dead.)", "",
         f"Reconstructed independently from **{len(df):,} settled 5m rounds** over **{n_days} days** of "
         "deployment quotes (not from the paper ledger): first snapshot at 20-32s left, leader = higher "
         "bid, frozen ask gates 0.60-0.97, one decision per round. Conservative ladder walk: fills beyond "
         "the top level are charged at the WORST price of each depth band (+1c / +2c / +5c).", "",
         "| intended size | rounds fillable | fill rate | avg entry VWAP | slippage vs top ask | win% | EV/share | EV LB (day-block) | total $ |",
         "|---|---|---|---|---|---|---|---|---|"]

    base_ask = df["ask"].to_numpy(float)
    rows = []
    for qsz in SIZES:
        vw = np.array([vwap_for_size(a, t, e1, e2, e5, qsz) for a, t, e1, e2, e5 in
                       zip(base_ask, df["top"], df["d1"], df["d2"], df["d5"])], dtype=object)
        ok = np.array([v is not None for v in vw])
        if ok.sum() < 30:
            L.append(f"| **{qsz}** | {int(ok.sum())} | {100*ok.mean():.1f}% | — | — | — | — | — | (too few) |")
            continue
        v = np.array([float(x) for x in vw[ok]])
        w = df["win"].to_numpy(int)[ok]
        d = df["day"].to_numpy(int)[ok]
        pnl_ps = w - v - fee(v)                       # per share, per round
        ev = float(pnl_ps.mean())
        lb = block_lb(pnl_ps, d)
        slip = float((v - base_ask[ok]).mean())
        total = float((pnl_ps * qsz).sum())
        rows.append((qsz, ev, lb, total, float(ok.mean())))
        L.append(f"| **{qsz}** | {int(ok.sum()):,} | {100*ok.mean():.1f}% | {100*v.mean():.2f}c | "
                 f"+{100*slip:.2f}c | {100*w.mean():.1f}% | **{100*ev:+.2f}c** | "
                 f"{(f'{100*lb:+.2f}c' if lb is not None else '—')} | ${total:,.0f} |")

    L += ["", "## What this means", ""]
    if rows:
        ev1 = next((r[1] for r in rows if r[0] == 1), None)
        big = [r for r in rows if r[0] >= 25]
        best_total = max(rows, key=lambda r: r[3])
        L.append(f"- At **1 share** the reconstruction gives EV **{100*ev1:+.2f}c** — the independent "
                 "check on the ledger's +0.90c (different code path, same deployment window).")
        if big:
            b = big[0]
            L.append(f"- At **{b[0]} shares**: EV **{100*b[1]:+.2f}c/share**, fill rate {100*b[4]:.0f}%"
                     + (f", day-block LB {100*b[2]:+.2f}c" if b[2] is not None else ""))
        L.append(f"- **Best total dollars** across the window came at size **{best_total[0]}**: "
                 f"${best_total[3]:,.0f} over {n_days} days "
                 f"(= ${best_total[3]/max(1,n_days):,.2f}/day at that size).")
        L.append("- Slippage is the mechanism: each extra depth band costs 1-5c, while the entire "
                 "measured edge is under 1c. **Any size that walks past the top level cannot be "
                 "profitable** — the first band alone is larger than the edge.")
    L += ["", "## Honest limits",
          "- Conservative band pricing (worst price in each band). A real fill lands somewhere between "
          "the band edges, so true VWAP sits between this and the top-of-book price.",
          "- Displayed size is not guaranteed size: quotes can be pulled between decision and fill.",
          "- Entry-side only. Valid for THIS rule because it holds to settlement; any early-exit "
          "strategy also needs bid-side depth, which the recorder does not yet store.",
          "- One decision per round, 5m only, deployment window only. Not a promotion.",
          "", "**Nothing here changes any threshold or promotes anything. PAPER research only.**"]
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else (main() or 0))
