import duckdb
import os

# Single source of truth for the DB location — the server writes via database.DB_PATH
# (now under data/ or BTC_DB_PATH). Importing it here prevents analytics/reports from
# reading a stale/empty root-level analytics.duckdb.
from database import DB_PATH
HORIZONS = [1, 3, 5, 7, 10, 15]


def _prediction_union(horizons=None) -> str:
    """Build a UNION ALL query over prediction tables."""
    horizons = horizons or HORIZONS
    return "\nUNION ALL\n".join(
        f"SELECT {h} AS table_horizon, * FROM predictions_{h}m" for h in horizons
    )

def analyze_confidence_buckets(horizon: int):
    """Query 1: Are our confidence buckets honest?"""
    query = f"""
        SELECT 
            horizon,
            ROUND(confidence, 1) AS confidence_bucket,
            COUNT(*) AS total_predictions,
            SUM(CASE WHEN hit THEN 1 ELSE 0 END) AS correct,
            ROUND(AVG(CASE WHEN hit THEN 1.0 ELSE 0.0 END) * 100, 1) AS actual_hit_rate,
            ROUND(AVG(confidence) * 100, 1) AS avg_stated_confidence,
            ROUND(AVG(move_error), 2) AS avg_dollar_error
        FROM predictions_{horizon}m
        WHERE resolved = TRUE AND hit IS NOT NULL
        GROUP BY horizon, confidence_bucket
        ORDER BY confidence_bucket
    """
    with duckdb.connect(DB_PATH) as conn:
        print(f"--- Confidence Bucket Analysis for {horizon}m ---")
        df = conn.execute(query).df()
        print(df)
        return df

def analyze_regime_accuracy(horizon: int):
    """Query 2: How does accuracy hold up per regime?"""
    query = f"""
        SELECT 
            regime,
            COUNT(*) AS total_predictions,
            SUM(CASE WHEN resolved = FALSE THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN signal = 'UP' THEN 1 ELSE 0 END) AS buy_signals,
            SUM(CASE WHEN signal = 'DOWN' THEN 1 ELSE 0 END) AS sell_signals,
            SUM(CASE WHEN signal = 'NEUTRAL' THEN 1 ELSE 0 END) AS avoid_signals,
            ROUND((MAX(timestamp) - MIN(timestamp)) / 86400000.0, 1) AS days_covered,
            ROUND(AVG(CASE WHEN resolved = TRUE AND hit THEN 1.0 ELSE 0.0 END) * 100, 1) AS accuracy,
            ROUND(AVG(confidence) * 100, 1) AS avg_confidence,
            ROUND(AVG(move_error), 2) AS avg_error_usd
        FROM predictions_{horizon}m
        GROUP BY regime
        ORDER BY accuracy DESC
    """
    with duckdb.connect(DB_PATH) as conn:
        print(f"--- Regime Accuracy Breakdown for {horizon}m ---")
        df = conn.execute(query).df()
        print(df)
        return df

def analyze_cascade_impact(horizon: int):
    """Query 3: Is the Hierarchical Cascade helping or hurting?"""
    query = f"""
        SELECT 
            cascade_active,
            COUNT(*) AS predictions,
            ROUND(AVG(CASE WHEN hit THEN 1.0 ELSE 0.0 END) * 100, 1) AS accuracy,
            ROUND(AVG(confidence) * 100, 1) AS avg_confidence
        FROM predictions_{horizon}m
        WHERE resolved = TRUE AND hit IS NOT NULL
        GROUP BY cascade_active
    """
    try:
        with duckdb.connect(DB_PATH) as conn:
            print(f"--- Cascade Active vs Inactive for {horizon}m ---")
            df = conn.execute(query).df()
            print(df)
            return df
    except Exception as e:
        print(f"Error querying cascade impact: {e}")
        return None

