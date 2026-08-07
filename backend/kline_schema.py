"""ONE canonical shape for a candle, from every source and every transport.

    python backend/kline_schema.py --selftest

WHY THIS EXISTS

There was no canonical kline. Three producers built three different dicts:

    REST  (data_ingestion ~:532)   time, OHLCV, close_time          ... and no is_closed
    WS    (data_ingestion ~:226)   time, OHLCV, is_closed           ... and no close_time
    WS    (data_ingestion ~:971)   same as above

Neither carried the source, the exchange event time, or the local receive time. Consumers then
disagreed about what a missing field meant:

    target_contract.py:538      k.get("is_closed") is not False     <- MISSING reads as CLOSED
    target_contract.py:591      same
    prediction_verifier.py:215  same
    server.py:712               k.get("is_closed") is True          <- MISSING reads as FORMING

Two opposite defaults for the same absent key, in the same process, on the path that decides
whether a bar may settle a prediction.

SEVEN TRACKED DEFECTS ACROSS FOUR AUDITS TRACE HERE, and each was being worked as its own item:

    2.3   the historical cache never re-fetches its last candle, so an incomplete final bar can
          never be corrected - there is no `is_closed` on a REST row to reject it by
    2.4   this schema split, named directly
    2.5   the signal-history rollover race attributes new-minute events to the closed candle
    4.1   a delayed book can become the executable quote - exchange event age is computed and
          never used as a rejection condition
    4.2   Pyth freshness uses receipt time because publish_time is discarded
    4.3   a stale feed can still settle an open round
    P0-4  `as_of_close` returns the bar's OPEN timestamp as the resolution event, because no
          close time is recorded and inferring cadence from neighbouring rows is unsafe (the
          P0-11 fixture's bars are +60s/+300s/+540s, so min(diffs) yields a wrong 240s)

THE RULE THIS MODULE ENFORCES

    Closure must be PROVEN, never inferred, and never assumed from absence.

`is_closed_at()` answers "was this bar complete at this instant?" from RECORDED timestamps only.
A bar that cannot prove closure is treated as OPEN, which is the fail-closed direction: refusing
to grade is recoverable, grading against a forming bar is not.
"""
from __future__ import annotations

import argparse
import sys

#: Fields every canonical kline carries. `close_ts_ms` is RECORDED, never derived from the
#: spacing of neighbouring rows.
CANONICAL_FIELDS = (
    "open_ts_ms",          # exchange bar-open time
    "close_ts_ms",         # exchange bar-close time, as the exchange stated it
    "is_closed",           # explicit; None means UNKNOWN, which is not the same as False
    "source",              # "binance_rest" | "binance_ws" | ...
    "source_event_ts_ms",  # when the exchange says the event happened
    "received_ts_ms",      # when this process first saw it
)

#: Sources that emit only completed bars. A REST history request returns finished candles, so
#: `is_closed` is genuinely True - but it is written EXPLICITLY rather than left to a consumer's
#: default, which is the entire point.
CLOSED_BY_CONSTRUCTION = frozenset({"binance_rest"})


class KlineSchemaError(ValueError):
    """A kline that cannot be normalized. Raised rather than silently defaulted."""


def _ms(value, *, field: str) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        raise KlineSchemaError(f"{field}={value!r} is not an integer millisecond timestamp")
    if out <= 0:
        raise KlineSchemaError(f"{field}={out} must be positive")
    return out


def canonical_kline(
    *,
    open_ts_ms,
    close_ts_ms,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    source: str,
    is_closed=None,
    source_event_ts_ms=None,
    received_ts_ms=None,
    **extra,
) -> dict:
    """Build ONE canonical candle. Every producer goes through here.

    `close_ts_ms` is mandatory. Binance supplies it on BOTH transports - REST field [6] and the
    websocket payload's `T` - so no producer ever needed to guess it; they simply dropped it.
    """
    open_ms = _ms(open_ts_ms, field="open_ts_ms")
    close_ms = _ms(close_ts_ms, field="close_ts_ms")
    if close_ms <= open_ms:
        raise KlineSchemaError(
            f"close_ts_ms={close_ms} must be after open_ts_ms={open_ms}; a bar cannot close "
            f"before it opens, and this is the invariant that makes duration provable")
    if not source:
        raise KlineSchemaError("source is required; an unattributed candle cannot be aged")
    if is_closed is None and source in CLOSED_BY_CONSTRUCTION:
        is_closed = True

    row = {
        # `time` in SECONDS is retained because the whole codebase reads it. The canonical
        # millisecond fields are added ALONGSIDE rather than replacing it, so this schema can
        # be adopted incrementally instead of in one breaking sweep.
        "time": open_ms // 1000,
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume),
        "close_time": close_ms // 1000,
        "open_ts_ms": open_ms,
        "close_ts_ms": close_ms,
        "is_closed": None if is_closed is None else bool(is_closed),
        "source": str(source),
        "source_event_ts_ms": (None if source_event_ts_ms is None
                               else _ms(source_event_ts_ms, field="source_event_ts_ms")),
        "received_ts_ms": (None if received_ts_ms is None
                           else _ms(received_ts_ms, field="received_ts_ms")),
    }
    row.update(extra)
    return row


def close_ts_ms(kline: dict):
    """The RECORDED close time, or None. Never inferred from neighbouring rows.

    Inference is what made P0-4 unfixable in place: the only available signal was the spacing
    of adjacent bars, and on any filtered or sparse list that spacing is not the real cadence.
    """
    if not isinstance(kline, dict):
        return None
    value = kline.get("close_ts_ms")
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    legacy = kline.get("close_time")
    if legacy is not None:
        try:
            return int(legacy) * 1000
        except (TypeError, ValueError):
            return None
    return None


