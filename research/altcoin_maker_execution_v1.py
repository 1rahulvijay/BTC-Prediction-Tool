"""Does a WIDER spread make passive execution pay - or does adverse selection scale with it?

PROTOCOL
    docs/active/PREREG_ALTCOIN_MAKER_EXECUTION_V1.md, sha256 a6ce76dc..., frozen before any
    altcoin maker result was computed.

THE METHOD IS REUSED UNMODIFIED
    `simulate()` and the fill bounds come from the sealed BINANCE_MAKER_EXECUTION_V1, so any
    difference in result is attributable to the INSTRUMENT and not to the method.

THE ERA PROBLEM, AND THE CONTROL
    Binance's public bookTicker archive ends 2024-03-30. Real altcoin best-bid/ask therefore
    exists only up to then - two years before this runs. BTCUSDT is included on the SAME day so
    the altcoin comparison is within-era, and BTC-2024 vs BTC-2026 measures the era effect
    separately. Without that control, instrument and era would be confounded.

    python research/altcoin_maker_execution_v1.py --selftest
    python research/altcoin_maker_execution_v1.py
"""
from __future__ import annotations

import argparse
import io
import sys
import urllib.request
import warnings
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maker_execution_v1 import (                                       # noqa: E402
    BOUNDS, MAKER_FEE_BPS, MARKOUTS_S, MIN_FILL_RATE, TAKER_FEE_BPS,
    hour_block_ci, simulate,
)

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "altcoin_maker"
BASE = "https://data.binance.vision/data/futures/um/daily"
PROTOCOL = "PREREG_ALTCOIN_MAKER_EXECUTION_V1.md"

DAY = "2024-03-28"
SYMBOLS = ("BTCUSDT", "AVAXUSDT", "LINKUSDT", "XRPUSDT")
CONTROL = "BTCUSDT"
#: Order size as a FRACTION of the visible best level - 0.01 BTC and 0.01 LINK are not
#: comparable quantities, so a fixed coin amount would make the symbols incomparable.
SIZE_FRACTION = 0.10
BTC_2026_ADVERSE_BPS = 1.526      # measured in BINANCE_MAKER_EXECUTION_V1
BTC_2026_SPREAD_BPS = 0.02


def _download(symbol: str, kind: str) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    local = CACHE / f"{symbol}-{kind}-{DAY}.parquet"
    if local.is_file():
        return pd.read_parquet(local)
    url = f"{BASE}/{kind}/{symbol}/{symbol}-{kind}-{DAY}.zip"
    with urllib.request.urlopen(url, timeout=180) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = archive.namelist()[0]
        with archive.open(name) as handle:
            head = handle.readline().decode()
            handle.seek(0)
            has_header = any(c.isalpha() for c in head.split(",")[0])
            frame = pd.read_csv(handle, header=0 if has_header else None)
    if kind == "bookTicker":
        frame.columns = ["update_id", "bid", "bid_size", "ask", "ask_size",
                         "event_ts", "ts_ms"][:len(frame.columns)]
        frame = frame[["ts_ms", "bid", "bid_size", "ask", "ask_size"]].astype(float)
    else:
        frame.columns = ["agg_id", "price", "size", "first_id", "last_id",
                         "ts_ms", "is_buyer_maker"][:len(frame.columns)]
        frame["side"] = np.where(frame["is_buyer_maker"].astype(bool), "sell", "buy")
        frame = frame[["ts_ms", "price", "size", "side"]]
        frame[["ts_ms", "price", "size"]] = frame[["ts_ms", "price", "size"]].astype(float)
    frame["ts_ms"] = frame["ts_ms"].astype("int64")
    # Archive timestamps are microseconds in later years; normalise to milliseconds.
    if frame["ts_ms"].iloc[0] > 10**14:
        frame["ts_ms"] = frame["ts_ms"] // 1000
    frame = frame.sort_values("ts_ms").reset_index(drop=True)
    frame.to_parquet(local, index=False)
    return frame


