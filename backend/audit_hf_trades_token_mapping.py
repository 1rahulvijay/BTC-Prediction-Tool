"""
audit_hf_trades_token_mapping.py - solve token_id -> UP/DOWN cleanly before ANY trades edge analysis.
=====================================================================================================
The HF dataset gives 2 tokens per BTC market but NO UP/DOWN label. Derive it deterministically from the
resolution: the WINNING token settles to ~1.0, so the token that trades HIGHER (esp. late) is the winner;
the resolution outcome (Up/Down) then says whether the winner is UP or DOWN. Validate the separation and
QUARANTINE any market where the two tokens are not cleanly separated (do not guess).

Writes data/hf_trades_cache/token_map.parquet: market_id, up_token, down_token, outcome, quality, quarantine.

Usage:
  python backend/audit_hf_trades_token_mapping.py --days 4      # audit a sample; --days 0 = all 21
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.request
from datetime import date

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "hf_trades_cache")
REPO = "obadiaha/polymarket-crypto-5m-15m"
OUT_MD = os.path.join(ROOT, "docs", "active", f"HF_TRADES_TOKEN_MAPPING_{date.today().isoformat()}.md")


def _get(url, raw=False):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read() if raw else json.loads(r.read().decode())


def _dl(path):
    os.makedirs(CACHE, exist_ok=True)
    local = os.path.join(CACHE, path.replace("/", "_"))
    if os.path.exists(local):
        return pd.read_parquet(local)
    df = pd.read_parquet(io.BytesIO(_get(f"https://huggingface.co/datasets/{REPO}/resolve/main/{path}", raw=True)))
    df.to_parquet(local, index=False)
    return df


def build_map(days: int):
    tree = _get(f"https://huggingface.co/api/datasets/{REPO}/tree/main?recursive=true")
    tfiles = sorted(f["path"] for f in tree if f.get("type") == "file" and f["path"].startswith("trades/"))
    if days and days < len(tfiles):
        idx = np.linspace(0, len(tfiles) - 1, days).astype(int)
        tfiles = [tfiles[i] for i in idx]
    # resolutions (market_id -> outcome, end_time)
    rz = _dl("resolutions/all.parquet")
    rz = rz[rz["asset"].astype(str).str.upper() == "BTC"]
    res = dict(zip(rz["market_id"], rz["outcome"].astype(str)))
    endt = dict(zip(rz["market_id"], pd.to_datetime(rz["end_time"], utc=True)))
    rows = []
    for tf in tfiles:
        t = _dl(tf)
        t = t[(t["asset"].astype(str).str.upper() == "BTC") &
              (t["market_id"].astype(str).str.startswith("btc-updown"))].copy()
        if not len(t):
            continue
        t["ts"] = pd.to_datetime(t["timestamp"], utc=True)
        t["price"] = t["price"].astype(float)
        for mid, g in t.groupby("market_id"):
            toks = g["token_id"].unique()
            if len(toks) != 2 or mid not in res:
                continue
            et = endt.get(mid)
            late = g[g["ts"] >= et - pd.Timedelta(seconds=60)] if et is not None else g
            late = late if len(late) >= 4 else g
            mean_late = late.groupby("token_id")["price"].mean()
            if len(mean_late) != 2:
                continue
            winner = mean_late.idxmax(); loser = mean_late.idxmin()
            wp, lp = float(mean_late[winner]), float(mean_late[loser])
            sep = wp - lp                                            # clean if winner ~1, loser ~0
            outcome = res[mid]
            up_tok = winner if outcome == "Up" else loser           # winner is UP iff outcome=Up
            dn_tok = loser if outcome == "Up" else winner
            quar = not (wp >= 0.6 and lp <= 0.4 and sep >= 0.3)      # ambiguous -> quarantine
            rows.append({"market_id": mid, "up_token": str(up_tok), "down_token": str(dn_tok),
                         "outcome": outcome, "winner_late_price": round(wp, 3), "loser_late_price": round(lp, 3),
                         "separation": round(sep, 3), "n_trades": int(len(g)), "quarantine": quar})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4)
    a = ap.parse_args()
    m = build_map(a.days)
    if not len(m):
        print("no markets mapped — check trades access."); return
    os.makedirs(CACHE, exist_ok=True)
    m.to_parquet(os.path.join(CACHE, "token_map.parquet"), index=False)
    n = len(m); q = int(m["quarantine"].sum()); clean = n - q
    is5 = m["market_id"].str.contains("updown-5m"); is15 = m["market_id"].str.contains("updown-15m")
    L = [f"# HF Trades Token Mapping — {date.today().isoformat()}", "",
         f"Deterministic token→UP/DOWN mapping from resolution + late trade price (winner settles to ~1.0). "
         f"Quarantine any market not cleanly separated. Foundation for the trades edge pipeline.", "",
         f"## Result",
         f"- markets mapped: **{n:,}** ({int(is5.sum()):,} 5m, {int(is15.sum()):,} 15m)",
         f"- **clean: {clean:,} ({100*clean/n:.1f}%)** · quarantined (ambiguous): {q:,} ({100*q/n:.1f}%)",
         f"- separation (winner − loser late price): median **{m['separation'].median():.3f}**, "
         f"clean-set median {m[~m['quarantine']]['separation'].median():.3f}",
         f"- winner late price (clean): median **{m[~m['quarantine']]['winner_late_price'].median():.3f}** "
         f"(→1.0 ✓) · loser: median **{m[~m['quarantine']]['loser_late_price'].median():.3f}** (→0.0 ✓)"]
    verdict = ("PASS — mapping is clean and deterministic; use the clean set, drop quarantined markets"
               if clean / n > 0.8 else
               "WEAK — too many ambiguous markets; investigate before trusting the trades edge analysis")
    L += [f"\n## Verdict\n**{verdict}**",
          "- The mapping is derived from settlement + price, so it is only as good as the resolution join and the "
          "late-trade coverage. Quarantined markets (no clean 1.0/0.0 separation) are excluded downstream.",
          "- Wrote `data/hf_trades_cache/token_map.parquet` for `build_pm_hf_trade_snapshots.py`.",
          "- ⚠️ This is still **executed-trade** research; a trade price is not an executable resting ask. The live "
          "`/book` recorder remains required for fillability proof."]
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(str(x) for x in L))
    print("\n".join(str(x) for x in L[3:]))
    print(f"\nWrote {OUT_MD}\nWrote {os.path.join(CACHE, 'token_map.parquet')}")


if __name__ == "__main__":
    main()
