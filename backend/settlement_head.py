"""The head that answers the question Polymarket actually resolves on.

WHY THIS EXISTS
    `TRAINING_CONTRACT` is `first_touch_triple_barrier_v1` - "which barrier is touched FIRST".
    Polymarket resolves on where price ENDS relative to the anchor. Measured on random walks the
    two disagree 24.9% of the time, so a first-touch probability cannot price a settlement
    question, and `target_contract.assert_admissible` refuses it.

    That refusal was correct and left the settlement lane with NO head at all:
    `build_sequences(return_settlement_labels=True)` could emit the labels, but nothing
    requested them and nothing consumed them. This is the consumer.

WHAT IT IS NOT
    It is not the ensemble. One calibrated gradient-boosted classifier per horizon, trained on
    endpoint labels, stamped with `ENDPOINT_SETTLEMENT_V1`. Deliberately small: the point is to
    have an admissible settlement probability that can be MEASURED against the market, not to
    add a ninth seat to a model zoo whose own evidence says direction is weak.

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
#: The Polymarket lane is what this head exists to serve, and that market resolves on a strict
#: comparison with no neutral band. Training it on the three-class endpoint labels meant ~68%
#: of real payouts were labelled NEUTRAL, so the head answered a question the venue never asks.
TARGET_CONTRACT = tc.POLYMARKET_BINARY_SETTLEMENT_V1

#: Class layout per contract. The head is fitted per contract rather than hardcoding three
#: columns, because the number of outcomes IS the contract - a binary market has no NEUTRAL
#: column to widen into, and inventing one is how the band came back.
CONTRACT_CLASSES = {
    tc.ENDPOINT_SETTLEMENT_V1: tc.CLASS_ORDER,          # (DOWN, NEUTRAL, UP)
    tc.POLYMARKET_BINARY_SETTLEMENT_V1: tc.BINARY_CLASS_ORDER,   # (DOWN, UP)
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


def prior_brier(labels: np.ndarray, n_classes: int = 3) -> float:
    """Brier of always predicting the TRAIN class prior. The baseline any head must beat.

    Accuracy is the wrong yardstick here for the same reason it was wrong for the ensemble
    weights: on an imbalanced settlement bucket, always answering the majority class scores
    well while carrying no information."""
    counts = np.bincount(labels, minlength=n_classes).astype(float)
    prior = counts / max(counts.sum(), 1.0)
    return brier_multiclass(np.tile(prior, (len(labels), 1)), labels)


def train_settlement_head(X: np.ndarray, Ysettle: dict, split_idx: int,
                          horizons=None, valid_mask: dict | None = None,
                          random_state: int = 0,
                          contract: str = TARGET_CONTRACT) -> dict:
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

        train_slice = slice(0, split_idx)
        y_train = labels[train_slice]
        x_train = X_flat[train_slice]
        if len(y_train) < MIN_TRAIN_ROWS:
            skipped[h] = f"only {len(y_train)} train rows (<{MIN_TRAIN_ROWS})"
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
        model = CalibratedClassifierCV(base, method="isotonic", cv=folds)
        model.fit(x_train, y_train)
        heads[h] = model

        entry = {"train_rows": int(len(y_train)),
                 "train_prior": np.bincount(y_train, minlength=n_classes).tolist()}
        y_hold = labels[split_idx:]
        x_hold = X_flat[split_idx:]
        if len(y_hold) >= 100 and len(np.unique(y_hold)) >= 2:
            probabilities = _aligned_proba(model, x_hold, n_classes)
            entry.update({
                "holdout_rows": int(len(y_hold)),
                "holdout_brier": brier_multiclass(probabilities, y_hold),
                # The baseline uses the TRAIN prior, so beating it cannot be achieved by
                # learning the holdout's own class balance.
                "prior_brier": prior_brier(y_train, n_classes),
            })
            entry["beats_prior"] = bool(entry["holdout_brier"] < entry["prior_brier"])
        else:
            entry["holdout_rows"] = int(len(y_hold))
            entry["holdout_brier"] = None
            entry["beats_prior"] = None
        metrics[h] = entry

    if not heads:
        raise SettlementHeadUnavailable(
            f"no horizon produced a settlement head: {skipped or 'no horizons supplied'}")
    return {"heads": heads, "metrics": metrics, "skipped": skipped,
            "target_contract": contract, "n_classes": n_classes,
            "trained_at_ms": int(time.time() * 1000)}


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

    check(TARGET_CONTRACT == tc.POLYMARKET_BINARY_SETTLEMENT_V1,
          "the head declares the BINARY settlement contract - the question the venue pays on")
    check(tc.assert_admissible(tc.POLYMARKET_SETTLEMENT_EV, TARGET_CONTRACT) == TARGET_CONTRACT,
          "and a settlement-EV consumer accepts it - the lane that had no head now has one")
    check(TARGET_CONTRACT not in tc.PATH_CONTRACTS,
          "it is NOT admissible for path questions, so it cannot replace the first-touch head")
    try:
        tc.assert_admissible(tc.POLYMARKET_SETTLEMENT_EV, tc.ENDPOINT_SETTLEMENT_V1)
        raise AssertionError("the banded endpoint contract priced a Polymarket EV")
    except tc.ContractMisuse:
        checks += 1
        print("  PASS  while the BANDED endpoint contract is refused for the same purpose - "
              "its NEUTRAL band is our artefact, and ~68% of real payouts fall inside it")

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
    check(bundle["target_contract"] == tc.POLYMARKET_BINARY_SETTLEMENT_V1
          and bundle["n_classes"] == 2,
          "the fitted bundle carries its contract and its TWO-class layout")
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
