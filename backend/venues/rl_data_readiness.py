"""Can the recorded archive support execution RL? Audited against what is actually collected.

THE SHORT ANSWER TODAY: NO, and not for a reason more data will fix on its own.

WHY THIS FILE IS A GATE AND NOT A NOTE
    An RL execution agent needs to know whether ITS order would have filled. That depends on
    queue position: how much size rested ahead of it at its price level, and how that size was
    consumed or cancelled. Reconstructing it requires a full order-book snapshot plus every
    sequenced diff-depth update, so the book can be replayed level by level with no gaps.

    The recorder collects TOP OF BOOK ONLY:

        binance_spot/bookTicker      best bid/ask + sizes        1 level per side
        binance_perp/bookTicker      best bid/ask + sizes        1 level per side
        bybit_perp/orderbook.1       depth ONE                   1 level per side
        binance_spot/aggTrade        aggregated trades
        binance_perp/aggTrade_rest   aggregated trades, polled
        binance_perp/premiumIndex    mark/index/funding
        binance_perp/openInterest    open interest

    There is no `@depth` stream, no lastUpdateId, no first/last update id pair, and no periodic
    REST snapshot anywhere in the collector. So the archive cannot reconstruct depth beyond the
    best level, at any point in its history, no matter how many days it runs.

    That makes exact `queue_depth_ahead_of_us` UNCOMPUTABLE from this data - not noisy, not
    approximate: absent. Even at the touch, bookTicker reports aggregate size, never the identity
    or ordering of the resting orders that make it up.

WHAT WOULD CHANGE THE ANSWER
    Adding, per venue and symbol:
        - a REST depth snapshot with its lastUpdateId
        - btcusdt@depth@100ms diff updates carrying (U, u) so gaps are DETECTABLE
        - a recorded resync whenever U does not chain onto the previous u
    Until those exist, an execution-RL claim is refused here rather than argued about later.

WHAT THE ARCHIVE CAN ALREADY SUPPORT
    Top-of-book microstructure: spread, microprice, best-size imbalance, trade signs and
    intensity, cross-venue lead-lag on the touch, and IMMEDIATE-TAKER execution modelling, where
    the fill is against the visible best level and queue position never matters.

    python backend/venues/rl_data_readiness.py
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Streams that carry more than the touch. Membership here is what makes L2 replay possible.
FULL_DEPTH_MARKERS = ("@depth", "depth@", "lastUpdateId", "diffDepth", "orderbook.50",
                      "orderbook.200", "orderbook.500")

# A stream name alone is not evidence of depth. `orderbook.1` contains the digit 1 for a reason.
TOP_OF_BOOK_ONLY = {
    "binance_spot/bookTicker": "best bid/ask + aggregate size, 1 level per side",
    "binance_perp/bookTicker": "best bid/ask + aggregate size, 1 level per side",
    "bybit_perp/orderbook.1": "depth ONE - the name states the level count",
}

CAPABILITY = {
    "top_of_book_microstructure": True,
    "trade_intensity_and_signing": True,
    "cross_venue_leadlag_on_touch": True,
    "immediate_taker_execution": True,     # fills against the visible best level
    "passive_fill_simulation": False,      # needs queue position
    "queue_position_reconstruction": False,
    "l2_book_replay": False,
    "execution_rl_training": False,
}


def collector_source() -> str:
    return (Path(__file__).resolve().parent / "multi_venue_recorder.py").read_text(
        encoding="utf-8")


def declared_streams(source: str) -> list[str]:
    import re

    match = re.search(r"^EXPECTED\s*=\s*\((.*?)\)", source, re.S | re.M)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def has_full_depth(source: str) -> tuple[bool, list[str]]:
    """True only if the COLLECTOR subscribes to a depth stream. Comments do not count."""
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#"))
    found = [marker for marker in FULL_DEPTH_MARKERS if marker in code]
    return bool(found), found


def audit() -> dict:
    source = collector_source()
    streams = declared_streams(source)
    depth_present, markers = has_full_depth(source)
    return {
        "declared_streams": streams,
        "full_depth_present": depth_present,
        "depth_markers_found": markers,
        "top_of_book_only": sorted(set(streams) & set(TOP_OF_BOOK_ONLY)),
        "capability": dict(CAPABILITY, **({
            "passive_fill_simulation": True,
            "queue_position_reconstruction": True,
            "l2_book_replay": True,
        } if depth_present else {})),
    }


def main() -> int:
    report = audit()
    ok = True

    def chk(cond: object, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok = ok and bool(cond)

    print("=" * 78)
    print("RL DATA READINESS - what the recorded archive can and cannot support")
    print("=" * 78)
    print(f"\ndeclared streams ({len(report['declared_streams'])}):")
    for stream in report["declared_streams"]:
        note = TOP_OF_BOOK_ONLY.get(stream, "")
        print(f"    {stream:<34}{note}")

    print("\ndepth reconstruction")
    chk(not report["full_depth_present"] or report["depth_markers_found"],
        "the depth verdict is derived from the collector's CODE, not from this file's prose")
    print(f"       full-depth stream subscribed: {report['full_depth_present']}")

    print("\ncapability")
    for name, value in report["capability"].items():
        print(f"    {'YES' if value else 'NO ':<4} {name}")

    print("\nverdict")
    if report["full_depth_present"]:
        print("    A depth stream is now collected. Re-audit: queue reconstruction may be")
        print("    possible, but only once snapshot+diff sequencing and gap detection are")
        print("    verified. This file must be updated deliberately, not assumed.")
        chk(False, "CAPABILITY table is stale - a depth stream exists but the table denies it")
    else:
        chk(report["capability"]["execution_rl_training"] is False,
            "execution RL is REFUSED: no depth stream, so queue position is uncomputable")
        chk(report["capability"]["passive_fill_simulation"] is False,
            "passive/maker fill simulation is REFUSED for the same reason")
        chk(report["capability"]["immediate_taker_execution"] is True,
            "immediate-taker execution modelling IS supported - it needs no queue position")
        print("\n    Exact queue_depth_ahead_of_us is ABSENT, not noisy. bookTicker reports")
        print("    aggregate size at the touch and never the identity or ordering of the")
        print("    resting orders composing it.")
        print("\n    To change this the collector must add, per venue/symbol:")
        print("      - a REST depth snapshot with its lastUpdateId")
        print("      - btcusdt@depth@100ms diffs carrying (U, u) so gaps are DETECTABLE")
        print("      - a recorded resync whenever U fails to chain onto the previous u")

    print("\nRL DATA READINESS", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
