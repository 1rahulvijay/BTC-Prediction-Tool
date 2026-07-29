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
    test_one_advance_per_closed_bar()
    test_belief_resets_across_a_gap()
    test_replay_is_deterministic()
    test_fitting_does_not_see_the_future()
    test_volume_scale_is_a_fitted_parameter()
    print("\nREGIME CAUSAL FILTER", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1




# ---------------------------------------------------------------------------------------------
# COMMIT A additions: the observation clock, and fit-time leakage.
#
# The tests above only prove the filter is causal GIVEN fitted parameters. They cannot see
# either of the two defects below, both of which live outside the filtering recursion.
# ---------------------------------------------------------------------------------------------


def test_one_advance_per_closed_bar() -> None:
    """The main loop ticks every 2s; the transition matrix counts one transition per BAR."""
    print("the filter advances once per bar, not once per main-loop tick")
    engine, closes, volumes, fitted = _fitted_engine()
    if not fitted or not engine.hmm_ready:
        chk(False, "engine did not fit")
        return

    bar_id = 1_700_000_000_000
    first = engine._hmm_classify(closes, volumes, observation_id=bar_id)
    belief_after_first = engine._belief.copy()
    advances_after_first = engine.hmm_advances

    # 30 main-loop calls within the same minute, exactly as BTC_MAIN_LOOP_SEC=2.0 produces.
    for _ in range(29):
        again = engine._hmm_classify(closes, volumes, observation_id=bar_id)
        chk_silent = again == first
        if not chk_silent:
            break
    chk(engine.hmm_advances == advances_after_first,
        f"30 calls on the SAME bar advanced the filter {engine.hmm_advances - advances_after_first} "
        f"extra times (must be 0)")
    chk(np.allclose(belief_after_first, engine._belief),
        "the posterior is byte-identical after 30 repeat calls")
    chk(engine.hmm_repeat_observations == 29,
        f"the 29 repeats are counted, not silently ignored ({engine.hmm_repeat_observations})")

    engine._hmm_classify(closes, volumes, observation_id=bar_id + 60_000)
    chk(engine.hmm_advances == advances_after_first + 1,
        "one NEW closed bar advances the filter exactly once")


def test_belief_resets_across_a_gap() -> None:
    print("a sequence break resets the belief instead of asserting continuity")
    engine, closes, volumes, fitted = _fitted_engine()
    if not fitted or not engine.hmm_ready:
        chk(False, "engine did not fit")
        return

    base = 1_700_000_000_000
    engine._hmm_classify(closes, volumes, observation_id=base)
    resets_before = engine.hmm_resets
    engine._hmm_classify(closes, volumes, observation_id=base + 60_000 * 50)   # 50-bar hole
    chk(engine.hmm_resets == resets_before + 1,
        "a 50-bar gap resets the posterior (restart / reconnect / backfill)")

    resets_before = engine.hmm_resets
    engine._hmm_classify(closes, volumes, observation_id=base + 60_000 * 51)
    chk(engine.hmm_resets == resets_before,
        "a normal consecutive bar does NOT reset")


def test_replay_is_deterministic() -> None:
    print("replaying the same bar sequence reproduces the same posterior")
    engine_a, closes, volumes, fitted = _fitted_engine()
    engine_b, _, _, _ = _fitted_engine()
    if not fitted:
        chk(False, "engine did not fit")
        return

    base = 1_700_000_000_000
    for i in range(20):
        engine_a._hmm_classify(closes[:200 + i], volumes[:200 + i], observation_id=base + i * 60_000)
    # Same sequence, but every bar also presented several times as the live loop would.
    for i in range(20):
        for _ in range(3):
            engine_b._hmm_classify(closes[:200 + i], volumes[:200 + i],
                                   observation_id=base + i * 60_000)
    chk(np.allclose(engine_a._belief, engine_b._belief),
        "repeat presentations do not change the final posterior - replay is deterministic")


def test_fitting_does_not_see_the_future() -> None:
    """Parameters must be estimated from training observations only.

    The causality test above fits on the WHOLE series and then checks the filter. That cannot
    detect fit-time leakage, because by then the future has already shaped the means,
    covariances, state labels and transition matrix."""
    print("HMM parameters do not depend on future observations")
    from regime import MarketRegime

    rng = np.random.default_rng(3)
    quiet = rng.normal(0.0, 0.0006, 400)
    trend = rng.normal(0.0035, 0.0008, 400)
    train_returns = np.concatenate([quiet, trend])
    closes_train = 60000.0 * np.exp(np.cumsum(train_returns))
    vols_train = np.abs(rng.normal(120.0, 30.0, len(closes_train))) + 10.0

    # Two DIFFERENT futures appended to the same training period.
    future_a = rng.normal(0.0, 0.0002, 400)
    future_b = rng.normal(0.0, 0.0250, 400)        # a violent regime the training period lacks
    closes_a = np.concatenate([closes_train, closes_train[-1] * np.exp(np.cumsum(future_a))])
    closes_b = np.concatenate([closes_train, closes_train[-1] * np.exp(np.cumsum(future_b))])
    vols_a = np.concatenate([vols_train, np.abs(rng.normal(120.0, 30.0, 400)) + 10.0])
    vols_b = np.concatenate([vols_train, np.abs(rng.normal(900.0, 90.0, 400)) + 10.0])

    cut = len(closes_train)

    # FOLD-LOCAL fit: parameters come from [:cut] only, in both worlds.
    a, b = MarketRegime(), MarketRegime()
    a.fit_hmm(closes_a[:cut], vols_a[:cut])
    b.fit_hmm(closes_b[:cut], vols_b[:cut])
    chk(np.allclose(a._transmat, b._transmat) and np.allclose(a._means, b._means),
        "two different futures yield IDENTICAL parameters when the fit is fold-local")

    labels_a = a.classify_series(closes_a, vols_a)[:cut]
    labels_b = b.classify_series(closes_b, vols_b)[:cut]
    chk(labels_a == labels_b,
        "and identical training-period labels - the future cannot rewrite the training partition")

    # The defect, for contrast: fitting on the FULL series lets the future move training labels.
    leaky_a, leaky_b = MarketRegime(), MarketRegime()
    leaky_a.fit_hmm(closes_a, vols_a)
    leaky_b.fit_hmm(closes_b, vols_b)
    leaked = (not np.allclose(leaky_a._transmat, leaky_b._transmat)) or (
        leaky_a.classify_series(closes_a, vols_a)[:cut]
        != leaky_b.classify_series(closes_b, vols_b)[:cut])
    chk(leaked,
        "control: fitting on the FULL series DOES let the future change training-period "
        "parameters or labels, which is the leak this fold-local fit removes")


def test_volume_scale_is_a_fitted_parameter() -> None:
    """The observation's third feature must mean the same thing at train and serve time."""
    print("the volume scale is fitted once, not recomputed per call")
    engine, closes, volumes, fitted = _fitted_engine()
    if not fitted or not engine.hmm_ready:
        chk(False, "engine did not fit")
        return

    chk(engine._median_volume is not None, "fitting stores the training volume median")

    # Serving passes only the last few bars. Under the old per-call median, vol_ratio was each
    # bar against FIVE bars' median while the Gaussians were fitted against ~900 bars'.
    full = engine._make_obs(closes, volumes)
    tail = engine._make_obs(closes[-5:], volumes[-5:])
    chk(np.allclose(full[-1], tail[-1]),
        "the last observation is IDENTICAL whether built from the full array or a 5-bar tail")

    # And the scale must not drift when a different future is appended.
    louder = np.concatenate([volumes, volumes[-50:] * 40.0])
    longer = np.concatenate([closes, closes[-50:]])
    engine_obs_before = engine._make_obs(closes, volumes)[10]
    engine_obs_after = engine._make_obs(longer, louder)[10]
    chk(np.allclose(engine_obs_before, engine_obs_after),
        "a high-volume FUTURE does not rescale a past observation")


if __name__ == "__main__":
    raise SystemExit(main())
