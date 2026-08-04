"""Maker execution against REAL Binance book and trades. Five fill bounds, none selected.

PROTOCOL
    docs/active/PREREG_BINANCE_MAKER_EXECUTION_V1.md, sha256 c15b4443..., frozen before any
    maker result was computed.

WHY
    MULTIHORIZON_DIRECTION_V1 measured gross edges of +0.97 to +1.97 bps against a 14 bps taker
    round trip. Every horizon was cost-dominated, so the only lever left is cost. A maker fill
    avoids crossing the spread - and fills precisely when someone informed wants the other side.
    Both halves are measured here.

HARD LIMIT, DECLARED
    The archive holds 22.9 HOURS. That is one day, so day-clustered inference is impossible and
    none is claimed; uncertainty is an HOUR-block bootstrap, which is strictly weaker.

    `recv_ts` is in SECONDS. The same unit confusion previously sent 56,467 rows to 1970 here.

    python research/maker_execution_v1.py --selftest
    python research/maker_execution_v1.py
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "multi_venue.duckdb"
PROTOCOL = "PREREG_BINANCE_MAKER_EXECUTION_V1.md"

VENUE, SYMBOL = "binance_perp", "BTCUSDT"
ORDER_INTERVAL_S = 60
ORDER_LIFE_S = 60
ORDER_SIZE = 0.01
LATENCY_MS = 250
MAKER_FEE_BPS = 1.0
TAKER_FEE_BPS = 5.5
TAKER_ROUND_TRIP_BPS = 14.0
BEST_GROSS_EDGE_BPS = 1.97          # the largest measured in MULTIHORIZON_DIRECTION_V1
MARKOUTS_S = (1, 5, 15, 30, 60)
MIN_FILL_RATE = 0.05
BOUNDS = ("NO_FILL", "IMMEDIATE", "TOUCH", "VOLUME_AHEAD", "OPERATIONAL")
HINDSIGHT_BOUNDS = frozenset({"IMMEDIATE"})


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Quotes and trades, with recv_ts converted from SECONDS to milliseconds explicitly."""
    import duckdb
    con = duckdb.connect(str(DB), read_only=True)
    try:
        quotes = con.execute(f"""
            SELECT CAST(recv_ts * 1000 AS BIGINT) AS ts_ms, bid, bid_size, ask, ask_size
            FROM venue_events
            WHERE venue = '{VENUE}' AND symbol = '{SYMBOL}' AND event = 'quote'
              AND bid IS NOT NULL AND ask IS NOT NULL AND ask > bid
            ORDER BY ts_ms""").df()
        trades = con.execute(f"""
            SELECT CAST(recv_ts * 1000 AS BIGINT) AS ts_ms, price, size, side
            FROM venue_events
            WHERE venue = '{VENUE}' AND symbol = '{SYMBOL}' AND event = 'trade'
              AND price IS NOT NULL AND size IS NOT NULL
            ORDER BY ts_ms""").df()
    finally:
        con.close()
    return quotes, trades


