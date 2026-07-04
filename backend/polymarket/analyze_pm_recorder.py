"""
analyze_pm_recorder.py — THE ceiling-breaker: is our P(Hold) mispriced by Polymarket? (offline)
=================================================================================================
The only path past the direction ceiling is market mispricing, not a better BTC model. This measures:

    EDGE = P(Hold of the current side) - market_ask(that side) - taker_fee - buffer

over the live-recorded rounds, and reports ROI / hit-rate at buffers 1c/2c/3c/5c. If `edge > 3c`
yields positive ROI over enough resolved rounds, there is a real path; if every buffer fails,
Polymarket prices the probability efficiently and there is no edge.

Data (all read-only — safe while the app runs):
  • execution_layer.duckdb : pm_round_snapshots (round state per tick) + pm_round_settlements (outcome)
  • analytics.duckdb       : polymarket_quotes (market yes/no ask) + polymarket_markets (token map)
  • persistence_model.pkl  : computes P(Hold) from the snapshot (distance, seconds_left, side, vol_60s)

ROI per trade uses the actual taker-fee-adjusted cost basis. Crypto fees follow
`fee_per_share = 0.07 * ask * (1-ask)` unless the market reports a different schedule.

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
CRYPTO_TAKER_FEE_RATE = 0.07
ENTRY_FAIR_CAP = 0.91


def _taker_fee_per_share(price, fee_rate=CRYPTO_TAKER_FEE_RATE):
    p = np.clip(np.asarray(price, dtype=float), 0.0, 1.0)
    return np.maximum(0.0, float(fee_rate)) * p * (1.0 - p)


def _phold(distance_pct, seconds_left, vol_60s_pct, horizon):
    """Base P(Hold) from the persistence model for a recorded snapshot (no live keepers in history)."""
    import joblib
    mp = os.path.join(DATA, "saved_models", "persistence_model.pkl")
    if not os.path.exists(mp):
        return None
    m = joblib.load(mp)
    feats, clf, iso = m.get("features"), m.get("clf"), m.get("iso")   # global iso (per-horizon = wash)
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
    matched to the latest same-horizon value at or before each quote (max age 5s).
    Recorder timestamps are seconds while analytics timestamps are milliseconds; normalize
    explicitly. Backward-only matching prevents future P(Hold) from leaking into an earlier quote."""
    import duckdb
    import pandas as pd
    try:
        a = duckdb.connect(os.path.join(DATA, "analytics.duckdb"), read_only=True)
        ps = a.execute("SELECT horizon, ts, p_hold FROM persistence_snapshot "
                       "WHERE p_hold IS NOT NULL").df()
        a.close()
    except Exception:
        return None
    if len(ps) == 0:
        return None
    left = snaps[["horizon", "ts"]].copy()
    left["horizon"] = left["horizon"].astype("int64")
    left["_order"] = np.arange(len(left))
    left["ts_ms"] = np.where(left["ts"].abs() < 1e11, left["ts"] * 1000.0, left["ts"])
    right = ps[["horizon", "ts", "p_hold"]].copy().rename(columns={"p_hold": "served_p_hold"})
    right["horizon"] = right["horizon"].astype("int64")
    right["ts_ms"] = np.where(right["ts"].abs() < 1e11, right["ts"] * 1000.0, right["ts"])
    merged = pd.merge_asof(
        left.sort_values("ts_ms"),
        right[["horizon", "ts_ms", "served_p_hold"]].sort_values("ts_ms"),
        on="ts_ms",
        by="horizon",
        direction="backward",
        tolerance=5000.0,
    ).sort_values("_order")
    return [float(v) if v is not None and np.isfinite(v) else None
            for v in merged["served_p_hold"]]


def _first_signal_indices(edge, slugs, ts, buffer):
    """Return the first executable signal per market that clears the buffer."""
    candidates = np.flatnonzero(edge >= buffer)
    if slugs is None or ts is None:
        return candidates
    ordered = sorted(candidates, key=lambda i: float(ts[i]))
    seen = set()
    keep = []
    for i in ordered:
        slug = str(slugs[i])
        if slug not in seen:
            seen.add(slug)
            keep.append(i)
    return np.asarray(keep, dtype=int)


def _roi_table(edge, won, ask, label, slugs=None, ts=None, fee=None):
    """ROI/hit-rate at each buffer, allowing at most one position per round."""
    print(f"\n  {label}: {len(edge)} eligible snapshots with an official outcome")
    print(f"    {'buffer':>7} {'n_trades':>9} {'win%':>7} {'avg_ROI':>9} {'total_ROI':>10}")
    for b in BUFFERS:
        idx = _first_signal_indices(edge, slugs, ts, b)
        n = len(idx)
        if n == 0:
            print(f"    {int(b*100):>6}c {'0':>9}  (none clear this buffer)"); continue
        w = won[idx].astype(bool); a = ask[idx]
        f = (_taker_fee_per_share(a) if fee is None else np.asarray(fee, dtype=float)[idx])
        cost_basis = a + f
        pnl = np.where(w, 1.0 - cost_basis, -cost_basis)
        roi = pnl / np.maximum(cost_basis, 1e-6)
        print(f"    {int(b*100):>6}c {n:>9} {w.mean()*100:>6.1f}% {roi.mean():>+9.3f} {roi.sum():>+10.2f}")


