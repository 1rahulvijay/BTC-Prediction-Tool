"""The frozen feature families for MULTIHORIZON_DIRECTION_V1. All backward-looking.

The label may see the future. Features may not. Every rolling statistic is shifted so that a
bar never reads itself, and the cross-sectional features are computed per timestamp across
pairs, then shifted, so a pair never reads the current bar of its peers either.

    python -m research.multihorizon.features --selftest
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

BARS_PER_HOUR = 4
HORIZON_MINUTES = (60, 240, 300, 600)
HORIZON_BARS = {m: m // 15 for m in HORIZON_MINUTES}

#: Never permitted as features.
FORBIDDEN = ("fwd_ret_bps", "fwd_abs_bps", "label", "future")


def _per_symbol(group: pd.DataFrame) -> pd.DataFrame:
    g = group.sort_values("ts_ms").copy()
    close, high, low = g["close"], g["high"], g["low"]
    ret1 = close.pct_change()

    for n in (1, 4, 16, 40):
        g[f"ret_{n}"] = close.pct_change(n).shift(1) * 1e4
        g[f"rv_{n}"] = ret1.rolling(max(n, 2), min_periods=max(n, 2)).std().shift(1) * 1e4
    g["atr_16"] = ((high - low) / close).rolling(16, min_periods=16).mean().shift(1) * 1e4
    hi40 = high.rolling(40, min_periods=40).max().shift(1)
    lo40 = low.rolling(40, min_periods=40).min().shift(1)
    span = (hi40 - lo40).replace(0.0, np.nan)
    g["range_pos"] = ((close.shift(1) - lo40) / span).clip(0, 1)
    g["dist_high"] = ((hi40 - close.shift(1)) / close.shift(1)) * 1e4
    g["dist_low"] = ((close.shift(1) - lo40) / close.shift(1)) * 1e4
    g["compression"] = (g["rv_4"] / g["rv_40"].replace(0.0, np.nan))
    sign = np.sign(ret1.fillna(0.0))
    g["run_len"] = sign.groupby((sign != sign.shift()).cumsum()).cumcount().shift(1)

    g["turnover_z"] = ((g["turnover"] - g["turnover"].rolling(96, min_periods=96).mean())
                       / g["turnover"].rolling(96, min_periods=96).std()).shift(1)
    g["vol_accel"] = (g["turnover"].rolling(4, min_periods=4).sum()
                      / g["turnover"].rolling(40, min_periods=40).sum().replace(0.0, np.nan)
                      ).shift(1)

    oi = g["open_interest"]
    g["oi_level"] = np.log(oi.replace(0.0, np.nan)).shift(1)
    g["oi_chg_1h"] = oi.pct_change(BARS_PER_HOUR).shift(1) * 1e4
    g["oi_chg_24h"] = oi.pct_change(96).shift(1) * 1e4
    # OI up while price down (and vice versa) is the classic divergence sign.
    g["oi_price_div"] = np.sign(g["oi_chg_1h"]) * -np.sign(g["ret_4"])
    g["funding"] = g["funding_rate"].shift(1) * 1e4
    g["funding_chg"] = g["funding_rate"].diff().shift(1) * 1e4
    g["funding_vs_ret"] = np.sign(g["funding"]) * np.sign(g["ret_16"])

    basis = (g["close"] - g["bin_close"]) / g["close"] * 1e4
    g["basis_bps"] = basis.shift(1)
    g["basis_chg"] = basis.diff().shift(1)
    g["rel_volume"] = (g["volume"] / g["bin_volume"].replace(0.0, np.nan)).shift(1)

    g["hour"] = ((g["ts_ms"] // 3600_000) % 24).astype(float)
    g["dow"] = ((g["ts_ms"] // 86_400_000) % 7).astype(float)

    for minutes, bars in HORIZON_BARS.items():
        fwd = (g["open"].shift(-1 - bars) / g["open"].shift(-1) - 1.0) * 1e4
        g[f"fwd_ret_bps_{minutes}"] = fwd
    return g


def build(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.concat([_per_symbol(g) for _, g in frame.groupby("symbol", sort=False)],
                    ignore_index=True)
    # CROSS-SECTIONAL, computed per timestamp then SHIFTED - a pair must not read the current
    # bar of its peers, which would be a subtle same-bar leak across the panel.
    out = out.sort_values(["ts_ms", "symbol"])
    by_ts = out.groupby("ts_ms")["ret_4"]
    out["xs_rank"] = by_ts.rank(pct=True)
    out["xs_dispersion"] = by_ts.transform("std")
    btc = (out[out.symbol == "BTCUSDT"].set_index("ts_ms")["ret_4"]
           .reindex(out["ts_ms"].values).to_numpy())
    out["rel_to_btc"] = out["ret_4"].to_numpy() - btc
    for column in ("xs_rank", "xs_dispersion", "rel_to_btc"):
        out[column] = out.groupby("symbol")[column].shift(1)
    out["day"] = out["ts_ms"] // 86_400_000
    return out.sort_values(["symbol", "ts_ms"]).reset_index(drop=True)


FEATURES = (
    "ret_1", "ret_4", "ret_16", "ret_40", "rv_1", "rv_4", "rv_16", "rv_40",
    "atr_16", "range_pos", "dist_high", "dist_low", "compression", "run_len",
    "turnover_z", "vol_accel",
    "oi_level", "oi_chg_1h", "oi_chg_24h", "oi_price_div",
    "funding", "funding_chg", "funding_vs_ret",
    "basis_bps", "basis_chg", "rel_volume",
    "xs_rank", "xs_dispersion", "rel_to_btc",
    "hour", "dow",
)


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    check(len(FEATURES) == 31, "the frozen feature set is 31 columns")
    leaked = [f for f in FEATURES if any(bad in f for bad in FORBIDDEN)]
    check(not leaked, "no forbidden token appears in any feature name")
    check(HORIZON_BARS == {60: 4, 240: 16, 300: 20, 600: 40},
          "the four declared horizons map to 15-minute bars correctly")

    rng = np.random.default_rng(0)
    n = 600
    ts = np.arange(n, dtype="int64") * 900_000 + 1_785_000_000_000
    rows = []
    for i, symbol in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT")):
        walk = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
        rows.append(pd.DataFrame({
            "symbol": symbol, "ts_ms": ts, "open": walk, "high": walk * 1.002,
            "low": walk * 0.998, "close": walk, "volume": rng.uniform(1, 5, n),
            "turnover": rng.uniform(1e6, 5e6, n),
            "open_interest": np.linspace(1e5, 1.2e5, n) * (1 + 0.1 * i),
            "funding_rate": 0.0001, "bin_close": walk * (1 + 0.0001),
            "bin_volume": rng.uniform(1, 5, n)}))
    built = build(pd.concat(rows, ignore_index=True))

    check(len(built) == n * 3, "every input bar survives the build")
    check(all(f in built.columns for f in FEATURES), "every declared feature is produced")

    # CAUSALITY: a feature at bar t must not change when bar t's own close changes.
    mutated = pd.concat(rows, ignore_index=True).copy()
    target = (mutated.symbol == "BTCUSDT") & (mutated.ts_ms == ts[300])
    mutated.loc[target, "close"] *= 1.5
    remade = build(mutated)
    a = built[(built.symbol == "BTCUSDT") & (built.ts_ms == ts[300])][list(FEATURES)]
    b = remade[(remade.symbol == "BTCUSDT") & (remade.ts_ms == ts[300])][list(FEATURES)]
    same = [c for c in FEATURES if np.allclose(a[c].to_numpy(), b[c].to_numpy(),
                                               equal_nan=True)]
    check(len(same) == len(FEATURES),
          "changing a bar's OWN close leaves all 31 of its features unchanged")

    # ...and an EARLIER bar's forward label MUST change when a bar inside its horizon moves.
    # The label is built from OPENS (executable entry and exit), so the open is what to perturb:
    # bar t's 60m label spans open[t+1] to open[t+5], so bar 295 covers bar 300.
    moved = pd.concat(rows, ignore_index=True).copy()
    moved.loc[(moved.symbol == "BTCUSDT") & (moved.ts_ms == ts[300]), "open"] *= 1.5
    shifted = build(moved)
    la = built[(built.symbol == "BTCUSDT") & (built.ts_ms == ts[295])]["fwd_ret_bps_60"]
    lb = shifted[(shifted.symbol == "BTCUSDT") & (shifted.ts_ms == ts[295])]["fwd_ret_bps_60"]
    check(not np.allclose(la.to_numpy(), lb.to_numpy(), equal_nan=True),
          "...while an EARLIER bar's forward label DOES change - the label is forward-looking")
    fa = built[(built.symbol == "BTCUSDT") & (built.ts_ms == ts[295])][list(FEATURES)]
    fb = shifted[(shifted.symbol == "BTCUSDT") & (shifted.ts_ms == ts[295])][list(FEATURES)]
    check(all(np.allclose(fa[c].to_numpy(), fb[c].to_numpy(), equal_nan=True)
              for c in FEATURES),
          "...and that FUTURE bar never leaks back into the earlier bar's features")

    check(built["fwd_ret_bps_600"].isna().sum() >= 40 * 3,
          "forward labels are NaN at each symbol's tail, never fabricated")
    check(built["range_pos"].dropna().between(0, 1).all(), "range position stays in [0, 1]")

    print(f"\nMULTIHORIZON FEATURES SELFTEST: PASS ({checks} checks)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.parse_args()
    raise SystemExit(selftest())
