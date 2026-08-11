"""A feed that is too stale for the UI must be too stale for the model. (scan-4 items 4.17/4.18)

    python backend/tests/test_venue_freshness_shared_rule.py

4.17 - COINBASE STALENESS WAS ENFORCED FOR THE UI AND BYPASSED BY THE FEATURES
    `current_venue_prices()` correctly dropped Coinbase once its print aged past
    COINBASE_MAX_STALE_MS - "absent beats invented". `prepare_derivatives_data()` then passed
    `data_state["coinbase_premium"]` straight into the feature vector with no age check at all.
    A disconnected Coinbase stream therefore VANISHED from the venue panel while its last
    premium stayed an active model input, and the premium is only recomputed when a tick
    arrives - so it froze rather than decayed.

4.18 - MULTI-EXCHANGE VENUES SHARED ONE TIMESTAMP AND RETAINED STALE PRICES
    `MultiExchangePriceClient.data` carried a single `time` for both venues, and each poll did
    `self.data["bybit"] = float(...) or self.data["bybit"]` - retaining the previous price on
    failure. `current_venue_prices()` consumed both without any per-venue age, so a venue that
    stopped responding kept contributing its last price to the consensus MEDIAN indefinitely
    while the shared `time` was refreshed every cycle and made the client look current.

THE RULE: one freshness definition, shared by every consumer. A number that no longer describes
an observation is not a cheaper observation - it is a different quantity.
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def main() -> int:
    import server

    now_ms = int(time.time() * 1000)
    now_s = time.time()

    print("4.17 the same Coinbase rule governs the panel AND the feature vector")
    fresh = {
        "klines": [{"close": 60000.0}],
        "coinbase_price": 60050.0,
        "coinbase_price_recv_ms": now_ms - 1_000,
        "coinbase_premium": 50.0,
        "multi_exchange": {},
        "derivatives": {},
    }
    chk(server.coinbase_if_fresh(fresh) == 60050.0,
        "a one-second-old print is fresh")
    stale = dict(fresh, coinbase_price_recv_ms=now_ms - (server.COINBASE_MAX_STALE_MS + 5_000),
                 derivatives={})     # its OWN dict: dict() is shallow, and prepare_derivatives_data
                                     # MUTATES data_state["derivatives"] in place, so sharing it
                                     # meant the second call overwrote the first call's answer
    chk(server.coinbase_if_fresh(stale) is None,
        f"a print older than COINBASE_MAX_STALE_MS ({server.COINBASE_MAX_STALE_MS}ms) is not")

    chk(server.current_venue_prices(dict(stale))["coinbase"] is None,
        "the venue panel drops it - this half always worked")

    # THE DEFECT: same state, opposite answer from the feature path.
    saved = server.data_state
    try:
        server.data_state = dict(stale, derivatives={})
        der_stale = dict(server.prepare_derivatives_data())   # snapshot, not a live reference
        server.data_state = dict(fresh, derivatives={})
        der_fresh = dict(server.prepare_derivatives_data())
    finally:
        server.data_state = saved
    chk(der_fresh.get("coinbase_premium") == 50.0,
        "a fresh premium still reaches the model unchanged")
    chk(der_stale.get("coinbase_premium") == 0.0,
        f"and a STALE one is 0.0, the column's declared neutral, rather than the frozen 50.0 "
        f"({der_stale.get('coinbase_premium')}) that used to keep flowing after the stream died")
    chk(der_stale.get("coinbase_premium_velocity") == 0.0,
        "velocity is gated on the same rule - it is a derivative of the same premium, and a "
        "frozen premium yields a velocity that reads as 'stable' rather than 'not observed'")

    print("4.18 each venue is aged against its OWN last successful observation")
    base = {"klines": [{"close": 60000.0}], "coinbase_price": None,
            "coinbase_price_recv_ms": 0, "derivatives": {}}
    both = dict(base, multi_exchange={
        "bybit": 60010.0, "kucoin": 60020.0,
        "bybit_observed_ts": now_s - 1.0, "kucoin_observed_ts": now_s - 1.0,
        "time": now_s,
    })
    v = server.current_venue_prices(both)
    chk(v["bybit"] == 60010.0 and v["kucoin"] == 60020.0,
        "two freshly observed venues both count")

    # kucoin died; the client kept its last price and kept refreshing the SHARED `time`.
    one_dead = dict(base, multi_exchange={
        "bybit": 60010.0, "kucoin": 60020.0,
        "bybit_observed_ts": now_s - 1.0,
        "kucoin_observed_ts": now_s - (server.VENUE_MAX_STALE_MS / 1000.0) - 60.0,
        "time": now_s,                      # <- looks current, and is about the POLL not the price
    })
    v2 = server.current_venue_prices(one_dead)
    chk(v2["bybit"] == 60010.0, "the live venue is unaffected")
    chk(v2["kucoin"] is None,
        "the dead venue is dropped even though the client's shared `time` is current - that "
        "shared field is when the client last POLLED, not when this price was OBSERVED")

    missing_ts = dict(base, multi_exchange={"bybit": 60010.0, "kucoin": 60020.0, "time": now_s})
    v3 = server.current_venue_prices(missing_ts)
    chk(v3["bybit"] is None and v3["kucoin"] is None,
        "a venue with NO observation timestamp is dropped, not trusted - unknown age must not "
        "read as fresh, which is the whole failure this repairs")

    print("consensus is what those venues feed, so this is not cosmetic")
    block = server.build_exchanges_block(one_dead)
    _kv = (block.get("venues") or {}).get("kucoin") or {}
    chk(_kv.get("price") is None,
        "the dropped venue is listed with price=None rather than omitted - the panel shows the "
        "venue EXISTS and has no current observation, which is more informative than silence")
    chk("kucoin" not in [k for k, v in (block.get("venues") or {}).items()
                         if (v or {}).get("price")],
        "and it contributes no price, so it cannot move the consensus median")
    chk(block.get("consensus") is not None,
        "and a consensus is still produced from what remains - this removes a bad input, it "
        "does not blank the panel")

    print("\nVENUE FRESHNESS SHARED RULE:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
