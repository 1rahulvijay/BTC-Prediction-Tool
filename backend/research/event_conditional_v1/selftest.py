"""Executing tests for BINANCE_EVENT_CONDITIONAL_PROFIT_V1 Phase 1.

These run behaviour. Nothing here inspects source text. Exit code is nonzero on any
failure so CI cannot report a false green.

    python -m backend.research.event_conditional_v1.selftest
"""
from __future__ import annotations

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))          # backend/research (executable_surface_config)

from contracts import (  # noqa: E402
    Action, DataQuality, EventCandidate, Family, FillStandard, load_protocol,
)
from data_contract import evaluate_archive, quality_for, segment_events  # noqa: E402
from event_detectors import (  # noqa: E402
    detect_cross_venue_lead_lag, detect_funding_basis_oi,
    detect_liquidation_continuation, detect_liquidation_exhaustion,
)
from execution import (  # noqa: E402
    BookState, binance_fee_usd, first_book_at_or_after, ladder_vwap,
    simulate_maker, simulate_taker, wait_outcome,
)
from viability import HorizonNotViable, check_horizon, require_horizon  # noqa: E402

OK = True


def chk(cond: bool, msg: str) -> None:
    global OK
    print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
    OK = OK and bool(cond)


def book(ts: int, bid: float, ask: float, size: float = 100.0) -> BookState:
    return BookState(recv_ts_ms=ts, bids=[(bid, size)], asks=[(ask, size)])


