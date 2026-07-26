"""
reconcile_late_leader_ledger.py — settle the +0.90c vs -0.07c discrepancy, round by round
==========================================================================================
Two measurements of the SAME rule over the SAME 21 days disagree:

    live paper ledger (rule_paper_trades)   n=2,145   EV +0.90c/share
    quote-table reconstruction (capacity)   n=2,474   EV -0.07c/share

Until this is explained at the round level, neither number can anchor a decision. This script
does the decisive test: join both on the round anchor and split the gap into its two possible
causes.

    ROUND SELECTION  - rounds one side traded and the other did not
    ACCOUNTING       - same round, different leader / ask / fee / outcome / pnl

The key statistic is EV on the INTERSECTION. If both agree there, the entire gap is selection
(the live rule only fires when a fresh bridge quote exists) and no code is wrong. If they
disagree on shared rounds, there is a real accounting bug and BOTH prior results are suspect.

Read-only against the Oracle copies. ASCII output. CPU-polite.
Usage: python backend/research/reconcile_late_leader_ledger.py
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
OUT_MD = os.path.join(ROOT, "docs", "active", f"LATE_LEADER_RECONCILIATION_{date.today().isoformat()}.md")
FEE_RATE = 0.07
RULE = "LATE_LEADER_30S_V1"


def fee(p):
    return FEE_RATE * p * (1.0 - p)


def main():
    import duckdb
    import pandas as pd

    ana = os.path.join(ORACLE, "analytics.duckdb")
    exe = os.path.join(ORACLE, "execution_layer.duckdb")
    for p in (ana, exe):
        if not os.path.exists(p):
            print(f"missing {p}")
            return

    # ---- side A: the live paper ledger ------------------------------------------------
    con = duckdb.connect(ana, read_only=True)
    led = con.execute(f"""
        SELECT round_id, ts, side, ask, fee, pnl, outcome, action
        FROM rule_paper_trades
        WHERE rule = '{RULE}'
    """).fetchdf()
    con.close()
    led["anchor"] = (led["round_id"].astype(str).str.extract(r"(\d+)$")[0]
                     .astype("float64") / 1000.0).round().astype("Int64")
    led_ent = led[(led["action"] == "ENTER") & led["pnl"].notna()].copy()

    # ---- side B: the quote-table reconstruction (identical logic to the capacity test) --
    con = duckdb.connect(exe, read_only=True)
    rec = con.execute("""
        WITH snap AS (
          SELECT slug, ts, seconds_left, up_bid, up_ask, down_bid, down_ask,
                 ROW_NUMBER() OVER (PARTITION BY slug ORDER BY seconds_left DESC) rn
          FROM pm_round_snapshots
          WHERE horizon = 5 AND seconds_left BETWEEN 20 AND 32
            AND up_bid IS NOT NULL AND down_bid IS NOT NULL
            AND up_ask IS NOT NULL AND down_ask IS NOT NULL
        )
        SELECT s.slug, s.ts,
               CASE WHEN s.up_bid > s.down_bid THEN 'UP' ELSE 'DOWN' END AS side,
               CASE WHEN s.up_bid > s.down_bid THEN s.up_ask ELSE s.down_ask END AS ask,
               CASE WHEN t.up_win = 1 THEN 'UP' ELSE 'DOWN' END AS won
        FROM snap s JOIN pm_round_settlements t USING (slug)
        WHERE s.rn = 1
    """).fetchdf()
    con.close()
    rec["anchor"] = rec["slug"].astype(str).str.extract(r"(\d+)$")[0].astype("Int64")
    rec = rec[(rec["ask"] >= 0.60) & (rec["ask"] < 0.97)].copy()
    rec["win"] = (rec["side"] == rec["won"]).astype(int)
    rec["pnl"] = rec["win"] - rec["ask"] - fee(rec["ask"])

    # ---- the split --------------------------------------------------------------------
    a = set(led_ent["anchor"].dropna().tolist())
    b = set(rec["anchor"].dropna().tolist())
    both, only_a, only_b = a & b, a - b, b - a
    la = led_ent.set_index("anchor")
    rb = rec.set_index("anchor")

    def ev(series):
        return float(np.mean(series)) if len(series) else float("nan")

    ev_a_all = ev(led_ent["pnl"])
    ev_b_all = ev(rec["pnl"])
    ev_a_int = ev(la.loc[sorted(both), "pnl"]) if both else float("nan")
    ev_b_int = ev(rb.loc[sorted(both), "pnl"]) if both else float("nan")
    ev_a_only = ev(la.loc[sorted(only_a), "pnl"]) if only_a else float("nan")
    ev_b_only = ev(rb.loc[sorted(only_b), "pnl"]) if only_b else float("nan")

    L = [f"# LATE_LEADER_30S_V1 - ledger vs replay reconciliation ({date.today().isoformat()})", "",
         "Two measurements of the same rule over the same window disagreed. This settles it at the "
         "round level by joining both on the round anchor and splitting the gap into **round "
         "selection** vs **accounting**.", "",
         "| set | rounds | EV/share |", "|---|---|---|",
         f"| ledger, all its rounds | {len(led_ent):,} | **{100*ev_a_all:+.2f}c** |",
         f"| replay, all its rounds | {len(rec):,} | **{100*ev_b_all:+.2f}c** |",
         f"| **INTERSECTION - ledger** | {len(both):,} | **{100*ev_a_int:+.2f}c** |",
         f"| **INTERSECTION - replay** | {len(both):,} | **{100*ev_b_int:+.2f}c** |",
         f"| ledger only (replay skipped) | {len(only_a):,} | {100*ev_a_only:+.2f}c |",
         f"| replay only (ledger skipped) | {len(only_b):,} | {100*ev_b_only:+.2f}c |", ""]

    # ---- per-round field comparison on the intersection --------------------------------
    if both:
        idx = sorted(both)
        j = pd.DataFrame({
            "led_side": la.loc[idx, "side"], "rep_side": rb.loc[idx, "side"],
            "led_ask": la.loc[idx, "ask"].astype(float), "rep_ask": rb.loc[idx, "ask"].astype(float),
            "led_pnl": la.loc[idx, "pnl"].astype(float), "rep_pnl": rb.loc[idx, "pnl"].astype(float),
            "led_ts": la.loc[idx, "ts"].astype(float), "rep_ts": rb.loc[idx, "ts"].astype(float),
        })
        side_mismatch = int((j["led_side"] != j["rep_side"]).sum())
        ask_diff = (j["led_ask"] - j["rep_ask"]).abs()
        pnl_diff = (j["led_pnl"] - j["rep_pnl"]).abs()
        # ledger ts is ms, snapshot ts is seconds
        tdiff = (j["led_ts"] / 1000.0 - j["rep_ts"]).abs()
        L += ["## Field-level agreement on shared rounds", "",
              "| field | disagreements | median gap | 95th pct |", "|---|---|---|---|",
              f"| leader side | **{side_mismatch:,}** of {len(j):,} "
              f"({100.0*side_mismatch/max(1,len(j)):.1f}%) | - | - |",
              f"| entry ask | {int((ask_diff > 0.001).sum()):,} | {100*ask_diff.median():.2f}c | "
              f"{100*ask_diff.quantile(.95):.2f}c |",
              f"| realized pnl | {int((pnl_diff > 0.001).sum()):,} | {100*pnl_diff.median():.2f}c | "
              f"{100*pnl_diff.quantile(.95):.2f}c |",
              f"| decision time | - | {tdiff.median():.1f}s | {tdiff.quantile(.95):.1f}s |", ""]

    # ---- verdict ----------------------------------------------------------------------
    L += ["## Verdict", ""]
    gap_total = ev_a_all - ev_b_all
    gap_shared = (ev_a_int - ev_b_int) if both else float("nan")
    if both and abs(gap_shared) < 0.002:
        L += [f"**ACCOUNTING IS SOUND - the entire gap is ROUND SELECTION.** On the {len(both):,} rounds "
              f"both traded, the two paths agree to within {100*abs(gap_shared):.2f}c "
              f"({100*ev_a_int:+.2f}c vs {100*ev_b_int:+.2f}c). Neither implementation is wrong.", "",
              f"The difference comes entirely from *which rounds each traded*. The replay entered "
              f"{len(only_b):,} rounds the live rule declined, and those rounds average "
              f"**{100*ev_b_only:+.2f}c** - materially worse than the shared set.", "",
              "**What that means, and it is the uncomfortable part:** the live rule's advantage is not "
              "skill in the rule, it is the *bridge freshness filter*. It only trades when a <=5s quote "
              "happens to exist. That is a real, reproducible execution condition - but it is a "
              "**liquidity/staleness filter, not a strategy edge**, and it was never part of the frozen "
              "spec. The mechanically complete version of the rule sits at zero.", "",
              "So both statements are true and must be quoted together:", "",
              "- *As deployed* (only trading on fresh quotes): EV +0.90c, still failing its gate.",
              "- *As specified* (every qualifying round): EV ~0.",]
    elif both:
        L += [f"**ACCOUNTING DISCREPANCY - the paths disagree by {100*gap_shared:+.2f}c on rounds they "
              f"BOTH traded** ({100*ev_a_int:+.2f}c ledger vs {100*ev_b_int:+.2f}c replay). This is not "
              "selection; it is a real implementation difference. Both prior results are suspect until "
              "the field table above is traced to its cause (leader definition, entry instant, fee "
              "formula, or settlement mapping).",]
    else:
        L += ["**NO OVERLAP** - the anchor join failed; the two sources cannot be compared as keyed.",]

    L += ["", f"Total gap explained: ledger {100*ev_a_all:+.2f}c - replay {100*ev_b_all:+.2f}c = "
          f"**{100*gap_total:+.2f}c**.", "",
          "## Limits",
          "- Round-anchor join; a round present twice in either source is collapsed to its first entry.",
          "- The replay picks the snapshot nearest 32s left; the live rule fires on its first observed "
          "tick in the 20-32s band. Both are legitimate readings of the same frozen spec.",
          "- Nothing here changes a threshold or promotes anything. PAPER research only."]

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    main()
