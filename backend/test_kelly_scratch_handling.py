"""Kelly sizing must not charge a scratch the price of a loss.

THE DEFECT
    _compute_kelly_fraction classified trades as wins (net_pnl > 0) and losses (net_pnl < 0),
    then computed

        win_rate = len(wins) / len(recent)               # denominator includes scratches
        kelly    = (win_rate*b - (1 - win_rate)) / b     # so (1 - win_rate) carries them
        b        = avg_win / avg_loss                    # built from REAL losses only

    A trade returning exactly zero fell into neither list, yet entered the formula through
    (1 - win_rate) and was implicitly assigned the average LOSING magnitude. Whenever scratches
    were common, size was understated - and in the limit a strategy of mostly-scratches with a
    small real edge could be sized at zero.

WHY p = wins/(wins+losses) IS NOT THE REPAIR
    Dropping scratches from the denominator discards the fact that capital was committed and
    returned nothing. That genuinely lowers growth. It OVERSTATES size, trading one bias for a
    more dangerous one.

THE REPAIR
    Maximise empirical expected log growth over the actual after-cost returns:

        g(f) = mean( log(1 + f * r_i) )

    A zero return contributes log(1) = 0 to the sum while still counting in the mean, so it
    dilutes growth without being charged as a loss. No special case is needed, because the
    arithmetic already encodes what a scratch is.

    python backend/test_kelly_scratch_handling.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _simulator():
    from trading_simulator import TradingSimulator

    return TradingSimulator()


DAY_MS = 86_400_000


def _trade(net: float, size: float = 1000.0, index: int = 0) -> dict:
    """One trade, spread across DISTINCT UTC days.

    Spacing these 1 ms apart put every trade in a single UTC day, so the day-block bootstrap
    correctly refused for want of days - and the assertions below then passed on the research
    PROBE rather than on the quantity they claim to measure. Test data has to satisfy the
    method's preconditions or the test proves nothing."""
    return {"net_pnl_usd": net, "position_size": size,
            "timestamp": 1_700_000_000_000 + index * (DAY_MS // 8)}


def _legacy_binary_kelly(recent: list) -> float:
    """The formula this replaces, kept so the difference can be MEASURED rather than asserted."""
    wins = [t for t in recent if t["net_pnl_usd"] > 0]
    losses = [t for t in recent if t["net_pnl_usd"] < 0]
    if not wins or not losses:
        return 0.02
    win_rate = len(wins) / len(recent)
    avg_win = sum(t["net_pnl_usd"] for t in wins) / len(wins)
    avg_loss = abs(sum(t["net_pnl_usd"] for t in losses) / len(losses))
    if avg_loss <= 0:
        return 0.02
    b = avg_win / avg_loss
    return max(0.0, ((win_rate * b - (1 - win_rate)) / b) / 2.0)


def test_scratches_are_not_charged_as_losses() -> None:
    print("a scratch is not charged the average losing magnitude")
    sim = _simulator()

    # 40 wins of +2%, 20 losses of -2%, 40 scratches of exactly 0. A clear positive edge.
    #
    # INTERLEAVED, because the bootstrap resamples whole UTC days. Laying all the wins first,
    # then all the losses, then all the scratches makes every day a single pure outcome, so a
    # resample can draw an all-loss universe and the bound correctly refuses. That is the method
    # working, not a bug - but it is not the property this test is about. Real trading mixes
    # outcomes within a day, so the fixture does too.
    pattern = [+20.0] * 4 + [-20.0] * 2 + [0.0] * 4      # one day's worth, repeated
    trades = [_trade(pattern[i % len(pattern)], index=i) for i in range(100)]

    paired = [(t["net_pnl_usd"] / t["position_size"], t["timestamp"]) for t in trades]
    new = sim.assess_kelly(paired).authorized_fraction
    legacy = _legacy_binary_kelly(trades)

    print(f"       legacy binary half-Kelly = {legacy:.4f}")
    print(f"       empirical full Kelly     = {new:.4f}")
    chk(new > 0.0, f"the empirical method finds the real edge ({new:.4f})")
    chk(new > legacy,
        f"and sizes ABOVE the legacy formula ({new:.4f} > {legacy:.4f}), which had charged "
        f"40 zero-return trades at the average LOSS")


def test_scratches_still_dilute_growth() -> None:
    """Scratches must not be free either - committed capital that returns nothing lowers growth."""
    print("scratches still dilute growth (they are not simply discarded)")
    sim = _simulator()

    edge_only = [0.02] * 40 + [-0.02] * 20
    with_scratches = edge_only + [0.0] * 40

    f_pure = sim._empirical_kelly(edge_only)
    f_diluted = sim._empirical_kelly(with_scratches)
    g_pure = sim.expected_log_growth(f_pure, edge_only)
    g_diluted = sim.expected_log_growth(f_diluted, with_scratches)

    print(f"       growth without scratches = {g_pure:.6f}")
    print(f"       growth with 40 scratches = {g_diluted:.6f}")
    chk(g_diluted < g_pure,
        "adding zero-return trades LOWERS expected growth - dropping them from the "
        "denominator would have hidden this")


def test_no_edge_returns_zero_in_live_mode() -> None:
    print("no demonstrable edge means zero size in live mode")
    sim = _simulator()

    import random

    rng = random.Random(11)
    # EXACTLY balanced: 60 up, 60 down, shuffled. There is no edge to find by construction,
    # rather than "probably no edge" - a lucky imbalance would make this test assert nothing.
    noise = [0.02] * 60 + [-0.02] * 60
    rng.shuffle(noise)
    chk(abs(sum(noise)) < 1e-12, "the control series is exactly balanced by construction")
    live = sim._empirical_kelly(noise, live_mode=True)
    chk(live == 0.0, f"live sizing on a no-edge series is exactly zero ({live})")
    chk(sim.kelly_maximiser(noise) == 0.0,
        "and even the unguarded maximiser finds nothing on a symmetric series")


def test_a_ruinous_outcome_is_excluded() -> None:
    print("a fraction that could wipe the stake is never selected")
    sim = _simulator()

    # A -100% outcome means 1 + f*r <= 0 for f >= 1; the search must reject it.
    returns = [0.05] * 50 + [-1.0] * 5
    f = sim.kelly_maximiser(returns)
    chk(f < 1.0, f"selected fraction stays below total loss ({f:.4f})")
    chk(math.isinf(sim.expected_log_growth(1.0, returns)),
        "full commitment against a -100% outcome is -inf growth, so it can never win the search")


def test_growth_is_maximised_not_guessed() -> None:
    print("the selected fraction really maximises empirical log growth")
    sim = _simulator()

    returns = [0.03] * 60 + [-0.02] * 40
    f = sim.kelly_maximiser(returns)
    g = sim.expected_log_growth(f, returns)
    finer = [x / 2000.0 for x in range(0, 1001)]     # step 0.0005, finer than the 0.0025 grid
    best = max(sim.expected_log_growth(x, returns) for x in finer)
    chk(g >= best - 1e-6,
        f"no fraction on a FINER grid beats the selection ({g:.6f} vs best {best:.6f})")
    chk(sim._empirical_kelly(returns) <= f,
        "the evidence-gated size never exceeds the maximiser")


def test_end_to_end_sizing_stays_capped() -> None:
    print("the 2% cap and minimum-sample rule still hold")
    sim = _simulator()
    sim.trade_history = [_trade(+50.0, index=i) for i in range(100)]
    fraction = sim._compute_kelly_fraction()
    chk(fraction <= 0.02, f"an enormous apparent edge is still capped at 2% ({fraction})")

    sim.trade_history = [_trade(+50.0, index=i) for i in range(10)]
    chk(sim._compute_kelly_fraction() == 0.01,
        "fewer than 30 trades returns the conservative 1% default")


def test_bootstrap_resamples_UTC_DAYS_not_trade_indices() -> None:
    """It was called a day-block bootstrap while resampling trade-INDEX blocks of n//10.

    Trade-index blocks are not days. A quiet day and a busy day contribute different numbers of
    trades, so a fixed index block spans a varying and unknown amount of calendar time - and the
    dependence the method exists to respect is calendar dependence."""
    print("the bootstrap groups by UTC day, not by trade index")
    sim = _simulator()

    # Same returns, same count, but compressed into ONE day. Days, not indices, must decide.
    one_day = [(0.02 if i % 3 else -0.02, 1_700_000_000_000 + i) for i in range(120)]
    verdict = sim.assess_kelly(one_day)
    chk(verdict.day_count == 1, f"120 trades one millisecond apart are ONE day ({verdict.day_count})")
    chk(verdict.lower_bound_passed is False,
        "a single day cannot support a day-block bound, so nothing is authorized")
    chk(verdict.authorized_fraction == 0.0, "authorized fraction is exactly zero")
    chk("days" in verdict.reason, f"and the reason names the constraint ({verdict.reason})")

    spread = [(0.02 if i % 3 else -0.02, 1_700_000_000_000 + i * DAY_MS // 4) for i in range(120)]
    spread_verdict = sim.assess_kelly(spread)
    chk(spread_verdict.day_count > sim.MIN_BOOTSTRAP_DAYS,
        f"the identical returns spread over calendar time give {spread_verdict.day_count} days")


def test_assessment_separates_estimate_from_authorization() -> None:
    print("point estimate, evidence and authorization are separate fields")
    sim = _simulator()

    one_day = [(0.05, 1_700_000_000_000 + i) for i in range(60)]     # strong but ONE day
    verdict = sim.assess_kelly(one_day)
    chk(verdict.point_estimate > 0.0,
        f"the sample still HAS a positive maximiser ({verdict.point_estimate:.4f})")
    chk(verdict.authorized_fraction == 0.0,
        "but nothing is authorized, because the evidence did not clear the bound")
    chk(verdict.research_probe_fraction > 0.0,
        f"a labelled research probe remains ({verdict.research_probe_fraction})")
    chk(sim.assess_kelly(one_day, live_mode=True).research_probe_fraction == 0.0,
        "and in LIVE mode even the probe is zero")


def test_pnl_ledger_reconciles_with_no_double_counting() -> None:
    """Realized PnL must equal the adjusted-price difference minus fees, and nothing else."""
    print("realized PnL reconciles exactly (slippage counted once)")
    size_btc = 0.5
    price_in, price_out = 60000.0, 60600.0
    slip, fee = 0.0002, 0.0004

    entry_price = price_in * (1 + slip)          # UP: buy worse
    exit_price = price_out * (1 - slip)          # UP: sell worse
    gross = (exit_price - entry_price) * size_btc
    fees = (size_btc * entry_price * fee) + (size_btc * price_out * fee)

    correct = gross - fees
    doubled = gross - fees - (price_out * slip * size_btc) * 2

    print(f"       adjusted-price PnL - fees      = {correct:.4f}")
    print(f"       with slippage charged AGAIN    = {doubled:.4f}")
    chk(abs(correct - doubled) > 1e-9,
        f"the two differ by {abs(correct - doubled):.4f} USD per round trip")

    # The slippage embedded in the adjusted prices equals the amount that was being re-subtracted.
    embedded = (price_in * slip + price_out * slip) * size_btc
    chk(abs(embedded - (price_out * slip * size_btc) * 2) < 2.0,
        "the embedded cost and the re-subtracted amount are the same quantity, to rounding")

    from pathlib import Path as _P
    src = (_P(__file__).resolve().parent / "trading_simulator.py").read_text(encoding="utf-8")
    chk("net_pnl_usd = pnl_usd - fees_usd" in src
        and "net_pnl_usd = pnl_usd - fees_usd - slippage_usd" not in src,
        "the simulator subtracts fees only - slippage is already inside the adjusted prices")


def test_simulator_declares_itself_non_promotable() -> None:
    print("the simulator states its own standing")
    sim = _simulator()
    status = sim.calibration_status
    chk(status.get("Promotion status") == "HEURISTIC_RESEARCH_ONLY / NON_PROMOTABLE",
        f"promotion status is declared ({status.get('Promotion status')})")
    chk("NOT APPLIED" in (status.get("Fill realization") or ""),
        "and it states that fill_prob is computed but never applied to the fill outcome")


def main() -> int:
    test_scratches_are_not_charged_as_losses()
    test_scratches_still_dilute_growth()
    test_no_edge_returns_zero_in_live_mode()
    test_a_ruinous_outcome_is_excluded()
    test_growth_is_maximised_not_guessed()
    test_end_to_end_sizing_stays_capped()
    test_bootstrap_resamples_UTC_DAYS_not_trade_indices()
    test_assessment_separates_estimate_from_authorization()
    test_pnl_ledger_reconciles_with_no_double_counting()
    test_simulator_declares_itself_non_promotable()
    print("\nKELLY SCRATCH HANDLING", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