def simulate(quotes: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """One passive order per interval, alternating side. Returns per-order outcomes per bound."""
    q_ts = quotes["ts_ms"].to_numpy("int64")
    bid = quotes["bid"].to_numpy(float)
    ask = quotes["ask"].to_numpy(float)
    bid_sz = quotes["bid_size"].to_numpy(float)
    ask_sz = quotes["ask_size"].to_numpy(float)
    mid = (bid + ask) / 2.0

    t_ts = trades["ts_ms"].to_numpy("int64")
    t_px = trades["price"].to_numpy(float)
    t_sz = trades["size"].to_numpy(float)

    start, end = q_ts[0], q_ts[-1]
    stamps = np.arange(start + LATENCY_MS, end - ORDER_LIFE_S * 1000, ORDER_INTERVAL_S * 1000)
    rows = []
    for n, stamp in enumerate(stamps):
        side = 1 if n % 2 == 0 else -1          # alternating, exogenous by construction
        # Book as seen at decision time, then the order rests LATENCY_MS later.
        i = np.searchsorted(q_ts, stamp, side="right") - 1
        j = np.searchsorted(q_ts, stamp + LATENCY_MS, side="right") - 1
        if i < 0 or j < 0:
            continue
        post_price = bid[i] if side > 0 else ask[i]
        queue_ahead = bid_sz[i] if side > 0 else ask_sz[i]
        if queue_ahead <= ORDER_SIZE:
            continue                             # our size is not below the visible level
        spread_bps = (ask[i] - bid[i]) / mid[i] * 1e4
        # After latency, is our price still resting passively (not crossed)?
        rested = (post_price <= bid[j]) if side > 0 else (post_price >= ask[j])

        window = (t_ts > stamp + LATENCY_MS) & (t_ts <= stamp + ORDER_LIFE_S * 1000)
        w_ts, w_px, w_sz = t_ts[window], t_px[window], t_sz[window]
        # Trades at or through our resting price consume the queue ahead of us.
        through = (w_px <= post_price) if side > 0 else (w_px >= post_price)
        cum = np.cumsum(w_sz * through)

        fills = {"NO_FILL": None, "IMMEDIATE": stamp + LATENCY_MS}
        touch_idx = np.flatnonzero(through)
        fills["TOUCH"] = int(w_ts[touch_idx[0]]) if len(touch_idx) else None
        need = queue_ahead + ORDER_SIZE
        va_idx = np.flatnonzero(cum >= need)
        fills["VOLUME_AHEAD"] = int(w_ts[va_idx[0]]) if len(va_idx) else None
        fills["OPERATIONAL"] = fills["VOLUME_AHEAD"] if rested else None

        row = {"n": n, "side": side, "ts_ms": int(stamp), "hour": int(stamp // 3_600_000),
               "spread_bps": spread_bps, "post_price": post_price, "mid_at_post": mid[i]}
        for bound, fill_ts in fills.items():
            row[f"filled_{bound}"] = fill_ts is not None
            if fill_ts is None:
                row[f"net_{bound}"] = 0.0        # unfilled: no position, no PnL
                continue
            # Value of the fill: mid at settlement horizon versus our fill price, signed,
            # minus the maker fee. Markouts are measured separately.
            k = np.searchsorted(q_ts, fill_ts + MARKOUTS_S[-1] * 1000, side="right") - 1
            exit_mid = mid[min(k, len(mid) - 1)]
            gross = side * (exit_mid - post_price) / post_price * 1e4
            row[f"net_{bound}"] = gross - MAKER_FEE_BPS
            if bound == "OPERATIONAL":
                for horizon in MARKOUTS_S:
                    m = np.searchsorted(q_ts, fill_ts + horizon * 1000, side="right") - 1
                    row[f"markout_{horizon}s"] = (
                        side * (mid[min(m, len(mid) - 1)] - post_price) / post_price * 1e4)
        rows.append(row)
    return pd.DataFrame(rows)


def hour_block_ci(values: np.ndarray, hours: np.ndarray,
                  iterations: int = 2000, seed: int = 149) -> tuple:
    """Hour-block bootstrap. NOT day-clustered - the archive is a single day and cannot be."""
    unique = np.unique(hours)
    if len(unique) < 2 or len(values) == 0:
        return (float("nan"), float("nan"))
    groups = [values[hours == h] for h in unique]
    rng = np.random.default_rng(seed)
    means = np.empty(iterations)
    for k in range(iterations):
        pick = rng.integers(0, len(groups), len(groups))
        means[k] = np.concatenate([groups[j] for j in pick]).mean()
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def verdict_for(operational_net: float, op_ci: tuple, fill_rate: float,
                immediate_net: float, adverse: float, spread: float,
                gross_edge: float = BEST_GROSS_EDGE_BPS) -> tuple[str, str]:
    """`gross_edge` is a parameter ONLY so the selftest can exercise both branches.

    With the frozen constants it is 1.97 bps against a 2.0 bps maker round trip, so
    MAKER_CHANGES_THE_ARITHMETIC is unreachable in the real run - by arithmetic, before adverse
    selection is even considered. That is a result, not a defect, and it is asserted below."""
    if immediate_net <= 0:
        return ("MAKER_LOST_TO_ADVERSE_SELECTION",
                f"even the IMMEDIATE ceiling is {immediate_net:+.2f} bps - no realistic fill "
                f"model can rescue a negative optimistic bound")
    saving = spread / 2 + (TAKER_FEE_BPS - MAKER_FEE_BPS)
    if adverse >= saving:
        return ("MAKER_LOST_TO_ADVERSE_SELECTION",
                f"post-fill adverse selection {adverse:.2f} bps >= spread/2 + fee saving "
                f"{saving:.2f} bps - passive execution swaps explicit cost for informed flow")
    if fill_rate < MIN_FILL_RATE:
        return ("MAKER_FILL_RATE_INSUFFICIENT",
                f"OPERATIONAL fill rate {fill_rate:.1%} is below the declared "
                f"{MIN_FILL_RATE:.0%} floor - value per submitted order is dominated by "
                f"non-participation")
    implied_round_trip = 2 * MAKER_FEE_BPS
    if np.isfinite(op_ci[0]) and op_ci[0] > 0 and implied_round_trip < gross_edge:
        return ("MAKER_CHANGES_THE_ARITHMETIC",
                f"net per submitted order {operational_net:+.2f} bps, CI excludes zero, and the "
                f"implied round trip {implied_round_trip:.1f} bps is below the "
                f"{gross_edge:.2f} bps measured gross edge")
    return ("MAKER_SAVES_BUT_NOT_ENOUGH",
            f"implied maker round trip {implied_round_trip:.1f} bps versus a "
            f"{gross_edge:.2f} bps measured gross edge - passive execution helps and "
            f"does not close the gap")


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    check(len(BOUNDS) == 5 and "IMMEDIATE" in HINDSIGHT_BOUNDS,
          "five fill bounds, with IMMEDIATE tagged as hindsight")
    check(MAKER_FEE_BPS > 0,
          "the maker fee is CHARGED - not assumed zero because taker fees disappear")

    base = 1_785_000_000_000
    q_ts = np.arange(0, 600_000, 100, dtype="int64") + base
    quotes = pd.DataFrame({"ts_ms": q_ts, "bid": 100.0, "bid_size": 5.0,
                           "ask": 100.02, "ask_size": 5.0})
    # No trades at all: nothing can fill beyond the hindsight bound.
    empty = simulate(quotes, pd.DataFrame({"ts_ms": [], "price": [], "size": [], "side": []}))
    check(len(empty) > 0, "orders are generated on the declared grid")
    check(not empty["filled_TOUCH"].any(), "with NO trades, TOUCH never fills")
    check(not empty["filled_VOLUME_AHEAD"].any(), "...nor does VOLUME_AHEAD")
    check(empty["filled_IMMEDIATE"].all(),
          "...while IMMEDIATE fills every time, which is why it is a ceiling")
    check((empty["net_NO_FILL"] == 0).all(), "an unfilled order has exactly zero PnL")

    # A single small trade touches the level but cannot clear a 5.0 queue ahead.
    small = pd.DataFrame({"ts_ms": [base + 5_000], "price": [100.0], "size": [0.05],
                          "side": ["sell"]})
    got = simulate(quotes, small)
    buys = got[got.side == 1]
    check(bool(buys["filled_TOUCH"].iloc[0]), "a trade AT the level counts as a touch")
    check(not bool(buys["filled_VOLUME_AHEAD"].iloc[0]),
          "...but 0.05 traded cannot clear 5.0 of queue ahead - the bounds genuinely differ")

    big = pd.DataFrame({"ts_ms": [base + 5_000], "price": [99.99], "size": [20.0],
                        "side": ["sell"]})
    got = simulate(quotes, big)
    buys = got[got.side == 1]
    check(bool(buys["filled_VOLUME_AHEAD"].iloc[0]),
          "a trade THROUGH the level with size beyond the queue does fill")

    # `or True` made this unfalsifiable. The real property: two blocks with a non-zero
    # mean return a FINITE interval that brackets that mean.
    lo2, hi2 = hour_block_ci(np.full(100, 3.0), np.repeat([1, 2], 50))
    check(np.isfinite(lo2) and lo2 <= 3.0 <= hi2,
          "two blocks yield a finite interval bracketing the mean")
    lo, hi = hour_block_ci(np.zeros(100), np.repeat([1, 2], 50))
    check(lo == 0.0 and hi == 0.0, "a zero series has a zero-width interval")
    check(not np.isfinite(hour_block_ci(np.ones(10), np.ones(10))[0]),
          "ONE block yields nan, never a fabricated interval")

    kind, _ = verdict_for(1.0, (0.5, 1.5), 0.5, -1.0, 0.1, 2.0)
    check(kind == "MAKER_LOST_TO_ADVERSE_SELECTION",
          "a negative IMMEDIATE ceiling closes the lane outright")
    kind, _ = verdict_for(1.0, (0.5, 1.5), 0.5, 2.0, 9.9, 2.0)
    check(kind == "MAKER_LOST_TO_ADVERSE_SELECTION",
          "adverse selection at or above the saving closes the lane")
    kind, _ = verdict_for(1.0, (0.5, 1.5), 0.01, 2.0, 0.1, 2.0)
    check(kind == "MAKER_FILL_RATE_INSUFFICIENT",
          "a fill rate below the declared floor is insufficient participation")
    kind, _ = verdict_for(1.0, (0.5, 1.5), 0.5, 2.0, 0.1, 2.0, gross_edge=6.0)
    check(kind == "MAKER_CHANGES_THE_ARITHMETIC",
          "positive net, adequate fills and a round trip UNDER the gross edge passes")
    kind, _ = verdict_for(1.0, (0.5, 1.5), 0.5, 2.0, 0.1, 2.0)
    check(kind == "MAKER_SAVES_BUT_NOT_ENOUGH",
          "...but with the FROZEN constants a 2.0 bps maker round trip already exceeds the "
          "1.97 bps measured gross edge, so the pass branch is arithmetically unreachable")

    print(f"\nMAKER EXECUTION SELFTEST: PASS ({checks} checks)")
    return 0


def run() -> int:
    if not DB.is_file():
        print(f"missing {DB}")
        return 1
    import datetime as dt
    quotes, trades = load()
    fmt = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d %H:%M")
    print("=" * 100)
    print(f"MAKER EXECUTION V1 - protocol {PROTOCOL} (frozen before any result)")
    print("=" * 100)
    hours = (quotes.ts_ms.max() - quotes.ts_ms.min()) / 3_600_000
    print(f"  {len(quotes):,} quotes / {len(trades):,} trades   {fmt(quotes.ts_ms.min())} -> "
          f"{fmt(quotes.ts_ms.max())}  ({hours:.1f}h)")
    print("  SINGLE DAY: inference is HOUR-blocked, not day-clustered, and is weaker for it")
    print(f"  order {ORDER_SIZE} BTC every {ORDER_INTERVAL_S}s, alternating side, "
          f"{ORDER_LIFE_S}s life, {LATENCY_MS}ms latency, maker fee {MAKER_FEE_BPS} bps")

    orders = simulate(quotes, trades)
    if orders.empty:
        print("  no orders generated")
        return 1
    print(f"  {len(orders):,} orders submitted   median spread "
          f"{orders.spread_bps.median():.2f} bps")
    print()
    print(f"  {'bound':<16}{'fill rate':>11}{'net/submitted':>15}{'net/filled':>13}"
          f"   hour-block 95% CI (per submitted)")
    print("  " + "-" * 88)
    for bound in BOUNDS:
        filled = orders[f"filled_{bound}"].to_numpy(bool)
        net = orders[f"net_{bound}"].to_numpy(float)
        rate = float(filled.mean())
        per_filled = float(net[filled].mean()) if filled.any() else float("nan")
        lo, hi = hour_block_ci(net, orders["hour"].to_numpy())
        ci = f"[{lo:+6.3f}, {hi:+6.3f}]" if np.isfinite(lo) else "  (insufficient)"
        flag = "   CEILING" if bound in HINDSIGHT_BOUNDS else ""
        print(f"  {bound:<16}{rate:>10.1%}{net.mean():>15.3f}"
              f"{per_filled:>13.3f}   {ci}{flag}")

    op_filled = orders["filled_OPERATIONAL"].to_numpy(bool)
    print()
    if op_filled.any():
        print("  adverse selection on OPERATIONAL fills (mid markout, signed):")
        for horizon in MARKOUTS_S:
            column = f"markout_{horizon}s"
            if column in orders:
                print(f"    {horizon:>3}s  {orders.loc[op_filled, column].mean():+7.3f} bps")
        adverse = float(orders.loc[op_filled, "markout_1s"].mean()
                        - orders.loc[op_filled, f"markout_{MARKOUTS_S[-1]}s"].mean())
    else:
        adverse = 0.0
        print("  no OPERATIONAL fills - adverse selection is undefined")

    op_net = float(orders["net_OPERATIONAL"].mean())
    op_ci = hour_block_ci(orders["net_OPERATIONAL"].to_numpy(float),
                          orders["hour"].to_numpy())
    verdict, reason = verdict_for(op_net, op_ci, float(op_filled.mean()),
                                  float(orders["net_IMMEDIATE"].mean()), adverse,
                                  float(orders.spread_bps.median()))
    print()
    print(f"  implied maker round trip {2 * MAKER_FEE_BPS:.1f} bps vs taker "
          f"{TAKER_ROUND_TRIP_BPS:.1f} bps   measured gross edge "
          f"{BEST_GROSS_EDGE_BPS:.2f} bps")
    print(f"  VERDICT: {verdict}")
    print(f"  {reason}")
    print()
    print("  IMMEDIATE is a hindsight ceiling and is never achievable. One day of data cannot")
    print("  support a forward claim; this sizes the mechanism, it does not authorise a lane.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    raise SystemExit(selftest() if args.selftest else run())
