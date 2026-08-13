"""Polymarket snapshots joined to canonical venue settlement exports.

The snapshots and settlement exports are produced as a paired research interface by the
recorder health path. Only outcomes resolved by Polymarket CLOB or Gamma are admitted. The
loader deliberately refuses a Binance-close proxy because it is not the contract's settlement
source and would answer a different question than the one that pays.

Every confidence interval must cluster by ``round_id``. Many snapshots share one outcome, so
treating rows as independent would manufacture precision.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
SNAPSHOTS = REPO / "data" / "pm_export_snapshots.parquet"
SETTLEMENTS = REPO / "data" / "pm_export_settlements.parquet"

COLS = [
    "ts", "slug", "horizon", "anchor_ts", "seconds_left", "anchor_price", "btc_price",
    "distance_bps", "current_side", "vol_60s_pct", "p_hold_cur", "p_hold_up",
    "p_hold_down", "up_bid", "up_ask", "up_mid", "down_bid", "down_ask", "down_mid",
    "up_top_ask_size", "down_top_ask_size", "book_age_s",
]


def load_official(
    min_seconds_left: float = 5.0,
    *,
    snapshots_path: Path = SNAPSHOTS,
    settlements_path: Path = SETTLEMENTS,
) -> pd.DataFrame:
    """Return valid snapshots with one canonical settled outcome attached.

    Added columns are ``settled_up``, ``round_id`` and UTC ``day``. The merge is validated as
    many-to-one so duplicate settlement rows cannot silently multiply evidence.
    """
    if not snapshots_path.exists() or not settlements_path.exists():
        return pd.DataFrame()
    settlements = pd.read_parquet(
        settlements_path,
        columns=["slug", "horizon", "anchor_ts", "up_win", "resolution_source"],
    )
    settlements = settlements[
        settlements["up_win"].isin([0, 1])
        & settlements["resolution_source"].isin(["polymarket_clob", "polymarket_gamma"])
    ].copy()
    if settlements.empty:
        return pd.DataFrame()
    settlements["horizon"] = settlements["horizon"].astype("int64")
    settlements["settled_up"] = settlements["up_win"].astype("int64")
    settlements = settlements.drop_duplicates(subset=["slug", "horizon"], keep="last")

    snapshots = pd.read_parquet(snapshots_path, columns=COLS)
    snapshots["horizon"] = snapshots["horizon"].astype("int64")
    frame = snapshots.merge(
        settlements[["slug", "horizon", "settled_up", "resolution_source"]],
        on=["slug", "horizon"],
        how="inner",
        validate="many_to_one",
    )
    frame = frame[frame["seconds_left"] >= min_seconds_left]
    frame = frame[
        frame["up_ask"].between(0.01, 0.99)
        & frame["up_bid"].between(0.005, 0.985)
        & (frame["up_bid"] < frame["up_ask"])
    ]
    frame["round_id"] = frame["slug"].astype(str)
    frame["day"] = (frame["anchor_ts"].astype("int64") // 86_400).astype("int64")
    return frame.reset_index(drop=True)


def round_bootstrap(
    values: np.ndarray,
    rounds: np.ndarray,
    stat=np.mean,
    n_boot: int = 2_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict:
    """Resample whole rounds and return a two-sided confidence interval."""
    values = np.asarray(values, dtype=float)
    rounds = np.asarray(rounds)
    ok = np.isfinite(values)
    values, rounds = values[ok], rounds[ok]
    unique_rounds = np.unique(rounds)
    if not len(unique_rounds):
        return {
            "point": float("nan"), "lcb": float("nan"), "ucb": float("nan"),
            "n_rows": 0, "n_rounds": 0,
        }
    index = {round_id: np.flatnonzero(rounds == round_id) for round_id in unique_rounds}
    rng = np.random.default_rng(seed)
    estimates = np.empty(n_boot)
    for position in range(n_boot):
        chosen = rng.choice(unique_rounds, size=len(unique_rounds), replace=True)
        estimates[position] = stat(values[np.concatenate([index[item] for item in chosen])])
    return {
        "point": float(stat(values)),
        "lcb": float(np.percentile(estimates, 100 * alpha / 2)),
        "ucb": float(np.percentile(estimates, 100 * (1 - alpha / 2))),
        "n_rows": int(len(values)),
        "n_rounds": int(len(unique_rounds)),
    }


def brier(probability: np.ndarray, outcome: np.ndarray) -> float:
    probability = np.asarray(probability, dtype=float)
    outcome = np.asarray(outcome, dtype=float)
    return float(np.mean((probability - outcome) ** 2))
