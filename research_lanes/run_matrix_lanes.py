"""Three Binance lanes that need only the research matrix. Run together, reported separately.

  VOLATILITY_EXPANSION_V1  - can we predict WHICH windows move enough to be worth trading?
  TIME_PHASE_ALPHA_V1      - does clock phase carry return or volatility structure?
  SPOT_PERP_BASIS_V1       - does an extreme perp-spot basis mean-revert past costs?

Each is scored with a day-block bootstrap and against an explicit baseline. None of them fits
a model on more than causal, backward-looking columns already present in the matrix.

    python research_lanes/run_matrix_lanes.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

LANES = Path(__file__).resolve().parent
sys.path.insert(0, str(LANES))
from common.scorecard import day_block_bootstrap, day_index, forward_abs_move_bps  # noqa: E402

REPO = LANES.parent
MATRIX = REPO / "data" / "research_matrix_1m.parquet"
COST_BPS = 12.0
HORIZON = 5          # bars; the app's primary horizon


def _load():
    cols = ["ts_ms", "close", "rv_15m", "rv_30m", "rv_60m", "compression_ratio",
            "shock_magnitude", "vpin_15m", "perp_spot_basis_bps", "cvd_divergence"]
    df = pd.read_parquet(MATRIX, columns=cols).replace([np.inf, -np.inf], np.nan)
    df["day"] = day_index(df["ts_ms"].to_numpy("int64"))
    return df


# ---------------------------------------------------------------- volatility expansion
def volatility_expansion(df: pd.DataFrame) -> dict:
    """Target: will |5m move| exceed the 12bps round trip? Features are backward-looking only.

    The point is NOT direction. If only 23% of windows clear costs (BINANCE_COST_CLEARANCE_V1),
    then knowing WHICH 23% is worth more than knowing the sign.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    feats = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude", "vpin_15m"]
    d = df.dropna(subset=feats + ["close"]).copy()
    a = forward_abs_move_bps(d["close"].to_numpy(float), HORIZON)
    d["y"] = (a > COST_BPS).astype(float)
    d.loc[~np.isfinite(a), "y"] = np.nan
    d = d.dropna(subset=["y"])

    days = np.sort(d["day"].unique())
    cut = days[int(len(days) * 0.70)]
    purge = HORIZON                     # bars; drop rows whose outcome crosses the day cut
    tr = d[d["day"] < cut].iloc[:-purge]
    te = d[d["day"] >= cut]

    clf = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.06, random_state=42)
    clf.fit(tr[feats].values, tr["y"].values)
    p = clf.predict_proba(te[feats].values)[:, 1]
    auc = float(roc_auc_score(te["y"].values, p))

    # Baseline: realized vol alone. A model must beat the obvious thing.
    auc_rv = float(roc_auc_score(te["y"].values, te["rv_15m"].values))

    boot = day_block_bootstrap((p > np.median(p)).astype(float) == te["y"].values,
                               te["day"].values, np.mean, n_boot=400)
    # Top-decile lift: of the windows the model ranks most likely to move, how many do?
    k = max(1, int(len(p) * 0.10))
    top = np.argsort(p)[-k:]
    lift = float(te["y"].values[top].mean() / te["y"].values.mean())
    return {
        "auc": auc, "auc_baseline_rv15": auc_rv, "base_rate": float(te["y"].mean()),
        "top_decile_hit": float(te["y"].values[top].mean()), "top_decile_lift": lift,
        "acc_lcb": boot["lcb"], "n_test": int(len(te)), "n_test_days": int(te["day"].nunique()),
        "train_days": int(tr["day"].nunique()),
    }


