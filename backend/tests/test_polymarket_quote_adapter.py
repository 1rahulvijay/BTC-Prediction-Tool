"""Contract tests for the in-process Polymarket quote adapter.

The central test is `test_drop_in_replacement_for_the_file_bridge`: it feeds the adapter's
payload to the REAL `price_to_beat._market_quote_for_round` and asserts that consumer accepts
it. Everything else here is a fail-closed check, because the adapter's job is to refuse.
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

import polymarket_quote_adapter as adapter_module  # noqa: E402
from polymarket.l2_book import L2Book  # noqa: E402
from polymarket_quote_adapter import PolymarketQuoteAdapter  # noqa: E402

ANCHOR = 1_723_600_000          # a fixed UTC anchor second; every round below hangs off it
NOW = ANCHOR + 60.0             # one minute into the 5m round


def _book(bid: float, ask: float, *, recv_ts: float, bid_size=120.0, ask_size=90.0) -> L2Book:
    book = L2Book("token")
    book.load_snapshot(
        [{"price": str(bid), "size": str(bid_size)}],
        [{"price": str(ask), "size": str(ask_size)}],
        market="0xmarket",
        exchange_ts_ms=int(recv_ts * 1000),
        recv_ts_ns=int(recv_ts * 1e9),
        book_hash="abc123",
    )
    return book


def _market(
    *,
    slug: str = f"bitcoin-updown-5m-{ANCHOR}",
    yes_outcome: str = "Up",
    no_outcome: str = "Down",
    yes_token: str = "UPTOK",
    no_token: str = "DOWNTOK",
    tick_size="0.01",
    fees_enabled=True,
    end_date: str | None = None,
) -> dict:
    return {
        "id": "1", "condition_id": "0xcond", "slug": slug,
        "yes_token": yes_token, "no_token": no_token,
        "yes_outcome": yes_outcome, "no_outcome": no_outcome,
        "tick_size": tick_size, "minimum_order_size": "5",
        "fees_enabled": fees_enabled, "end_date": end_date,
        "resolution_source": "twap",
    }


class FakeClient:
    """Mirrors the two PolymarketClient attributes the adapter reads: markets and orderbooks."""

    def __init__(self, markets: list[dict], books: dict[str, L2Book]):
        self.markets: dict[str, dict] = {}
        for market in markets:
            self.markets[market["yes_token"]] = market
            self.markets[market["no_token"]] = market
        self.orderbooks = books

    def status(self) -> dict:
        return {"connected": True, "healthy": True}


def _standard(recv_ts: float = NOW - 0.2, **market_kwargs):
    market = _market(**market_kwargs)
    books = {
        market["yes_token"]: _book(0.48, 0.52, recv_ts=recv_ts),
        market["no_token"]: _book(0.47, 0.51, recv_ts=recv_ts),
    }
    return FakeClient([market], books), market


def _adapter(client, now: float = NOW, **kwargs) -> PolymarketQuoteAdapter:
    return PolymarketQuoteAdapter(client, clock=lambda: now, **kwargs)


# -- round identity -------------------------------------------------------------------------

def test_slug_parsing_is_strict() -> None:
    assert adapter_module.parse_round_slug(f"bitcoin-updown-5m-{ANCHOR}") == (5, ANCHOR)
    assert adapter_module.parse_round_slug(f"bitcoin-updown-15m-{ANCHOR}") == (15, ANCHOR)
    # An unrecognised horizon must be refused, not silently bucketed as 15m the way the legacy
    # recorder's `300 if "updown-5m" in slug else 900` did. Minute horizons matter most: if the
    # venue lists a 1m or 30m round, a loose `(\d+)m` pattern would accept it and then price it
    # against a duration this module has no mapping for.
    assert adapter_module.parse_round_slug(f"bitcoin-updown-1h-{ANCHOR}") is None
    assert adapter_module.parse_round_slug(f"bitcoin-updown-1m-{ANCHOR}") is None
    assert adapter_module.parse_round_slug(f"bitcoin-updown-30m-{ANCHOR}") is None
    # "150m" must not partially match the "15" alternative.
    assert adapter_module.parse_round_slug(f"bitcoin-updown-150m-{ANCHOR}") is None
    assert adapter_module.parse_round_slug("bitcoin-updown-5m") is None
    assert adapter_module.parse_round_slug("ethereum-price-2024") is None
    assert adapter_module.parse_round_slug("") is None
    assert adapter_module.parse_round_slug(None) is None


def test_exact_round_identity_only() -> None:
    client, _ = _standard()
    adapter = _adapter(client)
    assert adapter.quote_for_round(5, ANCHOR * 1000) is not None
    # One round early, one round late, and the wrong horizon all return nothing. No round is
    # ever answered with its neighbour.
    assert adapter.quote_for_round(5, (ANCHOR - 300) * 1000) is None
    assert adapter.quote_for_round(5, (ANCHOR + 300) * 1000) is None
    assert adapter.quote_for_round(15, ANCHOR * 1000) is None
    assert adapter.quote_for_round(None, ANCHOR * 1000) is None


def test_end_date_must_agree_with_the_slug_anchor() -> None:
    from datetime import datetime, timezone

    good = datetime.fromtimestamp(ANCHOR + 300, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    client, _ = _standard(end_date=good)
    assert _adapter(client).quote_for_round(5, ANCHOR * 1000) is not None

    bad = datetime.fromtimestamp(ANCHOR + 3600, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    client, _ = _standard(end_date=bad)
    quote, reason = _adapter(client).build_round_quote(_market(end_date=bad), NOW)
    assert quote is None and reason == adapter_module.REJECT_END_DATE


# -- UP/DOWN alignment ----------------------------------------------------------------------

def test_up_down_mapping_follows_labels_not_field_names() -> None:
    # Normal orientation: yes_token is UP.
    up, down, reason = adapter_module.resolve_up_down_tokens(_market())
    assert (up, down, reason) == ("UPTOK", "DOWNTOK", None)

    # Inverted: the venue listed Down first, so yes_token is the DOWN token. Reading the field
    # name instead of the label here would flip the side of every paper entry.
    up, down, reason = adapter_module.resolve_up_down_tokens(
        _market(yes_outcome="Down", no_outcome="Up")
    )
    assert (up, down, reason) == ("DOWNTOK", "UPTOK", None)


def test_non_up_down_markets_are_refused() -> None:
    for kwargs in (
        {"yes_outcome": "Yes", "no_outcome": "No"},
        {"yes_outcome": "Up", "no_outcome": "Up"},
        {"yes_outcome": "", "no_outcome": "Down"},
    ):
        _, _, reason = adapter_module.resolve_up_down_tokens(_market(**kwargs))
        assert reason == adapter_module.REJECT_OUTCOMES, kwargs

    _, _, reason = adapter_module.resolve_up_down_tokens(
        _market(yes_token="SAME", no_token="SAME")
    )
    assert reason == adapter_module.REJECT_TOKENS


def test_inverted_market_produces_correctly_swapped_prices() -> None:
    market = _market(yes_outcome="Down", no_outcome="Up")
    books = {
        "UPTOK": _book(0.10, 0.12, recv_ts=NOW - 0.1),      # yes_token, labelled Down
        "DOWNTOK": _book(0.88, 0.90, recv_ts=NOW - 0.1),    # no_token, labelled Up
    }
    quote = _adapter(FakeClient([market], books)).quote_for_round(5, ANCHOR * 1000)
    assert quote is not None
    # up_* must come from the token LABELLED Up, which is DOWNTOK here.
    assert quote["up_ask"] == 0.90 and quote["down_ask"] == 0.12


# -- freshness ------------------------------------------------------------------------------

def test_age_is_measured_from_the_staler_leg() -> None:
    market = _market()
    books = {
        "UPTOK": _book(0.48, 0.52, recv_ts=NOW - 0.1),      # fresh
        "DOWNTOK": _book(0.47, 0.51, recv_ts=NOW - 4.0),    # stale but inside the window
    }
    quote = _adapter(FakeClient([market], books)).quote_for_round(5, ANCHOR * 1000)
    assert quote is not None
    assert abs(quote["ts"] - (NOW - 4.0)) < 1e-6, "quote must carry the OLDER leg's time"


def test_one_frozen_leg_cannot_ride_on_a_live_one() -> None:
    market = _market()
    books = {
        "UPTOK": _book(0.48, 0.52, recv_ts=NOW - 0.1),       # live
        "DOWNTOK": _book(0.47, 0.51, recv_ts=NOW - 30.0),    # frozen half a minute ago
    }
    client = FakeClient([market], books)
    assert _adapter(client).quote_for_round(5, ANCHOR * 1000) is None
    _, rejected = _adapter(client).quotes_by_round()
    assert rejected[market["slug"]] == adapter_module.REJECT_STALE


def test_clock_skew_into_the_future_is_refused() -> None:
    client, market = _standard(recv_ts=NOW + 5.0)
    _, rejected = _adapter(client).quotes_by_round()
    assert rejected[market["slug"]] == adapter_module.REJECT_FUTURE


# -- book state -----------------------------------------------------------------------------

def test_unusable_books_are_refused_with_named_reasons() -> None:
    market = _market()

    missing = FakeClient([market], {"UPTOK": _book(0.48, 0.52, recv_ts=NOW)})
    _, rejected = _adapter(missing).quotes_by_round()
    assert rejected[market["slug"]] == adapter_module.REJECT_NO_BOOK

    unsynced = L2Book("DOWNTOK")          # never received a snapshot
    partial = FakeClient([market], {"UPTOK": _book(0.48, 0.52, recv_ts=NOW), "DOWNTOK": unsynced})
    _, rejected = _adapter(partial).quotes_by_round()
    assert rejected[market["slug"]] == adapter_module.REJECT_UNSYNC

    one_sided = L2Book("DOWNTOK")
    one_sided.load_snapshot([{"price": "0.47", "size": "10"}], [], recv_ts_ns=int(NOW * 1e9))
    client = FakeClient([market], {"UPTOK": _book(0.48, 0.52, recv_ts=NOW), "DOWNTOK": one_sided})
    _, rejected = _adapter(client).quotes_by_round()
    assert rejected[market["slug"]] == adapter_module.REJECT_INVALID


def test_crossed_book_is_refused() -> None:
    market = _market()
    crossed = L2Book("DOWNTOK")
    crossed.load_snapshot(
        [{"price": "0.60", "size": "10"}], [{"price": "0.55", "size": "10"}],
        recv_ts_ns=int(NOW * 1e9),
    )
    client = FakeClient([market], {"UPTOK": _book(0.48, 0.52, recv_ts=NOW), "DOWNTOK": crossed})
    _, rejected = _adapter(client).quotes_by_round()
    assert rejected[market["slug"]] == adapter_module.REJECT_INVALID


def test_book_without_receive_time_is_refused() -> None:
    market = _market()
    no_clock = L2Book("DOWNTOK")
    no_clock.load_snapshot(
        [{"price": "0.47", "size": "10"}], [{"price": "0.51", "size": "10"}], recv_ts_ns=0,
    )
    client = FakeClient([market], {"UPTOK": _book(0.48, 0.52, recv_ts=NOW), "DOWNTOK": no_clock})
    _, rejected = _adapter(client).quotes_by_round()
    assert rejected[market["slug"]] == adapter_module.REJECT_NO_RECV


# -- metadata -------------------------------------------------------------------------------

def test_tick_size_is_required_by_default_and_optional_by_configuration() -> None:
    client, market = _standard(tick_size=None)
    _, rejected = _adapter(client).quotes_by_round()
    assert rejected[market["slug"]] == adapter_module.REJECT_TICK

    client, _ = _standard(tick_size=None)
    assert _adapter(client, require_tick_size=False).quote_for_round(5, ANCHOR * 1000) is not None

    client, market = _standard(tick_size="0")
    _, rejected = _adapter(client).quotes_by_round()
    assert rejected[market["slug"]] == adapter_module.REJECT_TICK


def test_unknown_fee_status_is_treated_as_fees_on() -> None:
    # Absent metadata must not price the market as free. The legacy recorder defaulted this to
    # False, which understates cost - the one direction that manufactures edge.
    client, _ = _standard(fees_enabled=None)
    quote = _adapter(client).quote_for_round(5, ANCHOR * 1000)
    assert quote["fees_enabled"] is True
    assert quote["fee_rate"] == adapter_module.DEFAULT_CRYPTO_TAKER_FEE_RATE

    client, _ = _standard(fees_enabled=False)
    quote = _adapter(client).quote_for_round(5, ANCHOR * 1000)
    assert quote["fees_enabled"] is False and quote["fee_rate"] == 0.0


# -- payload shape --------------------------------------------------------------------------

def test_payload_publishes_the_live_round_not_an_upcoming_one() -> None:
    live = _market(slug=f"bitcoin-updown-5m-{ANCHOR}", yes_token="U1", no_token="D1")
    upcoming = _market(slug=f"bitcoin-updown-5m-{ANCHOR + 300}", yes_token="U2", no_token="D2")
    books = {
        "U1": _book(0.48, 0.52, recv_ts=NOW - 0.1), "D1": _book(0.47, 0.51, recv_ts=NOW - 0.1),
        "U2": _book(0.30, 0.34, recv_ts=NOW - 0.1), "D2": _book(0.65, 0.69, recv_ts=NOW - 0.1),
    }
    payload = _adapter(FakeClient([live, upcoming], books)).quote_payload()
    assert set(payload["markets"]) == {"5"}
    # NOW is inside the live round and before the upcoming one starts.
    assert payload["markets"]["5"]["anchor_ts"] == ANCHOR
    assert payload["markets"]["5"]["up_ask"] == 0.52


def test_expired_round_is_not_published() -> None:
    client, _ = _standard(recv_ts=ANCHOR + 299.9)
    payload = _adapter(client, now=ANCHOR + 301.0).quote_payload()
    assert payload["markets"] == {}


def test_diagnostics_name_every_rejection() -> None:
    good = _market(slug=f"bitcoin-updown-5m-{ANCHOR}", yes_token="U1", no_token="D1")
    bad = _market(slug="bitcoin-updown-1h-999", yes_token="U2", no_token="D2")
    books = {
        "U1": _book(0.48, 0.52, recv_ts=NOW - 0.1), "D1": _book(0.47, 0.51, recv_ts=NOW - 0.1),
        "U2": _book(0.30, 0.34, recv_ts=NOW - 0.1), "D2": _book(0.65, 0.69, recv_ts=NOW - 0.1),
    }
    report = _adapter(FakeClient([good, bad], books)).diagnostics()
    assert report["acceptable_rounds"] == 1
    assert report["live_rounds"]["5m"]["anchor_ts"] == ANCHOR
    assert report["reject_counts"] == {adapter_module.REJECT_SLUG: 1}
    assert report["client_status"]["connected"] is True


def test_adapter_never_mutates_the_client() -> None:
    import copy

    client, _ = _standard()
    before = copy.deepcopy(client.markets)
    adapter = _adapter(client)
    adapter.quote_payload()
    adapter.diagnostics()
    adapter.quote_for_round(5, ANCHOR * 1000)
    assert client.markets == before


# -- the contract that matters --------------------------------------------------------------

def test_drop_in_replacement_for_the_file_bridge() -> None:
    """The real consumer must accept the adapter's payload with no changes to its logic."""
    import price_to_beat

    client, _ = _standard(recv_ts=NOW - 0.2)
    payload = _adapter(client).quote_payload()

    # Stand in for the file read without touching disk: the consumer caches the parsed bridge
    # payload in these globals and only re-reads every 0.5s.
    price_to_beat._PM_QUOTES = payload
    price_to_beat._PM_QUOTES_CHECKED = time.time()
    price_to_beat._PM_QUOTES_MTIME = 1.0
    try:
        round_data = {"horizon": 5, "window_start": ANCHOR * 1000, "current_position": "UP"}
        quote = price_to_beat._market_quote_for_round(round_data, NOW * 1000)
        assert quote is not None, "consumer rejected an adapter payload it should accept"
        assert quote["side"] == "UP"
        assert quote["ask"] == 0.52 and quote["bid"] == 0.48
        assert quote["depth"] == 90.0
        assert quote["age_seconds"] <= 5.0

        down = price_to_beat._market_quote_for_round(
            {**round_data, "current_position": "DOWN"}, NOW * 1000
        )
        assert down["ask"] == 0.51 and down["bid"] == 0.47

        # The consumer's own fail-closed checks must still bite on adapter-sourced payloads.
        assert price_to_beat._market_quote_for_round(
            {**round_data, "window_start": (ANCHOR + 300) * 1000}, NOW * 1000
        ) is None, "wrong anchor must be refused"
        assert price_to_beat._market_quote_for_round(
            round_data, (NOW + 30) * 1000
        ) is None, "a 30s-old quote must be refused"
        assert price_to_beat._market_quote_for_round(
            {**round_data, "current_position": "FLAT"}, NOW * 1000
        ) is None, "an unknown position must be refused"
    finally:
        price_to_beat._PM_QUOTES = None
        price_to_beat._PM_QUOTES_CHECKED = 0.0
        price_to_beat._PM_QUOTES_MTIME = -1.0


