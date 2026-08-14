"""In-process Polymarket quote adapter: PolymarketClient books -> executable round quotes.

WHY THIS EXISTS
    `price_to_beat._market_quote_for_round` reads `data/pm_live_quotes.json`, an atomic file
    bridge published by `backend/polymarket/live_btc_updown_recorder.py`. That recorder is a
    legacy detached process which the core launcher no longer starts by default
    (`BTC_START_LEGACY_RECORDERS=0`, commit 6e74b03). On a default boot the bridge file is
    absent or stale, so executable Polymarket pricing is simply unavailable.

    The backend already maintains the same books in process: `PolymarketClient.orderbooks`
    holds a synchronized `L2Book` per outcome token. This module converts that live state into
    the EXACT payload shape the existing consumer already validates, so the file bridge can be
    replaced without changing the consumer's contract or its fail-closed checks.

THIS MODULE IS NOT WIRED IN
    Nothing here is imported by `price_to_beat.py`, `server.py` or any startup path. It is a
    pure, side-effect-free reader. Switching the live quote source over is a separate,
    deliberate change.

WHAT "FAIL CLOSED" MEANS HERE
    Every rejection returns None and records a NAMED reason. The adapter never substitutes a
    default for missing market truth, because a fabricated quote is worse than no quote: the
    consumer's job is to abstain when pricing is unavailable, and silently handing it a
    plausible-looking number defeats that.

    Three choices are deliberately conservative and differ from the legacy recorder:

    1. FRESHNESS USES THE STALER LEG. A round quote needs both the UP and DOWN book. Its age is
       therefore the age of the OLDER of the two, never the newer. Reporting the fresher leg
       would let a frozen DOWN book ride along on a live UP book.

    2. UNKNOWN FEES ARE ASSUMED ON. The legacy recorder defaulted absent `feesEnabled` to False,
       which sets fee_rate 0.0 and UNDERSTATES cost. Understating cost is the direction that
       manufactures edge, so absent metadata here means fees_enabled=True at the canonical rate.
       Only an explicit False disables them.

    3. `taker_base_fee` IS NOT READ. Gamma exposes it without documenting whether it is a
       fraction or basis points. Guessing the unit is a silent 100x cost error in one direction,
       so this module uses the canonical rate from `polymarket_fee` and ignores the field.

UP/DOWN ALIGNMENT IS VERIFIED, NEVER ASSUMED
    `PolymarketClient._market_from_gamma` fills `yes_token`/`no_token` by matching the outcome
    list against ("yes","no") first and ("up","down") second. For a BTC up/down market that
    normally makes yes_token the UP token - but "normally" is not a guarantee, and an inverted
    mapping would flip the side of every paper entry while looking completely healthy. This
    module reads the recorded outcome LABELS and refuses any market whose labels are not an
    unambiguous up/down pair.
"""
from __future__ import annotations

import math
import re
import time
from typing import Any, Callable, Iterator

from polymarket_fee import DEFAULT_CRYPTO_TAKER_FEE_RATE

# `bitcoin-updown-5m-1723600000` -> horizon 5, anchor 1723600000. The trailing integer is the
# round's UTC anchor second and is the only round identity the consumer will accept. Anything
# that does not match exactly is refused rather than coerced; the legacy recorder's
# `300 if "updown-5m" in slug else 900` silently treated every unrecognised slug as 15m.
_SLUG_PATTERN = re.compile(r"(?:^|-)updown-(5|15)m-(\d{9,12})$")

HORIZON_SECONDS = {5: 300, 15: 900}

# Reasons are stable strings so health panels and tests can assert on them.
REJECT_SLUG = "slug_not_a_5m_or_15m_round"
REJECT_END_DATE = "end_date_disagrees_with_slug_anchor"
REJECT_OUTCOMES = "outcome_labels_not_an_up_down_pair"
REJECT_TOKENS = "up_and_down_tokens_identical_or_missing"
REJECT_NO_BOOK = "book_missing_for_one_or_both_tokens"
REJECT_UNSYNC = "book_not_synchronized"
REJECT_INVALID = "book_invalid"
REJECT_PRICES = "top_of_book_outside_0_to_1"
REJECT_NO_RECV = "book_has_no_receive_timestamp"
REJECT_STALE = "book_older_than_max_age"
REJECT_FUTURE = "book_receive_time_in_the_future"
REJECT_TICK = "tick_size_missing_or_invalid"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_round_slug(slug: str) -> tuple[int, int] | None:
    """Return (horizon_minutes, anchor_ts_seconds), or None when the slug is not a BTC round."""
    match = _SLUG_PATTERN.search(str(slug or "").strip().lower())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _end_date_seconds(market: dict) -> float | None:
    raw = market.get("end_date")
    if not raw:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def resolve_up_down_tokens(market: dict) -> tuple[str | None, str | None, str | None]:
    """Map a market's recorded outcome LABELS to (up_token, down_token, reject_reason).

    Both orderings are accepted because the label is the evidence; the field name `yes_token`
    is not. Anything other than an unambiguous up/down pair is refused.
    """
    yes_label = str(market.get("yes_outcome") or "").strip().lower()
    no_label = str(market.get("no_outcome") or "").strip().lower()
    yes_token = str(market.get("yes_token") or "").strip()
    no_token = str(market.get("no_token") or "").strip()
    if {yes_label, no_label} != {"up", "down"}:
        return None, None, REJECT_OUTCOMES
    if not yes_token or not no_token or yes_token == no_token:
        return None, None, REJECT_TOKENS
    if yes_label == "up":
        return yes_token, no_token, None
    return no_token, yes_token, None


