"""Batch 5 - every remaining lane that has enough independent units to run today.

WHY THESE FIVE, AND WHERE EACH RUNS
    Two datasets, very different power:

        research_matrix_1m.parquet   43,200 bars / 30 UTC days   <- best available
        pm_round_snapshots            177,911 obs / 10 UTC days

    PATH_ASYMMETRY, COMPETING_RISKS and REGIME_EXIT_HAZARD are all path questions about BTC, so
    they run on the 1m matrix and get 30 day-blocks. Running them on Polymarket snapshots - the
    obvious choice, since the trade settles there - would have thrown away two thirds of the
    independence units to answer the same question about the same underlying.

    NEXT_ROUND_OPENING and EXIT_EDGE_DECAY are questions about Polymarket's own quotes, so they
    have no choice but the 10-day sample, and their bounds are correspondingly wide.

DISCIPLINE CARRIED FROM BATCHES 1-4
    1. Bootstrap resamples whole UTC days, never rows.
    2. Accuracy is compared to the COST hurdle, never to 50%.
    3. Any lane that sweeps thresholds reports a family-wise p-value from a max-statistic
       permutation, because the best of N sweeps is not a pre-declared test.
    4. Entry rules must be nameable as causal decisions; no idxmin/idxmax over a future window.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import numpy as np

LANE = Path(__file__).resolve().parent
sys.path.insert(0, str(LANE))

from common.scorecard import day_block_bootstrap, day_index, md_table  # noqa: E402

REPO = LANE.parent
MATRIX = REPO / "data" / "research_matrix_1m.parquet"
EXEC_DB = REPO / "data" / "execution_layer.duckdb"

BINANCE_ROUND_TRIP_BPS = 12.0     # established in batch 1; taker in + taker out
PM_FEE_RATE = 0.07
PM_SPREAD = 0.0121


def load_matrix():
    con = duckdb.connect()
    df = con.execute(
        f"SELECT ts_ms, open, high, low, close, rv_15m, rv_60m, vpin_15m, "
        f"compression_ratio, cvd_5m, shock_magnitude "
        f"FROM '{MATRIX.as_posix()}' ORDER BY ts_ms"
    ).fetchdf()
    con.close()
    return df


def max_stat_permutation(values: np.ndarray, days: np.ndarray, groups: list[np.ndarray],
                         observed_best: float, n_perm: int = 1000, seed: int = 0) -> dict:
    """Family-wise p-value: shuffle outcomes within day, re-score the whole sweep, take the max.

    Preserves each day's base rate and size; destroys only the association each rule relies on.
    """
    rng = np.random.default_rng(seed)
    null_best = np.empty(n_perm)
    uniq = np.unique(days)
    for i in range(n_perm):
        shuffled = values.copy()
        for d in uniq:
            mask = days == d
            shuffled[mask] = rng.permutation(values[mask])
        best = -np.inf
        for sel in groups:
            if sel.sum() >= 50:
                best = max(best, float(shuffled[sel].mean()))
        null_best[i] = best
    return {
        "observed_best": round(float(observed_best), 4),
        "null_best_median": round(float(np.median(null_best)), 4),
        "null_best_p95": round(float(np.percentile(null_best, 95)), 4),
        "p_value_family_wise": round(float((null_best >= observed_best).mean()), 4),
        "survives": bool((null_best >= observed_best).mean() < 0.05),
    }


# =============================================================================================
# PATH_ASYMMETRY_V1
# =============================================================================================

def lane_path_asymmetry(df) -> dict:
    """Is favourable excursion ever larger than adverse excursion, conditional on causal state?

    A prior batch found MFE 7.97 vs MAE 7.99 bps unconditionally - symmetric to within noise.
    The open question is whether any state SPLITS that symmetry. If none does, the asymmetric-
    payoff route to profitability is closed, not merely unproven.
    """
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    ts = df["ts_ms"].to_numpy("int64")
    out = {"lane": "PATH_ASYMMETRY_V1", "horizons": {}}

    for horizon in (5, 15):
        n = len(close) - horizon
        if n < 1000:
            continue
        idx = np.arange(n)
        fwd_hi = np.array([high[i + 1:i + 1 + horizon].max() for i in idx])
        fwd_lo = np.array([low[i + 1:i + 1 + horizon].min() for i in idx])
        base = close[idx]
        mfe = (fwd_hi - base) / base * 10_000.0
        mae = (base - fwd_lo) / base * 10_000.0
        days = day_index(ts[idx])

        rows = [{
            "state": "unconditional",
            "n": int(n),
            "mfe_bps": round(float(mfe.mean()), 3),
            "mae_bps": round(float(mae.mean()), 3),
            "ratio": round(float(mfe.mean() / mae.mean()), 4),
            "net_bps": round(float((mfe - mae).mean()), 4),
            "net_lcb95": round(day_block_bootstrap(mfe - mae, days)["lcb"], 4),
        }]
        groups = []
        for col in ("rv_15m", "vpin_15m", "compression_ratio", "cvd_5m", "shock_magnitude"):
            values = df[col].to_numpy(float)[idx]
            ok = np.isfinite(values)
            if ok.sum() < 1000:
                continue
            for label, lo, hi in (("low tercile", 0, 33), ("high tercile", 67, 100)):
                a, b = np.percentile(values[ok], [lo, hi])
                sel = ok & (values >= a) & (values <= b) if lo == 0 else ok & (values >= a)
                if sel.sum() < 500 or np.unique(days[sel]).size < 10:
                    continue
                groups.append(sel)
                net = mfe[sel] - mae[sel]
                boot = day_block_bootstrap(net, days[sel])
                rows.append({
                    "state": f"{col} {label}",
                    "n": int(sel.sum()),
                    "mfe_bps": round(float(mfe[sel].mean()), 3),
                    "mae_bps": round(float(mae[sel].mean()), 3),
                    "ratio": round(float(mfe[sel].mean() / mae[sel].mean()), 4),
                    "net_bps": round(boot["point"], 4),
                    "net_lcb95": round(boot["lcb"], 4),
                })
        # The economically relevant question is whether net excursion clears the round trip.
        best = max((r["net_bps"] for r in rows[1:]), default=float("nan"))
        perm = max_stat_permutation(mfe - mae, days, groups, best) if groups else {}
        out["horizons"][f"{horizon}m"] = {
            "utc_days": int(np.unique(days).size),
            "hurdle_bps": BINANCE_ROUND_TRIP_BPS,
            "rows": rows,
            "states_clearing_hurdle": [r["state"] for r in rows
                                       if r["net_lcb95"] > BINANCE_ROUND_TRIP_BPS],
            "multiplicity": perm,
        }
    return out


# =============================================================================================
# COMPETING_RISKS_V1
# =============================================================================================

def lane_competing_risks(df) -> dict:
    """P(TP first), P(SL first), P(neither) for a swept TP/SL grid, with net EV after costs.

    This is the trade-geometry question: a direction probability is not a payoff. The sweep is
    wide on purpose, so the family-wise correction is mandatory rather than optional.
    """
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    ts = df["ts_ms"].to_numpy("int64")
    horizon = 15
    n = len(close) - horizon
    idx = np.arange(n)
    days = day_index(ts[idx])
    rows, groups, evs = [], [], []

    for tp in (5, 10, 20, 30):
        for sl in (5, 10, 20, 30):
            hit_tp = np.zeros(n, dtype=bool)
            hit_sl = np.zeros(n, dtype=bool)
            for i in idx:
                base = close[i]
                up_lvl = base * (1 + tp / 10_000.0)
                dn_lvl = base * (1 - sl / 10_000.0)
                window_hi = high[i + 1:i + 1 + horizon]
                window_lo = low[i + 1:i + 1 + horizon]
                t_up = np.argmax(window_hi >= up_lvl) if (window_hi >= up_lvl).any() else 10**6
                t_dn = np.argmax(window_lo <= dn_lvl) if (window_lo <= dn_lvl).any() else 10**6
                hit_tp[i] = t_up < t_dn
                hit_sl[i] = t_dn < t_up
            # Gross payoff of the long: +tp if TP first, -sl if SL first, else mark to close.
            neither = ~(hit_tp | hit_sl)
            payoff = np.where(hit_tp, tp, np.where(hit_sl, -sl, 0.0))
            payoff[neither] = ((close[idx + horizon][neither] - close[idx][neither])
                               / close[idx][neither] * 10_000.0)
            net = payoff - BINANCE_ROUND_TRIP_BPS
            boot = day_block_bootstrap(net, days)
            rows.append({
                "tp_bps": tp, "sl_bps": sl,
                "p_tp_first": round(float(hit_tp.mean()), 4),
                "p_sl_first": round(float(hit_sl.mean()), 4),
                "p_neither": round(float(neither.mean()), 4),
                "gross_bps": round(float(payoff.mean()), 4),
                "net_bps": round(boot["point"], 4),
                "net_lcb95": round(boot["lcb"], 4),
            })
            groups.append(np.ones(n, dtype=bool))
            evs.append(boot["point"])

    best = max(evs)
    return {
        "lane": "COMPETING_RISKS_V1",
        "horizon_min": horizon,
        "utc_days": int(np.unique(days).size),
        "cost_bps": BINANCE_ROUND_TRIP_BPS,
        "grid": rows,
        "combos_with_positive_lcb": [f"tp{r['tp_bps']}/sl{r['sl_bps']}"
                                     for r in rows if r["net_lcb95"] > 0],
        "note": "long-only geometry with no directional signal; measures whether any TP/SL "
                "shape is profitable on its own before any model is applied",
        "best_net_bps": round(float(best), 4),
    }


# =============================================================================================
# REGIME_EXIT_HAZARD_V1
# =============================================================================================

def lane_regime_exit_hazard(df) -> dict:
    """Is the END of the current volatility regime predictable, and is knowing it worth anything?

    Two separate questions, answered separately, because a predictable regime exit that does not
    change any trade's EV is a statistic, not an edge.
    """
    ts = df["ts_ms"].to_numpy("int64")
    close = df["close"].to_numpy(float)
    rv = df["rv_15m"].to_numpy(float)
    ok = np.isfinite(rv)
    lo, hi = np.nanpercentile(rv[ok], [33, 67])
    regime = np.where(rv <= lo, 0, np.where(rv >= hi, 2, 1))    # 0 calm, 1 mid, 2 stressed

    out = {"lane": "REGIME_EXIT_HAZARD_V1", "windows": {}}
    for ahead in (5, 15, 30):
        n = len(regime) - ahead
        idx = np.arange(n)
        changed = (regime[idx + ahead] != regime[idx]).astype(float)
        days = day_index(ts[idx])
        base = day_block_bootstrap(changed, days)

        # Causal predictor: is the regime YOUNG or OLD right now? Age is known at decision time.
        age = np.ones(len(regime), dtype=int)
        for i in range(1, len(regime)):
            age[i] = age[i - 1] + 1 if regime[i] == regime[i - 1] else 1
        cur_age = age[idx]
        rows = [{"condition": "unconditional", "n": int(n),
                 "p_regime_changes": round(base["point"], 4),
                 "lcb95": round(base["lcb"], 4), "ucb95": round(base["ucb"], 4)}]
        groups = []
        for label, sel in (("regime age <= 5m", cur_age <= 5),
                           ("regime age 6-30m", (cur_age > 5) & (cur_age <= 30)),
                           ("regime age > 30m", cur_age > 30)):
            if sel.sum() < 500 or np.unique(days[sel]).size < 10:
                continue
            groups.append(sel)
            boot = day_block_bootstrap(changed[sel], days[sel])
            rows.append({"condition": label, "n": int(sel.sum()),
                         "p_regime_changes": round(boot["point"], 4),
                         "lcb95": round(boot["lcb"], 4), "ucb95": round(boot["ucb"], 4)})
        spread = (max(r["p_regime_changes"] for r in rows[1:]) -
                  min(r["p_regime_changes"] for r in rows[1:])) if len(rows) > 1 else 0.0

        # ECONOMIC LEG: does gating a trade on "regime unlikely to change" beat not gating?
        fwd = np.full(n, np.nan)
        fwd = (close[idx + ahead] - close[idx]) / close[idx] * 10_000.0
        gate = cur_age > 30 if (cur_age > 30).sum() > 500 else None
        econ = {}
        if gate is not None:
            gated = day_block_bootstrap(np.abs(fwd[gate]) - BINANCE_ROUND_TRIP_BPS, days[gate])
            ungated = day_block_bootstrap(np.abs(fwd) - BINANCE_ROUND_TRIP_BPS, days)
            econ = {
                "gated_abs_move_minus_cost_bps": round(gated["point"], 4),
                "gated_lcb95": round(gated["lcb"], 4),
                "ungated_abs_move_minus_cost_bps": round(ungated["point"], 4),
                "ungated_lcb95": round(ungated["lcb"], 4),
                "note": "|move| minus round trip is the ORACLE bound - a perfect direction "
                        "model's ceiling. Negative means no model can profit in this state.",
            }
        out["windows"][f"{ahead}m"] = {
            "utc_days": int(np.unique(days).size),
            "rows": rows,
            "hazard_spread_across_ages": round(float(spread), 4),
            "economics": econ,
        }
    return out


# =============================================================================================
# NEXT_ROUND_OPENING_V1 and EXIT_EDGE_DECAY_V1  (Polymarket, 10 days)
# =============================================================================================

def lane_pm_opening_and_exit() -> dict:
    con = duckdb.connect(str(EXEC_DB), read_only=True)
    df = con.execute(
        """
        SELECT s.ts, s.slug, s.horizon, s.seconds_elapsed, s.seconds_left,
               s.up_ask, s.down_ask, s.up_bid, s.down_bid, t.settled_side
        FROM pm_round_snapshots s JOIN pm_round_settlements t USING (slug)
        WHERE s.up_ask IS NOT NULL AND s.down_ask IS NOT NULL
          AND s.up_ask > 0 AND s.up_ask < 1 AND s.down_ask > 0 AND s.down_ask < 1
          AND t.settled_side IN (0, 1)
        ORDER BY s.slug, s.ts
        """
    ).fetchdf()
    con.close()

    fee = lambda p: PM_FEE_RATE * p * (1 - p)          # noqa: E731
    up_win = df["settled_side"].to_numpy(float)
    up_ask = df["up_ask"].to_numpy(float)
    dn_ask = df["down_ask"].to_numpy(float)
    elapsed = df["seconds_elapsed"].to_numpy(float)
    days = day_index((df["ts"].to_numpy(float) * 1000).astype("int64"))

    buy_up = (up_win - up_ask - fee(up_ask)) * 100.0
    buy_dn = ((1 - up_win) - dn_ask - fee(dn_ask)) * 100.0

    # NEXT_ROUND_OPENING: is the market mispriced in the first seconds of a round?
    opening = []
    for lo, hi in ((0, 5), (5, 10), (10, 30), (30, 60), (60, 120)):
        sel = (elapsed >= lo) & (elapsed < hi)
        if sel.sum() < 200 or np.unique(days[sel]).size < 5:
            continue
        for name, values in (("BUY_UP", buy_up), ("BUY_DOWN", buy_dn)):
            boot = day_block_bootstrap(values[sel], days[sel])
            opening.append({
                "window": f"{lo}-{hi}s after open", "action": name,
                "n": int(sel.sum()), "n_days": boot["n_days"],
                "mean_cents": round(boot["point"], 4),
                "lcb95": round(boot["lcb"], 4), "ucb95": round(boot["ucb"], 4),
            })

    # EXIT_EDGE_DECAY: hold to settlement vs sell at the bid now, for a position already open.
    decay = []
    for lo, hi in ((0, 30), (30, 60), (60, 120), (120, 300)):
        sel = (df["seconds_left"].to_numpy(float) >= lo) & (df["seconds_left"].to_numpy(float) < hi)
        if sel.sum() < 200 or np.unique(days[sel]).size < 5:
            continue
        hold_up = (up_win[sel] - up_ask[sel] - fee(up_ask[sel])) * 100.0
        exit_up = (df["up_bid"].to_numpy(float)[sel] - up_ask[sel] - fee(up_ask[sel])) * 100.0
        diff = day_block_bootstrap(hold_up - exit_up, days[sel])
        decay.append({
            "seconds_left": f"{lo}-{hi}s", "n": int(sel.sum()), "n_days": diff["n_days"],
            "hold_minus_exit_cents": round(diff["point"], 4),
            "lcb95": round(diff["lcb"], 4), "ucb95": round(diff["ucb"], 4),
        })

    return {
        "NEXT_ROUND_OPENING_V1": {
            "rows": opening,
            "positive_lcb": [f"{r['window']} {r['action']}" for r in opening if r["lcb95"] > 0],
        },
        "EXIT_EDGE_DECAY_V1": {
            "rows": decay,
            "note": "positive means holding to settlement beat selling at the bid; it is a "
                    "comparison of two exits, not evidence that entering was profitable",
        },
    }


def main() -> int:
    print("BATCH 5 - remaining runnable lanes\n")
    df = load_matrix()
    results = {}

    for name, fn in (("PATH_ASYMMETRY_V1", lambda: lane_path_asymmetry(df)),
                     ("COMPETING_RISKS_V1", lambda: lane_competing_risks(df)),
                     ("REGIME_EXIT_HAZARD_V1", lambda: lane_regime_exit_hazard(df))):
        print(f"running {name} ...")
        results[name] = fn()

    print("running NEXT_ROUND_OPENING_V1 + EXIT_EDGE_DECAY_V1 ...")
    results.update(lane_pm_opening_and_exit())

    (LANE / "batch5_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n=== PATH_ASYMMETRY_V1 (1m matrix, 30 UTC days) ===")
    for horizon, block in results["PATH_ASYMMETRY_V1"]["horizons"].items():
        print(f"\n{horizon} (hurdle {block['hurdle_bps']} bps round trip)")
        print(md_table(block["rows"], ["state", "n", "mfe_bps", "mae_bps", "ratio",
                                       "net_bps", "net_lcb95"]))
        print(f"  states clearing hurdle: {block['states_clearing_hurdle'] or 'none'}")
        if block.get("multiplicity"):
            m = block["multiplicity"]
            print(f"  family-wise p={m['p_value_family_wise']} -> "
                  f"{'survives' if m['survives'] else 'does not survive'}")

    cr = results["COMPETING_RISKS_V1"]
    print(f"\n=== COMPETING_RISKS_V1 ({cr['utc_days']} UTC days, {cr['horizon_min']}m, "
          f"cost {cr['cost_bps']} bps) ===")
    print(md_table(cr["grid"], ["tp_bps", "sl_bps", "p_tp_first", "p_sl_first", "p_neither",
                                "gross_bps", "net_bps", "net_lcb95"]))
    print(f"  TP/SL combos with positive lower bound: {cr['combos_with_positive_lcb'] or 'none'}")

    print("\n=== REGIME_EXIT_HAZARD_V1 ===")
    for window, block in results["REGIME_EXIT_HAZARD_V1"]["windows"].items():
        print(f"\nhorizon {window} ({block['utc_days']} days), "
              f"hazard spread across ages {block['hazard_spread_across_ages']:.4f}")
        print(md_table(block["rows"], ["condition", "n", "p_regime_changes", "lcb95", "ucb95"]))
        if block["economics"]:
            e = block["economics"]
            print(f"  ORACLE |move|-cost  gated {e['gated_abs_move_minus_cost_bps']:+.3f} "
                  f"(LCB {e['gated_lcb95']:+.3f})  vs ungated "
                  f"{e['ungated_abs_move_minus_cost_bps']:+.3f} "
                  f"(LCB {e['ungated_lcb95']:+.3f})")

    print("\n=== NEXT_ROUND_OPENING_V1 (PM, 10 UTC days) ===")
    print(md_table(results["NEXT_ROUND_OPENING_V1"]["rows"],
                   ["window", "action", "n", "n_days", "mean_cents", "lcb95", "ucb95"]))
    print(f"  positive lower bounds: "
          f"{results['NEXT_ROUND_OPENING_V1']['positive_lcb'] or 'none'}")

    print("\n=== EXIT_EDGE_DECAY_V1 (PM, 10 UTC days) ===")
    print(md_table(results["EXIT_EDGE_DECAY_V1"]["rows"],
                   ["seconds_left", "n", "n_days", "hold_minus_exit_cents", "lcb95", "ucb95"]))
    print("\nwrote research_lanes/batch5_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
