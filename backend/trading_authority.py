"""Fail-closed, expiring capability gate for any future real-money adapter.

Nothing in this repository places real orders. This module exists so a future adapter cannot
inherit authority from a boolean. A grant is venue- and strategy-scoped, notional-capped,
release-bound, expiring, authenticated with the same control-token policy as the HTTP control
plane, and written to an append-only operator audit log.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from control_auth import configured_token, token_is_usable

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

NOT_AUTHORIZED = "REAL_TRADING_NOT_AUTHORIZED"
AUTHORIZED = "REAL_TRADING_AUTHORIZED"

AUTH_ENV = "BTC_REAL_TRADING_AUTHORIZED"
AUTH_PHRASE = "I_ACCEPT_REAL_MONEY_RISK"
TOKEN_ENV = "BTC_CONTROL_TOKEN"
RELEASE_ENV = "BTC_RELEASE_ID"
MAX_GRANT_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class ArmGrant:
    venue: str
    strategy_id: str
    maximum_notional: float
    expires_at_s: float
    operator_identity: str
    release_id: str
    armed_at_s: float
    token_fingerprint: str


_grants: dict[str, ArmGrant] = {}


class RealTradingRefused(RuntimeError):
    """Raised whenever a real-money order lacks an exact active grant."""


def _audit_path() -> Path:
    configured = (os.getenv("BTC_AUTHORITY_AUDIT_LOG") or "").strip()
    if configured:
        return Path(configured).resolve()
    root = Path(__file__).resolve().parents[1]
    data = Path(os.getenv("BTC_DATA_DIR") or root / "data")
    return data / "trading_authority_audit.jsonl"


def _audit(event: str, payload: dict) -> None:
    path = _audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "event": event,
        "timestamp_s": time.time(),
        **payload,
    }
    # Append-only, flushed and fsynced before authority is returned to the caller.
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _active_grant(venue: str) -> ArmGrant | None:
    key = str(venue).strip().lower()
    grant = _grants.get(key)
    if grant is not None and grant.expires_at_s <= time.time():
        _grants.pop(key, None)
        _audit("EXPIRED", {"venue": key, "release_id": grant.release_id})
        return None
    return grant


def arm(
    *,
    venue: str,
    strategy_id: str,
    maximum_notional: float,
    expires_at_s: float,
    operator_identity: str,
    release_id: str,
    control_token: str,
) -> ArmGrant:
    """Create one short-lived capability after authenticating every bound field."""
    venue_key = str(venue).strip().lower()
    strategy = str(strategy_id).strip()
    operator = str(operator_identity).strip()
    release = str(release_id).strip()
    if not all((venue_key, strategy, operator, release)):
        raise RealTradingRefused(
            "venue, strategy_id, operator_identity and release_id are required"
        )
    if os.environ.get(AUTH_ENV) != AUTH_PHRASE:
        raise RealTradingRefused(f"{AUTH_ENV} acceptance phrase is absent")
    configured = configured_token()
    usable, reason = token_is_usable(configured, env_name=TOKEN_ENV)
    if not usable:
        raise RealTradingRefused(reason)
    supplied = str(control_token or "")
    if not hmac.compare_digest(supplied, configured or ""):
        raise RealTradingRefused("control token authentication failed")
    try:
        limit = float(maximum_notional)
        expiry = float(expires_at_s)
    except (TypeError, ValueError) as exc:
        raise RealTradingRefused("notional and expiration must be numeric") from exc
    now = time.time()
    if not math.isfinite(limit) or limit <= 0:
        raise RealTradingRefused("maximum_notional must be finite and positive")
    if not math.isfinite(expiry) or not now < expiry <= now + MAX_GRANT_SECONDS:
        raise RealTradingRefused(
            f"expiration must be within the next {MAX_GRANT_SECONDS} seconds"
        )
    expected_release = (os.getenv(RELEASE_ENV) or "").strip()
    if expected_release and release != expected_release:
        raise RealTradingRefused(
            f"release mismatch: requested {release!r}, running {expected_release!r}"
        )
    grant = ArmGrant(
        venue=venue_key,
        strategy_id=strategy,
        maximum_notional=limit,
        expires_at_s=expiry,
        operator_identity=operator,
        release_id=release,
        armed_at_s=now,
        token_fingerprint=_token_fingerprint(configured or ""),
    )
    _audit("ARM", asdict(grant))
    _grants[venue_key] = grant
    return grant


def disarm(venue: str | None = None, *, reason: str = "operator") -> None:
    if venue is None:
        removed = list(_grants.values())
        _grants.clear()
    else:
        grant = _grants.pop(str(venue).strip().lower(), None)
        removed = [grant] if grant is not None else []
    for grant in removed:
        _audit(
            "DISARM",
            {
                "venue": grant.venue,
                "strategy_id": grant.strategy_id,
                "release_id": grant.release_id,
                "reason": str(reason),
            },
        )


def authorization_gaps(
    venue: str = "binance",
    *,
    strategy_id: str | None = None,
    notional: float | None = None,
) -> list[str]:
    """Return every unmet condition for the exact proposed order."""
    gaps: list[str] = []
    if os.environ.get(AUTH_ENV) != AUTH_PHRASE:
        gaps.append(f"{AUTH_ENV} is not set to the exact acceptance phrase")
    token = configured_token()
    usable, reason = token_is_usable(token, env_name=TOKEN_ENV)
    if not usable:
        gaps.append(reason)
    grant = _active_grant(venue)
    if grant is None:
        gaps.append(f"venue {venue!r} has no active capability grant")
        return gaps
    if strategy_id is None:
        gaps.append("strategy_id is required")
    elif str(strategy_id) != grant.strategy_id:
        gaps.append(
            f"strategy {strategy_id!r} is not authorized by grant "
            f"{grant.strategy_id!r}"
        )
    if notional is None:
        gaps.append("order notional is required")
    else:
        try:
            order_notional = float(notional)
        except (TypeError, ValueError):
            order_notional = math.nan
        if not math.isfinite(order_notional) or order_notional <= 0:
            gaps.append("order notional must be finite and positive")
        elif order_notional > grant.maximum_notional:
            gaps.append(
                f"order notional {order_notional} exceeds grant "
                f"{grant.maximum_notional}"
            )
    running_release = (os.getenv(RELEASE_ENV) or "").strip()
    if running_release and running_release != grant.release_id:
        gaps.append("running release differs from the armed release")
    return gaps


def state(
    venue: str = "binance",
    *,
    strategy_id: str | None = None,
    notional: float | None = None,
) -> str:
    return (
        AUTHORIZED
        if not authorization_gaps(
            venue, strategy_id=strategy_id, notional=notional
        )
        else NOT_AUTHORIZED
    )


def assert_may_place_real_order(
    venue: str,
    *,
    strategy_id: str,
    notional: float,
) -> ArmGrant:
    """Call immediately before submission; authorization is re-evaluated every time."""
    gaps = authorization_gaps(
        venue, strategy_id=strategy_id, notional=notional
    )
    if gaps:
        raise RealTradingRefused(
            f"{NOT_AUTHORIZED}: refusing {venue!r}/{strategy_id!r} "
            f"notional={notional}: {gaps}"
        )
    grant = _active_grant(venue)
    if grant is None:  # defensive: authorization_gaps already checked this
        raise RealTradingRefused(NOT_AUTHORIZED)
    _audit(
        "AUTHORIZE_ORDER",
        {
            "venue": grant.venue,
            "strategy_id": strategy_id,
            "notional": float(notional),
            "operator_identity": grant.operator_identity,
            "release_id": grant.release_id,
        },
    )
    return grant


def status() -> dict:
    grants = {
        venue: {
            **asdict(grant),
            "seconds_remaining": max(0.0, grant.expires_at_s - time.time()),
        }
        for venue in list(_grants)
        if (grant := _active_grant(venue)) is not None
    }
    return {
        "runtime_state": NOT_AUTHORIZED,
        "real_orders_authorized": False,
        "active_grants": grants,
        "reason": (
            "No real-order adapter is implemented. Grants are capability checks for a "
            "future separately reviewed adapter."
        ),
    }


def selftest() -> int:
    import tempfile

    ok = True

    def check(condition: bool, message: str) -> None:
        nonlocal ok
        print(f"  {'PASS' if condition else 'FAIL'}  {message}")
        ok = ok and condition

    saved = {
        name: os.environ.get(name)
        for name in (AUTH_ENV, TOKEN_ENV, RELEASE_ENV, "BTC_AUTHORITY_AUDIT_LOG")
    }
    with tempfile.TemporaryDirectory() as root:
        try:
            audit = Path(root) / "authority.jsonl"
            os.environ["BTC_AUTHORITY_AUDIT_LOG"] = str(audit)
            os.environ[AUTH_ENV] = AUTH_PHRASE
            os.environ[TOKEN_ENV] = "z" * 40
            os.environ[RELEASE_ENV] = "release-test"
            disarm(reason="selftest_reset")

            os.environ[TOKEN_ENV] = "x"
            check(
                any(
                    "shorter" in gap
                    for gap in authorization_gaps(
                        "binance", strategy_id="s1", notional=10
                    )
                ),
                "authority uses the same minimum token policy as the control API",
            )
            os.environ[TOKEN_ENV] = "z" * 40

            for label, overrides in (
                ("wrong secret", {"control_token": "q" * 40}),
                ("unbounded expiry", {"expires_at_s": time.time() + 7200}),
                ("invalid notional", {"maximum_notional": 0}),
            ):
                args = {
                    "venue": "binance",
                    "strategy_id": "s1",
                    "maximum_notional": 100,
                    "expires_at_s": time.time() + 60,
                    "operator_identity": "operator-1",
                    "release_id": "release-test",
                    "control_token": "z" * 40,
                    **overrides,
                }
                try:
                    arm(**args)
                    check(False, f"{label} must be refused")
                except RealTradingRefused:
                    check(True, f"{label} is refused")

            grant = arm(
                venue="binance",
                strategy_id="s1",
                maximum_notional=100,
                expires_at_s=time.time() + 60,
                operator_identity="operator-1",
                release_id="release-test",
                control_token="z" * 40,
            )
            check(grant.maximum_notional == 100, "a complete capability can arm")
            check(
                state("binance", strategy_id="s1", notional=50) == AUTHORIZED,
                "the exact strategy and bounded notional are authorized",
            )
            check(
                state("binance", strategy_id="s2", notional=50) == NOT_AUTHORIZED,
                "another strategy is refused",
            )
            check(
                state("binance", strategy_id="s1", notional=101) == NOT_AUTHORIZED,
                "notional above the grant is refused",
            )
            assert_may_place_real_order(
                "binance", strategy_id="s1", notional=50
            )
            rows = [
                json.loads(line)
                for line in audit.read_text(encoding="utf-8").splitlines()
            ]
            check(
                {"ARM", "AUTHORIZE_ORDER"}.issubset(
                    {row["event"] for row in rows}
                ),
                "arming and order authorization are durably audited",
            )
            check(
                all("control_token" not in row for row in rows),
                "the audit never stores the control secret",
            )
            disarm("binance", reason="selftest")
            check(
                state("binance", strategy_id="s1", notional=50)
                == NOT_AUTHORIZED,
                "disarm revokes authority immediately",
            )
        finally:
            _grants.clear()
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    print("TRADING AUTHORITY", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