def analyze_time_of_day(horizon: int):
    """Query 4: Asian vs London vs US Session Performance"""
    query = f"""
        SELECT 
            (timestamp / 3600000) % 24 AS hour_utc,
            COUNT(*) AS predictions,
            ROUND(AVG(CASE WHEN hit THEN 1.0 ELSE 0.0 END) * 100, 1) AS accuracy
        FROM predictions_{horizon}m
        WHERE resolved = TRUE AND hit IS NOT NULL
        GROUP BY hour_utc
        ORDER BY hour_utc
    """
    with duckdb.connect(DB_PATH) as conn:
        print(f"--- Time of Day Performance for {horizon}m ---")
        df = conn.execute(query).df()
        print(df)
        return df

def analyze_calibration(horizon: int):
    """Query 5: Isotonic Calibration Check (Actual Hit Rate vs Stated Confidence)"""
    query = f"""
        SELECT 
            ROUND(confidence / 0.05) * 0.05 AS confidence_bucket,
            COUNT(*) AS predictions,
            ROUND(AVG(CASE WHEN hit THEN 1.0 ELSE 0.0 END), 3) AS actual_hit_rate,
            ROUND(AVG(confidence), 3) AS stated_confidence,
            ROUND(AVG(CASE WHEN hit THEN 1.0 ELSE 0.0 END) - AVG(confidence), 3) AS calibration_error
        FROM predictions_{horizon}m
        WHERE hit IS NOT NULL AND confidence BETWEEN 0.55 AND 0.95
        GROUP BY confidence_bucket
        HAVING COUNT(*) >= 10
        ORDER BY confidence_bucket
    """
    with duckdb.connect(DB_PATH) as conn:
        print(f"--- Calibration Curve for {horizon}m ---")
        df = conn.execute(query).df()
        print(df)
        return df

def analyze_feature_importance(horizon: int, days: int = 7):
    """Query 6: SHAP Feature Importance over the last N days"""
    query = f"""
        SELECT 
            horizon, 
            rank, 
            feature, 
            ROUND(AVG(importance), 4) AS avg_importance
        FROM feature_importance
        WHERE timestamp > NOW() - INTERVAL '{days} days'
          AND horizon = {horizon}
        GROUP BY horizon, rank, feature
        ORDER BY rank
    """
    try:
        with duckdb.connect(DB_PATH) as conn:
            print(f"--- Top Features for {horizon}m (Last {days} Days) ---")
            df = conn.execute(query).df()
            print(df)
            return df
    except Exception as e:
        print(f"Error querying feature importance (table might not exist yet): {e}")
        return None

def validate_regime_thresholds(horizons=None):
    """
    Query 8: Validate the regime engine against real outcomes.

    The regime classifier uses fixed ADX/ATR thresholds that were never checked
    against results. This queries DuckDB to see which regimes actually correlate
    with higher directional accuracy. Any regime with >=30 verified predictions and
    sub-50% accuracy is a candidate for a forced-NEUTRAL override in regime.py.
    """
    if horizons is None:
        horizons = [1, 3, 5, 10, 15]
    results = {}
    for horizon in horizons:
        # LEAN-truth grading (raw_direction vs realized move sign), NOT the `hit` column:
        # `hit` is dual-semantic (avoid_success=TRUE on gated rows when the lean was WRONG),
        # which INVERTED this feed into the poor_regimes blocker — regimes whose leans were
        # consistently wrong-but-gated scored ~100% "accuracy" and stayed unblocked.
        query = f"""
            SELECT regime,
                   COUNT(*) AS n,
                   ROUND(AVG(CASE WHEN (raw_direction='UP'   AND actual_move > 0)
                                    OR (raw_direction='DOWN' AND actual_move < 0)
                                  THEN 1.0 ELSE 0.0 END) * 100, 1) AS accuracy,
                   ROUND(AVG(confidence) * 100, 1) AS avg_confidence
            FROM predictions_{horizon}m
            WHERE resolved = TRUE AND raw_direction IN ('UP','DOWN')
              AND actual_move IS NOT NULL
            GROUP BY regime
            HAVING COUNT(*) >= 20
            ORDER BY accuracy DESC
        """
        try:
            with duckdb.connect(DB_PATH) as conn:
                df = conn.execute(query).df()
                results[horizon] = df.to_dict("records")
                print(f"--- Regime validation for {horizon}m ---")
                print(df)
                for row in results[horizon]:
                    if row["n"] >= 30 and row["accuracy"] < 50:
                        print(f"  [REGIME WARNING] {horizon}m / {row['regime']}: "
                              f"{row['accuracy']}% on {row['n']} preds — consider forcing NEUTRAL here.")
        except Exception as e:
            print(f"Error validating regime for {horizon}m: {e}")
    return results


