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
import re
import sys
from pathlib import Path
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
    # P0-6. Read by apply_live_quality_filters, which now runs off this snapshot rather than
    # the live global. They are listed because `.get()` on a missing key does NOT raise - it
    # returns a default, and each of these defaults is dangerous in a specific way:
    #   order_flow_updated_ms   missing -> staleness computed against 0 -> EVERY decision
    #                           looks like a dead feed and gets filtered
    #   poor_regimes            missing -> the regime-quality veto silently stops vetoing
    #   spread_expansion_ratio  missing -> defaults to a calm book during a blow-out
    # The absent-key discipline that protects inference only works if the key list keeps up
    # with what the consumers actually read.
    "order_flow_updated_ms", "poor_regimes", "spread_expansion_ratio",
)

#: Only these drive the identity hash. Calibrators and policy are model context, not market
#: state; including them would change the id when nothing about the market moved.
#: `decision_price` IS market state and must be here: the klines in this snapshot are the
#: model's CLOSED-bar window, so two decisions taken at different live prices inside the same
#: forming bar would otherwise hash identically while pricing differently.
#: `spread_expansion_ratio` is market state and belongs here. `order_flow_updated_ms` and
#: `poor_regimes` deliberately do NOT: the first is the ARRIVAL TIME of state whose content is
#: already hashed, and the second is model-quality context. Hashing either would give every
#: decision a fresh id even when the market had not moved, which destroys the replay property
#: the id exists for.
IDENTITY_KEYS = ("klines", "order_flow", "derivatives", "sentiment", "regime_info",
                 "decision_price", "spread_expansion_ratio")


