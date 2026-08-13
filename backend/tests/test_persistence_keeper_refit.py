"""Regression checks for optional P(Hold) keeper promotion and production refit."""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))

import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import train_persistence_model as trainer  # noqa: E402


def main() -> int:
    assert trainer._keeper_is_promotable(
        keeper_auc=0.75, base_auc=0.74,
        keeper_late_auc=0.83, base_late_auc=0.82,
    )
    assert not trainer._keeper_is_promotable(
        keeper_auc=0.73, base_auc=0.74,
        keeper_late_auc=0.83, base_late_auc=0.82,
    )
    assert not trainer._keeper_is_promotable(
        keeper_auc=0.75, base_auc=0.74,
        keeper_late_auc=0.81, base_late_auc=0.82,
    )
    assert not trainer._keeper_is_promotable(
        keeper_auc=0.74, base_auc=0.74,
        keeper_late_auc=0.82, base_late_auc=0.82,
    )
    assert not trainer._keeper_is_promotable(
        keeper_auc=np.nan, base_auc=0.74,
        keeper_late_auc=0.83, base_late_auc=0.82,
    )
    # Regression values from the 2026-08-13 30-day smoke: this challenger must abstain.
    assert not trainer._keeper_is_promotable(
        keeper_auc=0.7342, base_auc=0.7353,
        keeper_late_auc=0.8148, base_late_auc=0.8249,
    )

    minute = 60_000
    old = 1_700_000_000_000
    recent = 1_780_000_000_000
    base = pd.DataFrame({
        "window_start_ms": old + np.arange(1_000) * minute,
        "horizon": np.where(np.arange(1_000) % 2, 5, 15),
        "label": np.arange(1_000) % 2,
    })
    keeper = pd.DataFrame({
        "window_start_ms": recent + np.arange(100) * minute,
        "horizon": np.where(np.arange(100) % 2, 5, 15),
        "label": np.arange(100) % 2,
    })
    base_fit, base_cal, base_cut = trainer._production_fit_cal(base, 0.95)
    keeper_fit, keeper_cal, keeper_cut = trainer._production_fit_cal(keeper, 0.95)

    assert len(base_fit) > 0 and len(base_cal) > 0
    assert len(keeper_fit) > 0 and len(keeper_cal) > 0
    assert keeper_cut > base_cut
    assert (base_fit["window_start_ms"] + base_fit["horizon"] * minute <= base_cut).all()
    assert (keeper_fit["window_start_ms"] + keeper_fit["horizon"] * minute <= keeper_cut).all()
    assert keeper_fit["window_start_ms"].min() >= recent

    print("PERSISTENCE KEEPER REFIT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