def main() -> int:
    p = load_protocol()
    print("event-conditional-v1 selftest")

    # ---------------------------------------------------------------- fee model
    print("\n[fees] Binance notional fees, never the Polymarket contract formula")
    chk(abs(binance_fee_usd(1000.0, 4.0) - 0.40) < 1e-12,
        "1000 USD at 4 bps = 0.40 USD")
    chk(abs(binance_fee_usd(1000.0, 5.0) - 0.50) < 1e-12,
        "1000 USD at 5 bps = 0.50 USD")
    # The exact trap: the Polymarket formula clamps p to [0,1] and returns 0 at 60000.
    from executable_surface_config import taker_fee as poly_fee  # type: ignore
    chk(poly_fee(60000.0, 0.0005) == 0.0,
        "the Polymarket formula really does return 0.0 at a 60000 price (the trap)")
    chk(binance_fee_usd(1000.0, 5.0) > 0.0,
        "the Binance fee at the same price is NONZERO")
    f30k = binance_fee_usd(1000.0, 5.0)
    chk(abs(f30k - binance_fee_usd(1000.0, 5.0)) < 1e-12,
        "Binance fee is price-independent (a rate on notional, not on price)")

    # ------------------------------------------------------------ ladder / causality
    print("\n[book] ladder walk and causal selection")
    vwap, filled = ladder_vwap([(100.0, 1.0), (101.0, 1.0)], 2.0)
    chk(abs(vwap - 100.5) < 1e-9 and abs(filled - 2.0) < 1e-9, "ladder VWAP walks levels")
    vwap, filled = ladder_vwap([(100.0, 0.5)], 2.0)
    chk(filled == 0.5, "insufficient displayed depth yields a PARTIAL fill, not a full one")
    books = [book(1000, 99, 101), book(2000, 90, 92), book(3000, 110, 112)]
    chk(first_book_at_or_after(books, 1500).recv_ts_ms == 2000,
        "entry uses the FIRST book at/after decision+latency")
    chk(first_book_at_or_after(books, 1500).best_ask == 92,
        "it does not scan ahead for the better price at t=3000 (no hindsight)")
    chk(first_book_at_or_after(books, 9999) is None,
        "no book after the decision -> None, not the last known book")

    # ------------------------------------------------------------- taker orientation
    print("\n[taker] LONG/SHORT orientation and cost accounting")
    e, x = book(0, 100.0, 100.1), book(1000, 110.0, 110.1)
    lo = simulate_taker(Action.TAKER_LONG, e, x, 1000.0, 180, 5.0, 1.0)
    chk(lo.entry_price == 100.1 and lo.exit_price == 110.0,
        "LONG enters on the ASK and exits on the BID")
    chk(lo.gross_pnl_usd > 0 and lo.net_pnl_usd < lo.gross_pnl_usd,
        "LONG profits on a rise, and net is strictly below gross (costs charged)")
    sh = simulate_taker(Action.TAKER_SHORT, x, e, 1000.0, 180, 5.0, 1.0)
    chk(sh.entry_price == 110.0 and sh.exit_price == 100.1,
        "SHORT enters on the BID and exits on the ASK")
    chk(sh.gross_pnl_usd > 0, "SHORT profits on a fall")
    flat = simulate_taker(Action.TAKER_LONG, e, book(1000, 100.0, 100.1), 1000.0, 180, 5.0, 1.0)
    chk(flat.net_pnl_usd < 0,
        "a flat market still LOSES the round trip - cost is never waived")
    chk(lo.fee_usd > 0 and lo.slippage_usd > 0, "both fee and impact are charged")

    # --------------------------------------------------------------- maker + standards
    print("\n[maker] fill standards and promotability")
    xb = book(1000, 110.0, 110.1)
    m_q = simulate_maker(Action.MAKER_LONG, 100.0, FillStandard.QUEUE_ESTIMATED,
                         xb, 1000.0, 180, 2.0, 1.0, 5.0)
    m_t = simulate_maker(Action.MAKER_LONG, 100.0, FillStandard.TOUCH_PROXY,
                         xb, 1000.0, 180, 2.0, 1.0, 5.0)
    chk(m_q.promotable and not m_t.promotable,
        "TOUCH_PROXY is NOT promotable; QUEUE_ESTIMATED is")
    chk("TOUCH_PROXY_NOT_PROMOTABLE" in m_t.reasons,
        "the touch-only outcome says why it cannot promote")
    unfilled = simulate_maker(Action.MAKER_LONG, 100.0, None, xb, 1000.0, 180,
                              2.0, 1.0, 5.0, missed_fill_opportunity_usd=3.0)
    chk(not unfilled.filled and unfilled.net_pnl_usd == 0.0
        and unfilled.missed_fill_opportunity_usd == 3.0,
        "an unfilled maker order is RECORDED with zero PnL and a missed-opportunity cost")
    taker_same = simulate_taker(Action.TAKER_LONG, book(0, 99.9, 100.0), xb, 1000.0, 180, 5.0, 1.0)
    chk(m_q.fee_usd < taker_same.fee_usd,
        "maker entry costs strictly less than the taker equivalent")

    # ------------------------------------------------------------------------ WAIT
    print("\n[wait] the default action")
    w = wait_outcome(180)
    chk(w.action is Action.WAIT and w.net_pnl_usd == 0.0 and not w.filled,
        "WAIT is priced at exactly zero and is always recorded")

    # ------------------------------------------------------------- viability gate
    print("\n[gate] horizon viability")
    ok30, why30 = check_horizon(p, 30, maker=False)
    chk(not ok30, f"30s TAKER is refused  ({why30.split(' - ')[0]})")
    ok180, _ = check_horizon(p, 180, maker=False)
    chk(not ok180,
        "180s TAKER is refused too - 16.97% is a NEAR miss, and a near miss is a miss")
    # The protocol's admissible lists must be DERIVED from the floor, never hand-written
    # alongside it. This assertion is what caught the two disagreeing.
    drift = []
    for maker in (False, True):
        style = "maker" if maker else "taker"
        for h_str, row in p.oracle_ceilings.items():
            h = int(h_str)
            measured_ok = row["maker_6bps" if maker else "taker_12bps"] >= p.viability_floor
            listed = h in p.admissible_horizons(maker)
            if measured_ok != listed:
                drift.append(f"{h}s {style}: measured_ok={measured_ok} listed={listed}")
    chk(not drift, "admissible lists agree with the floor for every horizon"
        + (f"  DRIFT: {drift}" if drift else ""))
    ok900, _ = check_horizon(p, 900, maker=False)
    chk(ok900, "900s taker is admissible (ceiling 44.04%)")
    ok180m, _ = check_horizon(p, 180, maker=True)
    chk(ok180m, "180s MAKER is admissible (ceiling 40.44%) - cheaper execution reopens it")
    raised = False
    try:
        require_horizon(p, 30, maker=False)
    except HorizonNotViable:
        raised = True
    chk(raised, "require_horizon RAISES on a disqualified horizon (cannot be ignored)")
    okX, whyX = check_horizon(p, 45, maker=False)
    chk(not okX and "no measured ceiling" in whyX,
        "an UNMEASURED horizon is refused rather than assumed viable")

    # ------------------------------------------------------------------- detectors
    print("\n[detect] fail-closed behaviour, no imputation")
    c = detect_liquidation_continuation(
        1, liq_notional_usd=500_000.0, liq_side="SELL", aggressive_flow_ratio=2.0,
        opposing_depth_ratio=0.4, vol_expansion_ratio=1.5)
    chk(c is not None and c.usable, "continuation fires when every condition is met")
    c2 = detect_liquidation_continuation(
        1, liq_notional_usd=500_000.0, liq_side="SELL", aggressive_flow_ratio=None,
        opposing_depth_ratio=0.4, vol_expansion_ratio=1.5)
    chk(c2 is not None and c2.data_quality is DataQuality.MISSING
        and "aggressive_flow_ratio" in c2.missing_inputs,
        "a missing input yields MISSING and NAMES the input (never imputed to zero)")
    chk(not c2.usable, "a MISSING candidate is not usable")
    c3 = detect_liquidation_continuation(
        1, liq_notional_usd=1.0, liq_side="SELL", aggressive_flow_ratio=2.0,
        opposing_depth_ratio=0.4, vol_expansion_ratio=1.5)
    chk(c3 is None, "a small liquidation simply does not fire (no event, no row)")
    e1 = detect_liquidation_exhaustion(
        1, liq_notional_usd=500_000.0, impact_decay_ratio=0.2,
        opposing_replenish_ratio=1.5, flow_decay_ratio=0.3)
    chk(e1 is not None and e1.usable, "exhaustion fires on decaying impact + replenishment")
    ll = detect_cross_venue_lead_lag(1, leader_move_bps=10.0, perp_move_bps=1.0,
                                     leader_venue="coinbase_spot")
    chk(ll is not None and ll.usable, "lead-lag fires when the perp has not repriced")
    ll2 = detect_cross_venue_lead_lag(1, leader_move_bps=10.0, perp_move_bps=9.5,
                                      leader_venue="coinbase_spot")
    chk(ll2 is None, "lead-lag does NOT fire once the perp has already repriced")
    fb = detect_funding_basis_oi(1, price_change_bps=10.0, oi_change_pct=0.02,
                                 basis_velocity_bps=3.0, funding_rate=0.0001,
                                 seconds_to_funding=600.0)
    chk(fb is not None and fb.features["quadrant"] == 3.0,
        "funding/basis/OI fires and records the price-up/OI-up quadrant distinctly")
    fb2 = detect_funding_basis_oi(1, price_change_bps=10.0, oi_change_pct=-0.02,
                                  basis_velocity_bps=3.0, funding_rate=0.0001,
                                  seconds_to_funding=600.0)
    chk(fb2.features["quadrant"] == 2.0,
        "price-up/OI-down is a DIFFERENT quadrant, not averaged with price-up/OI-up")

    # ------------------------------------------------------------------ segmenting
    print("\n[segments] recorder gaps are never stitched")
    segs = segment_events([0, 1000, 2000, 20000, 21000])
    chk(segs[2] != segs[3], "a >10s gap starts a NEW continuity segment")
    chk(segs[0] == segs[1] == segs[2] and segs[3] == segs[4],
        "contiguous events stay in one segment")
    chk(quality_for(["x"], [], False) is DataQuality.MISSING
        and quality_for([], ["y"], False) is DataQuality.STALE
        and quality_for([], [], True) is DataQuality.GAP_SEGMENTED
        and quality_for([], [], False) is DataQuality.OK,
        "quality precedence is missing > stale > gap > ok")

    # ------------------------------------------------------------------ archive
    print("\n[archive] the contract reports reality, including emptiness")
    with tempfile.TemporaryDirectory() as td:
        rep = evaluate_archive(os.path.join(td, "nope.duckdb"))
        chk(not rep.db_exists and not rep.any_ready,
            "a missing archive yields NOT_READY for every family")
        chk(all(rep.family_blockers[f.value] for f in Family),
            "every family names its blockers rather than silently passing")

    print("\nevent-conditional-v1:", "ALL PASS" if OK else "FAILURES")
    return 0 if OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