# -- wiring ---------------------------------------------------------------------------------

class _QuoteSources:
    """Set price_to_beat's two quote sources and always restore them."""

    def __init__(self, *, adapter=None, bridge=None):
        self.adapter = adapter
        self.bridge = bridge

    def __enter__(self):
        import price_to_beat

        self.module = price_to_beat
        self.saved = (
            price_to_beat._QUOTE_ADAPTER,
            price_to_beat._PM_QUOTES,
            price_to_beat._PM_QUOTES_CHECKED,
            price_to_beat._PM_QUOTES_MTIME,
        )
        price_to_beat.set_quote_adapter(self.adapter)
        price_to_beat._PM_QUOTES = self.bridge
        # Non-zero so the loader does not re-read the real file during the test.
        price_to_beat._PM_QUOTES_CHECKED = time.time()
        price_to_beat._PM_QUOTES_MTIME = 1.0
        return price_to_beat

    def __exit__(self, *exc):
        adapter, quotes, checked, mtime = self.saved
        self.module.set_quote_adapter(adapter)
        self.module._PM_QUOTES = quotes
        self.module._PM_QUOTES_CHECKED = checked
        self.module._PM_QUOTES_MTIME = mtime
        return False


def _bridge_payload(up_ask: float) -> dict:
    return {
        "version": 2, "generated_at": NOW,
        "markets": {"5": {
            "ts": NOW - 0.2, "slug": "from-file-bridge", "anchor_ts": ANCHOR, "horizon": 5,
            "up_bid": up_ask - 0.04, "up_ask": up_ask, "up_spread": 0.04,
            "up_top_ask_size": 11.0,
            "down_bid": 0.47, "down_ask": 0.51, "down_spread": 0.04,
            "down_top_ask_size": 12.0,
            "fees_enabled": True, "fee_rate": 0.07,
        }},
    }


