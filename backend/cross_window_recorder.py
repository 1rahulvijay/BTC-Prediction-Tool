"""Record synchronized Polymarket 5m/15m pairs and measure whether a guaranteed floor is buyable.

THE RELATIONSHIP
    A 15-minute round opening at T settles at T+900. A 5-minute round opening at T+600 settles at
    T+900 as well - the SAME instant, on the same oracle. Their strikes differ because each was
    fixed at its own open, ten minutes apart.

    When that happens, buying UP on the LOWER strike and DOWN on the HIGHER strike pays:

        final <= K_low            UP=0  DOWN=1   ->  $1
        K_low < final <= K_high   UP=1  DOWN=1   ->  $2
        final >  K_high           UP=1  DOWN=0   ->  $1

    The floor is $1 in every state. No directional forecast is involved: this is a logical
    relationship between two contracts, not a prediction about BTC.

    LEG ORDERING IS A SAFETY PROPERTY, NOT A DETAIL. Buying UP on the HIGHER strike inverts the
    middle band and pays $0 there. `dominance_legs` refuses anything but the dominating pairing,
    and the selftest pins the $0 case so the ordering cannot silently flip.

WHAT MAKES IT REAL OR NOT
    The floor is worth nothing unless the pair can be ACQUIRED below it. That means walked book
    prices for the same quantity on both legs, both fees, and both books observed close enough
    together to be one decision. Polymarket's crypto taker fee is 0.07 * p * (1-p) per share,
    which peaks at 1.75c per share per leg - so a pair must be buyable under about 96.5c before
    any edge exists at all.

THIS RECORDS. IT DOES NOT TRADE.
    No order-placement path is imported and none may be added here. The output is evidence for a
    later, separately preregistered study.

    python backend/cross_window_recorder.py --selftest
    python backend/cross_window_recorder.py --once
    python backend/cross_window_recorder.py --forever
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from polymarket_fee import (  # noqa: E402
    DEFAULT_CRYPTO_TAKER_FEE_RATE as FEE_RATE,
    polymarket_taker_fee_per_share,
)

DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or REPO / "data")
DB_PATH = Path(os.environ.get("BTC_CROSS_WINDOW_DB") or DATA_DIR / "cross_window.duckdb")

#: Round durations in seconds, keyed by the horizon in the slug.
DURATIONS = {5: 300, 15: 900}

#: Both books must be observed within this window to count as ONE decision. Two books read a
#: second apart are two different markets, and an "edge" built from them may never have existed
#: simultaneously.
MAX_BOOK_SKEW_MS = 1_500

#: A quote older than this is not executable evidence.
MAX_BOOK_AGE_MS = 5_000

#: Capacity curve. An edge that exists only for 10 shares is a curiosity, not a strategy.
SIZE_LADDER = (10.0, 50.0, 100.0, 500.0, 1_000.0)

#: The oracle these markets actually settle on, read from each market's resolutionSource.
#: A strike observed from anything else is a PROXY: close, usually, but a wrong strike can
#: invert the dominance ordering, and an inverted pair pays $0 in the middle band. So a proxy
#: strike is recorded as evidence and refused as a floor.
SETTLEMENT_ORACLE = "chainlink-btc-usd"
OFFICIAL_STRIKE_SOURCES = frozenset({"chainlink"})

#: The guaranteed payout floor of a correctly ordered dominance pair.
GUARANTEED_FLOOR = 1.0

SCHEMA_VERSION = "cross-window-v1"


class NotEquivalent(ValueError):
    """Settlement rules could not be proven identical. No candidate may be emitted."""


def settlement_ts(anchor_ts: int, horizon_min: int) -> int:
    """When this round settles. The pairing key."""
    if horizon_min not in DURATIONS:
        raise NotEquivalent(f"unknown horizon {horizon_min}")
    return int(anchor_ts) + DURATIONS[horizon_min]


def find_pairs(rounds) -> list[tuple[dict, dict]]:
    """(5m, 15m) rounds that settle at the SAME instant.

    Equality is exact. A one-second difference is a different settlement observation and the
    floor argument does not survive it."""
    by_settlement: dict[int, dict[int, dict]] = {}
    for r in rounds:
        try:
            horizon = int(r["horizon"])
            settle = settlement_ts(int(r["anchor_ts"]), horizon)
        except (KeyError, ValueError, NotEquivalent):
            continue
        by_settlement.setdefault(settle, {})[horizon] = r
    pairs = []
    for settle in sorted(by_settlement):
        group = by_settlement[settle]
        if 5 in group and 15 in group:
            pairs.append((group[5], group[15]))
    return pairs


def dominance_legs(strike_5m: float, strike_15m: float):
    """Which leg to BUY on each market, or None when no floor exists.

    Returns (buy_side_5m, buy_side_15m, low_strike, high_strike). UP always goes on the LOWER
    strike. The reversed pairing pays ZERO in the middle band, so it is not returned at all -
    a caller cannot obtain it by accident."""
    if strike_5m is None or strike_15m is None:
        return None
    if float(strike_5m) == float(strike_15m):
        # Identical strikes make the two markets the same claim; UP+DOWN across them is a
        # complete set, which is a different lane with a different (zero) dominance premium.
        return None
    if float(strike_5m) < float(strike_15m):
        return ("UP", "DOWN", float(strike_5m), float(strike_15m))
    return ("DOWN", "UP", float(strike_15m), float(strike_5m))


def parse_market_rules(description: str, resolution_source: str = "") -> dict:
    """Read the settlement rule out of the market text. NEVER default it.

    The live markets say: "resolve to Up if the Bitcoin price at the end of the time range ...
    is GREATER THAN OR EQUAL TO the price at the beginning of that range", resolved from the
    Chainlink BTC/USD stream.

    Two things this function exists to prevent, both of which I had wrong before reading the
    real text:

      TIES RESOLVE UP, not down. `>=` means a settlement exactly at the strike pays the UP
      holder.

      A DEFAULT IS WORSE THAN A REFUSAL HERE. Defaulting both markets to the same guess makes
      the equivalence check compare two identical wrong values and pass - agreement without
      evidence. An unparseable rule is returned as None so the caller refuses.
    """
    text = " ".join((description or "").split()).lower()
    out = {"oracle": None, "tie_rule": None, "comparator": None, "raw_ok": bool(text)}

    if "greater than or equal to" in text:
        out["comparator"] = ">="
        out["tie_rule"] = "up"          # settlement exactly at the strike resolves UP
    elif "greater than" in text:
        out["comparator"] = ">"
        out["tie_rule"] = "down"

    source = (resolution_source or "") + " " + text
    if "chain.link" in source or "chainlink" in source:
        out["oracle"] = "chainlink-btc-usd"
    elif "pyth" in source:
        out["oracle"] = "pyth-btc-usd"
    elif "binance" in source:
        out["oracle"] = "binance-btc-usdt"
    return out


def payout(final_price: float, low_strike: float, high_strike: float,
           tie_is_up: bool = False) -> float:
    """Combined payout of the correctly ordered pair at a settlement price."""
    def resolves_up(price, strike):
        return price > strike or (price == strike and tie_is_up)
    up_leg = 1.0 if resolves_up(final_price, low_strike) else 0.0
    down_leg = 0.0 if resolves_up(final_price, high_strike) else 1.0
    return up_leg + down_leg


def walk_book(asks, quantity: float):
    """Average price per share to BUY `quantity`, walking the ask ladder.

    Returns (average_price, filled_quantity, levels_consumed). Top-of-book pricing overstates
    what is available; a floor bought at an unattainable price is not a floor."""
    remaining = float(quantity)
    cost = 0.0
    levels = 0
    for price, size in sorted(asks, key=lambda level: float(level[0])):
        if remaining <= 0:
            break
        take = min(remaining, float(size))
        if take <= 0:
            continue
        cost += take * float(price)
        remaining -= take
        levels += 1
    filled = float(quantity) - remaining
    if filled <= 0:
        return (None, 0.0, 0)
    return (cost / filled, filled, levels)


def pair_cost(asks_a, asks_b, quantity: float, fee_rate: float = FEE_RATE) -> dict:
    """Total cost per pair-share, including both fees. `None` cost when depth is insufficient."""
    price_a, filled_a, levels_a = walk_book(asks_a, quantity)
    price_b, filled_b, levels_b = walk_book(asks_b, quantity)
    if price_a is None or price_b is None or filled_a < quantity or filled_b < quantity:
        return {"quantity": quantity, "cost_per_pair": None, "filled_a": filled_a,
                "filled_b": filled_b, "reason": "insufficient_depth"}
    fee_a = polymarket_taker_fee_per_share(price_a, fee_rate)
    fee_b = polymarket_taker_fee_per_share(price_b, fee_rate)
    total = price_a + price_b + fee_a + fee_b
    return {
        "quantity": quantity,
        "price_a": price_a, "price_b": price_b,
        "fee_a": fee_a, "fee_b": fee_b,
        "cost_per_pair": total,
        "levels_a": levels_a, "levels_b": levels_b,
        "filled_a": filled_a, "filled_b": filled_b,
        # Guaranteed, not expected: this is the WORST case of the pair, not an average.
        "guaranteed_edge_per_pair": GUARANTEED_FLOOR - total,
        "guaranteed_edge_usd": (GUARANTEED_FLOOR - total) * quantity,
        "reason": None,
    }


def equivalence_issues(round_5m: dict, round_15m: dict,
                       book_ts_5m: float | None = None,
                       book_ts_15m: float | None = None) -> list[str]:
    """Everything that must hold before a floor may be claimed. Fail-closed.

    The floor argument depends entirely on the two contracts resolving from the SAME observation
    of the SAME price by the SAME rule. Anything unproven is an issue, not an assumption."""
    issues: list[str] = []
    try:
        s5 = settlement_ts(int(round_5m["anchor_ts"]), 5)
        s15 = settlement_ts(int(round_15m["anchor_ts"]), 15)
        if s5 != s15:
            issues.append(f"settlement mismatch: {s5} != {s15}")
    except Exception as exc:
        issues.append(f"settlement unresolvable: {exc}")

    oracle_5 = (round_5m.get("oracle") or "").strip().lower()
    oracle_15 = (round_15m.get("oracle") or "").strip().lower()
    if not oracle_5 or not oracle_15:
        issues.append("oracle identity not recorded for both markets")
    elif oracle_5 != oracle_15:
        issues.append(f"oracle mismatch: {oracle_5!r} != {oracle_15!r}")

    tie_5 = round_5m.get("tie_rule")
    tie_15 = round_15m.get("tie_rule")
    if tie_5 is None or tie_15 is None:
        issues.append("tie rule not recorded for both markets")
    elif tie_5 != tie_15:
        issues.append(f"tie rule mismatch: {tie_5!r} != {tie_15!r}")

    for label, market in (("5m", round_5m), ("15m", round_15m)):
        if market.get("strike") is None:
            issues.append(f"{label} strike missing")
        if str(market.get("status", "")).lower() in ("closed", "resolved", "paused"):
            issues.append(f"{label} market status {market.get('status')!r} is not tradeable")

    # SIMULTANEITY IS NOT FRESHNESS. Two books captured together can BOTH be twenty seconds
    # old and still show zero skew. The first version checked only the difference between
    # them, so a pair of equally stale books passed as an executable opportunity.
    now = time.time()
    for label, book_ts in (("5m", book_ts_5m), ("15m", book_ts_15m)):
        if book_ts is None:
            issues.append(f"{label} selected-leg timestamp unknown - unknown timing is not "
                          f"evidence of freshness")
            continue
        age_ms = (now - float(book_ts)) * 1000.0
        if age_ms > MAX_BOOK_AGE_MS:
            issues.append(f"{label} selected-leg book is {age_ms:.0f}ms old "
                          f"(> {MAX_BOOK_AGE_MS}ms)")
    if book_ts_5m is not None and book_ts_15m is not None:
        skew_ms = abs(float(book_ts_5m) - float(book_ts_15m)) * 1000.0
        if skew_ms > MAX_BOOK_SKEW_MS:
            issues.append(f"book skew {skew_ms:.0f}ms > {MAX_BOOK_SKEW_MS}ms - the two books "
                          f"were not one decision")
    return issues


def evaluate(round_5m: dict, round_15m: dict, book_5m: dict, book_15m: dict,
             sizes=SIZE_LADDER, fee_rate: float = FEE_RATE) -> dict:
    """Full candidate assessment. `admissible` is False unless EVERY condition holds."""
    legs = dominance_legs(round_5m.get("strike"), round_15m.get("strike"))
    # The timestamp of the leg actually being PRICED. Taking max(up, down) per market let a
    # stale selected book inherit freshness from the unused opposite token.
    def selected_ts(book, side):
        return book.get("recv_ts_up" if side == "UP" else "recv_ts_down")
    ts_5m = selected_ts(book_5m, legs[0]) if legs else None
    ts_15m = selected_ts(book_15m, legs[1]) if legs else None
    issues = equivalence_issues(round_5m, round_15m, ts_5m, ts_15m)
    if legs is None:
        issues.append("no dominance ordering (equal or missing strikes)")

    # A proxy strike may not claim a guaranteed floor: a wrong strike can invert the
    # ordering, and the inverted pair pays $0 in the middle band.
    for label, market in (("5m", round_5m), ("15m", round_15m)):
        source = (market.get("strike_source") or "").lower()
        if market.get("strike") is not None and not any(
                official in source for official in OFFICIAL_STRIKE_SOURCES):
            issues.append(f"{label} strike came from {source or 'unknown'}, not the settlement "
                          f"oracle ({SETTLEMENT_ORACLE}) - recorded as evidence, refused as a "
                          f"floor")

    result = {
        "schema_version": SCHEMA_VERSION,
        "observed_ts": time.time(),
        "slug_5m": round_5m.get("slug"), "slug_15m": round_15m.get("slug"),
        "strike_5m": round_5m.get("strike"), "strike_15m": round_15m.get("strike"),
        "settlement_ts": None,
        "buy_side_5m": legs[0] if legs else None,
        "buy_side_15m": legs[1] if legs else None,
        "low_strike": legs[2] if legs else None,
        "high_strike": legs[3] if legs else None,
        "issues": issues,
        "admissible": False,
        "ladder": [],
        "best_edge_per_pair": None,
        "best_size": None,
    }
    try:
        result["settlement_ts"] = settlement_ts(int(round_5m["anchor_ts"]), 5)
    except Exception:
        pass
    if issues or legs is None:
        return result

    asks_5m = book_5m.get("asks_up" if legs[0] == "UP" else "asks_down") or []
    asks_15m = book_15m.get("asks_up" if legs[1] == "UP" else "asks_down") or []
    ladder = [pair_cost(asks_5m, asks_15m, size, fee_rate) for size in sizes]
    result["ladder"] = ladder
    priced = [row for row in ladder if row["cost_per_pair"] is not None]
    if not priced:
        result["issues"] = ["no size could be filled from displayed depth"]
        return result
    best = max(priced, key=lambda row: row["guaranteed_edge_per_pair"])
    result["best_edge_per_pair"] = best["guaranteed_edge_per_pair"]
    result["best_size"] = best["quantity"]
    # Admissible means "an executable pair with a positive guaranteed floor existed", NOT that
    # it should be traded. Trading needs one-leg risk, which this recorder does not model.
    result["admissible"] = best["guaranteed_edge_per_pair"] > 0
    return result


# --------------------------------------------------------------------------------------------
# Storage


DDL = """
CREATE TABLE IF NOT EXISTS cross_window_observations (
    observed_ts      DOUBLE,
    schema_version   VARCHAR,
    settlement_ts    BIGINT,
    slug_5m          VARCHAR,
    slug_15m         VARCHAR,
    strike_5m        DOUBLE,
    strike_15m       DOUBLE,
    buy_side_5m      VARCHAR,
    buy_side_15m     VARCHAR,
    low_strike       DOUBLE,
    high_strike      DOUBLE,
    admissible       BOOLEAN,
    best_size        DOUBLE,
    best_edge_per_pair DOUBLE,
    issues           VARCHAR,
    ladder_json      VARCHAR
);

