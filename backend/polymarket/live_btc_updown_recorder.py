"""
live_btc_updown_recorder.py — forward shadow recorder for Polymarket btc-updown 5m/15m.
========================================================================================
OFFLINE-SAFE, STANDALONE, SHADOW-ONLY. NOT wired into the live app. Does five jobs:
  1. discover live `btc-updown-5m/15m` rounds (Gamma)            -> pm_round_meta
  2. record UP/DOWN CLOB book every ~1-2s (bid/ask/mid/spread/depth_1c/2c/5c)
  3. join BTC anchor state (distance_from_anchor, seconds_left, current_side)
  4. join the FROZEN P(Hold) engine (p_hold_up/down/current + decision_tier) — read-only
  5. resolve each round at expiry (settled_side / up_win / oracle source)
…and precomputes the edge fields (edge_up/down at 1c/2c/3c) + a shadow label per snapshot.

The ONLY question this exists to answer (VNEXT §12b): does the Polymarket ask LAG our
calibrated P(Hold) during live running rounds, enough to clear spread? No real trades — log only.

Safety: writes ONLY to data/execution_layer.duckdb (override BTC_EXEC_DB). NEVER analytics.duckdb,
NEVER any model file. Reads persistence_model.pkl read-only. Logs raw inputs (distance, seconds_left,
vol) so P(Hold) can be recomputed exactly offline if live vol-parity drifts.

Usage:
  python backend/polymarket/live_btc_updown_recorder.py            # run continuously
  python backend/polymarket/live_btc_updown_recorder.py --smoke    # one cycle, verify, exit
  python backend/polymarket/live_btc_updown_recorder.py --report   # print edge scorecard from DB
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import deque

import numpy as np
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
DB_PATH = os.environ.get("BTC_EXEC_DB") or os.path.join(DATA_DIR, "execution_layer.duckdb")
MODEL_PATH = os.path.join(DATA_DIR, "saved_models", "persistence_model.pkl")
GAMMA = "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=500&order=startDate&ascending=false"
GAMMA_MARKET = "https://gamma-api.polymarket.com/markets?slug={slug}"
CLOB_BOOK = "https://clob.polymarket.com/book?token_id={tid}"
BINANCE = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"


# --------------------------------------------------------------------------- sources
def get_btc():
    try:
        return float(requests.get(BINANCE, timeout=5).json()["price"])
    except Exception:
        return None


def discover_rounds():
    out = []
    try:
        ev = requests.get(GAMMA, timeout=12).json()
    except Exception:
        return out
    for e in ev:
        for m in e.get("markets", []):
            slug = m.get("slug", "") or ""
            if not (slug.startswith("btc-updown-5m") or slug.startswith("btc-updown-15m")):
                continue
            try:
                toks = json.loads(m.get("clobTokenIds", "[]") or "[]")
                if len(toks) < 2:
                    continue
                anchor_ts = int(slug.split("-")[-1]); dur = 300 if "updown-5m" in slug else 900
                out.append({"slug": slug, "condition_id": m.get("conditionId", ""),
                            "horizon": dur // 60, "anchor_ts": anchor_ts, "start_ts": anchor_ts,
                            "end_ts": anchor_ts + dur, "dur": dur, "up": toks[0], "down": toks[1]})
            except Exception:
                continue
    return out


def get_book(tid):
    try:
        b = requests.get(CLOB_BOOK.format(tid=tid), timeout=6).json()
        bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), reverse=True)
        asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks", []))
        if not bids or not asks:
            return None
        bb, ba = bids[0][0], asks[0][0]
        dband = lambda lad, ref, c: sum(sz for p, sz in lad if abs(p - ref) <= c)
        return {"bid": bb, "ask": ba, "mid": (bb + ba) / 2, "spread": ba - bb,
                "top_bid_size": bids[0][1], "top_ask_size": asks[0][1],
                "d1": dband(asks, ba, 0.01), "d2": dband(asks, ba, 0.02), "d5": dband(asks, ba, 0.05)}
    except Exception:
        return None


def get_winner(slug):
    try:
        j = requests.get(GAMMA_MARKET.format(slug=slug), timeout=8).json()
        if j:
            op = json.loads(j[0].get("outcomePrices", "[]") or "[]")
            if len(op) == 2 and abs(float(op[0]) - float(op[1])) > 0.5:
                return 1 if float(op[0]) > float(op[1]) else 0
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- P(Hold)
def load_phold():
    try:
        import joblib
        return joblib.load(MODEL_PATH)
    except Exception as e:
        print(f"[recorder] WARN P(Hold) not loaded ({e}) — logging raw inputs only.")
        return None


def phold_current(model, abs_dist_pct, seconds_left, vol_pct, horizon):
    if model is None:
        return None
    try:
        dvr = abs_dist_pct / (vol_pct + 1e-6)
        X = np.array([[abs_dist_pct, seconds_left, vol_pct, horizon, dvr]], dtype=float)
        return float(model["iso"].predict(model["clf"].predict_proba(X)[:, 1])[0])
    except Exception:
        return None


def decide(p_cur, dist_pct, seconds_left):
    if abs(dist_pct) < 0.02 and seconds_left > 60:
        return "NO_TRADE", "line_risk"
    if seconds_left > 180:
        return "WAIT", "too_early"
    if p_cur is None:
        return "WATCH", "no_model"
    if p_cur >= 0.93:
        return "T3", "late_far_hold"
    if p_cur >= 0.88:
        return "T2", "moderate_hold"
    return "WATCH", "low_edge"


COLS = ("ts slug condition_id horizon anchor_ts seconds_left seconds_elapsed anchor_price btc_price "
        "distance_pct distance_bps current_side vol_60s_pct model_version p_hold_cur p_hold_up p_hold_down "
        "decision_tier no_trade_reason up_bid up_ask up_mid up_spread up_top_ask_size up_d1 up_d2 up_d5 "
        "down_bid down_ask down_mid down_spread down_top_ask_size down_d1 down_d2 down_d5 "
        "edge_up_1c edge_up_2c edge_up_3c edge_down_1c edge_down_2c edge_down_3c shadow_label").split()


def init_db():
    import duckdb
    try:
        con = duckdb.connect(DB_PATH)
    except Exception as e:
        raise SystemExit(f"[recorder] cannot open {DB_PATH} ({e}). Set BTC_EXEC_DB to a free path "
                         f"(the live app or shadow_store may hold it).")
    con.execute("CREATE TABLE IF NOT EXISTS pm_round_snapshots(" + ", ".join(
        c + (" VARCHAR" if c in ("slug", "condition_id", "model_version", "decision_tier",
                                 "no_trade_reason", "shadow_label") else " DOUBLE") for c in COLS) + ")")
    con.execute("""CREATE TABLE IF NOT EXISTS pm_round_meta(slug VARCHAR PRIMARY KEY, condition_id VARCHAR,
        horizon INT, anchor_ts BIGINT, start_ts BIGINT, end_ts BIGINT, up_token VARCHAR, down_token VARCHAR,
        discovered_ts DOUBLE)""")
    con.execute("""CREATE TABLE IF NOT EXISTS pm_round_settlements(slug VARCHAR PRIMARY KEY, horizon INT,
        anchor_ts BIGINT, anchor_price DOUBLE, expiry_btc DOUBLE, settled_side INT, up_win INT, down_win INT,
        resolution_source VARCHAR, resolved_at DOUBLE)""")
    return con


def vol60(buf):
    if len(buf) < 3:
        return 0.02
    pts = [p for t, p in buf if t >= buf[-1][0] - 60]
    if len(pts) < 3:
        return 0.02
    px = np.array(pts)
    return float(np.std(np.diff(px) / px[:-1] * 100.0)) or 0.02


# --------------------------------------------------------------------------- run
def run(poll=1.5, discover=30.0, smoke=False):
    con = init_db()
    model = load_phold()
    mver = (model or {}).get("version", "unknown") if isinstance(model, dict) else "unknown"
    buf = deque(maxlen=200)
    rounds = {}
    last_disc = 0.0
    print(f"[recorder] DB={DB_PATH}  model={'loaded:'+str(mver) if model else 'NONE'}  smoke={smoke}")
    while True:
        now = time.time()
        btc = get_btc()
        if btc:
            buf.append((now, btc))
        v = vol60(buf)
        if now - last_disc > discover or smoke:
            for r in discover_rounds():
                if r["slug"] not in rounds:
                    rounds[r["slug"]] = r
                    con.execute("INSERT OR REPLACE INTO pm_round_meta VALUES (?,?,?,?,?,?,?,?,?)",
                                [r["slug"], r["condition_id"], r["horizon"], r["anchor_ts"], r["start_ts"],
                                 r["end_ts"], r["up"], r["down"], now])
            last_disc = now
            _export(con)

        n = 0
        for slug, r in list(rounds.items()):
            elapsed = now - r["anchor_ts"]
            if "anchor_price" not in r and elapsed >= 0 and btc:
                r["anchor_price"] = btc
            if smoke and "anchor_price" not in r and btc:
                r["anchor_price"] = btc
            if (0 <= elapsed <= r["dur"] or smoke) and btc and "anchor_price" in r:
                ap = r["anchor_price"]; dist = (btc - ap) / ap * 100.0; side = 1 if dist >= 0 else 0
                sl = max(r["end_ts"] - now, 0.0)
                pc = phold_current(model, abs(dist), min(sl, r["dur"]), v, r["horizon"])
                pu = pc if side == 1 else (1 - pc if pc is not None else None)
                pd = pc if side == 0 else (1 - pc if pc is not None else None)
                tier, reason = decide(pc, dist, sl)
                ub, dbk = get_book(r["up"]), get_book(r["down"])
                if ub and dbk:
                    eu = [(pu - ub["ask"] - c if pu is not None else None) for c in (.01, .02, .03)]
                    ed = [(pd - dbk["ask"] - c if pd is not None else None) for c in (.01, .02, .03)]
                    lab = ("BUY_UP_SHADOW" if (eu[2] or -1) > 0 else
                           "BUY_DOWN_SHADOW" if (ed[2] or -1) > 0 else "NO_EDGE")
                    row = [now, slug, r["condition_id"], r["horizon"], r["anchor_ts"], sl, max(elapsed, 0), ap,
                           btc, dist, dist * 100, side, v, mver, pc, pu, pd, tier, reason,
                           ub["bid"], ub["ask"], ub["mid"], ub["spread"], ub["top_ask_size"], ub["d1"], ub["d2"], ub["d5"],
                           dbk["bid"], dbk["ask"], dbk["mid"], dbk["spread"], dbk["top_ask_size"], dbk["d1"], dbk["d2"], dbk["d5"],
                           eu[0], eu[1], eu[2], ed[0], ed[1], ed[2], lab]
                    con.execute("INSERT INTO pm_round_snapshots VALUES (" + ",".join("?" * len(COLS)) + ")", row)
                    n += 1
            if elapsed > r["dur"] + 5 and not r.get("settled"):
                wor = get_winner(slug)
                wbtc = (1 if btc and btc > r.get("anchor_price", btc) else 0) if btc else None
                sside = wor if wor is not None else wbtc
                con.execute("INSERT OR REPLACE INTO pm_round_settlements VALUES (?,?,?,?,?,?,?,?,?,?)",
                            [slug, r["horizon"], r["anchor_ts"], r.get("anchor_price"), btc, sside,
                             (1 if sside == 1 else 0), (1 if sside == 0 else 0),
                             "oracle" if wor is not None else "btc_proxy", now])
                r["settled"] = True
                if elapsed > r["dur"] + 3600:
                    rounds.pop(slug, None)

        if smoke:
            g = con.execute("SELECT count(*), round(avg(p_hold_cur),3), round(avg(up_ask),3), "
                            "count(*) FILTER(WHERE shadow_label!='NO_EDGE') FROM pm_round_snapshots").fetchone()
            print(f"[recorder] smoke: rounds={len(rounds)} snaps_written={n} btc={btc} vol60={v:.4f}")
            print(f"[recorder] pm_round_snapshots rows={g[0]} avg_p_hold={g[1]} avg_up_ask={g[2]} shadow_signals={g[3]}")
            con.close(); return
        time.sleep(poll)


def _fwd(p):
    return p.replace(chr(92), "/")


def _export(con):
    """Write parquet snapshots so --report can read while the recorder holds the live DB lock."""
    try:
        con.execute(f"COPY pm_round_snapshots TO '{_fwd(os.path.join(DATA_DIR, 'pm_export_snapshots.parquet'))}' (FORMAT PARQUET)")
        con.execute(f"COPY pm_round_settlements TO '{_fwd(os.path.join(DATA_DIR, 'pm_export_settlements.parquet'))}' (FORMAT PARQUET)")
    except Exception:
        pass


def report():
    import duckdb
    snap = os.path.join(DATA_DIR, "pm_export_snapshots.parquet")
    sett = os.path.join(DATA_DIR, "pm_export_settlements.parquet")
    try:
        con = duckdb.connect(DB_PATH, read_only=True)
        SNAP, SETT = "pm_round_snapshots", "pm_round_settlements"
    except Exception:
        if not os.path.exists(snap):
            print("[report] Live DB is locked by the running recorder and no export snapshot exists yet — "
                  "wait ~30s for the first export (it writes one each discovery cycle), then retry.")
            return
        con = duckdb.connect()
        SNAP, SETT = f"read_parquet('{_fwd(snap)}')", f"read_parquet('{_fwd(sett)}')"
        print("[report] (recorder is live — reading the periodic export snapshot)")
    nr = con.execute(f"SELECT count(DISTINCT slug) FROM {SNAP}").fetchone()[0]
    ns = con.execute(f"SELECT count(*) FROM {SNAP}").fetchone()[0]
    settled = con.execute(f"SELECT count(*) FROM {SETT}").fetchone()[0]
    print(f"=== PM recorder scorecard ===\nrounds={nr}  snapshots={ns}  settled={settled}")
    if ns:
        med = con.execute(f"SELECT round(median(up_spread),3), round(median(up_top_ask_size),0) FROM {SNAP}").fetchone()
        print(f"median up_spread={med[0]}  median top_ask_size={med[1]}")
        hi = con.execute(f"SELECT round(avg(up_ask),3) FROM {SNAP} WHERE p_hold_cur>0.95 AND current_side=1").fetchone()[0]
        print(f"avg UP ask when P(Hold_UP)>0.95: {hi}")
    if settled:
        print("\nEdge table (BUY_UP signals joined to settlement):")
        print(f"{'buffer':8}{'signals':>9}{'avg_pHold':>11}{'avg_ask':>9}{'win%':>8}{'net/contract':>14}")
        for c, col in ((1, 'edge_up_1c'), (2, 'edge_up_2c'), (3, 'edge_up_3c')):
            q = con.execute(f"""SELECT count(*), round(avg(s.p_hold_up),3), round(avg(s.up_ask),3),
                round(avg(t.up_win)*100,1), round(avg(t.up_win - s.up_ask),4)
                FROM {SNAP} s JOIN {SETT} t USING(slug)
                WHERE s.{col}>0 AND s.current_side=1""").fetchone()
            print(f"{str(c)+'c':8}{q[0] or 0:>9}{str(q[1]):>11}{str(q[2]):>9}{str(q[3]):>8}{str(q[4]):>14}")
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--poll", type=float, default=1.5)
    ap.add_argument("--discover", type=float, default=30.0)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report()
    else:
        run(poll=a.poll, discover=a.discover, smoke=a.smoke)


if __name__ == "__main__":
    main()
