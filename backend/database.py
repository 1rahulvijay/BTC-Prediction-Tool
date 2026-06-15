import duckdb
import os
import time
import json
import logging

logger = logging.getLogger(__name__)

# DB location. Defaults to <project>/data/analytics.duckdb so all app-generated files
# sit together under data/. Override with BTC_DB_PATH (file) or BTC_DATA_DIR (folder).
# Keep OneDrive sync OFF for this folder so its service/IDE indexers can't lock it mid-write.
_DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'data'
)
DB_PATH = os.environ.get("BTC_DB_PATH") or os.path.join(_DATA_DIR, 'analytics.duckdb')
_dbdir = os.path.dirname(DB_PATH)
if _dbdir:
    os.makedirs(_dbdir, exist_ok=True)


def _connect(read_only: bool = False, retries: int = 6, backoff: float = 0.25):
    """Open DuckDB, retrying transient lock errors. OneDrive sync / IDE indexers can
    briefly hold the file handle; without a retry those writes are silently lost. After
    exhausting retries we raise so the caller's own try/except degrades gracefully."""
    last = None
    for i in range(retries):
        try:
            conn = duckdb.connect(DB_PATH, read_only=read_only)
            # Cap DuckDB's RAM + thread appetite. Default memory_limit is ~80% of system
            # RAM (~12.5 GB on a 16 GB box) and default threads = all cores — on this
            # shared machine that lets a single heavy query spike memory and compete with
            # the live event loop. These tables are tiny, so a tight cap is plenty.
            try:
                conn.execute("SET memory_limit='512MB'")
                conn.execute("SET threads=2")
            except Exception:
                pass
            return conn
        except Exception as e:
            last = e
            time.sleep(backoff * (i + 1))
    raise last


# ANCHOR connection: held open for the process lifetime (created in init_db, never used
# for queries). Why: every helper opens->queries->closes; when the LAST connection closes,
# DuckDB closes the whole database instance and runs a close-checkpoint — which can take
# seconds under a heavy WAL (training-time SHAP/insert bursts) while still holding the OS
# file handle. Any connect arriving in that window fails with "file in use by <own PID>"
# (observed live at 15:53 during the 2026-06-10 retrain: insert + fsr-summary + snapshot
# all failed for ~20s, then self-healed). With the anchor keeping the instance alive in
# DuckDB's same-process cache, helper connects always ATTACH instead of re-opening the
# file, so the self-lock window cannot occur.
_ANCHOR_CONN = None


