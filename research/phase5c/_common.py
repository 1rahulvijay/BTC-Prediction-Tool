"""Shared substrate for the Phase 5C studies.

One loader, not seven. Seven copies of the same join drift apart, and the copy that drifted is
never the one anybody re-reads.

EVERY STUDY HERE DECLARES ITS INFERENCE CEILING
    `test_effective_independent_sample_size` measured what each window supports:

        Binance 1-minute bars       360 days   MDE  7.4 pts   hypothesis tests are meaningful
        Polymarket checkpoints       21 days   MDE 25-30 pts  DESCRIPTION ONLY

    So the Polymarket studies in this package report distributions and never claim significance.
    `assert_descriptive_only()` makes that a runtime property rather than a note in a docstring.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CHECKPOINTS = ROOT / "data" / "research" / "causal_checkpoints_v1.parquet"
LABELS = ROOT / "data" / "research" / "causal_checkpoint_labels_v1.parquet"
BARS = ROOT / "data" / "btc_1m_data.csv"

#: Measured, not assumed. See test_effective_independent_sample_size.
POLYMARKET_DAYS = 21
POLYMARKET_MDE_POINTS = 25.5
BINANCE_DAYS = 360


def assert_descriptive_only(claimed_effect_points: float | None = None) -> str:
    """Refuse a significance claim the Polymarket window cannot support.

    Called with an effect size, this raises when the effect is below the window's minimum
    detectable shift. Called with None it returns the banner every such study prints."""
    if claimed_effect_points is not None and claimed_effect_points < POLYMARKET_MDE_POINTS:
        raise ValueError(
            f"effect of {claimed_effect_points:.2f} points is below the {POLYMARKET_MDE_POINTS} "
            f"point minimum detectable shift of a {POLYMARKET_DAYS}-day window - this study may "
            f"describe the distribution and may NOT claim the effect is real")
    return (f"DESCRIPTIVE ONLY - {POLYMARKET_DAYS} days supports no effect below "
            f"{POLYMARKET_MDE_POINTS} points. No significance is claimed below.")


def load_checkpoints(extra_labels: tuple[str, ...] = ()):
    """Causal checkpoints joined to their labels, eligible and settled."""
    import duckdb

    columns = "".join(f", l.{name}" for name in extra_labels)
    con = duckdb.connect(":memory:")
    try:
        return con.execute(f"""
            SELECT k.*, l.label_checkpoint_side_wins AS won{columns}
            FROM read_parquet('{CHECKPOINTS.as_posix()}') k
            JOIN read_parquet('{LABELS.as_posix()}') l
              ON l.slug = k.slug AND l.checkpoint_s = k.checkpoint_s
            WHERE k.eligible AND l.label_checkpoint_side_wins IS NOT NULL
              AND k.current_side IS NOT NULL
            ORDER BY k.snapshot_ts, k.checkpoint_s""").df()
    finally:
        con.close()


def load_bars(columns: tuple[str, ...] = ("ts_ms", "high", "low", "close")):
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        return con.execute(
            f"SELECT {','.join(columns)} FROM read_csv_auto('{BARS.as_posix()}') "
            f"ORDER BY ts_ms").df()
    finally:
        con.close()


def side_ask(frame) -> np.ndarray:
    """The executable ask on the side actually being held."""
    return np.where(frame["current_side"].to_numpy() == 1,
                    frame["up_ask"].to_numpy(float), frame["down_ask"].to_numpy(float))


def murphy_decomposition(probabilities, outcomes, bins: int = 10) -> dict:
    """Brier = reliability - resolution + uncertainty.

    reliability  how far each bucket's forecast sits from its realised rate (lower is better)
    resolution   how far bucket rates sit from the base rate (HIGHER is better)
    uncertainty  the base rate's own variance - identical for every forecaster on one sample
    """
    probabilities = np.asarray(probabilities, float)
    outcomes = np.asarray(outcomes, float)
    base = outcomes.mean()
    edges = np.linspace(0.0, 1.0, bins + 1)
    index = np.clip(np.digitize(probabilities, edges[1:-1]), 0, bins - 1)
    reliability = resolution = 0.0
    for bucket in range(bins):
        mask = index == bucket
        if not mask.any():
            continue
        weight = mask.mean()
        forecast = probabilities[mask].mean()
        realised = outcomes[mask].mean()
        reliability += weight * (forecast - realised) ** 2
        resolution += weight * (realised - base) ** 2
    uncertainty = base * (1.0 - base)
    return {"brier": float(np.mean((probabilities - outcomes) ** 2)),
            "reliability": float(reliability), "resolution": float(resolution),
            "uncertainty": float(uncertainty), "base_rate": float(base)}
