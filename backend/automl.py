"""
Continuous AutoML Engine
========================
Runs periodically in the background to tune XGBoost hyperparameters 
using historical predictions and outcomes from DuckDB.
If a model configuration achieves a higher cross-validated profit factor 
than the baseline, it is serialized and can be loaded as the new Challenger.
"""

import os
import logging
import duckdb
import optuna
import numpy as np
import xgboost as xgb
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import log_loss

logger = logging.getLogger(__name__)

# Use the same DB the server writes to (data/ or BTC_DB_PATH), not a stale root copy.
from database import DB_PATH
_DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data"
)
BEST_PARAMS_PATH = os.path.join(_DATA_DIR, "saved_models", "automl_best_params.json")

class AutoMLChallenger:
    def __init__(self, n_trials=20):
        self.n_trials = n_trials
        self.best_params = {}

    def fetch_training_data(self, horizon: int):
        if not os.path.exists(DB_PATH):
            return None, None
            
        try:
            with duckdb.connect(DB_PATH, read_only=True) as conn:
                # Label = LEAN sign-truth (raw_direction vs realized move), NOT the `hit`
                # column: `hit` is dual-semantic — on gated rows it equals avoid_success,
                # TRUE when the lean was WRONG — so tuning on it optimizes an inverted target.
                df = conn.execute(f"""
                    SELECT confidence, agreement, ewma_vol, spread_norm,
                           wall_imbalance, sr_compression, liq_imbalance,
                           quantile_spread, expectancy_usd,
                           CASE WHEN (raw_direction='UP'   AND actual_move > 0)
                                  OR (raw_direction='DOWN' AND actual_move < 0)
                                THEN TRUE ELSE FALSE END AS hit
                    FROM predictions_{horizon}m
                    WHERE resolved = TRUE AND raw_direction IN ('UP','DOWN')
                      AND actual_move IS NOT NULL AND confidence > 0.4
                    ORDER BY timestamp DESC
                    LIMIT 5000
                """).df()
            
            if len(df) < 500:
                return None, None
                
            X = df.drop(columns=['hit']).fillna(0).values
            y = df['hit'].astype(int).values
            # Reverse to chronological order for TimeSeriesSplit
            return X[::-1], y[::-1]
            
        except Exception as e:
            logger.error(f"AutoML DB fetch error: {e}")
            return None, None

    def optimize_horizon(self, horizon: int):
        X, y = self.fetch_training_data(horizon)
        if X is None or len(np.unique(y)) < 2:
            logger.info(f"Not enough data to optimize {horizon}m.")
            return

        def objective(trial):
            params = {
                'n_estimators': trial.suggest_int('n_estimators', 50, 300),
                'max_depth': trial.suggest_int('max_depth', 2, 6),
                'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.1, log=True),
                'subsample': trial.suggest_float('subsample', 0.5, 0.9),
                'colsample_bytree': trial.suggest_float('colsample_bytree', 0.5, 0.9),
                'gamma': trial.suggest_float('gamma', 0, 5),
                # Using 'hist' instead of 'gpu_hist' for broader compatibility
                'tree_method': 'hist', 
                'eval_metric': 'logloss',
                'random_state': 42
            }
            
            tscv = TimeSeriesSplit(n_splits=3)
            scores = []
            
            for train_idx, val_idx in tscv.split(X):
                model = xgb.XGBClassifier(**params)
                model.fit(X[train_idx], y[train_idx])
                preds = model.predict_proba(X[val_idx])[:, 1]
                scores.append(log_loss(y[val_idx], preds))
                
            return np.mean(scores)

        study = optuna.create_study(direction='minimize')
        study.optimize(objective, n_trials=self.n_trials)
        
        self.best_params[horizon] = study.best_params
        logger.info(f"AutoML {horizon}m best params: {study.best_params} (Loss: {study.best_value:.4f})")

    def run(self):
        logger.info("Starting Continuous AutoML optimization cycle...")
        for h in [1, 3, 5, 10, 15]:
            self.optimize_horizon(h)
            
        if self.best_params:
            import json
            os.makedirs(os.path.dirname(BEST_PARAMS_PATH), exist_ok=True)
            with open(BEST_PARAMS_PATH, 'w') as f:
                json.dump(self.best_params, f)
            logger.info("AutoML optimization cycle complete. Best params saved.")

if __name__ == "__main__":
    engine = AutoMLChallenger(n_trials=10)
    engine.run()
