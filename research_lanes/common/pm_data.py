"""Polymarket snapshots joined to OFFICIAL settlement. Shared by every PM lane.

WHY THE JOIN IS RESTRICTIVE ON PURPOSE
    `pm_export_snapshots.parquet` spans 916 rounds (2026-06-16 -> 2026-08-13). Official
    settlement exists only in `price_to_beat` rows whose `settlement_source` starts with
    `official:` — roughly 2026-07-05 to 07-25. Everything outside that window has either no
    recorded outcome or an exchange-derived one.

    This loader returns ONLY officially-settled rounds. Deriving the outcome from a Binance
    close instead would be the ROLLING_EXCHANGE_RETURN_SIGN_V1 proxy, which the model registry
    holds at may_price=False precisely because it uses the wrong price series and the wrong
    reference point for this venue. A residual model measured against a proxy answers a
    different question than the one that pays.

    The cost is sample size, and that is the honest trade.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
SNAPSHOTS = REPO / "data" / "pm_export_snapshots.parquet"

_SLUG_TS = re.compile(r"-(\d+)$")
_PTB_TS = re.compile(r"_(\d+)$")

COLS = ["ts", "slug", "horizon", "anchor_ts", "seconds_left", "anchor_price", "btc_price",
        "distance_bps", "current_side", "vol_60s_pct", "p_hold_cur", "p_hold_up",
        "p_hold_down", "up_bid", "up_ask", "up_mid", "down_bid", "down_ask", "down_mid",
        "up_top_ask_size", "down_top_ask_size", "book_age_s"]


def load_official(min_seconds_left: float = 5.0) -> pd.DataFrame:
    """Snapshots with an OFFICIAL settled outcome attached. One row per snapshot.

    Adds:
        settled_up   1 if the round settled UP, else 0
        round_id     the round key (independence unit for every bound)
        day          UTC day (secondary clustering unit)
    """
    sys.path.insert(0, str(REPO / "backend"))
    import duckdb

    import database

    con = duckdb.connect(database.DB_PATH, read_only=True)
    try:
        ptb = con.execute(
            "SELECT id, horizon, actual_direction, settlement_source FROM price_to_beat "
            "WHERE resolved AND settlement_source LIKE 'official:%' "
            "AND actual_direction IN ('UP','DOWN')"
        ).df()
    finally:
        con.close()
    if ptb.empty:
        return pd.DataFrame()

    ptb["anchor_ms"] = ptb["id"].str.extract(_PTB_TS.pattern)[0].astype("float64")
    ptb = ptb.dropna(subset=["anchor_ms"])
    ptb["anchor_ms"] = ptb["anchor_ms"].astype("int64")
    ptb["horizon"] = ptb["horizon"].astype("int64")
    ptb["settled_up"] = (ptb["actual_direction"] == "UP").astype(int)
    # One outcome per (anchor, horizon). Duplicate ids across sources must not multiply rows.
    ptb = ptb.drop_duplicates(subset=["anchor_ms", "horizon"], keep="first")

    snap = pd.read_parquet(SNAPSHOTS, columns=COLS)
    snap["anchor_ms"] = (snap["anchor_ts"].astype("float64") * 1000).round().astype("int64")
    snap["horizon"] = snap["horizon"].astype("int64")

    df = snap.merge(ptb[["anchor_ms", "horizon", "settled_up", "settlement_source"]],
                    on=["anchor_ms", "horizon"], how="inner")
    df = df[df["seconds_left"] >= min_seconds_left]
    df = df[df["up_ask"].between(0.01, 0.99) & df["up_bid"].between(0.005, 0.985)]
    df = df[df["up_bid"] < df["up_ask"]]
    df["round_id"] = df["slug"].astype(str)
    df["day"] = (df["anchor_ms"] // 86_400_000).astype("int64")
    return df.reset_index(drop=True)


def round_bootstrap(values: np.ndarray, rounds: np.ndarray, stat=np.mean,
                    n_boot: int = 2000, alpha: float = 0.05, seed: int = 0) -> dict:
    """Resample whole ROUNDS. ~167 snapshots share one round's single outcome, so a row
    bootstrap here would claim roughly 167x the independent sample the data contains."""
    values = np.asarray(values, dtype=float)
    rounds = np.asarray(rounds)
    ok = np.isfinite(values)
    values, rounds = values[ok], rounds[ok]
    uniq = np.unique(rounds)
    idx = {r: np.flatnonzero(rounds == r) for r in uniq}
    rng = np.random.default_rng(seed)
    out = np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        out[i] = stat(values[np.concatenate([idx[r] for r in pick])])
    return {"point": float(stat(values)), "lcb": float(np.percentile(out, 100 * alpha / 2)),
            "ucb": float(np.percentile(out, 100 * (1 - alpha / 2))),
            "n_rows": int(len(values)), "n_rounds": int(len(uniq))}


def brier(p: np.ndarray, y: np.ndarray) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))
