"""Freeze-aware artifact pinning for COMPLETE_TRADE_FORECAST_V1 serving.

The three complete-trade loaders (BTC path, share path, execution heads) each watch their artifact's
modification time and hot-reload when it changes. That is correct for local iteration and
catastrophic during an evidence run: the evidence clock must describe ONE immutable model bundle,
and a mid-run reload silently splices two different models into a single measurement period. The
forecasts before and after look identical in the ledger.

With `BTC_FREEZE_MODEL=1` this module:

    * pins the artifact hash on first successful load;
    * refuses a changed file rather than adopting it;
    * raises a visible, latched alert instead of failing quietly.

The refusal direction matters. On detecting a change, serving keeps the PINNED bundle and reports
the conflict - it does not fall back to "no model", because that would let anyone invalidate a
running evidence collection by touching a file.

    python backend/trade_forecast/freeze_guard.py --selftest
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def freeze_enabled() -> bool:
    """Read live rather than at import: start.bat sets this before the server boots."""
    return str(os.environ.get("BTC_FREEZE_MODEL") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


class ArtifactPin:
    """Per-artifact freeze state. One instance per serving module."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.pinned_hash: str | None = None
        self.pinned_at: float | None = None
        self.violation: str | None = None
        self.violation_ts: float | None = None
        self.violation_count = 0

    def pin(self, artifact_hash: str | None) -> None:
        if self.pinned_hash is None and artifact_hash:
            self.pinned_hash = str(artifact_hash)
            self.pinned_at = time.time()

    def check(self, artifact_hash: str | None) -> bool:
        """True when the artifact may be adopted; False when it must be refused.

        Unfrozen serving always adopts. Frozen serving adopts only the pinned hash."""
        if not freeze_enabled():
            return True
        if self.pinned_hash is None:
            self.pin(artifact_hash)
            return True
        if artifact_hash and str(artifact_hash) != self.pinned_hash:
            self.violation = (
                f"{self.name}: artifact changed under BTC_FREEZE_MODEL=1 "
                f"(pinned {self.pinned_hash[:12]}..., found {str(artifact_hash)[:12]}...). "
                f"REFUSED - serving continues on the pinned bundle."
            )
            self.violation_ts = time.time()
            self.violation_count += 1
            print(f"[trade-forecast] FROZEN ARTIFACT VIOLATION - {self.violation}", flush=True)
            return False
        return True

    def status(self) -> dict[str, Any]:
        return {
            "frozen": freeze_enabled(),
            "pinned_artifact_hash": self.pinned_hash,
            "pinned_at": self.pinned_at,
            # Latched: a violation stays visible after the file is reverted, because the run's
            # integrity is already in question and a self-clearing alert hides that.
            "freeze_violation": self.violation,
            "freeze_violation_ts": self.violation_ts,
            "freeze_violation_count": self.violation_count,
        }


def selftest() -> int:
    ok = True

    def chk(cond: bool, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok &= bool(cond)

    previous = os.environ.get("BTC_FREEZE_MODEL")
    try:
        os.environ["BTC_FREEZE_MODEL"] = "0"
        pin = ArtifactPin("share")
        chk(pin.check("aaa") and pin.check("bbb"), "unfrozen serving hot-reloads freely")
        chk(pin.violation is None, "unfrozen reload raises no violation")

        os.environ["BTC_FREEZE_MODEL"] = "1"
        pin = ArtifactPin("share")
        chk(pin.check("aaa"), "frozen serving adopts the FIRST artifact it sees")
        chk(pin.pinned_hash == "aaa", "first artifact hash is pinned")
        chk(pin.check("aaa"), "the pinned artifact continues to load")
        chk(not pin.check("bbb"), "a CHANGED artifact is refused under freeze")
        chk(pin.violation_count == 1 and "REFUSED" in (pin.violation or ""),
            "refusal raises a visible alert")
        # Reverting the file must not erase the fact that the run was disturbed.
        pin.check("aaa")
        chk(pin.violation is not None, "violation is LATCHED after the artifact reverts")
        chk(pin.status()["pinned_artifact_hash"] == "aaa",
            "status reports the pinned hash for the evidence record")

        os.environ["BTC_FREEZE_MODEL"] = "1"
        pin2 = ArtifactPin("btc")
        chk(pin2.check(None), "a missing hash cannot pin, and does not crash serving")
        chk(pin2.pinned_hash is None, "no hash means nothing to pin")

        for value, want in (("1", True), ("true", True), ("on", True),
                            ("0", False), ("", False), ("no", False)):
            os.environ["BTC_FREEZE_MODEL"] = value
            if freeze_enabled() is not want:
                chk(False, f"freeze flag parsing: {value!r}")
                break
        else:
            chk(True, "freeze flag parses 1/true/on and rejects 0/''/no")
    finally:
        if previous is None:
            os.environ.pop("BTC_FREEZE_MODEL", None)
        else:
            os.environ["BTC_FREEZE_MODEL"] = previous

    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
