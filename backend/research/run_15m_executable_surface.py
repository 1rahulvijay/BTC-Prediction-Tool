"""PHASE 5 -- unconditional executable TP-before-SL surface (+ first-profitable-exit hazard).

Canonical blueprint (2026-07-25). Loads an immutable L2 snapshot, reconstructs per-asset book
timelines, and runs the FROZEN grid through the shared deterministic fill engine.

Pass 1 is deliberately UNCONDITIONAL: leader and trailer, every checkpoint, no ML, no feature
filter, no selection. The job here is to measure the surface, not to find a winner.

Every output is stamped PILOT / NOT PROMOTABLE while the snapshot is below the pre-declared
gate (>=500 independent rounds AND >=8 calendar weeks). A pilot may KILL a hypothesis; it may
never promote one.

    python backend/research/run_15m_executable_surface.py --horizon 15 [--max-rounds N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict

import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from executable_fill_engine import BookState, net_path, first_barrier, first_profitable  # noqa
import executable_surface_config as CFG  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB = os.path.join(ROOT, "data", "research_snapshots",
                          "polymarket_l2_2026-07-25.duckdb")
SNAP = os.path.join(ROOT, "data", "pm_export_snapshots.parquet")
SETT = os.path.join(ROOT, "data", "pm_export_settlements.parquet")
OUT = os.path.join(ROOT, "data", "research", "15m_executable_surface")
NS = 1_000_000_000


# ---------------------------------------------------------------------------------------------
def load_rounds(con, horizon: int) -> list:
    """One row per round: both asset ids + the official winner."""
    rows = con.execute(f"""
        WITH m AS (
          SELECT condition_id, horizon, start_ts, end_ts,
                 MAX(CASE WHEN outcome='UP'   THEN asset_id END) AS up_asset,
                 MAX(CASE WHEN outcome='DOWN' THEN asset_id END) AS down_asset
          FROM pm_l2_markets WHERE horizon = ? GROUP BY 1,2,3,4)
        SELECT m.condition_id, m.start_ts, m.end_ts, m.up_asset, m.down_asset,
               s.settled_side
        FROM m JOIN read_parquet('{SETT}') s
          ON s.horizon = m.horizon AND abs(s.anchor_ts - m.start_ts) < 90
        WHERE m.up_asset IS NOT NULL AND m.down_asset IS NOT NULL
        ORDER BY m.start_ts
    """, (int(horizon),)).fetchall()
    return [{"condition_id": c, "start_ts": int(s), "end_ts": int(e),
             "up_asset": u, "down_asset": d,
             "winner": "UP" if int(w) == 1 else "DOWN"} for c, s, e, u, d, w in rows]


def verify_side_convention(con, rounds: list) -> dict:
    """GUARD: confirm current_side==1 means UP.

    If this convention were backwards, leader/trailer would be swapped in every single cell and
    the entire surface would be silently inverted -- while still looking perfectly plausible.
    Late in a round the BTC-side leader agrees with the eventual winner the large majority of
    the time, so agreement near expiry is a decisive check on the encoding.
    """
    ids = [r["condition_id"] for r in rounds]
    if not ids:
        return {"checked": 0, "agree_pct": None, "ok": False}
    con.execute("CREATE OR REPLACE TEMP TABLE _rr AS SELECT * FROM (VALUES " +
                ",".join(["(?, ?)"] * len(rounds)) + ") t(cid, winner)",
                [x for r in rounds for x in (r["condition_id"], r["winner"])])
    row = con.execute(f"""
        WITH late AS (
          SELECT s.condition_id,
                 arg_min(s.current_side, s.seconds_left) AS side_at_end
          FROM read_parquet('{SNAP}') s
          WHERE s.seconds_left <= 30 GROUP BY 1)
        SELECT COUNT(*),
               AVG(CASE WHEN (l.side_at_end = 1.0 AND r.winner = 'UP')
                          OR (l.side_at_end = 0.0 AND r.winner = 'DOWN')
                        THEN 1.0 ELSE 0.0 END)
        FROM late l JOIN _rr r ON r.cid = l.condition_id
    """).fetchone()
    n, agree = int(row[0] or 0), float(row[1] or 0.0)
    return {"checked": n, "agree_pct": round(agree * 100, 1), "ok": n >= 20 and agree >= 0.70}


def load_leader_map(con, rounds: list) -> dict:
    """(condition_id) -> sorted [(ts_s, side)] of BTC-state observations."""
    out = defaultdict(list)
    for cid, ts, side in con.execute(f"""
        SELECT condition_id, ts, current_side FROM read_parquet('{SNAP}')
        WHERE condition_id IN (SELECT cid FROM _rr) ORDER BY condition_id, ts
    """).fetchall():
        t = float(ts) / (1000.0 if float(ts) > 1e11 else 1.0)
        out[cid].append((t, "UP" if float(side) == 1.0 else "DOWN"))
    return out


def leader_at(obs: list, t_s: float):
    """Last observed BTC-side at or before t_s. None if the round has no prior observation."""
    best = None
    for ts, side in obs:
        if ts <= t_s:
            best = side
        else:
            break
    return best


def load_books(con, asset_ids: list) -> dict:
    """asset_id -> seq-ordered [BookState] with full ladders."""
    if not asset_ids:
        return {}
    ph = ",".join(["?"] * len(asset_ids))
    summaries = con.execute(f"""
        SELECT asset_id, seq, recv_ts_ns, best_bid, best_ask,
               best_bid_size, best_ask_size, spread
        FROM pm_l2_book_summaries
        WHERE asset_id IN ({ph}) AND valid AND synchronized
          AND best_bid IS NOT NULL AND best_ask IS NOT NULL
        ORDER BY asset_id, seq
    """, asset_ids).fetchall()
    ladders = defaultdict(lambda: ([], []))     # (asset,seq) -> (bids, asks)
    for a, s, side, p, sz in con.execute(f"""
        SELECT asset_id, seq, side, price, size FROM pm_l2_book_levels
        WHERE asset_id IN ({ph}) AND size > 0
        ORDER BY asset_id, seq, side, level_index
    """, asset_ids).fetchall():
        b, k = ladders[(a, s)]
        (b if side == "BUY" else k).append((float(p), float(sz)))
    books = defaultdict(list)
    for a, s, ts, bb, ba, bbs, bas, sp in summaries:
        bids, asks = ladders.get((a, s), ([], []))
        books[a].append(BookState(
            seq=int(s), recv_ts_ns=int(ts), best_bid=bb, best_ask=ba,
            best_bid_size=float(bbs or 0), best_ask_size=float(bas or 0),
            spread=sp,
            asks=asks or ([(ba, float(bas or 0))] if ba is not None else []),
            bids=bids or ([(bb, float(bbs or 0))] if bb is not None else [])))
    return books


# ---------------------------------------------------------------------------------------------
def run(db: str, horizon: int, max_rounds: int, batch: int = 12) -> dict:
    t0 = time.time()
    con = duckdb.connect(db, read_only=True)
    rounds = load_rounds(con, horizon)
    if max_rounds:
        rounds = rounds[:max_rounds]
    guard = verify_side_convention(con, rounds)
    leaders = load_leader_map(con, rounds)

    checkpoints = CFG.ENTRY_CHECKPOINTS_S[horizon]
    elig = {"min_ask": CFG.MIN_ASK, "max_ask": CFG.MAX_ASK, "max_spread": CFG.MAX_SPREAD,
            "min_top_ask_size": CFG.MIN_TOP_ASK_SIZE,
            "max_book_staleness_s": CFG.MAX_BOOK_STALENESS_S}

    cells = defaultdict(lambda: {"n": 0, "tp": 0, "sl": 0, "settle": 0, "sum": 0.0,
                                 "wins": 0, "gross_win": 0.0, "gross_loss": 0.0,
                                 "sum_hold": 0.0, "sum_mfe": 0.0, "sum_mae": 0.0})
    hazard = defaultdict(lambda: {"n": 0, "censored": 0, "t": []})
    skips = defaultdict(int)
    eligible_entries = rounds_used = 0

    for i in range(0, len(rounds), batch):
        chunk = rounds[i:i + batch]
        assets = [a for r in chunk for a in (r["up_asset"], r["down_asset"])]
        books = load_books(con, assets)
        for r in chunk:
            obs = leaders.get(r["condition_id"], [])
            used = False
            for cp in checkpoints:
                dec_s = r["end_ts"] - cp
                side_btc = leader_at(obs, dec_s)
                if side_btc is None:
                    skips["no_btc_state"] += 1
                    continue
                for side_name in CFG.SIDES:
                    asset_side = side_btc if side_name == "LEADER" else (
                        "DOWN" if side_btc == "UP" else "UP")
                    asset = r["up_asset"] if asset_side == "UP" else r["down_asset"]
                    bk = books.get(asset) or []
                    if not bk:
                        skips["no_books"] += 1
                        continue
                    settle = 1.0 if asset_side == r["winner"] else 0.0
                    for lat in CFG.LATENCIES_MS:
                        for qty in CFG.QUANTITIES:
                            p = net_path(bk, int(dec_s * NS), lat, qty, settle,
                                         eligibility=elig)
                            if not p.eligible:
                                skips[p.reason] += 1
                                continue
                            eligible_entries += 1
                            used = True
                            fp_t, _ = first_profitable(p)
                            hk = (side_name, cp, lat, qty)
                            hz = hazard[hk]
                            hz["n"] += 1
                            if fp_t is None:
                                hz["censored"] += 1
                            else:
                                hz["t"].append(fp_t)
                            for tp in CFG.TP_CENTS:
                                for sl in CFG.SL_CENTS:
                                    o = first_barrier(p, tp, sl)
                                    c = cells[(side_name, cp, tp, sl, lat, qty)]
                                    net = o["net_per_share"]
                                    c["n"] += 1
                                    c[o["exit_kind"].lower()] += 1
                                    c["sum"] += net
                                    c["sum_hold"] += o["holding_s"]
                                    c["sum_mfe"] += o["mfe_per_share"]
                                    c["sum_mae"] += o["mae_per_share"]
                                    if net > 0:
                                        c["wins"] += 1
                                        c["gross_win"] += net
                                    else:
                                        c["gross_loss"] += -net
            rounds_used += used
        print(f"  .. rounds {min(i+batch, len(rounds))}/{len(rounds)}"
              f"  entries={eligible_entries:,}  {time.time()-t0:.0f}s", flush=True)
    con.close()

    weeks = len({time.strftime("%Y-%W", time.gmtime(r["start_ts"])) for r in rounds})
    promotable = rounds_used >= CFG.GATE["min_independent_rounds"] and weeks >= CFG.GATE["min_calendar_weeks"]
    return {"meta": {"db": db, "horizon": horizon, "config_hash": CFG.config_hash(),
                     "config_version": CFG.CONFIG_VERSION,
                     "rounds_available": len(rounds), "rounds_with_entries": rounds_used,
                     "calendar_weeks": weeks, "eligible_entries": eligible_entries,
                     "declared_cells": CFG.family_size(), "elapsed_s": round(time.time()-t0, 1),
                     "side_convention_guard": guard,
                     "status": "PROMOTION-CAPABLE" if promotable else "PILOT / NOT PROMOTABLE"},
            "cells": cells, "hazard": hazard, "skips": dict(skips)}


def report(res: dict, top: int = 12) -> str:
    m = res["meta"]
    L = ["=" * 96,
         f"PHASE 5 -- UNCONDITIONAL EXECUTABLE SURFACE  |  {m['horizon']}m  |  {m['status']}",
         f"config {m['config_version']} hash={m['config_hash']}",
         f"rounds={m['rounds_with_entries']}/{m['rounds_available']}  weeks={m['calendar_weeks']}"
         f"  entries={m['eligible_entries']:,}  elapsed={m['elapsed_s']}s",
         f"side-convention guard: {m['side_convention_guard']}",
         "=" * 96]
    if "PILOT" in m["status"]:
        L += ["!! PILOT ONLY -- below the pre-declared gate "
              f"(>= {CFG.GATE['min_independent_rounds']} rounds AND "
              f">= {CFG.GATE['min_calendar_weeks']} weeks).",
              "!! These numbers may KILL a hypothesis. They may NEVER promote one.", ""]

    rows = []
    for (side, cp, tp, sl, lat, qty), c in res["cells"].items():
        if c["n"] < 20:
            continue
        ev = c["sum"] / c["n"]
        pf = (c["gross_win"] / c["gross_loss"]) if c["gross_loss"] > 1e-12 else float("inf")
        rows.append({"side": side, "cp": cp, "tp": tp, "sl": sl, "lat": lat, "qty": qty,
                     "n": c["n"], "ev_c": ev * 100, "win": 100.0 * c["wins"] / c["n"],
                     "pf": pf, "tp_rate": 100.0 * c["tp"] / c["n"],
                     "sl_rate": 100.0 * c["sl"] / c["n"],
                     "settle_rate": 100.0 * c["settle"] / c["n"],
                     "hold": c["sum_hold"] / c["n"],
                     "mfe_c": 100.0 * c["sum_mfe"] / c["n"],
                     "mae_c": 100.0 * c["sum_mae"] / c["n"]})
    rows.sort(key=lambda r: -r["ev_c"])
    pos = [r for r in rows if r["ev_c"] > 0]
    L += [f"cells with n>=20: {len(rows):,}   positive-EV cells: {len(pos):,}"
          f" ({100.0*len(pos)/len(rows):.1f}%)" if rows else "no cells met n>=20", ""]
    if rows:
        hdr = (f"{'side':<8}{'cp':>5}{'tp':>4}{'sl':>4}{'lat':>6}{'qty':>5}{'n':>7}"
               f"{'EV c':>9}{'win%':>7}{'PF':>7}{'TP%':>7}{'SL%':>7}{'SET%':>7}"
               f"{'hold s':>8}{'MFE c':>8}{'MAE c':>8}")
        L += ["TOP CELLS BY EV (ranking is NOT selection -- the null battery decides)", hdr,
              "-" * len(hdr)]
        for r in rows[:top]:
            L.append(f"{r['side']:<8}{r['cp']:>5}{r['tp']:>4}{r['sl']:>4}{r['lat']:>6}"
                     f"{r['qty']:>5}{r['n']:>7}{r['ev_c']:>9.2f}{r['win']:>7.1f}"
                     f"{r['pf']:>7.2f}{r['tp_rate']:>7.1f}{r['sl_rate']:>7.1f}"
                     f"{r['settle_rate']:>7.1f}{r['hold']:>8.1f}{r['mfe_c']:>8.2f}"
                     f"{r['mae_c']:>8.2f}")
        L += ["", "WORST CELLS BY EV"]
        for r in rows[-4:]:
            L.append(f"{r['side']:<8}{r['cp']:>5}{r['tp']:>4}{r['sl']:>4}{r['lat']:>6}"
                     f"{r['qty']:>5}{r['n']:>7}{r['ev_c']:>9.2f}{r['win']:>7.1f}"
                     f"{r['pf']:>7.2f}{r['tp_rate']:>7.1f}{r['sl_rate']:>7.1f}"
                     f"{r['settle_rate']:>7.1f}{r['hold']:>8.1f}{r['mfe_c']:>8.2f}"
                     f"{r['mae_c']:>8.2f}")

    L += ["", "FIRST-PROFITABLE-EXIT HAZARD (censored = never profitable while open)"]
    hz = sorted(res["hazard"].items(), key=lambda kv: (kv[0][0], -kv[0][1]))
    L.append(f"  {'side':<8}{'cp':>5}{'lat':>6}{'qty':>5}{'n':>7}{'ever%':>8}"
             f"{'p50 s':>8}{'p90 s':>8}")
    for (side, cp, lat, qty), h in hz:
        if h["n"] < 20 or lat != 500 or qty != 1:
            continue
        ts = sorted(h["t"])
        ever = 100.0 * len(ts) / h["n"]
        p50 = ts[len(ts)//2] if ts else float("nan")
        p90 = ts[int(len(ts)*0.9)] if ts else float("nan")
        L.append(f"  {side:<8}{cp:>5}{lat:>6}{qty:>5}{h['n']:>7}{ever:>8.1f}"
                 f"{p50:>8.1f}{p90:>8.1f}")
    L += ["", f"ENTRY SKIPS: {json.dumps(res['skips'], sort_keys=True)}"]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--horizon", type=int, default=15)
    ap.add_argument("--max-rounds", type=int, default=0)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    if not os.path.exists(a.db):
        print(f"ERROR: snapshot missing: {a.db}")
        return 1
    res = run(a.db, a.horizon, a.max_rounds)
    txt = report(res)
    print("\n" + txt)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "frozen_config.json"), "w", encoding="utf-8") as fh:
        json.dump({**CFG.frozen_config(), "config_hash": CFG.config_hash()}, fh, indent=2)
    with open(os.path.join(a.out, f"report_{a.horizon}m.txt"), "w", encoding="utf-8") as fh:
        fh.write(txt + "\n")
    ser = {"meta": res["meta"],
           "cells": [{"side": k[0], "cp": k[1], "tp": k[2], "sl": k[3], "lat": k[4],
                      "qty": k[5], **v} for k, v in res["cells"].items()],
           "skips": res["skips"]}
    with open(os.path.join(a.out, f"surface_{a.horizon}m.json"), "w", encoding="utf-8") as fh:
        json.dump(ser, fh, indent=1)
    print(f"\nwrote -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
