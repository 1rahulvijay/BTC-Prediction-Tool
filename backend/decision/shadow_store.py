"""
shadow_store.py — a SEPARATE DuckDB for the execution/decision layer.
=====================================================================
Deliberately uses its OWN file (`data/execution_layer.duckdb`), NOT the live app's
`analytics.duckdb`. DuckDB is single-writer per file, so a separate file means the
execution layer can be written/queried with ZERO contention or risk to the live
ensemble's database — the app never opens this file.

Holds the live-shadow tables (Codex schema, DuckDB-typed):
  • shadow_signals   — every composed decision + its resolution (paper EV)
  • shadow_orders    — maker limit-order fill tracking (fill quality, adverse selection)
  • rejection_events — why each non-trade was rejected (gate analytics)

Additive `CREATE TABLE IF NOT EXISTS` only — safe to re-run. This is the FOUNDATION
the (deferred, sign-off-gated) live wiring will write to. Until then it stays empty.
Offline analytics (`rejection_summary`, `tier_scorecard`) read from it.
"""
from __future__ import annotations

import os
import duckdb

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
# SEPARATE file — never analytics.duckdb.
DB_PATH = os.environ.get("BTC_EXEC_DB_PATH") or os.path.join(DATA_DIR, "execution_layer.duckdb")


def get_conn(path: str | None = None):
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return duckdb.connect(p)


