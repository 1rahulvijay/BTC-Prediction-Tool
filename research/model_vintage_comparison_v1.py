"""MODEL_VINTAGE_COMPARISON_V1 - the deployed Oracle against today's repository, same rows.

THE QUESTION
    The 2026-07-04 Oracle has been making live forward predictions since 2026-07-06. It is the
    only thing in this repository with genuine forward exposure, so every challenger has to
    beat IT rather than beat a backtest. This replays both over the identical checkpoint
    population and asks which number was better.

THE ASYMMETRY, MEASURED BEFORE ANY RESULT WAS SEEN
    The challenger is not a fair competitor here, and pretending otherwise would manufacture
    exactly the kind of "the new model is better" claim this repository keeps having to retract.

        ORACLE      recorded p_hold_cur, model_version 2026-06-21-keeper-dual-perhorizon-iso.
                    Genuinely out of sample: it predicted these rounds before they happened.

        CHALLENGER  data/saved_models/persistence_model.pkl, replayed. Its training set
                    (data/persistence_dataset.parquet) spans 2023-01-16 to 2026-07-30 with
                    refit_on_all=True, including 335,060 rows from July 2026 - the very window
                    scored below. It is IN SAMPLE. It has seen the answers.

    So the reading rule is asymmetric and is declared here, in advance:

        a CHALLENGER LOSS is informative   - it lost on rounds it had memorised
        a CHALLENGER WIN  proves nothing   - that is what memorisation looks like

    This is not a hedge added after the fact. It follows from the training span, which is a
    property of the file on disk, and it is why the verdict below never promotes anything.

WHY THE CHALLENGER IS ALSO HANDICAPPED
    The Oracle's live p_hold could use the 11-feature `keeper` variant when the volatility
    keepers were present that tick. Those inputs were never recorded in pm_round_snapshots, so
    the replay can only use the 5-feature base model. If the challenger loses, some of that gap
    may be the missing features rather than the model. Stated because it cuts against the
    conclusion, not for it.

EVIDENCE CLASS
    Every row is LIVE_RESEARCH or RETROSPECTIVE_VALIDATION. There are ZERO FORWARD_UNTOUCHED
    rows, because the round recorders have been dark since 2026-07-25. This study can therefore
    ELIMINATE a candidate. It can never promote one.

DECLARED BEFORE RESULTS
    G1  all arms scored on the IDENTICAL row population, no per-arm filtering
    G2  the entry rule is the same for every arm: p > ask + fee + ENTRY_MARGIN
    G3  fees from the canonical polymarket_fee module, never a constant retyped here
    G4  a matched-count RANDOM arm, so "it traded less" cannot read as skill
    G5  verdict is reported per evidence class and never aggregated across them

    python research/model_vintage_comparison_v1.py
    python research/model_vintage_comparison_v1.py --selftest
"""
from __future__ import annotations

RESEARCH_STATUS = "VALID_DIAGNOSTIC"
CAPITAL_AUTHORITY = False

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "backend"))

# day_block_lcb and expected_calibration_error are arithmetic, not claims. The STUDY that used
# to live around them is retracted; these estimators are not, and reimplementing them here
# would let the two copies drift apart while both looked authoritative.
from phold_auc_and_expectancy import (  # noqa: E402
    day_block_lcb, expected_calibration_error, roc_auc,
)
from polymarket_fee import polymarket_taker_fee_per_share  # noqa: E402

CHECKPOINTS = ROOT / "data" / "research" / "causal_checkpoints_v1.parquet"
LABELS = ROOT / "data" / "research" / "causal_checkpoint_labels_v1.parquet"
CHALLENGER = ROOT / "data" / "saved_models" / "persistence_model.pkl"

ENTRY_MARGIN = 0.02
MIN_TRADES = 40
RNG = np.random.default_rng(20260802)


