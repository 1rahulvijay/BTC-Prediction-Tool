import numpy as np
import scipy.stats as stats
import logging

QUARANTINED = True
ALLOW_ENV = "BTC_ALLOW_LEGACY_PM_MODEL"


class QuarantinedPrototype(RuntimeError):
    """This module may not be used for anything that informs a decision."""


def _refuse(reason: str) -> None:
    import os

    if os.environ.get(ALLOW_ENV) == "1":
        print("[polymarket_model] QUARANTINE OVERRIDDEN via " + ALLOW_ENV
              + " - output is NOT evidence: " + reason, flush=True)
        return
    raise QuarantinedPrototype(
        "polymarket_model is QUARANTINED (2026-07-28). " + reason + " Set "
        + ALLOW_ENV + "=1 only for isolated research; its output may never inform a "
        "decision, a backtest result or a promotion.")



logger = logging.getLogger(__name__)

class PolymarketModel:
    def __init__(self):
        _refuse('layer_2_residual_correction returns a placeholder 0.0, missing reference prices return 0.5, and "calibration" is only clipping to [0.01, 0.99]. Adding a residual trained on one horizon to a baseline for another is not a valid probability operation.')
        self.residual_models = {
            '5m': None,
            '15m': None
        }
        self.calibrators = {
            '5m': None,
            '15m': None
        }
        # In a real environment, load catboost models here if they exist
        
    def layer_1_mathematical_baseline(self, btc_price: float, ref_price: float, seconds_remaining: float, vol_annual: float) -> float:
        """
        Calculates P(BTC > ref_price at expiration) using a lognormal approximation.
        This provides the Layer 1 anchor probability.
        """
        if ref_price <= 0:
            return 0.5
            
        if seconds_remaining <= 0:
            return 1.0 if btc_price >= ref_price else 0.0
            
        t_years = seconds_remaining / (365 * 24 * 3600)
        if t_years <= 0:
            return 1.0 if btc_price >= ref_price else 0.0
            
        # Using Black-Scholes binary option approximation with mu = 0
        d2 = (np.log(btc_price / ref_price) - (vol_annual**2 / 2) * t_years) / (vol_annual * np.sqrt(t_years) + 1e-9)
        p_yes = stats.norm.cdf(d2)
        return float(p_yes)
        
    def layer_2_residual_correction(self, baseline_prob: float, features: dict, horizon: str = '5m') -> float:
        """
        CatBoost residual correction to fix biases in the baseline model.
        """
        # Placeholder for actual model inference
        # If the model predicts +0.02, it means the market favors YES more than lognormal math suggests
        if not self.residual_models.get(horizon):
            return 0.0
            
        # dummy logic for now
        return 0.0
        
    def layer_3_calibrate(self, raw_prob: float, horizon: str = '5m') -> float:
        """
        Applies Isotonic or Platt scaling.
        """
        return float(np.clip(raw_prob, 0.01, 0.99))
        
    def predict_fair_value(self, market: dict, features: dict, btc_price: float, vol_annual: float, horizon: str = '5m') -> float:
        ref_price = market.get('reference_price', 0.0)
        seconds_remaining = features.get('seconds_remaining', 0.0)
        
        baseline = self.layer_1_mathematical_baseline(btc_price, ref_price, seconds_remaining, vol_annual)
        residual = self.layer_2_residual_correction(baseline, features, horizon)
        
        final_raw = baseline + residual
        calibrated = self.layer_3_calibrate(final_raw, horizon)
        
        return calibrated
