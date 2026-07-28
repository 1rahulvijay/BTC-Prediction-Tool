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
import sys
import tempfile
import time
from collections import deque

import numpy as np
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND_DIR = os.path.join(ROOT, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from polymarket_fee import (  # noqa: E402
    DEFAULT_CRYPTO_TAKER_FEE_RATE as CRYPTO_TAKER_FEE_RATE,
    polymarket_taker_fee_per_share,
)

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
DB_PATH = os.environ.get("BTC_EXEC_DB") or os.path.join(
    DATA_DIR, "execution_layer.duckdb"
)
MODEL_PATH = os.path.join(DATA_DIR, "saved_models", "persistence_model.pkl")
GAMMA = "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=500&order=startDate&ascending=false"
GAMMA_MARKET = "https://gamma-api.polymarket.com/markets?closed=true&slug={slug}"
GAMMA_SLUG = "https://gamma-api.polymarket.com/markets?slug={slug}"  # live (un-filtered) lookup by exact slug
CLOB_BOOK = "https://clob.polymarket.com/book?token_id={tid}"
CLOB_MARKET = "https://clob.polymarket.com/markets/{condition_id}"
BINANCE = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
PYTH = "https://hermes.pyth.network/v2/updates/price/latest"
PYTH_BTC_ID = "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43"
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "btc-polymarket-shadow-recorder/2.0"})
ANCHOR_CAPTURE_MAX_LATE_SEC = 5.0
ENTRY_FAIR_CAP = 0.91
LIVE_QUOTES_PATH = os.path.join(DATA_DIR, "pm_live_quotes.json")


# --------------------------------------------------------------------------- sources
def get_btc():
    """Return (price, source), preferring the settlement-oracle proxy used by the app."""
    try:
        data = HTTP.get(PYTH, params={"ids[]": PYTH_BTC_ID}, timeout=5).json()
        price = data["parsed"][0]["price"]
        return float(price["price"]) * (10 ** int(price["expo"])), "pyth"
    except Exception:
        try:
            return float(
                HTTP.get(BINANCE, timeout=5).json()["price"]
            ), "binance_fallback"
        except Exception:
            return None, "unavailable"


def _market_tokens(market):
    """Map Gamma token ids by outcome name instead of assuming array order."""
    try:
        outcomes = json.loads(market.get("outcomes", "[]") or "[]")
        tokens = json.loads(market.get("clobTokenIds", "[]") or "[]")
        if len(outcomes) != len(tokens):
            return None
        mapped = {
            str(outcome).strip().lower(): str(token)
            for outcome, token in zip(outcomes, tokens)
        }
        if mapped.get("up") and mapped.get("down"):
            return mapped["up"], mapped["down"]
    except Exception:
        return None
    return None