def analyze_feature_retirement(horizon: int, days: int = 7):
    """
    Query 9: Feature retirement candidates.

    Good quant systems *remove* features, not just add them. This ranks features by
    their average SHAP importance over the recent window and surfaces the weakest —
    persistent near-zero contributors are candidates for retirement (feature bloat
    hurts both speed and generalization). Compares against all features so columns
    that NEVER appear in the top-10 are implicitly the weakest.
    """
    query = f"""
        SELECT feature,
               ROUND(AVG(importance), 5) AS avg_importance,
               COUNT(*) AS times_in_top10
        FROM feature_importance
        WHERE horizon = {horizon}
          AND timestamp > NOW() - INTERVAL '{days} days'
        GROUP BY feature
        ORDER BY avg_importance ASC
    """
    try:
        with duckdb.connect(DB_PATH) as conn:
            print(f"--- Feature retirement candidates for {horizon}m (last {days}d) ---")
            df = conn.execute(query).df()
            print("Weakest contributors (retirement candidates):")
            print(df.head(10).to_string(index=False))
            return df
    except Exception as e:
        print(f"Error querying feature retirement: {e}")
        return None


def analyze_conviction_performance(horizon: int = None):
    """
    Does conviction-gating actually win more? Compares hit rate of ACTIONABLE
    (high-conviction, confluent) signals vs the rest, and by conviction-grade band.
    This is the core validation of the win-rate thesis: actionable signals should
    out-hit the raw directional rate by a wide margin.
    """
    horizons = [horizon] if horizon else [1, 3, 5, 7, 10, 15]
    for h in horizons:
        try:
            with duckdb.connect(DB_PATH) as conn:
                print(f"--- Conviction performance for {h}m ---")
                # Sign-truth grading of the raw lean — these queries select raw-directional
                # rows, so the dual-semantic `hit` column (avoid_success on gated rows)
                # would invert the very performance this function is meant to measure.
                df = conn.execute(f"""
                    SELECT actionable,
                           COUNT(*) n,
                           ROUND(AVG(CASE WHEN (raw_direction='UP'   AND actual_move > 0)
                                            OR (raw_direction='DOWN' AND actual_move < 0)
                                          THEN 1.0 ELSE 0.0 END) * 100, 1) hit_rate,
                           ROUND(AVG(conviction), 1) avg_conviction
                    FROM predictions_{h}m
                    WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL
                    GROUP BY actionable
                """).df()
                print(df.to_string(index=False))
                band = conn.execute(f"""
                    SELECT CASE WHEN conviction >= 80 THEN 'A+ (80+)'
                                WHEN conviction >= 68 THEN 'A (68-80)'
                                WHEN conviction >= 55 THEN 'B (55-68)'
                                ELSE 'C/WATCH (<55)' END AS grade,
                           COUNT(*) n,
                           ROUND(AVG(CASE WHEN (raw_direction='UP'   AND actual_move > 0)
                                            OR (raw_direction='DOWN' AND actual_move < 0)
                                          THEN 1.0 ELSE 0.0 END) * 100, 1) hit_rate
                    FROM predictions_{h}m
                    WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL
                    GROUP BY grade ORDER BY grade DESC
                """).df()
                print(band.to_string(index=False))
        except Exception as e:
            print(f"  {h}m: {e}")


