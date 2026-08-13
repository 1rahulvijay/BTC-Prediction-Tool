"""Shared statistics for standalone alpha lanes. Imports NOTHING from the serving app.

The rule this module exists to enforce: a lane may not report a number without an interval,
and the interval must be built from INDEPENDENT units. Overlapping 1-minute observations of a
5-minute forward move share four minutes of price path; treating them as independent samples
is how a backtest manufactures confidence it has not earned. Every bound here resamples whole
UTC days.
"""
from __future__ import annotations

import numpy as np

MS_DAY = 86_400_000


def day_index(ts_ms: np.ndarray) -> np.ndarray:
    """UTC day number — the independence unit for every bound in this module."""
    return (np.asarray(ts_ms, dtype="int64") // MS_DAY).astype("int64")


def day_block_bootstrap(values: np.ndarray, days: np.ndarray, stat=np.mean,
                        n_boot: int = 2000, alpha: float = 0.05, seed: int = 0) -> dict:
    """Resample whole DAYS with replacement; return the statistic and its lower bound.

    Not a row bootstrap. Rows inside a day are serially dependent and overlapping, so a row
    resample reports the precision of a sample size the data does not contain.
    """
    values = np.asarray(values, dtype=float)
    days = np.asarray(days)
    ok = np.isfinite(values)
    values, days = values[ok], days[ok]
    uniq = np.unique(days)
    idx_by_day = {d: np.flatnonzero(days == d) for d in uniq}
    rng = np.random.default_rng(seed)
    stats = np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([idx_by_day[d] for d in pick])
        stats[i] = stat(values[idx])
    return {
        "point": float(stat(values)),
        "lcb": float(np.percentile(stats, 100 * alpha / 2)),
        "ucb": float(np.percentile(stats, 100 * (1 - alpha / 2))),
        "n_rows": int(len(values)),
        "n_days": int(len(uniq)),
    }


def forward_abs_move_bps(close: np.ndarray, horizon_bars: int) -> np.ndarray:
    """|close[t+h] - close[t]| / close[t] in bps, NaN-padded at the tail."""
    close = np.asarray(close, dtype=float)
    out = np.full(len(close), np.nan)
    if horizon_bars < len(close):
        base = close[:-horizon_bars]
        out[:-horizon_bars] = np.abs(close[horizon_bars:] - base) / base * 1e4
    return out


def forward_signed_move_bps(close: np.ndarray, horizon_bars: int) -> np.ndarray:
    close = np.asarray(close, dtype=float)
    out = np.full(len(close), np.nan)
    if horizon_bars < len(close):
        base = close[:-horizon_bars]
        out[:-horizon_bars] = (close[horizon_bars:] - base) / base * 1e4
    return out


def breakeven_accuracy(mean_abs_bps: float, round_trip_bps: float) -> float:
    """Directional accuracy required for EV > 0 under a symmetric payoff.

        EV = p*E|move| - (1-p)*E|move| - cost = (2p-1)*E|move| - cost
        p* = 0.5 * (1 + cost / E|move|)

    Symmetric is the OPTIMISTIC case: it assumes wins and losses are the same size. Real
    directional strategies usually lose slightly more on wrong calls (they are wrong more
    often when the move is large), so treat p* as a floor, not a target.
    """
    if mean_abs_bps <= 0:
        return float("nan")
    return 0.5 * (1.0 + round_trip_bps / mean_abs_bps)


def ev_bps(p_correct: float, mean_abs_bps: float, round_trip_bps: float) -> float:
    return (2.0 * p_correct - 1.0) * mean_abs_bps - round_trip_bps


def md_table(rows: list[dict], cols: list[str], aligns: dict | None = None) -> str:
    aligns = aligns or {}
    head = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---:" if aligns.get(c) == "r" else "---" for c in cols) + " |"
    body = "\n".join("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows)
    return "\n".join([head, sep, body])
