"""Run the remaining causal matrix tests that do not need new recorder data.

The tests are deliberately simple and chronological. Thresholds are learned from the first
70% of UTC days and frozen before the final 30% is scored. Confidence intervals resample whole
test days and use a Bonferroni family correction across the reported configurations.

This batch answers five narrow questions:

* does compression select windows whose absolute move clears costs?
* does a pullback inside an established move resume profitably?
* does trend age identify profitable continuation?
* after a large move, is continuation or recovery economically stronger?
* does minute-scale spot/perpetual basis reversion clear an optimistic cost floor?

Funding carry is returned as ``BLOCKED_DATA`` because ``funding_velocity`` is not a cash-flow
history and cannot reconstruct actual payment timestamps or paid rates.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


LANES = Path(__file__).resolve().parent
ROOT = LANES.parent
MATRIX = ROOT / "data" / "research_matrix_1m.parquet"
COST_BPS = 12.0
FAMILY_SIZE = 20
FAMILY_ALPHA = 0.05 / FAMILY_SIZE
HORIZONS = (5, 15, 30)


def _forward_return(close: np.ndarray, horizon: int) -> np.ndarray:
    out = np.full(len(close), np.nan, dtype=float)
    out[:-horizon] = (close[horizon:] / close[:-horizon] - 1.0) * 10_000
    return out


def _past_return(close: np.ndarray, lookback: int) -> np.ndarray:
    out = np.full(len(close), np.nan, dtype=float)
    out[lookback:] = (close[lookback:] / close[:-lookback] - 1.0) * 10_000
    return out


def _day_interval(values: np.ndarray, days: np.ndarray, *, seed: int) -> dict:
    frame = pd.DataFrame({"value": np.asarray(values, float), "day": np.asarray(days)})
    frame = frame[np.isfinite(frame["value"])]
    daily = frame.groupby("day", sort=False)["value"].mean().to_numpy(float)
    if not len(daily):
        return {
            "point": float("nan"), "lcb": float("nan"), "ucb": float("nan"),
            "n_rows": 0, "n_days": 0,
        }
    rng = np.random.default_rng(seed)
    draws = daily[rng.integers(0, len(daily), size=(20_000, len(daily)))].mean(axis=1)
    return {
        "point": float(daily.mean()),
        "lcb": float(np.quantile(draws, FAMILY_ALPHA / 2)),
        "ucb": float(np.quantile(draws, 1 - FAMILY_ALPHA / 2)),
        "n_rows": int(len(frame)),
        "n_days": int(len(daily)),
    }


def _economic_score(
    signed_return: np.ndarray,
    mask: np.ndarray,
    days: np.ndarray,
    *,
    seed: int,
) -> dict:
    valid = np.asarray(mask, bool) & np.isfinite(signed_return)
    gross = _day_interval(signed_return[valid], days[valid], seed=seed)
    return {
        "n": int(valid.sum()),
        "n_days": gross["n_days"],
        "direction_accuracy": float((signed_return[valid] > 0).mean()) if valid.any() else None,
        "gross_bps": gross["point"],
        "gross_family_lcb": gross["lcb"],
        "net_bps": gross["point"] - COST_BPS,
        "net_family_lcb": gross["lcb"] - COST_BPS,
        "clears_cost": bool(gross["lcb"] > COST_BPS),
    }


def _run(matrix: Path) -> dict:
    columns = ["ts_ms", "close", "compression_ratio", "perp_spot_basis_bps"]
    frame = pd.read_parquet(matrix, columns=columns).replace([np.inf, -np.inf], np.nan)
    close = frame["close"].to_numpy(float)
    days = (frame["ts_ms"].to_numpy("int64") // 86_400_000).astype("int64")
    unique_days = np.sort(np.unique(days))
    split_day = unique_days[int(len(unique_days) * 0.70)]
    train = days < split_day
    test = days >= split_day

    past3 = _past_return(close, 3)
    past5 = _past_return(close, 5)
    past15 = _past_return(close, 15)
    future = {horizon: _forward_return(close, horizon) for horizon in HORIZONS}

    compression = frame["compression_ratio"].to_numpy(float)
    compression_threshold = float(np.nanquantile(compression[train], 0.10))
    compressed = test & np.isfinite(compression) & (compression <= compression_threshold)
    compression_rows = []
    for position, horizon in enumerate(HORIZONS):
        absolute_move = np.abs(future[horizon])
        selected = compressed & np.isfinite(absolute_move)
        all_test = test & np.isfinite(absolute_move)
        margin = _day_interval(absolute_move[selected] - COST_BPS, days[selected], seed=10 + position)
        compression_rows.append({
            "horizon_m": horizon,
            "n": int(selected.sum()),
            "n_days": margin["n_days"],
            "cost_clearance": float((absolute_move[selected] > COST_BPS).mean()),
            "baseline_clearance": float((absolute_move[all_test] > COST_BPS).mean()),
            "mean_move_bps": float(np.mean(absolute_move[selected])),
            "move_minus_cost_family_lcb": margin["lcb"],
            "direction_not_predicted": True,
        })

    trend_threshold = float(np.nanquantile(np.abs(past15[train]), 0.75))
    pullback_threshold = float(np.nanquantile(np.abs(past3[train]), 0.50))
    trend_sign = np.sign(past15)
    pullback = (
        test
        & np.isfinite(past15)
        & np.isfinite(past3)
        & (np.abs(past15) >= trend_threshold)
        & (np.abs(past3) >= pullback_threshold)
        & (past15 * past3 < 0)
    )
    pullback_rows = [
        {
            "horizon_m": horizon,
            **_economic_score(
                trend_sign * future[horizon], pullback, days, seed=30 + position
            ),
        }
        for position, horizon in enumerate(HORIZONS)
    ]

    strong = np.isfinite(past15) & (np.abs(past15) >= trend_threshold)
    age = np.zeros(len(frame), dtype=int)
    for position in range(1, len(frame)):
        if strong[position] and strong[position - 1] and trend_sign[position] == trend_sign[position - 1]:
            age[position] = age[position - 1] + 1
        elif strong[position]:
            age[position] = 1
    age_buckets = [
        ("1m", age == 1),
        ("2-3m", (age >= 2) & (age <= 3)),
        ("4-6m", (age >= 4) & (age <= 6)),
        ("7m+", age >= 7),
    ]
    survival_rows = []
    for position, (label, age_mask) in enumerate(age_buckets):
        survival_rows.append({
            "age": label,
            "horizon_m": 5,
            **_economic_score(
                trend_sign * future[5], test & age_mask, days, seed=50 + position
            ),
        })

    shock_threshold = float(np.nanquantile(np.abs(past5[train]), 0.90))
    shocked = test & np.isfinite(past5) & (np.abs(past5) >= shock_threshold)
    continuation_rows = []
    recovery_rows = []
    shock_sign = np.sign(past5)
    for position, horizon in enumerate(HORIZONS):
        continuation_rows.append({
            "horizon_m": horizon,
            **_economic_score(
                shock_sign * future[horizon], shocked, days, seed=70 + position
            ),
        })
        recovery_rows.append({
            "horizon_m": horizon,
            **_economic_score(
                -shock_sign * future[horizon], shocked, days, seed=80 + position
            ),
        })

    basis = frame["perp_spot_basis_bps"].to_numpy(float)
    basis_hi = float(np.nanquantile(basis[train], 0.95))
    basis_lo = float(np.nanquantile(basis[train], 0.05))
    basis_rows = []
    for position, horizon in enumerate((1, 3, 5)):
        change = np.full(len(basis), np.nan, dtype=float)
        change[:-horizon] = basis[horizon:] - basis[:-horizon]
        rich = test & np.isfinite(change) & (basis >= basis_hi)
        cheap = test & np.isfinite(change) & (basis <= basis_lo)
        basis_rows.extend([
            {
                "side": "RICH_SHORT_PERP_LONG_SPOT", "horizon_m": horizon,
                **_economic_score(-change, rich, days, seed=100 + position),
            },
            {
                "side": "CHEAP_LONG_PERP_SHORT_SPOT", "horizon_m": horizon,
                **_economic_score(change, cheap, days, seed=110 + position),
            },
        ])

    tested = [
        *pullback_rows, *survival_rows, *continuation_rows, *recovery_rows, *basis_rows,
    ]
    return {
        "data": {
            "rows": int(len(frame)), "days": int(len(unique_days)),
            "train_days": int(np.unique(days[train]).size),
            "test_days": int(np.unique(days[test]).size),
            "split_day": int(split_day), "cost_bps": COST_BPS,
            "family_alpha": FAMILY_ALPHA,
        },
        "compression_breakout": {
            "status": "DIAGNOSTIC_ONLY",
            "train_q10_threshold": compression_threshold,
            "rows": compression_rows,
        },
        "trend_pullback": {"rows": pullback_rows},
        "trend_survival": {"rows": survival_rows},
        "profit_continuation": {"rows": continuation_rows},
        "adverse_move_recovery": {"rows": recovery_rows},
        "microbasis_reversion": {
            "optimistic_cost_floor_bps": COST_BPS,
            "train_p95": basis_hi, "train_p05": basis_lo, "rows": basis_rows,
        },
        "funding_basis_carry": {
            "status": "BLOCKED_DATA",
            "reason": (
                "The matrix contains funding_velocity but not the actual paid funding rate, "
                "payment timestamp, interval or spot financing cash flows. A carry PnL would be "
                "fabricated from a transformed feature."
            ),
        },
        "promotable_configurations": int(sum(row["clears_cost"] for row in tested)),
        "authority": "RESEARCH_ONLY",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=MATRIX)
    parser.add_argument("--output", type=Path, default=LANES / "matrix_path_extensions_results.json")
    args = parser.parse_args()
    result = _run(args.matrix)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, default=float) + "\n", encoding="utf-8")

    print(
        f"matrix rows={result['data']['rows']:,} days={result['data']['days']} "
        f"train/test={result['data']['train_days']}/{result['data']['test_days']}"
    )
    for name in (
        "trend_pullback", "trend_survival", "profit_continuation",
        "adverse_move_recovery", "microbasis_reversion",
    ):
        rows = result[name]["rows"]
        best = max(rows, key=lambda row: row["net_family_lcb"])
        print(
            f"{name:<26} best net={best['net_bps']:+.2f}bps "
            f"family-LCB={best['net_family_lcb']:+.2f} n={best['n']:,}"
        )
    print(
        f"promotable configurations={result['promotable_configurations']} "
        f"(funding carry={result['funding_basis_carry']['status']})"
    )
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
