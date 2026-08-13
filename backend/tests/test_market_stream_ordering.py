"""Regression tests for live stream ordering and directional sweep state."""

from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from data_ingestion import BinanceFuturesWebSocketClient  # noqa: E402
from order_flow import OrderFlowAnalyzer  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  PASS  {message}")


def test_perp_trade_ordering() -> None:
    client = BinanceFuturesWebSocketClient()
    emitted: list[dict] = []
    client.on("perp_bar", emitted.append)

    check(client._ingest_perp_trade(100.0, 2.0, False, 1_000, 10),
          "first aggregate trade opens the minute")
    check(client._ingest_perp_trade(101.0, 1.0, True, 20_000, 11),
          "new aggregate trade contributes once")
    check(client._ingest_perp_trade(102.0, 3.0, False, 61_000, 12),
          "newer minute finalizes the prior minute")
    check(emitted == [{"ts": 0, "cvd_perp": 1.0, "vol_perp": 3.0, "perp_price": 101.0}],
          "finalized CVD has the correct sign, volume and close")

    state = (client._pb_ms, client._pb_cvd, client._pb_vol, client._pb_last)
    check(not client._ingest_perp_trade(101.0, 1.0, True, 20_000, 11),
          "replayed aggregate-trade ID is rejected")
    check(not client._ingest_perp_trade(99.0, 5.0, False, 30_000, 13),
          "late older-minute trade is rejected")
    check((client._pb_ms, client._pb_cvd, client._pb_vol, client._pb_last) == state,
          "replay and late data cannot mutate or roll back the open bar")

    check(client._ingest_perp_trade(103.0, 2.0, True, 70_000, 14),
          "stream continues after a rejected late trade")
    check(client._ingest_perp_trade(104.0, 1.0, False, 121_000, 15),
          "next rollover remains monotone")
    check(emitted[-1] == {
        "ts": 60_000,
        "cvd_perp": 1.0,
        "vol_perp": 5.0,
        "perp_price": 103.0,
    }, "second finalized bar excludes rejected volume")


def test_perp_aggregate_trade_parser() -> None:
    parsed = BinanceFuturesWebSocketClient._parse_aggregate_trade({
        "a": 123, "p": "63317.30", "q": "0.239", "m": True,
        "T": 1_786_647_512_549,
    })
    check(parsed == (123, 63317.30, 0.239, True, 1_786_647_512_549),
          "REST aggregate trades preserve id, price, size, side and exchange time")
    for bad in (
        {"a": -1, "p": "63317.30", "q": "0.239", "m": True, "T": 1},
        {"a": 1, "p": "0", "q": "0.239", "m": True, "T": 1},
        {"a": 1, "p": "63317.30", "q": "0", "m": True, "T": 1},
    ):
        try:
            BinanceFuturesWebSocketClient._parse_aggregate_trade(bad)
            check(False, "unreachable")
        except ValueError:
            pass
    check(True, "invalid futures aggregate trades fail closed")


def _trade(price: float, timestamp: int, *, is_buy: bool) -> dict:
    return {
        "price": price,
        "quantity": 1.0,
        "time": timestamp,
        "is_buyer_maker": not is_buy,
    }


def test_directional_sweep_state() -> None:
    analyzer = OrderFlowAnalyzer(whale_threshold_btc=0.5)
    analyzer.local_extremes.update({
        "high": 110.0,
        "low": 100.0,
        "last_high_break": 10_000,
        "last_low_break": 0,
    })
    analyzer.process_trade(_trade(101.0, 11_000, is_buy=True))
    check(analyzer.liquidity_sweeps["bullish"] == 0.0,
          "a recent HIGH break cannot fabricate a bullish low-sweep reversal")

    analyzer.local_extremes.update({
        "last_high_break": 0,
        "last_low_break": 20_000,
    })
    analyzer.process_trade(_trade(109.0, 21_000, is_buy=False))
    check(analyzer.liquidity_sweeps["bearish"] == 0.0,
          "a recent LOW break cannot fabricate a bearish high-sweep reversal")

    analyzer.local_extremes.update({
        "last_high_break": 30_000,
        "last_low_break": 30_000,
    })
    analyzer.process_trade(_trade(101.0, 31_000, is_buy=True))
    analyzer.process_trade(_trade(109.0, 31_500, is_buy=False))
    check(analyzer.liquidity_sweeps["bullish"] == 31_000,
          "recent low-break reversal records bullish evidence")
    check(analyzer.liquidity_sweeps["bearish"] == 31_500,
          "recent high-break reversal records bearish evidence")


def main() -> int:
    test_perp_trade_ordering()
    test_perp_aggregate_trade_parser()
    test_directional_sweep_state()
    print("\nMarket stream ordering: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
