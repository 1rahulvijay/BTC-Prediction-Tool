"""
cond_stopping_m0.py — CONDITIONAL_STOPPING_V1, stage M0 (the cheapest kill)
===========================================================================
Runs ONLY the M0 gate declared in PREREG_CONDITIONAL_STOPPING_V1.md:

    "M0  empirical monotonic state table (the baseline that must be beaten by any model).
     If M0 does not stratify monotonically, STOP before fitting M1-M3."

Question: on the frozen entry population, does ANY single preregistered observable state variable
monotonically stratify the primary competing-risk target?

    primary target: bid improves >= 2c BEFORE bid deteriorates >= 2c, within the next 10s

If no variable stratifies, there is nothing for a model to condition on, and the lane closes
without fitting anything. If something does stratify, M1-M3 become permitted (still bound by the
full promotion gate and the untouched forward test).

DECLARED LIMITATION (from the prereg, section 14): the 21-day development window has NO bid size,
so exits are evaluated at qty=1 top-of-book. That is the most optimistic case and will OVERSTATE
every exit policy. Forward testing must run size-aware on the new full-ladder recording.

Independence: buckets are filled with post-entry book events (needed to populate a state table),
but every reported interval is clustered on the ROUND, and n_rounds is printed beside n_obs.
Treating events as independent would inflate n by ~10x and manufacture false confidence.

Read-only against the Oracle copies. ASCII output. CPU-polite.
Usage: python backend/research/cond_stopping_m0.py
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
OUT_MD = os.path.join(ROOT, "docs", "active", f"COND_STOPPING_M0_{date.today().isoformat()}.md")
FEE_RATE = 0.07
CHECKPOINTS = (240, 60)
ASK_LO, ASK_HI = 0.60, 0.97
TARGET_C = 0.02          # +/-2c competing risk
WINDOW_S = 10.0          # within 10 seconds
MIN_BUCKET = 100         # per the stratifier rule


def fee(p):
    return FEE_RATE * p * (1.0 - p)


def cluster_ci(y, groups):
    """Mean +/- 1.96 * cluster-robust SE (clustered on round). Events within a round correlate."""
    y = np.asarray(y, float)
    g = np.asarray(groups)
    m = y.mean()
    uniq = np.unique(g)
    if len(uniq) < 5:
        return m, np.nan
    sums = np.array([y[g == u].sum() - y[g == u].size * m for u in uniq])
    n = len(y)
    se = np.sqrt((sums ** 2).sum()) / n if n else np.nan
    return m, 1.96 * se


def main():
    import duckdb
    import pandas as pd
    exe = os.path.join(ORACLE, "execution_layer.duckdb")
    if not os.path.exists(exe):
        print(f"missing {exe}")
        return
    con = duckdb.connect(exe, read_only=True)
    snap = con.execute("""
        SELECT s.slug, s.horizon, s.ts, s.seconds_left, s.btc_price, s.anchor_price,
               s.up_bid, s.up_ask, s.down_bid, s.down_ask, s.up_spread, s.down_spread,
               s.up_top_ask_size, s.down_top_ask_size,
               CASE WHEN t.up_win = 1 THEN 'UP' ELSE 'DOWN' END AS won
        FROM pm_round_snapshots s JOIN pm_round_settlements t USING (slug)
        WHERE s.seconds_left >= 0
          AND s.up_bid IS NOT NULL AND s.down_bid IS NOT NULL
          AND s.up_ask IS NOT NULL AND s.down_ask IS NOT NULL
        ORDER BY s.slug, s.seconds_left DESC
    """).fetchdf()
    con.close()

    L = [f"# CONDITIONAL_STOPPING_V1 - M0 gate ({date.today().isoformat()})", "",
         "Stage M0 of the frozen preregistration (`PREREG_CONDITIONAL_STOPPING_V1.md`, "
         "sha256 `5fcae7b6...`). **If no preregistered state variable stratifies the primary "
         "target monotonically, the lane closes without fitting M1-M3.**", "",
         f"Primary target: **bid improves >= {int(TARGET_C*100)}c before it deteriorates "
         f">= {int(TARGET_C*100)}c, within {int(WINDOW_S)}s.** Entry population frozen "
         "(leader, ask 0.60-0.97, checkpoints 240s/60s). Development data has no bid size, so "
         "qty=1 top-of-book - the optimistic case, as declared.", ""]

    any_stratifies = False
    for horizon in (5, 15):
        hs = snap[snap["horizon"] == horizon]
        if hs.empty:
            continue
        for chk in CHECKPOINTS:
            rows = []
            for slug, g in hs.groupby("slug", sort=False):
                g = g.reset_index(drop=True)
                ent = g[g["seconds_left"] <= chk]
                if ent.empty:
                    continue
                i0 = ent.index[0]
                r0 = g.loc[i0]
                lead_up = r0["up_bid"] > r0["down_bid"]
                ask = float(r0["up_ask"] if lead_up else r0["down_ask"])
                if not (ASK_LO <= ask < ASK_HI):
                    continue
                entry_cost = ask + fee(ask)
                post = g.loc[i0:].reset_index(drop=True)
                bid = (post["up_bid"] if lead_up else post["down_bid"]).astype(float).to_numpy()
                spr = (post["up_spread"] if lead_up else post["down_spread"]).astype(float).to_numpy()
                secs = post["seconds_left"].astype(float).to_numpy()
                btc = post["btc_price"].astype(float).to_numpy()
                anch = float(r0["anchor_price"] or 0.0)
                n = len(bid)
                if n < 4:
                    continue
                net = bid - np.array([fee(b) for b in bid]) - entry_cost
                for i in range(1, n - 1):
                    # ---- label: competing risk within WINDOW_S, strictly forward ----
                    fut = np.where((secs[i] - secs) <= WINDOW_S)[0]
                    fut = fut[fut > i]
                    if len(fut) == 0:
                        continue
                    up_hit = np.where(net[fut] >= net[i] + TARGET_C)[0]
                    dn_hit = np.where(net[fut] <= net[i] - TARGET_C)[0]
                    if len(up_hit) == 0 and len(dn_hit) == 0:
                        continue                      # neither barrier: censored, excluded
                    if len(up_hit) and len(dn_hit):
                        y = int(up_hit[0] < dn_hit[0])
                    else:
                        y = int(len(up_hit) > 0)
                    # ---- causal features: strictly at/before i ----
                    rows.append({
                        "slug": slug, "y": y,
                        "net_pnl": net[i],
                        "bid_chg_1": bid[i] - bid[i - 1],
                        "bid_chg_3": bid[i] - bid[max(0, i - 3)],
                        "spread": spr[i],
                        "secs_left": secs[i],
                        "dist_anchor": abs(btc[i] - anch) if anch else np.nan,
                        "btc_move_1": abs(btc[i] - btc[i - 1]),
                    })
            if len(rows) < 500:
                continue
            R = pd.DataFrame(rows)
            base, _ = cluster_ci(R["y"], R["slug"])
            L += [f"## {horizon}m, entry {chk}s left "
                  f"(obs {len(R):,} / rounds {R['slug'].nunique():,}; base rate {base:.1%})", "",
                  "| state variable | monotone? | lowest bucket | highest bucket | spread |",
                  "|---|---|---|---|---|"]
            for feat in ("net_pnl", "bid_chg_1", "bid_chg_3", "spread", "secs_left",
                         "dist_anchor", "btc_move_1"):
                v = R[feat].to_numpy(float)
                if not np.isfinite(v).any():
                    continue
                try:
                    q = pd.qcut(R[feat].rank(method="first"), 5, labels=False, duplicates="drop")
                except Exception:
                    continue
                rates = []
                for b in sorted(pd.unique(q[~pd.isna(q)])):
                    m = q == b
                    if m.sum() < MIN_BUCKET:
                        rates = []
                        break
                    r, _ = cluster_ci(R.loc[m, "y"], R.loc[m, "slug"])
                    rates.append(r)
                if len(rates) < 4:
                    L.append(f"| {feat} | - | - | - | buckets too small |")
                    continue
                inc = all(rates[i] <= rates[i + 1] + 1e-9 for i in range(len(rates) - 1))
                dec = all(rates[i] >= rates[i + 1] - 1e-9 for i in range(len(rates) - 1))
                mono = inc or dec
                spread_pp = 100 * (rates[-1] - rates[0])
                if mono and abs(spread_pp) >= 5:
                    any_stratifies = True
                L.append(f"| {feat} | {'**YES**' if mono else 'no'} | {rates[0]:.1%} | "
                         f"{rates[-1]:.1%} | {spread_pp:+.1f}pp |")
            L.append("")

    L += ["## M0 verdict", ""]
    if any_stratifies:
        L += ["**At least one preregistered state variable stratifies the primary target "
              "monotonically with >=5pp spread and n>=100 per bucket.** Per the preregistration, "
              "M1-M3 are now permitted - bound by the full promotion gate, the declared 16-cell "
              "BH correction, and the 30-day untouched forward test.", "",
              "This is NOT evidence of profit. It says only that a conditional signal exists to "
              "model. The economic question - whether conditioning beats HOLD after ~3.5c of "
              "round-trip cost - remains open and is what the forward test decides."]
    else:
        L += ["**NO preregistered state variable stratifies the primary target monotonically.** "
              "Per the preregistration's own stop rule, `CONDITIONAL_STOPPING_V1` ends here: "
              "M1-M3 are NOT fitted, and the dynamic-exit lane closes.", "",
              "There is nothing for a model to condition on. A learner given only these features "
              "would be fitting noise, and it would still have to overcome ~3.5c of round-trip "
              "cost against a ~1c structural signal."]
    L += ["", "## Limits (declared in advance, prereg section 14)",
          "- qty=1 top-of-book exits: development data has no bid size. Optimistic by construction.",
          "- Buckets are filled with post-entry book events; all intervals are clustered on the "
          "round and n_rounds is reported beside n_obs.",
          "- Censored windows (neither barrier hit within 10s) are excluded from the label, which "
          "is the standard competing-risk treatment and is declared here rather than discovered.",
          "- Development sample only. It can close this lane; it can never promote it.", "",
          "**No model was fitted at this stage. Nothing here changes a threshold.**"]

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    main()