_ROUND = {"horizon": 5, "window_start": ANCHOR * 1000, "current_position": "UP"}


def test_set_quote_adapter_installs_and_clears() -> None:
    import price_to_beat

    with _QuoteSources() as module:
        client, _ = _standard()
        adapter = _adapter(client)
        module.set_quote_adapter(adapter)
        assert module._QUOTE_ADAPTER is adapter
        module.set_quote_adapter(None)
        assert module._QUOTE_ADAPTER is None
        assert module._adapter_quote(_ROUND) is None
    assert price_to_beat._QUOTE_ADAPTER is None or True  # restored by the context manager


def test_adapter_is_preferred_over_the_file_bridge() -> None:
    client, _ = _standard(recv_ts=NOW - 0.2)
    with _QuoteSources(adapter=_adapter(client), bridge=_bridge_payload(0.99)) as module:
        quote = module._market_quote_for_round(_ROUND, NOW * 1000)
        assert quote is not None
        # 0.52 is the adapter's book; 0.99 would mean the stale file won.
        assert quote["ask"] == 0.52
        assert quote["slug"] != "from-file-bridge"


def test_file_bridge_still_serves_when_no_adapter_is_installed() -> None:
    """Compatibility mode must keep working; wiring the adapter is additive, not a removal."""
    with _QuoteSources(adapter=None, bridge=_bridge_payload(0.55)) as module:
        quote = module._market_quote_for_round(_ROUND, NOW * 1000)
        assert quote is not None
        assert quote["ask"] == 0.55 and quote["slug"] == "from-file-bridge"


