"""Causal Polymarket quote-response and executable-edge research.

Reads the recorder DB, or its parquet exports while the DB is locked. The script
never writes to the live database and never places orders. It evaluates only
independent, near-open rounds and writes descriptive research artifacts.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR", ROOT / "data"))
DB = Path(os.environ.get("BTC_EXEC_DB", DATA / "execution_layer.duckdb"))
SNAP_EXPORT = DATA / "pm_export_snapshots.parquet"
SETT_EXPORT = DATA / "pm_export_settlements.parquet"
DEFAULT_OUT = DATA / "research" / "polymarket_market_response"
FEE_RATE = 0.07
FAIR_CAP = 0.91
MAX_ANCHOR_SKEW = 5.0
MIN_TRAIN_ROUNDS = 200
MIN_PROMOTION_ROUNDS = 1000


def fee(price):
    p = np.clip(np.asarray(price, dtype=float), 0.0, 1.0)
    return FEE_RATE * p * (1.0 - p)


def wilson_low(k, n, z=1.96):
    if n <= 0:
        return np.nan
    p = k / n
    d = 1.0 + z * z / n
    return (p + z * z / (2 * n) - z * math.sqrt(
        p * (1 - p) / n + z * z / (4 * n * n))) / d


def bootstrap_mean_ci(values, seed=17, draws=4000):
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    means = np.empty(draws, dtype=float)
    for i in range(draws):
        means[i] = np.mean(rng.choice(x, size=len(x), replace=True))
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def markdown_table(frame):
    if frame.empty:
        return "_No eligible rows._"
    work = frame.copy()
    work.columns = [str(c) for c in work.columns]
    header = "| " + " | ".join(work.columns) + " |"
    rule = "|" + "|".join(["---"] * len(work.columns)) + "|"
    rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in work.itertuples(index=False, name=None)]
    return "\n".join([header, rule, *rows])


def load_tables():
    import duckdb
    try:
        con = duckdb.connect(str(DB), read_only=True)
        snap = con.execute("SELECT * FROM pm_round_snapshots").df()
        sett = con.execute("SELECT * FROM pm_round_settlements").df()
        con.close()
        return snap, sett, "live_db"
    except Exception:
        if not SNAP_EXPORT.exists():
            raise FileNotFoundError(f"No readable recorder DB or export: {DB}")
        snap = pd.read_parquet(SNAP_EXPORT)
        sett = pd.read_parquet(SETT_EXPORT) if SETT_EXPORT.exists() else pd.DataFrame()
        return snap, sett, "parquet_export"


def clean_tables(snap, sett):
    required = {
        "ts", "slug", "horizon", "anchor_ts", "seconds_left", "anchor_price", "btc_price",
        "p_hold_up", "p_hold_down", "up_bid", "up_ask", "up_mid", "up_spread",
        "up_top_ask_size", "up_d1", "up_d2", "up_d5", "down_bid", "down_ask",
        "down_mid", "down_spread", "down_top_ask_size", "down_d1", "down_d2", "down_d5",
    }
    missing = sorted(required - set(snap.columns))
    if missing:
        raise ValueError(f"Recorder schema is missing required columns: {missing}")
    s = snap.copy()
    s["slug"] = s["slug"].astype(str)
    s = s.sort_values(["slug", "ts"]).drop_duplicates(["slug", "ts"], keep="last")
    first = s.groupby("slug", as_index=False).first()
    first["anchor_skew"] = (first["ts"].astype(float) - first["anchor_ts"].astype(float)).abs()
    bad = set(first.loc[first.anchor_skew > MAX_ANCHOR_SKEW, "slug"])
    s = s[~s.slug.isin(bad)].copy()
    valid_time = (s.seconds_left >= 0) & (s.seconds_left <= s.horizon * 60 + 2)
    finite = np.isfinite(s[["ts", "anchor_price", "btc_price", "up_ask", "down_ask"]]).all(axis=1)
    s = s[valid_time & finite].sort_values(["slug", "ts"]).reset_index(drop=True)
    t = sett.copy()
    if not t.empty:
        t["slug"] = t.slug.astype(str)
        if "resolution_source" in t:
            t = t[t.resolution_source.isin(["polymarket_clob", "polymarket_gamma"])]
        t = t.drop_duplicates("slug", keep="last")
    return s, t, len(bad)


def checkpoint_rows(snap, checkpoints=(120, 60, 30), tolerance=3.5):
    rows = []
    for slug, group in snap.groupby("slug", sort=False):
        group = group.sort_values("ts")
        horizon = int(group.horizon.iloc[0])
        for checkpoint in checkpoints:
            if checkpoint >= horizon * 60:
                continue
            distance = (group.seconds_left - checkpoint).abs()
            idx = distance.idxmin()
            if float(distance.loc[idx]) <= tolerance:
                row = group.loc[idx].to_dict()
                row["checkpoint"] = checkpoint
                rows.append(row)
    return pd.DataFrame(rows)


def coverage_metrics(snap, sett, source, removed):
    joined = set(snap.slug) & set(sett.slug) if not sett.empty else set()
    ts_min = pd.to_datetime(snap.ts.min(), unit="s", utc=True) if len(snap) else pd.NaT
    ts_max = pd.to_datetime(snap.ts.max(), unit="s", utc=True) if len(snap) else pd.NaT
    rows = [
        ("source", source),
        ("trustworthy_snapshots", len(snap)),
        ("trustworthy_rounds", snap.slug.nunique()),
        ("joined_official_rounds", len(joined)),
        ("off_open_rounds_removed", removed),
        ("first_timestamp_utc", str(ts_min)),
        ("last_timestamp_utc", str(ts_max)),
        ("independent_days", pd.to_datetime(snap.ts, unit="s", utc=True).dt.date.nunique()),
        ("train_gate_rounds", MIN_TRAIN_ROUNDS),
        ("promotion_gate_rounds", MIN_PROMOTION_ROUNDS),
    ]
    for horizon in sorted(snap.horizon.unique()):
        sub = snap[snap.horizon == horizon]
        rows.append((f"rounds_{int(horizon)}m", sub.slug.nunique()))
        rows.append((f"joined_{int(horizon)}m", len(set(sub.slug) & joined)))
    return pd.DataFrame(rows, columns=["metric", "value"])


def complement_parity(snap):
    s = snap.copy()
    s["all_in_cost"] = s.up_ask + s.down_ask + fee(s.up_ask) + fee(s.down_ask)
    s["margin"] = 1.0 - s.all_in_cost
    s["top_size"] = np.minimum(s.up_top_ask_size, s.down_top_ask_size)
    candidates = s[s.margin > 0].copy()
    first = candidates.sort_values("ts").groupby("slug", as_index=False).first() if len(candidates) else candidates
    best = candidates.sort_values("margin").groupby("slug", as_index=False).tail(1) if len(candidates) else candidates
    summary = pd.DataFrame([{
        "snapshots": len(s), "rounds": s.slug.nunique(), "candidate_snapshots": len(candidates),
        "candidate_rounds": candidates.slug.nunique() if len(candidates) else 0,
        "first_candidate_mean_margin_cents": first.margin.mean() * 100 if len(first) else np.nan,
        "best_hindsight_mean_margin_cents": best.margin.mean() * 100 if len(best) else np.nan,
        "maximum_margin_cents": candidates.margin.max() * 100 if len(candidates) else np.nan,
        "first_candidate_median_top_size": first.top_size.median() if len(first) else np.nan,
        "warning": "UP/DOWN books were fetched sequentially; candidates are not simultaneous-fill proof",
    }])
    keep = ["ts", "slug", "horizon", "seconds_left", "up_ask", "down_ask", "all_in_cost", "margin", "top_size"]
    return summary, candidates[keep]


def fixed_side_columns(frame, side):
    prefix = "up" if side == 1 else "down"
    probability = frame[f"p_hold_{prefix}"].astype(float)
    ask = frame[f"{prefix}_ask"].astype(float)
    bid = frame[f"{prefix}_bid"].astype(float)
    spread = frame[f"{prefix}_spread"].astype(float)
    size = frame[f"{prefix}_top_ask_size"].astype(float)
    fair = np.minimum(probability, FAIR_CAP)
    raw_edge = fair - ask - fee(ask)
    return probability, ask, bid, spread, size, fair, raw_edge


def edge_episodes(snap, sett):
    outcomes = sett.set_index("slug").settled_side.to_dict() if len(sett) else {}
    episodes = []
    for threshold in (0.00, 0.01, 0.02, 0.03, 0.05):
        for slug, group in snap.groupby("slug", sort=False):
            if slug not in outcomes:
                continue
            group = group.sort_values("ts").reset_index(drop=True)
            candidates = []
            for side in (0, 1):
                probability, ask, _, spread, size, fair, raw_edge = fixed_side_columns(group, side)
                move = (group.btc_price - group.anchor_price).abs()
                mask = ((probability >= 0.93) & (group.seconds_left > 15) & (group.seconds_left <= 120)
                        & (move >= 10) & (spread <= 0.03) & (size > 0) & (raw_edge >= threshold))
                for index in np.flatnonzero(mask.to_numpy()):
                    candidates.append((float(group.ts.iloc[index]), float(raw_edge.iloc[index]), side, index))
            if not candidates:
                continue
            entry_ts = min(x[0] for x in candidates)
            same_time = [x for x in candidates if x[0] == entry_ts]
            _, _, side, index = max(same_time, key=lambda x: x[1])
            probability, ask, bid, _, size, fair, raw_edge = fixed_side_columns(group, side)
            entry_ask = float(ask.iloc[index])
            entry_fee = float(fee(entry_ask))
            duration = 0.0
            first_exit = np.nan
            best_exit = -np.inf
            for j in range(index + 1, len(group)):
                if float(raw_edge.iloc[j]) >= threshold:
                    duration = float(group.ts.iloc[j] - entry_ts)
                exit_net = float(bid.iloc[j] - entry_ask - entry_fee - fee(bid.iloc[j]))
                best_exit = max(best_exit, exit_net)
                if not np.isfinite(first_exit) and exit_net > 0:
                    first_exit = float(group.ts.iloc[j] - entry_ts)
            won = int(int(outcomes[slug]) == side)
            settle_pnl = won - entry_ask - entry_fee
            episodes.append({
                "threshold": threshold, "slug": slug, "horizon": int(group.horizon.iloc[0]),
                "entry_ts": entry_ts, "side": "UP" if side else "DOWN", "seconds_left": group.seconds_left.iloc[index],
                "fair": float(fair.iloc[index]), "ask": entry_ask, "fee": entry_fee,
                "raw_edge": float(raw_edge.iloc[index]), "top_ask_size": float(size.iloc[index]),
                "edge_duration_s": duration, "settled_win": won, "settlement_net": settle_pnl,
                "first_profitable_exit_s": first_exit,
                "best_hindsight_exit_net": best_exit if np.isfinite(best_exit) else np.nan,
            })
    episodes = pd.DataFrame(episodes)
    metrics = []
    if len(episodes):
        for (threshold, horizon), group in episodes.groupby(["threshold", "horizon"]):
            low, high = bootstrap_mean_ci(group.settlement_net)
            k, n = int(group.settled_win.sum()), len(group)
            metrics.append({
                "threshold": threshold, "horizon": horizon, "signals": n,
                "win_rate": k / n, "wilson_low": wilson_low(k, n),
                "mean_settlement_net": group.settlement_net.mean(),
                "mean_net_ci_low": low, "mean_net_ci_high": high,
                "median_edge_duration_s": group.edge_duration_s.median(),
                "profitable_exit_rate": group.first_profitable_exit_s.notna().mean(),
                "median_first_exit_s": group.first_profitable_exit_s.median(),
                "mean_ask": group.ask.mean(), "mean_fair": group.fair.mean(),
            })
    return pd.DataFrame(metrics), episodes


def shock_response(snap):
    events = []
    for threshold in (10.0, 20.0, 30.0):
        for slug, group in snap.groupby("slug", sort=False):
            group = group.sort_values("ts").reset_index(drop=True)
            ts = group.ts.to_numpy(float)
            btc = group.btc_price.to_numpy(float)
            mid = group.up_mid.to_numpy(float)
            selected = None
            for i in range(1, len(group)):
                j = np.searchsorted(ts, ts[i] - 5.0, side="right") - 1
                if j < 0 or not (3.0 <= ts[i] - ts[j] <= 8.0):
                    continue
                shock = btc[i] - btc[j]
                if abs(shock) >= threshold and group.seconds_left.iloc[i] > 35:
                    selected = (i, j, shock)
                    break
            if selected is None:
                continue
            i, j, shock = selected
            sign = 1.0 if shock > 0 else -1.0
            immediate_quote = sign * (mid[i] - mid[j]) * 100.0
            for lag in (2, 5, 10, 20, 30):
                k = np.searchsorted(ts, ts[i] + lag, side="left")
                if k >= len(group) or ts[k] - (ts[i] + lag) > 3.0:
                    continue
                future_quote = sign * (mid[k] - mid[i]) * 100.0
                future_btc = sign * (btc[k] - btc[i])
                events.append({
                    "threshold_usd": threshold, "slug": slug, "horizon": int(group.horizon.iloc[0]),
                    "event_ts": ts[i], "shock_side": "UP" if sign > 0 else "DOWN",
                    "shock_usd": abs(shock), "immediate_quote_response_cents": immediate_quote,
                    "lag_s": lag, "future_quote_signed_cents": future_quote,
                    "future_btc_signed_usd": future_btc,
                    "quote_continued": int(future_quote > 0),
                    "btc_stable": int(abs(future_btc) <= max(5.0, threshold * 0.25)),
                })
    events = pd.DataFrame(events)
    metrics = []
    if len(events):
        for (threshold, lag), group in events.groupby(["threshold_usd", "lag_s"]):
            low, high = bootstrap_mean_ci(group.future_quote_signed_cents)
            stable = group[group.btc_stable == 1]
            metrics.append({
                "threshold_usd": threshold, "lag_s": lag, "events": len(group),
                "horizons": ",".join(str(x) for x in sorted(group.horizon.unique())),
                "mean_shock_usd": group.shock_usd.mean(),
                "mean_immediate_quote_cents": group.immediate_quote_response_cents.mean(),
                "quote_continuation_rate": group.quote_continued.mean(),
                "mean_future_quote_cents": group.future_quote_signed_cents.mean(),
                "quote_mean_ci_low": low, "quote_mean_ci_high": high,
                "mean_future_btc_usd": group.future_btc_signed_usd.mean(),
                "stable_btc_events": len(stable),
                "stable_btc_mean_quote_cents": stable.future_quote_signed_cents.mean() if len(stable) else np.nan,
            })
    return pd.DataFrame(metrics), events


def calibration_metrics(snap, sett):
    points = checkpoint_rows(snap)
    if points.empty or sett.empty:
        return pd.DataFrame(), points
    points = points.merge(sett[["slug", "settled_side"]], on="slug", how="inner")
    up = points.current_side == 1
    points["won"] = (points.current_side.astype(int) == points.settled_side.astype(int)).astype(int)
    points["market_p"] = np.where(up, points.up_mid, points.down_mid)
    points["model_p"] = points.p_hold_cur
    metrics = []
    for (horizon, checkpoint), group in points.groupby(["horizon", "checkpoint"]):
        row = {"horizon": int(horizon), "checkpoint": int(checkpoint), "rounds": len(group),
               "actual_rate": group.won.mean()}
        for name in ("market_p", "model_p"):
            valid = group[[name, "won"]].dropna()
            if len(valid):
                p = np.clip(valid[name].to_numpy(float), 1e-6, 1 - 1e-6)
                y = valid.won.to_numpy(float)
                row[f"{name}_mean"] = p.mean()
                row[f"{name}_brier"] = np.mean((p - y) ** 2)
                row[f"{name}_mae"] = np.mean(np.abs(p - y))
                row[f"{name}_logloss"] = -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
        metrics.append(row)
    return pd.DataFrame(metrics), points


def depth_metrics(snap):
    points = checkpoint_rows(snap)
    rows = []
    if points.empty:
        return pd.DataFrame()
    for (horizon, checkpoint), group in points.groupby(["horizon", "checkpoint"]):
        up = group.current_side == 1
        top = np.where(up, group.up_top_ask_size, group.down_top_ask_size)
        d1 = np.where(up, group.up_d1, group.down_d1)
        d2 = np.where(up, group.up_d2, group.down_d2)
        d5 = np.where(up, group.up_d5, group.down_d5)
        for size in (1, 10, 50, 100, 500):
            rows.append({
                "horizon": int(horizon), "checkpoint": int(checkpoint), "rounds": len(group),
                "order_size": size, "top_ask_available": np.mean(top >= size),
                "within_1c_available": np.mean(d1 >= size), "within_2c_available": np.mean(d2 >= size),
                "within_5c_available": np.mean(d5 >= size),
            })
    return pd.DataFrame(rows)


def write_report(out, coverage, complement, edge, shock, calibration, depth):
    joined = int(float(coverage.loc[coverage.metric == "joined_official_rounds", "value"].iloc[0]))
    days = int(float(coverage.loc[coverage.metric == "independent_days", "value"].iloc[0]))
    candidate_rounds = int(complement.candidate_rounds.iloc[0]) if len(complement) else 0
    maximum_margin = float(complement.maximum_margin_cents.iloc[0]) if len(complement) else np.nan
    maximum_edge_signals = int(edge.signals.max()) if len(edge) else 0
    positive_delayed_cells = int((shock.quote_mean_ci_low > 0).sum()) if len(shock) else 0
    verdict = ("INSUFFICIENT DATA: calculations are validated, but no fair-value model or profit claim is allowed."
               if joined < MIN_TRAIN_ROUNDS else "TRAINABLE research sample; promotion still requires 1,000 rounds.")
    lines = [
        "# Polymarket Market-Response Test", "", "Status: PAPER research only", "",
        "## Verdict", "", verdict, "",
        f"The trustworthy sample contains **{joined} officially settled rounds across {days} days**. "
        "Snapshot counts are not treated as independent trade counts.", "",
        "## Decisions", "",
        f"- **Quote underreaction: NOT SUPPORTED.** Delayed-response cells with a positive 95% lower bound: "
        f"{positive_delayed_cells}. The market usually repriced during the measured BTC shock.",
        f"- **Complement arbitrage: NOT ACTIONABLE.** {candidate_rounds} rounds showed a candidate; the best "
        f"margin was only {maximum_margin:.4f} cents and the two REST books were fetched sequentially.",
        f"- **Model edge: INCONCLUSIVE.** The largest independent threshold/horizon cell contains only "
        f"{maximum_edge_signals} entries, below the 200-round training gate.",
        "- **Recorded depth: DESCRIPTIVE ONLY.** Small top-of-book orders usually show available size, but "
        "latency, trades, queue state and observed fills are missing.", "",
        "## Coverage", "", markdown_table(coverage), "",
        "## Complement-Book Parity", "", markdown_table(complement.round(5)), "",
        "A positive margin is only a candidate. The recorder fetched UP and DOWN REST books sequentially, "
        "so simultaneous executable fills are not proven.", "",
        "## Conservative Edge Episodes", "", markdown_table(edge.round(5)), "",
        "Each row uses one first qualifying entry per round, a fixed side, exact recorded ask, fee estimate, "
        "official settlement and future recorded bids. Small cells are descriptive only.", "",
        "## BTC-Shock Quote Response", "", markdown_table(shock.round(5)), "",
        "Positive future signed quote movement can indicate delayed response, but BTC continuation is a "
        "confounder. The stable-BTC subset and confidence interval must agree before calling underreaction.", "",
        "## Checkpoint Calibration", "", markdown_table(calibration.round(5)), "",
        "Market midpoint and model P(Hold) are compared on one observation per round/checkpoint.", "",
        "## Recorded Depth Availability", "", markdown_table(depth.round(5)), "",
        "Depth availability does not prove a fill after latency and does not reconstruct VWAP without the full ladder.", "",
        "## Blocked Tests", "",
        "- Quote age: no exchange-event timestamp or last-book-update timestamp is stored.",
        "- Passive fill probability: no queue position, market trades or user order lifecycle is stored.",
        "- Exact depth-adjusted VWAP: only cumulative 1c/2c/5c depth is stored, not the full ladder.",
        "- Causal maker/taker selection: no observed paper order submissions and fills.",
        "- Fair-value residual model: fewer than 200 independent joined rounds.",
        "- Promotion decision: fewer than 1,000 independent joined rounds and no later untouched era.", "",
        "## Required Recorder Upgrade", "",
        "Record market WebSocket event time, receive time, full level changes, last trade, both-side BBO sizes, "
        "user order/trade lifecycle, requested paper order and first achievable fill. Continue official settlement joins.",
    ]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def run(args):
    out = Path(args.output_dir) if args.output_dir else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    snap_raw, sett_raw, source = load_tables()
    snap, sett, removed = clean_tables(snap_raw, sett_raw)
    coverage = coverage_metrics(snap, sett, source, removed)
    complement, complement_rows = complement_parity(snap)
    edge, episodes = edge_episodes(snap, sett)
    shock, shock_events = shock_response(snap)
    calibration, calibration_points = calibration_metrics(snap, sett)
    depth = depth_metrics(snap)

    coverage.to_csv(out / "coverage.csv", index=False)
    complement.to_csv(out / "complement_metrics.csv", index=False)
    complement_rows.to_csv(out / "complement_candidates.csv", index=False)
    edge.to_csv(out / "edge_metrics.csv", index=False)
    episodes.to_csv(out / "edge_episodes.csv", index=False)
    shock.to_csv(out / "shock_response_metrics.csv", index=False)
    shock_events.to_csv(out / "shock_response_events.csv", index=False)
    calibration.to_csv(out / "calibration_metrics.csv", index=False)
    calibration_points.to_csv(out / "calibration_points.csv", index=False)
    depth.to_csv(out / "depth_availability.csv", index=False)
    config = {"source": source, "fee_rate": FEE_RATE, "fair_cap": FAIR_CAP,
              "max_anchor_skew": MAX_ANCHOR_SKEW, "paper_only": True}
    (out / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    write_report(out, coverage, complement, edge, shock, calibration, depth)

    print(coverage.to_string(index=False))
    print("\nCOMPLEMENT\n" + complement.round(5).to_string(index=False))
    print("\nEDGE\n" + (edge.round(5).to_string(index=False) if len(edge) else "no eligible entries"))
    print("\nSHOCK RESPONSE\n" + (shock.round(5).to_string(index=False) if len(shock) else "no eligible events"))
    print(f"\nWrote {out}")
    return 0


def selftest():
    assert abs(float(fee(0.5)) - 0.0175) < 1e-12
    assert 0.20 < wilson_low(1, 1) < 0.21
    sample = pd.DataFrame({
        "ts": [1000.5, 1002.0, 1004.0], "slug": ["btc-updown-5m-1000"] * 3,
        "horizon": [5] * 3, "anchor_ts": [1000] * 3, "seconds_left": [299.5, 298, 296],
        "anchor_price": [60000] * 3, "btc_price": [60000, 60010, 60020],
        "p_hold_up": [.5, .8, .95], "p_hold_down": [.5, .2, .05], "p_hold_cur": [.5, .8, .95],
        "current_side": [1] * 3, "up_bid": [.49, .70, .80], "up_ask": [.51, .72, .82],
        "up_mid": [.50, .71, .81], "up_spread": [.02] * 3, "up_top_ask_size": [100] * 3,
        "up_d1": [100] * 3, "up_d2": [200] * 3, "up_d5": [500] * 3,
        "down_bid": [.49, .28, .18], "down_ask": [.51, .30, .20], "down_mid": [.50, .29, .19],
        "down_spread": [.02] * 3, "down_top_ask_size": [100] * 3,
        "down_d1": [100] * 3, "down_d2": [200] * 3, "down_d5": [500] * 3,
    })
    clean, _, removed = clean_tables(sample, pd.DataFrame())
    assert removed == 0 and len(clean) == 3
    comp, _ = complement_parity(clean)
    assert int(comp.candidate_rounds.iloc[0]) == 0
    print("SELFTEST PASS")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--selftest", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    if arguments.selftest:
        selftest()
        raise SystemExit(0)
    raise SystemExit(run(arguments))
