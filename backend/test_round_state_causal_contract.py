"""The round-state training join must be causal, and serving must accept what training writes.

Two defects lived here at once, and each hid the other:

  P0-02  the trainer joined the bar CONTAINING each snapshot. A decision at 12:30:15 was
         trained on the 12:30 bar, whose high/low/close/volume run through 12:30:59. Every
         training row saw the tail of its own minute.

  P0-01  the trainer stamped "<schema>-1000d" while the loader required equality with
         "<schema>". A retrain could succeed in full and still leave the head UNAVAILABLE,
         which reads to an operator as missing data rather than a version contract.

Together they meant: a leaked model, which could never load, so nobody found out it leaked.

Neither is caught by an artifact manifest. A manifest certifies WHICH dataset and code produced
an artifact - faithfully, including a leaked dataset and an unloadable version. Only a join rule
and a compatibility rule can catch these, so they are asserted here.

    python backend/test_round_state_causal_contract.py
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from train_round_state_heads import (
    BAR_INTERVAL_MS, bar_available_from_ms, causal_feature_ts_ms,
    ARTIFACT_SCHEMA_VERSION, VERSION, _join_keepers,
)
from round_state_panel import (
    EXPECTED_SCHEMA_VERSION, schema_version_of, version_is_compatible,
)

_OK = True


def check(condition: bool, label: str) -> None:
    global _OK
    if condition:
        print(f"  PASS  {label}")
    else:
        _OK = False
        print(f"  FAIL  {label}")


def main() -> int:
    minute = 1_754_200_000_000 // BAR_INTERVAL_MS * BAR_INTERVAL_MS  # an exact bar boundary

    print("causal join")
    # 12:30:15 -> the 12:30 bar closes at 12:31:00, AFTER the decision. Must not be used.
    mid = minute + 15_000
    check(causal_feature_ts_ms(mid) == minute - BAR_INTERVAL_MS,
          "a mid-minute snapshot uses the PREVIOUS bar, not the one containing it")
    check(bar_available_from_ms(causal_feature_ts_ms(mid)) <= mid,
          "the joined bar had CLOSED before the snapshot - the defining property")
    check(bar_available_from_ms(minute) > mid,
          "...and the containing bar had NOT closed, which is why it is excluded")

    # Exactly on the boundary: the previous bar closed at exactly this instant, so it is
    # admissible; the bar opening now has not closed.
    check(causal_feature_ts_ms(minute) == minute - BAR_INTERVAL_MS,
          "on an exact boundary the just-closed bar is used")
    check(bar_available_from_ms(causal_feature_ts_ms(minute)) <= minute,
          "...and it is admissible because it closed at exactly that instant")

    # Vectorised, because the trainer passes a Series.
    series = pd.Series([minute + 1, minute + 59_999, minute + BAR_INTERVAL_MS], dtype="int64")
    got = causal_feature_ts_ms(series)
    check(list(got) == [minute - BAR_INTERVAL_MS, minute - BAR_INTERVAL_MS, minute],
          "the rule is applied elementwise to a Series, as the trainer uses it")
    check(bool((pd.Series(got).apply(bar_available_from_ms) <= series).all()),
          "no row in a vectorised join can see an unclosed bar")

    print("\nthe join actually performed")
    snapshots = pd.DataFrame({"snapshot_ts": [minute + 15_000, minute + 45_000]})
    matrix = pd.DataFrame({
        "ts_ms": [minute - BAR_INTERVAL_MS, minute],
        "keeper_value": [111.0, 999.0],   # 999.0 is the future; it must never appear
    })
    joined = _join_keepers(snapshots, "snapshot_ts", matrix)
    check(len(joined) == 2, "both snapshots join a bar")
    check(set(joined["keeper_value"]) == {111.0},
          "the joined value is the PREVIOUS bar's - the future bar's 999.0 never appears")
    check(bool((joined["feature_ts_ms"] + BAR_INTERVAL_MS <= joined["snapshot_ts"]).all()),
          "every joined row satisfies bar_close <= snapshot_ts")

    # NEGATIVE TEST: the old rule, re-created, must fail the same assertion. A guard that has
    # never been shown to reject the offending behaviour is not a guard.
    leaked = snapshots["snapshot_ts"].astype("int64") // BAR_INTERVAL_MS * BAR_INTERVAL_MS
    check(bool((leaked + BAR_INTERVAL_MS > snapshots["snapshot_ts"]).all()),
          "the OLD rule is demonstrably non-causal - the guard can fail")

    print("\nversion contract")
    check(schema_version_of("2026-08-03-round-state-shadow-v2-1000d")
          == "2026-08-03-round-state-shadow-v2",
          "a training-window suffix is stripped to the schema")
    check(schema_version_of(ARTIFACT_SCHEMA_VERSION) == ARTIFACT_SCHEMA_VERSION,
          "a bare schema string is unchanged")
    check(ARTIFACT_SCHEMA_VERSION == EXPECTED_SCHEMA_VERSION,
          "trainer and loader agree on the schema they share")
    check(version_is_compatible(VERSION),
          "WHAT THE TRAINER ACTUALLY STAMPS IS LOADABLE - the P0-01 regression")
    check(version_is_compatible(f"{ARTIFACT_SCHEMA_VERSION}-nad"),
          "the 'na' window tag (BTC_HISTORICAL_DAYS unset) also loads")
    for window in ("1", "30", "365", "1000"):
        if not version_is_compatible(f"{ARTIFACT_SCHEMA_VERSION}-{window}d"):
            check(False, f"a {window}d artifact should load")
            break
    else:
        check(True, "every training window loads - the window is metadata, not compatibility")
    check(not version_is_compatible("2026-07-02-round-state-shadow-v1-1000d"),
          "a v1 (LEAKED) artifact is REFUSED - the fix cannot be served around")
    check(not version_is_compatible("some-other-model-v2"),
          "an unrelated schema is still refused")
    check(not version_is_compatible(None), "a missing version is refused")

    print()
    if _OK:
        print("ROUND-STATE CAUSAL CONTRACT: PASS")
        return 0
    print("ROUND-STATE CAUSAL CONTRACT: FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
