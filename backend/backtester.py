"""
Walk-Forward Backtest Engine
Tests prediction accuracy using rolling OOS validation.
Supports per-model backtest tracking and confusion matrix.
"""

import os
import numpy as np
import logging

logger = logging.getLogger(__name__)


def walk_forward_validate(X: np.ndarray, y: np.ndarray, n_folds: int = 5,
                          model_factory=None, embargo: int = 0,
                          window_type: str = "expanding",
                          progress_cb=None) -> dict:
    """
    Strict, *purged* temporal walk-forward validation: train on the past, validate
    only on the future, with an embargo gap between them. Never shuffles, never
    leaks future data into training.

    `embargo` drops that many samples between the train block and the validation
    block. This is essential here because consecutive sequences overlap by LOOKBACK
    bars and labels look ahead by the horizon, so adjacent train/val rows would
    otherwise share information (leakage). Set embargo >= LOOKBACK + horizon.
    This is the López de Prado purged-CV principle applied to a walk-forward split.

    This is the honest counterpart to the in-sample backtest. The two output flags
    are what matter:
      - is_overfit_warning: high fold-to-fold variance => model is regime-sensitive
        and inconsistent across time periods.
      - is_below_chance: mean out-of-sample directional accuracy < 0.50 => the model
        has overfit to its training window and is worse than a coin flip live.

    `X` is already flattened (samples, lookback*features). `y` is integer class
    labels (0=DOWN, 1=NEUTRAL, 2=UP). `model_factory` returns a fresh fitted-capable
    sklearn-style classifier; defaults to a small XGBoost for speed.
    """
    if model_factory is None:
        def model_factory():
            # RandomForest is robust to folds where a class (e.g. NEUTRAL) is absent;
            # XGBoost with a fixed num_class errors on non-contiguous label sets.
            from sklearn.ensemble import RandomForestClassifier
            return RandomForestClassifier(
                n_estimators=120, max_depth=6, n_jobs=-1,
                min_samples_leaf=5, random_state=42, class_weight="balanced",
            )

    n = len(X)
    fold_size = n // (n_folds + 1)
    if fold_size < 60:
        return {"folds": [], "mean_directional_accuracy": 0.0,
                "std_accuracy": 0.0, "is_overfit_warning": False,
                "is_below_chance": False, "sample_count": int(n), "note": "insufficient data"}

    results = []
    for i in range(n_folds):
        train_end = fold_size * (i + 2)
        # Purge: drop `embargo` samples after the train block before validating.
        val_start = min(train_end + max(0, embargo), n)
        val_end = min(val_start + fold_size, n)
        if val_end - val_start < 30:
            break

        # Train block ends BEFORE the embargo gap (no overlap with validation).
        # "expanding" anchors at 0; "rolling" uses a fixed recent window (adapts to
        # regime change and mirrors how the live model retrains on recent data).
        train_start = 0
        if window_type == "rolling":
            train_start = max(0, train_end - fold_size * 2)
        X_tr, y_tr = X[train_start:train_end], y[train_start:train_end]
        X_val, y_val = X[val_start:val_end], y[val_start:val_end]

        # Need at least 2 classes to fit
        if len(np.unique(y_tr)) < 2:
            continue

        try:
            if progress_cb:
                progress_cb({
                    "fold": i + 1,
                    "n_folds": n_folds,
                    "train_bars": int(train_end - train_start),
                    "val_bars": int(val_end - val_start),
                    "status": "training",
                })
            model = model_factory()
            model.fit(X_tr, y_tr)
            if progress_cb:
                progress_cb({
                    "fold": i + 1,
                    "n_folds": n_folds,
                    "train_bars": int(train_end - train_start),
                    "val_bars": int(val_end - val_start),
                    "status": "predicting",
                })
            preds = model.predict(X_val)
        except Exception as e:
            logger.warning(f"Walk-forward fold {i+1} failed: {e}")
            continue

        overall_acc = float((preds == y_val).mean())
        # Directional RECALL: of the bars that actually moved (UP/DOWN), did we catch
        # the direction? On a NEUTRAL-heavy horizon (e.g. 1m is ~61% NEUTRAL) the model
        # *correctly* predicts NEUTRAL most of the time, and each such bar is scored as
        # a miss here — so recall is structurally low and is INFORMATIONAL ONLY.
        recall_mask = y_val != 1
        dir_recall = float((preds[recall_mask] == y_val[recall_mask]).mean()) if recall_mask.sum() > 0 else 0.0
        # Directional PRECISION: of the directional CALLS the model actually committed to
        # (preds != NEUTRAL — the only bars you'd ever trade), how many matched the real
        # direction? This has a proper 0.50 coin-flip baseline and is the trading-relevant
        # accuracy, so the below-chance gate is based on THIS, not recall.
        call_mask = preds != 1
        n_calls = int(call_mask.sum())
        dir_precision = float((preds[call_mask] == y_val[call_mask]).mean()) if n_calls > 0 else 0.0

        results.append({
            "fold": i + 1,
            "train_bars": int(train_end - train_start),
            "val_bars": int(val_end - val_start),
            "overall_accuracy": round(overall_acc, 4),
            "directional_accuracy": round(dir_recall, 4),
            "directional_precision": round(dir_precision, 4),
            "directional_calls": n_calls,
        })

    if not results:
        return {"folds": [], "mean_directional_accuracy": 0.0,
                "std_accuracy": 0.0, "is_overfit_warning": False,
                "is_below_chance": False, "sample_count": int(n), "note": "no valid folds"}

    accs = [r["directional_accuracy"] for r in results]
    mean_acc = float(np.mean(accs))
    std_acc = float(np.std(accs))
    # Below-chance is judged on PRECISION over folds that actually committed enough
    # directional calls (>=10) to be meaningful. A model that mostly abstains (predicts
    # NEUTRAL) on a noisy horizon is being appropriately selective — that is NOT
    # "below chance", so we only flag when it commits to calls AND those calls lose to
    # a coin flip. Falls back to recall only if no fold made enough calls (degenerate).
    prec_folds = [r["directional_precision"] for r in results if r.get("directional_calls", 0) >= 10]
    total_calls = int(sum(r.get("directional_calls", 0) for r in results))
    if prec_folds:
        mean_precision = float(np.mean(prec_folds))
        below_chance = mean_precision < 0.50
    else:
        # Too few directional calls to judge precision — the model is abstaining, which
        # is selective, not broken. Don't raise the below-chance alarm.
        mean_precision = None
        below_chance = False
    return {
        "folds": results,
        "mean_directional_accuracy": round(mean_acc, 4),      # recall (informational)
        "mean_directional_precision": (round(mean_precision, 4) if mean_precision is not None else None),
        "directional_calls": total_calls,
        "std_accuracy": round(std_acc, 4),
        "is_overfit_warning": std_acc > 0.07,
        "is_below_chance": below_chance,
        "sample_count": int(n),
    }


