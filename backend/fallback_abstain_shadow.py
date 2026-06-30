"""
fallback_abstain_shadow.py — SHADOW test: abstain the fallback tilt in TRENDING regimes (read-only).
=====================================================================================================
Context: price_to_beat surfaces a side every window because Polymarket has no neutral outcome. When the
model's 3-class argmax is NEUTRAL, `_bet_lean()` still tilts a side from the two-way P(up)/P(down)
(lean_source='fallback'). The code already documents these fallback leans as ~coin-flip. The 2026-06-21
metrics analysis showed TRENDING regimes are the weakest cells, and a live 15m card (7 rounds, fallback
0/4) showed fallback tilts getting run over in a clean downtrend.

This script does NOT change `_bet_lean` or any live decision. It replays logged `price_to_beat` rounds and
asks: if we had surfaced NEUTRAL (abstained) for fallback leans in TRENDING regimes, what happens to the
accuracy / coverage / Wilson-LB of the calls that REMAIN? Re-run any time; it picks up new rounds.

Promote a policy only when (a) the abstained set is genuinely weak (≈ coin-flip or worse) AND (b) the
retained set's Wilson-LB improves and holds as n grows — then wire it (with sign-off) into `_bet_lean`.

Usage:  python backend/fallback_abstain_shadow.py
"""
from __future__ import annotations

import math
import os
import sys
from datetime import date, datetime, timezone

import duckdb

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
DB = os.path.join(DATA, "analytics.duckdb")
OUT = os.path.join(ROOT, "docs", "active", f"FALLBACK_ABSTAIN_SHADOW_{date.today().isoformat()}.md")

TRENDING = {"TRENDING_UP", "TRENDING_DOWN"}

# policy -> predicate(lean_source, regime) -> True means ABSTAIN (surface NEUTRAL, drop the call)
POLICIES = {
    "baseline (keep all)":                  lambda ls, rg: False,
    "abstain fallback in TRENDING":         lambda ls, rg: ls == "fallback" and rg in TRENDING,
    "abstain fallback in TRENDING+HIGH_VOL": lambda ls, rg: ls == "fallback" and rg in (TRENDING | {"HIGH_VOLATILITY"}),
    "abstain ALL fallback leans":           lambda ls, rg: ls == "fallback",
}


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    cen = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (cen - half), 100 * (cen + half))


def md(rows, headers):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join("–" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v)) for v in r) + " |")
    return "\n".join(out)


def acc_lb(hits):
    n = len(hits); k = sum(hits)
    return n, (100 * k / n if n else 0.0), wilson(k, n)[0]