def _load_recorder_tables():
    """Read DuckDB directly, or its periodic exports while the recorder owns the lock."""
    import duckdb
    try:
        con = duckdb.connect(os.path.join(DATA, "execution_layer.duckdb"), read_only=True)
        snaps = con.execute("""SELECT ts,slug,condition_id,horizon,seconds_left,distance_pct,
            current_side,vol_60s_pct,p_hold_cur,up_ask,down_ask FROM pm_round_snapshots""").df()
        setts = con.execute("""SELECT slug,settled_side,up_win,resolution_source
            FROM pm_round_settlements
            WHERE resolution_source IN ('polymarket_clob','polymarket_gamma')""").df()
        con.close()
        return snaps, setts, "duckdb"
    except Exception as db_error:
        snap_path = os.path.join(DATA, "pm_export_snapshots.parquet")
        sett_path = os.path.join(DATA, "pm_export_settlements.parquet")
        if not (os.path.exists(snap_path) and os.path.exists(sett_path)):
            raise RuntimeError(f"cannot read recorder DB or exports: {str(db_error)[:100]}")
        con = duckdb.connect()
        snaps = con.execute("""SELECT ts,slug,condition_id,horizon,seconds_left,distance_pct,
            current_side,vol_60s_pct,p_hold_cur,up_ask,down_ask FROM read_parquet(?)""",
                            [snap_path]).df()
        setts = con.execute("""SELECT slug,settled_side,up_win,resolution_source
            FROM read_parquet(?)
            WHERE resolution_source IN ('polymarket_clob','polymarket_gamma')""",
                            [sett_path]).df()
        con.close()
        return snaps, setts, "parquet_export"