def is_closed_at(kline: dict, at_ms: int) -> bool:
    """Was this bar PROVABLY complete at `at_ms`?

    Order matters, and it is fail-closed at every step:

      1. an explicit `is_closed is False` is decisive - the producer said it is forming;
      2. otherwise a RECORDED close time must exist AND have passed;
      3. absent a recorded close time, `is_closed is True` is accepted only because legacy
         websocket rows carry it without a close time;
      4. anything else is UNKNOWN, and unknown counts as OPEN.

    Step 4 is the behaviour change. Consumers previously wrote `is_closed is not False`, so a
    row with NO key at all was graded as closed - which is how a forming bar could settle a
    prediction on a feed that omitted the field.
    """
    if not isinstance(kline, dict):
        return False
    if kline.get("is_closed") is False:
        return False
    recorded = close_ts_ms(kline)
    if recorded is not None:
        return int(at_ms) >= recorded
    return kline.get("is_closed") is True


def has_canonical_fields(kline: dict) -> bool:
    """True when every canonical field is present (values may be None where permitted)."""
    return isinstance(kline, dict) and all(f in kline for f in CANONICAL_FIELDS)


def selftest() -> int:
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok = ok and bool(cond)

    base = dict(open_=100.0, high=101.0, low=99.0, close=100.5, volume=5.0)
    k = canonical_kline(open_ts_ms=60_000, close_ts_ms=119_999, source="binance_ws",
                        is_closed=True, source_event_ts_ms=120_000,
                        received_ts_ms=120_040, **base)

    print("the canonical shape carries every field a consumer needs")
    chk(has_canonical_fields(k), f"all canonical fields present: {CANONICAL_FIELDS}")
    chk(k["time"] == 60 and k["close_time"] == 119,
        "and the legacy second-resolution fields still exist, so adoption is incremental")

    print("closure is PROVEN from a recorded close time")
    chk(is_closed_at(k, 119_999) is True, "at the recorded close, the bar is closed")
    chk(is_closed_at(k, 119_998) is False, "one millisecond earlier it is not")
    chk(close_ts_ms(k) == 119_999, "and the close time is read back exactly as recorded")

    print("an UNKNOWN bar counts as OPEN - the fail-closed direction")
    chk(is_closed_at({"time": 60, "close": 100.0}, 10**13) is False,
        "a row with neither is_closed nor a close time is treated as FORMING. Consumers wrote "
        "`is_closed is not False`, so this exact row graded as CLOSED")
    chk(is_closed_at({"is_closed": False, "close_ts_ms": 1}, 10**13) is False,
        "an explicit forming flag beats a passed close time - the producer knows best")
    chk(is_closed_at({"is_closed": True}, 1) is True,
        "while a legacy websocket row with only is_closed=True is still honoured")

    print("REST rows are closed BY CONSTRUCTION, and say so explicitly")
    r = canonical_kline(open_ts_ms=60_000, close_ts_ms=119_999, source="binance_rest", **base)
    chk(r["is_closed"] is True,
        "a REST history bar is stamped is_closed=True rather than relying on a consumer's "
        "default for a missing key")

    print("invalid input REFUSES instead of defaulting")
    for kwargs, why in (
        (dict(open_ts_ms=120_000, close_ts_ms=60_000), "close before open"),
        (dict(open_ts_ms=0, close_ts_ms=60_000), "non-positive open"),
        (dict(open_ts_ms=60_000, close_ts_ms="soon"), "non-integer close"),
    ):
        try:
            canonical_kline(source="binance_ws", **kwargs, **base)
            raised = False
        except KlineSchemaError:
            raised = True
        chk(raised, f"{why} raises KlineSchemaError")

    try:
        canonical_kline(open_ts_ms=60_000, close_ts_ms=119_999, source="", **base)
        raised = False
    except KlineSchemaError:
        raised = True
    chk(raised, "an unattributed candle raises - it could never be aged against its source")

    print("P0-4: as_of_close returns the RECORDED close, not the bar's open")
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from target_contract import as_of_close

    bars = [
        canonical_kline(open_ts_ms=60_000, close_ts_ms=119_999, source="binance_rest",
                        open_=100.0, high=101.0, low=99.0, close=100.5, volume=1.0),
        canonical_kline(open_ts_ms=120_000, close_ts_ms=179_999, source="binance_rest",
                        open_=100.5, high=102.0, low=100.0, close=101.5, volume=1.0),
    ]
    price, event_ms = as_of_close(bars, 120_000)
    chk(price == 101.5,
        "selection is UNCHANGED - at_ms NAMES the horizon-end bar, and changing that would "
        "redefine every horizon by one bar (the reason two earlier attempts were reverted)")
    chk(event_ms == 179_999,
        f"but the resolution event is the RECORDED close ({event_ms}), not the bar's open "
        f"(120000). Every consumer stamping resolution_event_ts was one interval early")

    legacy = [{"time": 120, "close": 101.5}]
    _, legacy_ms = as_of_close(legacy, 120_000)
    chk(legacy_ms == 120_000,
        "a legacy row with no recorded close still returns its open time, so nothing regresses "
        "while the schema propagates through the codebase")

    print("\nKLINE SCHEMA:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.parse_args()
    sys.exit(selftest())