# ---------------------------------------------------------------- time phase
def time_phase(df: pd.DataFrame) -> dict:
    """Minute-of-hour and minute-of-quarter structure in forward move and realized return."""
    d = df.dropna(subset=["close"]).copy()
    ts = d["ts_ms"].to_numpy("int64")
    d["abs5"] = forward_abs_move_bps(d["close"].to_numpy(float), HORIZON)
    d["min_of_hour"] = (ts // 60_000) % 60
    d["min_of_quarter"] = (ts // 60_000) % 15
    d = d.dropna(subset=["abs5"])

    overall = float(d["abs5"].mean())
    by_q = d.groupby("min_of_quarter")["abs5"].mean()
    # Bound the single most extreme bucket by day bootstrap - the max of 15 buckets is a biased
    # statistic, so it needs an interval before it is called an effect.
    hot = int(by_q.idxmax())
    sub = d[d["min_of_quarter"] == hot]
    boot = day_block_bootstrap(sub["abs5"].values, sub["day"].values, np.mean, n_boot=400)
    rest = d[d["min_of_quarter"] != hot]
    boot_rest = day_block_bootstrap(rest["abs5"].values, rest["day"].values, np.mean, n_boot=400)
    return {
        "overall_mean_abs_bps": overall,
        "by_minute_of_quarter": {int(k): round(float(v), 2) for k, v in by_q.items()},
        "hottest_bucket": hot, "hot_mean": boot["point"], "hot_lcb": boot["lcb"],
        "rest_mean": boot_rest["point"], "rest_ucb": boot_rest["ucb"],
        "separated": bool(boot["lcb"] > boot_rest["ucb"]),
        "n_days": int(d["day"].nunique()),
    }


# ---------------------------------------------------------------- spot-perp basis
def spot_perp_basis(df: pd.DataFrame) -> dict:
    """Does an extreme perp-spot basis revert by more than the round trip?"""
    d = df.dropna(subset=["perp_spot_basis_bps", "close"]).copy()
    b = d["perp_spot_basis_bps"].to_numpy(float)
    out = {}
    for h in (5, 15, 30):
        fwd = np.full(len(b), np.nan)
        if h < len(b):
            fwd[:-h] = b[h:] - b[:-h]          # change in basis over the next h bars
        d[f"db{h}"] = fwd
    hi, lo = np.nanpercentile(b, 95), np.nanpercentile(b, 5)
    for h in (5, 15, 30):
        col = f"db{h}"
        rich = d[(d["perp_spot_basis_bps"] >= hi) & d[col].notna()]
        cheap = d[(d["perp_spot_basis_bps"] <= lo) & d[col].notna()]
        # Rich basis should NARROW (negative change) to be tradeable short-perp/long-spot.
        br = day_block_bootstrap(-rich[col].values, rich["day"].values, np.mean, n_boot=400)
        bc = day_block_bootstrap(cheap[col].values, cheap["day"].values, np.mean, n_boot=400)
        out[f"{h}m"] = {
            "rich_reversion_bps": br["point"], "rich_lcb": br["lcb"], "n_rich": br["n_rows"],
            "cheap_reversion_bps": bc["point"], "cheap_lcb": bc["lcb"], "n_cheap": bc["n_rows"],
            "rich_clears_cost": bool(br["lcb"] > COST_BPS),
            "cheap_clears_cost": bool(bc["lcb"] > COST_BPS),
        }
    out["p95_basis_bps"] = float(hi)
    out["p05_basis_bps"] = float(lo)
    return out


def main() -> int:
    df = _load()
    print(f"matrix rows={len(df):,}  days={df['day'].nunique():,}")
    res = {}
    print("\n[1/3] VOLATILITY_EXPANSION_V1 ..."); res["volatility_expansion"] = volatility_expansion(df)
    print("[2/3] TIME_PHASE_ALPHA_V1 ...");      res["time_phase"] = time_phase(df)
    print("[3/3] SPOT_PERP_BASIS_V1 ...");       res["spot_perp_basis"] = spot_perp_basis(df)

    v = res["volatility_expansion"]
    print(f"\nVOLATILITY EXPANSION  AUC={v['auc']:.3f} (rv15 baseline {v['auc_baseline_rv15']:.3f}) "
          f"base={v['base_rate']:.1%} top-decile hit={v['top_decile_hit']:.1%} "
          f"lift={v['top_decile_lift']:.2f}x  test days={v['n_test_days']}")
    t = res["time_phase"]
    print(f"TIME PHASE  hottest min-of-quarter={t['hottest_bucket']} "
          f"mean={t['hot_mean']:.2f} LCB={t['hot_lcb']:.2f} vs rest {t['rest_mean']:.2f} "
          f"UCB={t['rest_ucb']:.2f}  separated={t['separated']}")
    s = res["spot_perp_basis"]
    for h in ("5m", "15m", "30m"):
        r = s[h]
        print(f"BASIS {h:>3}  rich revert {r['rich_reversion_bps']:+.2f} (LCB {r['rich_lcb']:+.2f}) "
              f"clears12={r['rich_clears_cost']} | cheap {r['cheap_reversion_bps']:+.2f} "
              f"(LCB {r['cheap_lcb']:+.2f}) clears12={r['cheap_clears_cost']}")

    (LANES / "matrix_lanes_results.json").write_text(json.dumps(res, indent=2, default=float),
                                                     encoding="utf-8")
    print(f"\nwrote {LANES / 'matrix_lanes_results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
