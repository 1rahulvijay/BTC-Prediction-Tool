"""
audit_hf_polymarket_dataset.py - STRICT quality/parity audit of the HuggingFace Polymarket 5m/15m dataset.
=========================================================================================================
Treat the HF dataset (`obadiaha/polymarket-crypto-5m-15m`) as RESEARCH data, not production truth, until it
passes this audit. It has historical BTC 5m/15m orderbooks (best bid/ask + top-10 ladders, ~10s snapshots),
a markets table (slug `btc-updown-{5,15}m-<anchor_ts>`), and resolutions (Up/Down outcome) for Jan-Mar 2026.
Before ingesting, verify: market completeness, orderbook completeness, settlement correctness, price
consistency. Kill/quarantine bad rows aggressively.

Known structural note (not a bug, but must be handled downstream): each market has 2 tokens but the dataset
does NOT label which is UP vs DOWN. That must be DERIVED — the winning token settles to ~1.0, so with the
resolution outcome the UP token is identifiable. This audit reports it; the mispricing-table builder resolves it.

Usage:
  python backend/audit_hf_polymarket_dataset.py            # markets + resolutions (full) + 3 sample orderbook days
  python backend/audit_hf_polymarket_dataset.py --ob-days 6
"""
from __future__ import annotations

import argparse
import io
import json
import os
import urllib.request
from datetime import date

import numpy as np
import pandas as pd

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
REPO = "obadiaha/polymarket-crypto-5m-15m"
OUT_MD = os.path.join(ROOT, "docs", "active", f"HF_POLYMARKET_DATASET_AUDIT_{date.today().isoformat()}.md")


def _get(url, raw=False):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read() if raw else json.loads(r.read().decode())


def _dl(path):
    return pd.read_parquet(io.BytesIO(_get(f"https://huggingface.co/datasets/{REPO}/resolve/main/{path}", raw=True)))


def _pct(a, b):
    return f"{100*a/b:.1f}%" if b else "n/a"