def _stable(value):
    """A JSON-safe projection for hashing. Unhashable/exotic values degrade to their repr."""
    if isinstance(value, dict):
        return {str(k): _stable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_stable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def deep_freeze(value):
    """Recursively make a copied structure read-only.

    P0-6. Wrapping only the OUTER dict in MappingProxyType blocked `snap["klines"] = []` and
    nothing else. These both succeeded:

        snap["order_flow"]["cvd_1m"] = 999.0
        snap["klines"].append(...)

    so the "immutable decision view" was one assignment deep. It matters most in A/B, where the
    primary and the challenger are handed the SAME nested objects: a stray write by one silently
    changes what the other was scored on, and the two variants are then not comparable.

    Applied to the deep COPIES, so this is real immutability rather than a read-only view over
    something still reachable and writable elsewhere. Non-container values (calibrators, policy
    objects) pass through untouched - freezing them is neither possible nor wanted.
    """
    if isinstance(value, MappingProxyType):
        return value
    if isinstance(value, dict):
        return MappingProxyType({k: deep_freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(deep_freeze(v) for v in value)
    if isinstance(value, tuple):
        return tuple(deep_freeze(v) for v in value)
    return value


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
            # The override path was a SHALLOW list() copy: the list was new, every bar dict
            # inside it was the same object the feed keeps mutating.
            snap[key] = deep_freeze(copier(list(klines)))
            continue
        value = live_state.get(key)
        # Model context (calibrators, policy) is rebuilt on the main thread between decisions
        # and can be large; referencing it is safe. Market state is copied, then DEEP-frozen -
        # a copy that anyone downstream can still write into is not a frozen decision view.
        snap[key] = deep_freeze(copier(value)) if key in IDENTITY_KEYS else value

    # P0-6. The DECISION PRICE, frozen here and carried explicitly.
    #
    # It cannot be read back out of snap["klines"]: that list is the model window, which is
    # CLOSED bars only, while the loop priced expectancy, the quality filters and the revision
    # ledger from the live FORMING bar. Those are different numbers, so pointing the downstream
    # gates at the snapshot's last close would silently reprice every decision rather than
    # freeze it.
    #
    # Taken from live_state, which is the same read the loop used to do itself - the difference
    # is that it now happens ONCE, at a known instant, instead of separately in each gate as
    # WebSocket callbacks keep moving the price underneath them.
    live_klines = live_state.get("klines") or []
    snap["decision_price"] = (
        float(live_klines[-1]["close"]) if live_klines else None)
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

    # P0-6. NESTED mutation. The outer proxy blocked exactly one thing; everything inside it
    # stayed writable, and in A/B both variants are handed the SAME nested objects - so a stray
    # write by the primary silently changed what the challenger was scored on.
    for label, attempt in (
        ("a nested dict", lambda: snap["order_flow"].__setitem__("cvd_1m", 999.0)),
        ("a nested list", lambda: snap["klines"].append({"time": 9, "close": 5_000.0})),
        ("a dict INSIDE a nested list", lambda: snap["klines"][0].__setitem__("close", 5_000.0)),
        ("a nested pop", lambda: snap["order_flow"].pop("cvd_1m", None)),
    ):
        try:
            attempt()
            raise AssertionError(f"{label} was still mutable - the freeze is one level deep")
        except (TypeError, AttributeError):
            checks += 1
            print(f"  PASS  {label} cannot be mutated either")
    check(snap["order_flow"]["cvd_1m"] == -1.63 and len(snap["klines"]) == 2,
          "and the values survived every attempt unchanged")

    # Reading must still work exactly as before, or the freeze has broken its consumers.
    check(snap["klines"][-1]["close"] == 101.0 and len(snap["klines"][-2:]) == 2,
          "indexing, slicing and nested reads are unaffected")

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

    # ---- P0-6: the price the POST-MODEL gates use is frozen too -------------------------
    fresh = {
        "klines": [{"time": 1, "close": 100.0}, {"time": 2, "close": 101.0}],
        "order_flow": {"cvd_1m": -1.63, "spread_expansion_ratio": 1.0},
        "derivatives": {}, "sentiment": {}, "regime_info": {"regime": "RANGE"},
        "regime_model_weights": {}, "confidence_calibrators": {}, "signal_policy": {},
        "regime_calibration": {}, "feature_parity": {},
    }
    # The model window is CLOSED bars only; the live list also carries the forming bar. This
    # is exactly the production call, and the reason decision_price cannot be recovered from
    # snap["klines"] afterwards.
    closed_only = [{"time": 1, "close": 100.0}]
    frozen = build(fresh, 1_000, klines=closed_only)
    check(frozen["decision_price"] == 101.0,
          "the decision price is the LIVE price at freeze time, not the last CLOSED bar")
    check(frozen["klines"][-1]["close"] == 100.0,
          "while the model window stays closed-bar only - the two are different numbers, "
          "which is why the price has to be carried and not re-derived")

    # THE P0-6 RACE. Everything a post-model gate reads must be unchanged by a callback that
    # fires after the freeze - expectancy, the quality filters and the revision ledger all
    # used to re-read these from the live global once inference had already returned.
    fresh["klines"].append({"time": 3, "close": 5_000.0})
    fresh["order_flow"]["cvd_1m"] = 999.0
    fresh["order_flow"]["spread_expansion_ratio"] = 87.0
    check(frozen["decision_price"] == 101.0,
          "a candle arriving mid-decision does NOT reprice the gates")
    check(frozen["order_flow"]["spread_expansion_ratio"] == 1.0,
          "and a spread blow-out arriving mid-decision does not re-filter them")

    priced = build({**fresh, "klines": [{"time": 1, "close": 100.0},
                                        {"time": 2, "close": 101.0}],
                    "order_flow": {"cvd_1m": -1.63, "spread_expansion_ratio": 1.0},
                    "regime_info": {"regime": "RANGE"}}, 1_000, klines=closed_only)
    moved_price = build({**fresh, "klines": [{"time": 1, "close": 100.0},
                                             {"time": 2, "close": 102.0}],
                         "order_flow": {"cvd_1m": -1.63, "spread_expansion_ratio": 1.0},
                         "regime_info": {"regime": "RANGE"}}, 1_000, klines=closed_only)
    check(priced["decision_id"] != moved_price["decision_id"],
          "a different decision price is a DIFFERENT decision id, even with an identical "
          "closed-bar window - otherwise two differently-priced decisions would collide")

    empty = build({k: v for k, v in fresh.items() if k != "klines"}, 1_000)
    check(empty["decision_price"] is None,
          "with no price available it is None, never 0.0 - a zero would price expectancy "
          "against a free asset")

    # THE KEY LIST MUST TRACK ITS CONSUMERS. Routing the quality filters at this snapshot only
    # helps if it carries what they read; `.get()` on a missing key returns a default instead
    # of raising, and each of these defaults fails in a direction that looks like normal
    # operation - a permanently stale feed, a veto that never vetoes, a calm book in a crisis.
    server_src = (Path(__file__).resolve().parent / "server.py").read_text(encoding="utf-8")
    lines = server_src.splitlines()
    first = next(i for i, ln in enumerate(lines)
                 if ln.startswith("def apply_live_quality_filters("))
    # Bounded by the NEXT top-level def, not by a byte count. A fixed-size window silently
    # truncated the function and the check then passed while missing a key it reads.
    end = next((j for j in range(first + 1, len(lines))
                if lines[j].startswith(("def ", "async def ", "class "))), len(lines))
    body = chr(10).join(lines[first:end])
    check(end - first > 100,
          f"the whole function is scanned, not a truncated window ({end - first} lines)")
    # A FROZEN structure must not silently read as EMPTY. mappingproxy is not a dict subclass
    # and tuple is not a list, so the server's type guards rejected every frozen value and
    # returned {} / [] - no exception, no log, just an empty book and every gate that reads it
    # defaulting open. Pinned here because deep-freezing is what created the hazard.
    import server as _srv

    check(_srv._safe_dict(snap["order_flow"]).get("cvd_1m") == -1.63,
          "_safe_dict reads a FROZEN mapping rather than silently returning {}")
    check(len(_srv._safe_list(snap["klines"])) == 2,
          "_safe_list reads a FROZEN sequence rather than silently returning []")
    check(_srv._safe_dict({"a": 1}) == {"a": 1} and _srv._safe_dict(None) == {},
          "while a plain dict still passes and a non-mapping still degrades to {}")

    read_keys = set(re.findall(r'state\.get\("([a-z_]+)"', body))
    # Checked against the BUILT snapshot, not against SNAPSHOT_KEYS. Some keys (event_ts_ms,
    # decision_price, decision_id) are attached by build() directly rather than copied from
    # live state, and testing the key LIST would have failed on those while the actual property
    # - "the filter can read everything it asks for" - held.
    provided = set(frozen.keys())
    missing = sorted(read_keys - provided)
    check(not missing,
          f"every key apply_live_quality_filters reads is present on the snapshot "
          f"(missing: {missing})")

    print(f"\nDECISION SNAPSHOT SELFTEST: PASS ({checks} checks)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.parse_args()
    raise SystemExit(selftest())
