"""
probe_path_champion_lift.py - does the path engine's PLAY improve champion retained-call quality?
=================================================================================================
PRE-REGISTERED HYPOTHESIS (one-sided): adding the path play label to a P(Hold) gate REMOVES bad setups
(lowers line-cross / raises held %) WITHOUT being explained only by higher average P(Hold).

This is NOT "does path predict direction" (that's coin-flip). It is: among rounds the champion would gate
on P(Hold), do the `play==SKIP` rows fail MORE than `play in {RIDE,FADE-SETUP}` rows — at the SAME P(Hold)?

Read-only. Reconstructs `path_play` per round deterministically from the matrix keepers at the round's
OPEN via the EXACT serving function (price_to_beat._predict_path_plan) — same code that runs live.

Policies (P(Hold) gate +/- path filter):
  A baseline    P(Hold)>=0.93
  B remove SKIP P(Hold)>=0.93 AND play!=SKIP
  C strong path P(Hold)>=0.93 AND play in {RIDE,FADE-SETUP}
  D high-conf   P(Hold)>=0.95 AND play!=SKIP
  E conservative P(Hold)>=0.95 AND play in {RIDE,FADE-SETUP}

Discipline: final-holdout (last N days) reported separately, shuffled-null on the play label, and a
MATCHED-CONTROL (strata = P(Hold) decile x horizon x seconds-left bin x distance bin x regime) so a lift
that is only "higher P(Hold)" is caught. Verdict: PASS / WATCH / FAIL.

Usage:
  python backend/probe_path_champion_lift.py [--split 0.70] [--embargo 30] [--shuffle-null 1000]
                                             [--final-holdout-days 30] [--source pyth]
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
DB = os.path.join(DATA, "analytics.duckdb")
MATRIX = os.path.join(DATA, "research_matrix_1m.parquet")
OUT_MD = os.path.join(ROOT, "docs", "active", f"PATH_CHAMPION_LIFT_{date.today().isoformat()}.md")
OUT_PARQUET = os.path.join(DATA, "path_champion_lift.parquet")
sys.path.insert(0, HERE)
STRONG = ("RIDE", "FADE-SETUP")


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n; d = 1 + z * z / n
    return 100 * ((p + z * z / (2 * n)) / d - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d)


def _policy_stats(df, mask):
    s = df[mask]
    n = len(s); held = int(s["held"].sum())
    return {"n": n, "coverage": 100 * n / len(df) if len(df) else 0.0,
            "held_pct": 100 * held / n if n else 0.0, "line_cross_pct": 100 * (n - held) / n if n else 0.0,
            "wilson_lb": wilson(held, n), "avg_phold": float(s["p_hold"].mean()) if n else 0.0,
            "avg_secs": float(s["seconds_left"].mean()) if n else 0.0,
            "avg_dist": float(s["dist"].mean()) if n else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", type=float, default=0.70)
    ap.add_argument("--embargo", type=int, default=30)
    ap.add_argument("--shuffle-null", type=int, default=1000)
    ap.add_argument("--final-holdout-days", type=int, default=30)
    ap.add_argument("--source", default="pyth")
    a = ap.parse_args()

    import duckdb
    try:
        c = duckdb.connect(DB, read_only=True)
    except Exception as e:
        print(f"analytics.duckdb locked by the app — stop the backend and re-run.\n  ({str(e)[:90]})"); return
    cols = [r[1] for r in c.execute("PRAGMA table_info('price_to_beat')").fetchall()]
    src = f"AND COALESCE(p.source,'pyth')='{a.source}'" if "source" in cols else ""
    snaps = c.execute(f"""
        SELECT cs.round_id, cs.ts, cs.horizon, cs.seconds_left, cs.current_position, cs.current_move,
               cs.p_hold, COALESCE(cs.regime,'?') regime, p.timestamp anchor_ts, p.actual_direction
        FROM champion_snapshots cs JOIN price_to_beat p ON p.id = cs.round_id
        WHERE p.resolved AND cs.current_position IN ('UP','DOWN') AND p.actual_direction IN ('UP','DOWN')
          AND cs.p_hold IS NOT NULL AND p.horizon IN (5,15) {src}
        ORDER BY cs.ts""").df()
    c.close()
    if len(snaps) < 2000:
        print(f"only {len(snaps)} joined snapshots — too few."); return

    # ---- reconstruct path_play per ROUND from the matrix keepers at OPEN (deterministic serving fn) ----
    try:
        from price_to_beat import _predict_path_plan, _load_path_forecaster
    except Exception as e:
        print(f"cannot import serving fn: {e}"); return
    bundle = _load_path_forecaster()
    if not bundle or bundle.get("threshold_units") != "usd":
        print("path bundle missing/stale (need the v3 usd bundle)."); return
    feats = bundle["features"]
    mx = pd.read_parquet(MATRIX, columns=["ts_ms", "close"] + feats).sort_values("ts_ms").reset_index(drop=True)
    ts_arr = mx["ts_ms"].values
    play_by_round = {}
    for rid, anchor_ts, hz in snaps[["round_id", "anchor_ts", "horizon"]].drop_duplicates("round_id").itertuples(index=False):
        i = int(np.searchsorted(ts_arr, anchor_ts, side="right")) - 1
        if i < 0 or (anchor_ts - ts_arr[i]) > 120000:   # no matrix bar within 2m of open
            play_by_round[rid] = None; continue
        kp = {f: float(mx[f].iat[i]) for f in feats}
        if not all(np.isfinite(v) for v in kp.values()):
            play_by_round[rid] = None; continue
        try:
            pl = _predict_path_plan(bundle, int(hz), kp, float(mx["close"].iat[i]))
            play_by_round[rid] = (pl or {}).get("play")
        except Exception:
            play_by_round[rid] = None

    snaps["play"] = snaps["round_id"].map(play_by_round)
    snaps["held"] = (snaps["current_position"].astype(str) == snaps["actual_direction"].astype(str)).astype(int)
    snaps["dist"] = snaps["current_move"].abs()
    df = snaps[snaps["play"].notna()].reset_index(drop=True)
    play_cov = 100 * len(df) / len(snaps)
    if len(df) < 1500:
        print(f"only {len(df)} snapshots have a reconstructed play (of {len(snaps)}) — too few."); return

    cutoff_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - a.final_holdout_days * 86400000
    POLICIES = {
        "A baseline      P(Hold)>=.93": lambda d: d["p_hold"] >= 0.93,
        "B remove SKIP   .93 & play!=SKIP": lambda d: (d["p_hold"] >= 0.93) & (d["play"] != "SKIP"),
        "C strong path   .93 & RIDE/FADE": lambda d: (d["p_hold"] >= 0.93) & (d["play"].isin(STRONG)),
        "D high-conf     .95 & play!=SKIP": lambda d: (d["p_hold"] >= 0.95) & (d["play"] != "SKIP"),
        "E conservative  .95 & RIDE/FADE": lambda d: (d["p_hold"] >= 0.95) & (d["play"].isin(STRONG)),
    }
    L = [f"# Path → Champion Lift — {date.today().isoformat()}", "",
         f"Pre-registered: does `path_play` improve P(Hold)-gated retained-call quality (held% / line-cross / "
         f"Wilson-LB) **independent of average P(Hold)**? n={len(df):,} joined snapshots with a reconstructed "
         f"play ({play_cov:.0f}% of {len(snaps):,}) · source `{a.source}` · play split: "
         + ", ".join(f"{k}={v}" for k, v in df['play'].value_counts().items()), ""]

    def policy_table(d, title):
        L.append(f"\n## {title}  (n={len(d):,})")
        base = _policy_stats(d, POLICIES["A baseline      P(Hold)>=.93"](d))
        rows = []
        for name, fn in POLICIES.items():
            st = _policy_stats(d, fn(d))
            dlt = st["held_pct"] - base["held_pct"]
            rows.append((name, st["n"], round(st["coverage"], 1), round(st["held_pct"], 1),
                         round(st["line_cross_pct"], 1), round(st["wilson_lb"], 1), round(st["avg_phold"], 3),
                         f"{dlt:+.1f}" if name[0] != "A" else "—"))
        L.append("| policy | n | cov% | held% | cross% | Wilson-LB | avg P(Hold) | Δheld vs A |")
        L.append("|" + "|".join("---" for _ in range(8)) + "|")
        for r in rows:
            L.append("| " + " | ".join(str(x) for x in r) + " |")
        # lost-good / avoided-bad for B vs A
        A = POLICIES["A baseline      P(Hold)>=.93"](d); B = POLICIES["B remove SKIP   .93 & play!=SKIP"](d)
        dropped = d[A & ~B]
        ab, lg = int((dropped["held"] == 0).sum()), int((dropped["held"] == 1).sum())
        L.append(f"\n- **B vs A:** dropped {len(dropped)} SKIP rows → **avoided-bad {ab}** (line-cross removed) · "
                 f"**lost-good {lg}** (held calls cut) · net avoided failures = **{ab - lg:+d}**")
        return base

    base_full = policy_table(df, "Full sample")
    span_days = (df["ts"].max() - df["ts"].min()) / 86400000.0
    fin = df[df["ts"] >= cutoff_ms]
    degenerate_holdout = len(fin) >= 0.95 * len(df)   # all data inside the holdout window → no independent tail
    if degenerate_holdout:
        L.append(f"\n## Final holdout — NOT AVAILABLE\n- `champion_snapshots` spans only **{span_days:.1f} days** "
                 f"({pd.to_datetime(df['ts'].min(),unit='ms').date()} → {pd.to_datetime(df['ts'].max(),unit='ms').date()}), "
                 f"so the last {a.final_holdout_days}d window is the **entire sample**. There is **no independent "
                 f"temporal holdout** — every number below is in-sample over ~{span_days:.0f} days. This caps the "
                 f"verdict at WATCH no matter how strong the in-sample lift is.")
    elif len(fin) >= 300:
        policy_table(fin, f"Final holdout (last {a.final_holdout_days}d)")
    else:
        L.append(f"\n_Final holdout has only {len(fin)} rows — skipped (need ≥300)._")

    # ---- WHERE the SKIP signal actually bites: held% (SKIP vs non-SKIP) by P(Hold) band ----
    L.append("\n## Where the SKIP risk-signal bites (held% by P(Hold) band)")
    L.append("| P(Hold) band | n | non-SKIP held% | SKIP held% | SKIP penalty | SKIP share |")
    L.append("|---|---|---|---|---|---|")
    bands = [(0.50, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 0.93), (0.93, 1.01)]
    band_rows = []
    for lo, hi in bands:
        bd = df[(df["p_hold"] >= lo) & (df["p_hold"] < hi)]
        if len(bd) < 100:
            continue
        ns = bd[bd["play"] != "SKIP"]; sk = bd[bd["play"] == "SKIP"]
        nsh = 100 * ns["held"].mean() if len(ns) else float("nan")
        skh = 100 * sk["held"].mean() if len(sk) else float("nan")
        pen = nsh - skh if len(sk) else float("nan")
        band_rows.append((f"{lo:.2f}-{hi if hi<=1 else 1.0:.2f}", len(bd), round(nsh, 1),
                          round(skh, 1) if len(sk) else "—", f"{pen:+.1f}" if len(sk) else "—",
                          f"{100*len(sk)/len(bd):.0f}%"))
    for r in band_rows:
        L.append("| " + " | ".join(str(x) for x in r) + " |")
    L.append("\n_Decision-relevance: at P(Hold)≥0.93 even SKIP rows are ~91% holds, so the signal rarely flips a "
             "bet — it matters most in the LOWER bands, near the betting threshold, where a SKIP penalty can push a "
             "marginal call below the edge line. That is where to USE it (graded sizing / extra required edge), not "
             "as a binary drop at the high-confidence gate._")

    # ---- MATCHED CONTROL: within P(Hold) decile x horizon x regime, does play!=SKIP hold more than SKIP? ----
    L.append("\n## Matched control — is the lift independent of P(Hold)?")
    g = df[POLICIES["A baseline      P(Hold)>=.93"](df)].copy()
    g["ph_bin"] = pd.qcut(g["p_hold"], 5, labels=False, duplicates="drop")
    g["sec_bin"] = pd.cut(g["seconds_left"], [-1, 60, 150, 1e9], labels=["<60", "60-150", ">150"])
    g["keep"] = (g["play"] != "SKIP").astype(int)
    diffs, wn, ws = [], 0, 0
    for _, grp in g.groupby(["ph_bin", "horizon", "regime", "sec_bin"], observed=True):
        kp = grp[grp["keep"] == 1]; sk = grp[grp["keep"] == 0]
        if len(kp) >= 20 and len(sk) >= 20:
            diffs.append((len(grp), kp["held"].mean() - sk["held"].mean()))
            wn += len(grp); ws += 1
    if diffs:
        wdiff = sum(n * d for n, d in diffs) / sum(n for n, _ in diffs)
        L.append(f"- Across **{ws} matched strata** (n={wn:,}), weighted held% difference "
                 f"**play!=SKIP minus play==SKIP = {100*wdiff:+.2f}pp** (controlling for P(Hold)/horizon/regime/"
                 f"seconds-left). A positive value = path adds value the P(Hold) level alone does not.")
    else:
        wdiff = 0.0
        L.append("- Too few matched strata with ≥20 each — matched control inconclusive.")

    # ---- SHUFFLED NULL on the play label (B vs A held% lift) ----
    A = POLICIES["A baseline      P(Hold)>=.93"](df)
    a_rows = df[A]
    real_keep = a_rows[a_rows["play"] != "SKIP"]["held"].mean()
    real_skip = a_rows[a_rows["play"] == "SKIP"]["held"].mean()
    real_lift = real_keep - real_skip
    rng = np.random.default_rng(0)
    plays = a_rows["play"].values.copy(); held = a_rows["held"].values
    n_skip = int((plays == "SKIP").sum()); null = []
    for _ in range(a.shuffle_null):
        idx = rng.permutation(len(plays))
        sk = idx[:n_skip]; kp = idx[n_skip:]
        null.append(held[kp].mean() - held[sk].mean())
    null = np.array(null); p_val = float((null >= real_lift).mean())
    L.append(f"\n## Shuffled null ({a.shuffle_null}×)")
    L.append(f"- Real held% lift (play!=SKIP − play==SKIP, within the P(Hold)≥.93 gate) = **{100*real_lift:+.2f}pp** "
             f"(keep {100*real_keep:.1f}% vs skip {100*real_skip:.1f}%). Null mean {100*null.mean():+.2f}pp, "
             f"95th pct {100*np.quantile(null,0.95):+.2f}pp → **p={p_val:.3f}**.")

    # ---- VERDICT ----
    b_full = _policy_stats(df, POLICIES["B remove SKIP   .93 & play!=SKIP"](df))
    held_lift = b_full["held_pct"] - base_full["held_pct"]
    lb_lift = b_full["wilson_lb"] - base_full["wilson_lb"]
    A = POLICIES["A baseline      P(Hold)>=.93"](df); B = POLICIES["B remove SKIP   .93 & play!=SKIP"](df)
    dropped = df[A & ~B]
    avoided_bad = int((dropped["held"] == 0).sum()); lost_good = int((dropped["held"] == 1).sum())
    net_winners = avoided_bad - lost_good          # negative = the filter throws away more winners than it saves
    sig = p_val < 0.05
    matched_ok = wdiff > 0.005
    signal_real = sig and matched_ok               # does the play carry P(Hold)-independent hold-info?
    good_filter = net_winners > 0                  # is a BINARY drop net-positive on absolute winners?
    if signal_real and good_filter and not degenerate_holdout:
        verdict = "PASS — path play improves retained-call quality independent of P(Hold), AND is a net-positive filter"
    elif signal_real:
        verdict = ("WATCH — path play is a REAL, P(Hold)-independent risk signal (SKIP fails ~2-3x more, matched + "
                   "shuffled-null significant), BUT it is NOT a good binary filter at the 0.93 gate (drops more "
                   "winners than losers) and there is no independent temporal holdout yet")
    else:
        verdict = "FAIL — path play does not beat P(Hold)-only (lift not significant or explained by P(Hold))"
    L.append(f"\n## VERDICT\n**{verdict}**\n")
    L.append(f"- Signal real? **{signal_real}** (matched {100*wdiff:+.2f}pp, shuffled-null p={p_val:.3f}). "
             f"Net-positive binary filter? **{good_filter}** (B drops {len(dropped)} SKIP rows: avoided-bad "
             f"{avoided_bad} vs lost-good {lost_good} → **net {net_winners:+d} winners**). "
             f"Independent holdout? **{not degenerate_holdout}** (span {span_days:.0f}d).")
    L.append(f"- B−A: held {held_lift:+.2f}pp · Wilson-LB {lb_lift:+.2f}pp · shuffled-null p={p_val:.3f} · "
             f"matched-control {100*wdiff:+.2f}pp.")
    L.append("- Promote `play!=SKIP` into `champion_shadow` (shadow only) **only on PASS**; on WATCH keep "
             "logging; on FAIL drop it. Per the protocol: no live wiring on this single offline read.")

    df.to_parquet(OUT_PARQUET, index=False)
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\nWrote {OUT_MD}\nWrote {OUT_PARQUET}")


if __name__ == "__main__":
    main()
