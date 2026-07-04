"""Evidence-first tests for structural Polymarket BTC 5m strategies.

Tests four distinct claims on the kachoio per-second executable book archive:

1. Complement arbitrage: buy UP and DOWN simultaneously when asks + both taker
   fees cost less than the guaranteed $1 settlement value. Reports whether the
   edge survives one second and the common displayed ask size.
2. Next-round opening drift: after a strong one-way close, buy the same side in
   the immediately following round at its first executable ask.
3. Model-gated simultaneous straddle: predict which near-50/50 entries will
   profit under the already-frozen +20% per-leg exit mechanics.
4. Model-gated sequential reversal: after one contract reaches a 70c extreme,
   buy the cheap opposite contract; after a reversal, optionally repeat in the
   other direction. The model predicts whether the first extreme will reverse.

All learned claims use a chronological 70/30 split. Models are fit and released
one at a time. Outputs include every OOS trade, not just headline metrics.
This is research and paper evidence only; it never submits an order.
"""
from __future__ import annotations

import argparse
import gc
import io
import json
import math
import os
import sys
import tempfile
import time
import zipfile
from datetime import date
from pathlib import Path

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_name, "2")

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "Kaggle Data" / "archive (7).zip"
DEFAULT_OUT = ROOT / "data" / "research" / "polymarket_structural_edges"
FEE_RATE = 0.07
RANDOM_STATE = 42
TRAIN_FRAC = 0.70
STRADDLE_TP = 0.20
SEQUENTIAL_EXTREME = 0.70
SELECT_FRAC = 0.10


def log(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)


def fee(price):
    p = np.asarray(price, dtype=float)
    # Polymarket rounds protocol fees to five decimal places.
    return np.round(FEE_RATE * p * (1.0 - p), 5)


def ev_summary(pnl) -> dict:
    x = np.asarray(pnl, dtype=float)
    x = x[np.isfinite(x)]
    if not len(x):
        return {"n": 0, "ev_c": None, "ev_lb_c": None, "win_rate": None,
                "profit_factor": None, "total_c": None}
    sd = float(np.std(x, ddof=1)) if len(x) > 1 else 0.0
    lb = float(np.mean(x) - 1.96 * sd / math.sqrt(len(x)))
    wins = float(x[x > 0].sum())
    losses = float(-x[x <= 0].sum())
    return {
        "n": int(len(x)), "ev_c": round(float(np.mean(x) * 100), 4),
        "ev_lb_c": round(lb * 100, 4), "win_rate": round(float(np.mean(x > 0)), 5),
        "profit_factor": round(wins / losses, 5) if losses > 0 else None,
        "total_c": round(float(np.sum(x) * 100), 3),
    }