def init_db():
    # DELIBERATELY fail-fast (no try/except like the other helpers): if the DB cannot be
    # opened at BOOT, the app must stop loudly rather than run without persistence.
    # _connect() already retries transient locks for ~5s (e.g. an orphaned process).
    global _ANCHOR_CONN
    if _ANCHOR_CONN is None:
        _ANCHOR_CONN = duckdb.connect(DB_PATH)
    conn = _connect()
    # For each timeframe, create a table if it doesn't exist
    timeframes = [1, 3, 5, 7, 10, 15, 30]
    for tf in timeframes:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS predictions_{tf}m (
                id VARCHAR PRIMARY KEY,
                timestamp BIGINT,
                horizon INT,
                binance_price DOUBLE,
                target_price DOUBLE,
                expected_move DOUBLE,
                confidence DOUBLE,
                signal VARCHAR,
                chainlink_price DOUBLE,
                chainlink_target DOUBLE,
                actual_price DOUBLE,
                actual_move DOUBLE,
                hit BOOLEAN,
                price_match BOOLEAN,
                move_error DOUBLE,
                resolved BOOLEAN,
                cascade_active BOOLEAN DEFAULT FALSE,
                regime VARCHAR DEFAULT 'UNKNOWN'
            )
        """)
        try:
            conn.execute(f"ALTER TABLE predictions_{tf}m ADD COLUMN cascade_active BOOLEAN DEFAULT FALSE;")
        except Exception:
            pass # column already exists
        try:
            conn.execute(f"ALTER TABLE predictions_{tf}m ADD COLUMN regime VARCHAR DEFAULT 'UNKNOWN';")
        except Exception:
            pass # column already exists
        for ddl in [
            "ADD COLUMN raw_direction VARCHAR DEFAULT ''",
            "ADD COLUMN skip_reason VARCHAR DEFAULT ''",
            "ADD COLUMN avoid_success BOOLEAN DEFAULT FALSE",
            "ADD COLUMN prob_up DOUBLE DEFAULT 0.0",
            "ADD COLUMN prob_down DOUBLE DEFAULT 0.0",
            "ADD COLUMN agreement DOUBLE DEFAULT 0.0",
            "ADD COLUMN model_dirs_json VARCHAR DEFAULT '{}'",
            "ADD COLUMN verify_at BIGINT DEFAULT 0",
            "ADD COLUMN move_range_low DOUBLE DEFAULT NULL",
            "ADD COLUMN move_range_median DOUBLE DEFAULT NULL",
            "ADD COLUMN move_range_high DOUBLE DEFAULT NULL",
            "ADD COLUMN move_range_width DOUBLE DEFAULT NULL",
            "ADD COLUMN model_version VARCHAR DEFAULT 'baseline_v9'",
            "ADD COLUMN feature_schema_hash VARCHAR DEFAULT ''",
            # Pure LEAN sign-truth (raw_direction vs sign(actual_move)) — NULL until
            # resolved or when the lean was NEUTRAL. The legacy `hit` column stays
            # dual-semantic BY DESIGN (final-action quality incl. avoid_success); any
            # betting-accuracy consumer must use lean_hit, never hit.
            "ADD COLUMN lean_hit BOOLEAN DEFAULT NULL",
            # Decision-layer outputs persisted for durable evaluation: the A/B/C setup
            # grade, the precision engine's measured P(win), and the calibrated
            # confidence the gate actually compared against.
            "ADD COLUMN confluence_grade VARCHAR DEFAULT ''",
            "ADD COLUMN expected_precision DOUBLE DEFAULT NULL",
            "ADD COLUMN calibrated_confidence DOUBLE DEFAULT NULL",
            "ADD COLUMN model_raw_direction VARCHAR DEFAULT ''",
            "ADD COLUMN pre_server_direction VARCHAR DEFAULT ''",
            "ADD COLUMN final_direction VARCHAR DEFAULT ''",
            "ADD COLUMN trade_verdict VARCHAR DEFAULT ''",
            "ADD COLUMN no_trade_reasons_json VARCHAR DEFAULT '[]'",
            "ADD COLUMN decision_state_json VARCHAR DEFAULT '{}'",
            "ADD COLUMN model_confluence DOUBLE DEFAULT 0.0",
            "ADD COLUMN setup_score DOUBLE DEFAULT 0.0",
            "ADD COLUMN setup_quality_json VARCHAR DEFAULT '{}'",
        ]:
            try:
                conn.execute(f"ALTER TABLE predictions_{tf}m {ddl};")
            except Exception:
                pass # column already exists
        # One-time backfill of lean_hit for already-resolved historical rows (no-op
        # when nothing qualifies; safe to run every boot).
        try:
            conn.execute(f"""
                UPDATE predictions_{tf}m
                SET lean_hit = ((raw_direction = 'UP'   AND actual_move > 0)
                             OR (raw_direction = 'DOWN' AND actual_move < 0))
                WHERE lean_hit IS NULL AND resolved = TRUE
                  AND raw_direction IN ('UP', 'DOWN') AND actual_move IS NOT NULL
            """)
        except Exception:
            pass
        # Context columns for the trained meta-model (logged now so data accrues).
        for col in ["ewma_vol", "spread_norm", "wall_imbalance",
                    "sr_compression", "liq_imbalance", "quantile_width_pct", "quantile_asymmetry",
                    "quantile_spread",
                    "wf_accuracy", "wf_accuracy_minus_0_5", "wf_fold_std", "wf_sample_count", "wf_age_minutes",
                    "tradeability", "regime_score", "liquidity_score", "expected_edge",
                    "expectancy_usd", "expected_slippage_usd",
                    "conviction", "actionable", "confluence"]:
            try:
                conn.execute(f"ALTER TABLE predictions_{tf}m ADD COLUMN {col} DOUBLE DEFAULT 0.0;")
            except Exception:
                pass  # column already exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feature_importance (
            timestamp TIMESTAMP,
            horizon INT,
            rank INT,
            feature VARCHAR,
            importance DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS simulated_trades (
            trade_id VARCHAR PRIMARY KEY,
            prediction_id VARCHAR,
            timestamp BIGINT,
            horizon INT,
            direction VARCHAR,
            entry_price DOUBLE,
            exit_price DOUBLE,
            position_size DOUBLE,
            slippage_usd DOUBLE,
            fees_usd DOUBLE,
            pnl_usd DOUBLE,
            net_pnl_usd DOUBLE,
            hold_time_ms BIGINT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forward_ev_ledger (
            id VARCHAR PRIMARY KEY,
            prediction_id VARCHAR,
            source VARCHAR,
            timestamp BIGINT,
            horizon INT,
            entry_price DOUBLE,
            target_price DOUBLE,
            expected_move DOUBLE,
            confidence DOUBLE,
            raw_direction VARCHAR,
            final_direction VARCHAR,
            trade_verdict VARCHAR,
            action VARCHAR,
            notional_usd DOUBLE,
            fee_bps DOUBLE,
            slippage_bps DOUBLE,
            no_trade_reasons_json VARCHAR DEFAULT '[]',
            setup_quality_json VARCHAR DEFAULT '{}',
            resolved BOOLEAN DEFAULT FALSE,
            exit_price DOUBLE DEFAULT NULL,
            actual_move DOUBLE DEFAULT NULL,
            actual_direction VARCHAR DEFAULT '',
            direction_hit BOOLEAN DEFAULT NULL,
            gross_pnl_usd DOUBLE DEFAULT NULL,
            fees_usd DOUBLE DEFAULT NULL,
            slippage_usd DOUBLE DEFAULT NULL,
            net_pnl_usd DOUBLE DEFAULT NULL,
            avoided_loss_usd DOUBLE DEFAULT NULL,
            opportunity_cost_usd DOUBLE DEFAULT NULL,
            resolved_at BIGINT DEFAULT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS polymarket_markets (
            market_id VARCHAR PRIMARY KEY,
            condition_id VARCHAR,
            slug VARCHAR,
            question VARCHAR,
            yes_token VARCHAR,
            no_token VARCHAR,
            end_date VARCHAR,
            reference_price DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS polymarket_quotes (
            market_id VARCHAR,
            timestamp BIGINT,
            yes_bid DOUBLE,
            yes_ask DOUBLE,
            no_bid DOUBLE,
            no_ask DOUBLE,
            spread DOUBLE,
            order_book_imbalance DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS polymarket_predictions (
            prediction_id VARCHAR PRIMARY KEY,
            market_id VARCHAR,
            horizon INT,
            prediction_time BIGINT,
            seconds_remaining BIGINT,
            btc_price DOUBLE,
            reference_price DOUBLE,
            market_yes_bid DOUBLE,
            market_yes_ask DOUBLE,
            model_fair_yes DOUBLE,
            probability_interval_low DOUBLE,
            probability_interval_high DOUBLE,
            yes_edge DOUBLE,
            no_edge DOUBLE,
            action VARCHAR,
            skip_reason VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS polymarket_outcomes (
            market_id VARCHAR PRIMARY KEY,
            resolution BOOLEAN,
            resolved_at BIGINT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS polymarket_paper_trades (
            trade_id VARCHAR PRIMARY KEY,
            prediction_id VARCHAR,
            market_id VARCHAR,
            action VARCHAR,
            fill_price DOUBLE,
            size DOUBLE,
            fees DOUBLE,
            slippage DOUBLE,
            exit_price DOUBLE,
            net_pnl DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_snapshots (
            timestamp BIGINT PRIMARY KEY,
            price DOUBLE,
            regime VARCHAR,
            boot_seconds DOUBLE,
            signal_history_snapshots INT,
            signal_history_coverage_pct DOUBLE,
            resolved_total INT,
            pending_total INT,
            action_summary_json VARCHAR,
            horizon_accuracy_json VARCHAR,
            error_summary_json VARCHAR,
            drift_json VARCHAR,
            support_resistance_json VARCHAR DEFAULT '{}',
            indicator_snapshot_json VARCHAR DEFAULT '{}',
            kronos_status_json VARCHAR DEFAULT '{}',
            fsr_ppo_json VARCHAR DEFAULT '{}',
            notes VARCHAR
        )
    """)
    for ddl in [
        "ADD COLUMN support_resistance_json VARCHAR DEFAULT '{}'",
        "ADD COLUMN indicator_snapshot_json VARCHAR DEFAULT '{}'",
        "ADD COLUMN kronos_status_json VARCHAR DEFAULT '{}'",
        "ADD COLUMN fsr_ppo_json VARCHAR DEFAULT '{}'",
    ]:
        try:
            conn.execute(f"ALTER TABLE analysis_snapshots {ddl};")
        except Exception:
            pass
    # Durable A/B testing relational schema
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ab_experiments (
            experiment_id VARCHAR PRIMARY KEY,
            primary_model_version VARCHAR,
            challenger_model_version VARCHAR,
            started_at BIGINT,
            completed_at BIGINT,
            status VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ab_predictions (
            pred_id VARCHAR PRIMARY KEY,
            experiment_id VARCHAR,
            horizon INT,
            regime VARCHAR,
            prediction_timestamp BIGINT,
            primary_signal VARCHAR,
            primary_prob_up DOUBLE,
            primary_prob_down DOUBLE,
            challenger_signal VARCHAR,
            challenger_prob_up DOUBLE,
            challenger_prob_down DOUBLE,
            model_bundle_id VARCHAR DEFAULT '',
            feature_schema_hash VARCHAR DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ab_outcomes (
            pred_id VARCHAR PRIMARY KEY,
            experiment_id VARCHAR,
            actual_outcome VARCHAR,
            cost_adjusted_pnl DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ab_promotions (
            experiment_id VARCHAR PRIMARY KEY,
            promoted_at BIGINT,
            old_primary VARCHAR,
            new_primary VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ab_results (
            id VARCHAR PRIMARY KEY,
            variant VARCHAR,
            pred_id VARCHAR,
            timestamp BIGINT,
            horizon INT,
            direction VARCHAR,
            confidence DOUBLE,
            actual_direction VARCHAR,
            hit BOOLEAN,
            resolved BOOLEAN DEFAULT FALSE
        )
    """)
    for ddl in [
        "ADD COLUMN variant VARCHAR",
        "ADD COLUMN pred_id VARCHAR",
        "ADD COLUMN timestamp BIGINT",
        "ADD COLUMN horizon INT",
        "ADD COLUMN direction VARCHAR",
        "ADD COLUMN confidence DOUBLE",
        "ADD COLUMN actual_direction VARCHAR",
        "ADD COLUMN hit BOOLEAN",
        "ADD COLUMN resolved BOOLEAN DEFAULT FALSE",
        "ADD COLUMN model_bundle_id VARCHAR DEFAULT ''",
        "ADD COLUMN feature_schema_hash VARCHAR DEFAULT ''",
    ]:
        try:
            conn.execute(f"ALTER TABLE ab_results {ddl};")
        except Exception:
            pass
    # B1 (2026-06-13): live per-bar feature vector log. Closes the train/serve gap —
    # the high-edge microstructure features are constant in the historical training
    # matrix (one live snapshot broadcast over 50d); logging them live, keyed by the
    # cycle ts, lets a FUTURE retrain join (features -> realized outcome) on
    # predictions_{h}m.timestamp. Outcomes already persist there, so no resolution hook.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feature_outcome_log (
            ts BIGINT PRIMARY KEY,
            schema_hash VARCHAR,
            regime VARCHAR,
            features DOUBLE[]
        )
    """)
    # A4 (2026-06-13): live PERP per-1m-bar CVD — the parity twin of build_crossvenue_flow's
    # offline perp leg (spot leg already covered by trade_features live+backfill). UNION live +
    # offline at retrain to form the spot-vs-perp divergence feature. NOT yet a model feature
    # (no schema bump) — recording first so a future retrain can add the slots with parity.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS perp_cvd_live (
            ts BIGINT PRIMARY KEY,
            cvd_perp DOUBLE,
            vol_perp DOUBLE,
            perp_price DOUBLE
        )
    """)
    # GEX (2026-06-13): net dealer gamma exposure from Deribit BTC options — a REGIME
    # signal (positive=pinned/mean-reverting, negative=trending/explosive) that is NOT
    # price- or order-book-derived. Logged ~once per recording cycle. A future retrain
    # adds it as a slowly-varying feature (its live==serve value IS the recorded one, so
    # train/serve parity holds once it's in FEATURE_NAMES). NO schema bump now (side table).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gex_live (
            ts BIGINT PRIMARY KEY,
            gex DOUBLE,
            total_gamma DOUBLE,
            spot DOUBLE,
            pcr DOUBLE,
            atm_iv DOUBLE
        )
    """)
    # A1 (2026-06-13): intra-window persistence snapshots for the late-entry/T3 model —
    # "price is `distance` past the line with `seconds_left` s left, on `position` side:
    # does it HOLD to close?". Logged ~every 15s per open round from the Pyth (settlement)
    # tracker. Label is derived at TRAIN time by joining round_id -> price_to_beat.
    # actual_direction (B1 pattern: log raw, label from the already-persisted outcome).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS persistence_snapshot (
            round_id VARCHAR,
            horizon INT,
            ts BIGINT,
            seconds_left INT,
            distance DOUBLE,
            position VARCHAR,
            vol_60s_pct DOUBLE,
            p_hold DOUBLE
        )
    """)
    # Additive migration (2026-06-14): persist trailing-60s vol + the live calibrated P(hold)
    # at snapshot time, so the live P(hold) tier is EXACTLY gradeable from the DB (not just the
    # structural distance/time zone the §5bw validation reconstructed). Crash-safe no-op if present.
    for _ddl in ["ADD COLUMN vol_60s_pct DOUBLE", "ADD COLUMN p_hold DOUBLE"]:
        try:
            conn.execute(f"ALTER TABLE persistence_snapshot {_ddl}")
        except Exception:
            pass
    # A10 (2026-06-13): per-prediction setup FINGERPRINT — the DECISION context (regime,
    # conviction, agreement, grade, CVD, GEX) keyed by (ts,horizon), joinable to
    # predictions_{h}m for the outcome. Two no-retrain payoffs: (1) the evidence layer for
    # the kNN voter + T3 "similar setups" gate; (2) lets us MEASURE which signals (GEX / CVD /
    # grade) actually have edge BEFORE promoting any to a live gate (measure-before-gate).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS setup_fingerprint (
            ts BIGINT,
            horizon INT,
            regime VARCHAR,
            raw_direction VARCHAR,
            conviction DOUBLE,
            agreement DOUBLE,
            confidence DOUBLE,
            grade VARCHAR,
            cvd_1m DOUBLE,
            gex DOUBLE,
            expected_move DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feature_retirement_events (
            feature VARCHAR,
            retired_at BIGINT,
            restored_at BIGINT,
            model_version VARCHAR,
            reason VARCHAR,
            threshold DOUBLE,
            lookback_days INT,
            pre_retirement_shap DOUBLE,
            status VARCHAR
        )
    """)
    # Kronos directional forecast verification: did the Kronos price path's direction
    # at each horizon actually come true? Lets us compare Kronos vs our ensemble.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kronos_predictions (
            id VARCHAR PRIMARY KEY,
            timestamp BIGINT,
            horizon INT,
            ref_price DOUBLE,
            forecast_price DOUBLE,
            direction VARCHAR,
            verify_at BIGINT,
            actual_price DOUBLE,
            actual_direction VARCHAR DEFAULT '',
            hit BOOLEAN,
            resolved BOOLEAN DEFAULT FALSE
        )
    """)
    # Per-model directional accuracy: one row per base model per recorded prediction.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_predictions (
            id VARCHAR PRIMARY KEY,
            model VARCHAR,
            timestamp BIGINT,
            horizon INT,
            ref_price DOUBLE,
            direction VARCHAR,
            verify_at BIGINT,
            actual_price DOUBLE,
            actual_direction VARCHAR DEFAULT '',
            hit BOOLEAN,
            resolved BOOLEAN DEFAULT FALSE
        )
    """)
    # Offline replay rows are deliberately separate from live predictions_*m tables.
    # They are useful for calibration/backtest research but must never contaminate
    # live accuracy, because replay cannot reproduce feed outages, latency, or slippage.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_replay_predictions (
            id VARCHAR PRIMARY KEY,
            replay_run_id VARCHAR,
            timestamp BIGINT,
            horizon INT,
            model_bundle_id VARCHAR DEFAULT '',
            feature_schema_hash VARCHAR DEFAULT '',
            model_raw_direction VARCHAR DEFAULT '',
            final_direction VARCHAR DEFAULT '',
            trade_verdict VARCHAR DEFAULT '',
            confidence DOUBLE DEFAULT 0.0,
            expected_move DOUBLE DEFAULT 0.0,
            target_price DOUBLE DEFAULT 0.0,
            actual_price DOUBLE DEFAULT NULL,
            actual_move DOUBLE DEFAULT NULL,
            direction_hit BOOLEAN DEFAULT NULL,
            price_match BOOLEAN DEFAULT NULL,
            move_error DOUBLE DEFAULT NULL,
            no_trade_reasons_json VARCHAR DEFAULT '[]',
            setup_quality_json VARCHAR DEFAULT '{}',
            created_at BIGINT
        )
    """)
    # 5m/15m "price to beat" rounds: lock a reference price, record our UP/DOWN call,
    # then resolve vs the realized move (self-contained replacement for the PM engine).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_to_beat (
            id VARCHAR PRIMARY KEY,
            timestamp BIGINT,
            horizon INT,
            price_to_beat DOUBLE,
            our_direction VARCHAR,
            signal VARCHAR,
            conviction DOUBLE,
            actionable BOOLEAN DEFAULT FALSE,
            kronos_direction VARCHAR DEFAULT '',
            target_price DOUBLE,
            verify_at BIGINT,
            actual_price DOUBLE,
            actual_direction VARCHAR DEFAULT '',
            hit BOOLEAN,
            move DOUBLE,
            resolved BOOLEAN DEFAULT FALSE
        )
    """)
    # Additive migration: which rule produced the price-to-beat lean ("model" = committed
    # 3-class lean, "fallback" = two-way probability tilt). Lets the win-rate split survive
    # restarts (the tracker rehydrates its history from this table at boot).
    try:
        conn.execute("ALTER TABLE price_to_beat ADD COLUMN lean_source VARCHAR DEFAULT ''")
    except Exception:
        pass
    # Additive migration: persist the setup grade (A/B/C) and the late-entry flag so
    # grade-discipline win rates are measurable from the DB, not just the small
    # in-memory recent-rounds buffer.
    for _ddl in ["ADD COLUMN confluence_grade VARCHAR DEFAULT ''",
                 "ADD COLUMN late_entry BOOLEAN DEFAULT FALSE"]:
        try:
            conn.execute(f"ALTER TABLE price_to_beat {_ddl}")
        except Exception:
            pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fsr_ppo_decisions (
            id VARCHAR PRIMARY KEY,
            prediction_id VARCHAR,
            timestamp BIGINT,
            horizon INT,
            price DOUBLE,
            action VARCHAR,
            side VARCHAR,
            size_fraction DOUBLE,
            confidence DOUBLE,
            expected_reward_usd DOUBLE,
            reason VARCHAR,
            risk_note VARCHAR,
            fsr_json VARCHAR DEFAULT '{}',
            state_json VARCHAR DEFAULT '{}',
            verify_at BIGINT,
            actual_price DOUBLE,
            actual_direction VARCHAR DEFAULT '',
            reward_usd DOUBLE,
            hit BOOLEAN,
            resolved BOOLEAN DEFAULT FALSE
        )
    """)
    conn.close()


def log_exchange_verification(timestamp: int, horizon: int, direction: str,
                              confirmed: int, checked: int, venues: dict):
    """Durably record per-venue confirmation of a BTC directional call."""
    import json as _json
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS exchange_verifications (
                timestamp BIGINT, horizon INT, direction VARCHAR,
                confirmed INT, checked INT, confirmation_rate DOUBLE, venues_json VARCHAR
            )
        """)
        rate = round(confirmed / checked, 4) if checked else 0.0
        conn.execute("""
            INSERT INTO exchange_verifications
            (timestamp, horizon, direction, confirmed, checked, confirmation_rate, venues_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (timestamp, horizon, direction, confirmed, checked, rate, _json.dumps(venues)))
    except Exception as e:
        print(f"DuckDB Exchange Verify Error: {e}")
    finally:
        if conn:
            conn.close()


def log_perp_cvd_bar(ts: int, cvd_perp: float, vol_perp: float, perp_price: float):
    """A4: persist one finalized live PERP 1m-bar CVD (parity twin of the offline cross-flow
    perp leg). INSERT OR IGNORE dedupes a re-emitted bar. Crash-safe."""
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT OR IGNORE INTO perp_cvd_live (ts, cvd_perp, vol_perp, perp_price)
            VALUES (?, ?, ?, ?)
        """, (int(ts), float(cvd_perp), float(vol_perp), float(perp_price)))
    except Exception as e:
        print(f"DuckDB perp-cvd Insert Error: {e}")
    finally:
        if conn:
            conn.close()


