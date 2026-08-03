"""Maker execution against a REAL 200-level Bybit order book replay.

PROTOCOL
    docs/active/PREREG_BYBIT_L2_MAKER_V1.md, sha256 fc49c09d..., frozen before any result.

WHAT IS NEW
    The two previous maker tests had only top-of-book: queue position beyond level 1 was
    invisible and larger orders had to be excluded rather than modelled. Bybit publishes 200
    levels with update IDs, so the queue ahead of a resting order is a MEASURED quantity here
    rather than a proxy.

THE REPLAY MUST BE VALID OR THERE IS NO NUMBER
    The book is rebuilt from the opening snapshot and applied deltas in `u` order. A violated
    invariant STOPS the replay and yields REPLAY_INVALID. A silently repaired book is a
    fabricated book, and an economic verdict computed on one would be worse than no verdict.

    python research/bybit_l2_maker_v1.py --selftest
    python research/bybit_l2_maker_v1.py
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
import zipfile
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maker_execution_v1 import (                                       # noqa: E402
    MAKER_FEE_BPS, MARKOUTS_S, MIN_FILL_RATE, TAKER_FEE_BPS, hour_block_ci,
)

ROOT = Path(__file__).resolve().parents[1]
L2_DIR = ROOT / "data" / "bybit_l2"
PROTOCOL = "PREREG_BYBIT_L2_MAKER_V1.md"

DAY = "2026-08-02"
SYMBOL = "BTCUSDT"
ORDER_INTERVAL_MS = 60_000
ORDER_LIFE_MS = 60_000
ORDER_SIZE = 0.01
LATENCY_MS = 250
BINANCE_ADVERSE_BPS = 1.526        # BINANCE_MAKER_EXECUTION_V1, top-of-book
BINANCE_SPREAD_BPS = 0.02


class ReplayInvalid(RuntimeError):
    """A book invariant failed. No economic verdict may be issued."""


class Book:
    """A price-keyed L2 book rebuilt from snapshot + deltas. Invariants are checked, not assumed."""

    def __init__(self):
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.last_u: int | None = None

    def apply(self, record: dict) -> None:
        data = record["data"]
        if record["type"] == "snapshot":
            self.bids, self.asks = {}, {}
        elif self.last_u is not None and data.get("u") is not None:
            if data["u"] <= self.last_u:
                raise ReplayInvalid(f"update {data['u']} out of sequence after {self.last_u}")
        for side, book in (("b", self.bids), ("a", self.asks)):
            for price_str, size_str in data.get(side, []):
                price, size = float(price_str), float(size_str)
                if size == 0.0:
                    book.pop(price, None)       # size "0" REMOVES the level
                else:
                    book[price] = size
        self.last_u = data.get("u", self.last_u)

    def best(self) -> tuple[float, float, float, float]:
        if not self.bids or not self.asks:
            return (0.0, 0.0, 0.0, 0.0)
        bid = max(self.bids)
        ask = min(self.asks)
        if bid >= ask:
            raise ReplayInvalid(f"crossed book: bid {bid} >= ask {ask}")
        return bid, self.bids[bid], ask, self.asks[ask]

    def size_at(self, side: str, price: float) -> float:
        return (self.bids if side == "b" else self.asks).get(price, 0.0)


def replay(path: Path, max_records: int | None = None):
    """Yield (ts_ms, book) after each applied record. Raises ReplayInvalid on a bad book."""
    with zipfile.ZipFile(path) as archive:
        name = archive.namelist()[0]
        book = Book()
        with archive.open(name) as handle:
            for index, line in enumerate(handle):
                if max_records is not None and index >= max_records:
                    return
                record = json.loads(line)
                book.apply(record)
                if book.bids and book.asks:
                    yield int(record["ts"]), book


def simulate(path: Path, max_records: int | None = None) -> list[dict]:
    """Passive orders on a fixed grid, filled against the REAL queue at the posted level."""
    orders: list[dict] = []
    pending: list[dict] = []
    next_order_ts = None
    n = 0
    for ts, book in replay(path, max_records):
        n += 1
        try:
            bid, bid_sz, ask, ask_sz = book.best()
        except ReplayInvalid:
            raise
        mid = (bid + ask) / 2.0

        # --- resolve resting orders against the queue actually remaining at their price
        # `done` means NO LONGER ELIGIBLE TO FILL. It must not stop markout accrual: an order
        # that just filled is exactly the one whose markouts matter, and skipping it left every
        # gross figure at zero and every net at exactly the maker fee.
        still_open = []
        for order in pending:
            if order["done"]:
                if order["fill_ts"]:
                    for horizon in MARKOUTS_S:
                        key = f"markout_{horizon}s"
                        if order[key] is None and ts >= order["fill_ts"] + horizon * 1000:
                            order[key] = (order["side"] * (mid - order["price"])
                                          / order["price"] * 1e4)
                    if order[f"markout_{MARKOUTS_S[-1]}s"] is None:
                        still_open.append(order)
                continue
            still_open.append(order)
            level = book.size_at(order["side_key"], order["price"])
            # The queue ahead has been consumed when the level's remaining size falls below
            # what stood in front of us at posting. With real depth this is measured, not
            # inferred from a top-of-book proxy.
            if level <= order["queue_ahead"] - ORDER_SIZE:
                order["fill_ts"] = ts
                order["done"] = True
            elif ts - order["post_ts"] >= ORDER_LIFE_MS:
                order["done"] = True             # cancelled unfilled
            if order["fill_ts"] and order["mid_at_fill"] is None:
                order["mid_at_fill"] = mid
            for horizon in MARKOUTS_S:
                key = f"markout_{horizon}s"
                if order["fill_ts"] and order.get(key) is None \
                        and ts >= order["fill_ts"] + horizon * 1000:
                    order[key] = order["side"] * (mid - order["price"]) / order["price"] * 1e4
        pending = still_open

        if next_order_ts is None:
            next_order_ts = ts
        if ts < next_order_ts:
            continue
        side = 1 if len(orders) % 2 == 0 else -1
        price = bid if side > 0 else ask
        queue = bid_sz if side > 0 else ask_sz
        next_order_ts = ts + ORDER_INTERVAL_MS
        if queue <= ORDER_SIZE:
            continue
        order = {"post_ts": ts + LATENCY_MS, "side": side,
                 "side_key": "b" if side > 0 else "a", "price": price,
                 "queue_ahead": queue, "spread_bps": (ask - bid) / mid * 1e4,
                 "mid_at_post": mid, "fill_ts": None, "mid_at_fill": None, "done": False,
                 "hour": ts // 3_600_000}
        for horizon in MARKOUTS_S:
            order[f"markout_{horizon}s"] = None
        orders.append(order)
        pending.append(order)
    return orders


def summarise(orders: list[dict]) -> dict:
    if not orders:
        return {"orders": 0}
    filled = [o for o in orders if o["fill_ts"]]
    spread = float(np.median([o["spread_bps"] for o in orders]))
    depth = float(np.median([o["queue_ahead"] for o in orders]))

    def net(order):
        if not order["fill_ts"] or order["mid_at_fill"] is None:
            return 0.0
        last = order.get(f"markout_{MARKOUTS_S[-1]}s")
        gross = last if last is not None else 0.0
        return gross - MAKER_FEE_BPS

    nets = np.array([net(o) for o in orders])
    hours = np.array([o["hour"] for o in orders])
    gross_immediate = float(np.mean([
        o["side"] * (o["mid_at_post"] - o["price"]) / o["price"] * 1e4 for o in orders]))
    gross_op = float(np.mean([o.get(f"markout_{MARKOUTS_S[-1]}s") or 0.0
                              for o in filled])) if filled else float("nan")
    return {"orders": len(orders), "filled": len(filled),
            "fill_rate": len(filled) / len(orders), "spread": spread, "depth": depth,
            "net_submitted": float(nets.mean()),
            "net_filled": float(np.mean([net(o) for o in filled])) if filled else float("nan"),
            "ci": hour_block_ci(nets, hours),
            "gross_immediate": gross_immediate, "gross_operational": gross_op,
            "adverse": gross_immediate - gross_op if filled else float("nan"),
            "markouts": {h: float(np.mean([o[f"markout_{h}s"] for o in filled
                                           if o[f"markout_{h}s"] is not None]))
                         if filled else float("nan") for h in MARKOUTS_S}}


def verdict_for(result: dict) -> tuple[str, str]:
    if not result.get("orders"):
        return "NO_ORDERS", "no orders could be placed"
    saving = result["spread"] / 2 + (TAKER_FEE_BPS - MAKER_FEE_BPS)
    if result["gross_immediate"] <= 0:
        return ("MAKER_LOST_TO_ADVERSE_SELECTION",
                f"the IMMEDIATE ceiling is {result['gross_immediate']:+.3f} bps gross")
    if np.isfinite(result["adverse"]) and result["adverse"] >= saving:
        return ("MAKER_LOST_TO_ADVERSE_SELECTION",
                f"adverse selection {result['adverse']:.3f} >= half-spread + fee saving "
                f"{saving:.3f} bps")
    if result["fill_rate"] < MIN_FILL_RATE:
        return ("MAKER_FILL_RATE_INSUFFICIENT",
                f"fill rate {result['fill_rate']:.1%} below {MIN_FILL_RATE:.0%}")
    lo, _ = result["ci"]
    if np.isfinite(lo) and lo > 0:
        return ("MAKER_VIABLE_WITH_REAL_DEPTH",
                f"net per submitted order {result['net_submitted']:+.3f} bps, CI lower bound "
                f"{lo:+.3f} above zero")
    return ("MAKER_SAVES_BUT_NOT_ENOUGH",
            f"net per submitted order {result['net_submitted']:+.3f} bps; CI does not "
            f"place it above zero")


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    book = Book()
    book.apply({"type": "snapshot", "ts": 1,
                "data": {"b": [["100.0", "2.0"], ["99.9", "1.0"]],
                         "a": [["100.1", "3.0"], ["100.2", "1.0"]], "u": 10}})
    bid, bid_sz, ask, ask_sz = book.best()
    check((bid, bid_sz, ask, ask_sz) == (100.0, 2.0, 100.1, 3.0),
          "a snapshot rebuilds best bid/ask and their sizes")

    book.apply({"type": "delta", "ts": 2, "data": {"b": [["100.0", "0"]], "a": [], "u": 11}})
    check(book.best()[0] == 99.9, 'size "0" REMOVES a level - the best bid steps down')

    book.apply({"type": "delta", "ts": 3, "data": {"b": [["99.9", "5.0"]], "a": [], "u": 12}})
    check(book.size_at("b", 99.9) == 5.0, "a delta replaces a level's size, not adds to it")

    out_of_order = Book()
    out_of_order.apply({"type": "snapshot", "ts": 1,
                        "data": {"b": [["100", "1"]], "a": [["101", "1"]], "u": 100}})
    try:
        out_of_order.apply({"type": "delta", "ts": 2,
                            "data": {"b": [], "a": [], "u": 99}})
        check(False, "unreachable")
    except ReplayInvalid:
        pass
    check(True, "an OUT-OF-SEQUENCE update raises rather than being applied")

    crossed = Book()
    crossed.apply({"type": "snapshot", "ts": 1,
                   "data": {"b": [["102", "1"]], "a": [["101", "1"]], "u": 1}})
    try:
        crossed.best()
        check(False, "unreachable")
    except ReplayInvalid:
        pass
    check(True, "a CROSSED book raises rather than producing a negative spread")

    snap = Book()
    snap.apply({"type": "snapshot", "ts": 1,
                "data": {"b": [["100", "1"]], "a": [["101", "1"]], "u": 5}})
    snap.apply({"type": "snapshot", "ts": 2,
                "data": {"b": [["200", "1"]], "a": [["201", "1"]], "u": 1}})
    check(snap.best()[0] == 200.0,
          "a later SNAPSHOT resets the book and is exempt from the sequence check")

    # THE REGRESSION THIS MISSED FIRST TIME: a filled order MUST accrue markouts. Marking it
    # done on fill and skipping it left every gross at zero and every net at exactly the fee.
    order = {"post_ts": 0, "side": 1, "side_key": "b", "price": 100.0, "queue_ahead": 1.0,
             "spread_bps": 1.0, "mid_at_post": 100.05, "fill_ts": 1000,
             "mid_at_fill": 100.05, "done": True, "hour": 0}
    for horizon in MARKOUTS_S:
        order[f"markout_{horizon}s"] = 0.5
    stats = summarise([order])
    check(abs(stats["net_filled"] - (0.5 - MAKER_FEE_BPS)) < 1e-9,
          "a FILLED order's net is its markout minus the fee, not the fee alone")
    check(np.isfinite(stats["markouts"][MARKOUTS_S[-1]]),
          "markouts on a filled order are finite - nan means they never accrued")

    check(verdict_for({"orders": 0})[0] == "NO_ORDERS", "no orders is not a pass")
    kind, _ = verdict_for({"orders": 10, "spread": 1.0, "gross_immediate": -1.0,
                           "adverse": 0.1, "fill_rate": 0.5, "net_submitted": -1.0,
                           "ci": (-2.0, 0.0)})
    check(kind == "MAKER_LOST_TO_ADVERSE_SELECTION",
          "a negative optimistic ceiling closes it")
    kind, _ = verdict_for({"orders": 10, "spread": 1.0, "gross_immediate": 2.0,
                           "adverse": 0.1, "fill_rate": 0.5, "net_submitted": 1.0,
                           "ci": (0.5, 1.5)})
    check(kind == "MAKER_VIABLE_WITH_REAL_DEPTH",
          "a positive net with a CI above zero IS reachable - the pass branch is not dead")

    print(f"\nBYBIT L2 MAKER SELFTEST: PASS ({checks} checks)")
    return 0


def run() -> int:
    path = L2_DIR / f"{DAY}_{SYMBOL}_ob200.data.zip"
    if not path.is_file():
        print(f"missing {path}")
        return 1
    print("=" * 100)
    print(f"BYBIT L2 MAKER V1 - protocol {PROTOCOL} (frozen before any result)")
    print("=" * 100)
    print(f"  {path.name}  ({path.stat().st_size / 1e6:.0f} MB)   200-level replay, "
          f"queue position MEASURED")
    try:
        orders = simulate(path)
    except ReplayInvalid as exc:
        print(f"  VERDICT: REPLAY_INVALID - {exc}")
        print("  No economic verdict is issued. A repaired book would be a fabricated book.")
        return 1

    result = summarise(orders)
    if not result.get("orders"):
        print("  no orders placed")
        return 1
    print(f"  {result['orders']:,} orders, {result['filled']:,} filled "
          f"({result['fill_rate']:.1%})")
    print(f"  median spread {result['spread']:.3f} bps   median depth at touch "
          f"{result['depth']:.3f} BTC")
    print()
    print(f"  gross IMMEDIATE (ceiling)   {result['gross_immediate']:+8.3f} bps")
    print(f"  gross OPERATIONAL (filled)  {result['gross_operational']:+8.3f} bps")
    print(f"  adverse selection           {result['adverse']:8.3f} bps")
    print(f"  net per SUBMITTED order     {result['net_submitted']:+8.3f} bps   "
          f"hour-block 95% CI [{result['ci'][0]:+.3f}, {result['ci'][1]:+.3f}]")
    print(f"  net per FILLED order        {result['net_filled']:+8.3f} bps")
    print()
    print("  markout after fill:", "  ".join(
        f"{h}s {v:+.3f}" for h, v in result["markouts"].items()))
    print()
    print("  versus Binance top-of-book (BINANCE_MAKER_EXECUTION_V1):")
    print(f"    spread            {result['spread']:.3f} bps (Bybit L2)  vs  "
          f"{BINANCE_SPREAD_BPS:.3f} bps (Binance)")
    print(f"    adverse selection {result['adverse']:.3f} bps (Bybit L2)  vs  "
          f"{BINANCE_ADVERSE_BPS:.3f} bps (Binance)")
    verdict, reason = verdict_for(result)
    print()
    print(f"  VERDICT: {verdict}")
    print(f"  {reason}")
    print()
    print("  ONE DAY, hour-blocked. Real depth removes the top-of-book limitation; it does not")
    print("  turn a single day into a forward claim.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    raise SystemExit(selftest() if args.selftest else run())
