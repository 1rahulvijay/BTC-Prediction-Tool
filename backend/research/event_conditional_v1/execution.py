"""Binance USD-M perpetual execution economics.

WHY THIS IS NOT SHARED WITH THE POLYMARKET ENGINE
    backend/research/executable_surface_config.taker_fee computes

        rate * p * (1 - p)        with p CLAMPED into [0, 1]

    which is correct for a 0-1 Polymarket contract and catastrophic here: at a perp
    price of 60000 the clamp makes p = 1.0 and the fee becomes exactly ZERO. It does
    not raise. It silently removes every cost from the result and inflates paper
    performance by precisely the amount under study. Binance fees are a rate on
    NOTIONAL and are computed here, in this module, only.

    This module also deliberately does not import from profit_campaign_v1. That
    campaign is frozen; coupling to it risks perturbing a recorded negative result.

CAUSALITY
    An entry may only use the first book at or after (decision_ts + latency). No
    midpoint fills, no hindsight best price, visible size only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from contracts import Action, ActionOutcome, FillStandard

# --------------------------------------------------------------------------- costs


def binance_fee_usd(notional_usd: float, fee_bps: float) -> float:
    """Fee on NOTIONAL. Price-independent by construction - that is the whole point."""
    if notional_usd < 0:
        raise ValueError("notional_usd must be >= 0")
    if fee_bps < 0:
        raise ValueError("fee_bps must be >= 0")
    return notional_usd * fee_bps / 10_000.0


# --------------------------------------------------------------------------- book


@dataclass(slots=True)
class BookState:
    """One observable top-of-book / ladder snapshot."""
    recv_ts_ms: int
    bids: list[tuple[float, float]] = field(default_factory=list)   # [(price, size)] desc
    asks: list[tuple[float, float]] = field(default_factory=list)   # [(price, size)] asc

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None


def first_book_at_or_after(books: list[BookState], ts_ms: int) -> BookState | None:
    """Earliest observable book at or after ts_ms. Never looks backwards, never
    searches for a better price later in the sequence."""
    for b in books:
        if b.recv_ts_ms >= ts_ms:
            return b
    return None


def ladder_vwap(levels: list[tuple[float, float]], qty: float) -> tuple[float | None, float]:
    """Walk visible size only. Returns (vwap, filled_qty); filled_qty < qty means the
    displayed ladder could not absorb the order - that is a real partial, not an error."""
    if qty <= 0:
        return None, 0.0
    remaining, cost, filled = qty, 0.0, 0.0
    for px, size in levels:
        if remaining <= 0:
            break
        take = min(remaining, size)
        cost += take * px
        filled += take
        remaining -= take
    if filled <= 0:
        return None, 0.0
    return cost / filled, filled


# ------------------------------------------------------------------- taker outcome


def simulate_taker(
    action: Action,
    entry_book: BookState,
    exit_book: BookState,
    notional_usd: float,
    horizon_s: int,
    taker_fee_bps: float,
    impact_bps: float,
) -> ActionOutcome:
    """LONG: enter on asks, exit on bids. SHORT: enter on bids, exit on asks."""
    if action not in (Action.TAKER_LONG, Action.TAKER_SHORT):
        raise ValueError(f"not a taker action: {action}")
    is_long = action.is_long

    ref = entry_book.best_ask if is_long else entry_book.best_bid
    if not ref or ref <= 0:
        return _unfilled(action, horizon_s, notional_usd, ("NO_ENTRY_QUOTE",))
    qty = notional_usd / ref

    entry_px, entry_qty = ladder_vwap(entry_book.asks if is_long else entry_book.bids, qty)
    if entry_px is None or entry_qty <= 0:
        return _unfilled(action, horizon_s, notional_usd, ("NO_ENTRY_LIQUIDITY",))

    exit_px, exit_qty = ladder_vwap(exit_book.bids if is_long else exit_book.asks, entry_qty)
    if exit_px is None or exit_qty <= 0:
        return _unfilled(action, horizon_s, notional_usd, ("NO_EXIT_LIQUIDITY",))

    q = min(entry_qty, exit_qty)
    gross = q * (exit_px - entry_px) if is_long else q * (entry_px - exit_px)

    entry_notional, exit_notional = q * entry_px, q * exit_px
    fee = binance_fee_usd(entry_notional, taker_fee_bps) + binance_fee_usd(exit_notional, taker_fee_bps)
    slip = binance_fee_usd(entry_notional, impact_bps) + binance_fee_usd(exit_notional, impact_bps)

    reasons: tuple[str, ...] = ()
    if q < qty * 0.999:
        reasons += ("PARTIAL_FILL",)

    return ActionOutcome(
        action=action, horizon_s=horizon_s, filled=True, fill_standard=None,
        entry_price=entry_px, exit_price=exit_px, quantity=q, notional_usd=entry_notional,
        gross_pnl_usd=gross, fee_usd=fee, slippage_usd=slip,
        net_pnl_usd=gross - fee - slip,
        holding_time_s=float(horizon_s), promotable=True, reasons=reasons,
    )


# ------------------------------------------------------------------- maker outcome


def simulate_maker(
    action: Action,
    resting_price: float,
    fill_standard: FillStandard | None,
    exit_book: BookState,
    notional_usd: float,
    horizon_s: int,
    maker_fee_bps: float,
    impact_bps: float,
    taker_fee_bps: float,
    wait_time_s: float = 0.0,
    adverse_selection_bps: float = 0.0,
    missed_fill_opportunity_usd: float = 0.0,
) -> ActionOutcome:
    """A maker entry that was NOT filled is a real, recorded outcome with net PnL 0
    and a missed-opportunity cost - not a dropped row. Exit is modelled as taker, which
    is the conservative choice: it does not assume a second passive fill.

    `promotable` is False whenever the fill rests on TOUCH_PROXY alone.
    """
    if action not in (Action.MAKER_LONG, Action.MAKER_SHORT):
        raise ValueError(f"not a maker action: {action}")

    if fill_standard is None:
        return ActionOutcome(
            action=action, horizon_s=horizon_s, filled=False, fill_standard=None,
            entry_price=None, exit_price=None, quantity=0.0, notional_usd=0.0,
            gross_pnl_usd=0.0, fee_usd=0.0, slippage_usd=0.0, net_pnl_usd=0.0,
            holding_time_s=0.0, adverse_selection_bps=adverse_selection_bps,
            missed_fill_opportunity_usd=missed_fill_opportunity_usd,
            promotable=True, reasons=("MAKER_NOT_FILLED",),
        )

    is_long = action.is_long
    if resting_price <= 0:
        return _unfilled(action, horizon_s, notional_usd, ("BAD_RESTING_PRICE",))
    qty = notional_usd / resting_price

    exit_px, exit_qty = ladder_vwap(exit_book.bids if is_long else exit_book.asks, qty)
    if exit_px is None or exit_qty <= 0:
        return _unfilled(action, horizon_s, notional_usd, ("NO_EXIT_LIQUIDITY",))

    q = min(qty, exit_qty)
    gross = q * (exit_px - resting_price) if is_long else q * (resting_price - exit_px)

    entry_notional, exit_notional = q * resting_price, q * exit_px
    # Passive entry pays maker; the exit crosses, so it pays taker.
    fee = binance_fee_usd(entry_notional, maker_fee_bps) + binance_fee_usd(exit_notional, taker_fee_bps)
    slip = binance_fee_usd(exit_notional, impact_bps)   # no impact on a passive entry
    adverse = binance_fee_usd(entry_notional, adverse_selection_bps)

    promotable = fill_standard is not FillStandard.TOUCH_PROXY
    reasons: tuple[str, ...] = () if promotable else ("TOUCH_PROXY_NOT_PROMOTABLE",)
    if q < qty * 0.999:
        reasons += ("PARTIAL_FILL",)

    return ActionOutcome(
        action=action, horizon_s=horizon_s, filled=True, fill_standard=fill_standard,
        entry_price=resting_price, exit_price=exit_px, quantity=q,
        notional_usd=entry_notional, gross_pnl_usd=gross, fee_usd=fee, slippage_usd=slip,
        net_pnl_usd=gross - fee - slip - adverse,
        holding_time_s=float(horizon_s) + wait_time_s,
        adverse_selection_bps=adverse_selection_bps,
        missed_fill_opportunity_usd=missed_fill_opportunity_usd,
        promotable=promotable, reasons=reasons,
    )


def wait_outcome(horizon_s: int) -> ActionOutcome:
    """WAIT is always priced, always exactly zero. It is the benchmark every action
    must beat, and it is recorded so abstention is visible in the ledger."""
    return ActionOutcome(
        action=Action.WAIT, horizon_s=horizon_s, filled=False, fill_standard=None,
        entry_price=None, exit_price=None, quantity=0.0, notional_usd=0.0,
        gross_pnl_usd=0.0, fee_usd=0.0, slippage_usd=0.0, net_pnl_usd=0.0,
        holding_time_s=0.0, promotable=True, reasons=("WAIT",),
    )


def _unfilled(action: Action, horizon_s: int, notional: float,
              reasons: tuple[str, ...]) -> ActionOutcome:
    return ActionOutcome(
        action=action, horizon_s=horizon_s, filled=False, fill_standard=None,
        entry_price=None, exit_price=None, quantity=0.0, notional_usd=0.0,
        gross_pnl_usd=0.0, fee_usd=0.0, slippage_usd=0.0, net_pnl_usd=0.0,
        holding_time_s=0.0, promotable=True, reasons=reasons,
    )
