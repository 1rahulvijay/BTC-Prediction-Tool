"""Trade-driven maker fill rule for the Bybit 200-level L2 replay.

WHY THE DEPTH RULE WAS WRONG

`bybit_l2_maker_v1.simulate` fills a resting order when the size remaining at its price
falls below what stood ahead of it at posting:

    if level <= order["queue_ahead"] - ORDER_SIZE:  -> FILLED

Depth falls for two different reasons and only one of them is an execution:

    a trade consumes the queue   -> you are filled, and someone crossed against you
    an order ahead is CANCELLED  -> you advance in the queue, nothing executed

Counting cancellations as fills inflates the fill rate (measured: 99.5%) and, worse,
biases adverse selection OPTIMISTICALLY - cancellation-driven "fills" are exactly the
benign ones, because no aggressor traded against you. The fills that hurt are the ones
where someone crossed.

THE CORRECTED RULE

A passive buy at price P is filled only by SELL-side aggressor volume printed at or below
P. Fill when the cumulative aggressor volume at-or-through P since posting exceeds the
queue that stood ahead:

    cumulative_trade_volume_through_P >= queue_ahead + ORDER_SIZE   -> FILLED

Cancellations are handled conservatively: they are NOT counted as progress. We cannot tell
from aggregated L2 whether a cancellation happened ahead of us or behind us, and assuming
"ahead" is the assumption that flatters the result. Assuming "behind" is pessimistic and
honest. This makes the corrected fill rate a LOWER bound and the corrected adverse
selection a fair estimate rather than an optimistic one.

DIRECTION OF THE CORRECTION

The uncorrected result was already negative: net -0.916 bps per submitted order
(hour-block 95% CI [-1.085, -0.769]), gross operational +0.080 bps against a 1.0 bps fee.
Removing cancellation-fills can only reduce the fill rate and worsen adverse selection, so
the CONCLUSION cannot flip - a passive fill does not come close to covering the fee. What
changes is whether the number is honest, and whether later tests inherit the bias.

    python research/bybit_trade_driven_fill.py --selftest
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field


@dataclass
class TradeTape:
    """Aggressor prints, sorted by timestamp, with cumulative volume queries by price.

    Bybit `public.bybit.com/trading/` rows carry side = the AGGRESSOR side. A passive BUY
    resting at P is executed by SELL aggressors trading at or below P.
    """
    ts_ms: list[int] = field(default_factory=list)
    price: list[float] = field(default_factory=list)
    size: list[float] = field(default_factory=list)
    side: list[str] = field(default_factory=list)      # "Buy" / "Sell" aggressor

    def add(self, ts_ms: int, price: float, size: float, side: str) -> None:
        self.ts_ms.append(int(ts_ms))
        self.price.append(float(price))
        self.size.append(float(size))
        self.side.append(side)

    def finalize(self) -> "TradeTape":
        order = sorted(range(len(self.ts_ms)), key=lambda i: self.ts_ms[i])
        self.ts_ms = [self.ts_ms[i] for i in order]
        self.price = [self.price[i] for i in order]
        self.size = [self.size[i] for i in order]
        self.side = [self.side[i] for i in order]
        return self

    def _slice(self, t0_ms: int, t1_ms: int) -> range:
        lo = bisect.bisect_left(self.ts_ms, t0_ms)
        hi = bisect.bisect_right(self.ts_ms, t1_ms)
        return range(lo, hi)

    def executed_volume(self, side_key: str, price: float,
                        t0_ms: int, t1_ms: int) -> float:
        """Aggressor volume in (t0, t1] that would execute a passive order at `price`.

        side_key "bids": a resting BUY. Executed by SELL aggressors at price <= P.
        side_key "asks": a resting SELL. Executed by BUY  aggressors at price >= P.
        """
        want = "Sell" if side_key == "bids" else "Buy"
        total = 0.0
        for i in self._slice(t0_ms + 1, t1_ms):
            if self.side[i] != want:
                continue
            if side_key == "bids":
                if self.price[i] <= price:
                    total += self.size[i]
            else:
                if self.price[i] >= price:
                    total += self.size[i]
        return total


def trade_driven_fill(order: dict, now_ms: int, tape: TradeTape,
                      order_size: float) -> bool:
    """True when real aggressor volume has consumed the queue ahead AND our size.

    Deliberately ignores depth decreases. A level shrinking without a print is a
    cancellation, which does not execute anybody.
    """
    executed = tape.executed_volume(order["side_key"], order["price"],
                                    order["post_ts"], now_ms)
    return executed >= order["queue_ahead"] + order_size


def selftest() -> int:
    ok = True

    def chk(c, m):
        nonlocal ok
        print(f"  {'PASS' if c else 'FAIL'}  {m}")
        ok = ok and bool(c)

    print("bybit trade-driven fill selftest")
    SIZE = 0.01

    tape = TradeTape()
    # A resting BUY at 100.0 with 0.5 BTC ahead of it.
    order = {"side_key": "bids", "price": 100.0, "queue_ahead": 0.5, "post_ts": 1_000}

    print("\n[cancellations] depth can vanish without executing anyone")
    chk(not trade_driven_fill(order, 2_000, tape, SIZE),
        "no prints at all -> NOT filled (the old rule filled on depth alone)")

    print("\n[aggressor side] only the opposing side executes a resting order")
    t = TradeTape()
    t.add(1_500, 100.0, 5.0, "Buy")          # BUY aggressor lifts asks, not our bid
    t.finalize()
    chk(not trade_driven_fill(order, 2_000, t, SIZE),
        "BUY aggressor volume does NOT fill a resting BUY, however large")

    t = TradeTape()
    t.add(1_500, 100.0, 0.30, "Sell")
    t.finalize()
    chk(not trade_driven_fill(order, 2_000, t, SIZE),
        "SELL volume below the queue ahead leaves us unfilled")

    t = TradeTape()
    t.add(1_500, 100.0, 0.60, "Sell")        # 0.60 > 0.5 + 0.01
    t.finalize()
    chk(trade_driven_fill(order, 2_000, t, SIZE),
        "SELL volume exceeding queue_ahead + our size DOES fill us")

    print("\n[price] a print must reach our price to touch us")
    t = TradeTape()
    t.add(1_500, 100.5, 5.0, "Sell")         # sold above our bid
    t.finalize()
    chk(not trade_driven_fill(order, 2_000, t, SIZE),
        "a SELL printed ABOVE our bid does not reach us")
    t = TradeTape()
    t.add(1_500, 99.5, 5.0, "Sell")          # swept through us
    t.finalize()
    chk(trade_driven_fill(order, 2_000, t, SIZE),
        "a SELL printed THROUGH our bid does reach us")

    print("\n[time] only volume after posting counts")
    t = TradeTape()
    t.add(500, 100.0, 5.0, "Sell")           # before we posted
    t.finalize()
    chk(not trade_driven_fill(order, 2_000, t, SIZE),
        "volume printed BEFORE the order was posted does not fill it")
    chk(not trade_driven_fill(order, 1_000, t, SIZE),
        "volume exactly at post time does not count (half-open interval)")

    print("\n[asks] the mirror case")
    ask = {"side_key": "asks", "price": 100.0, "queue_ahead": 0.5, "post_ts": 1_000}
    t = TradeTape()
    t.add(1_500, 100.0, 0.60, "Buy")
    t.finalize()
    chk(trade_driven_fill(ask, 2_000, t, SIZE),
        "BUY aggressor volume fills a resting SELL")
    t = TradeTape()
    t.add(1_500, 100.0, 0.60, "Sell")
    t.finalize()
    chk(not trade_driven_fill(ask, 2_000, t, SIZE),
        "SELL aggressor volume does not fill a resting SELL")

    print("\n[accumulation] partial prints accumulate until the queue clears")
    t = TradeTape()
    for k in range(6):
        t.add(1_100 + k * 100, 100.0, 0.10, "Sell")   # 0.60 total
    t.finalize()
    chk(not trade_driven_fill(order, 1_400, t, SIZE),
        "0.30 accumulated is not yet enough")
    chk(trade_driven_fill(order, 1_600, t, SIZE),
        "0.60 accumulated crosses queue_ahead + size and fills")

    print("\nbybit-trade-driven-fill:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.parse_args()
    raise SystemExit(selftest())
