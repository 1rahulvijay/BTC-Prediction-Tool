"""
Polymarket P&L simulator — what would betting the model's leans actually earn?

Run:  python backend/research/standalone/simulate_pnl.py

For every RESOLVED directional lean (UP/DOWN) the model made, this computes the P&L of
betting it on Polymarket at a range of assumed share prices, so you can see:
  - your break-even price (= your win rate), and
  - whether a given entry price is +EV or -EV.

Polymarket math per bet (stake S, share price c in [0,1]):
  shares  = S / c
  WIN  -> shares * $1 - S  =  S*(1-c)/c      (profit)
  LOSE -> -S                                  (lose the stake)
  EV/bet = winrate*S*(1-c)/c  -  (1-winrate)*S
The break-even price is exactly c = winrate.
"""

try:
    from . import _bootstrap as _research_bootstrap  # noqa: F401
except ImportError:
    import _bootstrap as _research_bootstrap  # noqa: F401

del _research_bootstrap

import os, time
os.environ.setdefault("BTC_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "..", "data"))
import duckdb
import database

STAKE = 5.0  # $ per bet


def connect():
    for _ in range(20):
        try:
            return duckdb.connect(database.DB_PATH, read_only=True)
        except Exception:
            time.sleep(1)
    return None


def winrate_for(con, horizon):
    """Strict directional win rate of the model's leans at this horizon (by move sign)."""
    r = con.execute(f"""
        SELECT count(*) FILTER(WHERE raw_direction IN('UP','DOWN') AND resolved),
               count(*) FILTER(WHERE raw_direction IN('UP','DOWN') AND resolved
                    AND ((raw_direction='UP' AND actual_move>0) OR (raw_direction='DOWN' AND actual_move<0)))
        FROM predictions_{horizon}m""").fetchone()
    return r[0], (r[1] / r[0] if r[0] else 0.0)


def main():
    con = connect()
    if con is None:
        print("DB locked — try again in a moment."); return
    print("=" * 70)
    print(f"POLYMARKET P&L SIM  (stake ${STAKE:.0f}/bet)")
    print("=" * 70)
    for h in (5, 15):
        n, wr = winrate_for(con, h)
        print(f"\n{h}m  —  {n} resolved leans, win rate {wr*100:.0f}%  (break-even price = {wr*100:.0f}c)")
        if n < 10:
            print("   too few bets to judge")
            continue
        print(f"   {'entry price':>12} | {'EV/bet':>8} | {'over 100 bets':>14}")
        for c in (0.45, 0.50, 0.55, wr, 0.60, 0.83):
            win_profit = STAKE * (1 - c) / c
            ev = wr * win_profit - (1 - wr) * STAKE
            tag = "  <- break-even" if abs(c - wr) < 1e-6 else (
                  "  <- your $5/+$1 example" if abs(c - 0.83) < 0.01 else "")
            print(f"   {c*100:>10.0f}c | {ev:>+7.2f} | {ev*100:>+13.0f}{tag}")
    con.close()
    print("\nRULE: buy the leaned side ONLY when Polymarket's price is BELOW your win rate.")
    print("Above it you lose long-run even when you're 'right'.")


if __name__ == "__main__":
    main()