class PolymarketQuoteAdapter:
    """Reads a PolymarketClient's live books and emits `pm_live_quotes.json`-shaped quotes.

    The adapter holds no state of its own beyond configuration. Every call re-reads the client,
    so a quote can never outlive the book it came from.
    """

    def __init__(
        self,
        client: Any,
        *,
        max_age_seconds: float = 5.0,
        clock: Callable[[], float] = time.time,
        fee_rate: float = DEFAULT_CRYPTO_TAKER_FEE_RATE,
        require_tick_size: bool = True,
        end_date_tolerance_seconds: float = 60.0,
    ) -> None:
        self.client = client
        # The consumer independently rejects quotes older than 5s. Matching that here keeps the
        # adapter from publishing rows it knows the consumer will discard.
        self.max_age_seconds = float(max_age_seconds)
        self.clock = clock
        self.fee_rate = float(fee_rate)
        self.require_tick_size = bool(require_tick_size)
        self.end_date_tolerance_seconds = float(end_date_tolerance_seconds)

    # -- market enumeration -------------------------------------------------------------

    def _distinct_markets(self) -> Iterator[dict]:
        """`client.markets` is keyed by token, so each market appears twice. Yield each once."""
        seen: set[int] = set()
        for market in (getattr(self.client, "markets", None) or {}).values():
            if not isinstance(market, dict) or id(market) in seen:
                continue
            seen.add(id(market))
            yield market

    # -- per-round construction ---------------------------------------------------------

    def _book_side(self, token: str) -> tuple[dict | None, str | None]:
        book = (getattr(self.client, "orderbooks", None) or {}).get(token)
        if book is None:
            return None, REJECT_NO_BOOK
        if not getattr(book, "synchronized", False):
            return None, REJECT_UNSYNC
        if not getattr(book, "valid", False):
            return None, REJECT_INVALID
        summary = book.summary()
        bid = _finite(summary.get("best_bid"))
        ask = _finite(summary.get("best_ask"))
        if bid is None or ask is None or not (0.0 < bid <= ask < 1.0):
            return None, REJECT_PRICES
        recv_ns = int(getattr(book, "last_recv_ts_ns", 0) or 0)
        if recv_ns <= 0:
            return None, REJECT_NO_RECV
        return {
            "bid": bid,
            "ask": ask,
            "spread": ask - bid,
            "top_bid_size": _finite(summary.get("best_bid_size")) or 0.0,
            "top_ask_size": _finite(summary.get("best_ask_size")) or 0.0,
            "bid_levels": int(summary.get("bid_levels") or 0),
            "ask_levels": int(summary.get("ask_levels") or 0),
            "bid_depth": _finite(summary.get("bid_depth")) or 0.0,
            "ask_depth": _finite(summary.get("ask_depth")) or 0.0,
            "recv_ts": recv_ns / 1e9,
            "exchange_ts_ms": int(summary.get("last_exchange_ts_ms") or 0),
            "book_hash": str(summary.get("book_hash") or ""),
        }, None

    def _fee_metadata(self, market: dict) -> tuple[bool, float]:
        raw = market.get("fees_enabled")
        if raw is False:
            return False, 0.0
        # None/absent means unknown. Unknown resolves to fees ON, because the failure that
        # matters is pricing a market as cheaper than it is.
        return True, self.fee_rate

    def build_round_quote(self, market: dict, now: float) -> tuple[dict | None, str | None]:
        """Turn one market into a bridge-shaped quote, or (None, reason)."""
        parsed = parse_round_slug(market.get("slug"))
        if parsed is None:
            return None, REJECT_SLUG
        horizon, anchor_ts = parsed
        duration = HORIZON_SECONDS[horizon]

        # When the venue supplies an end date, it must agree with the anchor encoded in the
        # slug. Disagreement means the slug is not describing this market's window and no
        # round identity can be trusted.
        end_date = _end_date_seconds(market)
        if end_date is not None:
            if abs(end_date - (anchor_ts + duration)) > self.end_date_tolerance_seconds:
                return None, REJECT_END_DATE

        up_token, down_token, reason = resolve_up_down_tokens(market)
        if reason is not None:
            return None, reason

        tick = _finite(market.get("tick_size"))
        if self.require_tick_size and (tick is None or not 0.0 < tick < 1.0):
            return None, REJECT_TICK

        up, reason = self._book_side(up_token)
        if reason is not None:
            return None, reason
        down, reason = self._book_side(down_token)
        if reason is not None:
            return None, reason

        # A round is exactly as fresh as its staler leg.
        recv_ts = min(up["recv_ts"], down["recv_ts"])
        age = now - recv_ts
        if age > self.max_age_seconds:
            return None, REJECT_STALE
        if age < -1.0:
            return None, REJECT_FUTURE

        fees_enabled, fee_rate = self._fee_metadata(market)
        return {
            "ts": recv_ts,
            "slug": str(market.get("slug") or ""),
            "condition_id": str(market.get("condition_id") or ""),
            "horizon": horizon,
            "anchor_ts": anchor_ts,
            "start_ts": anchor_ts,
            "end_ts": anchor_ts + duration,
            "seconds_left": float(anchor_ts + duration - now),
            "up_bid": up["bid"],
            "up_ask": up["ask"],
            "up_spread": up["spread"],
            "up_top_ask_size": up["top_ask_size"],
            "up_top_bid_size": up["top_bid_size"],
            "down_bid": down["bid"],
            "down_ask": down["ask"],
            "down_spread": down["spread"],
            "down_top_ask_size": down["top_ask_size"],
            "down_top_bid_size": down["top_bid_size"],
            "fees_enabled": fees_enabled,
            "fee_rate": fee_rate,
            "tick_size": tick,
            "minimum_order_size": _finite(market.get("minimum_order_size")),
            "resolution_source": str(market.get("resolution_source") or ""),
            # Provenance. `ts` is a RECEIVE time; the venue's own clock is carried separately so
            # later analysis can tell the two apart instead of inferring one from the other.
            "up_exchange_ts_ms": up["exchange_ts_ms"],
            "down_exchange_ts_ms": down["exchange_ts_ms"],
            "up_book_hash": up["book_hash"],
            "down_book_hash": down["book_hash"],
            "up_bid_levels": up["bid_levels"],
            "up_ask_levels": up["ask_levels"],
            "down_bid_levels": down["bid_levels"],
            "down_ask_levels": down["ask_levels"],
            "quote_source": "in_process_polymarket_client",
        }, None

    # -- public API ---------------------------------------------------------------------

    def quotes_by_round(self) -> tuple[dict[tuple[int, int], dict], dict[str, str]]:
        """Every acceptable quote keyed by (horizon, anchor_ts), plus per-slug reject reasons."""
        now = float(self.clock())
        accepted: dict[tuple[int, int], dict] = {}
        rejected: dict[str, str] = {}
        for market in self._distinct_markets():
            quote, reason = self.build_round_quote(market, now)
            if reason is not None:
                rejected[str(market.get("slug") or f"<no-slug:{id(market)}>")] = reason
                continue
            accepted[(quote["horizon"], quote["anchor_ts"])] = quote
        return accepted, rejected

    def quote_for_round(self, horizon: Any, window_start_ms: Any) -> dict | None:
        """Exact-identity lookup. The caller names the round; no nearest-match is ever returned."""
        try:
            key = (int(horizon), int(window_start_ms) // 1000)
        except (TypeError, ValueError):
            return None
        accepted, _ = self.quotes_by_round()
        return accepted.get(key)

    def quote_payload(self) -> dict:
        """Emit the `pm_live_quotes.json` v2 payload shape, keyed by horizon string.

        The file bridge publishes one quote per horizon, meaning the round currently in flight.
        Selecting by "contains now" rather than "most recent" prevents an upcoming round's book
        being published under the same horizon key as the live one - which would pass every
        freshness check while describing the wrong window.
        """
        now = float(self.clock())
        accepted, rejected = self.quotes_by_round()
        markets: dict[str, dict] = {}
        for (horizon, anchor_ts), quote in accepted.items():
            if not anchor_ts <= now < quote["end_ts"]:
                continue
            key = str(horizon)
            current = markets.get(key)
            if current is None or anchor_ts > current["anchor_ts"]:
                markets[key] = quote
        return {
            "version": 2,
            "generated_at": now,
            "markets": markets,
            "source": "in_process_polymarket_client",
            "rejected": rejected,
        }

    def diagnostics(self) -> dict:
        """Health view: what is publishable, what is not, and why."""
        now = float(self.clock())
        accepted, rejected = self.quotes_by_round()
        client_status = {}
        status_fn = getattr(self.client, "status", None)
        if callable(status_fn):
            try:
                client_status = status_fn()
            except Exception as exc:  # a diagnostics call must not raise into a health panel
                client_status = {"error": str(exc)[:200]}
        live = {
            f"{horizon}m": {
                "anchor_ts": anchor,
                "age_seconds": round(now - accepted[(horizon, anchor)]["ts"], 3),
                "seconds_left": round(accepted[(horizon, anchor)]["seconds_left"], 1),
            }
            for (horizon, anchor) in accepted
            if anchor <= now < accepted[(horizon, anchor)]["end_ts"]
        }
        return {
            "generated_at": now,
            "acceptable_rounds": len(accepted),
            "live_rounds": live,
            "rejected": rejected,
            "reject_counts": _count(rejected.values()),
            "client_status": client_status,
        }


def _count(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts
