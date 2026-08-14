"""A fee-free market must stay fee-free across the bridge: 0.0 is a rate, not a missing field.

WHY THIS EXISTS
    Every fee reader in price_to_beat.py used `float(quote.get("fee_rate") or 0.07)`. 0.0 is
    falsy, so a market that explicitly disables fees - the recorder writes fee_rate 0.0 for
    exactly that case - was re-priced at the full crypto taker fee, up to 1.75c per share at
    a 50c ask. The neighbouring field was already correct (`fees_enabled is not False`), so the
    two lines disagreed about what a fee-free market means.

    The direction was conservative (cost overstated, never understated), which is why it never
    produced a bad trade and never surfaced. What it did produce was a wrong number in the
    served payload and the decision panel, and a market that would look unprofitable if the
    venue ever quoted fees_enabled=True with a zero rate.

WHAT IS ASSERTED
    1. An explicit fee_rate of 0.0 survives _market_quote_for_round unchanged, and does not
       disagree with fees_enabled on the same dict.
    2. An ABSENT fee_rate still defaults to the canonical crypto taker rate - the fix must
       distinguish absent from zero, not drop the default.
    3. A non-default explicit rate passes through untouched (no silent re-typing of the rate).
    4. The three sibling readers (_leader_quote, _side_quote, _live_share_prices_for_round)
       apply the same rule, so no path reinstates a fee the venue is not charging.
    5. The champion's own fee arithmetic on the returned dict is 0.0 - the caller consumes
       what the bridge said, so no downstream reader depends on the old inflated value.

    python backend/tests/test_pm_quote_fee_rate_fidelity.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND.parent))


ROUND = {"horizon": 5, "window_start": 1_000_000, "current_position": "UP"}
NOW_MS = 1_026_000


def _quote(**overrides) -> dict:
    """A fresh, exactly-matching 5m bridge quote. Anchor and age must pass or the readers
    fail closed and the fee assertions would vacuously hold on a None."""
    quote = {
        "slug": "btc-updown-5m-test",
        "anchor_ts": 1_000,
        "ts": 1_025.5,
        "up_bid": 0.59,
        "up_ask": 0.61,
        "down_bid": 0.39,
        "down_ask": 0.41,
        "up_top_ask_size": 12.0,
        "down_top_ask_size": 15.0,
        "up_full_ladder": {"b": [[0.59, 100.0]], "a": [[0.61, 100.0]]},
        "down_full_ladder": {"b": [[0.39, 100.0]], "a": [[0.41, 100.0]]},
        "fees_enabled": False,
        "fee_rate": 0.0,
    }
    quote.update(overrides)
    return quote


def main() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    import price_to_beat as ptb
    from polymarket_fee import DEFAULT_CRYPTO_TAKER_FEE_RATE, polymarket_taker_fee_per_share

    saved = (ptb._PM_QUOTES, ptb._PM_QUOTES_MTIME, ptb._PM_QUOTES_CHECKED)
    prior_data_dir = os.environ.get("BTC_DATA_DIR")
    scratch = tempfile.TemporaryDirectory()

    def inject(quote: dict) -> None:
        """Install `quote` as THE bridge payload for the 5m market.

        _market_quote_for_round refreshes _PM_QUOTES from data/pm_live_quotes.json before
        reading it, so an injected quote is only stable if the loader finds no file to load.
        BTC_DATA_DIR points at an empty directory (a live bridge file on the developer's box
        would otherwise overwrite this mid-test, and the test would pass or fail depending on
        whether the recorder happened to be running), and the cached mtime is pinned to the
        loader's own "no file" sentinel so the refresh is a no-op however long the test takes."""
        ptb._PM_QUOTES = {"markets": {"5": quote}}
        ptb._PM_QUOTES_MTIME = -1.0
        ptb._PM_QUOTES_CHECKED = 0.0

    try:
        os.environ["BTC_DATA_DIR"] = scratch.name

        # --- 1. an explicit zero survives the round trip ------------------------------------
        inject(_quote())
        market = ptb._market_quote_for_round(ROUND, NOW_MS)
        check(market is not None, "the fee-free quote matches anchor and freshness (not dropped)")
        check(market["fee_rate"] == 0.0,
              f"fee_rate 0.0 survives _market_quote_for_round (got {market['fee_rate']})")
        check(market["fees_enabled"] is False and market["fee_rate"] == 0.0,
              "fees_enabled and fee_rate agree that the market is fee-free")

        # --- 5. and the champion's fee arithmetic on that dict is genuinely zero ------------
        check(polymarket_taker_fee_per_share(market["ask"], market["fee_rate"]) == 0.0,
              "the fee the Champion charges on this quote is 0.0, not the 1.6c default")

        # --- 2. absent is still absent: the default must not be lost ------------------------
        absent = _quote()
        absent.pop("fee_rate")
        inject(absent)
        market = ptb._market_quote_for_round(ROUND, NOW_MS)
        check(market is not None and market["fee_rate"] == DEFAULT_CRYPTO_TAKER_FEE_RATE,
              f"an ABSENT fee_rate still defaults to {DEFAULT_CRYPTO_TAKER_FEE_RATE} "
              "(absent is not zero)")

        # --- 3. a non-default explicit rate is carried, not re-typed ------------------------
        inject(_quote(fees_enabled=True, fee_rate=0.10))
        market = ptb._market_quote_for_round(ROUND, NOW_MS)
        check(market is not None and market["fee_rate"] == 0.10,
              "an explicit non-default rate passes through untouched")

        # --- 4. every sibling reader applies the same rule ----------------------------------
        # fees_enabled=True with a zero rate is the case the old `or 0.07` silently repriced,
        # and it is the only case where these three readers' fee arithmetic can show the bug:
        # with fees_enabled=False they zero the fee through the other field regardless.
        inject(_quote(fees_enabled=True, fee_rate=0.0))

        leader = ptb._leader_quote(ROUND, NOW_MS)
        check(leader is not None and leader["fee"] == 0.0,
              "_leader_quote charges no fee on a zero-rate market")

        side = ptb._side_quote(ROUND, NOW_MS, "UP")
        check(side is not None and side["fee_in"] == 0.0 and side["fee_out"] == 0.0,
              "_side_quote charges no entry or exit fee on a zero-rate market")

        shares = ptb._live_share_prices_for_round(ROUND, NOW_MS)
        check(shares is not None and shares["fee_rate"] == 0.0,
              "_live_share_prices_for_round reports the zero rate it was given")
        check(shares["up"]["buy_fee"] == 0.0 and shares["down"]["buy_fee"] == 0.0,
              "the published per-side buy_fee is 0.0 on a zero-rate market")

        # The same three readers must still default when the field is genuinely missing.
        missing = _quote(fees_enabled=True)
        missing.pop("fee_rate")
        inject(missing)
        leader = ptb._leader_quote(ROUND, NOW_MS)
        expected = round(DEFAULT_CRYPTO_TAKER_FEE_RATE * 0.61 * (1.0 - 0.61), 5)
        check(leader is not None and leader["fee"] == expected,
              f"_leader_quote still charges the default fee ({expected}) when the rate is absent")
    finally:
        ptb._PM_QUOTES, ptb._PM_QUOTES_MTIME, ptb._PM_QUOTES_CHECKED = saved
        if prior_data_dir is None:
            os.environ.pop("BTC_DATA_DIR", None)
        else:
            os.environ["BTC_DATA_DIR"] = prior_data_dir
        scratch.cleanup()

    print(f"\nPM QUOTE FEE-RATE FIDELITY: PASS ({checks} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