def analyze_ab_results(min_verified: int = 300, min_accuracy_edge: float = 0.02):
    """
    Durable A/B comparison from the `ab_results` table (survives restarts).

    Reports per-variant resolved counts and accuracy, plus a promotion recommendation:
    a challenger should only replace the primary once it has enough verified samples
    AND beats the primary's accuracy by at least `min_accuracy_edge`. The live
    ABTestRunner also requires a positive bootstrap lower bound before promotion.
    """
    try:
        with duckdb.connect(DB_PATH) as conn:
            df = conn.execute("""
                SELECT variant,
                       COUNT(*) FILTER (WHERE resolved) AS verified,
                       SUM(CASE WHEN resolved AND hit THEN 1 ELSE 0 END) AS hits,
                       ROUND(AVG(CASE WHEN resolved AND hit THEN 1.0
                                      WHEN resolved THEN 0.0 END) * 100, 2) AS accuracy_pct
                FROM ab_results
                GROUP BY variant
                ORDER BY accuracy_pct DESC
            """).df()
        print("--- A/B variant results (durable) ---")
        print(df.to_string(index=False))
        records = df.to_dict("records")
        stats = {r["variant"]: r for r in records}
        recommendation = "keep_primary"
        if len(stats) >= 2:
            ranked = sorted(records, key=lambda r: (r.get("accuracy_pct") or 0), reverse=True)
            best, second = ranked[0], ranked[1]
            if (int(best.get("verified") or 0) >= min_verified
                    and ((best.get("accuracy_pct") or 0) - (second.get("accuracy_pct") or 0)) / 100.0 >= min_accuracy_edge):
                recommendation = f"promote::{best['variant']}"
        print(f"Promotion recommendation: {recommendation}")
        return {"variants": stats, "recommendation": recommendation}
    except Exception as e:
        print(f"Error querying A/B results: {e}")
        return None


def apply_feature_retirement(days: int = 14, min_logged_features: int = 30,
                             keep_core: bool = True, dry_run: bool = True):
    """
    Wire feature-retirement analysis into an ACTUAL prune step.

    A feature is a retirement candidate if it never appears in any horizon's SHAP
    top-10 over the window. The retired list is written to DuckDB's
    `feature_retirement_events` table, which `features.build_features_from_klines`
    reads and zeroes (a safe, reversible
    prune that keeps the matrix dimension stable so saved models stay compatible).

    Guards (so it can't misfire on sparse early data):
      - requires >= `min_logged_features` distinct features observed in SHAP logs,
      - always keeps core price/return features,
      - `dry_run=True` by default: returns the candidate list WITHOUT writing.
        Call with dry_run=False to actually persist and activate the prune.
    """
    from features import FEATURE_NAMES
    core_keep = {"price_return", "volume_norm", "rsi", "macd_hist", "atr_norm",
                 "bb_position", "vwap_deviation"}
    query = f"""
        SELECT DISTINCT feature
        FROM feature_importance
        WHERE timestamp > NOW() - INTERVAL '{days} days'
    """
    try:
        with duckdb.connect(DB_PATH) as conn:
            seen = set(conn.execute(query).df()["feature"].tolist())
    except Exception as e:
        print(f"Feature retirement skipped (query failed): {e}")
        return {"applied": False, "reason": str(e)}

    if len(seen) < min_logged_features:
        msg = f"Not enough SHAP evidence yet ({len(seen)}/{min_logged_features} features seen)."
        print(msg)
        return {"applied": False, "reason": msg, "candidates": []}

    candidates = [f for f in FEATURE_NAMES if f not in seen and not (keep_core and f in core_keep)]
    print(f"--- Feature retirement: {len(candidates)} candidates over last {days}d ---")
    print(candidates)

    if dry_run:
        return {"applied": False, "dry_run": True, "candidates": candidates}

    import time
    now_ms = int(time.time() * 1000)
    try:
        with duckdb.connect(DB_PATH) as conn:
            current = conn.execute("""
                SELECT feature
                FROM feature_retirement_events
                WHERE status = 'retired'
            """).df()["feature"].tolist()
            candidate_set = set(candidates)
            for feat in current:
                if feat not in candidate_set:
                    conn.execute("""
                        UPDATE feature_retirement_events
                        SET status = 'restored', restored_at = ?
                        WHERE status = 'retired' AND feature = ?
                    """, (now_ms, feat))
            # Now insert the new candidates
            for feat in candidates:
                conn.execute("""
                    INSERT INTO feature_retirement_events (feature, retired_at, model_version, reason, threshold, lookback_days, status)
                    SELECT ?, ?, 'auto', 'SHAP prune', 0.0, ?, 'retired'
                    WHERE NOT EXISTS (SELECT 1 FROM feature_retirement_events WHERE feature = ? AND status = 'retired')
                """, (feat, now_ms, days, feat))
        print(f"Wrote {len(candidates)} retired features to duckdb. Restart/retrain to activate.")
        return {"applied": True, "retired": candidates}
    except Exception as e:
        print(f"Failed to write retirement list: {e}")
        return {"applied": False, "reason": str(e)}