def load_archive(archive: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    import pyarrow.parquet as pq

    log(f"[1/7] Loading executable books from {archive.name}")
    with zipfile.ZipFile(archive) as zf:
        markets = pq.read_table(
            io.BytesIO(zf.read("btc_markets.parquet")),
            columns=["condition_id", "market_start", "market_end", "outcome"],
        ).to_pandas()
        ticks = pq.read_table(
            io.BytesIO(zf.read("btc_ticks.parquet")),
            columns=["condition_id", "t", "bu", "au", "bd", "ad", "sau", "sad"],
        ).to_pandas()
    markets["outcome"] = markets["outcome"].str.lower()
    markets = markets[markets["outcome"].isin(["up", "down"])].copy()
    markets["start_t"] = markets["market_start"].astype("int64") // 10**9
    markets["end_t"] = markets["market_end"].astype("int64") // 10**9
    markets = markets[(markets.end_t - markets.start_t).between(290, 310)].copy()
    ticks = ticks[ticks.condition_id.isin(set(markets.condition_id))].copy()
    ticks = ticks.sort_values(["condition_id", "t"]).reset_index(drop=True)
    log(f"      loaded {len(markets):,} settled rounds and {len(ticks):,} second snapshots")
    return markets.sort_values("start_t").reset_index(drop=True), ticks


def complement_test(markets: pd.DataFrame, ticks: pd.DataFrame, out: Path):
    log("[2/7] Testing fee-adjusted complement arbitrage and 1s survival")
    x = ticks[["condition_id", "t", "au", "ad", "sau", "sad"]].copy()
    x["all_in"] = x.au + x.ad + fee(x.au) + fee(x.ad)
    x["margin"] = 1.0 - x.all_in
    x["common_size"] = np.minimum(x.sau.fillna(0), x.sad.fillna(0))
    x["next_t"] = x.groupby("condition_id").t.shift(-1)
    x["next_au"] = x.groupby("condition_id").au.shift(-1)
    x["next_ad"] = x.groupby("condition_id").ad.shift(-1)
    x["next_sau"] = x.groupby("condition_id").sau.shift(-1)
    x["next_sad"] = x.groupby("condition_id").sad.shift(-1)
    x["next_all_in"] = x.next_au + x.next_ad + fee(x.next_au) + fee(x.next_ad)
    x["next_margin"] = 1.0 - x.next_all_in
    x["next_common_size"] = np.minimum(x.next_sau.fillna(0), x.next_sad.fillna(0))
    raw = x[(x.margin > 0) & (x.common_size >= 1.0)].copy()
    raw["survives_1s"] = ((raw.next_t == raw.t + 1) & (raw.next_margin > 0)
                           & (raw.next_common_size >= 1.0))
    raw.to_csv(out / "complement_candidate_snapshots.csv", index=False)
    # One independent opportunity per round: the largest same-second margin.
    best = (raw.sort_values(["condition_id", "margin"], ascending=[True, False])
            .drop_duplicates("condition_id"))
    best = best.merge(markets[["condition_id", "market_start"]], on="condition_id", how="left")
    best.to_csv(out / "complement_candidates.csv", index=False)
    summary = pd.DataFrame([{
        "snapshots": len(x), "candidate_snapshots": len(raw),
        "candidate_rounds": best.condition_id.nunique(),
        "round_coverage": best.condition_id.nunique() / max(1, len(markets)),
        "survives_1s_rounds": int(raw.loc[raw.survives_1s, "condition_id"].nunique()),
        "max_margin_c": float(best.margin.max() * 100) if len(best) else None,
        "median_margin_c": float(best.margin.median() * 100) if len(best) else None,
        "median_common_size": float(best.common_size.median()) if len(best) else None,
        "max_1s_margin_c": float(raw.loc[raw.survives_1s, "next_margin"].max() * 100)
        if raw.survives_1s.any() else None,
    }])
    summary.to_csv(out / "complement_summary.csv", index=False)
    return summary.iloc[0].to_dict(), best


def _snap_at(ticks: pd.DataFrame, markets: pd.DataFrame, seconds_left: int, suffix: str):
    z = ticks.merge(markets[["condition_id", "end_t"]], on="condition_id", how="inner")
    z["distance"] = (z.end_t - z.t - seconds_left).abs()
    z = (z[z.distance <= 2].sort_values(["condition_id", "distance", "t"])
         .drop_duplicates("condition_id"))
    return z.set_index("condition_id")[["bu", "au", "bd", "ad"]].add_suffix(suffix)


def opening_drift_test(markets: pd.DataFrame, ticks: pd.DataFrame, out: Path):
    log("[3/7] Testing next-round opening drift on contiguous rounds")
    m = markets.copy()
    s240 = _snap_at(ticks, m, 240, "_240")
    s30 = _snap_at(ticks, m, 30, "_30")
    prior = m.set_index("condition_id").join(s240).join(s30)
    # The signal is the observable leader at 30s, not the eventual settled winner.
    # Settlement outcome remains available only for post-trade diagnostics.
    prior["signal_up"] = prior.bu_30 >= prior.bd_30
    prior["leader_bid_240"] = np.where(prior.signal_up, prior.bu_240, prior.bd_240)
    prior["leader_bid_30"] = np.where(prior.signal_up, prior.bu_30, prior.bd_30)
    prior["strong"] = ((prior.leader_bid_30 >= 0.75)
                       & ((prior.leader_bid_30 - prior.leader_bid_240) >= 0.20))
    prior["prior_leader_held"] = (prior.signal_up == prior.outcome.eq("up"))

    first = ticks.merge(m[["condition_id", "start_t"]], on="condition_id", how="inner")
    first["open_delay"] = first.t - first.start_t
    first = (first[first.open_delay.between(0, 10)]
             .sort_values(["condition_id", "t"]).drop_duplicates("condition_id"))
    current = m.merge(first[["condition_id", "t", "open_delay", "au", "ad"]],
                      on="condition_id", how="inner").sort_values("start_t")
    p = prior.reset_index()[["condition_id", "end_t", "signal_up", "leader_bid_240",
                             "leader_bid_30", "strong", "prior_leader_held"]].rename(columns={
                                 "condition_id": "prior_condition_id", "end_t": "prior_end_t"})
    joined = current.merge(p, left_on="start_t", right_on="prior_end_t", how="inner")
    joined["same_ask"] = np.where(joined.signal_up, joined.au, joined.ad)
    joined["same_win"] = (joined.outcome.eq("up") == joined.signal_up).astype(int)
    joined["same_pnl"] = joined.same_win - joined.same_ask - fee(joined.same_ask)
    joined["opposite_ask"] = np.where(joined.signal_up, joined.ad, joined.au)
    joined["opposite_win"] = 1 - joined.same_win
    joined["opposite_pnl"] = joined.opposite_win - joined.opposite_ask - fee(joined.opposite_ask)
    joined["chronological_split"] = np.where(
        joined.start_t < joined.start_t.quantile(TRAIN_FRAC), "train", "test")
    joined.to_csv(out / "opening_drift_trades.csv", index=False)
    rows = []
    for split in ("all", "train", "test"):
        q = joined if split == "all" else joined[joined.chronological_split == split]
        for subset, mask in (("all_contiguous", np.ones(len(q), dtype=bool)),
                             ("strong_prior_move", q.strong.to_numpy(bool))):
            for side, col in (("continue", "same_pnl"), ("reverse_placebo", "opposite_pnl")):
                stats = ev_summary(q.loc[mask, col])
                rows.append({"split": split, "subset": subset, "trade": side, **stats})
    summary = pd.DataFrame(rows)
    summary.to_csv(out / "opening_drift_summary.csv", index=False)
    return summary, joined


def _book_features(g: pd.DataFrame, i: int, secs_left: float) -> dict:
    row = g.iloc[i]
    out = {
        "secs_left": secs_left, "bu": row.bu, "au": row.au, "bd": row.bd, "ad": row.ad,
        "up_spread": row.au - row.bu, "down_spread": row.ad - row.bd,
        "ask_sum": row.au + row.ad, "bid_sum": row.bu + row.bd,
        "up_mid": (row.au + row.bu) / 2, "down_mid": (row.ad + row.bd) / 2,
        "ask_size_log_ratio": math.log1p(max(0, row.sau)) - math.log1p(max(0, row.sad)),
    }
    for lag in (5, 15, 30, 60):
        # Archive coverage is usually 1 Hz but can contain gaps. Use elapsed
        # seconds rather than row offsets so a 60s feature is really 60 seconds.
        target_t = float(row.t) - lag
        j = max(0, int(np.searchsorted(g.t.to_numpy(), target_t, side="right") - 1))
        old = g.iloc[j]
        out[f"up_mid_d{lag}"] = out["up_mid"] - (old.au + old.bu) / 2
        out[f"ask_sum_d{lag}"] = out["ask_sum"] - old.au - old.ad
    return out


def _simulate_straddle(g: pd.DataFrame, i: int, outcome_up: bool) -> dict:
    entry = g.iloc[i]
    eu, ed = float(entry.au), float(entry.ad)
    cost = eu + ed + float(fee(eu) + fee(ed))
    xu = xd = None
    for row in g.iloc[i + 1:].itertuples(index=False):
        if xu is None and row.bu >= eu * (1 + STRADDLE_TP):
            xu = float(row.bu - fee(row.bu))
        if xd is None and row.bd >= ed * (1 + STRADDLE_TP):
            xd = float(row.bd - fee(row.bd))
        if xu is not None and xd is not None:
            break
    both_tp = xu is not None and xd is not None
    if xu is None:
        xu = 1.0 if outcome_up else 0.0
    if xd is None:
        xd = 0.0 if outcome_up else 1.0
    return {"pnl": xu + xd - cost, "both_tp": int(both_tp), "entry_cost": cost}


def build_model_events(markets: pd.DataFrame, ticks: pd.DataFrame):
    log("[4/7] Building causal simultaneous-straddle and sequential-reversal events")
    meta = markets.set_index("condition_id")[["start_t", "end_t", "outcome"]]
    straddle, sequential = [], []
    for n, (cid, g0) in enumerate(ticks.groupby("condition_id", sort=False), 1):
        if cid not in meta.index:
            continue
        m = meta.loc[cid]
        g = g0.sort_values("t").reset_index(drop=True)
        secs = m.end_t - g.t.to_numpy()
        # Frozen blind-straddle candidate: first qualifying second in 270..180s.
        idx = np.flatnonzero((secs <= 270) & (secs >= 180)
                             & (np.maximum(g.bu, g.bd).to_numpy() <= 0.55)
                             & ((g.au - g.bu).to_numpy() <= 0.02)
                             & ((g.ad - g.bd).to_numpy() <= 0.02)
                             & (g.sau.to_numpy() >= 1.0) & (g.sad.to_numpy() >= 1.0))
        if len(idx):
            i = int(idx[0])
            f = _book_features(g, i, float(secs[i]))
            f.update({"condition_id": cid, "start_t": int(m.start_t)})
            f.update(_simulate_straddle(g, i, m.outcome == "up"))
            straddle.append(f)

        # Sequential reversal: first 70c leader extreme with >=60s remaining.
        leader_up = g.bu.to_numpy() >= g.bd.to_numpy()
        cheap_ask = np.where(leader_up, g.ad.to_numpy(), g.au.to_numpy())
        cheap_bid = np.where(leader_up, g.bd.to_numpy(), g.bu.to_numpy())
        cheap_size = np.where(leader_up, g.sad.to_numpy(), g.sau.to_numpy())
        extreme = np.flatnonzero((secs <= 270) & (secs >= 60)
                                 & (np.maximum(g.bu, g.bd).to_numpy() >= SEQUENTIAL_EXTREME)
                                 & (cheap_ask >= 0.03) & (cheap_ask <= 0.90)
                                 & ((cheap_ask - cheap_bid) <= 0.03) & (cheap_size >= 1.0))
        if len(extreme):
            i = int(extreme[0])
            first_up = bool(g.bu.iat[i] >= g.bd.iat[i])
            cheap_ask = float(g.ad.iat[i] if first_up else g.au.iat[i])
            cheap_bid_col = "bd" if first_up else "bu"
            opposite_leader_col = "bd" if first_up else "bu"
            cheap_side_wins = (m.outcome == "down") if first_up else (m.outcome == "up")
            first_exit = None
            second_i = None
            for k in range(i + 1, len(g)):
                bid = float(g[cheap_bid_col].iat[k])
                if first_exit is None and bid >= cheap_ask * (1 + STRADDLE_TP):
                    first_exit = bid - float(fee(bid))
                if second_i is None and float(g[opposite_leader_col].iat[k]) >= SEQUENTIAL_EXTREME:
                    second_ask = float(g.au.iat[k] if first_up else g.ad.iat[k])
                    second_bid = float(g.bu.iat[k] if first_up else g.bd.iat[k])
                    second_size = float(g.sau.iat[k] if first_up else g.sad.iat[k])
                    if (0.03 <= second_ask <= 0.90 and second_ask - second_bid <= 0.03
                            and second_size >= 1.0):
                        second_i = k
                if first_exit is not None and second_i is not None:
                    break
            first_value = first_exit if first_exit is not None else (1.0 if cheap_side_wins else 0.0)
            first_pnl = first_value - cheap_ask - float(fee(cheap_ask))
            # A second model-approved fade would buy the cheap side at the opposite extreme.
            second_pnl = 0.0
            second_available = second_i is not None
            if second_available:
                second_ask = float(g.au.iat[second_i] if first_up else g.ad.iat[second_i])
                second_bid_col = "bu" if first_up else "bd"
                second_side_wins = (m.outcome == "up") if first_up else (m.outcome == "down")
                second_exit = None
                for k in range(second_i + 1, len(g)):
                    bid = float(g[second_bid_col].iat[k])
                    if bid >= second_ask * (1 + STRADDLE_TP):
                        second_exit = bid - float(fee(bid))
                        break
                second_value = second_exit if second_exit is not None else (1.0 if second_side_wins else 0.0)
                second_pnl = second_value - second_ask - float(fee(second_ask))
            f = _book_features(g, i, float(secs[i]))
            f.update({"condition_id": cid, "start_t": int(m.start_t),
                      "first_extreme_up": int(first_up), "cheap_ask": cheap_ask,
                      "reversal": int(second_available), "first_leg_pnl": first_pnl,
                      "two_leg_pnl": first_pnl + second_pnl if second_available else first_pnl})
            sequential.append(f)
        if n % 2500 == 0:
            log(f"      replayed {n:,}/{len(markets):,} rounds")
    return pd.DataFrame(straddle), pd.DataFrame(sequential)


def _models():
    from sklearn.ensemble import (ExtraTreesClassifier, GradientBoostingClassifier,
                                  HistGradientBoostingClassifier, RandomForestClassifier)
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return [
        ("LogisticRegression", make_pipeline(StandardScaler(), LogisticRegression(
            max_iter=1000, class_weight="balanced", random_state=RANDOM_STATE))),
        ("HistGradientBoosting", HistGradientBoostingClassifier(
            max_iter=160, max_leaf_nodes=15, learning_rate=0.05, l2_regularization=1.0,
            random_state=RANDOM_STATE)),
        ("RandomForest", RandomForestClassifier(
            n_estimators=250, max_depth=8, min_samples_leaf=20, max_features="sqrt",
            class_weight="balanced_subsample", n_jobs=2, random_state=RANDOM_STATE)),
        ("ExtraTrees", ExtraTreesClassifier(
            n_estimators=250, max_depth=10, min_samples_leaf=15, max_features="sqrt",
            class_weight="balanced", n_jobs=2, random_state=RANDOM_STATE)),
        ("GradientBoosting", GradientBoostingClassifier(
            n_estimators=150, max_depth=2, min_samples_leaf=20, learning_rate=0.04,
            random_state=RANDOM_STATE)),
    ]


def model_test(df: pd.DataFrame, target: str, pnl: str, family: str, out: Path):
    from sklearn.base import clone
    from sklearn.metrics import average_precision_score, roc_auc_score

    excluded = {"condition_id", "start_t", "pnl", "both_tp", "entry_cost", "reversal",
                "first_leg_pnl", "two_leg_pnl"}
    features = [c for c in df.columns if c not in excluded and pd.api.types.is_numeric_dtype(df[c])]
    df = df.sort_values("start_t").dropna(subset=features + [target, pnl]).reset_index(drop=True)
    cut = max(1, min(len(df) - 1, int(len(df) * TRAIN_FRAC)))
    tr, te = df.iloc[:cut], df.iloc[cut:]
    Xtr, Xte = tr[features].to_numpy(float), te[features].to_numpy(float)
    ytr, yte = tr[target].to_numpy(int), te[target].to_numpy(int)
    metrics, predictions = [], []
    base = ev_summary(te[pnl])
    metrics.append({"family": family, "model": "UNGATED", "auc": None, "average_precision": None,
                    "selected_fraction": 1.0, **base})
    for name, model in _models():
        log(f"[5/7] {family}: fitting {name} ({len(tr):,} train / {len(te):,} test)")
        # Learn the operating threshold from a chronological calibration tail
        # inside the training period. Selecting the top test decile would use
        # future score distribution information unavailable to a live policy.
        cal_cut = max(50, int(len(tr) * 0.80))
        cal_cut = min(cal_cut, len(tr) - 25)
        calibrator = clone(model)
        calibrator.fit(Xtr[:cal_cut], ytr[:cal_cut])
        cal_prob = calibrator.predict_proba(Xtr[cal_cut:])[:, 1]
        threshold = float(np.quantile(cal_prob, 1.0 - SELECT_FRAC))
        del calibrator, cal_prob
        model.fit(Xtr, ytr)
        prob = model.predict_proba(Xte)[:, 1]
        selected = prob >= threshold
        s = ev_summary(te.loc[selected, pnl])
        metrics.append({
            "family": family, "model": name,
            "auc": round(float(roc_auc_score(yte, prob)), 5) if len(np.unique(yte)) > 1 else None,
            "average_precision": round(float(average_precision_score(yte, prob)), 5),
            "threshold": round(threshold, 6),
            "selected_fraction": round(float(np.mean(selected)), 5), **s,
        })
        pred = te[["condition_id", "start_t", target, pnl]].copy()
        pred["family"], pred["model"], pred["probability"], pred["selected"] = (
            family, name, prob, selected)
        predictions.append(pred)
        del model, prob
        gc.collect()
    pd.concat(predictions, ignore_index=True).to_csv(
        out / f"{family}_oos_predictions.csv", index=False)
    return pd.DataFrame(metrics), features


def write_report(out: Path, comp: dict, opening: pd.DataFrame, model_metrics: pd.DataFrame,
                 features: dict, counts: dict):
    def table(df):
        if not len(df):
            return "No rows."
        clean = df.copy().where(pd.notna(df), "—")
        cols = [str(c) for c in clean.columns]
        lines = ["| " + " | ".join(cols) + " |",
                 "|" + "|".join("---" for _ in cols) + "|"]
        for values in clean.astype(str).itertuples(index=False, name=None):
            lines.append("| " + " | ".join(v.replace("|", "\\|") for v in values) + " |")
        return "\n".join(lines)

    open_test = opening[(opening.split == "test") & (opening.subset == "strong_prior_move")]
    report = [
        f"# Polymarket Structural-Edge Test ({date.today().isoformat()})", "",
        "All prices are executable top-of-book quotes. Fees are charged on every taker entry and early exit. "
        "Learned claims use the oldest 70% for training and newest 30% for testing.", "",
        "## 1. Complement arbitrage", "",
        f"- Candidate rounds: **{int(comp.get('candidate_rounds') or 0):,}** / {counts['rounds']:,}",
        f"- Candidates surviving one second: **{int(comp.get('survives_1s_rounds') or 0):,}**",
        f"- Maximum same-second fee-adjusted margin: **{float(comp.get('max_margin_c') or 0):.3f}c**",
        "- Interpretation: a same-second quote is only riskless if both asks can be filled together at the "
        "displayed common size. One-second survival is the more conservative executable proxy.", "",
        "## 2. Next-round opening drift", "", table(open_test), "",
        "`continue` buys the previous round's observable 30-second leader in the next contiguous round. "
        "`reverse_placebo` buys the opposite side at the same opening snapshot.", "",
        "## 3. Model-gated straddles", "", table(model_metrics), "",
        "A selector is not promoted merely for AUC. Its newest-30% selected trades need positive EV, "
        "positive 95% lower-bound EV, and improvement over the ungated strategy. Top-decile selection was "
        "fixed before fitting; no threshold was optimized on test.", "",
        "## Features", "",
    ]
    for family, cols in features.items():
        report.extend([f"### {family}", "", ", ".join(f"`{c}`" for c in cols), ""])
    report.extend([
        "## Deployment decision", "",
        "- Complement arbitrage may become an alert/shadow scanner only if positive candidates survive "
        "one second and have non-trivial common displayed size.",
        "- Opening drift requires positive OOS EV lower bound before any live shadow.",
        "- Model straddles remain paper-only until independent forward results reproduce positive OOS EV. "
        "A profitable classifier metric without profitable ask-to-exit accounting is not an edge.", "",
    ])
    (out / "REPORT.md").write_text("\n".join(report), encoding="utf-8")


def selftest() -> bool:
    assert abs(float(fee(0.5)) - 0.0175) < 1e-12
    assert ev_summary([0.1, -0.05])["total_c"] == 5.0
    # Both asks at 47c have 96.49c all-in cost and therefore a true complement margin.
    margin = 1 - (0.47 + 0.47 + float(fee(0.47)) * 2)
    assert margin > 0
    # Timestamp lags must use elapsed seconds, not row count when snapshots gap.
    gap = pd.DataFrame({
        "t": [0, 10, 70], "bu": [.40, .45, .55], "au": [.41, .46, .56],
        "bd": [.59, .54, .44], "ad": [.60, .55, .45],
        "sau": [10, 10, 10], "sad": [10, 10, 10],
    })
    f = _book_features(gap, 2, 230)
    assert abs(f["up_mid_d60"] - (.555 - .455)) < 1e-12, f
    # Opening continuation must use the observable 30s leader, even when that
    # leader later loses the prior round.
    mk = pd.DataFrame({
        "condition_id": ["prior", "current"],
        "market_start": pd.to_datetime([0, 300], unit="s", utc=True),
        "market_end": pd.to_datetime([300, 600], unit="s", utc=True),
        "outcome": ["down", "up"], "start_t": [0, 300], "end_t": [300, 600],
    })
    tk = pd.DataFrame([
        {"condition_id": "prior", "t": 60, "bu": .49, "au": .50,
         "bd": .50, "ad": .51, "sau": 10, "sad": 10},
        {"condition_id": "prior", "t": 270, "bu": .80, "au": .81,
         "bd": .19, "ad": .20, "sau": 10, "sad": 10},
        {"condition_id": "current", "t": 300, "bu": .49, "au": .51,
         "bd": .48, "ad": .50, "sau": 10, "sad": 10},
    ])
    with tempfile.TemporaryDirectory() as td:
        _, trades = opening_drift_test(mk, tk, Path(td))
    assert len(trades) == 1 and bool(trades.signal_up.iloc[0])
    assert int(trades.same_win.iloc[0]) == 1 and not bool(trades.prior_leader_held.iloc[0])
    print("selftest: PASS")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", default=str(ARCHIVE))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return 0 if selftest() else 1
    archive = Path(args.archive)
    if not archive.exists():
        raise FileNotFoundError(archive)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    config = {
        "archive": str(archive), "fee_rate": FEE_RATE, "train_fraction": TRAIN_FRAC,
        "test_fraction": 1 - TRAIN_FRAC, "straddle_tp": STRADDLE_TP,
        "sequential_extreme": SEQUENTIAL_EXTREME, "model_selected_fraction": SELECT_FRAC,
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    (out / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    markets, ticks = load_archive(archive)
    comp, _ = complement_test(markets, ticks, out)
    opening, _ = opening_drift_test(markets, ticks, out)
    straddle, sequential = build_model_events(markets, ticks)
    straddle.to_parquet(out / "simultaneous_straddle_events.parquet", index=False)
    sequential.to_parquet(out / "sequential_reversal_events.parquet", index=False)
    sm, sf = model_test(straddle, "both_tp", "pnl", "simultaneous_straddle", out)
    qm, qf = model_test(sequential, "reversal", "two_leg_pnl", "sequential_reversal", out)
    metrics = pd.concat([sm, qm], ignore_index=True)
    metrics.to_csv(out / "model_strategy_metrics.csv", index=False)
    write_report(out, comp, opening, metrics,
                 {"simultaneous_straddle": sf, "sequential_reversal": qf},
                 {"rounds": len(markets), "ticks": len(ticks)})
    log(f"[7/7] Complete. Report: {out / 'REPORT.md'}")
    print("\n" + metrics.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