def load_population():
    """Checkpoints joined to labels: eligible, settled, with an executable quote."""
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        return con.execute(f"""
            SELECT k.slug, k.checkpoint_s, k.horizon, k.snapshot_ts, k.evidence_class,
                   k.current_side, k.distance_pct, k.vol_60s_pct, k.p_hold_cur,
                   CASE WHEN k.current_side = 1 THEN k.up_ask ELSE k.down_ask END AS ask,
                   CASE WHEN k.current_side = 1 THEN k.up_top_ask_size
                        ELSE k.down_top_ask_size END AS depth,
                   l.label_checkpoint_side_wins AS won
            FROM read_parquet('{CHECKPOINTS.as_posix()}') k
            JOIN read_parquet('{LABELS.as_posix()}') l
              ON l.slug = k.slug AND l.checkpoint_s = k.checkpoint_s
            WHERE k.eligible
              AND l.label_checkpoint_side_wins IS NOT NULL
              AND k.current_side IS NOT NULL
              AND k.vol_60s_pct > 0
              AND CASE WHEN k.current_side = 1 THEN k.up_ask ELSE k.down_ask END
                  BETWEEN 0.01 AND 0.99
            ORDER BY k.snapshot_ts, k.checkpoint_s
        """).df()
    finally:
        con.close()


def challenger_probabilities(frame):
    """Replay the current model on features rebuilt exactly as price_to_beat builds them."""
    import joblib

    bundle = joblib.load(CHALLENGER)
    values = {
        "abs_distance_pct": frame["distance_pct"].abs().to_numpy(float),
        "seconds_left": frame["checkpoint_s"].to_numpy(float),
        "vol_60s_pct": frame["vol_60s_pct"].to_numpy(float),
        "horizon": frame["horizon"].to_numpy(float),
    }
    # Same expression as the serving path, including the epsilon. A different epsilon would be
    # a different feature, and the model would be scored on inputs it never saw.
    values["dist_vol_ratio"] = values["abs_distance_pct"] / (values["vol_60s_pct"] + 1e-6)
    matrix = np.column_stack([values[name] for name in bundle["features"]])
    raw = bundle["clf"].predict_proba(matrix)[:, 1]
    return np.clip(bundle["iso"].predict(raw), 1e-6, 1 - 1e-6), bundle.get("version")


def arm_metrics(name, probability, won, ask, fee, days, *, note=""):
    """Discrimination, calibration and the economics of acting on this number."""
    selected = (probability - ask - fee) > ENTRY_MARGIN
    net = np.where(won == 1, 1.0 - ask - fee, -(ask + fee))
    row = {
        "arm": name,
        "n": int(len(probability)),
        "brier": float(np.mean((probability - won) ** 2)),
        "log_loss": float(-np.mean(won * np.log(probability)
                                   + (1 - won) * np.log(1.0 - probability))),
        "ece": float(expected_calibration_error(probability, won)),
        # roc_auc(scores, labels) - scores FIRST. Swapped, it returns nan for every arm and the
        # table prints "-" everywhere, which reads as "not applicable" rather than "broken".
        "auc": float(roc_auc(probability, won)),
        "trades": int(selected.sum()),
        "note": note,
    }
    if selected.sum() >= MIN_TRADES:
        row["net_per_$1"] = float(net[selected].mean())
        row["net_lcb"] = day_block_lcb(net[selected], days[selected])
    else:
        row["net_per_$1"] = float("nan")
        row["net_lcb"] = float("nan")
    return row