def analyze_expectancy():
    """Query 10: Simulated Execution Expectancy (Out of Sample)"""
    query = """
        SELECT 
            COUNT(*) AS total_trades,
            SUM(CASE WHEN net_pnl_usd > 0 THEN 1 ELSE 0 END) AS winning_trades,
            ROUND(AVG(CASE WHEN net_pnl_usd > 0 THEN 1.0 ELSE 0.0 END) * 100, 1) AS win_rate,
            ROUND(SUM(net_pnl_usd), 2) AS total_net_pnl_usd,
            ROUND(SUM(CASE WHEN net_pnl_usd > 0 THEN net_pnl_usd ELSE 0 END) / 
                  ABS(SUM(CASE WHEN net_pnl_usd < 0 THEN net_pnl_usd ELSE 1e-9 END)), 2) AS profit_factor,
            ROUND(AVG(net_pnl_usd), 2) AS expectancy_per_trade_usd,
            ROUND(SUM(slippage_usd), 2) AS total_slippage_usd,
            ROUND(SUM(fees_usd), 2) AS total_fees_usd
        FROM simulated_trades
    """
    try:
        with duckdb.connect(DB_PATH) as conn:
            print("--- Real-World Expectancy & Costs (Simulated Execution) ---")
            df = conn.execute(query).df()
            print(df)
            return df
    except Exception as e:
        print(f"Error querying expectancy (simulated_trades table might be empty): {e}")
        return None


def analyze_avoid_success(horizon: int = None):
    """Analyze whether AVOID/SKIP decisions protected the user from bad/flat setups."""
    horizons = [horizon] if horizon else HORIZONS
    query = f"""
        WITH allp AS ({_prediction_union(horizons)})
        SELECT horizon,
               COUNT(*) AS avoid_calls,
               SUM(CASE WHEN avoid_success THEN 1 ELSE 0 END) AS good_avoids,
               ROUND(AVG(CASE WHEN avoid_success THEN 1.0 ELSE 0.0 END) * 100, 1) AS avoid_success_rate,
               ROUND(AVG(confidence) * 100, 1) AS avg_confidence,
               COUNT(DISTINCT skip_reason) AS skip_reason_types
        FROM allp
        WHERE resolved = TRUE
          AND signal = 'NEUTRAL'
        GROUP BY horizon
        ORDER BY horizon
    """
    with duckdb.connect(DB_PATH) as conn:
        print("--- AVOID / SKIP Success ---")
        df = conn.execute(query).df()
        print(df)
        return df


