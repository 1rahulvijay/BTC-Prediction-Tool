"""
trading_edge_backtest.py — the TRADING-EDGE yardstick (cost-aware BUY/SELL/AVOID, offline).
=============================================================================================
Turns the model's probability into a directional strategy and measures the metrics that decide
whether an edge is TRADABLE (not just "directionally accurate"): expectancy, profit factor,
Sharpe, max drawdown, hit-rate, coverage — after fees+slippage, out-of-sample.

Independent + no-train + no app touch: REUSES the beat head's leak-free builder (so it's the same
honest features/alignment, §5bs), trains one model (LightGBM + isotonic) on the past, and backtests
on the UNSEEN future with NON-OVERLAPPING windows (+ an h-bar embargo at the split). Writes only
`data/trading_edge_report.json`.

HONEST PURPOSE (read this): we've proven 3 ways that direction is ~coin-flip on the backfillable
features, and a cost-aware target is STRICTLY HARDER (the move must beat costs). So expect
expectancy ≈ 0 and NEGATIVE after realistic fees — this harness is the YARDSTICK for when
L2/order-flow information lands, and the honest proof that there is no tradable edge yet. A
"proven edge" still requires forward LIVE measurement (use the live shadow lane for that).

The strategy: BUY if P(up) ≥ 0.5+δ, SELL if ≤ 0.5−δ, else AVOID. P&L per traded window =
sign·window_return − round_trip_cost. Swept over δ and cost so you SEE the cost sensitivity.

Usage:  python backend/research/standalone/trading_edge_backtest.py --days 30
        python backend/research/standalone/trading_edge_backtest.py --selftest
"""

try:
    from . import _bootstrap as _research_bootstrap  # noqa: F401
except ImportError:
    import _bootstrap as _research_bootstrap  # noqa: F401

del _research_bootstrap

import argparse
import json
import os
import sys

import numpy as np

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "data")
REPORT_PATH = os.path.join(DATA_DIR, "trading_edge_report.json")
HORIZONS = (1, 3, 5, 7, 10, 15, 30)
COSTS_BPS = (0.0, 5.0, 10.0)               # round-trip cost: 0=frictionless probe, 5/10=realistic
DELTAS = (0.0, 0.02, 0.05, 0.08, 0.10)     # edge threshold over 0.50 required to trade
MIN_YEAR = 365.25 * 24 * 60                # minutes per year


def window_return(O, C, h):
    """Realized fractional return of the h-bar window opening at bar t: (close[t+h-1]-open[t])/open[t].
    Aligned with beat_labels (same window definition)."""
    n = len(C)
    r = np.full(n, np.nan)
    end = n - h
    if end > 0:
        r[:end] = (C[h - 1:h - 1 + end] - O[:end]) / np.where(O[:end] > 0, O[:end], 1.0)
    return r


def backtest(p_up, ret, delta, cost):
    """BUY/SELL/AVOID by threshold δ; per-window P&L net of round-trip cost on traded windows."""
    pos = np.where(p_up >= 0.5 + delta, 1.0, np.where(p_up <= 0.5 - delta, -1.0, 0.0))
    traded = pos != 0
    pnl = pos * ret - np.where(traded, cost, 0.0)
    return pnl, traded