def log_gex(ts: int, gex: float, total_gamma: float, spot: float,
            pcr: float, atm_iv: float):
    """Persist one net-dealer-gamma-exposure sample (+ PCR/ATM-IV context). INSERT OR
    IGNORE dedupes the once-per-cycle ts key. Crash-safe — a logging failure must never
    affect serving. A future retrain joins/aligns on ts to add GEX as a feature."""
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT OR IGNORE INTO gex_live (ts, gex, total_gamma, spot, pcr, atm_iv)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (int(ts), float(gex), float(total_gamma), float(spot),
              float(pcr), float(atm_iv)))
    except Exception as e:
        print(f"DuckDB GEX Insert Error: {e}")
    finally:
        if conn:
            conn.close()


def log_setup_fingerprint(ts: int, horizon: int, regime: str, raw_direction: str,
                          conviction: float, agreement: float, confidence: float, grade: str,
                          cvd_1m: float, gex: float, expected_move: float):
    """A10: persist one per-prediction setup fingerprint (decision context). Joins to
    predictions_{h}m on (ts,horizon) for the outcome at train/measure time. Crash-safe."""
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT INTO setup_fingerprint
            (ts, horizon, regime, raw_direction, conviction, agreement, confidence, grade,
             cvd_1m, gex, expected_move)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (int(ts), int(horizon), str(regime), str(raw_direction), float(conviction),
              float(agreement), float(confidence), str(grade), float(cvd_1m), float(gex),
              float(expected_move)))
    except Exception as e:
        print(f"DuckDB setup-fingerprint Insert Error: {e}")
    finally:
        if conn:
            conn.close()


def log_persistence_snapshot(round_id: str, horizon: int, ts: int,
                             seconds_left: int, distance: float, position: str,
                             vol_60s_pct: float = None, p_hold: float = None):
    """A1: append one intra-window persistence snapshot. Crash-safe; the price-to-beat
    tracker dedupes to ~15s per round. Outcome/label joined at TRAIN time via round_id.
    vol_60s_pct + p_hold (2026-06-14) make the live P(hold) tier exactly gradeable."""
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT INTO persistence_snapshot
            (round_id, horizon, ts, seconds_left, distance, position, vol_60s_pct, p_hold)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(round_id), int(horizon), int(ts), int(seconds_left),
              float(distance), str(position),
              (float(vol_60s_pct) if vol_60s_pct is not None else None),
              (float(p_hold) if p_hold is not None else None)))
    except Exception as e:
        print(f"DuckDB persistence-snapshot Insert Error: {e}")
    finally:
        if conn:
            conn.close()


def log_feature_vector(ts: int, schema_hash: str, regime: str, features: list):
    """B1: persist the live per-bar feature vector keyed by the cycle timestamp
    (== predictions_{h}m.timestamp). A future retrain joins this on ts to pair the
    real (live-varying) microstructure features with the realized outcome already
    stored in predictions_*. INSERT OR IGNORE dedupes the once-per-cycle key.
    Crash-safe: never raises into the serving loop."""
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT OR IGNORE INTO feature_outcome_log (ts, schema_hash, regime, features)
            VALUES (?, ?, ?, ?)
        """, (int(ts), str(schema_hash), str(regime), list(features)))
    except Exception as e:
        print(f"DuckDB feature-log Insert Error: {e}")
    finally:
        if conn:
            conn.close()


def log_kronos_prediction(pred_id: str, timestamp: int, horizon: int, ref_price: float,
                          forecast_price: float, direction: str, verify_at: int):
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT OR REPLACE INTO kronos_predictions
            (id, timestamp, horizon, ref_price, forecast_price, direction, verify_at, resolved)
            VALUES (?, ?, ?, ?, ?, ?, ?, FALSE)
        """, (pred_id, timestamp, horizon, ref_price, forecast_price, direction, verify_at))
    except Exception as e:
        print(f"DuckDB Kronos Insert Error: {e}")
    finally:
        if conn:
            conn.close()


def resolve_kronos_prediction(pred_id: str, actual_price: float, actual_direction: str, hit: bool):
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            UPDATE kronos_predictions
            SET actual_price = ?, actual_direction = ?, hit = ?, resolved = TRUE
            WHERE id = ?
        """, (actual_price, actual_direction, hit, pred_id))
    except Exception as e:
        print(f"DuckDB Kronos Resolve Error: {e}")
    finally:
        if conn:
            conn.close()


def fetch_kronos_accuracy() -> dict:
    """Per-horizon resolved counts + accuracy for Kronos directional forecasts."""
    out = {}
    conn = None
    try:
        conn = _connect()
        df = conn.execute("""
            SELECT horizon,
                   COUNT(*) FILTER (WHERE resolved) AS total,
                   SUM(CASE WHEN resolved AND hit THEN 1 ELSE 0 END) AS hits
            FROM kronos_predictions
            GROUP BY horizon
        """).df()
        for r in df.to_dict("records"):
            t = int(r.get("total") or 0)
            hh = int(r.get("hits") or 0)
            out[int(r["horizon"])] = {"total": t, "hits": hh,
                                      "accuracy": round(hh / t, 4) if t else 0.0}
    except Exception as e:
        print(f"DuckDB Kronos Accuracy Error: {e}")
    finally:
        if conn:
            conn.close()
    return out


# ──────────────────────────────────────────────────────────────────────────
#  Per-model directional verification
# ──────────────────────────────────────────────────────────────────────────
def log_model_prediction(pred_id: str, model: str, timestamp: int, horizon: int,
                         ref_price: float, direction: str, verify_at: int):
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT OR REPLACE INTO model_predictions
            (id, model, timestamp, horizon, ref_price, direction, verify_at, resolved)
            VALUES (?, ?, ?, ?, ?, ?, ?, FALSE)
        """, (pred_id, model, timestamp, horizon, ref_price, direction, verify_at))
    except Exception as e:
        print(f"DuckDB Model Insert Error: {e}")
    finally:
        if conn:
            conn.close()


