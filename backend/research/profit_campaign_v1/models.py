"""Small target-specific models for economic action and exit selection."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


QUANTILES = (0.10, 0.20, 0.50, 0.80, 0.90)


@dataclass(slots=True)
class ProbabilityHead:
    imputer: SimpleImputer | None
    scaler: StandardScaler | None
    model: LogisticRegression | None
    constant: float | None

    @classmethod
    def fit(
        cls,
        features: np.ndarray,
        target: np.ndarray,
        *,
        seed: int,
    ) -> "ProbabilityHead":
        y = np.asarray(target, dtype=int)
        unique = np.unique(y)
        if len(unique) < 2:
            return cls(
                None,
                None,
                None,
                float(unique[0]) if len(unique) else 0.0,
            )
        imputer = SimpleImputer(strategy="median", keep_empty_features=True)
        matrix = imputer.fit_transform(features)
        scaler = StandardScaler()
        matrix = scaler.fit_transform(matrix)
        model = LogisticRegression(
            C=0.5,
            class_weight="balanced",
            max_iter=1_000,
            random_state=seed,
        )
        model.fit(matrix, y)
        return cls(imputer, scaler, model, None)

    def predict(self, features: np.ndarray) -> np.ndarray:
        if self.constant is not None:
            return np.full(len(features), self.constant, dtype=float)
        if self.imputer is None or self.scaler is None or self.model is None:
            raise RuntimeError("probability head is not fitted")
        matrix = self.imputer.transform(features)
        return self.model.predict_proba(self.scaler.transform(matrix))[:, 1]


@dataclass(slots=True)
class QuantileHead:
    imputer: SimpleImputer
    models: dict[float, HistGradientBoostingRegressor]

    @classmethod
    def fit(
        cls,
        features: np.ndarray,
        target: np.ndarray,
        *,
        quantiles: Iterable[float] = QUANTILES,
        seed: int,
    ) -> "QuantileHead":
        y = np.asarray(target, dtype=float)
        if len(y) < 50 or not np.isfinite(y).all():
            raise ValueError("quantile target requires 50+ finite rows")
        imputer = SimpleImputer(strategy="median", keep_empty_features=True)
        matrix = imputer.fit_transform(features)
        models: dict[float, HistGradientBoostingRegressor] = {}
        for quantile in quantiles:
            q = float(quantile)
            if not 0 < q < 1:
                raise ValueError("quantiles must be inside (0,1)")
            model = HistGradientBoostingRegressor(
                loss="quantile",
                quantile=q,
                max_iter=80,
                max_leaf_nodes=15,
                min_samples_leaf=max(20, min(100, len(y) // 50)),
                learning_rate=0.05,
                l2_regularization=5.0,
                random_state=seed + int(q * 100),
            )
            model.fit(matrix, y)
            models[q] = model
        return cls(imputer, models)

    def predict(self, features: np.ndarray) -> dict[float, np.ndarray]:
        matrix = self.imputer.transform(features)
        raw = {
            quantile: model.predict(matrix)
            for quantile, model in self.models.items()
        }
        ordered = sorted(raw)
        stacked = np.column_stack([raw[quantile] for quantile in ordered])
        monotonic = np.maximum.accumulate(stacked, axis=1)
        return {
            quantile: monotonic[:, index]
            for index, quantile in enumerate(ordered)
        }


@dataclass(slots=True)
class EconomicActionHead:
    profit_probability: ProbabilityHead
    net_quantiles: QuantileHead
    slippage_quantiles: QuantileHead
    time_to_profit_quantiles: QuantileHead | None

    @classmethod
    def fit(
        cls,
        features: np.ndarray,
        *,
        net_pnl: np.ndarray,
        slippage_bps: np.ndarray,
        time_to_profit_seconds: np.ndarray,
        seed: int,
    ) -> "EconomicActionHead":
        probability = ProbabilityHead.fit(
            features, np.asarray(net_pnl) > 0, seed=seed
        )
        net_head = QuantileHead.fit(features, net_pnl, seed=seed + 100)
        slippage_head = QuantileHead.fit(
            features,
            np.nan_to_num(
                slippage_bps,
                nan=float(np.nanmedian(slippage_bps))
                if np.isfinite(slippage_bps).any()
                else 0.0,
            ),
            seed=seed + 200,
        )
        observed = np.isfinite(time_to_profit_seconds)
        time_head = None
        if observed.sum() >= 50:
            time_head = QuantileHead.fit(
                features[observed],
                time_to_profit_seconds[observed],
                quantiles=(0.20, 0.50, 0.80),
                seed=seed + 300,
            )
        return cls(probability, net_head, slippage_head, time_head)

    def predict(self, features: np.ndarray) -> dict[str, np.ndarray]:
        output = {
            "p_net_positive": self.profit_probability.predict(features),
        }
        for quantile, values in self.net_quantiles.predict(features).items():
            output[f"net_q{int(quantile * 100):02d}"] = values
        for quantile, values in self.slippage_quantiles.predict(features).items():
            output[f"slippage_q{int(quantile * 100):02d}"] = values
        if self.time_to_profit_quantiles is not None:
            for quantile, values in self.time_to_profit_quantiles.predict(
                features
            ).items():
                output[f"time_to_profit_q{int(quantile * 100):02d}"] = values
        else:
            for quantile in (20, 50, 80):
                output[f"time_to_profit_q{quantile:02d}"] = np.full(
                    len(features), math.nan
                )
        return output


@dataclass(slots=True)
class IncrementalExitHead:
    wait_seconds: tuple[int, ...]
    quantile_heads: dict[int, QuantileHead]
    improvement_heads: dict[int, ProbabilityHead]

    @classmethod
    def fit(
        cls,
        features: np.ndarray,
        targets: dict[int, np.ndarray],
        *,
        seed: int,
    ) -> "IncrementalExitHead":
        quantiles = {}
        probabilities = {}
        for wait_seconds, target in sorted(targets.items()):
            values = np.asarray(target, dtype=float)
            quantiles[wait_seconds] = QuantileHead.fit(
                features,
                values,
                quantiles=(0.20, 0.50, 0.80),
                seed=seed + wait_seconds,
            )
            probabilities[wait_seconds] = ProbabilityHead.fit(
                features,
                values > 0,
                seed=seed + 1_000 + wait_seconds,
            )
        return cls(tuple(sorted(targets)), quantiles, probabilities)

    def predict(self, features: np.ndarray) -> dict[int, dict[str, np.ndarray]]:
        output = {}
        for wait_seconds in self.wait_seconds:
            quantiles = self.quantile_heads[wait_seconds].predict(features)
            output[wait_seconds] = {
                "q20_incremental_pnl": quantiles[0.20],
                "q50_incremental_pnl": quantiles[0.50],
                "q80_incremental_pnl": quantiles[0.80],
                "p_profit_improves": self.improvement_heads[
                    wait_seconds
                ].predict(features),
            }
        return output