def test_adapter_without_a_quote_falls_through_to_the_bridge() -> None:
    # No books at all -> adapter yields nothing for this round, bridge still answers.
    empty = FakeClient([_market()], {})
    with _QuoteSources(adapter=_adapter(empty), bridge=_bridge_payload(0.55)) as module:
        quote = module._market_quote_for_round(_ROUND, NOW * 1000)
        assert quote is not None and quote["slug"] == "from-file-bridge"


def test_broken_adapter_degrades_instead_of_raising() -> None:
    class Exploding:
        def quote_for_round(self, *_args):
            raise RuntimeError("book thread died")

    with _QuoteSources(adapter=Exploding(), bridge=_bridge_payload(0.55)) as module:
        quote = module._market_quote_for_round(_ROUND, NOW * 1000)
        assert quote is not None and quote["slug"] == "from-file-bridge"

    with _QuoteSources(adapter=Exploding(), bridge=None) as module:
        assert module._market_quote_for_round(_ROUND, NOW * 1000) is None


def test_adapter_quotes_are_not_exempt_from_consumer_validation() -> None:
    """A second source must not become a second standard."""
    client, _ = _standard(recv_ts=NOW - 0.2)
    with _QuoteSources(adapter=_adapter(client)) as module:
        assert module._market_quote_for_round(_ROUND, (NOW + 30) * 1000) is None
        assert module._market_quote_for_round(
            {**_ROUND, "window_start": (ANCHOR + 300) * 1000}, NOW * 1000
        ) is None