def resolve_model_prediction(pred_id: str, actual_price: float, actual_direction: str, hit: bool):
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            UPDATE model_predictions
            SET actual_price = ?, actual_direction = ?, hit = ?, resolved = TRUE
            WHERE id = ?
        """, (actual_price, actual_direction, hit, pred_id))
    except Exception as e:
        print(f"DuckDB Model Resolve Error: {e}")
    finally:
        if conn:
            conn.close()


def fetch_model_accuracy() -> dict:
    """Per-model, per-horizon resolved counts + accuracy. -> {model: {horizon: {...}}}."""
    out = {}
    conn = None
    try:
        conn = _connect()
        df = conn.execute("""
            SELECT model, horizon,
                   COUNT(*) FILTER (WHERE resolved) AS total,
                   SUM(CASE WHEN resolved AND hit THEN 1 ELSE 0 END) AS hits
            FROM model_predictions
            GROUP BY model, horizon
        """).df()
        for r in df.to_dict("records"):
            t = int(r.get("total") or 0)
            hh = int(r.get("hits") or 0)
            m = str(r["model"])
            out.setdefault(m, {})[int(r["horizon"])] = {
                "total": t, "hits": hh,
                "accuracy": round(hh / t, 4) if t else 0.0,
            }
    except Exception as e:
        print(f"DuckDB Model Accuracy Error: {e}")
    finally:
        if conn:
            conn.close()
    return out


# ──────────────────────────────────────────────────────────────────────────
#  Price-to-beat 5m/15m rounds
# ──────────────────────────────────────────────────────────────────────────
def log_historical_replay_prediction(entry: dict):
    """Persist one offline replay prediction outside live accuracy tables."""
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT OR REPLACE INTO historical_replay_predictions
            (id, replay_run_id, timestamp, horizon, model_bundle_id, feature_schema_hash,
             model_raw_direction, final_direction, trade_verdict, confidence,
             expected_move, target_price, actual_price, actual_move, direction_hit,
             price_match, move_error, no_trade_reasons_json, setup_quality_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.get("id"),
            entry.get("replay_run_id", ""),
            int(entry.get("timestamp") or 0),
            int(entry.get("horizon") or 0),
            entry.get("model_bundle_id", ""),
            entry.get("feature_schema_hash", ""),
            entry.get("model_raw_direction", ""),
            entry.get("final_direction", ""),
            entry.get("trade_verdict", ""),
            float(entry.get("confidence") or 0.0),
            float(entry.get("expected_move") or 0.0),
            float(entry.get("target_price") or 0.0),
            (float(entry["actual_price"]) if entry.get("actual_price") is not None else None),
            (float(entry["actual_move"]) if entry.get("actual_move") is not None else None),
            entry.get("direction_hit"),
            entry.get("price_match"),
            (float(entry["move_error"]) if entry.get("move_error") is not None else None),
            json.dumps(entry.get("no_trade_reasons") or []),
            json.dumps(entry.get("setup_quality") or {}),
            int(entry.get("created_at") or time.time() * 1000),
        ))
    except Exception as e:
        print(f"DuckDB Historical Replay Insert Error: {e}")
    finally:
        if conn:
            conn.close()


def fetch_historical_replay_summary(limit: int = 50) -> dict:
    out = {"summary": {}, "recent": []}
    conn = None
    try:
        conn = _connect()
        rows = conn.execute("""
            SELECT replay_run_id, horizon,
                   COUNT(*) AS row_count,
                   SUM(CASE
                        WHEN model_raw_direction IN ('UP', 'DOWN')
                         AND direction_hit IS NOT NULL THEN 1 ELSE 0 END) AS directional_n,
                   SUM(CASE
                        WHEN model_raw_direction IN ('UP', 'DOWN')
                         AND direction_hit THEN 1 ELSE 0 END) AS directional_hits,
                   SUM(CASE WHEN price_match THEN 1 ELSE 0 END) AS price_matches,
                   AVG(move_error) AS avg_move_error
            FROM historical_replay_predictions
            GROUP BY replay_run_id, horizon
            ORDER BY replay_run_id DESC, horizon
        """).fetchall()
        for run_id, h, row_count, directional_n, directional_hits, price_matches, avg_err in rows:
            row_count = int(row_count or 0)
            directional_n = int(directional_n or 0)
            directional_hits = int(directional_hits or 0)
            price_matches = int(price_matches or 0)
            out["summary"].setdefault(str(run_id), {})[int(h)] = {
                "rows": row_count,
                "directional_n": directional_n,
                "directional_hits": directional_hits,
                "directional_accuracy": round(directional_hits / directional_n, 4) if directional_n else None,
                "price_matches": price_matches,
                "price_match_rate": round(price_matches / directional_n, 4) if directional_n else None,
                "avg_move_error_usd": round(float(avg_err or 0.0), 2),
            }
        df = conn.execute("""
            SELECT replay_run_id, timestamp, horizon, model_raw_direction,
                   final_direction, trade_verdict, confidence, expected_move,
                   actual_move, direction_hit, move_error
            FROM historical_replay_predictions
            ORDER BY timestamp DESC
            LIMIT ?
        """, (int(limit),)).df()
        out["recent"] = df.to_dict("records")
    except Exception as e:
        out["error"] = str(e)
    finally:
        if conn:
            conn.close()
    return out


def log_forward_ev_event(entry: dict):
    """Log one live/paper forward-EV event keyed to a prediction id.

    This is not an execution fill. It is an auditable paper ledger that measures
    whether final TRADE calls had positive net value and whether AVOID/WEAK_LEAN
    decisions protected capital versus the raw model lean.
    """
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT OR REPLACE INTO forward_ev_ledger
            (id, prediction_id, source, timestamp, horizon, entry_price, target_price,
             expected_move, confidence, raw_direction, final_direction, trade_verdict,
             action, notional_usd, fee_bps, slippage_bps, no_trade_reasons_json,
             setup_quality_json, resolved)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE)
        """, (
            entry.get("id"),
            entry.get("prediction_id"),
            entry.get("source", "ensemble"),
            int(entry.get("timestamp") or 0),
            int(entry.get("horizon") or 0),
            float(entry.get("entry_price") or 0.0),
            float(entry.get("target_price") or 0.0),
            float(entry.get("expected_move") or 0.0),
            float(entry.get("confidence") or 0.0),
            entry.get("raw_direction", ""),
            entry.get("final_direction", ""),
            entry.get("trade_verdict", ""),
            entry.get("action", ""),
            float(entry.get("notional_usd") or 0.0),
            float(entry.get("fee_bps") or 0.0),
            float(entry.get("slippage_bps") or 0.0),
            json.dumps(entry.get("no_trade_reasons") or []),
            json.dumps(entry.get("setup_quality") or {}),
        ))
    except Exception as e:
        print(f"DuckDB Forward-EV Insert Error: {e}")
    finally:
        if conn:
            conn.close()


def resolve_forward_ev_event(prediction_id: str, exit_price: float, actual_move: float,
                             actual_direction: str, direction_hit: bool,
                             resolved_at: int = None):
    conn = None
    try:
        conn = _connect()
        rows = conn.execute("""
            SELECT id, entry_price, raw_direction, final_direction, trade_verdict,
                   action, notional_usd, fee_bps, slippage_bps
            FROM forward_ev_ledger
            WHERE prediction_id = ? AND resolved = FALSE
        """, (prediction_id,)).fetchall()
        for row in rows:
            (event_id, entry_price, raw_dir, final_dir, verdict, action,
             notional, fee_bps, slip_bps) = row
            entry_price = float(entry_price or 0.0)
            exit_price_f = float(exit_price or 0.0)
            notional = float(notional or 0.0)
            fee_bps = float(fee_bps or 0.0)
            slip_bps = float(slip_bps or 0.0)
            fees = notional * (fee_bps / 10000.0) * 2.0
            slippage = notional * (slip_bps / 10000.0) * 2.0

            gross = None
            net = None
            avoided_loss = None
            opportunity_cost = None

            def _pnl_for(side: str) -> float:
                if entry_price <= 0 or notional <= 0 or side not in ("UP", "DOWN"):
                    return 0.0
                ret = (exit_price_f - entry_price) / entry_price
                if side == "DOWN":
                    ret = -ret
                return notional * ret

            if action == "TRADE" and final_dir in ("UP", "DOWN"):
                gross = _pnl_for(final_dir)
                net = gross - fees - slippage
            else:
                hypo_gross = _pnl_for(raw_dir)
                hypo_net = hypo_gross - fees - slippage
                avoided_loss = max(0.0, -hypo_net)
                opportunity_cost = max(0.0, hypo_net)

            conn.execute("""
                UPDATE forward_ev_ledger
                SET resolved = TRUE, exit_price = ?, actual_move = ?, actual_direction = ?,
                    direction_hit = ?, gross_pnl_usd = ?, fees_usd = ?, slippage_usd = ?,
                    net_pnl_usd = ?, avoided_loss_usd = ?, opportunity_cost_usd = ?,
                    resolved_at = ?
                WHERE id = ?
            """, (
                float(exit_price or 0.0),
                float(actual_move or 0.0),
                actual_direction or "",
                direction_hit,
                gross,
                fees,
                slippage,
                net,
                avoided_loss,
                opportunity_cost,
                int(resolved_at or time.time() * 1000),
                event_id,
            ))
    except Exception as e:
        print(f"DuckDB Forward-EV Resolve Error: {e}")
    finally:
        if conn:
            conn.close()


