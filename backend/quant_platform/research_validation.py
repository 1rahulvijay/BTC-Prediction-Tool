"""Reusable research validation without strategy-specific economics."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import NormalDist, mean, pstdev
from typing import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class ExperimentProtocol:
    strategy_id: str
    hypothesis: str
    instrument: str
    feature_schema_sha256: str
    data_period: str
    entry_rule: str
    exit_rule: str
    parameters_json: str
    configurations_tried: int
    cost_model: str
    latency_model: str
    promotion_gate: str
    code_sha256: str
    dataset_sha256: str

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name == "configurations_tried":
                if int(value) <= 0:
                    raise ValueError("configurations_tried must be positive")
            elif not str(value).strip():
                raise ValueError(f"{name} must be non-empty")
        parsed = json.loads(self.parameters_json)
        if not isinstance(parsed, dict):
            raise ValueError("parameters_json must encode an object")

    @property
    def protocol_sha256(self) -> str:
        raw = json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def freeze(self, directory: str | Path) -> Path:
        target_dir = Path(directory)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{self.strategy_id}-{self.protocol_sha256}.json"
        payload = {
            **asdict(self),
            "protocol_sha256": self.protocol_sha256,
        }
        encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") != encoded:
            raise ValueError("frozen protocol path contains different bytes")
        path.write_text(encoded, encoding="utf-8", newline="\n")
        return path


@dataclass(frozen=True, slots=True)
class PurgedSplit:
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    @property
    def train_indices(self) -> range:
        return range(self.train_start, self.train_end)

    @property
    def test_indices(self) -> range:
        return range(self.test_start, self.test_end)


def purged_walk_forward_splits(
    sample_count: int,
    min_train_size: int,
    test_size: int,
    purge_size: int,
    embargo_size: int,
) -> list[PurgedSplit]:
    values = (sample_count, min_train_size, test_size, purge_size, embargo_size)
    if any(value < 0 for value in values) or min_train_size <= 0 or test_size <= 0:
        raise ValueError("invalid split sizes")
    splits: list[PurgedSplit] = []
    test_start = min_train_size + purge_size
    while test_start + test_size <= sample_count:
        train_end = test_start - purge_size
        if train_end >= min_train_size:
            splits.append(PurgedSplit(0, train_end, test_start, test_start + test_size))
        test_start += test_size + embargo_size
    if not splits:
        raise ValueError("configuration produces no walk-forward split")
    return splits


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile requires data")
    index = (len(ordered) - 1) * probability
    lower = int(math.floor(index))
    upper = int(math.ceil(index))
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def block_bootstrap_mean_interval(
    block_pnls: Sequence[Sequence[float]],
    iterations: int = 2_000,
    confidence: float = 0.95,
    seed: int = 17,
) -> dict[str, float]:
    blocks = [list(map(float, block)) for block in block_pnls if block]
    if len(blocks) < 2:
        raise ValueError("at least two non-empty blocks are required")
    if iterations < 100 or not 0 < confidence < 1:
        raise ValueError("invalid bootstrap configuration")
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(iterations):
        selected = [blocks[rng.randrange(len(blocks))] for _ in blocks]
        flat = [value for block in selected for value in block]
        samples.append(mean(flat))
    alpha = (1.0 - confidence) / 2.0
    observed = mean([value for block in blocks for value in block])
    return {
        "mean": observed,
        "lower": _quantile(samples, alpha),
        "upper": _quantile(samples, 1.0 - alpha),
        "iterations": float(iterations),
        "block_count": float(len(blocks)),
    }


def probability_backtest_overfitting(
    fold_returns: Sequence[Sequence[float]],
    max_combinations: int = 5_000,
) -> dict[str, float]:
    rows = [list(map(float, row)) for row in fold_returns]
    if len(rows) < 4 or len(rows) % 2:
        raise ValueError("CSCV requires an even number of at least four folds")
    strategy_count = len(rows[0])
    if strategy_count < 2 or any(len(row) != strategy_count for row in rows):
        raise ValueError("fold return matrix must be rectangular with 2+ strategies")
    half = len(rows) // 2
    all_splits = list(combinations(range(len(rows)), half))
    if len(all_splits) > max_combinations:
        stride = math.ceil(len(all_splits) / max_combinations)
        all_splits = all_splits[::stride][:max_combinations]
    below_median = 0
    rank_percentiles: list[float] = []
    for train_indices in all_splits:
        train = set(train_indices)
        test = [index for index in range(len(rows)) if index not in train]
        train_scores = [
            mean(rows[index][strategy] for index in train)
            for strategy in range(strategy_count)
        ]
        selected = max(range(strategy_count), key=train_scores.__getitem__)
        test_scores = [
            mean(rows[index][strategy] for index in test)
            for strategy in range(strategy_count)
        ]
        selected_score = test_scores[selected]
        lower_count = sum(score < selected_score for score in test_scores)
        equal_count = sum(score == selected_score for score in test_scores)
        percentile = (lower_count + 0.5 * equal_count) / strategy_count
        rank_percentiles.append(percentile)
        if percentile <= 0.5:
            below_median += 1
    return {
        "pbo": below_median / len(rank_percentiles),
        "median_oos_rank_percentile": _quantile(rank_percentiles, 0.5),
        "combinations": float(len(rank_percentiles)),
    }


def deflated_sharpe_ratio(
    observed_sharpe: float,
    sample_count: int,
    trials: int,
    trial_sharpe_std: float,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> dict[str, float]:
    values = (
        observed_sharpe,
        trial_sharpe_std,
        skewness,
        kurtosis,
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("DSR inputs must be finite")
    if sample_count < 3 or trials < 1 or trial_sharpe_std < 0 or kurtosis < 1:
        raise ValueError("invalid DSR inputs")
    normal = NormalDist()
    if trials == 1 or trial_sharpe_std == 0:
        expected_max = 0.0
    else:
        gamma = 0.5772156649015329
        z1 = normal.inv_cdf(1.0 - 1.0 / trials)
        z2 = normal.inv_cdf(1.0 - 1.0 / (trials * math.e))
        expected_max = trial_sharpe_std * ((1.0 - gamma) * z1 + gamma * z2)
    denominator_sq = (
        1.0
        - skewness * observed_sharpe
        + ((kurtosis - 1.0) / 4.0) * observed_sharpe**2
    )
    if denominator_sq <= 0:
        raise ValueError("DSR denominator is non-positive")
    z_score = (
        (observed_sharpe - expected_max)
        * math.sqrt(sample_count - 1)
        / math.sqrt(denominator_sq)
    )
    return {
        "probability": normal.cdf(z_score),
        "z_score": z_score,
        "expected_max_sharpe": expected_max,
    }


def profit_concentration(pnls_by_bucket: Iterable[float]) -> float:
    positive = [max(0.0, float(value)) for value in pnls_by_bucket]
    total = sum(positive)
    return max(positive, default=0.0) / total if total > 0 else 1.0
