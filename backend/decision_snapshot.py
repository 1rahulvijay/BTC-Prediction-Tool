"""One immutable market view per decision, so a prediction can be replayed exactly.

THE DEFECT
    The live loop deep-copied klines, order flow, derivatives and sentiment to BUILD the feature
    sequence, then passed the live mutable global `data_state` to inference. Inference runs in a
    worker thread while WebSocket callbacks keep mutating that global, so one prediction could be
    assembled from:

        feature sequence at t0
        order flow at t1
        price at t2
        logged context at t3

    Nothing is corrupted in an obvious way; the numbers are all real. They just did not co-occur.
    That makes exact replay impossible even when the feature vector is stored, because there is
    no single moment the decision corresponds to.

WHAT THIS DOES AND DOES NOT DO
    It freezes the state INFERENCE reads, and gives the decision a content-addressed id derived
    from the market data itself, so two identical market views produce the same id. It does not
    yet thread that id through execution simulation, persistence and the UI payload - that is the
    remaining half of P0-12 and it is not done.

    python backend/decision_snapshot.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from types import MappingProxyType

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

#: Keys inference actually reads. Copied per decision; anything else is deliberately absent so a
#: new read of live state is a KeyError at test time rather than a silent race in production.
SNAPSHOT_KEYS = (
    "klines", "order_flow", "derivatives", "sentiment", "regime_info",
    "regime_model_weights", "regime_calibration", "confidence_calibrators",
    "signal_policy", "feature_parity",
)

#: Only these drive the identity hash. Calibrators and policy are model context, not market
#: state; including them would change the id when nothing about the market moved.
IDENTITY_KEYS = ("klines", "order_flow", "derivatives", "sentiment", "regime_info")


def _stable(value):
    """A JSON-safe projection for hashing. Unhashable/exotic values degrade to their repr."""
    if isinstance(value, dict):
        return {str(k): _stable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_stable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def snapshot_id(state: dict, event_ts_ms: int) -> str:
    payload = {"event_ts_ms": int(event_ts_ms),
               **{k: _stable(state.get(k)) for k in IDENTITY_KEYS}}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def build(live_state: dict, event_ts_ms: int, klines=None, copier=None) -> MappingProxyType:
    """Freeze the decision view. `klines` overrides the list (the model window is closed-bar only).

    Returned as a MappingProxyType: assigning into it raises, so an accidental write during
    inference fails loudly instead of mutating shared state."""
    import copy as _copy
    copier = copier or _copy.deepcopy
    snap: dict = {}
    for key in SNAPSHOT_KEYS:
        if key == "klines" and klines is not None:
            snap[key] = list(klines)
            continue
        value = live_state.get(key)
        # Model context (calibrators, policy) is rebuilt on the main thread between decisions
        # and can be large; referencing it is safe. Market state is copied.
        snap[key] = copier(value) if key in IDENTITY_KEYS else value
    snap["decision_id"] = snapshot_id(snap, event_ts_ms)
    snap["event_ts_ms"] = int(event_ts_ms)
    return MappingProxyType(snap)


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    live = {
        "klines": [{"time": 1, "close": 100.0}, {"time": 2, "close": 101.0}],
        "order_flow": {"cvd_1m": -1.63},
        "derivatives": {"funding_rate": 0.0001},
        "sentiment": {"fgi": 55},
        "regime_info": {"regime": "RANGE"},
        "regime_model_weights": {"xgb": 0.4},
        "confidence_calibrators": {"5": "calibrator"},
        "signal_policy": {"min_conf": 0.6},
        "regime_calibration": {}, "feature_parity": {},
    }
    snap = build(live, 1_000)

    check(set(SNAPSHOT_KEYS).issubset(snap.keys()), "every key inference reads is captured")
    check(snap["decision_id"] and len(snap["decision_id"]) == 16,
          "the decision carries a content-addressed id")

    # THE RACE, REPRODUCED. Mutate the live global exactly as a WebSocket callback would.
    live["order_flow"]["cvd_1m"] = 999.0
    live["klines"].append({"time": 3, "close": 105.0})
    live["regime_info"]["regime"] = "TREND"
    check(snap["order_flow"]["cvd_1m"] == -1.63,
          "a live order-flow update does NOT leak into a decision already in flight")
    check(len(snap["klines"]) == 2, "...nor does a new candle appended mid-inference")
    check(snap["regime_info"]["regime"] == "RANGE", "...nor a regime flip")

    try:
        snap["klines"] = []
        raise AssertionError("the snapshot was mutable")
    except TypeError:
        checks += 1
        print("  PASS  the snapshot REFUSES assignment - an accidental write fails loudly")

    # Determinism: the same market view yields the same id; a changed one does not.
    again = build({**live, "order_flow": {"cvd_1m": -1.63},
                   "klines": [{"time": 1, "close": 100.0}, {"time": 2, "close": 101.0}],
                   "regime_info": {"regime": "RANGE"}}, 1_000)
    check(again["decision_id"] == snap["decision_id"],
          "an identical market view reproduces the SAME decision id - replay is addressable")
    moved = build({**live, "klines": [{"time": 1, "close": 100.0},
                                      {"time": 2, "close": 101.5}],
                   "order_flow": {"cvd_1m": -1.63},
                   "regime_info": {"regime": "RANGE"}}, 1_000)
    check(moved["decision_id"] != snap["decision_id"],
          "a changed price yields a DIFFERENT id, so the hash is not constant")
    check(build(live, 2_000)["decision_id"] != build(live, 1_000)["decision_id"],
          "the timestamp participates, so two views of the same book at different times differ")

    # Model context must NOT change identity - the market did not move.
    ctx = dict(live)
    ctx["confidence_calibrators"] = {"5": "DIFFERENT"}
    check(build(ctx, 1_000)["decision_id"] == build(live, 1_000)["decision_id"],
          "swapping model CONTEXT leaves the id unchanged - identity is market state")

    override = build(live, 1_000, klines=[{"time": 1, "close": 100.0}])
    check(len(override["klines"]) == 1,
          "the closed-bar model window can be supplied explicitly")

    print(f"\nDECISION SNAPSHOT SELFTEST: PASS ({checks} checks)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.parse_args()
    raise SystemExit(selftest())