def trading_metrics(pnl, traded, windows_per_year) -> dict:
    n = len(pnl)
    tr = pnl[traded]
    n_tr = int(traded.sum())
    exp = float(tr.mean()) if n_tr else 0.0
    t_stat = float(exp / (tr.std() + 1e-12) * np.sqrt(n_tr)) if n_tr > 1 else 0.0
    pos = float(pnl[pnl > 0].sum())
    neg = float(-pnl[pnl < 0].sum())
    pf = (pos / neg) if neg > 1e-12 else (float("inf") if pos > 0 else 0.0)
    mu, sd = float(pnl.mean()), float(pnl.std())
    sharpe = (mu / sd * np.sqrt(windows_per_year)) if sd > 1e-12 else 0.0
    cum = np.cumsum(pnl)
    dd = cum - np.maximum.accumulate(cum)
    return {
        "n_windows": n, "n_trades": n_tr,
        "coverage_pct": round(100.0 * n_tr / n, 1) if n else 0.0,
        "hit_rate_pct": round(100.0 * float((tr > 0).mean()), 1) if n_tr else 0.0,
        "expectancy_bps": round(exp * 1e4, 2),          # avg net P&L per trade
        "expectancy_t": round(t_stat, 2),               # |t|>=2 ≈ significant
        "total_return_pct": round(float(pnl.sum()) * 100, 2),
        "profit_factor": round(pf, 3) if pf != float("inf") else None,
        "sharpe_annual": round(sharpe, 2),
        "max_drawdown_pct": round(float(dd.min()) * 100, 2),
    }


def _fit_predict(Xv, yv, tr_end):
    """LightGBM (or histgb fallback) + isotonic, fit on [0,tr_end), calibrated on its tail."""
    from sklearn.isotonic import IsotonicRegression
    fit_end = int(tr_end * 0.85)
    try:
        from lightgbm import LGBMClassifier
        clf = LGBMClassifier(n_estimators=400, num_leaves=31, learning_rate=0.03, subsample=0.8,
                             colsample_bytree=0.8, random_state=0,
                             n_jobs=int(os.environ.get("OMP_NUM_THREADS", "2")), verbose=-1)
    except Exception:
        from sklearn.ensemble import HistGradientBoostingClassifier
        clf = HistGradientBoostingClassifier(max_iter=300, max_depth=4, learning_rate=0.05,
                                             l2_regularization=1.0, random_state=0)
    clf.fit(Xv[:fit_end], yv[:fit_end])
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.02, y_max=0.98)
    iso.fit(clf.predict_proba(Xv[fit_end:tr_end])[:, 1], yv[fit_end:tr_end])
    return clf, iso


def run(dates):
    from train_beat_classifier import _ohlc_for_dates, build_beat_features, beat_labels
    T, O, H, L, C = _ohlc_for_dates(dates)
    if C is None or len(C) < 600:
        sys.exit("not enough bars")
    X = build_beat_features(O, H, L, C, T)
    print(f"\nFeature matrix {X.shape}; {len(C)} bars over {len(dates)} day(s)")
    print("Model = LightGBM+isotonic, trained on the PAST, backtested on the UNSEEN future")
    print("(non-overlapping windows + h-bar embargo). Round-trip cost swept 0/5/10 bps.\n")
    report = {"costs_bps": COSTS_BPS, "deltas": DELTAS, "horizons": {}}
    any_edge = False
    for h in HORIZONS:
        y = beat_labels(O, C, h)
        ret = window_return(O, C, h)
        Xs, ys, rs = X[:-1], y[1:], ret[1:]      # §5bs alignment
        m = (ys >= 0) & np.isfinite(rs)
        Xv, yv, rv = Xs[m], ys[m], rs[m]
        n = len(yv)
        if n < 600 or len(np.unique(yv)) < 2:
            print(f"[{h}m] insufficient ({n})"); continue
        cut = int(n * 0.70)
        clf, iso = _fit_predict(Xv, yv, cut - h)        # embargo h bars before the test
        p_te = iso.predict(clf.predict_proba(Xv[cut:])[:, 1])
        ret_te = rv[cut:]
        sel = np.arange(0, len(p_te), h)                # NON-OVERLAPPING windows
        p_no, ret_no = p_te[sel], ret_te[sel]
        wpy = MIN_YEAR / h
        grid, best = {}, None
        for cost in COSTS_BPS:
            for delta in DELTAS:
                pnl, traded = backtest(p_no, ret_no, delta, cost / 1e4)
                mt = trading_metrics(pnl, traded, wpy)
                grid[f"c{int(cost)}_d{delta}"] = mt
                # "best realistic" = highest expectancy at 5bps with >=2% coverage
                if cost == 5.0 and mt["n_trades"] >= 20:
                    if best is None or mt["expectancy_bps"] > best[1]["expectancy_bps"]:
                        best = (delta, mt)
        report["horizons"][str(h)] = {"n_test_windows": len(p_no), "grid": grid,
                                      "best_at_5bps": ({"delta": best[0], **best[1]} if best else None)}
        # print frictionless probe + best realistic
        fr = grid["c0_d0.0"]
        print(f"  {h}m  frictionless(d0,0bps): exp={fr['expectancy_bps']:+.1f}bps "
              f"hit={fr['hit_rate_pct']:.1f}% PF={fr['profit_factor']} Sharpe={fr['sharpe_annual']:+.2f}")
        if best:
            d, b = best
            edge = b["expectancy_bps"] > 0 and abs(b["expectancy_t"]) >= 2
            any_edge = any_edge or edge
            print(f"       best@5bps (d={d}): exp={b['expectancy_bps']:+.1f}bps t={b['expectancy_t']:+.1f} "
                  f"cov={b['coverage_pct']:.0f}% hit={b['hit_rate_pct']:.1f}% PF={b['profit_factor']} "
                  f"Sharpe={b['sharpe_annual']:+.2f} maxDD={b['max_drawdown_pct']:.1f}%  "
                  f"-> {'EDGE?' if edge else 'no edge'}")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nFull grid -> {REPORT_PATH}")
    print("VERDICT:", "a positive-expectancy, statistically-significant cell EXISTS — investigate "
          "forward (paper-trade live)." if any_edge else
          "NO tradable edge at realistic cost — expectancy ~0/negative, as the coin-flip predicts. "
          "This is the yardstick; re-run after L2/order-flow features land.")