def fetch_forward_ev_summary(limit: int = 50) -> dict:
    out = {"summary": {}, "recent": []}
    conn = None
    try:
        conn = _connect()
        rows = conn.execute("""
            SELECT action,
                   COUNT(*) FILTER (WHERE resolved) AS n,
                   SUM(CASE WHEN resolved AND COALESCE(net_pnl_usd, 0) > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN resolved THEN COALESCE(net_pnl_usd, 0) ELSE 0 END) AS net_pnl,
                   SUM(CASE WHEN resolved THEN COALESCE(avoided_loss_usd, 0) ELSE 0 END) AS avoided,
                   SUM(CASE WHEN resolved THEN COALESCE(opportunity_cost_usd, 0) ELSE 0 END) AS opp_cost,
                   AVG(CASE WHEN resolved THEN COALESCE(net_pnl_usd, 0) ELSE NULL END) AS avg_net
            FROM forward_ev_ledger
            GROUP BY action
        """).fetchall()
        total_net = 0.0
        total_avoided = 0.0
        total_opp = 0.0
        total_n = 0
        for action, n, wins, net, avoided, opp, avg_net in rows:
            n = int(n or 0)
            wins = int(wins or 0)
            net = float(net or 0.0)
            avoided = float(avoided or 0.0)
            opp = float(opp or 0.0)
            total_n += n
            total_net += net
            total_avoided += avoided
            total_opp += opp
            out["summary"][str(action or "UNKNOWN")] = {
                "n": n,
                "wins": wins,
                "win_rate": round(wins / n, 4) if n else None,
                "net_pnl_usd": round(net, 2),
                "avg_net_pnl_usd": round(float(avg_net or 0.0), 2),
                "avoided_loss_usd": round(avoided, 2),
                "opportunity_cost_usd": round(opp, 2),
            }
        out["totals"] = {
            "resolved": total_n,
            "net_pnl_usd": round(total_net, 2),
            "avoided_loss_usd": round(total_avoided, 2),
            "opportunity_cost_usd": round(total_opp, 2),
        }
        df = conn.execute("""
            SELECT timestamp, horizon, action, raw_direction, final_direction,
                   trade_verdict, entry_price, exit_price, actual_direction,
                   net_pnl_usd, avoided_loss_usd, opportunity_cost_usd, resolved
            FROM forward_ev_ledger
            ORDER BY timestamp DESC
            LIMIT ?
        """, (int(limit),)).df()
        out["recent"] = df.to_dict("records")
    except Exception as e:
        out["error"] = str(e)
    finally:
        if conn:
            conn.close()
    return out


def log_price_to_beat(entry: dict):
    conn = None
    try:
        conn = _connect()
        cfl = entry.get("confluence") or {}
        grade = str(cfl.get("grade", "")) if isinstance(cfl, dict) else ""
        conn.execute("""
            INSERT OR REPLACE INTO price_to_beat
            (id, timestamp, horizon, price_to_beat, our_direction, signal, conviction,
             actionable, kronos_direction, target_price, verify_at, lean_source,
             confluence_grade, resolved)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE)
        """, (
            entry["id"], entry["timestamp"], entry["horizon"], entry["price_to_beat"],
            entry["our_direction"], entry.get("signal", ""), float(entry.get("conviction", 0.0)),
            bool(entry.get("actionable", False)), entry.get("kronos_direction", ""),
            entry.get("target_price"), entry["verify_at"], entry.get("lean_source", ""),
            grade,
        ))
    except Exception as e:
        print(f"DuckDB PriceToBeat Insert Error: {e}")
    finally:
        if conn:
            conn.close()


def resolve_price_to_beat(pred_id: str, actual_price: float, actual_direction: str,
                          hit: bool, move: float, late_entry: bool = False):
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            UPDATE price_to_beat
            SET actual_price = ?, actual_direction = ?, hit = ?, move = ?,
                late_entry = ?, resolved = TRUE
            WHERE id = ?
        """, (actual_price, actual_direction, hit, move, bool(late_entry), pred_id))
    except Exception as e:
        print(f"DuckDB PriceToBeat Resolve Error: {e}")
    finally:
        if conn:
            conn.close()


def fetch_price_to_beat_accuracy() -> dict:
    """Per-horizon resolved counts + accuracy for price-to-beat rounds."""
    out = {}
    conn = None
    try:
        conn = _connect()
        df = conn.execute("""
            SELECT horizon,
                   COUNT(*) FILTER (WHERE resolved) AS total,
                   SUM(CASE WHEN resolved AND hit THEN 1 ELSE 0 END) AS hits
            FROM price_to_beat
            GROUP BY horizon
        """).df()
        for r in df.to_dict("records"):
            t = int(r.get("total") or 0)
            hh = int(r.get("hits") or 0)
            out[int(r["horizon"])] = {"total": t, "hits": hh,
                                      "accuracy": round(hh / t, 4) if t else 0.0}
    except Exception as e:
        print(f"DuckDB PriceToBeat Accuracy Error: {e}")
    finally:
        if conn:
            conn.close()
    return out


# ──────────────────────────────────────────────────────────────────────────
#  Action / trade log (unified view of recorded predictions, latest first)
# ──────────────────────────────────────────────────────────────────────────
def log_fsr_ppo_decision(entry: dict):
    """Record the FSR-PPO challenger action for later reward verification."""
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT OR REPLACE INTO fsr_ppo_decisions
            (id, prediction_id, timestamp, horizon, price, action, side, size_fraction,
             confidence, expected_reward_usd, reason, risk_note, fsr_json, state_json,
             verify_at, resolved)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE)
        """, (
            entry.get("id"),
            entry.get("prediction_id"),
            int(entry.get("timestamp") or int(time.time() * 1000)),
            int(entry.get("horizon") or 0),
            float(entry.get("price") or 0.0),
            entry.get("action") or "AVOID",
            entry.get("side") or "AVOID",
            float(entry.get("size_fraction") or 0.0),
            float(entry.get("confidence") or 0.0),
            float(entry.get("expected_reward_usd") or 0.0),
            entry.get("reason") or "",
            entry.get("risk_note") or "",
            json.dumps(entry.get("fsr") or {}),
            json.dumps(entry.get("state") or {}),
            int(entry.get("verify_at") or 0),
        ))
    except Exception as e:
        print(f"DuckDB FSR-PPO Insert Error: {e}")
    finally:
        if conn:
            conn.close()


def resolve_fsr_ppo_decision(prediction_id: str, actual_price: float, actual_direction: str):
    """Resolve one recorded PPO challenger action by the parent prediction id."""
    conn = None
    try:
        conn = _connect()
        row = conn.execute("""
            SELECT id, price, action, side, size_fraction, state_json
            FROM fsr_ppo_decisions
            WHERE prediction_id = ? AND resolved = FALSE
            LIMIT 1
        """, (prediction_id,)).fetchone()
        if not row:
            return
        dec_id, entry_price, action, side, size_fraction, state_json = row
        entry_price = float(entry_price or 0.0)
        actual_price = float(actual_price or 0.0)
        size_fraction = float(size_fraction or 0.0)
        move = actual_price - entry_price
        cost = entry_price * 0.0005 * size_fraction
        if side == "BUY":
            reward = move * size_fraction - cost
            hit = actual_direction == "UP"
        elif side == "SELL":
            reward = -move * size_fraction - cost
            hit = actual_direction == "DOWN"
        else:
            try:
                state = json.loads(state_json or "{}")
            except Exception:
                state = {}
            expected = abs(float(state.get("expected_move_usd") or 0.0))
            if abs(move) < max(5.0, expected * 0.35):
                reward = 0.05
                hit = True
            else:
                reward = -0.05
                hit = False
        conn.execute("""
            UPDATE fsr_ppo_decisions
            SET actual_price = ?, actual_direction = ?, reward_usd = ?,
                hit = ?, resolved = TRUE
            WHERE id = ?
        """, (actual_price, actual_direction, float(reward), bool(hit), dec_id))
    except Exception as e:
        print(f"DuckDB FSR-PPO Resolve Error: {e}")
    finally:
        if conn:
            conn.close()