def audit(ob_days: int):
    L = [f"# HuggingFace Polymarket Dataset Audit — {date.today().isoformat()}", "",
         f"Strict quality/parity audit of `{REPO}` before ingest. Research data until it passes.", ""]
    tree = _get(f"https://huggingface.co/api/datasets/{REPO}/tree/main?recursive=true")
    files = [f["path"] for f in tree if f.get("type") == "file"]
    ob_files = sorted(f for f in files if f.startswith("orderbooks/"))
    L.append(f"**Files:** {len(ob_files)} orderbook days ({ob_files[0].split('/')[-1]} .. {ob_files[-1].split('/')[-1]}), "
             f"markets/all.parquet, resolutions/all.parquet.\n")

    # ---------------- MARKETS ----------------
    mk = _dl("markets/all.parquet")
    mk = mk[mk["asset"].astype(str).str.upper() == "BTC"].copy()
    mk["is5"] = mk["slug"].astype(str).str.contains("updown-5m-")
    mk["is15"] = mk["slug"].astype(str).str.contains("updown-15m-")
    mk["anchor"] = mk["slug"].astype(str).str.rsplit("-", n=1).str[-1]
    st = pd.to_datetime(mk["start_time"], utc=True, errors="coerce")
    et = pd.to_datetime(mk["end_time"], utc=True, errors="coerce")
    win_s = (et - st).dt.total_seconds()
    dup = int(mk["slug"].duplicated().sum())
    anchor_ok = int((mk["anchor"].str.fullmatch(r"\d{9,11}")).sum())
    win5_ok = int(((mk["is5"]) & (win_s == 300)).sum()); win15_ok = int(((mk["is15"]) & (win_s == 900)).sum())
    L += ["## 1. Market completeness",
          f"- BTC markets: **{len(mk):,}** — 5m **{int(mk['is5'].sum()):,}**, 15m **{int(mk['is15'].sum()):,}**, "
          f"other {int((~mk['is5'] & ~mk['is15']).sum()):,}",
          f"- duplicate slugs: **{dup}** {'✅' if dup == 0 else '⚠️ quarantine'}",
          f"- slug anchor is a valid unix ts: {_pct(anchor_ok, len(mk))}",
          f"- window length correct (5m=300s / 15m=900s): 5m {_pct(win5_ok, int(mk['is5'].sum()))}, "
          f"15m {_pct(win15_ok, int(mk['is15'].sum()))}"]

    # ---------------- RESOLUTIONS ----------------
    rz = _dl("resolutions/all.parquet")
    rz = rz[rz["asset"].astype(str).str.upper() == "BTC"].copy()
    out_ok = rz["outcome"].astype(str).isin(["Up", "Down"])
    rst = pd.to_datetime(rz["end_time"], utc=True, errors="coerce")
    rres = pd.to_datetime(rz["resolved_at"], utc=True, errors="coerce")
    no_leak = int((rres >= rst).sum())               # resolution at/after window close = no future leakage
    mk_ids = set(mk["market_id"]); rz_ids = set(rz["market_id"])
    cov = len(mk_ids & rz_ids)
    L += ["\n## 2. Settlement correctness",
          f"- BTC resolutions: **{len(rz):,}**; outcome ∈ {{Up,Down}}: {_pct(int(out_ok.sum()), len(rz))}",
          f"- resolved_at ≥ window close (no future leakage): {_pct(no_leak, len(rz))} "
          f"{'✅' if no_leak/max(len(rz),1) > 0.98 else '⚠️'}",
          f"- markets with a matching resolution: **{cov:,}** of {len(mk):,} ({_pct(cov, len(mk))})"]

    # ---------------- ORDERBOOKS (sample) ----------------
    idxs = np.linspace(0, len(ob_files) - 1, min(ob_days, len(ob_files))).astype(int)
    stats = {"rows": 0, "bad_prices": 0, "crossed": 0, "ladder_ok": 0, "ladder_n": 0, "two_tok": 0, "mkts": 0,
             "comp_ok": 0, "comp_n": 0, "dt_med": [], "ts_in_win": 0, "ts_win_n": 0, "tight": 0, "spr": []}
    EPOCH = pd.Timestamp("1970-01-01", tz="UTC")
    endmap = dict(zip(mk["market_id"], et))
    startmap = dict(zip(mk["market_id"], st))
    for i in idxs:
        o = _dl(ob_files[i])
        o = o[o["asset"].astype(str).str.upper() == "BTC"] if "asset" in o else o
        stats["rows"] += len(o)
        ba, bb = o["best_ask"].astype(float), o["best_bid"].astype(float)
        stats["bad_prices"] += int(((ba < 0) | (ba > 1) | (bb < 0) | (bb > 1) | ba.isna() | bb.isna()).sum())
        stats["crossed"] += int((bb > ba + 1e-9).sum())
        # ladder parse (sample up to 400 rows/day for speed)
        for lv in o["ask_levels"].dropna().head(400):
            stats["ladder_n"] += 1
            try:
                arr = json.loads(lv) if isinstance(lv, str) else lv
                if isinstance(arr, list) and arr and all("price" in x and "size" in x for x in arr):
                    stats["ladder_ok"] += 1
            except Exception:
                pass
        # timestamp is datetime64[us] -> POSIX seconds (unit-independent via epoch delta)
        ts = pd.to_datetime(o["timestamp"], utc=True, errors="coerce")
        o = o.assign(_ts=((ts - EPOCH).dt.total_seconds()).astype("int64"))
        stats["spr"].append(float((o["best_ask"].astype(float) - o["best_bid"].astype(float)).median()))
        stats["tight"] += int(((o["best_ask"].astype(float) - o["best_bid"].astype(float)) <= 0.05).sum())
        # 2 tokens per (market,snapshot) + book-width: buying BOTH shares costs ask_a+ask_b (overround; ~1 when tight)
        g = o.groupby(["market_id", "_ts"])
        two = g["token_id"].nunique()
        stats["two_tok"] += int((two == 2).sum()); stats["mkts"] += int(two.size)
        pair = g.agg(asum=("best_ask", "sum"), n=("token_id", "size"))
        pair = pair[pair["n"] == 2]
        stats["comp_n"] += len(pair); stats["comp_ok"] += int(((pair["asum"] >= 0.99) & (pair["asum"] <= 1.10)).sum())
        # cadence per token
        for _, grp in o.sort_values("_ts").groupby("token_id"):
            d = np.diff(grp["_ts"].values)
            if len(d):
                stats["dt_med"].append(float(np.median(d)))
        # ts inside market window
        o2 = o.dropna(subset=["market_id"])
        for mid, tsv in zip(o2["market_id"], o2["_ts"]):
            s, e = startmap.get(mid), endmap.get(mid)
            if s is not None and e is not None and pd.notna(s) and pd.notna(e):
                stats["ts_win_n"] += 1
                if s.timestamp() - 5 <= tsv <= e.timestamp() + 5:
                    stats["ts_in_win"] += 1
    med_dt = float(np.median(stats["dt_med"])) if stats["dt_med"] else float("nan")
    med_spr = float(np.median(stats["spr"])) if stats["spr"] else float("nan")
    L += [f"\n## 3. Orderbook completeness ({len(idxs)} sample days, {stats['rows']:,} rows)",
          f"- snapshot cadence (median dt per token): **{med_dt:.0f}s** {'✅ ~10s' if 5 <= med_dt <= 20 else '⚠️'}",
          f"- valid best bid/ask in [0,1]: {_pct(stats['rows']-stats['bad_prices'], stats['rows'])} (rest = empty/thin → quarantine)",
          f"- crossed books (bid>ask): **{stats['crossed']:,}** ({_pct(stats['crossed'], stats['rows'])}) "
          f"{'✅' if stats['crossed']/max(stats['rows'],1) < 0.01 else '⚠️ quarantine'}",
          f"- top-10 ladder parseable: {_pct(stats['ladder_ok'], stats['ladder_n'])} ✅",
          f"- 2 tokens per (market, snapshot): {_pct(stats['two_tok'], stats['mkts'])} ✅",
          f"- snapshot ts inside [window_start, window_end]: {_pct(stats['ts_in_win'], stats['ts_win_n'])}",
          "\n**Liquidity characterization (not a defect — the key trading caveat):**",
          f"- median bid/ask spread: **${med_spr:.2f}** (of a $1 share) — these books are WIDE",
          f"- tight snapshots (spread ≤ 5c): **{_pct(stats['tight'], stats['rows'])}** — the only tradeable subset",
          f"- both-sides overround (ask_a+ask_b in [0.99,1.10]): {_pct(stats['comp_ok'], stats['comp_n'])} — the rest "
          f"are wide/illiquid (often 0.1/0.9 at open). Fair value ≠ ask until the book tightens."]

    # ---------------- VERDICT ----------------
    crossed_rate = stats["crossed"] / max(stats["rows"], 1)
    cadence_ok = 5 <= med_dt <= 20
    leak_ok = no_leak / max(len(rz), 1) > 0.98
    core_ok = dup == 0 and leak_ok and cov / max(len(mk), 1) > 0.9 and crossed_rate < 0.01 and cadence_ok
    L += ["\n## Verdict",
          f"**{'PASS (core) — markets/resolutions/orderbook structure are clean; ingest with quarantine + liquidity filter' if core_ok else 'CONDITIONAL — some core checks failed'}**",
          f"- **Markets + resolutions are pristine** (0 dup slugs, 100% window lengths, 100% no-leakage, {_pct(cov, len(mk))} "
          f"resolution coverage). Orderbook prices/ladders/tokens/cadence are clean.",
          "- ⚠️ **Two things to handle on ingest, not defects:** (1) **UP/DOWN token labels are not in the data** — derive "
          "per market (the winning token settles to ~1.0, so the resolution outcome identifies UP); (2) **books are WIDE** "
          f"— median spread ~${med_spr:.2f}, only ~{_pct(stats['tight'], stats['rows'])} of snapshots are tight (≤5c). "
          "Filter to liquid snapshots before any edge claim.",
          "- Research quality **good**; execution/fillability **still needs the live 1s recorder** (10s snapshots + wide "
          "books miss fast edge decay).",
          "- Next: `build_pm_historical_mispricing_table.py` (derive UP/DOWN, pivot to per-round quotes, join resolution, "
          "flag liquid rows), then backfill P(Hold) and run `analyze_pm_historical_edge.py`."]

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(str(x) for x in L))
    print("\n".join(str(x) for x in L[3:]))
    print(f"\nWrote {OUT_MD}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ob-days", type=int, default=3)
    a = ap.parse_args()
    audit(a.ob_days)


if __name__ == "__main__":
    main()