CREATE TABLE IF NOT EXISTS cross_window_heartbeats (
    beat_ts_ms       BIGINT PRIMARY KEY,
    ok               BOOLEAN NOT NULL,
    observations     INTEGER NOT NULL,
    admissible       INTEGER NOT NULL,
    detail           VARCHAR
);
"""


def connect(path: Path = DB_PATH):
    import duckdb
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(DDL)
    return con


def persist(con, result: dict) -> None:
    con.execute(
        "INSERT INTO cross_window_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [result["observed_ts"], result["schema_version"], result["settlement_ts"],
         result["slug_5m"], result["slug_15m"], result["strike_5m"], result["strike_15m"],
         result["buy_side_5m"], result["buy_side_15m"], result["low_strike"],
         result["high_strike"], result["admissible"], result["best_size"],
         result["best_edge_per_pair"], json.dumps(result["issues"]),
         json.dumps(result["ladder"])])


def persist_heartbeat(con, *, ok: bool, observations: int = 0,
                      admissible: int = 0, detail: str = "") -> None:
    """Prove that a collection pass ran even when no synchronized pair existed.

    Opportunity rows are event-driven. Without this separate clock, an empty but healthy
    market interval is indistinguishable from a dead process in the system-health panel.
    """
    con.execute(
        "INSERT OR REPLACE INTO cross_window_heartbeats VALUES (?,?,?,?,?)",
        [time.time_ns() // 1_000_000, bool(ok), int(observations), int(admissible),
         str(detail)[:240]],
    )


# --------------------------------------------------------------------------------------------
# Selftest


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    # Health must advance on an empty pass; candidate rows alone are not a liveness clock.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="cross_window_selftest_") as tmp:
        test_con = connect(Path(tmp) / "cross_window.duckdb")
        try:
            persist_heartbeat(test_con, ok=True)
            beat = test_con.execute(
                "SELECT ok, observations, admissible FROM cross_window_heartbeats"
            ).fetchone()
        finally:
            test_con.close()
        check(beat == (True, 0, 0),
              "an empty healthy pass writes a heartbeat instead of looking stalled")

    # --- THE PAIRING RULE ------------------------------------------------------------
    check(settlement_ts(1_000, 5) == 1_300 and settlement_ts(400, 15) == 1_300,
          "a 5m opening at T+600 and a 15m opening at T settle at the SAME instant")
    rounds = [
        {"slug": "btc-updown-15m-400", "horizon": 15, "anchor_ts": 400},
        {"slug": "btc-updown-5m-1000", "horizon": 5, "anchor_ts": 1_000},
        {"slug": "btc-updown-5m-1300", "horizon": 5, "anchor_ts": 1_300},   # settles later
    ]
    pairs = find_pairs(rounds)
    check(len(pairs) == 1 and pairs[0][0]["anchor_ts"] == 1_000,
          "only the rounds that settle together are paired")
    check(find_pairs([rounds[0]]) == [], "a 15m with no matching 5m yields no pair")
    off_by_one = find_pairs([{"slug": "a", "horizon": 15, "anchor_ts": 400},
                             {"slug": "b", "horizon": 5, "anchor_ts": 1_001}])
    check(off_by_one == [],
          "a ONE SECOND settlement difference is not a pair - the floor argument does not "
          "survive two different settlement observations")

    # --- LEG ORDERING IS THE SAFETY PROPERTY -----------------------------------------
    legs = dominance_legs(100_000.0, 100_100.0)
    check(legs == ("UP", "DOWN", 100_000.0, 100_100.0),
          "with the 5m strike LOWER, buy UP on the 5m and DOWN on the 15m")
    legs_rev = dominance_legs(100_100.0, 100_000.0)
    check(legs_rev == ("DOWN", "UP", 100_000.0, 100_100.0),
          "with the 5m strike HIGHER the sides swap - UP always sits on the lower strike")
    check(dominance_legs(100_000.0, 100_000.0) is None,
          "EQUAL strikes give no dominance premium and are refused")
    check(dominance_legs(None, 100_000.0) is None, "a missing strike is refused")

    # --- THE PAYOFF FLOOR, EXHAUSTIVELY ----------------------------------------------
    low, high = 100_000.0, 100_100.0
    for price in (99_000, 100_000, 100_000.01, 100_050, 100_100, 100_100.01, 101_000):
        assert payout(price, low, high) >= GUARANTEED_FLOOR, price
    checks += 1
    print("  PASS  the correctly ordered pair pays at least $1 at every settlement price")
    check(payout(100_050, low, high) == 2.0,
          "and $2 inside the band between the two strikes")

    # THE REAL TIE RULE IS ">=", so verify the floor holds under it too - not only under the
    # ">" convention I assumed before reading the market text.
    for price in (99_000, 100_000, 100_050, 100_100, 101_000):
        assert payout(price, low, high, tie_is_up=True) >= GUARANTEED_FLOOR, price
    checks += 1
    print("  PASS  the floor also holds under the REAL '>=' tie rule, where ties resolve UP")

    live_text = ('This market will resolve to "Up" if the Bitcoin price at the end of the time '
                 'range specified in the title is greater than or equal to the price at the '
                 'beginning of that range. Otherwise, it will resolve to "Down". The resolution '
                 'source for this market is information from Chainlink, specifically the BTC/USD '
                 'data stream available at https://data.chain.link/streams/btc-usd.')
    rules = parse_market_rules(live_text, "https://data.chain.link/streams/btc-usd")
    check(rules["tie_rule"] == "up" and rules["comparator"] == ">=",
          "the LIVE market text parses to a tie rule of UP - I had defaulted it to DOWN, and "
          "because both markets got the same wrong default the equivalence check passed")
    check(rules["oracle"] == "chainlink-btc-usd",
          "and the oracle is read from the resolution source, not assumed")
    strict = parse_market_rules("resolves Up if the price is greater than the starting price")
    check(strict["tie_rule"] == "down" and strict["comparator"] == ">",
          "a strictly-greater-than market parses to a tie rule of DOWN - the two are "
          "distinguished, not collapsed")
    unknown = parse_market_rules("some market with no comparator language at all")
    check(unknown["tie_rule"] is None and unknown["oracle"] is None,
          "an unparseable rule returns None so the caller REFUSES - a default here would make "
          "two markets agree without evidence")

    # The INVERTED pair, which is what makes ordering load-bearing.
    def inverted(price):
        up_leg = 1.0 if price > high else 0.0
        down_leg = 0.0 if price > low else 1.0
        return up_leg + down_leg
    check(inverted(100_050) == 0.0,
          "buying UP on the HIGHER strike pays ZERO in the middle band - which is why "
          "dominance_legs never returns that ordering")

    # --- BOOK WALKING ----------------------------------------------------------------
    asks = [(0.40, 100.0), (0.42, 200.0), (0.45, 500.0)]
    price, filled, levels = walk_book(asks, 100.0)
    check(price == 0.40 and filled == 100.0 and levels == 1,
          "a size inside the top level fills at the top price")
    price, filled, levels = walk_book(asks, 250.0)
    check(abs(price - (100 * 0.40 + 150 * 0.42) / 250) < 1e-9 and levels == 2,
          "a larger size WALKS the ladder and pays a worse average")
    check(walk_book(asks, 100.0)[0] < walk_book(asks, 700.0)[0],
          "so cost rises with size - top-of-book pricing would have hidden that")
    check(walk_book([], 10.0) == (None, 0.0, 0), "an empty book fills nothing")
    check(walk_book(asks, 5_000.0)[1] == 800.0,
          "insufficient depth reports the PARTIAL fill rather than pretending")

    # --- PAIR COST AND FEES ----------------------------------------------------------
    cheap_a = [(0.45, 1_000.0)]
    cheap_b = [(0.45, 1_000.0)]
    row = pair_cost(cheap_a, cheap_b, 100.0)
    expected_fee = polymarket_taker_fee_per_share(0.45)
    check(abs(row["cost_per_pair"] - (0.90 + 2 * expected_fee)) < 1e-9,
          "pair cost includes BOTH legs and BOTH fees")
    check(row["guaranteed_edge_per_pair"] > 0,
          "a pair bought at 90c plus fees still clears the $1 floor")
    dear = pair_cost([(0.50, 1_000.0)], [(0.49, 1_000.0)], 100.0)
    check(dear["guaranteed_edge_per_pair"] < 0,
          "a pair at 99c does NOT clear once ~3.5c of fees are added - the fee is what makes "
          "most of these unprofitable")
    thin = pair_cost([(0.40, 10.0)], [(0.40, 10.0)], 100.0)
    check(thin["cost_per_pair"] is None and thin["reason"] == "insufficient_depth",
          "insufficient depth yields NO cost rather than a price for shares that do not exist")

    # --- EQUIVALENCE IS FAIL-CLOSED ---------------------------------------------------
    good_5 = {"anchor_ts": 1_000, "strike": 100_000.0, "oracle": "chainlink-btc-usd",
              "tie_rule": "down", "status": "open", "slug": "btc-updown-5m-1000",
              "strike_source": "chainlink"}
    good_15 = {"anchor_ts": 400, "strike": 100_100.0, "oracle": "chainlink-btc-usd",
               "tie_rule": "down", "status": "open", "slug": "btc-updown-15m-400",
               "strike_source": "chainlink"}
    fresh, fresh_b = time.time(), time.time() - 0.2
    check(equivalence_issues(good_5, good_15, fresh, fresh_b) == [],
          "matching settlement, oracle, tie rule and fresh books raise no issues")

    # SIMULTANEITY IS NOT FRESHNESS. Two books captured together but both long stale used to
    # pass, because only their difference was checked.
    stale = time.time() - 30.0
    stale_issues = equivalence_issues(good_5, good_15, stale, stale + 0.1)
    check(any("old" in issue for issue in stale_issues),
          "two books that are 30s old but only 100ms APART are refused - zero skew is not "
          "evidence of an executable opportunity")
    check(any("timestamp unknown" in issue for issue in
              equivalence_issues(good_5, good_15, None, fresh)),
          "an UNKNOWN leg timestamp is refused rather than assumed fresh")
    # equivalence_issues carries its OWN settlement check, independent of find_pairs. Both
    # must refuse a near-miss: a mutation relaxing this one to "within 600s" survived every
    # other check here, and 600s is exactly the 5m/15m offset - the most likely wrong pair.
    near_miss = equivalence_issues({**good_5, "anchor_ts": 1_300}, good_15, fresh, fresh)
    check(any("settlement mismatch" in issue for issue in near_miss),
          "a 300s settlement difference is refused by equivalence_issues, not only by pairing")
    off_by_600 = equivalence_issues({**good_5, "anchor_ts": 1_600}, good_15, fresh, fresh)
    check(any("settlement mismatch" in issue for issue in off_by_600),
          "...and so is a 600s difference, which is exactly the 5m/15m open offset")
    check(equivalence_issues({**good_5, "anchor_ts": 1_001}, good_15, fresh, fresh),
          "even a ONE SECOND difference is an issue - settlement equality is exact")

    check(any("oracle" in issue for issue in
              equivalence_issues({**good_5, "oracle": "pyth"}, good_15, fresh, fresh)),
          "a DIFFERENT oracle is refused - two oracles can disagree at settlement")
    check(any("oracle identity" in issue for issue in
              equivalence_issues({**good_5, "oracle": ""}, good_15, fresh, fresh)),
          "an UNRECORDED oracle is refused too - unproven is not the same as fine")
    check(any("tie rule" in issue for issue in
              equivalence_issues({**good_5, "tie_rule": "up"}, good_15, fresh, fresh)),
          "a differing tie rule is refused; it decides the exactly-at-strike case")
    check(any("skew" in issue for issue in
              equivalence_issues(good_5, good_15, fresh, fresh - 4.0)),
          "books observed 4 seconds apart are not one decision")
    check(any("status" in issue for issue in
              equivalence_issues({**good_5, "status": "closed"}, good_15, fresh, fresh)),
          "a closed market is not tradeable evidence")

    # --- END TO END -------------------------------------------------------------------
    book_5 = {"recv_ts_up": time.time(), "recv_ts_down": time.time(),
              "asks_up": [(0.44, 1_000.0)], "asks_down": [(0.60, 1_000.0)]}
    book_15 = {"recv_ts_up": time.time(), "recv_ts_down": time.time(),
               "asks_up": [(0.62, 1_000.0)], "asks_down": [(0.44, 1_000.0)]}
    result = evaluate(good_5, good_15, book_5, book_15)
    check(result["admissible"] and result["best_edge_per_pair"] > 0,
          "an 88c pair across two synchronized markets is recorded as admissible")
    check(result["buy_side_5m"] == "UP" and result["buy_side_15m"] == "DOWN",
          "and it bought the dominating ordering")
    check(len(result["ladder"]) == len(SIZE_LADDER),
          "the full capacity ladder is recorded, not just one size")

    bad = evaluate({**good_5, "oracle": "pyth"}, good_15, book_5, book_15)
    check(not bad["admissible"] and bad["ladder"] == [],
          "a non-equivalent pair is never priced at all - the issue short-circuits it")

    expensive = evaluate(good_5, good_15,
                         {"recv_ts_up": time.time(), "recv_ts_down": time.time(),
                          "asks_up": [(0.52, 1_000.0)], "asks_down": []},
                         {"recv_ts_up": time.time(), "recv_ts_down": time.time(),
                          "asks_up": [], "asks_down": [(0.50, 1_000.0)]})
    check(not expensive["admissible"] and expensive["best_edge_per_pair"] < 0,
          "a 102c pair is recorded with a NEGATIVE edge rather than dropped - the distribution "
          "of near-misses is the evidence this recorder exists to gather")

    # --- IT MUST NOT BE ABLE TO TRADE -------------------------------------------------
    # --- STRIKE OBSERVATION -----------------------------------------------------------
    _STRIKE_CACHE.clear()
    check(record_strike_observation(1_000, 100_000.0, 1_002.0),
          "a price sampled 2s after the round opens is accepted as the strike")
    check(observe_strike(1_000) == 100_000.0, "and it is remembered for later pairing")
    check(not record_strike_observation(2_000, 100_000.0, 2_060.0),
          "a sample taken 60s after the open is REFUSED - the oracle fixed at the anchor, and "
          "a late price is a different number that would shift the dominance ordering")
    check(observe_strike(2_000) is None,
          "so that round has no strike, and evaluate() will refuse it rather than guess")
    record_strike_observation(1_000, 99_999.0, 1_001.0)
    check(observe_strike(1_000) == 100_000.0,
          "the FIRST valid observation wins - a later one cannot silently move a strike that "
          "has already been used for pairing")
    check(observe_strike(999_999) is None, "an unobserved round has no strike")
    check(observe_strike_source(1_000) == "unknown",
          "a strike remembers WHERE it came from, not just its value")

    # A PROXY strike may not claim a floor. The app's price helper prefers Pyth and falls back
    # to Binance, but these markets settle on Chainlink - and a wrong strike can invert the
    # dominance ordering, which pays $0 in the middle band rather than $1.
    proxy_5 = {**good_5, "strike_source": "pyth"}
    proxy = evaluate(proxy_5, good_15, book_5, book_15)
    check(not proxy["admissible"] and any("not the settlement oracle" in i
                                          for i in proxy["issues"]),
          "a Pyth-sourced strike is recorded as evidence but REFUSED as a guaranteed floor")
    check(evaluate(good_5, good_15, book_5, book_15)["admissible"],
          "...while a chainlink-sourced strike still qualifies, so the rule is selective")

    # TIMESTAMP LAUNDERING. The pair buys UP on the 5m leg, so only recv_ts_up is relevant
    # there. Make that leg 30s stale while the UNUSED down token is fresh: a max() across both
    # tokens would report the fresh one and pass this off as an executable opportunity.
    laundered_5 = {"recv_ts_up": time.time() - 30.0, "recv_ts_down": time.time(),
                   "asks_up": [(0.44, 1_000.0)], "asks_down": [(0.60, 1_000.0)]}
    launder = evaluate(good_5, good_15, laundered_5, book_15)
    check(not launder["admissible"] and any("old" in i for i in launder["issues"]),
          "a STALE priced leg is refused even when the unused opposite token is fresh - "
          "freshness may not be inherited from a book that is not being bought")
    check(evaluate(good_5, good_15, book_5, book_15)["admissible"],
          "...and the same pair with a fresh priced leg still qualifies")

    # P0-5: a token whose book failed to fetch must not abort the pass.
    empty = evaluate(good_5, good_15,
                     {"recv_ts_up": None, "recv_ts_down": None, "asks_up": [], "asks_down": []},
                     book_15)
    check(not empty["admissible"],
          "a missing book yields a refused observation rather than an exception")

    # --- Parsed, not grepped. A raw text scan matches its OWN list of forbidden names, and it
    # would also flag a name that only ever appears inside a string. What matters is whether
    # this module can CALL or IMPORT an order-placement surface.
    import ast
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.alias):
            identifiers.add((node.asname or node.name).split(".")[-1])
    banned = {"post_order", "place_order", "create_order", "submit_order", "cancel_order",
              "ClobClient", "private_key", "sign_order"}
    leaked = identifiers & banned
    assert not leaked, f"order-placement surface reachable: {sorted(leaked)}"
    checks += 1
    print("  PASS  no order-placement name is called or imported anywhere in this module")

    # And the guard must be able to FAIL, or it proves nothing.
    probe = ast.parse("import py_clob_client as c; c.ClobClient().post_order(order)")
    probe_ids = {n.id for n in ast.walk(probe) if isinstance(n, ast.Name)}
    probe_ids |= {n.attr for n in ast.walk(probe) if isinstance(n, ast.Attribute)}
    probe_ids |= {(n.asname or n.name).split(".")[-1]
                  for n in ast.walk(probe) if isinstance(n, ast.alias)}
    check(bool(probe_ids & banned),
          "...and the same scan DOES flag a module that imports and calls a CLOB client")

    print(f"\nCROSS-WINDOW RECORDER SELFTEST: PASS ({checks} checks)")
    return 0


# --------------------------------------------------------------------------------------------
# Live collection


#: Strikes observed at round open, keyed by anchor timestamp. A round's strike is the oracle
#: price at its OWN open, so the 15m leg of a pair needs a value captured ten minutes earlier.
_STRIKE_CACHE: dict[int, tuple[float, str]] = {}
_RULES_CACHE: dict[str, dict] = {}

#: How far from the anchor an observation may be and still be treated as the strike. The oracle
#: fixes at the anchor instant; a price sampled a minute later is a different number.
MAX_STRIKE_OBSERVATION_LAG_S = 20.0


def record_strike_observation(anchor_ts: int, price: float, observed_ts: float,
                             source: str = "unknown") -> bool:
    """Remember the oracle price at a round's open. Refuses an observation taken too late.

    Returns True when stored. A strike guessed from a late sample would silently shift the
    dominance ordering, which is the one thing this recorder must get right."""
    if price is None or anchor_ts is None:
        return False
    if abs(float(observed_ts) - float(anchor_ts)) > MAX_STRIKE_OBSERVATION_LAG_S:
        return False
    _STRIKE_CACHE.setdefault(int(anchor_ts), (float(price), str(source)))
    return True


def observe_strike(anchor_ts: int):
    """The remembered strike price, or None when this round's open was never observed."""
    entry = _STRIKE_CACHE.get(int(anchor_ts))
    return entry[0] if entry else None