def analyze_meta_filter_outcomes(horizon: int = None):
    """Compare raw model calls that were accepted vs changed to AVOID/SKIP."""
    horizons = [horizon] if horizon else HORIZONS
    query = f"""
        WITH allp AS ({_prediction_union(horizons)})
        SELECT horizon,
               CASE
                   WHEN signal = 'NEUTRAL' AND raw_direction IN ('UP', 'DOWN') THEN 'rejected_to_avoid'
                   WHEN signal IN ('UP', 'DOWN', 'LONG', 'SHORT', 'STRONG LONG', 'STRONG SHORT') THEN 'accepted_directional'
                   ELSE 'natural_neutral'
               END AS filter_bucket,
               COUNT(*) AS resolved,
               ROUND(AVG(CASE WHEN hit THEN 1.0 ELSE 0.0 END) * 100, 1) AS hit_rate,
               ROUND(AVG(CASE WHEN avoid_success THEN 1.0 ELSE 0.0 END) * 100, 1) AS avoid_success_rate,
               ROUND(AVG(confidence) * 100, 1) AS avg_confidence,
               ROUND(AVG(move_error), 2) AS avg_move_error
        FROM allp
        WHERE resolved = TRUE
          AND hit IS NOT NULL
        GROUP BY horizon, filter_bucket
        ORDER BY horizon, filter_bucket
    """
    with duckdb.connect(DB_PATH) as conn:
        print("--- Meta/Quality Filter Accepted vs Rejected Outcomes ---")
        df = conn.execute(query).df()
        print(df)
        return df


def analyze_skip_reasons(horizon: int = None):
    """Rank why the app avoided/skipped signals."""
    horizons = [horizon] if horizon else HORIZONS
    query = f"""
        WITH allp AS ({_prediction_union(horizons)})
        SELECT horizon,
               COALESCE(NULLIF(skip_reason, ''), 'no_reason_recorded') AS skip_reason,
               COUNT(*) AS calls,
               ROUND(AVG(CASE WHEN avoid_success THEN 1.0 ELSE 0.0 END) * 100, 1) AS avoid_success_rate
        FROM allp
        WHERE signal = 'NEUTRAL'
        GROUP BY horizon, skip_reason
        ORDER BY horizon, calls DESC
    """
    with duckdb.connect(DB_PATH) as conn:
        print("--- Skip Reasons ---")
        df = conn.execute(query).df()
        print(df)
        return df


def analyze_quantile_width_vs_error(horizon: int = None):
    """Check whether wide move-size ranges predict larger realized miss rates."""
    horizons = [horizon] if horizon else HORIZONS
    query = f"""
        WITH allp AS ({_prediction_union(horizons)})
        SELECT horizon,
               CASE
                   WHEN move_range_width IS NULL THEN 'no_quantile'
                   WHEN move_range_width < 50 THEN 'tight_<50'
                   WHEN move_range_width < 150 THEN 'medium_50_150'
                   ELSE 'wide_150+'
               END AS quantile_width_bucket,
               COUNT(*) AS resolved,
               ROUND(AVG(move_range_width), 2) AS avg_range_width,
               ROUND(AVG(move_error), 2) AS avg_move_error,
               ROUND(AVG(CASE WHEN price_match THEN 1.0 ELSE 0.0 END) * 100, 1) AS price_match_rate,
               ROUND(AVG(CASE WHEN hit THEN 1.0 ELSE 0.0 END) * 100, 1) AS direction_hit_rate
        FROM allp
        WHERE resolved = TRUE
          AND hit IS NOT NULL
        GROUP BY horizon, quantile_width_bucket
        ORDER BY horizon, quantile_width_bucket
    """
    with duckdb.connect(DB_PATH) as conn:
        print("--- Quantile Move-Range Width vs Realized Error ---")
        df = conn.execute(query).df()
        print(df)
        return df