def measure(symbol: str) -> dict:
    quotes = _download(symbol, "bookTicker")
    trades = _download(symbol, "aggTrades")
    quotes = quotes[(quotes.ask > quotes.bid) & (quotes.bid > 0)].reset_index(drop=True)
    # Size as a fraction of the visible level, so symbols are comparable.
    typical = float(np.median(np.minimum(quotes.bid_size, quotes.ask_size)))
    import maker_execution_v1 as base
    saved = base.ORDER_SIZE
    base.ORDER_SIZE = max(typical * SIZE_FRACTION, 1e-9)
    try:
        orders = simulate(quotes, trades)
    finally:
        base.ORDER_SIZE = saved
    if orders.empty:
        return {"symbol": symbol, "orders": 0}

    spread = float(orders.spread_bps.median())
    op = orders["filled_OPERATIONAL"].to_numpy(bool)
    gross_immediate = float(orders["net_IMMEDIATE"].mean()) + MAKER_FEE_BPS
    gross_operational = (float(orders.loc[op, "net_OPERATIONAL"].mean()) + MAKER_FEE_BPS
                         if op.any() else float("nan"))
    adverse = (gross_immediate - gross_operational) if op.any() else float("nan")
    result = {"symbol": symbol, "orders": len(orders), "spread": spread,
              "order_size": base.ORDER_SIZE if False else typical * SIZE_FRACTION,
              "gross_immediate": gross_immediate, "gross_operational": gross_operational,
              "adverse": adverse}
    for bound in BOUNDS:
        result[f"rate_{bound}"] = float(orders[f"filled_{bound}"].mean())
        result[f"net_{bound}"] = float(orders[f"net_{bound}"].mean())
    result["op_ci"] = hour_block_ci(orders["net_OPERATIONAL"].to_numpy(float),
                                    orders["hour"].to_numpy())
    for horizon in MARKOUTS_S:
        column = f"markout_{horizon}s"
        result[column] = float(orders.loc[op, column].mean()) if op.any() else float("nan")
    return result


def verdict_for(result: dict) -> tuple[str, str]:
    if not result.get("orders"):
        return "NO_ORDERS", "no orders could be placed"
    saving = result["spread"] / 2 + (TAKER_FEE_BPS - MAKER_FEE_BPS)
    if result["gross_immediate"] <= 0:
        return ("MAKER_LOST_TO_ADVERSE_SELECTION",
                f"the IMMEDIATE ceiling is {result['gross_immediate']:+.3f} bps gross - "
                f"negative before any realistic fill model")
    if np.isfinite(result["adverse"]) and result["adverse"] >= saving:
        return ("MAKER_LOST_TO_ADVERSE_SELECTION",
                f"adverse selection {result['adverse']:.3f} >= half-spread + fee saving "
                f"{saving:.3f} bps")
    if result["rate_OPERATIONAL"] < MIN_FILL_RATE:
        return ("MAKER_FILL_RATE_INSUFFICIENT",
                f"OPERATIONAL fill rate {result['rate_OPERATIONAL']:.1%} below "
                f"{MIN_FILL_RATE:.0%}")
    lo, _ = result["op_ci"]
    if np.isfinite(lo) and lo > 0:
        return ("MAKER_VIABLE_ON_THIS_INSTRUMENT",
                f"net per submitted order {result['net_OPERATIONAL']:+.3f} bps with a CI "
                f"lower bound {lo:+.3f} above zero")
    return ("MAKER_SAVES_BUT_NOT_ENOUGH",
            f"net per submitted order {result['net_OPERATIONAL']:+.3f} bps, CI does not "
            f"exclude zero")


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    check(CONTROL in SYMBOLS and len(SYMBOLS) == 4,
          "four symbols, with BTCUSDT as the within-era control")
    check(SIZE_FRACTION > 0 and SIZE_FRACTION < 1,
          "order size is a FRACTION of the visible level, so symbols are comparable")
    check(MAKER_FEE_BPS > 0, "the maker fee is charged, inherited from the sealed protocol")
    check(simulate.__module__ == "maker_execution_v1",
          "the simulation is REUSED from the sealed BTC protocol, not reimplemented")

    kind, _ = verdict_for({"orders": 100, "spread": 4.0, "gross_immediate": -0.5,
                           "gross_operational": -1.0, "adverse": 0.5,
                           "rate_OPERATIONAL": 0.5, "net_OPERATIONAL": -1.0,
                           "op_ci": (-2.0, 0.0)})
    check(kind == "MAKER_LOST_TO_ADVERSE_SELECTION",
          "a negative optimistic ceiling closes the instrument")
    kind, _ = verdict_for({"orders": 100, "spread": 4.0, "gross_immediate": 2.0,
                           "gross_operational": -6.0, "adverse": 8.0,
                           "rate_OPERATIONAL": 0.5, "net_OPERATIONAL": -6.0,
                           "op_ci": (-8.0, -4.0)})
    check(kind == "MAKER_LOST_TO_ADVERSE_SELECTION",
          "adverse selection exceeding half-spread plus fee saving closes it")
    kind, _ = verdict_for({"orders": 100, "spread": 4.0, "gross_immediate": 2.0,
                           "gross_operational": 1.8, "adverse": 0.2,
                           "rate_OPERATIONAL": 0.01, "net_OPERATIONAL": 0.5,
                           "op_ci": (0.2, 0.8)})
    check(kind == "MAKER_FILL_RATE_INSUFFICIENT",
          "an insufficient fill rate is not rescued by a good CI")
    kind, _ = verdict_for({"orders": 100, "spread": 4.0, "gross_immediate": 2.0,
                           "gross_operational": 1.8, "adverse": 0.2,
                           "rate_OPERATIONAL": 0.5, "net_OPERATIONAL": 0.5,
                           "op_ci": (0.2, 0.8)})
    check(kind == "MAKER_VIABLE_ON_THIS_INSTRUMENT",
          "positive net with a CI above zero and adequate fills IS reachable here")
    kind, _ = verdict_for({"orders": 100, "spread": 4.0, "gross_immediate": 2.0,
                           "gross_operational": 1.8, "adverse": 0.2,
                           "rate_OPERATIONAL": 0.5, "net_OPERATIONAL": 0.1,
                           "op_ci": (-0.3, 0.5)})
    check(kind == "MAKER_SAVES_BUT_NOT_ENOUGH", "a CI spanning zero does not pass")
    check(verdict_for({"orders": 0})[0] == "NO_ORDERS", "no orders is not a pass")

    print(f"\nALTCOIN MAKER SELFTEST: PASS ({checks} checks)")
    return 0