def run(frame) -> dict:
    won = frame["won"].to_numpy(float)
    ask = frame["ask"].to_numpy(float)
    fee = np.array([polymarket_taker_fee_per_share(value) for value in ask])
    days = (frame["snapshot_ts"].to_numpy(float) // 86400).astype(np.int64)

    oracle = np.clip(frame["p_hold_cur"].to_numpy(float), 1e-6, 1 - 1e-6)
    challenger, challenger_version = challenger_probabilities(frame)
    # The market's own probability for the side being considered IS the ask.
    market = np.clip(ask, 1e-6, 1 - 1e-6)

    arms = [
        # Takes 0 trades BY CONSTRUCTION: p equals ask, so p > ask + fee + margin is never
        # true. It is the calibration bar, not a trading arm, and the zero is not a failure.
        arm_metrics("MARKET", market, won, ask, fee, days,
                    note="the ask itself; calibration bar, 0 trades by construction"),
        arm_metrics("ORACLE", oracle, won, ask, fee, days,
                    note="recorded live, genuinely out of sample"),
        arm_metrics("CHALLENGER", challenger, won, ask, fee, days,
                    note=f"{challenger_version}; IN SAMPLE - a win proves nothing"),
    ]
    # Matched-count random: same number of trades as the Oracle, chosen without information.
    target = max(arms[1]["trades"], MIN_TRADES)
    if target <= len(frame):
        picked = np.zeros(len(frame), dtype=bool)
        picked[RNG.choice(len(frame), size=min(target, len(frame)), replace=False)] = True
        net = np.where(won == 1, 1.0 - ask - fee, -(ask + fee))
        arms.append({
            "arm": "RANDOM", "n": int(len(frame)), "brier": float("nan"),
            "log_loss": float("nan"), "ece": float("nan"), "auc": 0.5,
            "trades": int(picked.sum()),
            "net_per_$1": float(net[picked].mean()),
            "net_lcb": day_block_lcb(net[picked], days[picked]),
            "note": "matched on trade COUNT, so trading less cannot read as skill",
        })

    entered_oracle = (oracle - ask - fee) > ENTRY_MARGIN
    entered_challenger = (challenger - ask - fee) > ENTRY_MARGIN
    disagree = entered_oracle != entered_challenger
    oracle_only = entered_oracle & ~entered_challenger
    challenger_only = ~entered_oracle & entered_challenger

    def rate_and_se(mask):
        """Win rate with its binomial standard error - 381 rows is not 3,000."""
        if not mask.sum():
            return None, None
        outcomes = won[mask]
        rate = float(outcomes.mean())
        return rate, float(np.sqrt(max(rate * (1.0 - rate), 1e-12) / len(outcomes)))

    oracle_rate, oracle_se = rate_and_se(oracle_only)
    challenger_rate, challenger_se = rate_and_se(challenger_only)
    disagreement = {
        "rows": int(disagree.sum()),
        "oracle_enters_challenger_waits": int(oracle_only.sum()),
        "challenger_enters_oracle_waits": int(challenger_only.sum()),
        "oracle_only_win_rate": oracle_rate, "oracle_only_se": oracle_se,
        "challenger_only_win_rate": challenger_rate, "challenger_only_se": challenger_se,
        "breakeven_win_rate": float(np.mean((ask + fee)[disagree])) if disagree.sum() else None,
    }
    return {"arms": arms, "disagreement": disagreement,
            "challenger_version": challenger_version}


def selftest() -> int:
    """The estimators must separate a good probability from a useless one."""
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    rng = np.random.default_rng(7)
    won = rng.integers(0, 2, 4000).astype(float)
    perfect = np.clip(won * 0.98 + 0.01, 1e-6, 1 - 1e-6)
    useless = np.full(4000, 0.5)
    ask = np.full(4000, 0.5)
    fee = np.array([polymarket_taker_fee_per_share(v) for v in ask])
    days = np.repeat(np.arange(20), 200)

    good = arm_metrics("GOOD", perfect, won, ask, fee, days)
    bad = arm_metrics("BAD", useless, won, ask, fee, days)
    check(good["brier"] < bad["brier"] and good["log_loss"] < bad["log_loss"],
          "a near-perfect probability beats a constant 0.5 on Brier and log loss")
    check(good["auc"] > 0.99 and abs(bad["auc"] - 0.5) < 0.05,
          "AUC separates the informative arm from the uninformative one")
    check(bad["trades"] == 0,
          "a constant 0.5 never clears ask+fee+margin, so it takes no trades")
    check(np.isnan(bad["net_per_$1"]),
          "an arm below MIN_TRADES reports NOT MEASURED rather than a number from 3 rows")
    check(good["trades"] > 0 and good["net_per_$1"] > 0,
          "an arm that knows the answer does clear the entry rule and earns")
    print(f"\nVINTAGE COMPARISON SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    print("=" * 104)
    print("MODEL VINTAGE COMPARISON - the deployed Oracle against today's repository")
    print("=" * 104)
    if args.selftest:
        return selftest()

    for path in (CHECKPOINTS, LABELS, CHALLENGER):
        if not path.is_file():
            print(f"  BLOCKED: {path.relative_to(ROOT).as_posix()} is missing.")
            return 0

    frame = load_population()
    if frame.empty:
        print("  BLOCKED: no eligible settled checkpoints.")
        return 0

    print(f"  population : {len(frame):,} eligible settled checkpoints over "
          f"{frame['slug'].nunique():,} rounds")
    print(f"  entry rule : p > ask + fee + {ENTRY_MARGIN} (identical for every arm)")
    print()
    print("  THE CHALLENGER IS IN SAMPLE. Its training set spans 2023-01-16 to 2026-07-30")
    print("  with refit_on_all=True and 335,060 July 2026 rows - the window scored below.")
    print("  A challenger LOSS is informative. A challenger WIN proves nothing.")

    for evidence_class, subset in [("ALL", frame)] + [
            (name, frame[frame["evidence_class"] == name])
            for name in sorted(frame["evidence_class"].unique())]:
        if len(subset) < MIN_TRADES:
            continue
        result = run(subset.reset_index(drop=True))
        print()
        print(f"  --- {evidence_class}  ({len(subset):,} rows) " + "-" * (58 - len(evidence_class)))
        print(f"{'arm':<12}{'brier':>9}{'logloss':>9}{'ECE':>8}{'AUC':>8}"
              f"{'trades':>8}{'net/$1':>10}{'day LCB':>10}  note")
        for row in result["arms"]:
            def fmt(value, spec):
                # Pad the dash to the same width as a number, or NOT MEASURED cells shove the
                # rest of the row left and the table stops lining up under its own header.
                if value is None or (isinstance(value, float) and np.isnan(value)):
                    width = spec.lstrip(">").split(".")[0]
                    return format("-", f">{width}") if width.isdigit() else "-"
                return format(value, spec)
            print(f"{row['arm']:<12}{fmt(row['brier'], '>9.4f')}{fmt(row['log_loss'], '>9.4f')}"
                  f"{fmt(row['ece'], '>8.4f')}{fmt(row['auc'], '>8.4f')}{row['trades']:>8,}"
                  f"{fmt(row['net_per_$1'], '>10.4f')}{fmt(row['net_lcb'], '>10.4f')}  "
                  f"{row['note']}")
        if evidence_class == "ALL":
            gap = result["disagreement"]
            print()
            print(f"  DISAGREEMENT: {gap['rows']:,} rows where the two arms differ on entry")
            print(f"    oracle enters, challenger waits : "
                  f"{gap['oracle_enters_challenger_waits']:>6,}   win "
                  f"{fmt(gap['oracle_only_win_rate'], '.4f')} "
                  f"+/- {fmt(gap['oracle_only_se'], '.4f')}")
            print(f"    challenger enters, oracle waits : "
                  f"{gap['challenger_enters_oracle_waits']:>6,}   win "
                  f"{fmt(gap['challenger_only_win_rate'], '.4f')} "
                  f"+/- {fmt(gap['challenger_only_se'], '.4f')}")
            print(f"    break-even win rate on those rows: "
                  f"{fmt(gap['breakeven_win_rate'], '.4f')}")
            print("    A rate ABOVE break-even means those unique picks paid; BELOW means they")
            print("    did not. Read against the standard errors, not as exact figures.")

    print()
    print("  VERDICT: elimination-grade only. Zero FORWARD_UNTOUCHED rows exist, so nothing")
    print("  here can promote a model. Recorders have been dark since 2026-07-25.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
