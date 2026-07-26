"""Blueprint §31.2 - enforce head health in the decision path.

`monitoring/head_health.py` measures, from live outcomes, what each head is still allowed to do:

    USABLE            may price, may rank
    CALIBRATION_ONLY  may RANK, may NOT supply fair value
    DISABLED_NO_SKILL may do neither
    SHADOW / INSUFFICIENT_DATA / DRIFTED   likewise restricted

Until now nothing read those permissions, so a head measured as unable to price could still price.
This module is the reader. It is deliberately tiny and fail-open-with-a-reason: a missing report
must not take the app down, but it must also never silently look like a passing grade.

WHY THIS MATTERS MORE THAN IT LOOKS
    `BTC_ENABLE_PAPER_BET=1` is an operator override. Without this check, that override also
    re-enables betting on a probability the live data says cannot price - which is exactly the
    failure the lockdown existed to prevent. With it, the override can only act on a head that
    currently measures as USABLE. The switch stops being a way to overrule the evidence.

    python backend/head_permissions.py            # print current permissions
    python backend/head_permissions.py --selftest
"""
from __future__ import annotations

import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
REPORT = os.path.join(DATA, "research", "head_health", "head_health.json")

# A report older than this is not evidence about today's models.
MAX_REPORT_AGE_S = 14 * 24 * 3600

# Enforcement is ON by default. Set BTC_ENFORCE_HEAD_HEALTH=0 to observe-only (logged, not silent).
ENFORCED = os.environ.get("BTC_ENFORCE_HEAD_HEALTH", "1") != "0"

_CACHE = {"ts": 0.0, "val": None}
_CACHE_TTL_S = 60.0


def _load():
    now = time.time()
    if _CACHE["val"] is not None and now - _CACHE["ts"] < _CACHE_TTL_S:
        return _CACHE["val"]
    _CACHE["ts"] = now
    try:
        with open(REPORT, encoding="utf-8") as fh:
            rep = json.load(fh)
        age = now - os.path.getmtime(REPORT)
        rep["_age_s"] = age
        rep["_stale"] = age > MAX_REPORT_AGE_S
        _CACHE["val"] = rep
    except Exception:
        _CACHE["val"] = None
    return _CACHE["val"]


def head_state(head: str) -> tuple[str, dict]:
    """(state, permissions) for a head. Unknown/missing/stale -> UNKNOWN, permissive.

    Permissive on absence is deliberate: this module must not be able to disable the app by
    failing to find a file. The reason string carries WHY it is permissive so the caller can
    surface it rather than treating it as a pass.
    """
    rep = _load()
    if not rep:
        return "UNKNOWN", {"may_price": True, "may_rank": True,
                           "may_display_confidence": True,
                           "reason": "no head-health report; permissions not measured"}
    if rep.get("_stale"):
        return "STALE", {"may_price": True, "may_rank": True,
                         "may_display_confidence": True,
                         "reason": f"head-health report is {rep['_age_s']/86400:.0f}d old"}
    h = (rep.get("heads") or {}).get(head)
    if not h:
        return "UNKNOWN", {"may_price": True, "may_rank": True,
                           "may_display_confidence": True,
                           "reason": f"'{head}' absent from the head-health report"}
    perms = dict(h.get("permissions") or {})
    perms["reason"] = h.get("reason", "")
    return h.get("state", "UNKNOWN"), perms


def may_price(head: str) -> tuple[bool, str]:
    """May this head supply a FAIR VALUE (i.e. authorize money-relevant arithmetic)?"""
    state, perms = head_state(head)
    if not ENFORCED:
        return True, f"{head}={state} (enforcement disabled)"
    ok = bool(perms.get("may_price", True))
    return ok, f"{head}={state}: {perms.get('reason', '')}"


def may_rank(head: str) -> tuple[bool, str]:
    """May this head ORDER candidates (weaker permission than pricing)?"""
    state, perms = head_state(head)
    if not ENFORCED:
        return True, f"{head}={state} (enforcement disabled)"
    return bool(perms.get("may_rank", True)), f"{head}={state}"


def selftest() -> int:
    ok = True

    def chk(c, m):
        nonlocal ok
        print(f"  {'PASS' if c else 'FAIL'}  {m}")
        ok = ok and c

    print("head_permissions selftest")
    import tempfile
    global REPORT, _CACHE
    tmp = tempfile.mkdtemp()
    REPORT = os.path.join(tmp, "head_health.json")

    # missing report -> permissive, but says so
    _CACHE = {"ts": 0.0, "val": None}
    st, p = head_state("p_hold")
    chk(st == "UNKNOWN" and p["may_price"] and "not measured" in p["reason"],
        "missing report -> permissive WITH a reason (never a silent pass)")

    # a CALIBRATION_ONLY head may rank but may not price
    with open(REPORT, "w", encoding="utf-8") as fh:
        json.dump({"heads": {"p_hold": {
            "state": "CALIBRATION_ONLY", "reason": "ECE 0.0678 > 0.05",
            "permissions": {"may_price": False, "may_rank": True,
                            "may_display_confidence": False}}}}, fh)
    _CACHE = {"ts": 0.0, "val": None}
    can_price, why = may_price("p_hold")
    can_rank, _ = may_rank("p_hold")
    chk(not can_price, f"CALIBRATION_ONLY head may NOT price  ({why})")
    chk(can_rank, "CALIBRATION_ONLY head MAY still rank")

    # DISABLED_NO_SKILL may do neither
    with open(REPORT, "w", encoding="utf-8") as fh:
        json.dump({"heads": {"flip_risk": {
            "state": "DISABLED_NO_SKILL", "reason": "BSS -0.0130 <= 0",
            "permissions": {"may_price": False, "may_rank": False,
                            "may_display_confidence": False}}}}, fh)
    _CACHE = {"ts": 0.0, "val": None}
    chk(not may_price("flip_risk")[0] and not may_rank("flip_risk")[0],
        "DISABLED_NO_SKILL head may neither price nor rank")

    # a USABLE head is unrestricted
    with open(REPORT, "w", encoding="utf-8") as fh:
        json.dump({"heads": {"p_hold": {
            "state": "USABLE", "reason": "BSS +0.10, ECE 0.01",
            "permissions": {"may_price": True, "may_rank": True,
                            "may_display_confidence": True}}}}, fh)
    _CACHE = {"ts": 0.0, "val": None}
    chk(may_price("p_hold")[0], "USABLE head may price again (the gate re-opens on its own)")

    # a stale report is not evidence about today's models
    old = time.time() - (MAX_REPORT_AGE_S + 3600)
    os.utime(REPORT, (old, old))
    _CACHE = {"ts": 0.0, "val": None}
    st, p = head_state("p_hold")
    chk(st == "STALE" and "old" in p["reason"], "an aged report is reported STALE, not trusted")

    print("head-permissions:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    rep = _load()
    print(f"report: {REPORT}")
    print(f"enforcement: {'ON' if ENFORCED else 'OFF (observe only)'}")
    if not rep:
        print("  no report found - permissions are NOT measured (permissive)")
    else:
        print(f"  age: {rep.get('_age_s', 0)/3600:.1f}h  stale={rep.get('_stale')}")
        for head in (rep.get("heads") or {}):
            st, p = head_state(head)
            print(f"  {head:<14}{st:<20}price={p.get('may_price')}  rank={p.get('may_rank')}"
                  f"   {p.get('reason', '')[:44]}")
