"""
Trained Meta-Model (Trust Filter)
=================================
Replaces the hardcoded `if agreement < 0.6 and vol high: NEUTRAL` rule with a
*learned* binary classifier that predicts whether a given direction signal will be
correct, using the context recorded at prediction time (confidence, agreement,
regime, volatility, spread, liquidity walls, S/R compression, liquidation pressure,
time of day).

It trains only once enough verified outcomes exist in DuckDB (>= MIN_SAMPLES per
horizon). Until then `should_execute()` is a pass-through (returns True), so it never
degrades behaviour before it has evidence. Once trained, it can downgrade weak signals
to NEUTRAL — professional desks gain more from *not trading* the low-quality signals
than from improving the direction model.
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)

try:
    import duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

try:
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.preprocessing import LabelEncoder
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


META_FEATURES = [
    "confidence", "agreement", "regime_encoded", "ewma_vol",
    "spread_norm", "wall_imbalance", "sr_compression", "liq_imbalance",
    "quantile_width_pct", "quantile_asymmetry", "quantile_spread", "expectancy_usd",
    "wf_accuracy", "wf_accuracy_minus_0_5", "wf_fold_std", "wf_sample_count", "wf_age_minutes",
    "hour_utc", "tradeability", "regime_score", "liquidity_score", "expected_edge"
]


class TrainedMetaModel:
    MIN_SAMPLES = 100

    def __init__(self):
        self.model = None
        self.regime_encoder = None
        self.is_trained = False
        self.val_accuracy = None
        self.n_samples = 0

    def train(self, db_path: str, horizon: int) -> str:
        if not (HAS_DUCKDB and HAS_SKLEARN):
            return "duckdb/sklearn unavailable"
        try:
            with duckdb.connect(db_path) as conn:
                df = conn.execute(f"""
                    SELECT confidence, agreement, regime, ewma_vol, spread_norm,
                           wall_imbalance, sr_compression, liq_imbalance, 
                           quantile_width_pct, quantile_asymmetry, quantile_spread,
                           wf_accuracy, wf_accuracy_minus_0_5, wf_fold_std, wf_sample_count, wf_age_minutes,
                           (timestamp / 3600000) % 24 AS hour_utc, 
                           tradeability, regime_score, liquidity_score, expected_edge, expectancy_usd, hit,
                           binance_price, actual_move, expected_slippage_usd, signal, raw_direction
                    FROM predictions_{horizon}m
                    WHERE resolved = TRUE AND hit IS NOT NULL AND confidence > 0.55 AND binance_price IS NOT NULL
                    ORDER BY timestamp
                """).df()
        except Exception as e:
            return f"query failed: {e}"

        if len(df) < self.MIN_SAMPLES:
            return f"insufficient data: {len(df)}/{self.MIN_SAMPLES}"

        # Need both outcomes present to learn anything.
        if df["hit"].nunique() < 2:
            return "only one outcome class present"

        self.regime_encoder = LabelEncoder()
        df["regime_encoded"] = self.regime_encoder.fit_transform(df["regime"].fillna("RANGE"))

        # Cost-aware target: Did the trade actually produce positive P&L after costs?
        def calc_profitable(row):
            direction_sign = 1 if row.get("raw_direction") == "UP" else (-1 if row.get("raw_direction") == "DOWN" else 0)
            gross_pnl = direction_sign * row.get("actual_move", 0.0)
            cost_estimate = row.get("binance_price", 0.0) * 0.0010 + row.get("expected_slippage_usd", 0.0)
            return int((gross_pnl - cost_estimate) > 0)
            
        df["profitable"] = df.apply(calc_profitable, axis=1)
        X = df[META_FEATURES].fillna(0).values
        y = df["profitable"].astype(int).values

        # Strict temporal split — never shuffle a time series.
        split = int(len(df) * 0.75)
        X_tr, y_tr = X[:split], y[:split]
        X_val, y_val = X[split:], y[split:]
        if len(np.unique(y_tr)) < 2:
            return "training split has one class"

        import xgboost as xgb
        self.model = xgb.XGBClassifier(
            n_estimators=100, max_depth=3, learning_rate=0.05,
            subsample=0.8, random_state=42, eval_metric="logloss"
        )
        self.model.fit(X_tr, y_tr)
        self.val_accuracy = float(self.model.score(X_val, y_val)) if len(X_val) else None
        self.is_trained = True
        self.n_samples = len(df)
        return (f"trained on {len(df)} samples"
                + (f", val acc {self.val_accuracy:.3f}" if self.val_accuracy is not None else ""))

    def should_execute(self, context: dict, threshold: float = 0.58) -> tuple:
        """
        Returns (execute: bool, trust_probability: float).
        Pass-through (True, 0.5) until trained.
        """
        if not self.is_trained or self.model is None:
            return True, 0.5
        try:
            regime_enc = 0
            if self.regime_encoder is not None:
                rg = context.get("regime", "RANGE")
                if rg in list(self.regime_encoder.classes_):
                    regime_enc = int(self.regime_encoder.transform([rg])[0])
            row = {
                "confidence": context.get("confidence", 0.0),
                "agreement": context.get("agreement", 0.0),
                "regime_encoded": regime_enc,
                "ewma_vol": context.get("ewma_vol", 0.0),
                "spread_norm": context.get("spread_norm", 0.0),
                "wall_imbalance": context.get("wall_imbalance", 0.0),
                "sr_compression": context.get("sr_compression", 0.0),
                "liq_imbalance": context.get("liq_imbalance", 0.0),
                "quantile_width_pct": context.get("quantile_width_pct", 0.0),
                "quantile_asymmetry": context.get("quantile_asymmetry", 0.0),
                "quantile_spread": context.get("quantile_spread", 0.0),
                "wf_accuracy": context.get("wf_accuracy", 0.5),
                "wf_accuracy_minus_0_5": context.get("wf_accuracy_minus_0_5", 0.0),
                "wf_fold_std": context.get("wf_fold_std", 0.0),
                "wf_sample_count": context.get("wf_sample_count", 0),
                "wf_age_minutes": context.get("wf_age_minutes", 0.0),
                "hour_utc": context.get("hour_utc", 0),
                "tradeability": context.get("tradeability", 0.0),
                "regime_score": context.get("regime_score", 0.0),
                "liquidity_score": context.get("liquidity_score", 0.0),
                "expected_edge": context.get("expected_edge", 0.0),
                "expectancy_usd": context.get("expectancy_usd", 0.0),
            }
            feats = [[row[f] for f in META_FEATURES]]
            trust = float(self.model.predict_proba(feats)[0][1])
            return trust >= threshold, trust
        except Exception as e:
            logger.debug(f"meta should_execute fallback: {e}")
            return True, 0.5

    def status(self) -> dict:
        return {
            "trained": self.is_trained,
            "samples": self.n_samples,
            "val_accuracy": round(self.val_accuracy, 3) if self.val_accuracy is not None else None,
        }
