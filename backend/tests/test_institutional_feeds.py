"""Deterministic checks for Deribit option-surface summary semantics."""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from institutional_feeds import compute_option_surface_metrics


def _instrument(
    expiry: str,
    strike: int,
    option_type: str,
    oi: float,
    iv_percent: float,
) -> dict:
    return {
        "instrument_name": f"BTC-{expiry}-{strike}-{option_type}",
        "open_interest": oi,
        "mark_iv": iv_percent,
        "underlying_price": 100.0,
    }


def main() -> int:
    now = dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc)
    near = "1AUG26"
    far = "1SEP26"
    instruments = [
        # An expiry only eight hours away is too unstable for the dashboard surface.
        _instrument("31JUL26", 50, "P", 1.0, 200.0),
        _instrument("31JUL26", 150, "C", 1.0, 1.0),
        _instrument(near, 95, "P", 10.0, 55.0),
        _instrument(near, 100, "P", 20.0, 50.0),
        _instrument(near, 100, "C", 30.0, 40.0),
        _instrument(near, 105, "C", 10.0, 45.0),
        # A later expiry must not overwrite the nearest-expiry surface.
        _instrument(far, 100, "P", 1_000.0, 150.0),
        _instrument(far, 200, "C", 1_000.0, 5.0),
    ]
    metrics = compute_option_surface_metrics(
        instruments,
        int(now.timestamp() * 1000),
    )
    assert metrics["max_pain"] == 100.0
    assert metrics["atm_iv"] == 0.45
    assert metrics["skew_25d"] == 0.10
    assert metrics["put_call_ratio"] == 0.9904
    expected_expiry = dt.datetime(
        2026,
        8,
        1,
        8,
        tzinfo=dt.timezone.utc,
    )
    assert metrics["option_expiry_ms"] == int(expected_expiry.timestamp() * 1000)

    empty = compute_option_surface_metrics([], int(now.timestamp() * 1000))
    assert empty == {
        "put_call_ratio": 1.0,
        "max_pain": 0.0,
        "atm_iv": 0.0,
        "skew_25d": 0.0,
        "option_expiry_ms": 0,
    }
    print("institutional-feeds: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
