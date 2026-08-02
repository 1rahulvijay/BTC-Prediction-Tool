"""Day/week block confidence intervals."""
from __future__ import annotations

import numpy as np
import pandas as pd


def block_mean_interval(
    values: np.ndarray,
    timestamps_ms: np.ndarray,
    *,
    block: str,
    seed: int,
    iterations: int = 500,
    confidence: float = 0.95,
) -> dict[str, float | int | None]:
    frame = pd.DataFrame({
        "value": np.asarray(values, dtype=float),
        "ts": pd.to_datetime(np.asarray(timestamps_ms, dtype=np.int64), unit="ms", utc=True),
    }).dropna()
    if block == "day":
        frame["bucket"] = frame["ts"].dt.strftime("%Y-%m-%d")
    elif block == "week":
        iso = frame["ts"].dt.isocalendar()
        frame["bucket"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str)
    else:
        raise ValueError("block must be day or week")
    grouped = frame.groupby("bucket", sort=True)["value"].sum().to_numpy(dtype=float)
    if len(grouped) < 2:
        return {"mean": float(frame["value"].mean()) if len(frame) else None,
                "lower": None, "upper": None, "blocks": int(len(grouped))}
    rng = np.random.default_rng(seed)
    draws = rng.choice(grouped, size=(int(iterations), len(grouped)), replace=True).mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    return {
        "mean": float(grouped.mean()),
        "lower": float(np.quantile(draws, alpha)),
        "upper": float(np.quantile(draws, 1.0 - alpha)),
        "blocks": int(len(grouped)),
    }


def selftest() -> None:
    ts = np.arange(10) * 86_400_000 + 1_700_000_000_000
    result = block_mean_interval(np.ones(10), ts, block="day", seed=1, iterations=100)
    assert result["blocks"] == 10 and result["lower"] == 1.0