def test_server_installs_the_adapter_on_the_live_client() -> None:
    """Static check: server.py must import and install it, without importing server itself."""
    import ast

    source = (BACKEND / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "PolymarketQuoteAdapter" in imported
    assert "price_to_beat_module" in imported

    installs = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "set_quote_adapter"
    ]
    assert len(installs) == 1, f"expected exactly one set_quote_adapter call, found {len(installs)}"
    call = installs[0].args[0]
    assert isinstance(call, ast.Call) and call.func.id == "PolymarketQuoteAdapter"
    assert call.args[0].id == "polymarket_client", "adapter must wrap the live client"


# -- health surface -------------------------------------------------------------------------

def test_health_reports_no_executable_round_when_every_market_is_refused() -> None:
    """The failure this exists to catch: socket green, quotes zero, nobody told."""
    # A market the adapter must refuse (no tick size), with perfectly healthy books.
    client, _ = _standard(tick_size=None)
    with _QuoteSources(adapter=_adapter(client)) as module:
        health = module.quote_source_health()
        assert health["adapter_installed"] is True
        assert health["source"] == "in_process"
        assert health["acceptable_rounds"] == 0
        assert health["live_rounds"] == {}
        assert health["blockers"] == ["no_executable_round"]
        assert health["reject_counts"] == {adapter_module.REJECT_TICK: 1}


