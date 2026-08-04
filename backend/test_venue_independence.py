"""P1-12: a derived price is never counted as an independent venue.

    python backend/test_venue_independence.py

THE DEFECT
    current_venue_prices returned:

        "coinbase": (binance + prem) if binance is not None else None

    where `prem` is `data_state.get("coinbase_premium", 0.0) or 0.0`. Coinbase is geo-blocked
    from this box, so the premium is almost always its 0.0 default and the expression evaluates
    to BINANCE EXACTLY. That value was then:

      - folded into the MEDIAN consensus, counting Binance twice and pulling consensus toward
        it;
      - assigned a deviation from consensus identical to Binance's, i.e. it agreed perfectly
        with itself and that was reported as cross-venue confirmation;
      - graded by PerVenueVerifier as an independent venue, inflating per-venue accuracy;
      - used to understate fragmentation, because a duplicated venue never disagrees.

    A venue with no live print is now absent. Absent beats invented.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import server as s                                          # noqa: E402
from exchange_verifier import VENUES, PerVenueVerifier      # noqa: E402

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _state(**over):
    base = {"klines": [{"close": 100.0}], "multi_exchange": {}, "coinbase_premium": 0.0}
    base.update(over)
    return base


def main() -> int:
    now = int(time.time() * 1000)

    print("a venue with no live print is ABSENT, not synthesised")
    vp = s.current_venue_prices(_state())
    chk(vp["binance"] == 100.0, "binance is the live close")
    chk(vp["coinbase"] is None,
        f"coinbase is None with no feed (got {vp['coinbase']}) - the old rule returned 100.0, "
        f"a byte-for-byte copy of binance")

    print("the duplicate is what corrupted the consensus")
    # Reconstruct the OLD behaviour to show the corruption was real, not theoretical.
    # Four genuine venues. The duplicate does not shift the median at every venue count - with
    # three real venues it happens to land on the same value - so the fixture uses a count
    # where it demonstrably does. "Sometimes harmless" is not the same as harmless.
    others = {"bybit": 101.0, "kucoin": 102.0, "chainlink": 103.0}
    fixed = dict(vp, **others)
    old_style = dict(fixed, coinbase=vp["binance"])

    def median(d):
        vals = sorted(v for v in d.values() if v and v > 0)
        return vals[len(vals) // 2] if vals else None

    chk(median(fixed) == 102.0 and median(old_style) == 101.0,
        f"the phantom venue moved the median consensus from {median(fixed)} to "
        f"{median(old_style)} - a real shift, not a cosmetic duplicate")
    chk(median(old_style) < median(fixed),
        "and it pulls consensus TOWARD binance, the venue it was copied from - so every "
        "other venue's deviation is measured against a binance-biased centre")

    print("a STALE coinbase print is refused")
    stale = s.current_venue_prices(_state(
        coinbase_price=99.0,
        coinbase_price_recv_ms=now - s.COINBASE_MAX_STALE_MS - 1))
    chk(stale["coinbase"] is None,
        "an old print is not a current observation, so the venue is absent")

    print("a FRESH coinbase print is the OBSERVED price")
    fresh = s.current_venue_prices(_state(
        coinbase_price=99.25, coinbase_price_recv_ms=now, coinbase_premium=5.0))
    chk(fresh["coinbase"] == 99.25,
        f"the recorded print is used verbatim (got {fresh['coinbase']})")
    chk(fresh["coinbase"] != fresh["binance"] + 5.0,
        "and NOT reconstructed as binance + premium, which would have said 105.0 - a price no "
        "venue ever quoted")

    print("the per-venue verifier cannot grade an absent venue")
    v = PerVenueVerifier(horizons=(5,))
    v.record("UP", 5, s.current_venue_prices(_state()), now)
    recorded = [p for p in v.pending] if hasattr(v, "pending") else []
    graded_venues = set()
    for entry in recorded:
        graded_venues |= set((entry.get("prices") or {}).keys())
    chk("coinbase" not in graded_venues,
        f"coinbase is not among the venues snapshotted for grading ({sorted(graded_venues)})")
    chk("binance" in graded_venues or not recorded,
        "while a venue that WAS observed is still graded")

    print("the venue list itself still names coinbase, deliberately")
    chk("coinbase" in VENUES,
        "the venue is not deleted - it is a real exchange and will grade normally the moment a "
        "live print exists; what changed is that absence is now reported as absence")

    print("the premium feature is untouched")
    chk(s.current_venue_prices(_state(coinbase_premium=7.0))["coinbase"] is None,
        "the premium no longer manufactures a venue price")
    src = (Path(__file__).resolve().parent / "server.py").read_text(encoding="utf-8")
    chk('data_state["coinbase_premium"] = coinbase_price - binance_price' in src,
        "but coinbase_premium is still COMPUTED and stored - it is a trained model feature, "
        "so removing it would silently change feature semantics and require a retrain")

    print("\nVENUE INDEPENDENCE:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
