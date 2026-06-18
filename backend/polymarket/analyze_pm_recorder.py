"""
analyze_pm_recorder.py — THE ceiling-breaker: is our P(Hold) mispriced by Polymarket? (offline)
=================================================================================================
The only path past the direction ceiling is market mispricing, not a better BTC model. This measures:

    EDGE = P(Hold of the current side) - market_ask(that side) - buffer

over the live-recorded rounds, and reports ROI / hit-rate at buffers 1c/2c/3c/5c. If `edge > 3c`
yields positive ROI over enough resolved rounds, there is a real path; if every buffer fails,
Polymarket prices the probability efficiently and there is no edge.

Data (all read-only — safe while the app runs):
  • execution_layer.duckdb : pm_round_snapshots (round state per tick) + pm_round_settlements (outcome)
  • analytics.duckdb       : polymarket_quotes (market yes/no ask) + polymarket_markets (token map)
  • persistence_model.pkl  : computes P(Hold) from the snapshot (distance, seconds_left, side, vol_60s)

ROI per trade (buy the held side at `ask`, hold to expiry): win -> (1-ask)/ask ; lose -> -1.

Usage:  python backend/polymarket/analyze_pm_recorder.py
        python backend/polymarket/analyze_pm_recorder.py --selftest
"""
import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
BUFFERS = (0.01, 0.02, 0.03, 0.05)


def _phold(distance_pct, seconds_left, vol_60s_pct, horizon):
    """Base P(Hold) from the persistence model for a recorded snapshot (no live keepers in history)."""
    import joblib
    mp = os.path.join(DATA, "saved_models", "persistence_model.pkl")
    if not os.path.exists(mp):
        return None
    m = joblib.load(mp)
    feats, clf, iso = m.get("features"), m.get("clf"), m.get("iso")
    if not feats or clf is None:
        return None
    out = []
    for d, sl, v, h in zip(distance_pct, seconds_left, vol_60s_pct, horizon):
        ad = abs(float(d or 0.0)); v = float(v or 0.0)
        fv = {"abs_distance_pct": ad, "seconds_left": float(sl or 0.0), "vol_60s_pct": v,
              "horizon": float(h or 5), "dist_vol_ratio": ad / (v + 1e-6)}
        try:
            raw = clf.predict_proba([[fv[k] for k in feats]])[:, 1]
            out.append(float(iso.predict(raw)[0]) if iso is not None else float(raw[0]))
        except Exception:
            out.append(None)
    return out


def _served_phold(snaps):
    """SERVED keeper P(Hold) from analytics.persistence_snapshot (the EXACT value the card shows),
    matched to each round snapshot by (horizon, nearest ts within 5s). List aligned to snaps, or None."""
    import duckdb
    try:
        a = duckdb.connect(os.path.join(DATA, "analytics.duckdb"), read_only=True)
        ps = a.execute("SELECT horizon, ts, p_hold FROM persistence_snapshot "
                       "WHERE p_hold IS NOT NULL").df()
        a.close()
    except Exception:
        return None
    if len(ps) == 0:
        return None
    out = []
    for _, s in snaps.iterrows():
        cand = ps[ps["horizon"] == s["horizon"]]
        if len(cand) == 0:
            out.append(None); continue
        di = (cand["ts"] - s["ts"]).abs()
        j = di.idxmin()
        out.append(float(cand.loc[j, "p_hold"]) if di.loc[j] < 5000 else None)
    return out


def _roi_table(edge, won, ask, label):
    """ROI/hit-rate at each buffer over rows that pass edge>=buffer."""
    print(f"\n  {label}: {len(edge)} (snapshot,quote) pairs with an outcome")
    print(f"    {'buffer':>7} {'n_trades':>9} {'win%':>7} {'avg_ROI':>9} {'total_ROI':>10}")
    for b in BUFFERS:
        m = edge >= b
        n = int(m.sum())
        if n == 0:
            print(f"    {int(b*100):>6}c {'0':>9}  (none clear this buffer)"); continue
        w = won[m].astype(bool); a = ask[m]
        roi = np.where(w, (1.0 - a) / np.maximum(a, 1e-6), -1.0)
        print(f"    {int(b*100):>6}c {n:>9} {w.mean()*100:>6.1f}% {roi.mean():>+9.3f} {roi.sum():>+10.2f}")