def init_db(conn=None):
    """Create the execution-layer schema (additive). Returns the connection."""
    own = conn is None
    conn = conn or get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_signals (
            id              VARCHAR PRIMARY KEY,
            ts_ms           BIGINT,
            horizon         INTEGER,
            p_big_move      DOUBLE,
            p_tradable      DOUBLE,
            p_fail_fast     DOUBLE,
            p_good_fill     DOUBLE,
            side_rule       VARCHAR,
            selected_side   VARCHAR,
            expected_move_bps   DOUBLE,
            expected_cost_bps   DOUBLE,
            move_cost_ratio     DOUBLE,
            final_tier      VARCHAR,
            final_action    VARCHAR,
            rejection_reason VARCHAR,
            entry_mode      VARCHAR,
            entry_price     DOUBLE,
            verify_at_ms    BIGINT,
            mfe_5m_bps      DOUBLE,
            mae_5m_bps      DOUBLE,
            mfe_15m_bps     DOUBLE,
            mae_15m_bps     DOUBLE,
            realized_pnl_bps DOUBLE,
            exit_reason     VARCHAR,
            exit_price      DOUBLE,
            resolved        BOOLEAN DEFAULT FALSE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_orders (
            id              VARCHAR PRIMARY KEY,
            signal_id       VARCHAR,
            ts_ms           BIGINT,
            side            VARCHAR,
            entry_mode      VARCHAR,
            limit_price     DOUBLE,
            mid_price       DOUBLE,
            spread_bps      DOUBLE,
            fill_status     VARCHAR,
            seconds_to_fill DOUBLE,
            fill_price      DOUBLE,
            adverse_selection_after_fill_bps DOUBLE,
            resolved        BOOLEAN DEFAULT FALSE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rejection_events (
            ts_ms    BIGINT,
            horizon  INTEGER,
            reason   VARCHAR,
            tier     VARCHAR
        )
    """)
    if own:
        return conn
    return conn


def log_shadow_signal(row: dict, conn=None):
    """Insert one composed decision. `row` keys are a subset of shadow_signals columns;
    `id` and `ts_ms` are required."""
    own = conn is None
    conn = conn or get_conn()
    cols = ["id", "ts_ms", "horizon", "p_big_move", "p_tradable", "p_fail_fast", "p_good_fill",
            "side_rule", "selected_side", "expected_move_bps", "expected_cost_bps",
            "move_cost_ratio", "final_tier", "final_action", "rejection_reason", "entry_mode",
            "entry_price", "verify_at_ms"]
    vals = [row.get(c) for c in cols]
    conn.execute(
        f"INSERT OR REPLACE INTO shadow_signals ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
        vals)
    if own:
        conn.close()


def log_rejection(ts_ms: int, horizon: int, reason: str, tier: str, conn=None):
    own = conn is None
    conn = conn or get_conn()
    conn.execute("INSERT INTO rejection_events (ts_ms,horizon,reason,tier) VALUES (?,?,?,?)",
                 [ts_ms, horizon, reason, tier])
    if own:
        conn.close()


def bulk_write_signals(df, conn=None):
    """Fast insert of many shadow_signals rows from a DataFrame (offline replay)."""
    own = conn is None
    conn = conn or get_conn()
    table_cols = [r[0] for r in conn.execute("DESCRIBE shadow_signals").fetchall()]
    cols = [c for c in df.columns if c in table_cols]
    conn.register("sig_df", df[cols])
    conn.execute(f"INSERT OR REPLACE INTO shadow_signals ({','.join(cols)}) "
                 f"SELECT {','.join(cols)} FROM sig_df")
    conn.unregister("sig_df")
    if own:
        conn.close()


def bulk_write_rejections(df, conn=None):
    """Fast insert of many rejection_events rows from a DataFrame."""
    own = conn is None
    conn = conn or get_conn()
    cols = [c for c in ["ts_ms", "horizon", "reason", "tier"] if c in df.columns]
    conn.register("rej_df", df[cols])
    conn.execute(f"INSERT INTO rejection_events ({','.join(cols)}) SELECT {','.join(cols)} FROM rej_df")
    conn.unregister("rej_df")
    if own:
        conn.close()


def reset(conn=None):
    """Truncate all execution-layer tables (offline re-runs)."""
    own = conn is None
    conn = conn or get_conn()
    for t in ("shadow_signals", "shadow_orders", "rejection_events"):
        conn.execute(f"DELETE FROM {t}")
    if own:
        conn.close()


def rejection_summary(conn=None) -> list[tuple]:
    own = conn is None
    conn = conn or get_conn()
    rows = conn.execute(
        "SELECT reason, COUNT(*) n FROM rejection_events GROUP BY reason ORDER BY n DESC").fetchall()
    if own:
        conn.close()
    return rows


def tier_scorecard(conn=None) -> list[tuple]:
    """Per final_tier on RESOLVED shadow signals: n, win%, avg realized pnl (bps)."""
    own = conn is None
    conn = conn or get_conn()
    rows = conn.execute("""
        SELECT final_tier,
               COUNT(*) n,
               AVG(CASE WHEN realized_pnl_bps > 0 THEN 1.0 ELSE 0.0 END) * 100 win_pct,
               AVG(realized_pnl_bps) avg_pnl_bps
        FROM shadow_signals WHERE resolved = TRUE
        GROUP BY final_tier ORDER BY final_tier
    """).fetchall()
    if own:
        conn.close()
    return rows


if __name__ == "__main__":
    print("=== shadow_store self-test (temp file, no app impact) ===")
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "exec_layer_selftest.duckdb")
    if os.path.exists(tmp):
        os.remove(tmp)
    c = get_conn(tmp)
    init_db(c)
    tbls = {r[0] for r in c.execute("SHOW TABLES").fetchall()}
    assert {"shadow_signals", "shadow_orders", "rejection_events"} <= tbls, tbls
    log_shadow_signal({"id": "s1", "ts_ms": 1, "horizon": 5, "final_tier": "T2",
                       "final_action": "T2_SHADOW", "selected_side": "LONG"}, conn=c)
    log_rejection(2, 5, "weak_selectivity", "T0", conn=c)
    log_rejection(3, 5, "cost_gate_failed", "T1", conn=c)
    c.execute("UPDATE shadow_signals SET resolved=TRUE, realized_pnl_bps=3.0 WHERE id='s1'")
    assert dict(rejection_summary(conn=c)).get("weak_selectivity") == 1
    sc = tier_scorecard(conn=c)
    assert sc and sc[0][0] == "T2" and abs(sc[0][3] - 3.0) < 1e-9, sc
    c.close()
    os.remove(tmp)
    print("shadow_store self-test PASSED")

    # Never touch the live execution DB from a self-test. The recorder may own
    # its Windows file lock, and a validation command must stay side-effect free.
    if "--init-real" in __import__("sys").argv:
        real = init_db()
        real_tbls = sorted(r[0] for r in real.execute("SHOW TABLES").fetchall())
        real.close()
        print(f"Initialized {DB_PATH}")
        print(f"  tables: {real_tbls}")
