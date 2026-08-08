"""
A fixed $250 paper stake, limits that scale with it, and a terminal ruin state.

The stake is fixed and never topped up, so the run has a definite end. Two things had to
change for that to mean anything:

LIMITS   The per-strategy risk limits were ABSOLUTE dollars sized for a $10,000 account:
         position notional 1,000, exposure 1,000, daily loss 100, weekly loss 250. Against
         a $250 stake that is 4x the whole account per position and a weekly loss limit
         equal to TOTAL RUIN. A limit that does not scale with the capital it protects is
         not a limit.

RUIN     Nothing stopped a strategy whose money was gone. The nearest check was
         `peak_equity_usd <= 0`, which tests the PEAK - an account that started at 250,
         peaked at 300 and fell to 0 has a valid peak and would have kept trading.

Run directly:  python backend/test_fixed_stake_and_ruin.py
"""

import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FAILURES = []


def chk(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def _snapshot(now):
    from binance_paper.schemas import DataQuality, MarketSnapshot
    return MarketSnapshot("BTCUSDT", now - 5, now, 100.0, 99.9, 100.1, 10.0, 10.0,
                          0.2, 20.0, 0, DataQuality.HEALTHY, 1, None, None, 0, 100, 100, None)


def test_fixed_stake():
    print("\nSTAKE the paper account is a fixed $250 and every limit is derived from it")
    from binance_paper.config import (
        DEFAULT_STARTING_CASH_USD, EngineConfig, StrategyRiskConfig)

    chk(DEFAULT_STARTING_CASH_USD == 250.0,
        "the stake is $250, fixed and never topped up - a bankroll that silently refills "
        "answers no question about whether the strategy works")
    cfg = EngineConfig.from_env()
    chk(cfg.starting_cash_usd == 250.0,
        f"and the engine starts there (${cfg.starting_cash_usd:.2f})")

    r = StrategyRiskConfig()
    cap = DEFAULT_STARTING_CASH_USD
    chk(r.max_position_notional_usd == cap * 0.10,
        f"one position risks at most ${r.max_position_notional_usd:.2f}, a tenth of the "
        f"book - it used to be $1,000, which is 4x the ENTIRE account")
    chk(r.max_account_exposure_usd == cap * 0.20,
        f"all open positions together at most ${r.max_account_exposure_usd:.2f}")
    chk(r.maximum_daily_loss_usd == cap * 0.05,
        f"the day stops after ${r.maximum_daily_loss_usd:.2f} - it used to be $100, which "
        f"is 40% of this account in a single day")
    chk(r.maximum_weekly_loss_usd == cap * 0.12,
        f"the week stops after ${r.maximum_weekly_loss_usd:.2f} - it used to be $250, "
        f"EXACTLY the whole account, so the weekly limit could never bind before ruin")

    for name, value in (("position", r.max_position_notional_usd),
                        ("exposure", r.max_account_exposure_usd),
                        ("daily loss", r.maximum_daily_loss_usd),
                        ("weekly loss", r.maximum_weekly_loss_usd)):
        if value >= cap:
            chk(False, f"the {name} limit is not smaller than the account")
            break
    else:
        chk(True, "and no single limit is as large as the account it protects")


def test_ruin_is_terminal_and_named():
    print("\nRUIN reaching zero is the run ENDING, and it says so")
    from binance_paper.config import StrategyRiskConfig
    from binance_paper.governor import CapitalPreservationGovernor, GovernorAccountState

    now = int(time.time() * 1000)
    snap = _snapshot(now)
    gov = CapitalPreservationGovernor(latency_ms=500, quote_stale_ms=2_000)
    risk = StrategyRiskConfig()

    def decide(equity, peak=250.0):
        state = GovernorAccountState("s", 250.0, equity, peak, 0.0, 0.0, risk)
        return gov.evaluate(snapshot=snap, accounts=[state], now_ms=now)

    healthy = decide(250.0)
    chk(healthy.mode.value == "NORMAL" and healthy.can_open,
        "a full account trades normally")

    ruined = decide(0.0)
    chk("capital_exhausted" in ruined.reason_codes,
        "an account at zero reports `capital_exhausted` under its own name - filing the end "
        "of a run under `capital_limit_severely_breached` would call it a limit breach")
    chk(not ruined.can_open and ruined.must_flatten,
        "it cannot open and must flatten - the position is terminal, not sized down")

    negative = decide(-5.0)
    chk("capital_exhausted" in negative.reason_codes and not negative.can_open,
        "and past zero is the same answer, not a new one")

    print("\n     ... and a PEAK above zero no longer hides it")
    peaked = decide(0.0, peak=300.0)
    chk("capital_exhausted" in peaked.reason_codes,
        "an account that started at 250, peaked at 300 and fell to 0 has a perfectly valid "
        "peak - the old `peak_equity_usd <= 0` check would have let it keep trading on an "
        "empty account")

    print("\n     ... and being too small to trade is its own state, before zero")
    starved = decide(risk.max_position_notional_usd - 1.0, peak=risk.max_position_notional_usd)
    chk("capital_below_minimum_position" in starved.reason_codes and not starved.can_open,
        f"below ${risk.max_position_notional_usd:.2f} the smallest permitted position no "
        f"longer fits, so it goes CLOSE_ONLY - still solvent, but it cannot produce further "
        f"evidence either")

    print("\n     ... the whole ladder, on the $250 stake")
    for equity in (250.0, 235.0, 224.0, 30.0, 0.0):
        d = decide(equity)
        print(f"       ${equity:>7.2f}  dd {(250.0 - equity) / 250.0:>6.1%}  "
              f"{d.mode.value:<18} open={str(d.can_open):<5} {d.reason_codes}")
    chk(decide(224.0).can_open is False,
        "new entries stop at a 10% drawdown - about $25 of the $250 - so the account cannot "
        "reach zero while the limits are in force. `capital_exhausted` is the answer if it "
        "ever does, not the plan")


def main():
    print("=" * 78)
    print("FIXED STAKE AND RUIN")
    print("=" * 78)
    test_fixed_stake()
    test_ruin_is_terminal_and_named()
    print("\n" + "=" * 78)
    if FAILURES:
        print(f"FIXED STAKE AND RUIN: FAIL ({len(FAILURES)})")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("FIXED STAKE AND RUIN: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
