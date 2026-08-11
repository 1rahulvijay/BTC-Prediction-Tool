"""Pins P0-3 (signal-history persistence), P0-4 (HMM restart) and P0-5 (A/B symmetry).

Each was verified in source AND reproduced before being fixed. The reproductions matter more
than the fixes: every one of these passed its surrounding tests while being broken in
production, because the tests exercised a path production does not take.

    python backend/tests/test_p0_3_4_5_contracts.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def test_signal_history_roundtrip() -> None:
    """P0-3: save() reported success while load() silently returned 0, every restart."""
    from signal_history import LiveSignalHistoryBuffer

    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "signal_history.pkl")
        buffer = LiveSignalHistoryBuffer()
        for i in range(25):
            buffer.record(1_700_000_000_000 + i * 60_000,
                          {"order_flow": {"cvd_1m": float(i), "vpin": 0.5}})
        original = len(buffer)
        check(original == 25, "the buffer recorded its snapshots")
        check(buffer.save(path, force=True), "save() reports success")

        # The manifest is what verified_load hashes against. Without it, strict mode would
        # refuse the artifact even once the load path was fixed.
        check(os.path.exists(path + ".integrity.json"),
              "an integrity manifest is written beside the payload")

        restored = LiveSignalHistoryBuffer()
        loaded = restored.load(path)
        check(loaded == original,
              f"load() returns {loaded} of {original} - it returned 0 before, because the "
              f"loader was handed an open FILE OBJECT where verified_load expects a PATH, and "
              f"the resulting exception was swallowed")
        check(len(restored) == original, "and the buffer is genuinely repopulated")

        # The dirty counter must not be cleared by merely BUILDING a payload.
        fresh = LiveSignalHistoryBuffer()
        for i in range(10):
            fresh.record(1_700_000_000_000 + i * 60_000, {"order_flow": {"cvd_1m": 1.0}})
        before = fresh._dirty_count
        fresh.snapshot_payload()
        check(fresh._dirty_count == before,
              "snapshot_payload() does NOT clear the dirty counter - it only builds the "
              "payload, and the write happens later and may fail")
        fresh.mark_saved()
        check(fresh._dirty_count == 0, "mark_saved() is what clears it, after a real write")


def test_hmm_survives_restart() -> None:
    """P0-4: the HMM lived only in the training process."""
    from regime import MarketRegime

    rng = np.random.default_rng(0)
    n = 3000
    closes = 60_000 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    volumes = np.abs(rng.normal(100, 30, n))
    adx = np.full(n, 25.0)
    atr = np.full(n, 100.0)

    trained = MarketRegime()
    check(trained.fit_hmm(closes, volumes), "the HMM fits")
    trained.detect_regime(closes, adx, atr, volumes, observation_id=1_785_000_000)

    check(MarketRegime().hmm_ready is False,
          "a FRESH engine has no HMM - this is what every restart produced, so the direction "
          "experts trained on HMM partitions were routed by the heuristic fallback")

    # Through JSON, as a real bundle would carry it.
    state = json.loads(json.dumps(trained.state_dict()))
    check(bool(state.get("state_labels")),
          "the state carries state_labels - the HMM-state to regime-name mapping. An "
          "underscore-only key list dropped it, and a restored HMM could not NAME its states")

    restored = MarketRegime()
    check(restored.load_state_dict(state), "and it restores through a JSON round trip")
    check(restored.hmm_ready, "the restored engine is HMM-ready")

    nxt = 1_785_000_060
    before = trained.detect_regime(closes, adx, atr, volumes, observation_id=nxt)
    after = restored.detect_regime(closes, adx, atr, volumes, observation_id=nxt)
    check(before["regime"] == after["regime"],
          f"the SAME next bar routes identically before and after restart "
          f"({before['regime']})")
    check(abs(before["confidence"] - after["confidence"]) < 1e-9,
          "with the same confidence, so the filtered belief was carried too")
    check(before.get("method") == after.get("method") == "hmm",
          "and both take the HMM path, not the threshold fallback")

    # A long outage must STILL reset - restoring the belief must not assert continuity.
    stale = MarketRegime()
    stale.load_state_dict(state)
    resets_before = stale.hmm_resets
    stale.detect_regime(closes, adx, atr, volumes, observation_id=1_785_000_000 + 86_400)
    check(stale.hmm_resets > resets_before,
          "a 24h gap still RESETS the belief - the gap rule compares milliseconds, and a "
          "seconds-valued observation id used to make an outage look like 18,000 against a "
          "180,000 threshold, so it never fired")

    check(MarketRegime().load_state_dict({"hmm_ready": True}) is False,
          "a partial state fails CLOSED rather than half-restoring")
    check(MarketRegime().load_state_dict({}) is False, "and an empty state is refused")


def test_ab_compares_like_with_like() -> None:
    """P0-5: the primary was compared AFTER policy filtering, the challenger before."""
    from ab_testing import ABTestRunner, ModelVariant, RAW_MODEL_COMPARISON

    class _StubModel:
        is_trained = True

        def __init__(self, direction):
            self.direction = direction

        def generate_ensemble_prediction(self, h, seq, data_state, acc_cache=None,
                                         cascade_data=None):
            return {"direction": self.direction, "confidence": 0.71,
                    "model_bundle_id": f"bundle-{self.direction}"}

    runner = ABTestRunner(ModelVariant("incumbent", _StubModel("UP")),
                          ModelVariant("challenger", _StubModel("DOWN")))
    returned = runner.predict(5, np.zeros((1, 1)), {})

    # The server mutates the returned dict IN PLACE - this is exactly what the filter chain,
    # the expectancy neutraliser and the no-trade engine do.
    returned["direction"] = "NEUTRAL"
    returned["meta_filtered"] = True

    stored = runner.last_by_horizon[5]["primary"]
    check(stored["direction"] == "UP",
          "the stored primary keeps its RAW direction after the server neutralises the "
          "returned dict - it held a live reference before, so persist() compared a "
          "post-policy incumbent against a raw challenger")
    check("meta_filtered" not in stored,
          "and post-model annotations do not leak into the stored forecast")
    check(runner.last_by_horizon[5]["challenger"]["direction"] == "DOWN",
          "the challenger is captured raw, as it always was")
    check(runner.last_by_horizon[5]["comparison_basis"] == RAW_MODEL_COMPARISON,
          "the comparison basis is RECORDED, so a promotion decision cannot claim a "
          "policy-level comparison that was never made")


def main() -> int:
    test_signal_history_roundtrip()
    test_hmm_survives_restart()
    test_ab_compares_like_with_like()
    print(f"\nP0-3/4/5 CONTRACTS: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
