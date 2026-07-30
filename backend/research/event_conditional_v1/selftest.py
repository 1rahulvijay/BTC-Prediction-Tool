"""Executing tests for BINANCE_EVENT_CONDITIONAL_PROFIT_V1 Phase 1.

These run behaviour. Nothing here inspects source text. Exit code is nonzero on any
failure so CI cannot report a false green.

    python -m backend.research.event_conditional_v1.selftest
"""
from __future__ import annotations

import os
import tempfile

from .contracts import (
    Action, DataQuality, Family, FillStandard, load_protocol,
)
from .data_contract import evaluate_archive, quality_for, segment_events
from .event_detectors import (
    detect_cross_venue_lead_lag, detect_funding_basis_oi,
    detect_liquidation_continuation, detect_liquidation_exhaustion,
)
from .execution import (
    BookState, binance_fee_usd, first_book_at_or_after, ladder_vwap,
    simulate_maker, simulate_taker, wait_outcome,
)
from .viability import HorizonNotViable, check_horizon, clearance, require_horizon

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
    from backend.research.executable_surface_config import taker_fee as poly_fee
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
    print("\n[wait] the benchmark row is ENFORCED, and promotion fails closed")
    w = wait_outcome(180)
    chk(w.action is Action.WAIT and w.net_pnl_usd == 0.0 and not w.filled,
        "WAIT is priced at exactly zero and is always recorded")
    chk(not w.promotable, "WAIT is never promotable - it is the benchmark, not a strategy")

    from .contracts import ActionOutcome, EventCandidate, EventDecision
    bare = ActionOutcome(action=Action.TAKER_LONG, horizon_s=300, filled=False,
                         fill_standard=None, entry_price=None, exit_price=None,
                         quantity=0.0, notional_usd=0.0, gross_pnl_usd=0.0, fee_usd=0.0,
                         slippage_usd=0.0, net_pnl_usd=0.0, holding_time_s=0.0)
    chk(not bare.promotable,
        "a freshly constructed outcome defaults to NOT promotable (fails closed)")

    cand = EventCandidate(family=Family.LIQUIDATION_CONTINUATION, ts_ms=1)
    ok_dec = EventDecision(candidate=cand, outcomes=[wait_outcome(300), lo])
    chk(ok_dec.selected is Action.WAIT, "a decision carrying WAIT constructs fine")
    raised = False
    try:
        EventDecision(candidate=cand, outcomes=[lo])          # no WAIT row
    except ValueError:
        raised = True
    chk(raised, "a decision WITHOUT the WAIT row RAISES - the benchmark cannot go missing")
    raised = False
    try:
        EventDecision(candidate=cand, outcomes=[wait_outcome(300), wait_outcome(300)])
    except ValueError:
        raised = True
    chk(raised, "two WAIT rows RAISE - exactly one benchmark, never a duplicate")
    tainted = wait_outcome(300)
    tainted.net_pnl_usd = 5.0
    raised = False
    try:
        EventDecision(candidate=cand, outcomes=[tainted])
    except ValueError:
        raised = True
    chk(raised, "a WAIT row with nonzero PnL RAISES - doing nothing cannot earn money")

    # ------------------------------------------------------------- viability gate
    print("\n[gate] horizon viability")
    ok30, why30 = check_horizon(p, 30, maker=False)
    chk(not ok30, f"30s TAKER refused  ({why30.split(' (')[0].split(': ')[1]})")
    ok180, _ = check_horizon(p, 180, maker=False)
    chk(not ok180, "180s TAKER refused too - LB95 15.29% is a near miss, and a miss")

    # THE reason admission uses a lower bound: 60s maker passes on the point estimate
    # (21.46%) and fails on LB95 (19.59%). If this ever inverts, the gate has been
    # quietly switched back to point estimates.
    pt60m = clearance(p, 60, maker=True, lower_bound=False)
    lb60m = clearance(p, 60, maker=True, lower_bound=True)
    chk(pt60m >= p.viability_floor > lb60m,
        f"60s maker: point {pt60m:.2%} PASSES, LB95 {lb60m:.2%} FAILS - the case that "
        f"makes the lower bound load-bearing")
    ok60m, _ = check_horizon(p, 60, maker=True)
    chk(not ok60m, "60s maker is therefore REFUSED (admission follows LB95, not the point)")

    # Admissible lists must be DERIVED from the floor, never hand-written alongside it.
    drift = []
    for maker in (False, True):
        style = "maker" if maker else "taker"
        for h_str in p.clearance_points:
            h = int(h_str)
            lb = clearance(p, h, maker, lower_bound=True)
            measured_ok = lb is not None and lb >= p.viability_floor
            listed = h in p.admissible_horizons(maker)
            if measured_ok != listed:
                drift.append(f"{h}s {style}: lb_ok={measured_ok} listed={listed}")
    chk(not drift, "admissible lists agree with the LB95 floor for every horizon"
        + (f"  DRIFT: {drift}" if drift else ""))

    # The selected grid is a deterministic subset: shortest, middle, longest.
    for maker in (False, True):
        style = "maker" if maker else "taker"
        adm = sorted(p.admissible_horizons(maker))
        want = sorted({adm[0], adm[(len(adm) - 1) // 2], adm[-1]})
        got = sorted(p.selected_horizons(maker))
        chk(got == want, f"{style} grid is the deterministic shortest/middle/longest "
                         f"of {adm} -> {got}")
        chk(set(got).issubset(set(adm)), f"{style} selected grid is a subset of admissible")
        chk(len(got) <= 3, f"{style} grid is capped at 3 horizons (limits trial count)")

    ok900, _ = check_horizon(p, 900, maker=False)
    chk(ok900, "900s taker is admissible (LB95 41.59%)")
    ok180m, _ = check_horizon(p, 180, maker=True)
    chk(ok180m, "180s MAKER is admissible (LB95 38.07%) - cheaper execution reopens it")
    raised = False
    try:
        require_horizon(p, 30, maker=False)
    except HorizonNotViable:
        raised = True
    chk(raised, "require_horizon RAISES on a disqualified horizon (cannot be ignored)")
    okX, whyX = check_horizon(p, 45, maker=False)
    chk(not okX and "no measured clearance" in whyX,
        "an UNMEASURED horizon is refused rather than assumed viable")

    # The optimistic maker scenario must never be able to admit anything.
    gate = p.raw["horizon_viability_gate"]
    chk(gate["primary_cost_scenarios"] == {"taker": "taker_12bps", "maker": "maker_6bps"},
        "PRIMARY cost scenarios are 12bps taker / 6bps maker")
    chk("maker_4bps" in gate["sensitivity_only_scenarios"]
        and "maker_4bps" not in gate["primary_cost_scenarios"].values(),
        "maker_4bps is SENSITIVITY_ONLY and cannot admit a horizon")
    chk("clearance_lb95_by_horizon_s" in gate["measured_evidence"]
        and "maker_4bps" not in gate["measured_evidence"]["clearance_lb95_by_horizon_s"]["30"],
        "no LB is even published for the sensitivity scenario - it cannot be gated on")

    # The design/test separation must be recorded, or these days could later be
    # reused as 'untouched' evidence for the very horizons they selected.
    chk(gate["measured_evidence"]["dataset_role"] == "DESIGN_ONLY",
        "the 129-day sample is marked DESIGN_ONLY (it selected the horizons)")
    smp = gate["measured_evidence"]["sample"]
    chk(smp["days_sampled"] == 129 and smp["days_available"] == 1286
        and len(smp["manifest_sha256"]) >= 16 and smp["bootstrap_seed"] == 20260728,
        "sample manifest records sampled/available days, hash and bootstrap seed")

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
    print("\n[segments] gaps, reconnects, sequence and clock breaks are never stitched")
    segs = segment_events([0, 1000, 2000, 20000, 21000])
    chk(segs[2] != segs[3], "a >10s gap starts a NEW continuity segment")
    chk(segs[0] == segs[1] == segs[2] and segs[3] == segs[4],
        "contiguous events stay in one segment")
    # Time alone is not sufficient: these events are 20ms apart but the recorder
    # reconnected in between, so the book between them was never observed.
    s2 = segment_events([0, 20, 40, 60], sessions=["a", "a", "b", "b"])
    chk(s2[1] != s2[2],
        "a recorder-session change splits the segment even 20ms apart (reconnect)")
    s3 = segment_events([0, 20, 40, 60], seqs=[10, 11, 5, 6])
    chk(s3[1] != s3[2], "a sequence REGRESSION splits (the book was rebuilt)")
    s4 = segment_events([0, 20, 40, 60], schema_versions=["v1", "v1", "v2", "v2"])
    chk(s4[1] != s4[2], "a schema-version change splits")
    s5 = segment_events([1000, 2000, 1500, 2500])
    chk(s5[1] != s5[2], "a CLOCK regression splits (timestamps went backwards)")
    s6 = segment_events([0, 20, 40], sessions=["a", "a", "a"], seqs=[1, 2, 3])
    chk(len(set(s6)) == 1, "a clean run with a healthy session stays ONE segment")

    # Stream tiers: the family must not silently run on a different venue set.
    from .data_contract import REQUIRED_STREAMS, VARIANTS, Tier
    core = {r.key for r in REQUIRED_STREAMS if r.tier is Tier.CORE_REQUIRED}
    variant = {r.key for r in REQUIRED_STREAMS if r.tier is Tier.VARIANT_REQUIRED}
    chk("bybit_perp" in variant and "coinbase_spot" in variant,
        "second-venue feeds are VARIANT_REQUIRED, not silently 'optional' in a required list")
    chk(core.isdisjoint(variant), "a stream is in exactly one tier")
    chk(set(VARIANTS["CROSS_VENUE_BINANCE_SPOT_PERP_V1"]).isdisjoint(variant),
        "the two-venue variant needs NO variant-tier stream")
    chk(variant.issubset(set(VARIANTS["CROSS_VENUE_FOUR_VENUE_V1"])),
        "the four-venue variant names every variant-tier stream it needs")
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
        import duckdb
        db = os.path.join(td, "seconds.duckdb")
        con = duckdb.connect(db)
        con.execute("CREATE TABLE venue_events(venue VARCHAR, stream VARCHAR, recv_ts DOUBLE)")
        start_s = 1_700_000_000.0
        for req in REQUIRED_STREAMS:
            con.execute(
                "INSERT INTO venue_events VALUES (?, ?, ?), (?, ?, ?)",
                [
                    req.venue, req.stream, start_s,
                    req.venue, req.stream, start_s + 86_400.0,
                ],
            )
        con.close()
        rep = evaluate_archive(db)
        chk(abs(rep.span_days - 1.0) < 1e-9,
            "epoch seconds are normalized correctly (one day is not reported as 0.001d)")
        chk(all(stream.present for stream in rep.streams),
            "the contract names the streams the recorder actually writes")

    print("\nevent-conditional-v1:", "ALL PASS" if OK else "FAILURES")
    return 0 if OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
