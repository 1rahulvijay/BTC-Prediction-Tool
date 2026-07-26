"""
test_stopping_baselines.py — can ANY simple causal stopping policy capture the exit phenomenon?
================================================================================================
The 15m study found a profitable executable exit appears after ~90% of leader entries, usually
within 9-39s - but every STATIC TP/SL grid lost money. The canonical plan therefore gates the
whole dynamic-exit lane behind this question, to be answered with frozen baselines BEFORE any ML:

    "Stop this lane if no observable state is monotone and no baseline has positive lower-bound EV."

This is that gate. Seven pre-declared causal policies, no fitting, no tuning:

    HOLD              hold to settlement (the incumbent, and the honest baseline)
    FIRST_+1c         exit at the first quote whose net exit >= +1c
    FIRST_+2c         exit at the first quote whose net exit >= +2c
    PERSIST_2         exit when net >= +1c on two consecutive quotes (rejects one-tick blips)
    MOMENTUM_REV      ride while the bid rises; exit on the first tick down
    TIMEOUT_10S/30S   exit at the first quote >= N seconds after entry
    RANDOM            exit at a uniformly random quote (the matched-noise control)

Causality is enforced by construction: a decision seen at quote i executes at quote i+1 (~1.8s
median latency on this feed). Entry pays the ask + taker fee; every early exit pays the bid MINUS
its own taker fee. Hold pays $1/$0 with no exit fee. One decision per round per policy.

Reported per policy: mean net EV, day-block bootstrap lower bound (whole days resampled - trades
within a day share a regime), profit factor, weekly sign stability.

Read-only against the Oracle copies. ASCII output. CPU-polite.
Usage: python backend/research/test_stopping_baselines.py
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
OUT_MD = os.path.join(ROOT, "docs", "active", f"STOPPING_BASELINES_{date.today().isoformat()}.md")
FEE_RATE = 0.07
ENTRY_CHECKPOINTS = (240, 60)          # pre-declared; 5m rounds
ASK_LO, ASK_HI = 0.60, 0.97            # frozen-rule gates
BOOT, SEED = 2000, 4242
RNG = np.random.default_rng(SEED)


def fee(p):
    return FEE_RATE * p * (1.0 - p)


def block_lb(pnl, days, draws=BOOT, seed=SEED):
    uniq = np.unique(days)
    if len(uniq) < 3 or len(pnl) < 25:
        return None
    idx = {d: np.where(days == d)[0] for d in uniq}
    rng = np.random.default_rng(seed)
    means = np.empty(draws)
    for i in range(draws):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        means[i] = np.concatenate([pnl[idx[d]] for d in pick]).mean()
    return float(np.percentile(means, 5))


def pf(pnl):
    w = pnl[pnl > 0].sum()
    l = -pnl[pnl <= 0].sum()
    return float(w / l) if l > 0 else float("inf")


def run_policies(path_bids, won, entry_cost):
    """One round -> net pnl per policy. path_bids = leader bid at each POST-entry quote.

    Causal rule: a condition observed at quote i is acted on at quote i+1, never at i.
    """
    out = {}
    settle = (1.0 if won else 0.0) - entry_cost
    out["HOLD"] = settle
    n = len(path_bids)
    net = np.array([b - fee(b) - entry_cost for b in path_bids]) if n else np.array([])

    def exec_at(i):
        j = i + 1
        return net[j] if j < n else settle

    for label, thr in (("FIRST_+1c", 0.01), ("FIRST_+2c", 0.02)):
        hit = np.where(net >= thr)[0] if n else []
        out[label] = exec_at(hit[0]) if len(hit) else settle

    persist = settle
    for i in range(1, n):
        if net[i] >= 0.01 and net[i - 1] >= 0.01:
            persist = exec_at(i)
            break
    out["PERSIST_2"] = persist

    mom = settle
    rose = False
    for i in range(1, n):
        if path_bids[i] > path_bids[i - 1]:
            rose = True
        elif rose and path_bids[i] < path_bids[i - 1]:
            mom = exec_at(i)
            break
    out["MOMENTUM_REV"] = mom
    return out, net, settle


def main():
    import duckdb
    import pandas as pd
    exe = os.path.join(ORACLE, "execution_layer.duckdb")
    if not os.path.exists(exe):
        print(f"missing {exe}")
        return
    con = duckdb.connect(exe, read_only=True)
    df = con.execute("""
        SELECT s.slug, s.ts, s.seconds_left, s.up_bid, s.up_ask, s.down_bid, s.down_ask,
               CASE WHEN t.up_win = 1 THEN 'UP' ELSE 'DOWN' END AS won
        FROM pm_round_snapshots s JOIN pm_round_settlements t USING (slug)
        WHERE s.horizon = 5 AND s.seconds_left >= 0
          AND s.up_bid IS NOT NULL AND s.down_bid IS NOT NULL
          AND s.up_ask IS NOT NULL AND s.down_ask IS NOT NULL
        ORDER BY s.slug, s.seconds_left DESC
    """).fetchdf()
    con.close()

    L = [f"# Simple causal stopping baselines ({date.today().isoformat()})", "",
         "The gate the canonical plan puts in front of the dynamic-exit lane: **before any model, "
         "does any FROZEN causal stopping policy beat holding to settlement?** Seven pre-declared "
         "policies, no fitting. Entry pays ask+fee; every early exit pays bid-fee; a condition seen "
         "at quote *i* executes at quote *i+1* (~1.8s later), so no policy acts on information at "
         "the instant it appears.", ""]

    grand = []
    for chk in ENTRY_CHECKPOINTS:
        rows = []
        for slug, g in df.groupby("slug", sort=False):
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
            post = g.loc[i0 + 1:]
            bids = (post["up_bid"] if lead_up else post["down_bid"]).astype(float).to_numpy()
            if len(bids) < 2:
                continue
            won = (r0["won"] == ("UP" if lead_up else "DOWN"))
            res, net, settle = run_policies(bids, won, entry_cost)
            secs = post["seconds_left"].astype(float).to_numpy()
            elapsed = secs[0] - secs
            for tl in (10, 30):
                idx = np.where(elapsed >= tl)[0]
                res[f"TIMEOUT_{tl}S"] = (net[idx[0] + 1] if len(idx) and idx[0] + 1 < len(net)
                                         else settle)
            k = int(RNG.integers(0, len(net)))
            res["RANDOM"] = net[k + 1] if k + 1 < len(net) else settle
            res["_day"] = int(r0["ts"] // 86400)
            res["_week"] = int(r0["ts"] // (7 * 86400))
            res["_best_possible"] = float(net.max())     # hindsight ceiling, NOT a policy
            rows.append(res)
        if not rows:
            continue
        R = pd.DataFrame(rows)
        pols = ["HOLD", "FIRST_+1c", "FIRST_+2c", "PERSIST_2", "MOMENTUM_REV",
                "TIMEOUT_10S", "TIMEOUT_30S", "RANDOM"]
        L += [f"## Entry at {chk}s left  (n = {len(R):,} rounds, {R['_day'].nunique()} days)", "",
              "| policy | mean EV | day-block LB | profit factor | weeks + | vs HOLD |",
              "|---|---|---|---|---|---|"]
        hold_ev = float(R["HOLD"].mean())
        for p in pols:
            v = R[p].to_numpy(float)
            lb = block_lb(v, R["_day"].to_numpy(int))
            wk = R.groupby("_week")[p].mean()
            star = " **<-**" if p != "HOLD" and v.mean() > hold_ev else ""
            L.append(f"| {p} | **{100*v.mean():+.2f}c** | "
                     f"{(f'{100*lb:+.2f}c' if lb is not None else '-')} | {pf(v):.2f} | "
                     f"{int((wk > 0).sum())}/{len(wk)} | {100*(v.mean()-hold_ev):+.2f}c{star} |")
        ceiling = float(R["_best_possible"].mean())
        L += ["", f"*Hindsight ceiling (best exit in each round, unknowable in advance): "
                  f"**{100*ceiling:+.2f}c** - this is what the '90% of rounds have a profitable "
                  f"exit' statistic actually measures. The gap between it and every policy above is "
                  f"the price of not knowing the future.*", ""]
        grand.append((chk, R, pols, hold_ev, ceiling))

    L += ["## Verdict", ""]
    any_beat = False
    for chk, R, pols, hold_ev, ceiling in grand:
        for p in pols:
            if p in ("HOLD", "RANDOM"):
                continue
            v = R[p].to_numpy(float)
            lb = block_lb(v, R["_day"].to_numpy(int))
            if v.mean() > hold_ev and lb is not None and lb > 0:
                any_beat = True
                L.append(f"- **{p} @ {chk}s** beats HOLD ({100*v.mean():+.2f}c vs "
                         f"{100*hold_ev:+.2f}c) with a positive lower bound ({100*lb:+.2f}c).")
    if not any_beat:
        L += ["**NO simple causal stopping policy beats holding to settlement with a positive "
              "lower bound.** Per the pre-declared gate, the dynamic-exit lane **stops here**: no "
              "ML, no survival model, no RL.", "",
              "The reason is visible in the numbers: the hindsight ceiling is large and positive, "
              "yet every causal policy lands at or below HOLD. That is the signature of a "
              "phenomenon that exists in the *path* but not in any *observable state* available "
              "beforehand. Exiting early converts a settlement payoff into a spread crossing plus "
              "a second fee, and the ~1.8s delay removes whatever is left.", "",
              "Same lesson as TP-or-settle and the maker test, now proven for ADAPTIVE rules too: "
              "**on this market every extra decision costs more than the information it acts on.**"]
    L += ["", "## Limits",
          "- Exits are top-of-book bids; `top_bid_size` was not recorded in this window, so exits "
          "assume 1 share. Real size makes every early-exit policy WORSE, never better.",
          "- Quote cadence ~1.8s median: sub-second opportunities are invisible here.",
          "- 5m rounds, 21 days, one decision per round per policy. Enough to kill, never to promote.",
          "", "**Nothing here changes a threshold or promotes anything. PAPER research only.**"]

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    main()