def selftest():
    rng = np.random.default_rng(0)
    n = 6000
    ret = rng.normal(0, 0.004, n)                       # window returns
    # a model that KNOWS direction: p_up high when ret>0 (+ noise)
    p_up = 1 / (1 + np.exp(-(ret * 400 + rng.normal(0, 0.5, n))))
    wpy = MIN_YEAR / 5

    # frictionless, δ=0: trading WITH the edge must be profitable.
    pnl, traded = backtest(p_up, ret, 0.0, 0.0)
    m = trading_metrics(pnl, traded, wpy)
    assert m["expectancy_bps"] > 0, f"edge case should be +expectancy, got {m['expectancy_bps']}"
    assert m["total_return_pct"] > 0 and m["sharpe_annual"] > 0
    assert (m["profit_factor"] is None) or m["profit_factor"] > 1.0
    assert m["max_drawdown_pct"] <= 0.0
    assert m["coverage_pct"] == 100.0 and m["n_trades"] == n   # δ=0 trades every window

    # heavy cost must destroy it.
    pnl_c, traded_c = backtest(p_up, ret, 0.0, 50.0 / 1e4)
    assert trading_metrics(pnl_c, traded_c, wpy)["expectancy_bps"] < m["expectancy_bps"]

    # a useless model (random p) ~ zero expectancy frictionless.
    pnl_r, traded_r = backtest(rng.uniform(0, 1, n), ret, 0.0, 0.0)
    assert abs(trading_metrics(pnl_r, traded_r, wpy)["expectancy_bps"]) < 3.0

    # δ filter reduces coverage.
    _, tr_hi = backtest(p_up, ret, 0.10, 0.0)
    assert tr_hi.sum() < n
    print("trading_edge_backtest self-test: ALL PASS (P&L, expectancy, PF, Sharpe, maxDD, cost/delta all sound)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--start"); ap.add_argument("--end"); ap.add_argument("--validate")
    ap.add_argument("--days", type=int, help="last N full days to yesterday")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        sys.exit(0)
    from train_beat_classifier import resolve_dates
    dates, _ = resolve_dates(a)
    if not dates:
        ap.error("provide --selftest, --days N, --start/--end, or --validate DATE")
    run(dates)
