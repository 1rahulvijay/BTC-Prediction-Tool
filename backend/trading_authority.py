"""The single chokepoint any real-money order must pass. Refuses by default.

WHY THIS EXISTS
    `real_orders_disabled: True` was a hardcoded literal in a status dict. It reported a fact; it
    did not ENFORCE one. Nothing called it, nothing could fail because of it, and a future real
    order path could be added without ever contradicting it - the dict would keep saying True
    while orders went out.

    A safety property that no code path consults is documentation, not a control.

THE DEFAULT IS REFUSAL, AND IT IS NOT REACHABLE FROM THE API
    The default runtime state is REAL_TRADING_NOT_AUTHORIZED. Authorization requires ALL of:

      1. BTC_REAL_TRADING_AUTHORIZED=I_ACCEPT_REAL_MONEY_RISK   (exact, not "1"/"true")
      2. BTC_CONTROL_TOKEN configured, so the control plane is itself authenticated
      3. an explicit in-process arm() call naming the venue

    Requirement 1 is deliberately a long exact phrase: an operator cannot enable real money by
    setting a plausible-looking flag to 1, and a stray `=true` in an env file cannot do it.
    Requirement 3 cannot be satisfied over HTTP - no endpoint calls arm(), and a test asserts
    that no route handler references it.

    Nothing in this repository is authorized today. This module exists so that the day something
    is, it must pass through here and be logged, rather than inheriting silence.

    python backend/trading_authority.py --selftest
"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

NOT_AUTHORIZED = "REAL_TRADING_NOT_AUTHORIZED"
AUTHORIZED = "REAL_TRADING_AUTHORIZED"

AUTH_ENV = "BTC_REAL_TRADING_AUTHORIZED"
AUTH_PHRASE = "I_ACCEPT_REAL_MONEY_RISK"
TOKEN_ENV = "BTC_CONTROL_TOKEN"

_armed_venues: set[str] = set()


class RealTradingRefused(RuntimeError):
    """Raised whenever a real-money order is attempted without full authorization."""


def arm(venue: str) -> None:
    """Explicit in-process authorization for one venue. Never called by any route handler."""
    _armed_venues.add(str(venue))


def disarm(venue: str | None = None) -> None:
    if venue is None:
        _armed_venues.clear()
    else:
        _armed_venues.discard(str(venue))


def state(venue: str = "binance") -> str:
    return AUTHORIZED if authorization_gaps(venue) == [] else NOT_AUTHORIZED


def authorization_gaps(venue: str = "binance") -> list[str]:
    """Every unmet requirement. Empty means real trading is authorized for `venue`."""
    gaps = []
    if os.environ.get(AUTH_ENV) != AUTH_PHRASE:
        gaps.append(f"{AUTH_ENV} is not set to the exact acceptance phrase")
    if not (os.environ.get(TOKEN_ENV) or "").strip():
        gaps.append(f"{TOKEN_ENV} is unset, so the control plane is not authenticated")
    if str(venue) not in _armed_venues:
        gaps.append(f"venue '{venue}' has not been armed in-process")
    return gaps


def assert_may_place_real_order(venue: str = "binance", *, quantity: float = 0.0) -> None:
    """Call this immediately before ANY real order. Raises unless fully authorized.

    Placed at the order site rather than at startup on purpose: a startup check can be true at
    boot and false by the time an order is sent."""
    gaps = authorization_gaps(venue)
    if gaps:
        raise RealTradingRefused(
            f"{NOT_AUTHORIZED}: refusing a real order on '{venue}' "
            f"(quantity={quantity}). Unmet: {gaps}")


def status() -> dict:
    return {
        "runtime_state": state(),
        "real_orders_authorized": state() == AUTHORIZED,
        "armed_venues": sorted(_armed_venues),
        "gaps": authorization_gaps(),
    }


def selftest() -> int:
    ok = True

    def chk(cond: object, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok = ok and bool(cond)

    saved = {k: os.environ.get(k) for k in (AUTH_ENV, TOKEN_ENV)}
    try:
        print("the default is refusal")
        os.environ.pop(AUTH_ENV, None)
        os.environ.pop(TOKEN_ENV, None)
        disarm()
        chk(state() == NOT_AUTHORIZED, f"default runtime state is {NOT_AUTHORIZED}")
        chk(len(authorization_gaps()) == 3, f"all three requirements unmet ({authorization_gaps()})")
        try:
            assert_may_place_real_order("binance", quantity=1.0)
            chk(False, "a real order must be refused by default")
        except RealTradingRefused as exc:
            chk(NOT_AUTHORIZED in str(exc), "and the refusal names the runtime state")

        print("no single flag is sufficient")
        os.environ[AUTH_ENV] = AUTH_PHRASE
        chk(state() == NOT_AUTHORIZED, "the acceptance phrase alone does not authorize")
        os.environ[TOKEN_ENV] = "z" * 40
        chk(state() == NOT_AUTHORIZED, "phrase + token still does not authorize without arm()")
        arm("binance")
        chk(state() == AUTHORIZED, "all three together authorize")

        print("a plausible-looking value does not authorize")
        for weak in ("1", "true", "TRUE", "yes", "enabled", "I_ACCEPT_REAL_MONEY_RISK "):
            os.environ[AUTH_ENV] = weak
            chk(state() == NOT_AUTHORIZED, f"{weak!r} does not authorize")
        os.environ[AUTH_ENV] = AUTH_PHRASE

        print("authorization is per venue")
        chk(state("binance") == AUTHORIZED, "the armed venue is authorized")
        chk(state("polymarket") == NOT_AUTHORIZED, "an unarmed venue is NOT")
        try:
            assert_may_place_real_order("polymarket", quantity=1.0)
            chk(False, "an unarmed venue must refuse")
        except RealTradingRefused:
            chk(True, "and refuses at the order site")

        print("disarming takes authority away again")
        disarm("binance")
        chk(state() == NOT_AUTHORIZED, "after disarm, refusal resumes")
    finally:
        disarm()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print(f"\nNOTHING IN THIS REPOSITORY IS AUTHORIZED. Current state: {state()}")
    print("TRADING AUTHORITY", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