def main():
    if not os.path.exists(DB):
        print("no analytics.duckdb"); return
    try:
        c = duckdb.connect(DB, read_only=True)
    except Exception as e:
        print(f"analytics.duckdb is locked by the running app — re-run when free (or briefly pause the "
              f"backend). DuckDB blocks cross-process reads while it holds the write lock.\n  ({str(e)[:90]})")
        return
    rows = c.execute("""
        SELECT timestamp, horizon, COALESCE(lean_source,'model') ls, regime, our_direction,
               CASE WHEN hit THEN 1 ELSE 0 END
        FROM price_to_beat
        WHERE horizon IN (5,15) AND our_direction IN ('UP','DOWN') AND resolved
          AND regime IS NOT NULL AND regime <> 'UNKNOWN'
        ORDER BY timestamp""").fetchall()
    c.close()

    if len(rows) < 30:
        print(f"Only {len(rows)} regime-era directional rounds — too few; re-run later."); return
    rng = (datetime.fromtimestamp(rows[0][0] / 1000, timezone.utc).date(),
           datetime.fromtimestamp(rows[-1][0] / 1000, timezone.utc).date())
    data = [(r[2], r[3], int(r[5]), r[1]) for r in rows]      # (lean_source, regime, hit, horizon)

    L = [f"# Fallback-Abstain Shadow — {date.today().isoformat()}", "",
         f"Read-only replay over **{len(data)} regime-era directional rounds** ({rng[0]} → {rng[1]}, "
         f"5m+15m). No live behavior changed. A policy is promotable only when the **abstained set is weak** "
         f"(≈ coin-flip) AND the **retained set's Wilson-LB improves and holds** as n grows.", ""]

    # reference: lean_source × regime
    from collections import defaultdict
    agg = defaultdict(lambda: [0, 0])
    for ls, rg, h, _ in data:
        agg[(ls, rg)][0] += 1; agg[(ls, rg)][1] += h
    ref = []
    for (ls, rg), (n, k) in sorted(agg.items(), key=lambda kv: (kv[0][0], -(kv[1][1] / kv[1][0] if kv[1][0] else 0))):
        ref.append((ls, rg, n, 100 * k / n, wilson(k, n)[0]))
    L.append("## lean_source × regime reference")
    L.append(md(ref, ["lean_source", "regime", "n", "acc %", "Wilson-LB"]))
    L.append("\n_The fallback × TRENDING cells are the suspected coin-flips this policy targets._")

    def policy_table(subset, title):
        out = [f"\n## {title}  (n={len(subset)})"]
        tbl = []
        for name, abst in POLICIES.items():
            retained = [hit for (ls, rg, hit, h) in subset if not abst(ls, rg)]
            abstained = [hit for (ls, rg, hit, h) in subset if abst(ls, rg)]
            n, a, lb = acc_lb(retained)
            cov = 100 * n / len(subset) if subset else 0.0
            an, aa, _ = acc_lb(abstained)
            cut = f"{an} @ {aa:.0f}%" if an else "—"
            flag = "✅" if (name != "baseline (keep all)" and lb > 50) else ("—" if name.startswith("baseline") else "·")
            tbl.append((name, n, cov, a, lb, cut, flag))
        out.append(md(tbl, ["policy", "kept", "coverage %", "retained acc %", "Wilson-LB", "cut (n@acc)", "LB>50?"]))
        return "\n".join(out)

    L.append(policy_table(data, "All regime-era rounds"))
    L.append(policy_table([d for d in data if d[3] == 5], "5m only"))
    L.append(policy_table([d for d in data if d[3] == 15], "15m only"))

    # headline numbers for the summary
    fb_trend = [hit for (ls, rg, hit, h) in data if ls == "fallback" and rg in TRENDING]
    base_all = [hit for (ls, rg, hit, h) in data]
    retained_main = [hit for (ls, rg, hit, h) in data if not (ls == "fallback" and rg in TRENDING)]
    nft, aft, lft = acc_lb(fb_trend)
    nb, ab, lb_b = acc_lb(base_all)
    nr, ar, lr = acc_lb(retained_main)
    L.append("\n## Read")
    L.append(f"- **What the target policy cuts:** {nft} fallback-in-TRENDING calls at **{aft:.1f}%** "
             f"(Wilson-LB {lft:.1f}%) — {'a weak/coin-flip set worth dropping.' if aft < 52 else 'NOT clearly weak — reconsider.'}")
    L.append(f"- **Effect on retained calls:** baseline {ab:.1f}% (LB {lb_b:.1f}%, n={nb}) → "
             f"retained {ar:.1f}% (LB {lr:.1f}%, n={nr}) at {100*nr/nb:.0f}% coverage.")
    L.append("- ✅ = retained Wilson-LB > 50% at this n. Promote only if it **holds** as n grows AND the cut "
             "set stays weak. Until then this is shadow-only — `_bet_lean()` is untouched.")
    L.append("\n**If promoted (later, with sign-off):** in `_bet_lean()`, return `NEUTRAL` when "
             "`rawDirection` is NEUTRAL (fallback path) AND `p['regime']` is a TRENDING regime — i.e. don't "
             "tilt a coin-flip side into a trend; let the window show 'no call'.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
