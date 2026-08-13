"""Five more lanes: three Polymarket, two Binance. Run together, appended to the reports.

  STATE_VALUE_ATLAS_V1               where does edge exist, without fitting anything?
  MARKET_DISAGREEMENT_RESOLUTION_V1  when model and market disagree, who is right?
  POLY_STALE_QUOTE_V1                is a stale book exploitable?
  MFE_MAE_DISTRIBUTION_V1            how far does price run, and how far against you first?
  IMPACT_ASYMMETRY_V1                does equal flow move price equally up and down?

    python research_lanes/run_remaining_lanes.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

LANES = Path(__file__).resolve().parent
sys.path.insert(0, str(LANES))
from common.pm_data import load_official, round_bootstrap  # noqa: E402
from common.scorecard import day_block_bootstrap, day_index  # noqa: E402

REPO = LANES.parent
TAKER_COEF = 0.07


def _fee(p):
    p = np.asarray(p, float)
    return TAKER_COEF * p * (1.0 - p)


# ------------------------------------------------------------------ 1. state value atlas
def state_value_atlas(d) -> dict:
    """Partition states, then just COUNT. No model, so nothing to overfit.

    A cell is interesting only if realized UP frequency differs from the market's own price by
    more than the fee — and only if it has enough INDEPENDENT ROUNDS to mean anything.
    """
    x = d.copy()
    x["p_market"] = (x["up_bid"] + x["up_ask"]) / 2.0
    x["b_time"] = pd.cut(x["seconds_left"], [0, 60, 120, 300, 600, 1e9],
                         labels=["<60s", "60-120s", "2-5m", "5-10m", ">10m"])
    x["b_dist"] = pd.cut(x["distance_bps"], [-1e9, -15, -5, 5, 15, 1e9],
                         labels=["<<-15", "-15..-5", "flat", "5..15", ">>15"])
    x["b_px"] = pd.cut(x["p_market"], [0, .35, .45, .55, .65, 1],
                       labels=["<35c", "35-45c", "45-55c", "55-65c", ">65c"])
    g = x.groupby(["b_time", "b_dist", "b_px"], observed=True)
    cells = []
    for key, sub in g:
        n_rounds = sub["round_id"].nunique()
        if n_rounds < 30:                       # too few independent outcomes to read
            continue
        realized = float(sub["settled_up"].mean())
        market = float(sub["p_market"].mean())
        gap = realized - market
        b = round_bootstrap(sub["settled_up"].to_numpy(float) - sub["p_market"].to_numpy(float),
                            sub["round_id"].to_numpy(), np.mean, n_boot=400)
        cells.append({"cell": " | ".join(map(str, key)), "n_rows": len(sub),
                      "n_rounds": int(n_rounds), "realized": realized, "market": market,
                      "gap": gap, "lcb": b["lcb"], "ucb": b["ucb"],
                      "beats_fee": bool(abs(b["lcb"]) > 0.02 and np.sign(b["lcb"]) == np.sign(b["ucb"]))})
    cells.sort(key=lambda c: -abs(c["gap"]))
    return {"n_cells_examined": len(cells),
            "n_cells_significant": sum(c["beats_fee"] for c in cells),
            "top": cells[:8]}


# ------------------------------------------------- 2. market disagreement resolution
def disagreement_resolution(d) -> dict:
    """When the model and the market disagree, who turns out right?"""
    p_model = d["p_hold_up"].to_numpy(float)
    p_mkt = ((d["up_bid"] + d["up_ask"]) / 2.0).to_numpy(float)
    y = d["settled_up"].to_numpy(float)
    resid = p_model - p_mkt
    rid = d["round_id"].to_numpy()
    out = []
    for lo, hi in [(0.02, 0.05), (0.05, 0.08), (0.08, 0.12), (0.12, 0.20), (0.20, 1.0)]:
        m = np.abs(resid) >= lo
        m &= np.abs(resid) < hi
        if m.sum() < 200:
            continue
        # Who is closer to the truth in this band?
        model_wins = (np.abs(p_model[m] - y[m]) < np.abs(p_mkt[m] - y[m])).astype(float)
        b = round_bootstrap(model_wins, rid[m], np.mean, n_boot=400)
        out.append({"band": f"{lo:.2f}-{hi:.2f}", "n": int(m.sum()), "n_rounds": b["n_rounds"],
                    "model_win_rate": b["point"], "lcb": b["lcb"], "ucb": b["ucb"]})
    return {"bands": out}


# ------------------------------------------------------------------ 3. stale quote
def stale_quote(d) -> dict:
    """Is an older book systematically mispriced relative to settlement?"""
    x = d[d["book_age_s"].between(-1, 300)].copy()
    x["p_market"] = (x["up_bid"] + x["up_ask"]) / 2.0
    x["err"] = (x["p_market"] - x["settled_up"]).abs()
    x["b_age"] = pd.cut(x["book_age_s"], [-1, 0.1, 0.5, 2, 10, 300],
                        labels=["<0.1s", "0.1-0.5s", "0.5-2s", "2-10s", ">10s"])
    rows = []
    for key, sub in x.groupby("b_age", observed=True):
        if sub["round_id"].nunique() < 20:
            continue
        b = round_bootstrap(sub["err"].to_numpy(float), sub["round_id"].to_numpy(),
                            np.mean, n_boot=300)
        rows.append({"age": str(key), "n": len(sub), "n_rounds": b["n_rounds"],
                     "mean_abs_err": b["point"], "lcb": b["lcb"], "ucb": b["ucb"]})
    return {"by_age": rows}


# ------------------------------------------------------------------ 4. MFE / MAE
def mfe_mae() -> dict:
    """How far does price run in your favour, and how far against you first?"""
    df = pd.read_parquet(REPO / "data" / "research_matrix_1m.parquet",
                         columns=["ts_ms", "close", "future_high_5m", "future_low_5m",
                                  "future_close_5m"]).dropna()
    c = df["close"].to_numpy(float)
    mfe_long = (df["future_high_5m"].to_numpy(float) - c) / c * 1e4
    mae_long = (df["future_low_5m"].to_numpy(float) - c) / c * 1e4      # negative
    days = day_index(df["ts_ms"].to_numpy("int64"))
    b_mfe = day_block_bootstrap(mfe_long, days, np.mean, n_boot=300)
    b_mae = day_block_bootstrap(-mae_long, days, np.mean, n_boot=300)
    q = {f"mfe_q{int(p*100)}": float(np.percentile(mfe_long, p * 100)) for p in (.25, .5, .75, .9)}
    q |= {f"mae_q{int(p*100)}": float(np.percentile(-mae_long, p * 100)) for p in (.25, .5, .75, .9)}
    # For a long with a 12bps target and a 12bps stop, which is hit "first" cannot be resolved
    # from bar extremes - only whether each was reached at all. Report that honestly.
    hit_t = float((mfe_long >= 12).mean())
    hit_s = float((-mae_long >= 12).mean())
    return {"mean_mfe_bps": b_mfe["point"], "mfe_lcb": b_mfe["lcb"],
            "mean_mae_bps": b_mae["point"], "mae_lcb": b_mae["lcb"],
            "pct_touch_+12bps": hit_t, "pct_touch_-12bps": hit_s,
            "pct_touch_both": float(((mfe_long >= 12) & (-mae_long >= 12)).mean()),
            **q, "n_days": int(len(np.unique(days)))}


# ------------------------------------------------------------------ 5. impact asymmetry
def impact_asymmetry() -> dict:
    """Does equal aggressive volume move price equally in each direction?"""
    df = pd.read_parquet(REPO / "data" / "research_matrix_1m.parquet",
                         columns=["ts_ms", "close", "taker_buy", "taker_sell"]).dropna()
    c = df["close"].to_numpy(float)
    ret = np.full(len(c), np.nan)
    ret[:-1] = (c[1:] - c[:-1]) / c[:-1] * 1e4
    tb, ts_ = df["taker_buy"].to_numpy(float), df["taker_sell"].to_numpy(float)
    days = day_index(df["ts_ms"].to_numpy("int64"))
    ok = np.isfinite(ret) & (tb > 0) & (ts_ > 0)
    # bps of move per unit of net aggressive volume, measured separately by sign of imbalance
    imb = (tb - ts_) / (tb + ts_)
    buy_heavy = ok & (imb > np.nanpercentile(imb[ok], 90))
    sell_heavy = ok & (imb < np.nanpercentile(imb[ok], 10))
    bb = day_block_bootstrap(ret[buy_heavy], days[buy_heavy], np.mean, n_boot=300)
    bs = day_block_bootstrap(-ret[sell_heavy], days[sell_heavy], np.mean, n_boot=300)
    return {"buy_heavy_move_bps": bb["point"], "buy_lcb": bb["lcb"], "n_buy": bb["n_rows"],
            "sell_heavy_move_bps": bs["point"], "sell_lcb": bs["lcb"], "n_sell": bs["n_rows"],
            "asymmetry_bps": bb["point"] - bs["point"]}


def main() -> int:
    d = load_official()
    res = {}
    print(f"PM rows={len(d):,} rounds={d.round_id.nunique():,} days={d.day.nunique()}\n")

    print("[1/5] STATE_VALUE_ATLAS_V1"); res["atlas"] = state_value_atlas(d)
    a = res["atlas"]
    print(f"  cells with >=30 rounds: {a['n_cells_examined']}   significant beyond fee: "
          f"{a['n_cells_significant']}")
    for c in a["top"][:6]:
        print(f"    {c['cell']:<34} rounds={c['n_rounds']:>4} realized={c['realized']:.3f} "
              f"market={c['market']:.3f} gap={c['gap']:+.3f} [{c['lcb']:+.3f},{c['ucb']:+.3f}]")

    print("\n[2/5] MARKET_DISAGREEMENT_RESOLUTION_V1"); res["disagree"] = disagreement_resolution(d)
    for r in res["disagree"]["bands"]:
        print(f"    |resid| {r['band']:<10} n={r['n']:>7,} rounds={r['n_rounds']:>4} "
              f"model wins {r['model_win_rate']:.3f} [{r['lcb']:.3f},{r['ucb']:.3f}]")

    print("\n[3/5] POLY_STALE_QUOTE_V1"); res["stale"] = stale_quote(d)
    for r in res["stale"]["by_age"]:
        print(f"    age {r['age']:<10} n={r['n']:>7,} rounds={r['n_rounds']:>4} "
              f"mean |mkt-outcome| {r['mean_abs_err']:.4f} [{r['lcb']:.4f},{r['ucb']:.4f}]")

    print("\n[4/5] MFE_MAE_DISTRIBUTION_V1"); res["mfe_mae"] = mfe_mae()
    m = res["mfe_mae"]
    print(f"    mean MFE {m['mean_mfe_bps']:.2f} bps (LCB {m['mfe_lcb']:.2f}) | "
          f"mean MAE {m['mean_mae_bps']:.2f} (LCB {m['mae_lcb']:.2f})")
    print(f"    touch +12bps {m['pct_touch_+12bps']:.1%} | touch -12bps {m['pct_touch_-12bps']:.1%} "
          f"| touch BOTH {m['pct_touch_both']:.1%}")

    print("\n[5/5] IMPACT_ASYMMETRY_V1"); res["impact"] = impact_asymmetry()
    i = res["impact"]
    print(f"    buy-heavy move {i['buy_heavy_move_bps']:+.3f} bps (LCB {i['buy_lcb']:+.3f}) | "
          f"sell-heavy {i['sell_heavy_move_bps']:+.3f} (LCB {i['sell_lcb']:+.3f}) | "
          f"asymmetry {i['asymmetry_bps']:+.3f}")

    (LANES / "remaining_lanes_results.json").write_text(
        json.dumps(res, indent=2, default=float), encoding="utf-8")
    print(f"\nwrote {LANES / 'remaining_lanes_results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
