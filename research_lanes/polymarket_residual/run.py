"""POLYMARKET_RESIDUAL_V1 — is the model better than the market's own price, after fees?

THE QUESTION
    Not "does the model predict settlement". The market already predicts settlement, and you
    must pay to disagree with it. So:

        baseline A   the executable market price itself
        baseline B   the app's p_hold
        test         does B beat A on Brier, and does trading the disagreement net money?

    A model can beat the market on Brier and still lose, because crossing the spread and
    paying the taker fee costs more than the edge. Both are measured.

FEES
    Polymarket crypto taker fee = C * 0.07 * p * (1-p) per share, maker platform fee zero.
    At p=0.50 that is 1.75c on a $1 contract - 3.5% of a 50c entry. It dominates small edges,
    which is exactly why the naive "model says 0.68, ask is 0.61, buy" arithmetic misleads.

    python research_lanes/polymarket_residual/run.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

LANE = Path(__file__).resolve().parent
sys.path.insert(0, str(LANE.parent))
from common.pm_data import brier, load_official, round_bootstrap  # noqa: E402

TAKER_COEF = 0.07


def taker_fee(price: np.ndarray) -> np.ndarray:
    """Per-share taker fee at a given execution price."""
    p = np.asarray(price, float)
    return TAKER_COEF * p * (1.0 - p)


def main() -> int:
    df = load_official()
    if df.empty:
        print("no joined rows")
        return 1

    # P(UP) from the app, taken from p_hold_up directly.
    #
    # NOT derived from p_hold_cur + current_side. `current_side` in this export is '0.0'/'1.0',
    # not 'UP'/'DOWN', so a string comparison against "UP" silently matches nothing and flips
    # EVERY row. That produced a model Brier of 0.3109 - worse than always predicting 0.5 -
    # which is the tell: a forecast that bad is usually inverted, not uninformative.
    #
    # Measured on the joined set:
    #     p_hold_up                     Brier 0.1832   corr +0.517
    #     p_hold_cur flipped by side    Brier 0.3109   corr -0.009
    #     constant 0.5                  Brier 0.2500
    p_model = df["p_hold_up"].astype(float).to_numpy()
    ok = np.isfinite(p_model) & (p_model > 0) & (p_model < 1)
    d = df[ok].copy()
    d["p_model"] = p_model[ok]
    d["p_market"] = ((d["up_bid"] + d["up_ask"]) / 2.0).astype(float)
    y = d["settled_up"].to_numpy(float)

    print(f"rows={len(d):,}  rounds={d.round_id.nunique():,}  days={d.day.nunique()}  "
          f"base rate UP={y.mean():.3f}")

    # ---- 1. Does the model beat the market as a FORECAST? --------------------------------
    bm = brier(d["p_market"].to_numpy(), y)
    bd = brier(d["p_model"].to_numpy(), y)
    diff = (d["p_market"].to_numpy() - y) ** 2 - (d["p_model"].to_numpy() - y) ** 2
    bs = round_bootstrap(diff, d["round_id"].to_numpy(), np.mean, n_boot=800)
    print(f"\nBrier  market={bm:.4f}  model={bd:.4f}  "
          f"improvement={bs['point']:+.4f} [{bs['lcb']:+.4f}, {bs['ucb']:+.4f}] "
          f"over {bs['n_rounds']} rounds")
    print(f"  model beats market on Brier: {bs['lcb'] > 0}")

    # ---- 2. Does trading the disagreement make money at EXECUTABLE prices? ---------------
    ask, bid = d["up_ask"].to_numpy(float), d["up_bid"].to_numpy(float)
    fee_up = taker_fee(ask)
    fee_dn = taker_fee(1.0 - bid)
    # BUY UP  at ask: pay ask+fee, receive 1 if UP.
    pnl_up = np.where(y > 0, 1.0, 0.0) - ask - fee_up
    # BUY DOWN at (1-bid): pay (1-bid)+fee, receive 1 if DOWN.
    pnl_dn = np.where(y > 0, 0.0, 1.0) - (1.0 - bid) - fee_dn
    edge_up = d["p_model"].to_numpy() - ask - fee_up
    edge_dn = (1.0 - d["p_model"].to_numpy()) - (1.0 - bid) - fee_dn

    rows = []
    for thr in (0.00, 0.02, 0.04, 0.06, 0.08, 0.10):
        take_up, take_dn = edge_up > thr, edge_dn > thr
        n = int(take_up.sum() + take_dn.sum())
        if n == 0:
            rows.append({"thr": thr, "n": 0}); continue
        pnl = np.concatenate([pnl_up[take_up], pnl_dn[take_dn]])
        rid = np.concatenate([d["round_id"].to_numpy()[take_up],
                              d["round_id"].to_numpy()[take_dn]])
        b = round_bootstrap(pnl, rid, np.mean, n_boot=800)
        rows.append({"thr": thr, "n": n, "n_rounds": b["n_rounds"],
                     "ev": b["point"], "lcb": b["lcb"], "ucb": b["ucb"]})

    print(f"\n{'edge thr':>9}{'trades':>9}{'rounds':>8}{'net EV/share':>14}"
          f"{'95% LCB':>11}{'95% UCB':>11}  verdict")
    for r in rows:
        if not r["n"]:
            print(f"{r['thr']:>9.2f}{0:>9}{'-':>8}{'-':>14}{'-':>11}{'-':>11}  no trades"); continue
        v = "POSITIVE" if r["lcb"] > 0 else ("marginal" if r["ev"] > 0 else "negative")
        print(f"{r['thr']:>9.2f}{r['n']:>9,}{r['n_rounds']:>8}{r['ev']:>+14.4f}"
              f"{r['lcb']:>+11.4f}{r['ucb']:>+11.4f}  {v}")

    out = {"n_rows": int(len(d)), "n_rounds": int(d.round_id.nunique()),
           "n_days": int(d.day.nunique()), "base_rate_up": float(y.mean()),
           "brier_market": bm, "brier_model": bd, "brier_improvement": bs,
           "by_threshold": rows}
    (LANE / "results.json").write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(f"\nwrote {LANE / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