def fetch_fsr_ppo_summary(limit: int = 20) -> dict:
    """Return recent PPO challenger performance and actions."""
    out = {
        "total": 0,
        "hits": 0,
        "accuracy": 0.0,
        "avg_reward_usd": 0.0,
        "by_action": {},
        "recent": [],
    }
    conn = None
    try:
        conn = _connect()
        row = conn.execute("""
            SELECT COUNT(*) FILTER (WHERE resolved) AS total,
                   SUM(CASE WHEN resolved AND hit THEN 1 ELSE 0 END) AS hits,
                   AVG(CASE WHEN resolved THEN reward_usd ELSE NULL END) AS avg_reward
            FROM fsr_ppo_decisions
        """).fetchone()
        total = int(row[0] or 0) if row else 0
        hits = int(row[1] or 0) if row else 0
        out["total"] = total
        out["hits"] = hits
        out["accuracy"] = round(hits / total, 4) if total else 0.0
        out["avg_reward_usd"] = round(float(row[2] or 0.0), 4) if row else 0.0

        df = conn.execute("""
            SELECT action,
                   COUNT(*) FILTER (WHERE resolved) AS total,
                   SUM(CASE WHEN resolved AND hit THEN 1 ELSE 0 END) AS hits,
                   AVG(CASE WHEN resolved THEN reward_usd ELSE NULL END) AS avg_reward
            FROM fsr_ppo_decisions
            GROUP BY action
        """).df()
        for r in df.to_dict("records"):
            t = int(r.get("total") or 0)
            h = int(r.get("hits") or 0)
            action = str(r.get("action") or "AVOID")
            out["by_action"][action] = {
                "total": t,
                "hits": h,
                "accuracy": round(h / t, 4) if t else 0.0,
                "avg_reward_usd": round(float(r.get("avg_reward") or 0.0), 4),
            }

        rdf = conn.execute("""
            SELECT timestamp, horizon, price, action, side, size_fraction,
                   confidence, expected_reward_usd, reason, risk_note,
                   actual_price, actual_direction, reward_usd, hit, resolved
            FROM fsr_ppo_decisions
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,)).df()
        for r in rdf.to_dict("records"):
            out["recent"].append({
                "timestamp": int(r.get("timestamp") or 0),
                "horizon": int(r.get("horizon") or 0),
                "price": _f(r.get("price")),
                "action": str(r.get("action") or "AVOID"),
                "side": str(r.get("side") or "AVOID"),
                "size_fraction": _f(r.get("size_fraction")),
                "confidence": _f(r.get("confidence")),
                "expected_reward_usd": _f(r.get("expected_reward_usd")),
                "reason": str(r.get("reason") or ""),
                "risk_note": str(r.get("risk_note") or ""),
                "actual_price": _f(r.get("actual_price")),
                "actual_direction": str(r.get("actual_direction") or ""),
                "reward_usd": _f(r.get("reward_usd")),
                "hit": bool(r.get("hit")) if r.get("hit") is not None else None,
                "resolved": bool(r.get("resolved")) if r.get("resolved") is not None else False,
            })
    except Exception as e:
        print(f"DuckDB FSR-PPO Summary Error: {e}")
    finally:
        if conn:
            conn.close()
    return out


def fetch_price_to_beat_history(horizon: int, limit: int = 500) -> list:
    """Rehydrate the price-to-beat win-rate history after a restart: oldest-first
    [(hit_int, lean_source)] for resolved directional rounds. Rows from before the
    lean_source column default to 'model' (historically true: pre-fallback-era rounds
    were all committed model leans).

    ERA-FILTERED: only rounds graded since the CURRENT model bundle was saved. Mixing
    eras poisoned the strip (observed: 250 blended rounds showed 52% while the new
    model's fresh rounds told a different story) — a win rate must describe ONE model."""
    out = []
    conn = None
    try:
        min_ts = 0
        try:
            _vp = os.path.join(os.path.dirname(DB_PATH), "saved_models",
                               "architecture_version.pkl")
            if os.path.exists(_vp):
                min_ts = int(os.path.getmtime(_vp) * 1000)
        except Exception:
            pass
        conn = _connect()
        rows = conn.execute(f"""
            SELECT CASE WHEN hit THEN 1 ELSE 0 END, COALESCE(lean_source, '')
            FROM price_to_beat
            WHERE horizon = ? AND resolved AND our_direction IN ('UP','DOWN')
              AND timestamp >= {int(min_ts)}
            ORDER BY timestamp DESC LIMIT {int(limit)}
        """, (horizon,)).fetchall()
        out = [(int(h), (s if s in ("model", "fallback") else "model"))
               for h, s in reversed(rows)]
    except Exception as e:
        print(f"DuckDB PTB History Error: {e}")
    finally:
        if conn:
            conn.close()
    return out


def cleanup_orphan_pending_rows() -> dict:
    """Boot-time janitor: delete pending rows that can never resolve.

    price_to_beat / model_predictions / kronos_predictions keep their pending state
    in MEMORY only — after a restart, any row still resolved=FALSE whose verify_at
    has already passed is permanently dead (its resolver is gone). They don't poison
    metrics (readers filter resolved=TRUE) but accumulate forever. predictions_*m is
    deliberately NOT touched: the verifier rehydrates those pendings at boot.
    """
    out = {}
    cutoff = int((time.time() - 600) * 1000)  # 10-min grace for clock skew
    conn = None
    try:
        conn = _connect()
        for tbl in ("price_to_beat", "model_predictions", "kronos_predictions"):
            try:
                n = conn.execute(f"""
                    DELETE FROM {tbl}
                    WHERE resolved = FALSE AND verify_at > 0 AND verify_at < ?
                """, (cutoff,)).fetchone()
                out[tbl] = int(n[0]) if n else 0
            except Exception:
                out[tbl] = -1  # table missing/no column — skip silently
    except Exception as e:
        print(f"DuckDB orphan cleanup error: {e}")
    finally:
        if conn:
            conn.close()
    return out


def fetch_price_to_beat_recent(limit: int = 20) -> list:
    """Rehydrate the resolved-rounds UI table after a restart (newest-first round
    dicts shaped like the tracker's in-memory resolved rounds). Same model-era
    filter as fetch_price_to_beat_history — the table must describe ONE model."""
    out = []
    conn = None
    try:
        min_ts = 0
        try:
            _vp = os.path.join(os.path.dirname(DB_PATH), "saved_models",
                               "architecture_version.pkl")
            if os.path.exists(_vp):
                min_ts = int(os.path.getmtime(_vp) * 1000)
        except Exception:
            pass
        conn = _connect()
        rows = conn.execute(f"""
            SELECT id, timestamp, horizon, price_to_beat, our_direction,
                   COALESCE(lean_source, '') AS lean_source,
                   COALESCE(confluence_grade, '') AS grade,
                   actual_price, actual_direction, hit, move,
                   COALESCE(late_entry, FALSE) AS late_entry
            FROM price_to_beat
            WHERE resolved AND our_direction IN ('UP','DOWN')
              AND timestamp >= {int(min_ts)}
            ORDER BY timestamp DESC LIMIT {int(limit)}
        """).fetchall()
        for (rid, ts, h, ptb, d, src, grade, ap, ad, hit, move, late) in rows:
            out.append({
                "id": rid, "timestamp": int(ts), "horizon": int(h),
                "price_to_beat": float(ptb or 0.0), "our_direction": d,
                "lean_source": src if src in ("model", "fallback") else "model",
                "confluence": {"grade": grade} if grade else None,
                "actual_price": float(ap or 0.0), "actual_direction": ad,
                "hit": (bool(hit) if hit is not None else None),
                "move": float(move or 0.0), "late_entry": bool(late),
                "status": "resolved",
            })
    except Exception as e:
        print(f"DuckDB PTB Recent Error: {e}")
    finally:
        if conn:
            conn.close()
    return out


def fetch_action_log(limit: int = 50) -> list:
    """Union recent predictions across all horizons for the UI action feed."""
    timeframes = [1, 3, 5, 7, 10, 15, 30]
    selects = []
    for tf in timeframes:
        # Push the ORDER BY/LIMIT into each per-table subquery so DuckDB only reads
        # each table's recent tail instead of scanning every row, unioning, then sorting.
        # Cheap today (tables are tiny) but keeps this O(limit) as they grow over a
        # multi-week frozen run.
        # Parenthesize each branch: ORDER BY/LIMIT on an unparenthesized UNION-ALL branch is
        # ambiguous (it would bind to the whole compound), so each subquery is wrapped.
        selects.append(f"""(
            SELECT timestamp, horizon, signal, raw_direction, expected_move, target_price, binance_price,
                   confidence, conviction, actionable, regime, actual_price, actual_move,
                   hit, resolved, skip_reason
            FROM predictions_{tf}m ORDER BY timestamp DESC LIMIT {int(limit)}
        )""")
    union_sql = " UNION ALL ".join(selects)
    out = []
    # NOTE: must open in the SAME mode the live backend already holds (read-write).
    # On Windows the backend process keeps analytics.duckdb open RW; a read_only=True
    # connection raises a mode-mismatch in-process (and "file in use" cross-process),
    # so this stays default read-write. The retry loop in _connect handles lock races.
    conn = None
    try:
        conn = _connect()
        df = conn.execute(
            f"SELECT * FROM ({union_sql}) ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).df()
        for r in df.to_dict("records"):
            out.append({
                "timestamp": int(r.get("timestamp") or 0),
                "horizon": int(r.get("horizon") or 0),
                "signal": str(r.get("signal") or "NEUTRAL"),
                "raw_direction": str(r.get("raw_direction") or "NEUTRAL"),
                "expected_move": _f(r.get("expected_move")),
                "target_price": _f(r.get("target_price")),
                "reference_price": _f(r.get("binance_price")),
                "confidence": _f(r.get("confidence")),
                "conviction": _f(r.get("conviction")),
                "actionable": bool(r.get("actionable")) if r.get("actionable") is not None else False,
                "regime": str(r.get("regime") or "UNKNOWN"),
                "actual_price": _f(r.get("actual_price")),
                "actual_move": _f(r.get("actual_move")),
                "hit": (None if r.get("hit") is None else bool(r.get("hit"))),
                "resolved": bool(r.get("resolved")) if r.get("resolved") is not None else False,
                "skip_reason": str(r.get("skip_reason") or ""),
            })
    except Exception as e:
        print(f"DuckDB Action Log Error: {e}")
    finally:
        if conn:
            conn.close()
    return out


def _f(v):
    try:
        if v is None:
            return None
        f = float(v)
        # NaN/inf are valid Python floats but NOT JSON-compliant — json.dumps raises
        # "Out of range float values are not JSON compliant: nan", which surfaced as a
        # 500 on /api/action-log (unresolved rows carry NaN move_error/actual_move).
        # Normalize them to None so every consumer (HTTP + WS) serializes cleanly.
        if f != f or f == float("inf") or f == float("-inf"):
            return None
        return f
    except Exception:
        return None


def log_ab_prediction(variant: str, pred_id: str, timestamp: int, horizon: int,
                      direction: str, confidence: float, model_bundle_id: str = "",
                      feature_schema_hash: str = ""):
    """Record a variant's prediction for durable A/B comparison."""
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT OR REPLACE INTO ab_results
            (id, variant, pred_id, timestamp, horizon, direction, confidence, resolved, model_bundle_id, feature_schema_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, FALSE, ?, ?)
        """, (f"{variant}_{pred_id}", variant, pred_id, timestamp, horizon, direction, confidence, model_bundle_id, feature_schema_hash))
    except Exception as e:
        print(f"DuckDB A/B Insert Error: {e}")
    finally:
        if conn:
            conn.close()


def resolve_ab_results(pred_id: str, actual_direction: str):
    """Resolve every variant row for a prediction: hit = (its direction == actual)."""
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            UPDATE ab_results
            SET actual_direction = ?, hit = (direction = ?), resolved = TRUE
            WHERE pred_id = ? AND resolved = FALSE
        """, (actual_direction, actual_direction, pred_id))
    except Exception as e:
        print(f"DuckDB A/B Resolve Error: {e}")
    finally:
        if conn:
            conn.close()


def fetch_ab_variant_stats() -> dict:
    """Per-variant resolved counts + accuracy from DuckDB (for durable promotion)."""
    out = {}
    conn = None
    try:
        conn = _connect()
        df = conn.execute("""
            SELECT variant,
                   COUNT(*) FILTER (WHERE resolved) AS verified,
                   SUM(CASE WHEN resolved AND hit THEN 1 ELSE 0 END) AS hits
            FROM ab_results
            GROUP BY variant
        """).df()
        for r in df.to_dict("records"):
            v = int(r.get("verified") or 0)
            h = int(r.get("hits") or 0)
            out[r["variant"]] = {"verified": v, "hits": h,
                                 "accuracy": round(h / v, 4) if v else 0.0}
    except Exception as e:
        print(f"DuckDB A/B Stats Error: {e}")
    finally:
        if conn:
            conn.close()
    return out

def fetch_ab_variant_profit_stats() -> dict:
    """Cost-adjusted per-variant trade stats from resolved A/B rows.

    A/B rows store the variant direction; the primary prediction tables store the
    realized dollar move. Join them by pred_id/horizon and compute a per-BTC
    directional PnL after the same cost floor used by labels.
    """
    out = {}
    timeframes = [1, 3, 5, 7, 10, 15, 30]
    union_sql = " UNION ALL ".join([
        f"""
        SELECT id, horizon, binance_price, actual_move, resolved
        FROM predictions_{tf}m
        """ for tf in timeframes
    ])
    cost_floor = float(os.environ.get("BTC_BACKTEST_COST_FLOOR", os.environ.get("BTC_LABEL_COST_FLOOR", "0.0008")))
    conn = None
    try:
        conn = _connect()
        df = conn.execute(f"""
            SELECT a.variant, a.direction, p.actual_move, p.binance_price
            FROM ab_results a
            JOIN ({union_sql}) p
              ON p.id = a.pred_id AND p.horizon = a.horizon
            WHERE a.resolved = TRUE
              AND p.resolved = TRUE
              AND p.actual_move IS NOT NULL
              AND p.binance_price IS NOT NULL
        """).df()
        for r in df.to_dict("records"):
            variant = str(r.get("variant") or "")
            direction = str(r.get("direction") or "NEUTRAL")
            if not variant:
                continue
            row = out.setdefault(variant, {
                "resolved": 0,
                "trades": 0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
                "expectancy_usd": 0.0,
                "profit_factor": 0.0,
            })
            row["resolved"] += 1
            if direction not in ("UP", "DOWN"):
                continue
            actual_move = float(r.get("actual_move") or 0.0)
            price = float(r.get("binance_price") or 0.0)
            sign = 1.0 if direction == "UP" else -1.0
            pnl = sign * actual_move - (price * cost_floor)
            row["trades"] += 1
            row["expectancy_usd"] += pnl
            if pnl > 0:
                row["gross_profit"] += pnl
            else:
                row["gross_loss"] += abs(pnl)
        for row in out.values():
            trades = int(row.get("trades") or 0)
            gp = float(row.get("gross_profit") or 0.0)
            gl = float(row.get("gross_loss") or 0.0)
            row["expectancy_usd"] = round(row["expectancy_usd"] / trades, 4) if trades else 0.0
            row["profit_factor"] = round(gp / gl, 4) if gl > 0 else (99.0 if gp > 0 else 0.0)
            row["gross_profit"] = round(gp, 4)
            row["gross_loss"] = round(gl, 4)
    except Exception as e:
        print(f"DuckDB A/B Profit Stats Error: {e}")
    finally:
        if conn:
            conn.close()
    return out

def log_prediction(pred_id: str, timestamp: int, horizon: int, binance_price: float,
                   target_price: float, expected_move: float, confidence: float, signal: str,
                   chainlink_price: float, chainlink_target: float, cascade_active: bool = False,
                   regime: str = "UNKNOWN", context: dict = None, raw_direction: str = "",
                   skip_reason: str = "", avoid_success: bool = False, prob_up: float = 0.0,
                   prob_down: float = 0.0, agreement: float = 0.0, model_dirs: dict = None,
                   verify_at: int = 0, expected_move_range: dict = None,
                   expectancy_usd: float = 0.0, expected_slippage_usd: float = 0.0,
                   model_bundle_id: str = "baseline_v9", feature_schema_hash: str = "",
                   confluence_grade: str = "", expected_precision: float = None,
                   calibrated_confidence: float = None, model_raw_direction: str = "",
                   pre_server_direction: str = "", final_direction: str = "",
                   trade_verdict: str = "", no_trade_reasons: list = None,
                   decision_state: dict = None, model_confluence: float = 0.0,
                   setup_score: float = 0.0, setup_quality: dict = None):
    conn = None
    try:
        conn = _connect()
        conn.execute(f"""
            INSERT INTO predictions_{horizon}m (
                id, timestamp, horizon, binance_price, target_price, expected_move,
                confidence, signal, chainlink_price, chainlink_target, resolved, cascade_active, regime,
                raw_direction, skip_reason, avoid_success, prob_up, prob_down, agreement,
                model_dirs_json, verify_at, move_range_low, move_range_median,
                move_range_high, move_range_width, model_version, feature_schema_hash,
                confluence_grade, expected_precision, calibrated_confidence,
                model_raw_direction, pre_server_direction, final_direction, trade_verdict,
                no_trade_reasons_json, decision_state_json, model_confluence, setup_score,
                setup_quality_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (pred_id, timestamp, horizon, binance_price, target_price, expected_move,
              confidence, signal, chainlink_price, chainlink_target, False, cascade_active, regime,
              raw_direction or signal, skip_reason or "", avoid_success, prob_up, prob_down,
              agreement, json.dumps(model_dirs or {}), verify_at,
              (expected_move_range or {}).get("low"),
              (expected_move_range or {}).get("median"),
              (expected_move_range or {}).get("high"),
              ((expected_move_range or {}).get("high") - (expected_move_range or {}).get("low"))
              if (expected_move_range or {}).get("high") is not None and (expected_move_range or {}).get("low") is not None
              else None, model_bundle_id, feature_schema_hash,
              confluence_grade or "", expected_precision, calibrated_confidence,
              model_raw_direction or raw_direction or signal,
              pre_server_direction or raw_direction or signal,
              final_direction or signal,
              trade_verdict or "",
              json.dumps(no_trade_reasons or []),
              json.dumps(decision_state or {}),
              float(model_confluence or 0.0),
              float(setup_score or 0.0),
              json.dumps(setup_quality or {})))
        # Store prediction-time context for the trained meta-model.
        if context:
            conn.execute(f"""
                UPDATE predictions_{horizon}m
                SET agreement = ?, ewma_vol = ?, spread_norm = ?, wall_imbalance = ?,
                    sr_compression = ?, liq_imbalance = ?, 
                    quantile_width_pct = ?, quantile_asymmetry = ?, quantile_spread = ?,
                    wf_accuracy = ?, wf_accuracy_minus_0_5 = ?, wf_fold_std = ?,
                    wf_sample_count = ?, wf_age_minutes = ?, tradeability = ?,
                    regime_score = ?, liquidity_score = ?, expected_edge = ?,
                    expectancy_usd = ?, expected_slippage_usd = ?,
                    conviction = ?, actionable = ?, confluence = ?
                WHERE id = ?
            """, (
                float(context.get("agreement", agreement or 0.0)),
                float(context.get("ewma_vol", 0.0)),
                float(context.get("spread_norm", 0.0)),
                float(context.get("wall_imbalance", 0.0)),
                float(context.get("sr_compression", 0.0)),
                float(context.get("liq_imbalance", 0.0)),
                float(context.get("quantile_width_pct", 0.0)),
                float(context.get("quantile_asymmetry", 0.0)),
                float(context.get("quantile_spread", 0.0)),
                float(context.get("wf_accuracy", 0.5)),
                float(context.get("wf_accuracy_minus_0_5", 0.0)),
                float(context.get("wf_fold_std", 0.0)),
                float(context.get("wf_sample_count", 0.0)),
                float(context.get("wf_age_minutes", 0.0)),
                float(context.get("tradeability", 0.0)),
                float(context.get("regime_score", 0.0)),
                float(context.get("liquidity_score", 0.0)),
                float(context.get("expected_edge", 0.0)),
                float(expectancy_usd),
                float(expected_slippage_usd),
                float(context.get("conviction", 0.0)),
                float(context.get("actionable", 0.0)),
                float(context.get("confluence", 0.0)),
                pred_id,
            ))
    except Exception as e:
        print(f"DuckDB Insert Error: {e}")
    finally:
        if conn:
            conn.close()

def update_outcome(pred_id: str, horizon: int, actual_price: float, actual_move: float,
                   hit: bool, price_match: bool, move_error: float, avoid_success: bool = False,
                   lean_hit: bool = None):
    conn = None
    try:
        conn = _connect()
        conn.execute(f"""
            UPDATE predictions_{horizon}m
            SET actual_price = ?, actual_move = ?, hit = ?, price_match = ?, move_error = ?,
                avoid_success = ?, lean_hit = ?, resolved = ?
            WHERE id = ?
        """, (actual_price, actual_move, hit, price_match, move_error, avoid_success,
              lean_hit, True, pred_id))
    except Exception as e:
        print(f"DuckDB Update Error: {e}")
    finally:
        if conn:
            conn.close()

def fetch_unresolved_predictions(max_age_hours: int = 48) -> list[dict]:
    """Load unresolved predictions so backend reloads do not lose pending outcomes."""
    rows = []
    cutoff = int((time.time() - max_age_hours * 3600) * 1000)
    conn = None
    try:
        conn = _connect()
        for h in [1, 3, 5, 7, 10, 15, 30]:
            df = conn.execute(f"""
                SELECT id, horizon, signal, raw_direction, skip_reason, confidence,
                       target_price, expected_move, binance_price, timestamp, verify_at,
                       prob_up, prob_down, agreement, cascade_active, model_dirs_json, regime
                FROM predictions_{h}m
                WHERE resolved = FALSE AND timestamp >= ?
            """, (cutoff,)).df()
            for r in df.to_dict("records"):
                try:
                    model_dirs = json.loads(r.get("model_dirs_json") or "{}")
                except Exception:
                    model_dirs = {}
                predicted_price = float(r.get("binance_price") or 0.0)
                expected_move = abs(float(r.get("expected_move") or 0.0))
                target_price = float(r.get("target_price") or predicted_price)
                rows.append({
                    "id": r.get("id", ""),
                    "horizon": int(r.get("horizon") or h),
                    "direction": r.get("signal") or "NEUTRAL",
                    "raw_direction": r.get("raw_direction") or r.get("signal") or "NEUTRAL",
                    "skip_reason": r.get("skip_reason") or "",
                    "confidence": float(r.get("confidence") or 0.0),
                    "target_price": target_price,
                    "expected_move_usd": round(expected_move, 2),
                    "signed_expected_move_usd": round(target_price - predicted_price, 2),
                    "predicted_price": predicted_price,
                    "timestamp": int(r.get("timestamp") or 0),
                    "verify_at": int(r.get("verify_at") or ((r.get("timestamp") or 0) + h * 60 * 1000)),
                    "signal": r.get("signal") or "NEUTRAL",
                    "prob_up": float(r.get("prob_up") or 0.0),
                    "prob_down": float(r.get("prob_down") or 0.0),
                    "agreement": float(r.get("agreement") or 0.0),
                    "cascade_active": bool(r.get("cascade_active")),
                    "model_dirs": model_dirs,
                    "regime": r.get("regime") or "UNKNOWN",
                })
    except Exception as e:
        print(f"DuckDB Pending Fetch Error: {e}")
    finally:
        if conn:
            conn.close()
    return rows

def get_last_prediction_timestamps() -> dict[int, int]:
    """Latest recorded prediction timestamp by horizon, used to restore cadence after reload."""
    out = {}
    conn = None
    try:
        conn = _connect()
        for h in [1, 3, 5, 7, 10, 15, 30]:
            ts = conn.execute(f"SELECT MAX(timestamp) FROM predictions_{h}m").fetchone()[0]
            if ts is not None:
                out[h] = int(ts)
    except Exception as e:
        print(f"DuckDB Timestamp Fetch Error: {e}")
    finally:
        if conn:
            conn.close()
    return out

def log_analysis_snapshot(snapshot: dict):
    """Store compact dashboard/analysis state for later audits."""
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT OR REPLACE INTO analysis_snapshots (
                timestamp, price, regime, boot_seconds, signal_history_snapshots,
                signal_history_coverage_pct, resolved_total, pending_total,
                action_summary_json, horizon_accuracy_json, error_summary_json,
                drift_json, support_resistance_json, indicator_snapshot_json,
                kronos_status_json, fsr_ppo_json, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            int(snapshot.get("timestamp") or int(time.time() * 1000)),
            float(snapshot.get("price") or 0.0),
            snapshot.get("regime") or "UNKNOWN",
            float(snapshot.get("boot_seconds") or 0.0),
            int(snapshot.get("signal_history_snapshots") or 0),
            float(snapshot.get("signal_history_coverage_pct") or 0.0),
            int(snapshot.get("resolved_total") or 0),
            int(snapshot.get("pending_total") or 0),
            json.dumps(snapshot.get("action_summary") or {}),
            json.dumps(snapshot.get("horizon_accuracy") or {}),
            json.dumps(snapshot.get("error_summary") or {}),
            json.dumps(snapshot.get("drift") or {}),
            json.dumps(snapshot.get("support_resistance") or {}),
            json.dumps(snapshot.get("indicator_snapshot") or {}),
            json.dumps(snapshot.get("kronos_status") or {}),
            json.dumps(snapshot.get("fsr_ppo") or {}),
            snapshot.get("notes") or "",
        ))
    except Exception as e:
        print(f"DuckDB Analysis Snapshot Insert Error: {e}")
    finally:
        if conn:
            conn.close()

def insert_feature_importance(horizon: int, rank: int, feature: str, importance: float):
    import datetime
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT INTO feature_importance 
            (timestamp, horizon, rank, feature, importance)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.datetime.now(), horizon, rank, feature, importance))
    except Exception as e:
        print(f"DuckDB Insert Error: {e}")
    finally:
        if conn:
            conn.close()

def log_simulated_trade(trade: dict):
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT INTO simulated_trades 
            (trade_id, prediction_id, timestamp, horizon, direction, entry_price, 
             exit_price, position_size, slippage_usd, fees_usd, pnl_usd, net_pnl_usd, hold_time_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade["trade_id"], trade["prediction_id"], trade["timestamp"], trade["horizon"], 
            trade["direction"], trade["entry_price"], trade["exit_price"], trade["position_size"], 
            trade["slippage_usd"], trade["fees_usd"], trade["pnl_usd"], trade["net_pnl_usd"], 
            trade["hold_time_ms"]
        ))
    except Exception as e:
        print(f"DuckDB Simulated Trade Insert Error: {e}")
    finally:
        if conn:
            conn.close()

# -------------------------------------------------------------------
# Institutional Data Infrastructure: Parquet Partition Writers
# -------------------------------------------------------------------
import pyarrow as pa
import pyarrow.parquet as pq

def log_raw_trade_parquet(trade: dict):
    """
    Append a raw trade tick to a daily Parquet partition.
    For direct DuckDB querying: SELECT * FROM 'data/trades/**/*.parquet'

    DISABLED BY DEFAULT: this read-whole-file + rewrite-per-trade pattern is O(n^2) over
    the day and a corruption source (a truncated mid-write leaves a tiny invalid file).
    These ticks are NOT used by the prediction pipeline (archive only), so it's off unless
    BTC_LOG_TICKS_PARQUET=1.
    """
    if os.environ.get("BTC_LOG_TICKS_PARQUET", "0") != "1":
        return
    try:
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'trades')
        os.makedirs(data_dir, exist_ok=True)
        import datetime
        dt = datetime.datetime.fromtimestamp((trade.get("time", 0) / 1000) or time.time())
        date_str = dt.strftime("%Y-%m-%d")
        
        file_path = os.path.join(data_dir, f"trades_{date_str}.parquet")
        
        table = pa.Table.from_pydict({
            "symbol": [trade.get("symbol", "BTCUSDT")],
            "trade_id": [trade.get("trade_id", 0)],
            "price": [float(trade.get("price", 0.0))],
            "quantity": [float(trade.get("quantity", 0.0))],
            "is_buyer_maker": [bool(trade.get("is_buyer_maker", False))],
            "timestamp": [int(trade.get("time", 0))]
        })
        
        if os.path.exists(file_path):
            try:
                existing_table = pq.read_table(file_path)
                table = pa.concat_tables([existing_table, table])
            except Exception as e:
                print(f"Corrupted Trade Parquet file detected ({file_path}), backing up. Error: {e}")
                os.rename(file_path, file_path + f".corrupted.{int(time.time())}")
            
        pq.write_table(table, file_path)
    except Exception as e:
        print(f"Parquet Trade Write Error: {e}")

def log_depth_parquet(depth: dict):
    """
    Append an orderbook snapshot/update to a daily Parquet partition.

    DISABLED BY DEFAULT (same O(n^2) read-rewrite issue as trades, archive-only, not used
    by the prediction pipeline). Enable with BTC_LOG_TICKS_PARQUET=1.
    """
    if os.environ.get("BTC_LOG_TICKS_PARQUET", "0") != "1":
        return
    try:
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'depth')
        os.makedirs(data_dir, exist_ok=True)
        import datetime
        dt = datetime.datetime.fromtimestamp((depth.get("time", 0) / 1000) or time.time())
        date_str = dt.strftime("%Y-%m-%d")
        
        file_path = os.path.join(data_dir, f"depth_{date_str}.parquet")
        
        table = pa.Table.from_pydict({
            "symbol": [depth.get("symbol", "BTCUSDT")],
            "timestamp": [int(depth.get("time", 0))],
            "bids_json": [json.dumps(depth.get("bids", []))],
            "asks_json": [json.dumps(depth.get("asks", []))]
        })
        
        if os.path.exists(file_path):
            try:
                existing_table = pq.read_table(file_path)
                table = pa.concat_tables([existing_table, table])
            except Exception as e:
                print(f"Corrupted Depth Parquet file detected ({file_path}), backing up. Error: {e}")
                os.rename(file_path, file_path + f".corrupted.{int(time.time())}")
            
        pq.write_table(table, file_path)
    except Exception as e:
        print(f"Parquet Depth Write Error: {e}")

def log_polymarket_quote(quote: dict):
    """Log a Polymarket quote to DuckDB."""
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT INTO polymarket_quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            quote.get("market_id", ""),
            int(time.time() * 1000),
            quote.get("yes_best_bid", 0.0),
            quote.get("yes_best_ask", 1.0),
            quote.get("no_best_bid", 0.0),
            quote.get("no_best_ask", 1.0),
            quote.get("yes_spread", 1.0),
            quote.get("yes_imbalance", 0.0)
        ))
    except Exception as e:
        print(f"DuckDB Polymarket Quote Insert Error: {e}")
    finally:
        if conn:
            conn.close()

def log_polymarket_market(market: dict):
    """Log a discovered Polymarket market to DuckDB."""
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT OR IGNORE INTO polymarket_markets VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            market.get("id", ""),
            market.get("condition_id", ""),
            market.get("slug", ""),
            market.get("question", ""),
            market.get("yes_token", ""),
            market.get("no_token", ""),
            market.get("end_date", ""),
            market.get("reference_price", 0.0)
        ))
    except Exception as e:
        print(f"DuckDB Polymarket Market Insert Error: {e}")
    finally:
        if conn:
            conn.close()

def log_polymarket_paper_trade(trade: dict):
    """Log a Polymarket paper trade to DuckDB."""
    conn = None
    try:
        conn = _connect()
        conn.execute("""
            INSERT INTO polymarket_paper_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.get("trade_id", ""),
            trade.get("prediction_id", ""),
            trade.get("market_id", ""),
            trade.get("action", ""),
            trade.get("fill_price", 0.0),
            trade.get("size", 0.0),
            trade.get("fees", 0.0),
            trade.get("slippage", 0.0),
            trade.get("exit_price", 0.0),
            trade.get("net_pnl", 0.0)
        ))
    except Exception as e:
        print(f"DuckDB Polymarket Paper Trade Insert Error: {e}")
    finally:
        if conn:
            conn.close()

