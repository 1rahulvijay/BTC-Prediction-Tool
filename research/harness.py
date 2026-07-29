"""Shared harness so every research script is measured the same, honest way.

WHY THIS EXISTS
    An audit of research/v1..v31 found four disqualifying patterns. Three of them are
    accounting or methodology errors that a shared harness simply removes:

      * UNBOUNDED LOSS - scripts did `capital += 1000.0 * bps` with a FIXED notional
        regardless of remaining balance, so capital went arbitrarily negative and produced
        "returns" of -212%, -834% and -10032%. Unleveraged capital cannot lose more than
        100%. Here the stake is a FRACTION of current capital and capital floors at zero.

      * NO OUT-OF-SAMPLE - 28 of 31 reported performance from the data the rule was chosen
        on. split() is mandatory here, and report() prints IN-SAMPLE and OUT-OF-SAMPLE side
        by side so the gap between them is impossible to hide.

      * NO COSTS - a round trip pays fees and spread. Applied on every trade by default.

    The fourth pattern - assuming the outcome (`if np.random.rand() < 0.78`) - cannot be
    fixed by a harness. It requires the script to actually measure a prediction, which is
    what the rewrites do.

WHAT A RESULT MEANS HERE
    Trades are sized as a fixed FRACTION of current capital, so results compound and a
    losing strategy asymptotes toward -100% instead of running to minus infinity. Every
    number is net of costs. A positive in-sample number with a negative out-of-sample number
    is the normal signature of overfitting, and printing both is the point.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
BTC_CSV = REPO / "data" / "btc_1m_data.csv"

# One round trip: taker in, taker out, plus a spread allowance. Deliberately not optimistic.
DEFAULT_FEE_BPS = 4.0
DEFAULT_SPREAD_BPS = 1.0
DEFAULT_RISK_FRACTION = 0.02        # 2% of capital per trade
TRAIN_FRACTION = 0.70


def load_btc(rows: int | None = 200_000) -> pd.DataFrame:
    """Load real 1-minute BTC data. Newest `rows` bars, chronological."""
    frame = pd.read_csv(BTC_CSV)
    if rows and len(frame) > rows:
        frame = frame.tail(rows)
    return frame.reset_index(drop=True)


def split(frame: pd.DataFrame, train_fraction: float = TRAIN_FRACTION):
    """CHRONOLOGICAL split. Never random - shuffling a time series leaks the future."""
    cut = int(len(frame) * train_fraction)
    return frame.iloc[:cut].copy(), frame.iloc[cut:].copy()


def forward_returns(frame: pd.DataFrame, horizon: int = 15) -> pd.Series:
    """Return over the NEXT `horizon` bars. Shifted so bar t never sees its own outcome."""
    return frame["close"].shift(-horizon) / frame["close"] - 1.0


class Backtest:
    """Fraction-of-capital sizing, costs on every trade, capital floored at zero."""

    def __init__(self, capital: float = 10_000.0,
                 fee_bps: float = DEFAULT_FEE_BPS,
                 spread_bps: float = DEFAULT_SPREAD_BPS,
                 risk_fraction: float = DEFAULT_RISK_FRACTION) -> None:
        self.initial = float(capital)
        self.capital = float(capital)
        self.cost = (2.0 * fee_bps + spread_bps) / 10_000.0   # round trip
        self.risk_fraction = float(risk_fraction)
        self.wins = 0
        self.losses = 0
        self.trades = 0
        self.gross_returns: list[float] = []

    def trade(self, gross_return: float) -> float:
        """Book one trade whose GROSS directional return is `gross_return`."""
        if not np.isfinite(gross_return):
            return 0.0
        if self.capital <= 0.0:
            return 0.0                    # bankrupt accounts do not keep trading
        net = float(gross_return) - self.cost
        stake = self.capital * self.risk_fraction
        pnl = stake * net
        self.capital = max(0.0, self.capital + pnl)
        self.trades += 1
        self.gross_returns.append(net)
        if net > 0:
            self.wins += 1
        else:
            self.losses += 1
        return pnl

    @property
    def total_return_pct(self) -> float:
        return (self.capital / self.initial - 1.0) * 100.0

    @property
    def win_rate_pct(self) -> float:
        return (self.wins / self.trades * 100.0) if self.trades else 0.0

    @property
    def sharpe(self) -> float:
        if len(self.gross_returns) < 2:
            return 0.0
        values = np.asarray(self.gross_returns, dtype=float)
        sd = values.std(ddof=1)
        if sd <= 0:
            return 0.0
        # Per-trade Sharpe annualised by trade count, not by calendar - stated plainly
        # rather than dressed up as an annual figure the sample cannot support.
        return float(values.mean() / sd * np.sqrt(len(values)))

    def summary(self) -> dict:
        return {
            "trades": self.trades,
            "win_rate_pct": round(self.win_rate_pct, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "sharpe": round(self.sharpe, 2),
            "final_capital": round(self.capital, 2),
        }


def report(name: str, in_sample: Backtest, out_of_sample: Backtest,
           notes: str = "") -> dict:
    """Print IS and OOS together. The gap between them is the finding."""
    print("=" * 78)
    print(f"{name}")
    print("=" * 78)
    print(f"{'':<22}{'IN-SAMPLE':>16}{'OUT-OF-SAMPLE':>18}")
    for label, key in (("trades", "trades"), ("win rate %", "win_rate_pct"),
                       ("total return %", "total_return_pct"), ("sharpe", "sharpe")):
        a = in_sample.summary()[key]
        b = out_of_sample.summary()[key]
        print(f"  {label:<20}{a:>16}{b:>18}")

    oos = out_of_sample.summary()
    verdict = (
        "NO EDGE - out-of-sample return is not positive"
        if oos["total_return_pct"] <= 0 else
        "POSITIVE OUT-OF-SAMPLE - still requires forward evidence before any use"
    )
    if out_of_sample.trades < 30:
        verdict = f"INCONCLUSIVE - only {out_of_sample.trades} out-of-sample trades"
    print(f"\n  VERDICT: {verdict}")
    if notes:
        print(f"  NOTE: {notes}")
    print(f"  costs applied: {in_sample.cost * 10_000:.1f} bps round trip, "
          f"stake = {in_sample.risk_fraction * 100:.0f}% of capital")
    return {"name": name, "in_sample": in_sample.summary(),
            "out_of_sample": oos, "verdict": verdict}