def analyze_analysis_snapshots(limit: int = 50):
    """Inspect periodic dashboard/analysis snapshots written by the backend."""
    query = f"""
        SELECT timestamp,
               price,
               regime,
               boot_seconds,
               signal_history_snapshots,
               signal_history_coverage_pct,
               resolved_total,
               pending_total,
               support_resistance_json,
               indicator_snapshot_json,
               kronos_status_json,
               notes
        FROM analysis_snapshots
        ORDER BY timestamp DESC
        LIMIT {int(limit)}
    """
    with duckdb.connect(DB_PATH) as conn:
        print("--- Recent Analysis Snapshots ---")
        df = conn.execute(query).df()
        print(df)
        return df


def ab_promotion_criteria(min_verified: int = 300, min_accuracy_edge: float = 0.02) -> dict:
    """
    Document the current challenger promotion rule.
    A/B stats are persisted in DuckDB and reseeded into ABTestRunner on boot. The
    runner also requires the 5th percentile bootstrap lower bound for challenger
    minus primary accuracy to be positive.
    """
    criteria = {
        "min_verified_per_variant": min_verified,
        "challenger_accuracy_edge_required": min_accuracy_edge,
        "requires_bootstrap_significance": True,
        "promote_when": (
            "challenger_verified >= min_verified AND "
            "challenger_accuracy - primary_accuracy >= edge AND "
            "bootstrap_lower_bound > 0"
        ),
    }
    print("--- A/B Promotion Criteria ---")
    print(criteria)
    return criteria

def monitor_feature_drift():
    """Query 7: Placeholder for PSI Drift Detection logic"""
    # Real PSI requires comparing historical feature distributions vs live. 
    # For now we log the instruction to implement it in the live pipeline.
    print("--- PSI Drift Detection ---")
    print("Run `compute_psi` periodically in the training loop to track feature stability.")

def generate_monthly_report(days_back: int = 30) -> dict:
    """Generate the Out-of-Sample monthly tear sheet."""
    cutoff_ms = int((__import__('time').time() - days_back * 86400) * 1000)
    query = f"""
        WITH all_preds AS ({_prediction_union()})
        SELECT 
            COUNT(*) as total_predictions,
            SUM(CASE WHEN resolved = TRUE AND hit THEN 1 ELSE 0 END) as total_hits,
            SUM(CASE WHEN resolved = TRUE THEN 1 ELSE 0 END) as total_resolved,
            SUM(CASE WHEN expectancy_usd > 0 THEN expectancy_usd ELSE 0 END) as gross_profit,
            SUM(CASE WHEN expectancy_usd < 0 THEN ABS(expectancy_usd) ELSE 0 END) as gross_loss,
            AVG(expectancy_usd) as avg_expectancy,
            regime,
            AVG(CASE WHEN resolved = TRUE AND hit THEN 1.0 ELSE 0.0 END) as accuracy
        FROM all_preds
        WHERE timestamp >= {cutoff_ms}
        GROUP BY GROUPING SETS ((), (regime))
    """
    try:
        with duckdb.connect(DB_PATH) as conn:
            df = conn.execute(query).df()
            print(f"--- Monthly Report ({days_back} days) ---")
            print(df)
            return df.to_dict("records")
    except Exception as e:
        print(f"Monthly report failed: {e}")
        return []


if __name__ == "__main__":
    if os.path.exists(DB_PATH):
        analyze_confidence_buckets(5)
        analyze_regime_accuracy(5)
        analyze_cascade_impact(5)
        analyze_time_of_day(5)
        analyze_calibration(5)
        analyze_feature_importance(5)
        validate_regime_thresholds()
        analyze_feature_retirement(5)
        monitor_feature_drift()
        analyze_expectancy()
        analyze_avoid_success()
        analyze_meta_filter_outcomes()
        analyze_skip_reasons()
        analyze_quantile_width_vs_error()
        analyze_analysis_snapshots()
        ab_promotion_criteria()
        generate_monthly_report()
    else:
        print(f"Database not found at {DB_PATH}. Run the server to generate data.")