def run() -> int:
    print("=" * 104)
    print(f"ALTCOIN MAKER EXECUTION V1 - protocol {PROTOCOL} (frozen before any result)")
    print("=" * 104)
    print(f"  day {DAY} (bookTicker archive ends 2024-03-30 - TWO YEARS before this run)")
    print("  BTCUSDT on the same day is the WITHIN-ERA control; method reused unmodified")
    print(f"  order size = {SIZE_FRACTION:.0%} of the median visible best level")
    print()

    results = []
    for symbol in SYMBOLS:
        print(f"  {symbol:<10}", end="", flush=True)
        try:
            result = measure(symbol)
        except Exception as exc:
            print(f"FAILED {str(exc)[:60]}")
            continue
        results.append(result)
        print(f"{result.get('orders', 0):>6,} orders   median spread "
              f"{result.get('spread', float('nan')):.3f} bps")

    if not results:
        print("  no symbol produced a result")
        return 1

    print()
    print(f"  {'symbol':<10}{'spread':>9}{'fill%':>8}{'gross imm':>11}{'gross op':>10}"
          f"{'adverse':>9}{'net/sub':>9}   hour-block 95% CI")
    print("  " + "-" * 94)
    for r in results:
        if not r.get("orders"):
            continue
        lo, hi = r["op_ci"]
        ci = f"[{lo:+6.3f}, {hi:+6.3f}]" if np.isfinite(lo) else "  (insufficient)"
        print(f"  {r['symbol']:<10}{r['spread']:>9.3f}{r['rate_OPERATIONAL']:>7.1%}"
              f"{r['gross_immediate']:>11.3f}{r['gross_operational']:>10.3f}"
              f"{r['adverse']:>9.3f}{r['net_OPERATIONAL']:>9.3f}   {ci}")

    print()
    print("  THE SCIENTIFIC CONTENT - does adverse selection scale with spread?")
    usable = [r for r in results if r.get("orders") and np.isfinite(r["adverse"])]
    if len(usable) >= 3:
        spreads = np.array([r["spread"] for r in usable])
        adverse = np.array([r["adverse"] for r in usable])
        ratio = adverse / (spreads / 2)
        correlation = float(np.corrcoef(spreads, adverse)[0, 1])
        print(f"    correlation(median spread, adverse selection) = {correlation:+.3f} "
              f"across {len(usable)} symbols")
        for r, rr in zip(usable, ratio):
            print(f"    {r['symbol']:<10} spread {r['spread']:6.3f} bps   adverse "
                  f"{r['adverse']:6.3f} bps   = {rr:5.2f}x the half-spread")
        wider_helps = any(r["net_OPERATIONAL"] > 0 and np.isfinite(r["op_ci"][0])
                          and r["op_ci"][0] > 0 for r in usable)
    else:
        correlation, wider_helps = float("nan"), False
        print("    too few symbols to relate spread to adverse selection")

    print()
    for r in results:
        if not r.get("orders"):
            continue
        verdict, reason = verdict_for(r)
        print(f"  {r['symbol']:<10} {verdict}")
        print(f"             {reason}")

    control = next((r for r in results if r["symbol"] == CONTROL and r.get("orders")), None)
    if control:
        print()
        print(f"  ERA CHECK - {CONTROL} 2024 vs 2026:")
        print(f"    median spread    {control['spread']:.3f} bps (2024)  vs  "
              f"{BTC_2026_SPREAD_BPS:.3f} bps (2026)")
        print(f"    adverse selection {control['adverse']:.3f} bps (2024)  vs  "
              f"{BTC_2026_ADVERSE_BPS:.3f} bps (2026)")

    print()
    if wider_helps:
        print("  At least one wider-spread instrument shows positive net value per submitted")
        print("  order. Passive execution is instrument-dependent, not universally closed.")
    else:
        print("  No instrument shows positive net value per submitted order. Wider spreads did")
        print("  not buy a net improvement, so passive execution is closed as a route on")
        print("  Binance perpetuals. The remaining hypothesis is a venue with structurally")
        print("  wider spreads and less informed flow, which this is not.")
    print("  ONE DAY, and a day two years old. This sizes a mechanism; it is not a forward")
    print("  claim and may not be presented as one.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    raise SystemExit(selftest() if args.selftest else run())