def discover_rounds():
    out = []
    try:
        ev = HTTP.get(GAMMA, timeout=12).json()
    except Exception:
        # Exact-slug discovery below is independent and must still run when the
        # broad event listing is unavailable.
        ev = []
    for e in ev:
        for m in e.get("markets", []):
            slug = m.get("slug", "") or ""
            if not (
                slug.startswith("btc-updown-5m") or slug.startswith("btc-updown-15m")
            ):
                continue
            try:
                toks = _market_tokens(m)
                if not toks:
                    continue
                anchor_ts = int(slug.split("-")[-1])
                dur = 300 if "updown-5m" in slug else 900
                out.append(
                    {
                        "slug": slug,
                        "condition_id": m.get("conditionId", ""),
                        "horizon": dur // 60,
                        "anchor_ts": anchor_ts,
                        "start_ts": anchor_ts,
                        "end_ts": anchor_ts + dur,
                        "dur": dur,
                        "up": toks[0],
                        "down": toks[1],
                    }
                )
            except Exception:
                continue
    # BUG FIX (2026-06-29): the Gamma list (order=startDate desc) surfaces only far-future pre-created
    # rounds (~23h out), so the CURRENTLY-LIVE round (0<=elapsed<=dur) was never discovered and NO
    # snapshots were ever written -> the whole mispricing dataset stayed empty. Slugs are deterministic,
    # so fetch the live 5m + 15m round directly each cycle. (Settlement coverage still comes from the
    # list path above.)
    now = int(time.time())
    have = {r["slug"] for r in out}
    for dur in (300, 900):
        current = (now // dur) * dur
        # Pre-discover the next round so its anchor can be captured at the true open.
        for anc in (current, current + dur):
            slug = f"btc-updown-{dur // 60}m-{anc}"
            if slug in have:
                continue
            try:
                j = HTTP.get(GAMMA_SLUG.format(slug=slug), timeout=8).json()
                if not j:
                    continue
                m = j[0]
                toks = _market_tokens(m)
                if not toks:
                    continue
                out.append(
                    {
                        "slug": slug,
                        "condition_id": m.get("conditionId", ""),
                        "horizon": dur // 60,
                        "anchor_ts": anc,
                        "start_ts": anc,
                        "end_ts": anc + dur,
                        "dur": dur,
                        "up": toks[0],
                        "down": toks[1],
                    }
                )
                have.add(slug)
            except Exception:
                continue
    return out


LADDER_LEVELS = 12  # kept per side; 133-deep books are ~all dust past this
_JSON = __import__("json")


def get_book(tid):
    """One /book snapshot -> top-of-book, depth bands, FULL ladders and timing provenance.

    Provenance added 2026-07-25 so that later research can reconstruct *what was knowable when*:
      recv_ms   round-trip latency of this fetch (local)
      book_ts   the venue's own timestamp for the book, when it supplies one
      book_hash the venue's book hash, for dedupe / gap detection
      ladder    full bid+ask ladders (JSON, top LADDER_LEVELS per side)
    Without the ladders every exit study had to assume one share; without the timestamps the
    age of a quote at decision time was unknowable.
    """
    try:
        t0 = time.time()
        b = HTTP.get(CLOB_BOOK.format(tid=tid), timeout=6).json()
        recv_ms = (time.time() - t0) * 1000.0
        bids = sorted(
            ((float(x["price"]), float(x["size"])) for x in b.get("bids", [])),
            reverse=True,
        )
        asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks", []))
        if not bids or not asks:
            return None
        bb, ba = bids[0][0], asks[0][0]
        dband = lambda lad, ref, c: sum(sz for p, sz in lad if abs(p - ref) <= c)
        # BID-side bands added 2026-07-25: without them every historical exit had to assume
        # 1 share, so EXIT capacity (and therefore any early-exit strategy's real size) was
        # unmeasurable. b1/b2/b5 mirror d1/d2/d5: cumulative size within 1c/2c/5c of the top.
        try:  # venue timestamp is ms in some responses, s in others
            _bts = float(b.get("timestamp") or 0.0)
            book_ts = _bts / 1000.0 if _bts > 1e11 else _bts
        except Exception:
            book_ts = 0.0
        return {
            "bid": bb,
            "ask": ba,
            "mid": (bb + ba) / 2,
            "spread": ba - bb,
            "top_bid_size": bids[0][1],
            "top_ask_size": asks[0][1],
            "d1": dband(asks, ba, 0.01),
            "d2": dband(asks, ba, 0.02),
            "d5": dband(asks, ba, 0.05),
            "b1": dband(bids, bb, 0.01),
            "b2": dband(bids, bb, 0.02),
            "b5": dband(bids, bb, 0.05),
            "recv_ms": round(recv_ms, 1),
            "book_ts": book_ts,
            "book_hash": str(b.get("hash") or "")[:32],
            "ladder": _JSON.dumps(
                {
                    "b": [[round(p, 4), round(s, 2)] for p, s in bids[:LADDER_LEVELS]],
                    "a": [[round(p, 4), round(s, 2)] for p, s in asks[:LADDER_LEVELS]],
                },
                separators=(",", ":"),
            ),
            # The recorder DB intentionally remains capped at LADDER_LEVELS.
            # The atomic bridge can carry the complete public ladder so isolated
            # execution research can grade 1/5/10-share VWAP without bloating it.
            "full_ladder": _JSON.dumps(
                {
                    "b": [[round(p, 4), round(s, 2)] for p, s in bids],
                    "a": [[round(p, 4), round(s, 2)] for p, s in asks],
                },
                separators=(",", ":"),
            ),
        }
    except Exception:
        return None


def _winner_from_tokens(tokens):
    winners = [t for t in (tokens or []) if t.get("winner") is True]
    if len(winners) != 1:
        return None
    outcome = str(winners[0].get("outcome", "")).strip().lower()
    if outcome == "up":
        return 1
    if outcome == "down":
        return 0
    return None


def _taker_fee_per_share(price, fee_rate=CRYPTO_TAKER_FEE_RATE):
    return polymarket_taker_fee_per_share(price, fee_rate)


def _write_live_quotes(now, markets):
    """Publish current executable quotes without sharing the recorder's DuckDB writer lock."""
    payload = {"version": 2, "generated_at": float(now), "markets": markets}
    os.makedirs(os.path.dirname(LIVE_QUOTES_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix="pm_live_quotes_", suffix=".json", dir=os.path.dirname(LIVE_QUOTES_PATH)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        os.replace(tmp, LIVE_QUOTES_PATH)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def get_winner(slug, condition_id=None):
    """Return (settled_side, official_source), never a price-feed proxy.

    Closed markets disappear from Gamma's default active-market query. The CLOB market
    endpoint remains addressable by the persisted condition id and exposes an explicit
    winner token, so it is the primary restart-safe settlement source.
    """
    if condition_id:
        try:
            market = HTTP.get(
                CLOB_MARKET.format(condition_id=condition_id), timeout=8
            ).json()
            winner = (
                _winner_from_tokens(market.get("tokens"))
                if market.get("closed")
                else None
            )
            if winner is not None:
                return winner, "polymarket_clob"
        except Exception:
            pass
    try:
        j = HTTP.get(GAMMA_MARKET.format(slug=slug), timeout=8).json()
        if j:
            market = j[0]
            outcomes = json.loads(market.get("outcomes", "[]") or "[]")
            prices = [
                float(x) for x in json.loads(market.get("outcomePrices", "[]") or "[]")
            ]
            if market.get("closed") and len(outcomes) == len(prices) == 2:
                winning = int(np.argmax(prices))
                if prices[winning] >= 0.99 and prices[1 - winning] <= 0.01:
                    outcome = str(outcomes[winning]).strip().lower()
                    if outcome in ("up", "down"):
                        return (1 if outcome == "up" else 0), "polymarket_gamma"
    except Exception:
        pass
    return None, None


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


COLS = (
    "ts slug condition_id horizon anchor_ts seconds_left seconds_elapsed anchor_price btc_price "
    "distance_pct distance_bps current_side vol_60s_pct model_version p_hold_cur p_hold_up p_hold_down "
    "decision_tier no_trade_reason up_bid up_ask up_mid up_spread up_top_ask_size up_d1 up_d2 up_d5 "
    "down_bid down_ask down_mid down_spread down_top_ask_size down_d1 down_d2 down_d5 "
    "edge_up_1c edge_up_2c edge_up_3c edge_down_1c edge_down_2c edge_down_3c shadow_label "
    "price_source "
    # Exit-capacity columns (2026-07-25). Every study before this date had to assume a
    # 1-share exit because only ASK-side depth was stored; these make exit VWAP - and
    # therefore the real capacity of any early-exit strategy - measurable for the first time.
    "up_top_bid_size up_b1 up_b2 up_b5 down_top_bid_size down_b1 down_b2 down_b5 "
    # Provenance + full ladders (2026-07-25). decision_ts is when the row was assembled;
    # book_age_s is how stale the older of the two books already was at that moment. Together
    # with the ladders these let a replay answer "what was actually knowable, and at what
    # price, at decision time" instead of assuming a fresh top-of-book fill.
    "decision_ts book_age_s up_recv_ms down_recv_ms up_book_ts down_book_ts "
    "up_book_hash down_book_hash up_ladder down_ladder artifact_hash"
).split()

# Text columns beyond the original set (ladders are JSON strings, hashes are hex).
_TEXT_COLS = {
    "slug",
    "condition_id",
    "model_version",
    "decision_tier",
    "no_trade_reason",
    "shadow_label",
    "price_source",
    "up_book_hash",
    "down_book_hash",
    "up_ladder",
    "down_ladder",
    "artifact_hash",
}


def _artifact_hash() -> str:
    """Short content hash of the served model bundle, so every row is attributable to weights.

    Version STRINGS collided across boxes on 2026-07-25 (same string, different weights), which
    made live results unattributable. A content hash cannot collide that way. Computed once.
    """
    import hashlib

    h = hashlib.sha256()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data = os.environ.get("BTC_DATA_DIR") or os.path.join(os.path.dirname(root), "data")
    for name in ("persistence_model.pkl", "round_state_heads.pkl"):
        p = os.path.join(data, "saved_models", name)
        try:
            with open(p, "rb") as f:
                while chunk := f.read(1 << 20):
                    h.update(chunk)
        except Exception:
            h.update(b"missing:" + name.encode())
    return h.hexdigest()[:12]


_ARTIFACT_HASH = None


def selftest_schema() -> int:
    """Guard the highest-risk invariant in this file: len(row literal) == len(COLS).

    A mismatch breaks EVERY insert at runtime, silently ending evidence collection. Verified by
    parsing the row literal with ast - an earlier ad-hoc comma counter miscounted a ternary and
    reported a false mismatch, so this must not be done with string heuristics.
    """
    import ast

    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    n = None
    for node in ast.walk(ast.parse(src)):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "row"
            and isinstance(node.value, ast.List)
        ):
            n = len(node.value.elts)
    ok = n == len(COLS)
    print(f"  {'OK  ' if ok else 'FAIL'} row literal {n} == COLS {len(COLS)}")
    txt_ok = _TEXT_COLS <= set(COLS)
    print(f"  {'OK  ' if txt_ok else 'FAIL'} every text column is declared in COLS")
    h = _artifact_hash()
    h_ok = len(h) == 12 and h == _artifact_hash()
    print(f"  {'OK  ' if h_ok else 'FAIL'} artifact hash deterministic ({h})")
    good = bool(ok and txt_ok and h_ok)
    print("\nSELFTEST", "PASS" if good else "FAIL")
    return 0 if good else 1


def init_db(path=None):
    import duckdb

    try:
        con = duckdb.connect(path or DB_PATH)
    except Exception as e:
        raise SystemExit(
            f"[recorder] cannot open {DB_PATH} ({e}). Set BTC_EXEC_DB to a free path "
            f"(the live app or shadow_store may hold it)."
        )
    con.execute(
        "CREATE TABLE IF NOT EXISTS pm_round_snapshots("
        + ", ".join(c + (" VARCHAR" if c in _TEXT_COLS else " DOUBLE") for c in COLS)
        + ")"
    )
    con.execute(
        "ALTER TABLE pm_round_snapshots ADD COLUMN IF NOT EXISTS price_source VARCHAR"
    )
    # Additive migration so an existing recorder DB gains the new columns without a rebuild;
    # pre-2026-07-25 rows keep NULL (correctly: that data was never observed).
    for _c in COLS:
        _t = "VARCHAR" if _c in _TEXT_COLS else "DOUBLE"
        con.execute(
            f"ALTER TABLE pm_round_snapshots ADD COLUMN IF NOT EXISTS {_c} {_t}"
        )
    con.execute("""CREATE TABLE IF NOT EXISTS pm_round_meta(slug VARCHAR PRIMARY KEY, condition_id VARCHAR,
        horizon INT, anchor_ts BIGINT, start_ts BIGINT, end_ts BIGINT, up_token VARCHAR, down_token VARCHAR,
        discovered_ts DOUBLE)""")
    con.execute("""CREATE TABLE IF NOT EXISTS pm_round_settlements(slug VARCHAR PRIMARY KEY, horizon INT,
        anchor_ts BIGINT, anchor_price DOUBLE, expiry_btc DOUBLE, settled_side INT, up_win INT, down_win INT,
        resolution_source VARCHAR, resolved_at DOUBLE)""")
    con.execute("""CREATE TABLE IF NOT EXISTS pm_settlement_attempts(slug VARCHAR PRIMARY KEY,
        attempts INT, last_attempt DOUBLE, last_error VARCHAR)""")
    return con


def _snapshot_prices(con, slug):
    first = con.execute(
        "SELECT anchor_price FROM pm_round_snapshots WHERE slug=? AND anchor_price IS NOT NULL "
        "ORDER BY ts LIMIT 1",
        [slug],
    ).fetchone()
    last = con.execute(
        "SELECT btc_price FROM pm_round_snapshots WHERE slug=? AND btc_price IS NOT NULL "
        "ORDER BY ts DESC LIMIT 1",
        [slug],
    ).fetchone()
    return (first[0] if first else None), (last[0] if last else None)


def pending_settlement_count(con, now=None):
    now = time.time() if now is None else float(now)
    return int(
        con.execute(
            """
        SELECT count(*) FROM pm_round_meta m
        WHERE m.end_ts <= ? AND NOT EXISTS (
            SELECT 1 FROM pm_round_settlements s WHERE s.slug=m.slug)
    """,
            [now],
        ).fetchone()[0]
    )


def resolve_pending_settlements(
    con, now=None, limit=50, retry_after=60.0, winner_fetcher=None
):
    """Resolve persisted expired rounds, including those discovered before a restart."""
    now = time.time() if now is None else float(now)
    fetcher = winner_fetcher or get_winner
    rows = con.execute(
        """
        SELECT m.slug, m.condition_id, m.horizon, m.anchor_ts
        FROM pm_round_meta m
        LEFT JOIN pm_settlement_attempts a USING(slug)
        WHERE m.end_ts <= ?
          AND NOT EXISTS (SELECT 1 FROM pm_round_settlements s WHERE s.slug=m.slug)
          AND coalesce(a.last_attempt, 0) <= ?
        ORDER BY m.end_ts
        LIMIT ?
    """,
        [now - 5.0, now - float(retry_after), int(limit)],
    ).fetchall()
    resolved = 0
    for slug, condition_id, horizon, anchor_ts in rows:
        side, source = fetcher(slug, condition_id)
        if side not in (0, 1):
            old = con.execute(
                "SELECT attempts FROM pm_settlement_attempts WHERE slug=?", [slug]
            ).fetchone()
            con.execute(
                "INSERT OR REPLACE INTO pm_settlement_attempts VALUES (?,?,?,?)",
                [slug, int(old[0] if old else 0) + 1, now, "official_result_pending"],
            )
            continue
        anchor_price, expiry_btc = _snapshot_prices(con, slug)
        con.execute(
            "INSERT OR REPLACE INTO pm_round_settlements VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                slug,
                int(horizon),
                int(anchor_ts),
                anchor_price,
                expiry_btc,
                int(side),
                int(side == 1),
                int(side == 0),
                source,
                now,
            ],
        )
        con.execute("DELETE FROM pm_settlement_attempts WHERE slug=?", [slug])
        resolved += 1
    return {
        "attempted": len(rows),
        "resolved": resolved,
        "remaining": pending_settlement_count(con, now),
    }


def vol60(buf, reference_price=None):
    """Mirror price_to_beat's P(Hold) feature: std(price over 60s)/anchor*100."""
    if len(buf) < 3:
        return 0.02
    pts = [p for t, p in buf if t >= buf[-1][0] - 60]
    if len(pts) < 3:
        return 0.02
    px = np.asarray(pts, dtype=float)
    ref = float(reference_price or px[-1])
    if not np.isfinite(ref) or ref <= 0:
        return 0.02
    return float(np.std(px) / ref * 100.0) or 0.02


# --------------------------------------------------------------------------- run
def run(poll=1.5, discover=30.0, smoke=False, settle_batch=50):
    # A smoke run must never write synthetic/future-round snapshots into the production evidence DB.
    global _ARTIFACT_HASH
    con = init_db(":memory:" if smoke else None)
    model = load_phold()
    mver = (
        (model or {}).get("version", "unknown")
        if isinstance(model, dict)
        else "unknown"
    )
    # Content hash of the served weights, stamped on every row (version strings collided across
    # boxes on 2026-07-25, making live results unattributable to specific weights).
    _ARTIFACT_HASH = _artifact_hash()
    print(f"[recorder] model={mver} artifact={_ARTIFACT_HASH}")
    buf = deque(maxlen=200)
    rounds = {}
    last_disc = 0.0
    print(
        f"[recorder] DB={DB_PATH}  model={'loaded:' + str(mver) if model else 'NONE'}  smoke={smoke}"
    )
    print(
        f"[recorder] persisted rounds={con.execute('SELECT count(*) FROM pm_round_meta').fetchone()[0]} "
        f"pending_settlements={pending_settlement_count(con)}"
    )
    while True:
        now = time.time()
        btc, price_source = get_btc()
        if btc:
            buf.append((now, btc))
        if now - last_disc > discover or smoke:
            for r in discover_rounds():
                if r["slug"] not in rounds:
                    rounds[r["slug"]] = r
                    con.execute(
                        "INSERT OR REPLACE INTO pm_round_meta VALUES (?,?,?,?,?,?,?,?,?)",
                        [
                            r["slug"],
                            r["condition_id"],
                            r["horizon"],
                            r["anchor_ts"],
                            r["start_ts"],
                            r["end_ts"],
                            r["up"],
                            r["down"],
                            now,
                        ],
                    )
            settlement = resolve_pending_settlements(con, now=now, limit=settle_batch)
            if settlement["attempted"] or settlement["remaining"]:
                print(
                    f"[settlement] attempted={settlement['attempted']} resolved={settlement['resolved']} "
                    f"remaining={settlement['remaining']}"
                )
            last_disc = now
            if not smoke:
                _export(con)

        n = 0
        live_quotes = {}
        smoke_v = 0.02
        for slug, r in list(rounds.items()):
            elapsed = now - r["anchor_ts"]
            if (
                "anchor_price" not in r
                and 0 <= elapsed <= ANCHOR_CAPTURE_MAX_LATE_SEC
                and btc
            ):
                r["anchor_price"] = btc
            elif "anchor_price" not in r and elapsed > ANCHOR_CAPTURE_MAX_LATE_SEC:
                # A current round discovered after its open has no trustworthy anchor.
                # Skip that partial round instead of manufacturing distance/P(Hold).
                r["anchor_missed"] = True
            if smoke and "anchor_price" not in r and btc:
                r["anchor_price"] = btc
            if (0 <= elapsed <= r["dur"] or smoke) and btc and "anchor_price" in r:
                ap = r["anchor_price"]
                dist = (btc - ap) / ap * 100.0
                side = 1 if dist >= 0 else 0
                sl = max(r["end_ts"] - now, 0.0)
                v = vol60(buf, ap)
                smoke_v = v
                pc = phold_current(model, abs(dist), min(sl, r["dur"]), v, r["horizon"])
                pu = pc if side == 1 else (1 - pc if pc is not None else None)
                pd = pc if side == 0 else (1 - pc if pc is not None else None)
                tier, reason = decide(pc, dist, sl)
                ub, dbk = get_book(r["up"]), get_book(r["down"])
                _dec_ts = time.time()  # both books in hand: the decision instant
                if ub and dbk:
                    fair_up = min(pu, ENTRY_FAIR_CAP) if pu is not None else None
                    fair_down = min(pd, ENTRY_FAIR_CAP) if pd is not None else None
                    fee_up = _taker_fee_per_share(ub["ask"])
                    fee_down = _taker_fee_per_share(dbk["ask"])
                    eu = [
                        (
                            fair_up - ub["ask"] - fee_up - c
                            if fair_up is not None
                            else None
                        )
                        for c in (0.01, 0.02, 0.03)
                    ]
                    ed = [
                        (
                            fair_down - dbk["ask"] - fee_down - c
                            if fair_down is not None
                            else None
                        )
                        for c in (0.01, 0.02, 0.03)
                    ]
                    lab = (
                        "BUY_UP_SHADOW"
                        if (eu[2] or -1) > 0
                        else "BUY_DOWN_SHADOW"
                        if (ed[2] or -1) > 0
                        else "NO_EDGE"
                    )
                    row = [
                        now,
                        slug,
                        r["condition_id"],
                        r["horizon"],
                        r["anchor_ts"],
                        sl,
                        max(elapsed, 0),
                        ap,
                        btc,
                        dist,
                        dist * 100,
                        side,
                        v,
                        mver,
                        pc,
                        pu,
                        pd,
                        tier,
                        reason,
                        ub["bid"],
                        ub["ask"],
                        ub["mid"],
                        ub["spread"],
                        ub["top_ask_size"],
                        ub["d1"],
                        ub["d2"],
                        ub["d5"],
                        dbk["bid"],
                        dbk["ask"],
                        dbk["mid"],
                        dbk["spread"],
                        dbk["top_ask_size"],
                        dbk["d1"],
                        dbk["d2"],
                        dbk["d5"],
                        eu[0],
                        eu[1],
                        eu[2],
                        ed[0],
                        ed[1],
                        ed[2],
                        lab,
                        price_source,
                        ub["top_bid_size"],
                        ub["b1"],
                        ub["b2"],
                        ub["b5"],
                        dbk["top_bid_size"],
                        dbk["b1"],
                        dbk["b2"],
                        dbk["b5"],
                        # provenance: when this decision row was assembled, and how stale the
                        # OLDER of the two books already was at that instant
                        _dec_ts,
                        (
                            max(_dec_ts - ub["book_ts"], _dec_ts - dbk["book_ts"])
                            if ub["book_ts"] and dbk["book_ts"]
                            else None
                        ),
                        ub["recv_ms"],
                        dbk["recv_ms"],
                        ub["book_ts"] or None,
                        dbk["book_ts"] or None,
                        ub["book_hash"],
                        dbk["book_hash"],
                        ub["ladder"],
                        dbk["ladder"],
                        _ARTIFACT_HASH,
                    ]
                    con.execute(
                        "INSERT INTO pm_round_snapshots ("
                        + ",".join(COLS)
                        + ") VALUES ("
                        + ",".join("?" * len(COLS))
                        + ")",
                        row,
                    )
                    live_quotes[str(int(r["horizon"]))] = {
                        "ts": float(now),
                        "slug": slug,
                        "condition_id": r["condition_id"],
                        "horizon": int(r["horizon"]),
                        "anchor_ts": int(r["anchor_ts"]),
                        "seconds_left": float(sl),
                        "up_bid": ub["bid"],
                        "up_ask": ub["ask"],
                        "up_spread": ub["spread"],
                        "up_top_ask_size": ub["top_ask_size"],
                        "down_bid": dbk["bid"],
                        "down_ask": dbk["ask"],
                        "down_spread": dbk["spread"],
                        "down_top_ask_size": dbk["top_ask_size"],
                        # exit-side depth on the bridge too, so the live app can size an exit
                        "up_top_bid_size": ub["top_bid_size"],
                        "up_b1": ub["b1"],
                        "up_b5": ub["b5"],
                        "down_top_bid_size": dbk["top_bid_size"],
                        "down_b1": dbk["b1"],
                        "down_b5": dbk["b5"],
                        # Complete-trade shadow lane: carry the already-recorded full
                        # 12-level ladders through the lock-free JSON bridge. The backend
                        # must not open this recorder's DuckDB writer file.
                        "up_ladder": ub["ladder"],
                        "down_ladder": dbk["ladder"],
                        "up_full_ladder": ub["full_ladder"],
                        "down_full_ladder": dbk["full_ladder"],
                        "up_book_ts": ub["book_ts"] or None,
                        "down_book_ts": dbk["book_ts"] or None,
                        "up_recv_ms": ub["recv_ms"],
                        "down_recv_ms": dbk["recv_ms"],
                        "up_book_hash": ub["book_hash"],
                        "down_book_hash": dbk["book_hash"],
                        "artifact_hash": _ARTIFACT_HASH,
                        "fees_enabled": True,
                        "fee_rate": CRYPTO_TAKER_FEE_RATE,
                        # Additive, read-only decision context for isolated research shadows.
                        # These fields do not consume repricing output and cannot alter the
                        # recorder's side, eligibility, paper ledger, or settlement logic.
                        "anchor_price": float(ap),
                        "btc_price": float(btc),
                        "distance_bps": float(dist * 100.0),
                        "vol_60s_pct": float(v),
                        "p_hold_current": float(pc) if pc is not None else None,
                        "p_up": float(pu) if pu is not None else None,
                        "p_down": float(pd) if pd is not None else None,
                        "up_edge_buffer3": float(eu[2]) if eu[2] is not None else None,
                        "down_edge_buffer3": float(ed[2])
                        if ed[2] is not None
                        else None,
                        "baseline_shadow_decision": lab,
                        "price_source": price_source,
                    }
                    n += 1
            if elapsed > r["dur"] + 3600:
                rounds.pop(slug, None)

        if smoke:
            g = con.execute(
                "SELECT count(*), round(avg(p_hold_cur),3), round(avg(up_ask),3), "
                "count(*) FILTER(WHERE shadow_label!='NO_EDGE') FROM pm_round_snapshots"
            ).fetchone()
            print(
                f"[recorder] smoke: rounds={len(rounds)} snaps_written={n} btc={btc} "
                f"price_source={price_source} vol60={smoke_v:.4f}"
            )
            print(
                f"[recorder] pm_round_snapshots rows={g[0]} avg_p_hold={g[1]} avg_up_ask={g[2]} shadow_signals={g[3]}"
            )
            con.close()
            return
        try:
            _write_live_quotes(now, live_quotes)
        except Exception as exc:
            print(f"[recorder] live quote export skipped: {exc}")
        time.sleep(poll)


def _fwd(p):
    return p.replace(chr(92), "/")


def _export(con):
    """Write parquet snapshots so --report can read while the recorder holds the live DB lock."""
    try:
        con.execute(
            f"COPY pm_round_snapshots TO '{_fwd(os.path.join(DATA_DIR, 'pm_export_snapshots.parquet'))}' (FORMAT PARQUET)"
        )
        con.execute(
            f"COPY pm_round_settlements TO '{_fwd(os.path.join(DATA_DIR, 'pm_export_settlements.parquet'))}' (FORMAT PARQUET)"
        )
    except Exception:
        pass


def settle_once(limit=1000):
    con = init_db()
    before = pending_settlement_count(con)
    result = resolve_pending_settlements(con, limit=limit, retry_after=0.0)
    _export(con)
    total = con.execute("SELECT count(*) FROM pm_round_settlements").fetchone()[0]
    con.close()
    print(
        f"[settlement] backlog_before={before} attempted={result['attempted']} "
        f"resolved={result['resolved']} remaining={result['remaining']} total={total}"
    )
    return result


def selftest():
    import tempfile

    tmp = os.path.join(
        tempfile.gettempdir(), f"pm_recorder_selftest_{os.getpid()}.duckdb"
    )
    for suffix in ("", ".wal"):
        try:
            os.remove(tmp + suffix)
        except FileNotFoundError:
            pass
    con = init_db(tmp)
    now = 10_000.0
    con.execute(
        "INSERT INTO pm_round_meta VALUES (?,?,?,?,?,?,?,?,?)",
        [
            "btc-updown-5m-test",
            "cond-test",
            5,
            9_000,
            9_000,
            9_300,
            "up-token",
            "down-token",
            9_000,
        ],
    )
    assert (
        _winner_from_tokens(
            [{"outcome": "Up", "winner": True}, {"outcome": "Down", "winner": False}]
        )
        == 1
    )
    assert _market_tokens(
        {"outcomes": '["Down","Up"]', "clobTokenIds": '["down-id","up-id"]'}
    ) == ("up-id", "down-id")
    vb = deque([(0.0, 100.0), (1.0, 101.0), (2.0, 99.0)])
    assert abs(vol60(vb, 100.0) - float(np.std([100.0, 101.0, 99.0]))) < 1e-12
    fake = lambda slug, condition_id: (0, "polymarket_clob")
    out = resolve_pending_settlements(
        con, now=now, limit=10, retry_after=0, winner_fetcher=fake
    )
    row = con.execute(
        "SELECT settled_side,resolution_source FROM pm_round_settlements"
    ).fetchone()
    assert out["resolved"] == 1 and row == (0, "polymarket_clob"), (out, row)
    con.close()
    os.remove(tmp)
    print("live_btc_updown_recorder self-test: PASS")


def report():
    import duckdb

    snap = os.path.join(DATA_DIR, "pm_export_snapshots.parquet")
    sett = os.path.join(DATA_DIR, "pm_export_settlements.parquet")
    try:
        con = duckdb.connect(DB_PATH, read_only=True)
        SNAP, SETT = "pm_round_snapshots", "pm_round_settlements"
    except Exception:
        if not os.path.exists(snap):
            print(
                "[report] Live DB is locked by the running recorder and no export snapshot exists yet — "
                "wait ~30s for the first export (it writes one each discovery cycle), then retry."
            )
            return
        con = duckdb.connect()
        SNAP, SETT = f"read_parquet('{_fwd(snap)}')", f"read_parquet('{_fwd(sett)}')"
        print("[report] (recorder is live — reading the periodic export snapshot)")
    nr = con.execute(f"SELECT count(DISTINCT slug) FROM {SNAP}").fetchone()[0]
    ns = con.execute(f"SELECT count(*) FROM {SNAP}").fetchone()[0]
    settled = con.execute(f"SELECT count(*) FROM {SETT}").fetchone()[0]
    print(
        f"=== PM recorder scorecard ===\nrounds={nr}  snapshots={ns}  settled={settled}"
    )
    if ns:
        med = con.execute(
            f"SELECT round(median(up_spread),3), round(median(up_top_ask_size),0) FROM {SNAP}"
        ).fetchone()
        print(f"median up_spread={med[0]}  median top_ask_size={med[1]}")
        hi = con.execute(
            f"SELECT round(avg(up_ask),3) FROM {SNAP} WHERE p_hold_cur>0.95 AND current_side=1"
        ).fetchone()[0]
        print(f"avg UP ask when P(Hold_UP)>0.95: {hi}")
    if settled:
        print("\nEdge table (BUY_UP signals joined to settlement):")
        print(
            f"{'buffer':8}{'signals':>9}{'avg_pHold':>11}{'avg_ask':>9}{'win%':>8}{'net/contract':>14}"
        )
        for c, col in ((1, "edge_up_1c"), (2, "edge_up_2c"), (3, "edge_up_3c")):
            q = con.execute(f"""SELECT count(*), round(avg(s.p_hold_up),3), round(avg(s.up_ask),3),
                round(avg(t.up_win)*100,1), round(avg(t.up_win - s.up_ask),4)
                FROM {SNAP} s JOIN {SETT} t USING(slug)
                WHERE s.{col}>0 AND s.current_side=1""").fetchone()
            print(
                f"{str(c) + 'c':8}{q[0] or 0:>9}{str(q[1]):>11}{str(q[2]):>9}{str(q[3]):>8}{str(q[4]):>14}"
            )
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--poll", type=float, default=1.5)
    ap.add_argument("--discover", type=float, default=30.0)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument(
        "--settle-once",
        action="store_true",
        help="resolve persisted expired rounds from official Polymarket outcomes, then exit",
    )
    ap.add_argument("--settle-limit", type=int, default=1000)
    ap.add_argument("--settle-batch", type=int, default=50)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest_schema()  # row/COLS invariant: a mismatch breaks every insert
        selftest()
    elif a.report:
        report()
    elif a.settle_once:
        settle_once(limit=a.settle_limit)
    else:
        run(
            poll=a.poll, discover=a.discover, smoke=a.smoke, settle_batch=a.settle_batch
        )


if __name__ == "__main__":
    main()
