"""Portable calibrated classifier used by isolated research processes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.pipeline import Pipeline


@dataclass
class CalibratedBinary:
    features: list[str]
    medians: pd.Series
    pipeline: Pipeline
    calibrator: IsotonicRegression | None

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        matrix = frame[self.features].replace([np.inf, -np.inf], np.nan)
        matrix = matrix.fillna(self.medians)
        raw = self.pipeline.predict_proba(matrix)[:, 1]
        if self.calibrator is None:
            return np.clip(raw, 1e-6, 1.0 - 1e-6)
        return np.clip(self.calibrator.predict(raw), 1e-6, 1.0 - 1e-6)

    def manifest(self) -> dict[str, Any]:
        return {
            "class": f"{type(self).__module__}.{type(self).__name__}",
            "features": list(self.features),
            "calibrated": self.calibrator is not None,
        }
