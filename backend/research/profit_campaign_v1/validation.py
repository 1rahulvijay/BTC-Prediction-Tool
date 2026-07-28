"""Economic reporting, split discipline, stress and trial registration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from backend.quant_platform.research_validation import (
    block_bootstrap_mean_interval,
    deflated_sharpe_ratio,
    probability_backtest_overfitting,
    profit_concentration,
)


@dataclass(frozen=True, slots=True)
class TemporalSplit:
    role: str
    train_indices: np.ndarray
    test_indices: np.ndarray


def chronological_splits(
    timestamps_ns: np.ndarray,
    *,
    development_fraction: float,
    folds: int,
    purge_seconds: int,
    embargo_seconds: int,
) -> tuple[list[TemporalSplit], TemporalSplit]:
    timestamps = np.asarray(timestamps_ns, dtype=np.int64)
    if len(timestamps) < 60 or np.any(np.diff(timestamps) < 0):
        raise ValueError("chronological split requires 60+ sorted rows")
    if not 0.5 <= development_fraction < 1.0 or folds < 2:
        raise ValueError("invalid split configuration")
    development_end = int(len(timestamps) * development_fraction)
    final_test = np.arange(development_end, len(timestamps), dtype=int)
    if len(final_test) == 0:
        raise ValueError("untouched final period is empty")
    purge_ns = int(purge_seconds * 1_000_000_000)
    embargo_ns = int(embargo_seconds * 1_000_000_000)
    initial_train_end = max(30, int(development_end * 0.40))
    available = development_end - initial_train_end
    minimum_test_size = 5 if len(timestamps) < 120 else 10
    test_width = max(minimum_test_size, available // folds)
    splits: list[TemporalSplit] = []
    for fold in range(folds):
        raw_test_start = initial_train_end + fold * test_width
        raw_test_end = (
            development_end
            if fold == folds - 1
            else min(development_end, raw_test_start + test_width)
        )
        if raw_test_start >= development_end or raw_test_end <= raw_test_start:
            continue
        test_start_ts = timestamps[raw_test_start]
        train = np.flatnonzero(
            (timestamps < test_start_ts - purge_ns)
            & (np.arange(len(timestamps)) < development_end)
        )
        test_start = int(
            np.searchsorted(timestamps, test_start_ts + embargo_ns, side="left")
        )
        test = np.arange(test_start, raw_test_end, dtype=int)
        test = test[test < development_end]
        if len(train) >= 30 and len(test) >= minimum_test_size:
            splits.append(TemporalSplit(f"walk_forward_{fold + 1}", train, test))
    if len(splits) < 2:
        raise ValueError("split configuration produced fewer than two usable folds")
    final_start_ts = timestamps[development_end]
    final_train = np.flatnonzero(timestamps < final_start_ts - purge_ns)
    final_test = final_test[
        timestamps[final_test] >= final_start_ts + embargo_ns
    ]
    if len(final_train) < 30 or len(final_test) < 10:
        raise ValueError("purge/embargo left an unusable untouched period")
    return splits, TemporalSplit("untouched_test", final_train, final_test)


def _quantile(values: np.ndarray, probability: float) -> float | None:
    return float(np.quantile(values, probability)) if len(values) else None


def economic_metrics(
    trades: pd.DataFrame,
    *,
    pnl_column: str = "net_pnl_usd",
    capital_column: str = "capital_usd",
) -> dict[str, Any]:
    if trades.empty and pnl_column not in trades:
        return {
            "trades": 0,
            "total_net_pnl_usd": 0.0,
            "mean_net_pnl_usd": None,
            "promotion_evidence_available": False,
        }
    if pnl_column not in trades:
        raise ValueError(f"missing PnL column: {pnl_column}")
    pnl = pd.to_numeric(trades[pnl_column], errors="coerce")
    valid = trades.loc[pnl.notna()].copy()
    values = pnl.dropna().to_numpy(float)
    if len(values) == 0:
        return {
            "trades": 0,
            "total_net_pnl_usd": 0.0,
            "mean_net_pnl_usd": None,
            "promotion_evidence_available": False,
        }
    gains = values[values > 0].sum()
    losses = -values[values < 0].sum()
    equity = np.cumsum(values)
    running_peak = np.maximum.accumulate(np.concatenate(([0.0], equity)))
    drawdown = running_peak[1:] - equity
    q10 = float(np.quantile(values, 0.10))
    expected_shortfall = float(values[values <= q10].mean())
    capital = (
        pd.to_numeric(valid[capital_column], errors="coerce").fillna(0).to_numpy(float)
        if capital_column in valid
        else np.zeros(len(valid), dtype=float)
    )
    deployed = float(capital.sum())
    entry = pd.to_datetime(valid["entry_ts_ns"], unit="ns", utc=True, errors="coerce")
    valid_days = entry.dt.strftime("%Y-%m-%d")
    by_day = [
        group[pnl_column].astype(float).tolist()
        for _, group in valid.assign(_day=valid_days).dropna(subset=["_day"]).groupby("_day")
    ]
    bootstrap = None
    if len(by_day) >= 2:
        bootstrap = block_bootstrap_mean_interval(by_day)
    holding_hours = 0.0
    if "observed_holding_seconds" in valid:
        holding_hours = (
            pd.to_numeric(
                valid["observed_holding_seconds"], errors="coerce"
            ).fillna(0).sum()
            / 3600.0
        )
    sample_sharpe = (
        float(values.mean() / values.std(ddof=1))
        if len(values) > 2 and values.std(ddof=1) > 0
        else 0.0
    )
    skew = float(pd.Series(values).skew()) if len(values) > 3 else 0.0
    kurtosis = float(pd.Series(values).kurt() + 3.0) if len(values) > 4 else 3.0
    return {
        "trades": int(len(values)),
        "trading_days": int(pd.Series(valid_days).nunique()),
        "calendar_weeks": int(entry.dt.strftime("%G-%V").nunique()),
        "total_net_pnl_usd": float(values.sum()),
        "mean_net_pnl_usd": float(values.mean()),
        "median_net_pnl_usd": float(np.median(values)),
        "q10_net_pnl_usd": q10,
        "q20_net_pnl_usd": _quantile(values, 0.20),
        "q50_net_pnl_usd": _quantile(values, 0.50),
        "q80_net_pnl_usd": _quantile(values, 0.80),
        "q90_net_pnl_usd": _quantile(values, 0.90),
        "expected_shortfall_usd": expected_shortfall,
        "profit_factor": float(gains / losses) if losses > 0 else None,
        "maximum_drawdown_usd": float(drawdown.max(initial=0.0)),
        "return_on_deployed_capital": (
            float(values.sum() / deployed) if deployed > 0 else None
        ),
        "profit_per_hour_exposure_usd": (
            float(values.sum() / holding_hours) if holding_hours > 0 else None
        ),
        "fees_usd": (
            float(pd.to_numeric(valid["fee_usd"], errors="coerce").fillna(0).sum())
            if "fee_usd" in valid
            else None
        ),
        "slippage_reserve_usd": (
            float(
                pd.to_numeric(
                    valid["impact_reserve_usd"], errors="coerce"
                ).fillna(0).sum()
            )
            if "impact_reserve_usd" in valid
            else None
        ),
        "funding_usd": (
            float(
                pd.to_numeric(valid["funding_usd"], errors="coerce").fillna(0).sum()
            )
            if "funding_usd" in valid
            else None
        ),
        "fill_rate": (
            float((valid.get("status", "") != "REJECTED").mean())
            if "status" in valid
            else None
        ),
        "day_block_mean_lower_95_usd": (
            float(bootstrap["lower"]) if bootstrap else None
        ),
        "day_block_mean_upper_95_usd": (
            float(bootstrap["upper"]) if bootstrap else None
        ),
        "sample_sharpe": sample_sharpe,
        "skewness": skew,
        "kurtosis": kurtosis,
        "promotion_evidence_available": len(by_day) >= 30,
    }


def deflated_sharpe_from_trials(
    selected_pnl: Iterable[float],
    trial_sharpes: Iterable[float],
) -> dict[str, float] | None:
    values = np.asarray(list(selected_pnl), dtype=float)
    trial_values = np.asarray(list(trial_sharpes), dtype=float)
    if len(values) < 3 or len(trial_values) == 0:
        return None
    standard = values.std(ddof=1)
    observed = float(values.mean() / standard) if standard > 0 else 0.0
    trial_std = float(trial_values.std(ddof=1)) if len(trial_values) > 1 else 0.0
    skew = float(pd.Series(values).skew()) if len(values) > 3 else 0.0
    kurtosis = float(pd.Series(values).kurt() + 3.0) if len(values) > 4 else 3.0
    return deflated_sharpe_ratio(
        observed,
        len(values),
        max(1, len(trial_values)),
        max(0.0, trial_std),
        skewness=skew if math.isfinite(skew) else 0.0,
        kurtosis=max(1.0, kurtosis if math.isfinite(kurtosis) else 3.0),
    )


def pbo_from_fold_policy_returns(
    fold_policy_returns: list[list[float]],
) -> dict[str, float] | None:
    if len(fold_policy_returns) < 4:
        return None
    rows = fold_policy_returns[: len(fold_policy_returns) // 2 * 2]
    if len(rows) < 4:
        return None
    return probability_backtest_overfitting(rows)


def positive_profit_concentration(
    frame: pd.DataFrame,
    *,
    bucket_column: str,
    pnl_column: str = "net_pnl_usd",
) -> float | None:
    if frame.empty or bucket_column not in frame:
        return None
    values = (
        frame.groupby(bucket_column, dropna=False)[pnl_column]
        .sum(min_count=1)
        .dropna()
        .tolist()
    )
    return profit_concentration(values) if values else None


class TrialRegistry:
    def __init__(
        self,
        path: Path,
        protocol_sha256: str,
        implementation_sha256: str,
    ):
        self.path = path
        self.protocol_sha256 = protocol_sha256
        self.implementation_sha256 = implementation_sha256
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        campaign_id: str,
        family: str,
        parameters: dict[str, Any],
        metrics: dict[str, Any],
        dataset_sha256: str,
    ) -> str:
        payload = {
            "campaign_id": campaign_id,
            "family": family,
            "parameters": parameters,
            "metrics": metrics,
            "protocol_sha256": self.protocol_sha256,
            "implementation_sha256": self.implementation_sha256,
            "dataset_sha256": dataset_sha256,
        }
        trial_id = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        row = {
            **payload,
            "trial_id": trial_id,
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        existing_ids: set[str] = set()
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    existing_ids.add(str(json.loads(line)["trial_id"]))
        if trial_id not in existing_ids:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        return trial_id


def finite_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_json(item) for item in value]
    if isinstance(value, np.generic):
        return finite_json(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "No rows."
    columns = [str(column) for column in frame.columns]

    def render(value: Any) -> str:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return ""
        text = str(value)
        return text.replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    for row in frame.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(render(value) for value in row) + " |")
    return "\n".join(lines)