def observe_strike_source(anchor_ts: int):
    """Where that strike came from. `None` when unobserved."""
    entry = _STRIKE_CACHE.get(int(anchor_ts))
    return entry[1] if entry else None


def _market_rules(slug: str) -> dict:
    """Parsed settlement rules for a slug, fetched once."""
    if not slug:
        return {"oracle": None, "tie_rule": None}
    if slug in _RULES_CACHE:
        return _RULES_CACHE[slug]
    rules = {"oracle": None, "tie_rule": None}
    try:
        import requests
        payload = requests.get(
            f"https://gamma-api.polymarket.com/markets?slug={slug}", timeout=8).json()
        market = payload[0] if isinstance(payload, list) and payload else payload
        rules = parse_market_rules(market.get("description", ""),
                                   market.get("resolutionSource", ""))
        rules["status"] = "closed" if market.get("closed") else "open"
    except Exception:
        pass
    _RULES_CACHE[slug] = rules
    return rules


def collect_once(con=None, verbose: bool = True) -> list[dict]:
    """One discovery + book pass. Returns the evaluated candidates."""
    from polymarket.live_btc_updown_recorder import (discover_rounds, get_book,
                                                     get_btc)

    rounds = discover_rounds()

    # P0-1: OBSERVE the strikes. The first version defined record_strike_observation and never
    # called it outside the selftest, so _STRIKE_CACHE stayed empty in production and every
    # candidate was refused forever - a test/production parity failure that the selftest could
    # not see because it populated the cache by hand.
    #
    # A round's strike is the oracle price at ITS OWN open, so this must run on every pass: the
    # 15m leg of a pair was fixed ten minutes before the 5m leg exists.
    price, price_source = get_btc()
    now = time.time()
    for r in rounds:
        anchor = r.get("anchor_ts")
        if anchor is not None and price is not None:
            record_strike_observation(int(anchor), float(price), now, source=price_source)

    normalised = []
    for r in rounds:
        # Rules are PARSED from the market text, never defaulted. Gamma publishes no strike -
        # the market resolves against "the price at the beginning of that range" on the
        # Chainlink BTC/USD stream.
        rules = _market_rules(r.get("slug"))
        anchor = r.get("anchor_ts")
        normalised.append({
            "slug": r.get("slug"),
            "horizon": r.get("horizon"),
            "anchor_ts": anchor,
            "strike": observe_strike(int(anchor)) if anchor is not None else None,
            "strike_source": observe_strike_source(int(anchor)) if anchor is not None else None,
            "oracle": rules.get("oracle"),
            "tie_rule": rules.get("tie_rule"),
            # P0-4: status comes from the FETCHED rules. Defaulting to "open" let a closed
            # market look tradeable whenever discovery omitted the field, which it always does.
            "status": rules.get("status") or "unknown",
            "tokens": {"up": r.get("up"), "down": r.get("down")},
        })
    pairs = find_pairs(normalised)
    if verbose:
        observed = sum(1 for r in normalised if r["strike"] is not None)
        print(f"  {len(normalised)} rounds ({observed} with an observed strike, source="
              f"{price_source}) -> {len(pairs)} synchronized 5m/15m pairs")

    results = []
    for round_5m, round_15m in pairs:
        books = {}
        for label, market in (("5m", round_5m), ("15m", round_15m)):
            tokens = market.get("tokens") or {}
            # P0-5: get_book returns None on failure. Coercing to {} here keeps ONE
            # unavailable token from raising AttributeError and abandoning the whole pass -
            # "process alive" is not "evidence advancing".
            up_book = (get_book(tokens.get("up")) or {}) if tokens.get("up") else {}
            down_book = (get_book(tokens.get("down")) or {}) if tokens.get("down") else {}
            books[label] = {
                # P0-3: per-token timestamps, kept SEPARATE. A max() across both tokens let a
                # stale priced leg inherit freshness from the untouched opposite one, and
                # substituting time.time() turned missing timing into apparent freshness.
                # Absent stays absent, and equivalence_issues refuses it.
                "recv_ts_up": up_book.get("recv_ts"),
                "recv_ts_down": down_book.get("recv_ts"),
                "asks_up": up_book.get("asks") or [],
                "asks_down": down_book.get("asks") or [],
            }
        result = evaluate(round_5m, round_15m, books["5m"], books["15m"])
        results.append(result)
        if con is not None:
            persist(con, result)
        if verbose:
            edge = result["best_edge_per_pair"]
            print(f"    {result['slug_5m']} + {result['slug_15m']}  "
                  f"admissible={result['admissible']}  "
                  f"edge={f'{edge:+.4f}' if edge is not None else 'n/a'}  "
                  f"{('issues: ' + '; '.join(result['issues'])) if result['issues'] else ''}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--forever", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not (args.once or args.forever):
        parser.error("pass --selftest, --once or --forever")

    con = connect()
    print(f"cross-window recorder -> {DB_PATH}")
    print("RECORD ONLY. This module cannot place an order.")
    try:
        while True:
            try:
                results = collect_once(con)
                persist_heartbeat(
                    con,
                    ok=True,
                    observations=len(results),
                    admissible=sum(bool(row.get("admissible")) for row in results),
                )
            except Exception as exc:                     # never let one bad pass end collection
                print(f"  pass failed: {type(exc).__name__}: {str(exc)[:160]}")
                persist_heartbeat(
                    con,
                    ok=False,
                    detail=f"{type(exc).__name__}: {str(exc)[:180]}",
                )
            if args.once:
                break
            time.sleep(max(1.0, args.interval))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