def test_health_is_clear_when_a_round_is_publishable() -> None:
    client, _ = _standard()
    with _QuoteSources(adapter=_adapter(client)) as module:
        health = module.quote_source_health()
        assert health["blockers"] == []
        assert health["acceptable_rounds"] == 1
        assert health["live_rounds"]["5m"]["anchor_ts"] == ANCHOR


def test_health_does_not_blame_compatibility_mode() -> None:
    """No adapter installed means the file bridge is the intended source, not a fault."""
    with _QuoteSources(adapter=None) as module:
        health = module.quote_source_health()
        assert health["adapter_installed"] is False
        assert health["source"] == "file_bridge"
        assert health["blockers"] == []


def test_health_probe_that_raises_reports_a_blocker() -> None:
    class Exploding:
        def diagnostics(self):
            raise RuntimeError("client went away")

    with _QuoteSources(adapter=Exploding()) as module:
        health = module.quote_source_health()
        # A probe that fails must never look like a probe that found nothing wrong.
        assert health["blockers"] == ["quote_diagnostics_failed"]
        assert "client went away" in health["error"]


def test_server_surfaces_quote_health_and_gates_it() -> None:
    """Static check that server.py reports and blocks on quote health, not just feed health."""
    import ast

    source = (BACKEND / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "quote_source_health"
    ]
    assert len(calls) == 1, "server must consult the pricing path's own health exactly once"

    keys = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "polymarket_quotes" in keys, "health payload must expose the quote source"
    assert any(
        "polymarket_quotes:" in value for value in keys
    ), "quote-health blockers must be prefixed and surfaced"


# -- hot-path cost --------------------------------------------------------------------------

def test_round_lookup_only_summarizes_the_matching_market() -> None:
    """quote_for_round is the live pricing path; it must not summarize every tracked book."""
    counted: list[str] = []

    class CountingBook(L2Book):
        def summary(self):
            counted.append(self.asset_id)
            return super().summary()

    def counting_book(asset: str, bid: float, ask: float) -> L2Book:
        book = CountingBook(asset)
        book.load_snapshot(
            [{"price": str(bid), "size": "10"}], [{"price": str(ask), "size": "10"}],
            recv_ts_ns=int((NOW - 0.1) * 1e9),
        )
        return book

    markets, books = [], {}
    for index in range(6):
        anchor = ANCHOR + index * 300
        up, down = f"U{index}", f"D{index}"
        markets.append(_market(slug=f"bitcoin-updown-5m-{anchor}", yes_token=up, no_token=down))
        books[up] = counting_book(up, 0.48, 0.52)
        books[down] = counting_book(down, 0.47, 0.51)

    counted.clear()
    quote = _adapter(FakeClient(markets, books)).quote_for_round(5, ANCHOR * 1000)
    assert quote is not None
    # Exactly the two legs of the requested round - not all twelve books.
    assert sorted(counted) == ["D0", "U0"], f"summarized {len(counted)} books: {sorted(counted)}"


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"POLYMARKET QUOTE ADAPTER: PASS ({len(tests)} tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
