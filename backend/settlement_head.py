"""A binary endpoint head. NOT the Polymarket settlement model, and it must not be called one.

WHY THIS EXISTS
    `TRAINING_CONTRACT` is `first_touch_triple_barrier_v1` - "which barrier is touched FIRST".
    Polymarket resolves on where price ENDS relative to the anchor. Measured on random walks the
    two disagree 24.9% of the time, so a first-touch probability cannot price a settlement
    question, and `target_contract.assert_admissible` refuses it.

    That refusal was correct and left the settlement lane with NO head at all:
    `build_sequences(return_settlement_labels=True)` could emit the labels, but nothing
    requested them and nothing consumed them. This is the consumer.

WHAT IT IS NOT
    It is not the ensemble. One calibrated gradient-boosted classifier per horizon, stamped
    with `ROLLING_EXCHANGE_RETURN_SIGN_V1`.

    It is NOT the Polymarket settlement model, for two independent reasons:

      WRONG PRICE SERIES   labels come from exchange closes; the venue settles on a Chainlink
                           stream.
      WRONG REFERENCE      labels compare the horizon end to the DECISION-time price; the venue
                           compares the round end to the round's fixed ANCHOR. Measured, that
                           inverts the outcome on up to ~35% of rounds late in a round - worst
                           exactly where seconds-left information is worth most.

    So `POLYMARKET_SETTLEMENT_EV` REFUSES this head, deliberately. Round-aligned labels built
    from the venue's own source are required before any artifact may price that market. This
    docstring said the opposite for a day, and a stale description in this repository has been
    read as verified architecture more than once - hence `test_target_declaration_consistency`.

AUTHORITY
    None. It is registered with `may_price=False, may_rank=False, may_size=False`, and its
    metrics are recorded so a later, separately preregistered study can decide whether it beats
    the Polymarket price. A head that exists is not a head that has earned anything.

    python backend/settlement_head.py --selftest
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import target_contract as tc                                      # noqa: E402

#: The contract this head answers. Stamped on the artifact and checked on load: a bundle
#: claiming a different target is refused rather than silently used for the wrong question.
#: This proxy resolves on the verified comparator with no neutral band. Training it on the
#: three-class endpoint labels meant ~68%
#: of real payouts were labelled NEUTRAL, so the head answered a question the venue never asks.
TARGET_CONTRACT = tc.ROLLING_EXCHANGE_RETURN_SIGN_V1

#: Class layout per contract. The head is fitted per contract rather than hardcoding three
#: columns, because the number of outcomes IS the contract - a binary market has no NEUTRAL
#: column to widen into, and inventing one is how the band came back.
CONTRACT_CLASSES = {
    tc.ENDPOINT_SETTLEMENT_V1: tc.CLASS_ORDER,          # (DOWN, NEUTRAL, UP)
    tc.ROLLING_EXCHANGE_RETURN_SIGN_V1: tc.BINARY_CLASS_ORDER,  # (DOWN, UP)
    tc.POLYMARKET_BINARY_SETTLEMENT_V1: tc.BINARY_CLASS_ORDER,    # reserved; no labels yet
}

ARTIFACT_FILENAME = "settlement_head.pkl"
REGISTRY_NAME = "settlement"

#: Minimum usable rows per horizon. Below this the head is NOT written - an artifact fitted on
#: a handful of rows is worse than no artifact, because its existence implies evidence.
MIN_TRAIN_ROWS = 500

#: Column order matches the rest of the model layer: [DOWN, NEUTRAL, UP].
DOWN, NEUTRAL, UP = 0, 1, 2


class SettlementHeadUnavailable(RuntimeError):
    """No head could be fitted. Callers must abstain, never fall back to a path probability."""


def _labels_from_onehot(y_onehot: np.ndarray) -> np.ndarray:
    return np.argmax(np.asarray(y_onehot), axis=1).astype(int)


def brier_multiclass(probabilities: np.ndarray, labels: np.ndarray) -> float:
    """Mean squared error against the one-hot truth, averaged over classes."""
    truth = np.zeros_like(probabilities)
    truth[np.arange(len(labels)), labels] = 1.0
    return float(np.mean(np.sum((probabilities - truth) ** 2, axis=1)))


def n_classes_for(contract: str) -> int:
    """How many outcomes the contract has. Raises on an unknown one rather than assuming."""
    if contract not in CONTRACT_CLASSES:
        raise SettlementHeadUnavailable(
            f"no class layout for contract {contract!r}; known: {sorted(CONTRACT_CLASSES)}. "
            f"Guessing the number of outcomes would mislabel every row.")
    return len(CONTRACT_CLASSES[contract])


def class_index(contract: str, name: str) -> int:
    return CONTRACT_CLASSES[contract].index(name)


def prior_brier(train_labels: np.ndarray, eval_labels: np.ndarray | None = None,
                n_classes: int = 3) -> float:
    """Brier of always predicting the TRAIN class prior, SCORED ON `eval_labels`.

    The prior comes from TRAIN so it cannot peek at the holdout's own class balance; the score
    is computed on the SAME rows the model was scored on, or the comparison is meaningless.

    The earlier version took one array and did both with it, and the caller passed y_train.
    The model's Brier was measured on y_hold, so `beats_prior` compared two numbers computed
    on DIFFERENT datasets - it could read True purely because the holdout was easier or more
    balanced than the training window. That is not a baseline, it is a coincidence.

    Accuracy is the wrong yardstick here for the same reason it was wrong for the ensemble
    weights: on an imbalanced settlement bucket, always answering the majority class scores
    well while carrying no information."""
    counts = np.bincount(train_labels, minlength=n_classes).astype(float)
    prior = counts / max(counts.sum(), 1.0)
    scored_on = train_labels if eval_labels is None else eval_labels
    return brier_multiclass(np.tile(prior, (len(scored_on), 1)), scored_on)


#: Minimum INDEPENDENT units, not rows. Five checkpoints per round and overlapping lookbacks
#: mean 500 rows can be 100 rounds or fewer, and adjacent rounds are correlated too. A row
#: floor answers "did we have enough numbers", not "did we have enough independent evidence".
MIN_TRAIN_GROUPS = 200
MIN_HOLDOUT_GROUPS = 60


def purged_chronological_splits(n_rows: int, purge: int, n_folds: int = 3):
    """Chronological calibration folds with a purge gap, as (train_idx, val_idx) pairs.

    `CalibratedClassifierCV(..., cv=3)` uses ordinary K-fold: it interleaves later rows into
    the training half and earlier rows into validation, and it ignores the fact that adjacent
    sequence rows share most of their lookback and that horizon labels overlap. The isotonic
    map was therefore fitted partly on rows entangled with the ones it was scoring.

    Each fold trains on a prefix, skips `purge` rows, then validates on the block that follows.
    """
    if n_rows <= 0 or n_folds < 2:
        return []
    block = n_rows // (n_folds + 1)
    if block <= purge + 1:
        return []
    splits = []
    for k in range(1, n_folds + 1):
        train_end = block * k
        val_start = train_end + purge
        val_end = min(val_start + block, n_rows)
        if val_start >= val_end or train_end < 2:
            continue
        splits.append((np.arange(0, train_end), np.arange(val_start, val_end)))
    return splits


def _group_count(groups, index) -> int:
    """Distinct groups (rounds) covered by `index`. Falls back to row count when no groups are
    supplied, which is recorded rather than silently treated as equivalent."""
    if groups is None:
        return len(index)
    return int(len(np.unique(np.asarray(groups)[index])))


def _groups_for_horizon(groups, horizon):
    """Accept one grouping or a horizon-specific grouping map."""
    if isinstance(groups, dict):
        return groups.get(horizon)
    return groups


def _cluster_probability_intervals(probabilities, labels, groups, *, random_state=0,
                                   n_bootstrap=1000):
    """Predeclared confidence bins with a group-bootstrap 95% lower hit-rate bound."""
    probabilities = np.asarray(probabilities, dtype=float)
    labels = np.asarray(labels, dtype=int)
    groups = np.asarray(groups)
    predicted = np.argmax(probabilities, axis=1)
    confidence = np.max(probabilities, axis=1)
    correct = (predicted == labels).astype(float)
    edges = np.asarray((0.50, 0.55, 0.60, 0.65, 0.70, 0.75,
                        0.80, 0.85, 0.90, 0.95, 1.0000001))
    rng = np.random.default_rng(random_state)
    intervals = []
    for low, high in zip(edges[:-1], edges[1:]):
        mask = (confidence >= low) & (confidence < high)
        bucket_groups = np.unique(groups[mask])
        if len(bucket_groups) < 30:
            continue
        group_hit_rates = np.asarray([
            float(np.mean(correct[mask & (groups == group)])) for group in bucket_groups
        ])
        samples = rng.choice(
            group_hit_rates, size=(int(n_bootstrap), len(group_hit_rates)), replace=True)
        observed = float(np.mean(group_hit_rates))
        intervals.append({
            "confidence_low": float(low), "confidence_high": float(min(high, 1.0)),
            "rows": int(np.sum(mask)), "independent_groups": int(len(bucket_groups)),
            "observed_accuracy": observed,
            "accuracy_lower_95": float(np.quantile(np.mean(samples, axis=1), 0.05)),
            "method": "group_bootstrap_95",
        })
    return intervals

def train_settlement_head(X: np.ndarray, Ysettle: dict, split_idx: int,
                          horizons=None, valid_mask: dict | None = None,
                          random_state: int = 0,
                          contract: str = TARGET_CONTRACT,
                          groups=None, lookback: int = 0) -> dict:
    """Fit one calibrated settlement classifier per horizon.

    `split_idx` is the SAME chronological boundary the ensemble uses, so the held-out metric
    below is measured on rows this head never saw. `valid_mask` is accepted for symmetry with
    the path head but is NOT used to drop rows: endpoint direction has no ambiguous case, and
    silently applying the first-touch mask here would discard rows that are perfectly labelled
    for THIS question.
    """
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import HistGradientBoostingClassifier

    n_classes = n_classes_for(contract)
    horizons = list(horizons or Ysettle.keys())
    X_flat = np.asarray(X).reshape(len(X), -1)
    heads: dict[int, object] = {}
    metrics: dict[int, dict] = {}
    skipped: dict[int, str] = {}

    for h in horizons:
        if h not in Ysettle:
            skipped[h] = "no_settlement_labels"
            continue
        labels = _labels_from_onehot(Ysettle[h])
        width = np.asarray(Ysettle[h]).shape[1]
        if width != n_classes:
            # A 3-column array offered for a binary contract is the banded label set under
            # a binary name. Refused: it would train the head on the wrong question while
            # the artifact claimed the right one.
            skipped[h] = (f"labels have {width} classes but {contract} has {n_classes}")
            continue
        if len(labels) != len(X_flat):
            skipped[h] = f"label/feature length mismatch {len(labels)} vs {len(X_flat)}"
            continue

        horizon_groups = _groups_for_horizon(groups, h)
        # PURGED BOUNDARY. Rows just before split_idx share most of their lookback with rows
        # just after it, and their horizon labels overlap, so an unpurged split leaks the
        # holdout into training through the entanglement rather than through the index.
        purge = int(lookback) + int(h)
        train_end = max(0, split_idx - purge)
        train_idx = np.arange(0, train_end)
        hold_idx = np.arange(split_idx, len(labels))
        y_train = labels[train_idx]
        x_train = X_flat[train_idx]
        if len(y_train) < MIN_TRAIN_ROWS:
            skipped[h] = f"only {len(y_train)} train rows after a {purge}-row purge (<{MIN_TRAIN_ROWS})"
            continue
        # INDEPENDENT UNITS, not rows.
        n_train_groups = _group_count(horizon_groups, train_idx)
        n_hold_groups = _group_count(horizon_groups, hold_idx)
        # WITHOUT groups these floors cannot fire at all. Guarding them on `groups is not
        # None` and leaving every caller passing None made them decorative - a declared limit
        # that no run could ever hit, which is the same defect as a counter nobody reads.
        #
        # Round IDs do not exist yet (they arrive with the round-aligned dataset), so the head
        # does not pretend to validate independence. It records that it could not, and the
        # metrics below are marked as carrying NO independence guarantee. A promotion gate
        # must refuse a bundle whose independence was never established.
        if horizon_groups is not None:
            if n_train_groups < MIN_TRAIN_GROUPS:
                skipped[h] = (f"only {n_train_groups} independent train rounds "
                              f"(<{MIN_TRAIN_GROUPS})")
                continue
            if n_hold_groups < MIN_HOLDOUT_GROUPS:
                skipped[h] = (f"only {n_hold_groups} independent holdout rounds "
                              f"(<{MIN_HOLDOUT_GROUPS})")
                continue
        if len(np.unique(y_train)) < 2:
            skipped[h] = "training rows carry a single class"
            continue

        base = HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.06, max_leaf_nodes=15,
            min_samples_leaf=100, random_state=random_state)
        # Calibrated because the OUTPUT is consumed as a probability in an EV calculation, not
        # as a ranking. An uncalibrated 0.70 that is really 0.55 turns a losing action into an
        # apparent winner - the exact defect CROSSING_CALIBRATION_V1 was written to measure.
        _counts = np.bincount(y_train, minlength=n_classes)
        folds = min(3, int(np.min(_counts[_counts > 0])))
        if folds < 2:
            skipped[h] = "thinnest class has too few rows to calibrate"
            continue
        # Chronological and purged, matching what the main ensemble was repaired to use.
        # An integer cv here would interleave future rows into the calibration training half.
        cal_splits = purged_chronological_splits(len(y_train), purge, n_folds=folds)
        if not cal_splits:
            skipped[h] = (f"cannot build purged chronological calibration folds "
                          f"(rows={len(y_train)}, purge={purge}) - refusing to fall back to "
                          f"interleaved K-fold, which would fit the calibrator on rows "
                          f"entangled with the ones it scores")
            continue
        model = CalibratedClassifierCV(base, method="isotonic", cv=cal_splits)
        model.fit(x_train, y_train)
        heads[h] = model

        entry = {"train_rows": int(len(y_train)),
                 "train_prior": np.bincount(y_train, minlength=n_classes).tolist()}
        entry["train_rounds"] = n_train_groups
        entry["holdout_rounds"] = n_hold_groups
        entry["purge_rows"] = purge
        entry["grouped"] = horizon_groups is not None
        # Stated on every horizon's metrics, so a reader cannot mistake "the floor did not
        # fire" for "the floor was satisfied".
        entry["independence_validated"] = horizon_groups is not None
        if horizon_groups is None:
            entry["independence_note"] = (
                "no round grouping supplied: rows are NOT independent (overlapping lookbacks, "
                "and multiple checkpoints per round once round-aligned labels exist). Row "
                "counts overstate evidence; holdout metrics carry no independence guarantee.")
        y_hold = labels[hold_idx]
        x_hold = X_flat[hold_idx]
        if len(y_hold) >= 100 and len(np.unique(y_hold)) >= 2:
            probabilities = _aligned_proba(model, x_hold, n_classes)
            entry.update({
                "holdout_rows": int(len(y_hold)),
                "holdout_brier": brier_multiclass(probabilities, y_hold),
                # The baseline uses the TRAIN prior, so beating it cannot be achieved by
                # learning the holdout's own class balance.
                # Train prior, scored on the SAME holdout rows as the model.
                "prior_brier": prior_brier(y_train, y_hold, n_classes),
            })
            entry["beats_prior"] = bool(entry["holdout_brier"] < entry["prior_brier"])
            if horizon_groups is not None:
                entry["confidence_intervals"] = _cluster_probability_intervals(
                    probabilities, y_hold, np.asarray(horizon_groups)[hold_idx],
                    random_state=random_state + int(h))
        else:
            entry["holdout_rows"] = int(len(y_hold))
            entry["holdout_brier"] = None
            entry["beats_prior"] = None
        metrics[h] = entry

    if not heads:
        raise SettlementHeadUnavailable(
            f"no horizon produced a settlement head: {skipped or 'no horizons supplied'}")
    # The RULE travels with the artifact, not just the contract name. Two bundles can both
    # claim `polymarket_binary_settlement_v1` and be fitted under different tie conventions;
    # only the rule identity distinguishes them, and the tie convention was wrong once already.
    bundle = {"heads": heads, "metrics": metrics, "skipped": skipped,
              "target_contract": contract, "n_classes": n_classes,
              # Bundle-level, so a promotion gate reads one field instead of every horizon.
              "independence_validated": bool(metrics) and all(
                  row.get("independence_validated") is True for row in metrics.values()),
              "trained_at_ms": int(time.time() * 1000)}
    if tc.is_binary_endpoint(contract):
        bundle["settlement_rule"] = tc.DEFAULT_SETTLEMENT_RULE.identity()
    return bundle


def _aligned_proba(model, x: np.ndarray, n_classes: int) -> np.ndarray:
    """predict_proba widened to the contract's full class layout, BY CLASS ID.

    A fold that never saw a class returns fewer columns, and reading column 1 positionally
    would then return a different class's probability under the right-looking name. The
    width comes from the contract, so a binary head is never widened into a NEUTRAL column
    that its market has no outcome for."""
    raw = model.predict_proba(x)
    classes = np.asarray(getattr(model, "classes_", np.arange(raw.shape[1])), dtype=int)
    out = np.zeros((len(raw), n_classes), dtype=float)
    for position, cls in enumerate(classes):
        if 0 <= int(cls) < n_classes:
            out[:, int(cls)] = raw[:, position]
    return out


def settlement_probability(bundle: dict, x_row: np.ndarray, horizon: int) -> dict:
    """P(settles UP) for one row, with its contract attached.

    Returns the contract alongside the number so a consumer can call
    `target_contract.assert_admissible` - a bare float is what let a path probability price an
    endpoint question in the first place."""
    heads = (bundle or {}).get("heads") or {}
    if horizon not in heads:
        raise SettlementHeadUnavailable(f"no settlement head for horizon {horizon}")
    contract = bundle.get("target_contract", TARGET_CONTRACT)
    n_classes = int(bundle.get("n_classes") or n_classes_for(contract))
    row = np.asarray(x_row, dtype=float).reshape(1, -1)
    probabilities = _aligned_proba(heads[horizon], row, n_classes)[0]
    out = {
        "p_up": float(probabilities[class_index(contract, tc.UP)]),
        "p_down": float(probabilities[class_index(contract, tc.DOWN)]),
        "target_contract": contract,
        "horizon": int(horizon),
    }
    confidence = max(out["p_up"], out["p_down"])
    intervals = ((bundle.get("metrics") or {}).get(horizon) or {}).get(
        "confidence_intervals") or []
    matched = next((row for row in intervals
                    if float(row.get("confidence_low", 2.0)) <= confidence
                    < float(row.get("confidence_high", -1.0)) + 1e-12), None)
    if matched:
        out["confidence_lower_95"] = float(matched["accuracy_lower_95"])
        out["uncertainty_method"] = str(matched.get("method") or "group_bootstrap_95")
        out["uncertainty_bucket"] = dict(matched)
    # Carried through to the consumer so an EV calculation can check which market's rule the
    # probability was fitted under, rather than assuming its own.
    if bundle.get("settlement_rule"):
        out["settlement_rule"] = bundle["settlement_rule"]
    # p_neutral only exists where the CONTRACT has a neutral outcome. Emitting a 0.0 under
    # a binary contract would read as "we measured no chance of flat" rather than "flat is
    # not an outcome this market pays".
    if tc.NEUTRAL in CONTRACT_CLASSES[contract]:
        out["p_neutral"] = float(probabilities[class_index(contract, tc.NEUTRAL)])
    return out


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    check(TARGET_CONTRACT == tc.ROLLING_EXCHANGE_RETURN_SIGN_V1,
          "the head declares the EXCHANGE PROXY contract - its labels use exchange closes and "
          "a decision-time reference, neither of which is how the venue settles")
    check(tc.assert_admissible(tc.PROXY_SETTLEMENT_RESEARCH, TARGET_CONTRACT) == TARGET_CONTRACT,
          "a RESEARCH consumer may use it, so the head can be measured against geometry and "
          "the market price")
    check(TARGET_CONTRACT not in tc.PATH_CONTRACTS,
          "it is NOT admissible for path questions, so it cannot replace the first-touch head")
    for _purpose in (tc.POLYMARKET_SETTLEMENT_EV, tc.POLYMARKET_HOLD_EXIT_EV):
        try:
            tc.assert_admissible(_purpose, TARGET_CONTRACT)
            raise AssertionError(f"{_purpose} priced on the exchange proxy")
        except tc.ContractMisuse:
            pass
    checks += 1
    print("  PASS  while every Polymarket EV purpose REFUSES it - the proxy may be measured, "
          "never used to price the market it is a proxy for")
    try:
        tc.assert_admissible(tc.POLYMARKET_SETTLEMENT_EV, tc.ENDPOINT_SETTLEMENT_V1)
        raise AssertionError("the banded endpoint contract priced a Polymarket EV")
    except tc.ContractMisuse:
        checks += 1
        print("  PASS  and so is the BANDED endpoint contract - its NEUTRAL band is our "
              "artefact, and ~68% of real payouts fall inside it")

    # ---- a learnable signal, and a prior it must beat --------------------------------
    rng = np.random.default_rng(0)
    n, n_features = 4000, 6
    X = rng.normal(0, 1, (n, n_features))
    # Endpoint direction driven by feature 0, with real noise.
    score = X[:, 0] + rng.normal(0, 0.8, n)
    split = 3000

    # BINARY (the default contract): two columns, [DOWN, UP], no band.
    binary_labels = (score > 0).astype(int)
    Ybinary = {5: np.eye(2)[binary_labels].astype(np.float32)}
    bundle = train_settlement_head(X, Ybinary, split, horizons=[5])
    check(5 in bundle["heads"], "a head is fitted for the requested horizon")
    check(bundle["target_contract"] == tc.ROLLING_EXCHANGE_RETURN_SIGN_V1
          and bundle["n_classes"] == 2,
          "the fitted bundle carries its contract and its TWO-class layout")
    check(bundle.get("independence_validated") is False,
          "a bundle trained WITHOUT round grouping declares independence_validated=False - "
          "the group floors cannot fire when no caller supplies groups, and a limit that no "
          "run can reach is decoration, not a gate")
    _m5 = bundle["metrics"][5]
    check(_m5.get("independence_validated") is False and _m5.get("independence_note"),
          "and each horizon states WHY, so 'the floor did not fire' cannot be read as 'the "
          "floor was satisfied'")
    _grouped = train_settlement_head(
        X, Ybinary, split, horizons=[5],
        groups={5: np.repeat(np.arange(len(X) // 4 + 1), 4)[:len(X)]})
    check(_grouped.get("independence_validated") is True,
          "while a run given real round groups declares it True - the flag tracks the input, "
          "not a constant")
    _intervals = _grouped["metrics"][5].get("confidence_intervals") or []
    check(_intervals and all(row["independent_groups"] >= 30 for row in _intervals),
          "grouped holdout confidence gets a cluster-bootstrap lower bound, and sparse bins "
          "are withheld rather than called empirical uncertainty")
    _rule_id = bundle.get("settlement_rule") or {}
    check(_rule_id.get("comparator") == ">=" and _rule_id.get("tie_outcome") == tc.UP,
          "and the SETTLEMENT RULE it was fitted under, so a bundle trained on the old "
          "strict-'>' tie convention is distinguishable from one trained on the real rule")
    check(len(_rule_id.get("rule_text_hash", "")) == 64
          and _rule_id.get("source") == "chainlink_btc_usd",
          "including the rule-text hash and the settlement source it resolves against")
    bprobe = settlement_probability(bundle, X[split], 5)
    check("p_neutral" not in bprobe,
          "a binary head emits NO p_neutral - a 0.0 there would read as 'we measured no "
          "chance of flat' rather than 'flat is not an outcome this market pays'")
    check(abs(bprobe["p_up"] + bprobe["p_down"] - 1.0) < 1e-6,
          "and its two probabilities sum to one")

    # Offering the three-class labels under the binary contract must be REFUSED, not squeezed.
    three_col = np.eye(3)[np.where(score > 0.45, 2, np.where(score < -0.45, 0, 1))
                          ].astype(np.float32)
    try:
        train_settlement_head(X, {5: three_col}, split, horizons=[5])
        raise AssertionError("banded labels were accepted under the binary contract")
    except SettlementHeadUnavailable:
        checks += 1
        print("  PASS  three-class labels offered under the BINARY contract are refused - "
              "otherwise the artifact would claim one question and be trained on another")

    # BANDED (still correct for the Binance perp lane, where the band is the no-trade zone).
    labels = np.where(score > 0.45, UP, np.where(score < -0.45, DOWN, NEUTRAL))
    Ysettle = {5: np.eye(3)[labels].astype(np.float32)}
    bundle = train_settlement_head(X, Ysettle, split, horizons=[5],
                                   contract=tc.ENDPOINT_SETTLEMENT_V1)
    check(bundle["target_contract"] == tc.ENDPOINT_SETTLEMENT_V1
          and bundle["n_classes"] == 3,
          "the same trainer fits the BANDED contract when asked for it explicitly, so the "
          "perp lane keeps the head whose neutral band it actually uses")

    m = bundle["metrics"][5]
    check(m["holdout_rows"] == n - split, "metrics are measured on the untouched tail")
    check(m["holdout_brier"] < m["prior_brier"],
          f"holdout Brier {m['holdout_brier']:.4f} beats the TRAIN-prior baseline "
          f"{m['prior_brier']:.4f} - the baseline is the prior, not accuracy, because always "
          f"answering the majority class scores well while carrying no information")
    check(m["beats_prior"] is True, "and that verdict is recorded on the artifact")

    probe = settlement_probability(bundle, X[split], 5)
    check(abs(probe["p_up"] + probe["p_down"] + probe["p_neutral"] - 1.0) < 1e-6,
          "the three settlement probabilities sum to one")
    check(probe["target_contract"] == tc.ENDPOINT_SETTLEMENT_V1,
          "and the probability travels WITH its contract, so a consumer can check "
          "admissibility instead of receiving a bare float")

    # The head must actually track the driver, not emit a constant.
    strong_up = np.zeros(n_features)
    strong_up[0] = 3.0
    strong_down = np.zeros(n_features)
    strong_down[0] = -3.0
    up_p = settlement_probability(bundle, strong_up, 5)["p_up"]
    down_p = settlement_probability(bundle, strong_down, 5)["p_up"]
    check(up_p > down_p + 0.2,
          f"a strongly bullish row scores higher than a bearish one ({up_p:.3f} vs "
          f"{down_p:.3f}) - the head is not returning a constant")

    # ---- IT MUST REFUSE RATHER THAN GUESS -------------------------------------------
    try:
        train_settlement_head(X[:50], {5: Ysettle[5][:50]}, 40, horizons=[5])
        raise AssertionError("a 40-row fit was accepted")
    except SettlementHeadUnavailable:
        checks += 1
        print(f"  PASS  fewer than {MIN_TRAIN_ROWS} rows raises rather than writing an "
              f"artifact whose existence would imply evidence")

    single = np.eye(3)[np.full(n, UP)].astype(np.float32)
    try:
        train_settlement_head(X, {5: single}, split, horizons=[5])
        raise AssertionError("a single-class fit was accepted")
    except SettlementHeadUnavailable:
        checks += 1
        print("  PASS  a single-class target raises - a head that has only seen UP cannot "
              "price DOWN")

    try:
        settlement_probability(bundle, X[0], 15)
        raise AssertionError("an untrained horizon returned a probability")
    except SettlementHeadUnavailable:
        checks += 1
        print("  PASS  an untrained horizon RAISES - the caller must abstain, never fall back "
              "to the path head")

    # ---- the widened probability layout ---------------------------------------------
    class _TwoClass:
        classes_ = np.array([DOWN, UP])

        def predict_proba(self, x):
            return np.tile(np.array([0.3, 0.7]), (len(x), 1))

    widened = _aligned_proba(_TwoClass(), np.zeros((2, n_features)), 3)
    check(abs(widened[0][DOWN] - 0.3) < 1e-9 and abs(widened[0][UP] - 0.7) < 1e-9,
          "a two-class head is widened by CLASS ID, so its second column lands in UP")
    check(widened[0][NEUTRAL] == 0.0,
          "...and NEUTRAL reads 0.0 rather than silently receiving the UP probability - "
          "positional reading would have mislabelled it")

    # The width comes from the CONTRACT, so a binary head is never given a phantom column.
    class _BinaryClass:
        classes_ = np.array([0, 1])                       # [DOWN, UP] under the binary layout

        def predict_proba(self, x):
            return np.tile(np.array([0.3, 0.7]), (len(x), 1))

    narrow = _aligned_proba(_BinaryClass(), np.zeros((2, n_features)),
                            n_classes_for(tc.POLYMARKET_BINARY_SETTLEMENT_V1))
    check(narrow.shape[1] == 2 and abs(narrow[0][1] - 0.7) < 1e-9,
          "under the binary contract a head widens to TWO columns, not three - there is no "
          "NEUTRAL outcome to widen into")
    # And a class ID the contract does not have is DROPPED, not folded into a real class.
    stray = _aligned_proba(_TwoClass(), np.zeros((2, n_features)),
                           n_classes_for(tc.POLYMARKET_BINARY_SETTLEMENT_V1))
    check(abs(stray[0][0] - 0.3) < 1e-9 and stray[0][1] == 0.0,
          "a three-class head's NEUTRAL/UP id offered under a binary layout is dropped rather "
          "than landing in a column it does not belong to")
    try:
        n_classes_for("some_contract_nobody_declared")
        raise AssertionError("an unknown contract was given a class count")
    except SettlementHeadUnavailable:
        checks += 1
        print("  PASS  and an unknown contract raises rather than defaulting to three "
              "classes, which would mislabel every row it touched")

    # ---- registry: it exists, and it may do nothing ----------------------------------
    from model_registry import REGISTRY
    entry = next((e for e in REGISTRY if e.name == REGISTRY_NAME), None)
    check(entry is not None, "the head is REGISTERED, so it is not an unregistered bypass")
    check(entry.target == TARGET_CONTRACT,
          "the registry records the contract it answers, so a bundle claiming another target "
          "is WRONG_TARGET")
    check(not (entry.may_price or entry.may_rank or entry.may_size),
          "and it carries NO authority - existing is not the same as having earned anything")

    print(f"\nSETTLEMENT HEAD SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    parser.error("nothing to do: pass --selftest (training runs from the server's train path)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
