"""BYBIT_L2_MAKER_V2 - the V1 maker test with a TRADE-DRIVEN fill rule.

V1 filled a resting order whenever depth at its price fell below what stood ahead. Depth
falls for two reasons and only one is an execution: a trade consumes the queue (filled), a
cancellation ahead of you advances you (nothing executed). Counting both produced a 99.5%
fill rate and biased adverse selection optimistically, because cancellation-driven "fills"
are exactly the benign ones where no aggressor traded against you.

V2 fills only on real aggressor volume reaching the price, joined from the matching Bybit
public trade tape:

    cumulative opposing-side volume at-or-through P since posting >= queue_ahead + SIZE

Cancellations are conservatively treated as NOT advancing us: aggregated L2 cannot say
whether a cancellation sat ahead of or behind our order, and assuming "ahead" is the
assumption that flatters the result.

V1 is NOT modified. Its frozen result stands as published; this is a separate, named
protocol so the two are comparable rather than one silently replacing the other.

    python research/bybit_l2_maker_v2_trade_driven.py
"""
from __future__ import annotations

import gzip
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bybit_l2_maker_v1 import (  # noqa: E402
    DAY, L2_DIR, ORDER_LIFE_MS, ORDER_SIZE, SYMBOL, ReplayInvalid, replay,
)
from bybit_trade_driven_fill import TradeTape  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TRADES = ROOT / "data" / "bybit_trades" / f"{SYMBOL}{DAY}.csv.gz"
MAKER_FEE_BPS = 1.0
MARKOUTS_S = (1, 5, 15, 30, 60)
POST_EVERY_MS = 60_000
PROTOCOL = "BYBIT_L2_MAKER_V2_TRADE_DRIVEN"


def load_tape(path: Path) -> TradeTape:
    """Bybit public trades: timestamp is float SECONDS, `side` is the AGGRESSOR side."""
    tape = TradeTape()
    with gzip.open(path, "rt") as fh:
        header = fh.readline().rstrip("\n").split(",")
        i_ts, i_side = header.index("timestamp"), header.index("side")
        i_sz, i_px = header.index("size"), header.index("price")
        for line in fh:
            f = line.rstrip("\n").split(",")
            if len(f) <= i_px:
                continue
            try:
                tape.add(int(float(f[i_ts]) * 1000.0), float(f[i_px]),
                         float(f[i_sz]), f[i_side])
            except ValueError:
                continue
    return tape.finalize()


def _executed(tape: TradeTape, side_key: str, price: float,
              t0_ms: int, t1_ms: int) -> float:
    """V1 stores side_key as 'b'/'a'; map to the tape's bids/asks convention."""
    key = "bids" if side_key.startswith("b") else "asks"
    return tape.executed_volume(key, price, t0_ms, t1_ms)


def simulate_trade_driven(path: Path, tape: TradeTape,
                          max_records: int | None = None) -> list[dict]:
    orders: list[dict] = []
    next_post_ts = None
    for ts, book in replay(path, max_records):
        try:
            bid, _bid_sz, ask, _ask_sz = book.best()
        except ReplayInvalid:
            raise
        mid = (bid + ask) / 2.0

        for order in orders:
            if order["fill_ts"] is None and not order["done"]:
                if _executed(tape, order["side_key"], order["price"],
                             order["post_ts"], ts) >= order["queue_ahead"] + ORDER_SIZE:
                    order["fill_ts"] = ts
                    order["mid_at_fill"] = mid
                elif ts - order["post_ts"] >= ORDER_LIFE_MS:
                    order["done"] = True          # cancelled unfilled
            if order["fill_ts"] and order["mid_at_fill"] is not None:
                for h in MARKOUTS_S:
                    k = f"markout_{h}s"
                    if order.get(k) is None and ts >= order["fill_ts"] + h * 1000:
                        order[k] = (order["side"] * (mid - order["mid_at_fill"])
                                    / order["mid_at_fill"] * 1e4)

        if next_post_ts is None or ts >= next_post_ts:
            next_post_ts = ts + POST_EVERY_MS
            for side, side_key, price in ((1, "b", bid), (-1, "a", ask)):
                orders.append({
                    "side": side, "side_key": side_key, "price": price,
                    "post_ts": ts, "queue_ahead": book.size_at(side_key, price),
                    "fill_ts": None, "mid_at_fill": None, "done": False,
                })
    return orders


def main() -> int:
    l2 = L2_DIR / f"{DAY}_{SYMBOL}_ob200.data.zip"
    print("=" * 78)
    print(f"{PROTOCOL}   {SYMBOL} {DAY}")
    print("=" * 78)
    if not l2.exists() or not TRADES.exists():
        print(f"  MISSING  l2={l2.exists()}  trades={TRADES.exists()}")
        return 1

    tape = load_tape(TRADES)
    print(f"  trade tape : {len(tape.ts_ms):,} prints")
    orders = simulate_trade_driven(l2, tape)
    filled = [o for o in orders if o["fill_ts"]]
    n, f = len(orders), len(filled)
    print(f"  orders     : {n:,} submitted, {f:,} filled ({f / max(n, 1):.1%})")
    if not filled:
        print("  no fills - nothing further to report")
        return 0

    for h in MARKOUTS_S:
        vals = [o[f"markout_{h}s"] for o in filled if o.get(f"markout_{h}s") is not None]
        if vals:
            print(f"    markout {h:>2}s: {statistics.mean(vals):+.4f} bps  (n={len(vals)})")

    adverse = [o["markout_60s"] for o in filled if o.get("markout_60s") is not None]
    gross = statistics.mean(adverse) if adverse else 0.0
    net_filled = gross - MAKER_FEE_BPS
    net_submitted = (gross * f / n) - (MAKER_FEE_BPS * f / n)
    print(f"\n  gross (60s markout, filled) : {gross:+.4f} bps")
    print(f"  maker fee                   : -{MAKER_FEE_BPS:.4f} bps")
    print(f"  net per FILLED order        : {net_filled:+.4f} bps")
    print(f"  net per SUBMITTED order     : {net_submitted:+.4f} bps")
    print("\n  V1 (depth rule, optimistic): 99.5% filled, net -0.916 bps/submitted")
    print("  A passive fill does not cover the 1.0 bps fee under either rule.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
