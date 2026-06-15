"""
composed_decision_backtest.py — the COMBINED execution-stack proof (offline, honest).
=====================================================================================
Runs the ACTUAL production `compose_decision` ladder over 60 days of 1m bars and
reports, per tier and per execution mode, the paper EV after costs — plus the full
rejection-reason breakdown. This is the integration test for the whole decision
package: selectivity → side engine → cost gate → composer → (event exits).

Honesty rules baked in:
  • The head scores P(big_move)/P(tradable)/P(fail_fast) and the expected-move forecast
    are PURGED out-of-fold predictions (embargo = 5m), so we never score on training rows.
  • SIDE is the validated deterministic engine; direction is NEVER an ML side model.
  • Thresholds are PERCENTILES of the score (calibration-robust) — the audit showed the
    raw probabilities are uncalibrated, so we gate on rank, exactly like the side engine.
  • Realized EV uses the actual future 5m move; cost is charged per mode
    (MAKER_MAKER 0 / MAKER_TAKER 7 / TAKER_TAKER 14 bps). The cost gate uses the FORECAST
    move (no lookahead); only the realized PnL uses the actual move.

Reads `research_matrix_1m.parquet`; writes nothing the app uses. Run single-threaded.

Usage:  python backend/decision/composed_decision_backtest.py
"""
from __future__ import annotations

import os
from collections import Counter

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit

import argparse
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from decision.decision_composer import compose_decision, DecisionContext
from decision import shadow_store

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")

SEL_FEATS = ["rv_15m", "rv_30m", "log_count", "vpin_15m", "compression_ratio", "shock_magnitude"]
TRAD_FEATS = ["compression_ratio", "rv_60m", "rv_30m"]
FAIL_FEATS = ["shock_magnitude", "vpin"]
MOVE_FEATS = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio"]
EMBARGO = 5


def _purged_oof_clf(X, y, embargo=EMBARGO):
    oof = np.full(len(y), np.nan)
    for tr, te in TimeSeriesSplit(n_splits=5).split(X):
        tr = tr[tr < te[0] - embargo]
        if len(tr) < 200 or len(np.unique(y[tr])) < 2:
            continue
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(class_weight="balanced", max_iter=1000).fit(sc.transform(X[tr]), y[tr])
        oof[te] = clf.predict_proba(sc.transform(X[te]))[:, 1]
    return oof


def _purged_oof_reg(X, yv, embargo=EMBARGO):
    oof = np.full(len(yv), np.nan)
    for tr, te in TimeSeriesSplit(n_splits=5).split(X):
        tr = tr[tr < te[0] - embargo]
        if len(tr) < 200:
            continue
        sc = StandardScaler().fit(X[tr])
        rg = Ridge(alpha=1.0).fit(sc.transform(X[tr]), yv[tr])
        oof[te] = rg.predict(sc.transform(X[te]))
    return oof


