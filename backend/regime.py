import time
import numpy as np
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

try:
    from sklearn.mixture import GaussianMixture
    HAS_GMM = True
except ImportError:
    HAS_GMM = False

class MarketRegime:
    """
    Market Regime Engine for classifying current market conditions.
    Provides states: TRENDING_UP, TRENDING_DOWN, RANGE, HIGH_VOLATILITY, LOW_VOLATILITY
    """

    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"

    def __init__(self):
        self.current_regime = self.RANGE
        self.confidence = 0.0
        # Regime memory: how long the current regime has held, and learned
        # transition frequencies (from_regime -> {to_regime: count}).
        self.regime_start_time = time.time()
        self.transition_counts = defaultdict(lambda: defaultdict(int))
        # Learned-regime (HMM) state — replaces fixed ADX/ATR thresholds once fit.
        # Gaussian-emission HMM: GMM fits the emission densities, transitions are
        # estimated empirically, and an online forward filter gives temporally
        # coherent (sticky) regime states rather than independent per-bar guesses.
        self.hmm_ready = False
        self.state_labels = {}
        self._means = None
        self._inv_covs = None
        self._logdets = None
        self._transmat = None
        self._belief = None
        self._k = 3

    def _make_obs(self, closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
        """Observation matrix for GMM: [log_return, |log_return|, volume_ratio]."""
        closes = np.asarray(closes, dtype=np.float64)
        volumes = np.asarray(volumes, dtype=np.float64)
        logret = np.zeros(len(closes))
        logret[1:] = np.diff(np.log(closes + 1e-9))
        med_vol = np.median(volumes) if len(volumes) else 1.0
        vol_ratio = np.clip(volumes / (med_vol + 1e-9), 0, 10)
        obs = np.column_stack([logret, np.abs(logret), vol_ratio])
        return obs[1:]  # drop the leading zero-return row

    def _map_states(self, means: np.ndarray) -> dict:
        """Map learned states to interpretable labels by centroid (vol level + drift)."""
        absret = means[:, 1]
        ret = means[:, 0]
        hi = int(np.argmax(absret)); lo = int(np.argmin(absret))
        ret_std = np.std(ret) + 1e-9
        labels = {}
        for k in range(len(means)):
            if k == hi:
                labels[k] = self.HIGH_VOLATILITY
            elif k == lo:
                labels[k] = self.LOW_VOLATILITY
            elif ret[k] > 0.3 * ret_std:
                labels[k] = self.TRENDING_UP
            elif ret[k] < -0.3 * ret_std:
                labels[k] = self.TRENDING_DOWN
            else:
                labels[k] = self.RANGE
        return labels

    def fit_hmm(self, closes: np.ndarray, volumes: np.ndarray, n_states: int = 5) -> bool:
        """
        Fit a Gaussian-emission HMM. GMM provides the emission densities (means +
        full covariances); the transition matrix is estimated empirically from the
        decoded state path (with Laplace smoothing). Inference uses an online forward
        filter, so regimes are temporally coherent (sticky) instead of independent
        per-bar guesses — this is the key difference from the previous GMM-only engine.
        """
        if not HAS_GMM or len(closes) < 500:
            return False
        try:
            obs = self._make_obs(closes, volumes)
            if len(obs) < 200:
                return False
            if len(obs) > 40000:
                idx = np.random.RandomState(7).choice(len(obs), 40000, replace=False)
                fit_obs = obs[idx]
            else:
                fit_obs = obs
            gmm = GaussianMixture(n_components=n_states, covariance_type="full",
                                  max_iter=100, random_state=42, reg_covar=1e-5)
            gmm.fit(fit_obs)
            means = gmm.means_           # [n_states, 3]
            covars = gmm.covariances_    # [n_states, 3, 3]

            # Empirical transition matrix from the sequential decoded path.
            states = gmm.predict(obs)
            T = np.ones((n_states, n_states))  # Laplace smoothing
            for a, b in zip(states[:-1], states[1:]):
                T[a, b] += 1
            T = T / T.sum(axis=1, keepdims=True)

            # Precompute inverse covariance + log-determinant per state.
            k = means.shape[1]
            inv_covs, logdets = [], []
            for s in range(n_states):
                cov = covars[s] + np.eye(k) * 1e-6
                inv_covs.append(np.linalg.inv(cov))
                _, logdet = np.linalg.slogdet(cov)
                logdets.append(logdet)

            self._means = means
            self._inv_covs = inv_covs
            self._logdets = logdets
            self._k = k
            self._transmat = T
            self.state_labels = self._map_states(means)
            self._belief = np.full(n_states, 1.0 / n_states)
            self.hmm_ready = True
            logger.info(f"HMM regimes fit: labels {self.state_labels}")
            return True
        except Exception as e:
            logger.error(f"HMM regime fit failed: {e}")
            self.hmm_ready = False
            return False

    def classify_series(self, closes: np.ndarray, volumes: np.ndarray) -> list:
        """Per-bar COARSE regime (TREND / RANGE / VOLATILE) for the WHOLE series via the
        fitted GMM emissions, aligned to `closes` (index 0 mirrors index 1). This is the
        P4.3 train/serve-alignment primitive: training rows get labelled with the SAME
        partition serving routes by (`_get_regime_from_state` → same 3 buckets), instead of
        the old ADX/vol threshold clustering that served a different partition than it
        trained on. Falls back to all-RANGE if the HMM isn't ready."""
        n = len(closes)
        coarse = ["RANGE"] * n
        if not getattr(self, "hmm_ready", False) or getattr(self, "_means", None) is None:
            return coarse
        try:
            obs = self._make_obs(closes, volumes)  # aligned to closes[1:]
            for j in range(len(obs)):
                fine = self.state_labels.get(int(np.argmax(self._emission_loglik(obs[j]))),
                                             self.RANGE)
                if fine in (self.TRENDING_UP, self.TRENDING_DOWN):
                    coarse[j + 1] = "TREND"
                elif fine == self.HIGH_VOLATILITY:
                    coarse[j + 1] = "VOLATILE"
                else:                              # RANGE, LOW_VOLATILITY
                    coarse[j + 1] = "RANGE"
            if n > 1:
                coarse[0] = coarse[1]
        except Exception as e:
            logger.warning(f"classify_series failed, defaulting to RANGE: {e}")
        return coarse

    def _emission_loglik(self, x: np.ndarray) -> np.ndarray:
        """Per-state Gaussian log-likelihood of observation x."""
        ll = np.zeros(len(self._means))
        for s in range(len(self._means)):
            d = x - self._means[s]
            maha = float(d @ self._inv_covs[s] @ d)
            ll[s] = -0.5 * (self._k * np.log(2 * np.pi) + self._logdets[s] + maha)
        return ll

    def _hmm_classify(self, closes: np.ndarray, volumes: np.ndarray):
        """Online forward filter: belief_t ∝ (belief_{t-1} · T) × emission(x_t)."""
        obs = self._make_obs(closes[-5:], volumes[-5:])
        if len(obs) == 0:
            return self.current_regime, 0.5
        x = obs[-1]
        ll = self._emission_loglik(x)
        prior = self._belief @ self._transmat
        logp = np.log(prior + 1e-12) + ll
        logp -= logp.max()
        post = np.exp(logp)
        post /= (post.sum() + 1e-12)
        self._belief = post
        s = int(np.argmax(post))
        return self.state_labels.get(s, self.RANGE), float(post[s])

    def _next_regime_forecast(self) -> dict:
        """Most likely next regime based on observed transition frequencies."""
        outgoing = self.transition_counts.get(self.current_regime, {})
        total = sum(outgoing.values())
        if total < 5:
            return {"next_likely": None, "probability": 0.0}
        nxt, cnt = max(outgoing.items(), key=lambda kv: kv[1])
        return {"next_likely": nxt, "probability": round(cnt / total, 3)}

    def forecast_transitions(self, steps: list[int] = None) -> dict:
        """
        Multi-step regime transition forecast using the HMM transition matrix.
        P(regime at t+k) = belief @ T^k
        Returns per-regime probabilities at each forecast horizon.
        """
        if steps is None:
            steps = [5, 15, 30]
        if not self.hmm_ready or self._transmat is None or self._belief is None:
            return {"forecasts": {}, "available": False}
        forecasts = {}
        for k in steps:
            future_belief = self._belief.copy()
            # Matrix power: belief @ T^k (iterative to avoid numerical issues)
            T = self._transmat
            Tk = np.linalg.matrix_power(T, k) if k <= 100 else T  # cap for safety
            future_belief = future_belief @ Tk
            future_belief = np.clip(future_belief, 0, None)
            future_belief /= (future_belief.sum() + 1e-12)
            dist = {}
            for s, label in self.state_labels.items():
                if s < len(future_belief):
                    dist[label] = round(float(future_belief[s]), 4)
            forecasts[f"{k}bar"] = dist
        return {"forecasts": forecasts, "available": True}

    def get_confidence_vector(self) -> dict:
        """
        Return the full posterior distribution over regimes instead of a single label.
        Enables proportional blending in the model when the regime is ambiguous.
        """
        if not self.hmm_ready or self._belief is None:
            # Fallback: 100% in current regime
            return {self.current_regime: 1.0}
        vec = {}
        for s, label in self.state_labels.items():
            if s < len(self._belief):
                prob = float(self._belief[s])
                vec[label] = vec.get(label, 0.0) + prob
        # Normalize
        total = sum(vec.values()) + 1e-12
        return {k: round(v / total, 4) for k, v in vec.items()}

    def get_regime_entropy(self) -> float:
        """Shannon entropy of the regime belief vector. High = uncertain."""
        vec = self.get_confidence_vector()
        probs = np.array(list(vec.values()))
        probs = probs[probs > 1e-9]
        if len(probs) <= 1:
            return 0.0
        return float(-np.sum(probs * np.log2(probs)))

    def forecast_volatility(self, closes: np.ndarray, volumes: np.ndarray) -> dict:
        """
        Forecast future volatility using EWMA extrapolation of recent vol states.
        Returns normalized vol estimates for 1m, 5m, 15m ahead.
        """
        if len(closes) < 30:
            return {"vol_forecast_1m": 0.0, "vol_forecast_5m": 0.0, "vol_forecast_15m": 0.0}
        log_returns = np.diff(np.log(closes[-60:] + 1e-9))
        if len(log_returns) < 5:
            return {"vol_forecast_1m": 0.0, "vol_forecast_5m": 0.0, "vol_forecast_15m": 0.0}
        # EWMA vol (RiskMetrics style)
        lam = 0.94
        var_ewma = float(np.var(log_returns[:5]))
        for r in log_returns[5:]:
            var_ewma = lam * var_ewma + (1 - lam) * r * r
        vol_1m = float(np.sqrt(var_ewma))
        # Scale by sqrt(t) for longer horizons
        vol_5m = vol_1m * np.sqrt(5)
        vol_15m = vol_1m * np.sqrt(15)
        # Normalize to 0-1 range (typical BTC 1m vol is 0.001-0.01)
        def norm(v):
            return min(1.0, v / 0.015)
        return {
            "vol_forecast_1m": round(norm(vol_1m), 4),
            "vol_forecast_5m": round(norm(vol_5m), 4),
            "vol_forecast_15m": round(norm(vol_15m), 4),
        }

    def detect_regime(self, closes: np.ndarray, adx_arr: np.ndarray, atr_arr: np.ndarray, volumes: np.ndarray) -> dict:
        """
        Classifies the market regime using the latest indicators.
        Requires at least 100 historical points for robust baseline stats.
        """
        if len(closes) < 100 or len(adx_arr) < 100 or len(atr_arr) < 100:
            return {"regime": self.current_regime, "confidence": 0.0}

        # Latest values
        c_adx = adx_arr[-1]
        c_atr = atr_arr[-1]
        c_price = closes[-1]
        c_vol = volumes[-1]

        if np.isnan(c_adx) or np.isnan(c_atr):
            return {"regime": self.current_regime, "confidence": 0.0}

        # Historical medians
        valid_atr = atr_arr[~np.isnan(atr_arr)]
        median_atr = np.median(valid_atr[-100:])
        median_vol = np.median(volumes[-100:])
        
        # Volatility Check
        volatility_ratio = c_atr / median_atr if median_atr > 0 else 1.0
        
        # Volume Spike
        volume_spike = c_vol / median_vol if median_vol > 0 else 1.0

        # Trend detection
        is_trending = c_adx > 25
        trend_strength = min(c_adx / 50.0, 1.0) # Confident around ADX=50
        
        # Direction
        price_ma20 = np.mean(closes[-20:])
        is_up = c_price > price_ma20

        new_regime = self.RANGE
        conf = 0.5

        if self.hmm_ready:
            # Learned temporal regimes (HMM forward filter) take precedence.
            new_regime, conf = self._hmm_classify(closes, volumes)
        elif volatility_ratio > 2.0 or volume_spike > 5.0:
            new_regime = self.HIGH_VOLATILITY
            conf = min(volatility_ratio / 4.0, 1.0)
        elif volatility_ratio < 0.5:
            new_regime = self.LOW_VOLATILITY
            conf = 1.0 - volatility_ratio
        elif is_trending:
            new_regime = self.TRENDING_UP if is_up else self.TRENDING_DOWN
            conf = trend_strength
        else:
            new_regime = self.RANGE
            conf = 1.0 - trend_strength # Confidence in range is opposite of trend strength

        # ── Regime memory: track transitions and duration ──
        if new_regime != self.current_regime:
            self.transition_counts[self.current_regime][new_regime] += 1
            self.regime_start_time = time.time()
        self.current_regime = new_regime
        self.confidence = conf

        duration_s = time.time() - self.regime_start_time
        forecast = self._next_regime_forecast()
        transition_forecast = self.forecast_transitions()
        confidence_vector = self.get_confidence_vector()
        vol_forecast = self.forecast_volatility(closes, volumes)

        return {
            "regime": self.current_regime,
            "confidence": float(self.confidence),
            "volatility_ratio": float(volatility_ratio),
            "adx": float(c_adx),
            "duration_seconds": int(duration_s),
            "duration_min": round(duration_s / 60.0, 1),
            "next_likely": forecast["next_likely"],
            "transition_probability": forecast["probability"],
            "method": "hmm" if self.hmm_ready else "threshold",
            # New: full confidence vector and transition forecasting
            "confidence_vector": confidence_vector,
            "regime_entropy": round(self.get_regime_entropy(), 4),
            "transition_forecast": transition_forecast,
            "vol_forecast": vol_forecast,
        }
