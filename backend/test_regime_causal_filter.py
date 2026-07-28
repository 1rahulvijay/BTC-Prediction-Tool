"""classify_series must run the SAME causal filter that serving runs.

WHAT WAS WRONG
    classify_series assigned each bar by `argmax(emission_loglik(obs[j]))`. Each observation was
    labelled purely from its own likelihood, so the fitted transition matrix contributed nothing:
    a Gaussian mixture classifier, not sequential HMM inference.

    The consequence is sharper than "inference is less accurate". This function's stated job is
    the train/serve-alignment primitive - training rows are labelled with "the SAME partition
    serving routes by". Serving routes by _hmm_classify, which forward-filters with the
    transition matrix. Emission-only labelling is a DIFFERENT partition, so the alignment the
    function existed to guarantee did not hold.

WHY FORWARD FILTERING AND NOT VITERBI
    Viterbi and forward-backward smoothing revise earlier states using LATER observations. Used
    to label training rows that is straightforward lookahead - a row's target would encode
    information unavailable at that row's decision time. Only the causal forward recursion

        alpha_t(j) ~ P(x_t | z_t=j) * sum_i alpha_{t-1}(i) P(z_t=j | z_{t-1}=i)

    is admissible here, and it is what serving already computes.

    python backend/test_regime_causal_filter.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _fitted_engine(seed: int = 7, n: int = 900):
    """A regime engine fitted on synthetic data with clearly separated regimes."""
    from regime import MarketRegime

    rng = np.random.default_rng(seed)
    # Three contiguous regimes: quiet drift, strong trend, high volatility.
    quiet = rng.normal(0.0, 0.0006, n // 3)
    trend = rng.normal(0.0035, 0.0008, n // 3)
    wild = rng.normal(0.0, 0.0090, n - 2 * (n // 3))
    returns = np.concatenate([quiet, trend, wild])
    closes = 60000.0 * np.exp(np.cumsum(returns))
    volumes = np.abs(rng.normal(120.0, 30.0, len(closes))) + 10.0

    engine = MarketRegime()
    fitted = engine.fit_hmm(closes, volumes)
    return engine, closes, volumes, bool(fitted)


def test_transition_matrix_affects_output() -> None:
    print("the fitted transition matrix actually participates")
    engine, closes, volumes, fitted = _fitted_engine()
    if not fitted or not engine.hmm_ready:
        chk(False, "engine did not fit - cannot test (investigate before trusting this suite)")
        return

    baseline = engine.classify_series(closes, volumes)

    # Replace the transition matrix with a STICKY one. Under emission-only labelling this can
    # have no effect whatsoever; under a forward filter it must change the labelling.
    n = len(engine._means)
    original = engine._transmat
    try:
        sticky = np.full((n, n), 0.001)
        np.fill_diagonal(sticky, 1.0)
        engine._transmat = sticky / sticky.sum(axis=1, keepdims=True)
        stickied = engine.classify_series(closes, volumes)
    finally:
        engine._transmat = original

    changed = sum(1 for a, b in zip(baseline, stickied) if a != b)
    chk(changed > 0,
        f"a sticky transition matrix changes the labelling ({changed} of {len(baseline)} bars) - "
        f"under emission-only argmax this difference is necessarily ZERO")

    flips_base = sum(1 for a, b in zip(baseline, baseline[1:]) if a != b)
    flips_sticky = sum(1 for a, b in zip(stickied, stickied[1:]) if a != b)
    chk(flips_sticky <= flips_base,
        f"stickier transitions do not increase regime flips ({flips_sticky} vs {flips_base})")


def test_labelling_is_causal() -> None:
    """A label at bar t must not change when bars after t are appended."""
    print("labels are causal - appending future bars cannot rewrite the past")
    engine, closes, volumes, fitted = _fitted_engine()
    if not fitted or not engine.hmm_ready:
        chk(False, "engine did not fit")
        return

    cut = len(closes) // 2
    prefix = engine.classify_series(closes[:cut], volumes[:cut])
    full = engine.classify_series(closes, volumes)

    # Index 0 mirrors index 1 by construction, so compare from 1.
    disagreements = sum(1 for i in range(1, cut) if prefix[i] != full[i])
    chk(disagreements == 0,
        f"the first {cut} labels are identical with and without future data "
        f"({disagreements} disagreements) - Viterbi or smoothing would fail this")


def test_train_and_serve_use_the_same_partition() -> None:
    """The labelling primitive must agree with what serving actually routes by."""
    print("training labels match the serving partition")
    engine, closes, volumes, fitted = _fitted_engine()
    if not fitted or not engine.hmm_ready:
        chk(False, "engine did not fit")
        return

    labels = engine.classify_series(closes, volumes)

    # Reproduce the serving recursion independently, from the same neutral prior.
    obs = engine._make_obs(closes, volumes)
    n = len(engine._means)
    belief = np.full(n, 1.0 / n)
    serving: list[str] = []
    for row in obs:
        ll = engine._emission_loglik(row)
        prior = belief @ engine._transmat
        logp = np.log(prior + 1e-12) + ll
        logp -= logp.max()
        post = np.exp(logp)
        post /= (post.sum() + 1e-12)
        belief = post
        fine = engine.state_labels.get(int(np.argmax(post)), engine.RANGE)
        if fine in (engine.TRENDING_UP, engine.TRENDING_DOWN):
            serving.append("TREND")
        elif fine == engine.HIGH_VOLATILITY:
            serving.append("VOLATILE")
        else:
            serving.append("RANGE")

    mismatches = sum(1 for i, s in enumerate(serving) if labels[i + 1] != s)
    chk(mismatches == 0,
        f"every training label equals the forward-filtered serving state ({mismatches} "
        f"mismatches of {len(serving)})")


def test_live_belief_is_not_disturbed() -> None:
    """Labelling a historical array must not corrupt the live filter's state."""
    print("labelling history does not disturb the live belief")
    engine, closes, volumes, fitted = _fitted_engine()
    if not fitted or not engine.hmm_ready:
        chk(False, "engine did not fit")
        return

    engine._belief = np.array([0.7, 0.1, 0.1, 0.05, 0.05][:len(engine._means)])
    engine._belief = engine._belief / engine._belief.sum()
    before = engine._belief.copy()
    engine.classify_series(closes, volumes)
    chk(np.allclose(before, engine._belief),
        "self._belief is unchanged - classify_series uses a LOCAL belief")


def test_unfitted_engine_falls_back_safely() -> None:
    print("an unfitted engine still returns a usable, honest fallback")
    from regime import MarketRegime

    engine = MarketRegime()
    out = engine.classify_series(np.array([1.0, 2.0, 3.0]), np.array([1.0, 1.0, 1.0]))
    chk(out == ["RANGE", "RANGE", "RANGE"], f"all-RANGE fallback when not fitted ({out})")


def main() -> int:
    test_transition_matrix_affects_output()
    test_labelling_is_causal()
    test_train_and_serve_use_the_same_partition()
    test_live_belief_is_not_disturbed()
    test_unfitted_engine_falls_back_safely()
    print("\nREGIME CAUSAL FILTER", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