def run():
    # --- recorder: round snapshots + settlements ---
    try:
        snaps, setts, recorder_source = _load_recorder_tables()
    except Exception as ex:
        sys.exit(str(ex))
    print(f"recorder: {len(snaps)} snapshots over {snaps['slug'].nunique() if len(snaps) else 0} rounds | "
          f"{len(setts)} official settlements | source={recorder_source}")
    if len(snaps) == 0:
        sys.exit("no round snapshots yet — let live_btc_updown_recorder.py run, then rerun.")
    # A trustworthy round must begin within five seconds of the timestamp encoded in
    # the slug. Positive delays are late manufactured anchors. Negative delays are
    # smoke/future-round contamination from the old smoke path. Remove both.
    first = snaps.sort_values("ts").groupby("slug", as_index=False).first()
    invalid_slugs = set()
    for _, row in first.iterrows():
        try:
            anchor_ts = int(str(row["slug"]).rsplit("-", 1)[1])
            quote_ts = float(row["ts"])
            if quote_ts > 1e11:
                quote_ts /= 1000.0
            if abs(quote_ts - anchor_ts) > 5.0:
                invalid_slugs.add(str(row["slug"]))
        except Exception:
            continue
    if invalid_slugs:
        before = len(snaps)
        snaps = snaps[~snaps["slug"].astype(str).isin(invalid_slugs)].reset_index(drop=True)
        print(f"anchor-quality filter: removed {len(invalid_slugs)} off-open rounds / "
              f"{before - len(snaps)} snapshots")
    if len(snaps) == 0:
        sys.exit("no trustworthy near-open anchors remain; keep the fixed recorder running.")
    print("market asks: exact UP/DOWN asks recorded in each shadow snapshot")

    # Prefer the actual keeper P(Hold) served by the app. The recorder's same-tick value
    # is the smaller base model (no keeper inputs), so it is a fallback rather than the
    # probability whose market mispricing this project is trying to test.
    recorded = [float(x) if x is not None and np.isfinite(x) else None for x in snaps["p_hold_cur"]]
    served = _served_phold(snaps)
    base = _phold(snaps["distance_pct"], snaps["seconds_left"], snaps["vol_60s_pct"], snaps["horizon"])
    if not any(x is not None for x in recorded) and served is None and base is None:
        sys.exit("no recorded/served P(Hold) and persistence_model.pkl missing — can't compute P(Hold).")
    served_values = served or [None] * len(snaps)
    base_values = base or [None] * len(snaps)
    chosen, source = [], []
    for s, r, b in zip(served_values, recorded, base_values):
        if s is not None:
            chosen.append(s); source.append("served_keeper")
        elif r is not None:
            chosen.append(r); source.append("recorded_base")
        elif b is not None:
            chosen.append(b); source.append("recomputed_base")
        else:
            chosen.append(None); source.append("missing")
    snaps["p_hold"] = chosen
    snaps["p_hold_source"] = source
    counts = snaps["p_hold_source"].value_counts().to_dict()
    print("P(Hold) source: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    valid = snaps["p_hold"].notna()
    print(f"P(Hold) computed for {int(valid.sum())}/{len(snaps)} snapshots "
          f"(mean {snaps.loc[valid,'p_hold'].mean():.3f})" if valid.any() else "P(Hold) none")

    if len(setts) == 0:
        print("\nINSUFFICIENT DATA for ROI: no official settled rounds yet. "
              "Run the recorder (or --settle-once), then rerun.")
        print("Need ~500-1000 resolved rounds to judge the edge (per the strategy).")
        return

    # The quote and P(Hold) came from the same recorder tick. No cross-process nearest-time join.
    rows = []
    sett = dict(zip(setts["slug"], setts["settled_side"]))
    for _, s in snaps[valid].iterrows():
        if s["slug"] not in sett:
            continue
        raw_side = s["current_side"]
        side = 1 if raw_side in (1, 1.0, "UP", "up") else 0
        ask = float(s["up_ask"] if side == 1 else s["down_ask"])
        if not (0.0 < ask < 1.0):
            continue
        won = int(side == int(sett[s["slug"]]))
        fee = float(_taker_fee_per_share([ask])[0])
        fair = min(float(s["p_hold"]), ENTRY_FAIR_CAP)
        rows.append((fair - ask - fee, won, ask, fee,
                     str(s["slug"]), float(s["ts"])))
    if not rows:
        print("\nNo (snapshot↔quote↔outcome) joins yet — rerun as data accrues.")
        return
    edge = np.array([r[0] for r in rows]); won = np.array([r[1] for r in rows]); ask = np.array([r[2] for r in rows])
    fee = np.array([r[3] for r in rows]); slugs = np.array([r[4] for r in rows]); ts = np.array([r[5] for r in rows])
    _roi_table(edge, won, ask, "EDGE = min(P(Hold), 91c) - ask - crypto taker fee", slugs=slugs, ts=ts, fee=fee)
    trade_counts = {b: len(_first_signal_indices(edge, slugs, ts, b)) for b in BUFFERS}
    if max(trade_counts.values(), default=0) < 500:
        print("\nINSUFFICIENT DATA: fewer than 500 one-entry-per-round signals. "
              "Displayed ROI is diagnostic only and cannot prove or reject an edge.")
    else:
        print("\nREAD: positive avg_ROI at >=3c over hundreds of rounds is a candidate mispricing edge; "
              "flat/negative recent and horizon-specific results reject it.")


def selftest():
    global DATA
    # _roi_table math: a planted +edge winner set yields positive ROI; a losing set negative.
    edge = np.array([0.05, 0.04, 0.06, 0.02, 0.01])
    won = np.array([1, 1, 1, 0, 0]); ask = np.array([0.85, 0.88, 0.84, 0.90, 0.95])
    fee = _taker_fee_per_share(ask)
    cost = ask + fee
    roi = np.where(won.astype(bool), (1 - cost) / cost, -1.0)
    assert roi[0] > 0 and roi[3] == -1.0, "ROI math wrong"
    # at buffer 3c, only the 3 winners (edge .05/.04/.06) clear -> all won -> positive
    m = edge >= 0.03
    assert won[m].mean() == 1.0, "buffer filter wrong"
    dedup = _first_signal_indices(np.array([.05, .06, .04]),
                                  np.array(["round-a", "round-a", "round-b"]),
                                  np.array([1.0, 2.0, 1.5]), .03)
    assert dedup.tolist() == [0, 2], f"one-entry-per-round filter wrong: {dedup}"
    # Recorder snapshots use epoch seconds; analytics uses epoch milliseconds. Confirm
    # the join normalizes units and never selects a future probability.
    import tempfile
    import duckdb
    import pandas as pd
    old_data = DATA
    with tempfile.TemporaryDirectory(prefix="pm_analyzer_selftest_") as tmp:
        DATA = tmp
        con = duckdb.connect(os.path.join(tmp, "analytics.duckdb"))
        con.execute("CREATE TABLE persistence_snapshot(horizon INT, ts BIGINT, p_hold DOUBLE)")
        con.execute("INSERT INTO persistence_snapshot VALUES "
                    "(5,1780000000000,0.70),(5,1780000003000,0.90)")
        con.close()
        matched = _served_phold(pd.DataFrame({"horizon": [5], "ts": [1780000002.0]}))
        assert matched == [0.70], f"backward ms-normalized P(Hold) join wrong: {matched}"
    DATA = old_data
    print(f"analyze_pm_recorder self-test: ALL PASS (ROI math + buffer + one-entry-per-round; sample avg ROI "
          f"@3c={np.where(won[m].astype(bool),(1-ask[m])/ask[m],-1.0).mean():+.3f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    selftest() if a.selftest else run()