def main(persist=False):
    if not os.path.exists(MATRIX):
        print(f"ERROR: {MATRIX} not found."); return
    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    need = list(set(SEL_FEATS + TRAD_FEATS + FAIL_FEATS + MOVE_FEATS +
                    ["future_abs_move_5m", "tradable_move_label", "fail_fast_label",
                     "ret_5m", "close", "perp_spot_basis_bps", "cvd_divergence", "vpin"]))
    df = df.dropna(subset=need).reset_index(drop=True)
    p75 = df["future_abs_move_5m"].quantile(0.75)
    y_big = (df["future_abs_move_5m"] > p75).astype(int).values
    y_trad = df["tradable_move_label"].fillna(0).astype(int).values
    y_fail = df["fail_fast_label"].fillna(0).astype(int).values
    move_bps_actual = (df["future_abs_move_5m"] / df["close"] * 10000.0).values

    print(f"Rows: {len(df):,}. Computing purged out-of-fold head scores...")
    p_big = _purged_oof_clf(df[SEL_FEATS].values, y_big)
    p_trad = _purged_oof_clf(df[TRAD_FEATS].values, y_trad)
    p_fail = _purged_oof_clf(df[FAIL_FEATS].values, y_fail)
    exp_move = _purged_oof_reg(df[MOVE_FEATS].values, move_bps_actual)

    m = ~(np.isnan(p_big) | np.isnan(p_trad) | np.isnan(p_fail) | np.isnan(exp_move))
    d = df[m].reset_index(drop=True)
    p_big, p_trad, p_fail, exp_move = p_big[m], p_trad[m], p_fail[m], exp_move[m]
    ret_5m_pct = (d["ret_5m"] / d["close"]).values
    basis = d["perp_spot_basis_bps"].values
    cvd = d["cvd_divergence"].values
    vpin = d["vpin"].values
    ts_ms = d["ts_ms"].astype("int64").values if "ts_ms" in d.columns else np.arange(len(d))
    close = d["close"].values

    # Percentile thresholds (calibration-robust — the audit's recommendation).
    def pct(a, q): return float(np.percentile(a[np.isfinite(a)], q))
    sel_t1, sel_t2, sel_t3 = pct(p_big, 95), pct(p_big, 99), pct(p_big, 99.75)
    trad_floor = pct(p_trad[p_big >= sel_t1], 50) if (p_big >= sel_t1).sum() else 0.5
    th = dict(basis_up=pct(basis, 95), basis_dn=pct(basis, 5),
              cvd_up=pct(cvd, 95), cvd_dn=pct(cvd, 5),
              vpin_up=pct(vpin, 95), vpin_dn=pct(vpin, 5))

    print(f"Thresholds: sel_t1={sel_t1:.3f} sel_t2={sel_t2:.3f} sel_t3={sel_t3:.3f} "
          f"trad_floor={trad_floor:.3f}")
    print("Running the production compose_decision over every bar "
          "(exec_mode=MAKER_MAKER, the only viable mode)...\n")

    actions = Counter(); reasons = Counter()
    rows = []   # (action, tier, side, realized_ret_pct)
    sig_records = []   # for the separate execution_layer.duckdb
    rej_records = []
    for i in range(len(d)):
        ctx = DecisionContext(
            p_big_move=p_big[i], p_tradable=p_trad[i], p_fail_fast=p_fail[i],
            basis_bps=basis[i], cvd_div=cvd[i], vpin=vpin[i],
            basis_up=th["basis_up"], basis_dn=th["basis_dn"],
            cvd_up=th["cvd_up"], cvd_dn=th["cvd_dn"],
            vpin_up=th["vpin_up"], vpin_dn=th["vpin_dn"],
            expected_move_bps=float(exp_move[i]), exec_mode="MAKER_MAKER",
            selectivity_floor=sel_t1, tradability_floor=trad_floor,
            maker_fill_proven=False,  # honest: live shadow has not proven fills
        )
        r = compose_decision(ctx)
        actions[r.action] += 1
        reasons[r.reason] += 1
        side = 1 if r.side == "LONG" else (-1 if r.side == "SHORT" else 0)
        if side != 0:
            rows.append((r.action, r.tier, side, ret_5m_pct[i] * side))
        if persist:
            rej_records.append({"ts_ms": int(ts_ms[i]), "horizon": 5,
                                "reason": r.reason, "tier": r.tier})
            if side != 0:  # a real trade candidate → log to shadow_signals (resolved offline)
                sig_records.append({
                    "id": f"replay_{int(ts_ms[i])}", "ts_ms": int(ts_ms[i]), "horizon": 5,
                    "p_big_move": float(p_big[i]), "p_tradable": float(p_trad[i]),
                    "p_fail_fast": float(p_fail[i]), "selected_side": r.side,
                    "side_rule": r.side_reason, "expected_move_bps": float(exp_move[i]),
                    "expected_cost_bps": float(r.expected_cost_bps),
                    "move_cost_ratio": float(r.move_cost_ratio) if r.move_cost_ratio != float("inf") else None,
                    "final_tier": r.tier, "final_action": r.action, "rejection_reason": r.reason,
                    "entry_mode": "MAKER_MAKER", "entry_price": float(close[i]),
                    "realized_pnl_bps": float(ret_5m_pct[i] * side * 10000.0), "resolved": True,
                })

    if persist:
        rdf_sig = pd.DataFrame(sig_records)
        rdf_rej = pd.DataFrame(rej_records)
        conn = shadow_store.get_conn()
        shadow_store.init_db(conn)
        shadow_store.reset(conn)
        if len(rdf_sig):
            shadow_store.bulk_write_signals(rdf_sig, conn=conn)
        shadow_store.bulk_write_rejections(rdf_rej, conn=conn)
        conn.close()
        print(f"[persist] wrote {len(rdf_sig):,} shadow_signals + {len(rdf_rej):,} rejection_events "
              f"-> {shadow_store.DB_PATH}\n")

    print("=" * 64)
    print("ACTION DISTRIBUTION (what the app would say each minute)")
    print("=" * 64)
    tot = sum(actions.values())
    for a, c in actions.most_common():
        print(f"  {a:<12} {c:>7}  ({c/tot*100:5.1f}%)")
    print("\nREJECTION / DECISION REASONS")
    for r, c in reasons.most_common():
        print(f"  {r:<28} {c:>7}  ({c/tot*100:5.1f}%)")

    if not rows:
        print("\nNo sided signals produced."); return
    rdf = pd.DataFrame(rows, columns=["action", "tier", "side", "gross_ret_pct"])

    print("\n" + "=" * 64)
    print("PAPER EV by tier x execution mode (after cost, on REALIZED 5m move)")
    print("=" * 64)
    print(f"{'tier-set':<26}{'N':>7}{'win%':>7}{'gross':>9}{'maker0':>9}{'mtaker7':>9}{'taker14':>9}")
    def show(label, sub):
        if len(sub) == 0:
            print(f"{label:<26}{0:>7}"); return
        g = sub["gross_ret_pct"].values * 10000  # bps
        gross = g.mean()
        win = (g > 0).mean() * 100
        net0 = (g - 0.0).mean()
        net7 = (g - 7.0).mean()
        net14 = (g - 14.0).mean()
        print(f"{label:<26}{len(sub):>7}{win:>6.1f}%{gross:>+9.2f}{net0:>+9.2f}{net7:>+9.2f}{net14:>+9.2f}")

    # Actionable live tiers (under maker-unproven, the live cap is T2_SHADOW/T1/WATCH)
    show("WATCH+sided (all)", rdf)
    show("T1 (engine)", rdf[rdf.tier == "T1"])
    show("T2 (engine)", rdf[rdf.tier == "T2"])
    show("T3 (engine)", rdf[rdf.tier == "T3"])
    show("action=T2_SHADOW", rdf[rdf.action == "T2_SHADOW"])

    print("\nHonest read: 'maker0' is the only economically viable column (Phase 16). If even")
    print("the top tier is <=0 at maker0, the edge does not survive — keep it WATCH/shadow.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--persist", action="store_true",
                    help="write decisions + resolutions into the separate execution_layer.duckdb")
    main(persist=ap.parse_args().persist)