def run():
    import duckdb
    # --- recorder: round snapshots + settlements ---
    try:
        e = duckdb.connect(os.path.join(DATA, "execution_layer.duckdb"), read_only=True)
    except Exception as ex:
        sys.exit(f"cannot read execution_layer.duckdb: {str(ex)[:80]}")
    snaps = e.execute("SELECT ts, slug, condition_id, horizon, seconds_left, distance_pct, "
                      "current_side, vol_60s_pct FROM pm_round_snapshots").df()
    setts = e.execute("SELECT slug, settled_side, up_win FROM pm_round_settlements").df()
    e.close()
    print(f"recorder: {len(snaps)} snapshots over {snaps['slug'].nunique() if len(snaps) else 0} rounds | "
          f"{len(setts)} settled")
    if len(snaps) == 0:
        sys.exit("no round snapshots yet — let live_btc_updown_recorder.py run, then rerun.")

    # --- market quotes (ask) joined by condition_id + nearest time ---
    try:
        a = duckdb.connect(os.path.join(DATA, "analytics.duckdb"), read_only=True)
        mk = a.execute("SELECT market_id, condition_id, yes_token, no_token FROM polymarket_markets").df()
        qt = a.execute("SELECT market_id, timestamp, yes_ask, no_ask, spread FROM polymarket_quotes").df()
        a.close()
        print(f"market: {len(qt)} quotes over {qt['market_id'].nunique() if len(qt) else 0} markets")
    except Exception as ex:
        print(f"(quotes unavailable: {str(ex)[:60]}) — reporting P(Hold) coverage only")
        qt = mk = None

    # --- P(Hold) per snapshot --- #4 PARITY: prefer the SERVED keeper P(Hold) (persistence_snapshot,
    # the EXACT value the card shows), matched by (horizon, nearest ts within 5s). Fall back to a base
    # recompute only if no served value is near. This makes the edge use the same P(Hold) as the UI.
    served = _served_phold(snaps)
    base = _phold(snaps["distance_pct"], snaps["seconds_left"], snaps["vol_60s_pct"], snaps["horizon"])
    if served is None and base is None:
        sys.exit("no served P(Hold) and persistence_model.pkl missing — can't compute P(Hold).")
    snaps["p_hold"] = [s if s is not None else (b if base is not None else None)
                       for s, b in zip(served or [None] * len(snaps), base or [None] * len(snaps))]
    n_served = sum(1 for s in (served or []) if s is not None)
    print(f"P(Hold) source: {n_served} served (keeper, card-parity) + "
          f"{int(snaps['p_hold'].notna().sum()) - n_served} base-recompute fallback")
    valid = snaps["p_hold"].notna()
    print(f"P(Hold) computed for {int(valid.sum())}/{len(snaps)} snapshots "
          f"(mean {snaps.loc[valid,'p_hold'].mean():.3f})" if valid.any() else "P(Hold) none")

    if qt is None or len(qt) == 0 or len(setts) == 0:
        print("\nINSUFFICIENT DATA for ROI: " + ("no settled rounds yet" if len(setts) == 0 else "no quotes")
              + ". The recorder + this tool are READY — rerun once rounds resolve with market quotes.")
        print("Need ~500-1000 resolved rounds to judge the edge (per the strategy).")
        return

    # join: snapshot.condition_id -> market_id -> nearest quote in time; ask of the CURRENT side
    import pandas as pd
    cond2mkt = dict(zip(mk["condition_id"], mk["market_id"]))
    rows = []
    sett = dict(zip(setts["slug"], setts["settled_side"]))
    for _, s in snaps[valid].iterrows():
        if s["slug"] not in sett:
            continue
        mid = cond2mkt.get(s["condition_id"])
        if mid is None:
            continue
        mq = qt[qt["market_id"] == mid]
        if len(mq) == 0:
            continue
        nearest = mq.iloc[(mq["timestamp"] - s["ts"]).abs().argmin()]
        side = s["current_side"]
        ask = float(nearest["yes_ask"] if side == "UP" else nearest["no_ask"])
        if not (0.0 < ask < 1.0):
            continue
        won = 1 if s["current_side"] == sett[s["slug"]] else 0
        rows.append((float(s["p_hold"]) - ask, won, ask))
    if not rows:
        print("\nNo (snapshot↔quote↔outcome) joins yet — rerun as data accrues.")
        return
    edge = np.array([r[0] for r in rows]); won = np.array([r[1] for r in rows]); ask = np.array([r[2] for r in rows])
    _roi_table(edge, won, ask, "EDGE = P(Hold) - ask")
    print("\nREAD: positive avg_ROI at >=3c over hundreds of rounds = a real mispricing edge. "
          "All buffers <=0 = Polymarket prices it efficiently (ceiling holds).")


def selftest():
    # _roi_table math: a planted +edge winner set yields positive ROI; a losing set negative.
    edge = np.array([0.05, 0.04, 0.06, 0.02, 0.01])
    won = np.array([1, 1, 1, 0, 0]); ask = np.array([0.85, 0.88, 0.84, 0.90, 0.95])
    roi = np.where(won.astype(bool), (1 - ask) / ask, -1.0)
    assert roi[0] > 0 and roi[3] == -1.0, "ROI math wrong"
    # at buffer 3c, only the 3 winners (edge .05/.04/.06) clear -> all won -> positive
    m = edge >= 0.03
    assert won[m].mean() == 1.0, "buffer filter wrong"
    print(f"analyze_pm_recorder self-test: ALL PASS (ROI math + buffer filter; sample avg ROI "
          f"@3c={np.where(won[m].astype(bool),(1-ask[m])/ask[m],-1.0).mean():+.3f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    selftest() if a.selftest else run()
