"""
model_metrics_logger.py — log EVERY model's live output to a SEPARATE DuckDB (crash-safe).
============================================================================================
The operator wants every model's live predictions logged for offline metrics. This writes to a
DEDICATED file `data/model_metrics.duckdb` — it NEVER touches the live `crypto_market_data` DB
(single-writer), so it's safe to run inside the live app. Two tables:

  direction_log — per horizon per tick: the ensemble direction + P(up/down/neutral) + confidence +
                  final (gated) direction + expected move + trade verdict + action.
  ptb_log       — per horizon per tick: price-to-beat P(Hold) (+source), tier, the signed-quantile
                  band (expected drop/high), projected close, projected-vs-beat, band source, lean.

Every call is wrapped so a logging failure can NEVER crash serving (the §5av crash-safe rule).
Read it OFFLINE (app stopped) with duckdb, or copy the file — same single-writer rule as the live DB.

Self-test:  python backend/model_metrics_logger.py --selftest
"""
import os
import threading
import time

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
DEFAULT_PATH = os.path.join(DATA_DIR, "model_metrics.duckdb")

_LOCK = threading.Lock()
_STATE = {"conn": None, "path": None, "failed": False}

_DDL = {
    "direction_log": (
        "ts_ms BIGINT, horizon INTEGER, regime VARCHAR, direction VARCHAR, prob_up DOUBLE, "
        "prob_down DOUBLE, prob_neutral DOUBLE, confidence DOUBLE, final_direction VARCHAR, "
        "expected_move DOUBLE, trade_verdict VARCHAR, action VARCHAR"),
    "ptb_log": (
        "ts_ms BIGINT, horizon INTEGER, venue VARCHAR, ref_price DOUBLE, beat_line DOUBLE, "
        "seconds_left DOUBLE, distance DOUBLE, p_hold DOUBLE, p_hold_source VARCHAR, tier VARCHAR, "
        "expected_drop DOUBLE, expected_high DOUBLE, projected_close DOUBLE, projected_vs_beat DOUBLE, "
        "band_source VARCHAR, live_lean VARCHAR"),
}


def _conn(path=None):
    """Lazily open the dedicated metrics DB + create tables. Returns conn or None (never raises)."""
    if _STATE["failed"]:
        return None
    if _STATE["conn"] is not None:
        return _STATE["conn"]
    try:
        import duckdb
        p = path or DEFAULT_PATH
        os.makedirs(os.path.dirname(p), exist_ok=True)
        conn = duckdb.connect(p)
        for name, cols in _DDL.items():
            conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({cols})")
        _STATE["conn"], _STATE["path"] = conn, p
        return conn
    except Exception:
        _STATE["failed"] = True            # disable quietly if duckdb/file unavailable
        return None


def _f(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _i(x):
    try:
        return int(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def log_direction(predictions, regime="", ts_ms=None, path=None):
    """Log the per-horizon ensemble outputs. `predictions` = the list broadcast to the UI. No-raise."""
    try:
        with _LOCK:
            c = _conn(path)
            if c is None or not predictions:
                return
            ts = ts_ms or int(time.time() * 1000)
            rows = []
            for p in predictions:
                if not isinstance(p, dict):
                    continue
                rows.append([ts, _i(p.get("horizon")), str(regime or ""),
                             str(p.get("direction") or ""),
                             _f(p.get("probUp") if p.get("probUp") is not None else p.get("prob_up")),
                             _f(p.get("probDown") if p.get("probDown") is not None else p.get("prob_down")),
                             _f(p.get("probNeutral") if p.get("probNeutral") is not None else p.get("prob_neutral")),
                             _f(p.get("confidence")), str(p.get("finalDirection") or p.get("direction") or ""),
                             _f(p.get("expectedMove")), str(p.get("trade_verdict") or p.get("finalAction") or ""),
                             str(p.get("finalAction") or "")])
            if rows:
                c.executemany("INSERT INTO direction_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    except Exception:
        pass


def log_ptb(rounds, venue="binance", ts_ms=None, path=None):
    """Log price-to-beat round outputs. `rounds` = iterable of the per-horizon round dicts. No-raise."""
    try:
        with _LOCK:
            c = _conn(path)
            if c is None or not rounds:
                return
            ts = ts_ms or int(time.time() * 1000)
            rows = []
            for r in rounds:
                if not isinstance(r, dict):
                    continue
                emr = r.get("expected_move_range") or {}
                rows.append([ts, _i(r.get("horizon")), str(venue),
                             _f(r.get("ref_price") or r.get("current_price")), _f(r.get("beat_line") or r.get("price_to_beat")),
                             _f(r.get("seconds_left")), _f(r.get("distance")),
                             _f(r.get("p_hold")), str(r.get("p_hold_source") or ""), str(r.get("tier") or ""),
                             _f(emr.get("low")), _f(emr.get("high")), _f(r.get("projected_close")),
                             _f(r.get("projected_vs_beat")), str(r.get("band_source") or ""),
                             str(r.get("live_lean") or "")])
            if rows:
                c.executemany("INSERT INTO ptb_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    except Exception:
        pass


def _selftest():
    import tempfile
    import duckdb
    tmp = os.path.join(tempfile.gettempdir(), f"mm_selftest_{os.getpid()}.duckdb")
    if os.path.exists(tmp):
        os.remove(tmp)
    preds = [{"horizon": 5, "direction": "UP", "probUp": 0.55, "probDown": 0.30, "probNeutral": 0.15,
              "confidence": 0.55, "finalDirection": "NEUTRAL", "expectedMove": 42.0,
              "trade_verdict": "NO_TRADE", "finalAction": "AVOID"},
             {"horizon": 15, "direction": "DOWN", "prob_up": 0.4, "prob_down": 0.6}]   # mixed key styles
    rounds = [{"horizon": 5, "ref_price": 67000.0, "beat_line": 66950.0, "p_hold": 0.94,
               "p_hold_source": "keeper", "tier": "T3", "expected_move_range": {"low": -135.0, "high": 140.0},
               "projected_close": 67005.0, "projected_vs_beat": 55.0, "band_source": "signed_quantile",
               "live_lean": "UP", "seconds_left": 45.0, "distance": 50.0}]
    log_direction(preds, regime="VOLATILE", path=tmp)
    log_ptb(rounds, path=tmp)
    log_direction(None, path=tmp)            # must not raise on empty
    log_ptb(["notadict", 123], path=tmp)     # non-dicts skipped by the isinstance guard; no-raise
    _STATE["conn"].close(); _STATE["conn"] = None; _STATE["failed"] = False
    c = duckdb.connect(tmp)
    nd = c.execute("SELECT count(*) FROM direction_log").fetchone()[0]
    npb = c.execute("SELECT count(*) FROM ptb_log").fetchone()[0]
    up5 = c.execute("SELECT prob_up FROM direction_log WHERE horizon=5").fetchone()[0]
    ph = c.execute("SELECT p_hold, band_source FROM ptb_log WHERE horizon=5").fetchone()
    c.close(); os.remove(tmp)
    assert nd == 2 and npb == 1, f"row counts wrong: dir {nd}, ptb {npb}"
    assert abs(up5 - 0.55) < 1e-9 and abs(ph[0] - 0.94) < 1e-9 and ph[1] == "signed_quantile"
    print(f"model_metrics_logger self-test: ALL PASS (direction rows {nd}, ptb rows {npb}, "
          f"p_hold {ph[0]}, band {ph[1]})")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(f"metrics DB -> {DEFAULT_PATH}; import and call log_direction()/log_ptb() from server.")
