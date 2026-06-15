"""
live_keepers.py — compute the 6 volatility KEEPERS live, with guaranteed parity.
=================================================================================
The keeper P(Hold) model (validated +0.019 AUC on the T3 region) needs 6 features:
rv_15m, rv_30m, rv_60m, vpin, compression_ratio, shock_magnitude. Only rv_15m + vpin
exist in the live 136-feature set; the other 4 do NOT. Rather than RE-IMPLEMENT them
(parity-risky), this module REUSES the exact `edge_probe.FEATURE_BUILDERS` that built
the research matrix — same code, same math, so the values match by construction.

All 6 derive from 1m OHLCV bars (no order book), which the live app already has. The
caller passes the recent 1m candle arrays; `compute_keepers` returns the latest values
to feed the keeper P(Hold) model.

Pure/offline-testable: the __main__ parity test recomputes the keepers from the cached
bars and checks they match the research matrix to ~0.
"""
from __future__ import annotations

import numpy as np

try:
    from edge_probe import FEATURE_BUILDERS
except Exception:  # pragma: no cover
    import os
    import sys
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from edge_probe import FEATURE_BUILDERS

KEEPER_NAMES = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude"]


def _bars(minute, close, high, low):
    return {"minute": np.asarray(minute, dtype=np.int64),
            "close": np.asarray(close, dtype=float),
            "high": np.asarray(high, dtype=float),
            "low": np.asarray(low, dtype=float)}


def compute_keeper_series(minute, close, high, low) -> dict:
    """Full per-minute keeper arrays (parity with the research matrix, by code reuse).
    `vpin` is NOT here — it comes from the live order_flow tracker."""
    b = _bars(minute, close, high, low)
    rv, rv_names = FEATURE_BUILDERS["realized_vol"](b)      # rv_15m, rv_30m, rv_60m, rv_term
    cr, cr_names = FEATURE_BUILDERS["range_compression"](b)  # compression_ratio, range_15m
    sh, sh_names = FEATURE_BUILDERS["liquidity_shock"](b)    # shock_magnitude, micro_range_15m
    out = {}
    for arr, names in ((rv, rv_names), (cr, cr_names), (sh, sh_names)):
        for j, nm in enumerate(names):
            if nm in KEEPER_NAMES:
                out[nm] = arr[:, j]
    return out


def compute_keepers(minute, close, high, low, vpin=None) -> dict:
    """Latest-bar keeper dict for serving. Pass live `vpin` from order_flow to complete
    the 6-feature set the keeper P(Hold) model expects."""
    s = compute_keeper_series(minute, close, high, low)
    out = {nm: float(s[nm][-1]) for nm in KEEPER_NAMES if nm in s and len(s[nm])}
    if vpin is not None:
        out["vpin"] = float(vpin)
    return out


if __name__ == "__main__":
    # PARITY TEST: recompute keepers from cached aggTrade bars and compare to the
    # research matrix values on matched minutes — must be ~0 (same builders, same bars).
    import os
    import pandas as pd
    from edge_probe import _load_bars

    print("=== live_keepers parity test (reuse vs research_matrix) ===")
    bars = _load_bars(12)  # load extra history so the 60m rolling window is fully warm
    ser = compute_keeper_series(bars["minute"], bars["close"], bars["high"], bars["low"])
    df_live = pd.DataFrame({"ts_ms": bars["minute"] * 60000, **ser})
    # Drop the first 90 minutes (rolling warmup) — those legitimately differ from the
    # research matrix, which had the preceding days to warm its windows.
    df_live = df_live.iloc[90:].reset_index(drop=True)

    DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    rm = pd.read_parquet(os.path.join(DATA_DIR, "research_matrix_1m.parquet"),
                         columns=["ts_ms"] + KEEPER_NAMES)
    j = df_live.merge(rm, on="ts_ms", suffixes=("_live", "_rm"), how="inner")
    print(f"matched minutes (post-warmup): {len(j):,}")
    ok = True
    for nm in KEEPER_NAMES:
        d = (j[f"{nm}_live"] - j[f"{nm}_rm"]).abs()
        scale = j[f"{nm}_rm"].abs().mean() + 1e-12
        med = float(d.median()); p99 = float(d.quantile(0.99))
        within = float((d / scale < 1e-3).mean()) * 100  # % of rows matching to 0.1%
        flag = "OK" if within >= 99.0 else "** MISMATCH **"
        if flag != "OK":
            ok = False
        print(f"  {nm:18} median={med:.2e}  p99={p99:.2e}  within0.1%={within:5.1f}%  {flag}")
    print("PARITY:", "PASS — live reuse matches the research matrix" if ok else "FAIL — investigate")
