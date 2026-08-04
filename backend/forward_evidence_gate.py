"""Refuse promotion and online adaptation while forward evidence is dark.

WHY THIS IS A GATE AND NOT A DASHBOARD TILE
    0/5 required recorders are advancing. With no forward sample there is no untouched evidence,
    no new execution data, and no way to tell decay from a bug - yet promotion, threshold
    adaptation and "auto-learning" would all still run, adjusting a live model on the strength of
    a frozen history it has already seen.

    Adapting on stale evidence is not a smaller version of adapting on fresh evidence. It is
    fitting the past twice and calling the second pass learning.

    python backend/forward_evidence_gate.py --selftest
    python backend/forward_evidence_gate.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: Recorders that must be advancing before any adaptation is permitted. `binance_l2_recorder`
#: is excluded: it has never run, so requiring it would make the gate permanently closed for a
#: reason unrelated to whether forward evidence exists.
REQUIRED = ("live_btc_updown_recorder.py", "multi_venue_recorder.py")

#: A writer holding the store is evidence of a live process, not a failure.
HEALTHY = ("ADVANCING", "LOCKED_BY_WRITER")

BLOCKED_ACTIONS = ("promote_challenger", "threshold_adaptation", "online_relearn",
                   "regime_weight_update", "confidence_recalibration")


def evidence_status(probe=None) -> dict:
    """Per-recorder health for the REQUIRED set, plus the overall verdict."""
    if probe is None:
        from recorder_health import probe as probe
    rows = {}
    for name in REQUIRED:
        try:
            result = probe(name)
        except Exception as exc:
            result = {"status": "PROBE_FAILED", "detail": str(exc)[:120]}
        rows[name] = result
    advancing = [n for n, r in rows.items() if r.get("status") in HEALTHY]
    ok = len(advancing) == len(REQUIRED)
    return {
        "forward_evidence": "ADVANCING" if ok else "DARK",
        "advancing": advancing,
        "required": list(REQUIRED),
        "recorders": rows,
        "blocked_actions": [] if ok else list(BLOCKED_ACTIONS),
        "banner": None if ok else (
            f"FORWARD RESEARCH PAUSED - {len(advancing)}/{len(REQUIRED)} required recorders "
            f"advancing"),
    }


def may_adapt(action: str, status: dict | None = None) -> tuple[bool, str]:
    """Fail CLOSED: an unrecognised action is refused while evidence is dark."""
    status = status if status is not None else evidence_status()
    if status["forward_evidence"] == "ADVANCING":
        return True, "forward evidence is advancing"
    return False, (f"{action} refused: {status['banner']}. Adapting a live model on a frozen "
                   f"history it has already seen is fitting the past twice.")


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    def fake(mapping):
        return lambda name: {"status": mapping.get(name, "NEVER_RAN")}

    all_ok = evidence_status(fake({n: "ADVANCING" for n in REQUIRED}))
    check(all_ok["forward_evidence"] == "ADVANCING", "all recorders advancing -> ADVANCING")
    check(all_ok["blocked_actions"] == [], "nothing is blocked when evidence is live")
    check(may_adapt("promote_challenger", all_ok)[0], "promotion is permitted then")

    dark = evidence_status(fake({}))
    check(dark["forward_evidence"] == "DARK", "no recorder advancing -> DARK")
    check(set(dark["blocked_actions"]) == set(BLOCKED_ACTIONS),
          "every adaptive action is blocked while evidence is dark")
    for action in BLOCKED_ACTIONS:
        allowed, _ = may_adapt(action, dark)
        assert not allowed, f"{action} was permitted while dark"
    checks += 1
    print("  PASS  promotion, thresholds, relearn, regime weights and recalibration all refused")

    partial = evidence_status(fake({REQUIRED[0]: "ADVANCING"}))
    check(partial["forward_evidence"] == "DARK",
          "PARTIAL coverage is still DARK - one live recorder is not a forward sample")

    locked = evidence_status(fake({n: "LOCKED_BY_WRITER" for n in REQUIRED}))
    check(locked["forward_evidence"] == "ADVANCING",
          "a store held by a WRITER counts as live - a held lock is evidence of a process")

    check(may_adapt("something_new", dark)[0] is False,
          "an UNRECOGNISED action is refused too - the gate fails closed")
    check(dark["banner"] and "FORWARD RESEARCH PAUSED" in dark["banner"],
          "a banner is produced for the UI, naming the reason")

    check(evidence_status(lambda n: (_ for _ in ()).throw(RuntimeError("boom")))
          ["forward_evidence"] == "DARK",
          "a probe that RAISES leaves the gate closed, never open")

    print(f"\nFORWARD EVIDENCE GATE SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    status = evidence_status()
    print("=" * 88)
    print("FORWARD EVIDENCE GATE")
    print("=" * 88)
    for name, row in status["recorders"].items():
        print(f"  {name:<32} {row.get('status'):<18} rows={row.get('rows', '?')}")
    print(f"\n  forward_evidence: {status['forward_evidence']}")
    if status["banner"]:
        print(f"  {status['banner']}")
        print(f"  blocked: {', '.join(status['blocked_actions'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