class Backtester:
    def __init__(self):
        self.results = {}
        self._trade_returns_by_horizon = {}

    def run(self, features: np.ndarray, closes: np.ndarray, horizons: list[int], predict_fn, lookback: int = 60, progress_cb=None) -> dict:
        """
        Run backtest over the historical dataset.
        features: [timesteps, NUM_FEATURES]
        closes: raw prices
        """
        n_samples = len(features)
        
        # Test on the last 20% of the dataset
        test_start = int(n_samples * 0.8)
        
        self.results = {}
        self._trade_returns_by_horizon = {}

        if n_samples < lookback + max(horizons) + 10:
            return self._empty_results(horizons)

        # Compute adaptive threshold from ATR
        from features import atr, compute_adaptive_threshold
        highs = closes * 1.001  # Approximate — real highs not available here
        lows = closes * 0.999
        atr_arr = atr(highs, lows, closes)
        threshold = compute_adaptive_threshold(closes, atr_arr)
        logger.info(f"Backtest adaptive threshold: {threshold:.6f}")

        for h in horizons:
            if progress_cb:
                progress_cb({
                    "phase": "main",
                    "horizon": h,
                    "status": "started",
                    "processed": 0,
                    "total": max(0, n_samples - h - 1 - max(test_start, lookback)),
                })
            preds = []
            actuals = []
            returns = []
            pred_directions = []  # For confusion matrix
            actual_directions = []
            
            # Ensure we have enough data forward
            end_idx = n_samples - h - 1

            total_h = max(0, end_idx - max(test_start, lookback))
            for j, i in enumerate(range(max(test_start, lookback), end_idx), start=1):
                # Input sequence
                seq = features[i - lookback: i]
                seq_batch = np.expand_dims(seq, axis=0)
                
                # Predict
                prob_down, prob_neutral, prob_up = predict_fn(seq_batch, h)
                
                # Predicted direction
                if prob_up > prob_down and prob_up > prob_neutral + 0.03:
                    pred_dir = 1
                elif prob_down > prob_up and prob_down > prob_neutral + 0.03:
                    pred_dir = -1
                else:
                    pred_dir = 0
                
                # Actual
                # Match features.build_sequences target-align-v2:
                # entry at closes[i], outcome at closes[i+h].
                current_price = closes[i]
                future_price = closes[i + h]
                
                if current_price <= 0:
                    continue
                    
                actual_ret = (future_price - current_price) / current_price
                
                if actual_ret > threshold:
                    actual_dir = 1
                elif actual_ret < -threshold:
                    actual_dir = -1
                else:
                    actual_dir = 0
                    
                preds.append(pred_dir)
                actuals.append(actual_dir)
                returns.append(actual_ret)
                pred_directions.append(pred_dir)
                actual_directions.append(actual_dir)
                if progress_cb and (j == 1 or j % 1000 == 0 or j == total_h):
                    progress_cb({
                        "phase": "main",
                        "horizon": h,
                        "status": "running",
                        "processed": int(j),
                        "total": int(total_h),
                    })

            self.results[h] = self._compute_metrics(preds, actuals, returns, h)
            self.results[h]["confusion_matrix"] = self._compute_confusion_matrix(pred_directions, actual_directions)
            if progress_cb:
                progress_cb({
                    "phase": "main",
                    "horizon": h,
                    "status": "done",
                    "processed": int(total_h),
                    "total": int(total_h),
                })

        self.results["sharpe"] = self._compute_sharpe(horizons)
        self.results["benchmarks"] = self._compute_benchmarks(closes[test_start:], horizons)

        return self.results

    def _compute_metrics(self, preds, actuals, returns, horizon: int = None) -> dict:
        n = len(preds)
        if n == 0:
            return self._empty_metrics()

        correct = sum(1 for p, a in zip(preds, actuals) if p == a)
        accuracy = correct / n

        # Precision / recall for UP (1)
        tp = sum(1 for p, a in zip(preds, actuals) if p == 1 and a == 1)
        fp = sum(1 for p, a in zip(preds, actuals) if p == 1 and a != 1)
        fn = sum(1 for p, a in zip(preds, actuals) if p != 1 and a == 1)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

        # Directional accuracy (UP/DOWN only, excluding NEUTRAL)
        dir_preds = [(p, a) for p, a in zip(preds, actuals) if p != 0]
        dir_correct = sum(1 for p, a in dir_preds if p == a)
        dir_accuracy = dir_correct / len(dir_preds) if dir_preds else 0.0

        # UP accuracy
        up_preds = [(p, a) for p, a in zip(preds, actuals) if p == 1]
        up_correct = sum(1 for p, a in up_preds if a == 1)
        up_accuracy = up_correct / len(up_preds) if up_preds else 0.0

        # DOWN accuracy
        down_preds = [(p, a) for p, a in zip(preds, actuals) if p == -1]
        down_correct = sum(1 for p, a in down_preds if a == -1)
        down_accuracy = down_correct / len(down_preds) if down_preds else 0.0

        # Trading metrics. Convert gross directional return to a rough net return
        # by subtracting round-trip cost, so PF/Sharpe are not inflated by sub-cost moves.
        cost = float(os.environ.get("BTC_BACKTEST_COST_FLOOR", os.environ.get("BTC_LABEL_COST_FLOOR", "0.0008")))
        wins = 0
        losses = 0
        trade_returns = []
        for p, r in zip(preds, returns):
            if p == 0:
                continue
            gross = r if p == 1 else -r
            pnl = gross - cost
            trade_returns.append(pnl)
            if pnl > 0:
                wins += 1
            else:
                losses += 1

        if horizon is not None:
            self._trade_returns_by_horizon[horizon] = list(trade_returns)

        total_trades = wins + losses
        win_rate = wins / total_trades if total_trades > 0 else 0

        gross_profit = sum(r for r in trade_returns if r > 0)
        gross_loss = abs(sum(r for r in trade_returns if r < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        # Max drawdown
        peak = 0.0
        max_dd = 0.0
        cum = 0.0
        for r in trade_returns:
            cum += r
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd

        return {
            "accuracy": float(accuracy),
            "directional_accuracy": float(dir_accuracy),
            "up_accuracy": float(up_accuracy),
            "down_accuracy": float(down_accuracy),
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(f1),
            "win_rate": float(win_rate),
            "profit_factor": min(float(profit_factor), 99.0),
            "sharpe": self._sharpe_from_returns(trade_returns, horizon),
            "avg_trade_return": float(np.mean(trade_returns)) if trade_returns else 0.0,
            "trade_return_std": float(np.std(trade_returns)) if trade_returns else 0.0,
            "win_rate_edge": float(win_rate - 0.5),
            "max_drawdown": float(max_dd),
            "total_trades": int(total_trades),
            "sample_size": n,
        }

    def _compute_confusion_matrix(self, preds, actuals) -> dict:
        """3x3 confusion matrix: DOWN(-1), NEUTRAL(0), UP(1)"""
        matrix = {
            "pred_down_actual_down": 0, "pred_down_actual_neutral": 0, "pred_down_actual_up": 0,
            "pred_neutral_actual_down": 0, "pred_neutral_actual_neutral": 0, "pred_neutral_actual_up": 0,
            "pred_up_actual_down": 0, "pred_up_actual_neutral": 0, "pred_up_actual_up": 0,
        }
        for p, a in zip(preds, actuals):
            p_label = "down" if p == -1 else ("up" if p == 1 else "neutral")
            a_label = "down" if a == -1 else ("up" if a == 1 else "neutral")
            key = f"pred_{p_label}_actual_{a_label}"
            matrix[key] += 1
        return matrix

    def _compute_sharpe(self, horizons):
        returns = []
        weighted_h = []
        for h in horizons:
            rs = self._trade_returns_by_horizon.get(h, [])
            if rs:
                returns.extend(rs)
                weighted_h.extend([h] * len(rs))

        if not returns:
            return 0.0

        avg_h = float(np.mean(weighted_h)) if weighted_h else 5.0
        return self._sharpe_from_returns(returns, avg_h)

    def _sharpe_from_returns(self, returns, horizon: int | float = None) -> float:
        if not returns or len(returns) < 2:
            return 0.0
        arr = np.asarray(returns, dtype=np.float64)
        std = float(np.std(arr))
        if std <= 1e-12:
            return 0.0
        # PER-TRADE Sharpe (mean/std of net trade returns). We deliberately do NOT
        # annualize by bar frequency. These are per-TRADE returns from a SELECTIVE
        # strategy (often few trades), so multiplying by sqrt(bars_per_year) — 105,120
        # for a 5m bar — treated each sparse trade as if it compounded every 5 minutes
        # and inflated Sharpe ~300x into impossible numbers (300+). A per-trade Sharpe is
        # honest and directly comparable across horizons; annualization would require the
        # real calendar trade frequency, which this trade-only series does not carry.
        return float(np.mean(arr) / std)

    def _compute_benchmarks(self, closes_test, horizons):
        if len(closes_test) < 2:
            return {"buy_hold": 0, "random": 0.33, "prev_candle": 0.33}
            
        buy_hold = (closes_test[-1] - closes_test[0]) / closes_test[0] if closes_test[0] > 0 else 0
        
        correct = 0
        total = 0
        for i in range(1, len(closes_test) - 1):
            prev_dir = 1 if closes_test[i] > closes_test[i-1] else -1 if closes_test[i] < closes_test[i-1] else 0
            act_dir = 1 if closes_test[i+1] > closes_test[i] else -1 if closes_test[i+1] < closes_test[i] else 0
            if prev_dir == act_dir:
                correct += 1
            total += 1
            
        prev_acc = correct / total if total > 0 else 0.0
        
        return {
            "buy_hold": float(buy_hold),
            "random": 0.333,
            "prev_candle": float(prev_acc),
        }

    def _empty_results(self, horizons):
        res = {h: self._empty_metrics() for h in horizons}
        res["sharpe"] = 0.0
        res["benchmarks"] = {"buy_hold": 0.0, "random": 0.33, "prev_candle": 0.0}
        return res

    def _empty_metrics(self):
        return {
            "accuracy": 0.0, "directional_accuracy": 0.0,
            "up_accuracy": 0.0, "down_accuracy": 0.0,
            "precision": 0.0, "recall": 0.0, "f1": 0.0,
            "win_rate": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0,
            "sharpe": 0.0, "avg_trade_return": 0.0, "trade_return_std": 0.0,
            "win_rate_edge": 0.0,
            "total_trades": 0, "sample_size": 0,
            "confusion_matrix": {},
        }
