"""Authentication for state-changing control endpoints. Fail-closed by construction.

WHY THIS EXISTS
    Every mutating endpoint on the paper-trading control plane - /start, /pause, /close-all,
    /positions/{id}/close and PATCH /strategies/{id} - had no authentication dependency at all,
    while the app served `allow_origins=["*"]`. Any web page the operator visited in the same
    browser could POST to the local API and start, pause or flatten the engine. No credentials
    were needed because none were required.

    The engine is paper-only today, so the blast radius is bounded. That is exactly why this is
    fixed now: the control-plane PATTERN is what would be reused for a live venue, and a pattern
    that was never authenticated does not become authenticated by being pointed at real money.

FAIL CLOSED, AND NEVER WITH A DEFAULT
    If BTC_CONTROL_TOKEN is unset, mutating endpoints are REFUSED (503) rather than opened. A
    default or generated token would be worse than none: it would look like protection while
    every deployment shared the same secret.

    The token belongs in the deployment environment - never in a unit file, never in Git, never
    in a default argument. Read-only GET endpoints are unaffected; they expose no authority.

    Comparison is constant-time, and the token is never logged, echoed or included in any error
    message. A refusal says which header was expected, never what the value should have been.

    python backend/control_auth.py --selftest
"""
from __future__ import annotations

import hmac
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TOKEN_ENV = "BTC_CONTROL_TOKEN"
HEADER_NAME = "X-Control-Token"
MIN_TOKEN_LENGTH = 16


def configured_token() -> str | None:
    """The deployment's token, or None when unset. Never returns a fallback."""
    value = os.environ.get(TOKEN_ENV) or ""
    value = value.strip()
    return value or None


def token_is_usable(token: str | None) -> tuple[bool, str]:
    if not token:
        return False, f"{TOKEN_ENV} is not set in the deployment environment"
    if len(token) < MIN_TOKEN_LENGTH:
        return False, f"{TOKEN_ENV} is shorter than {MIN_TOKEN_LENGTH} characters"
    return True, "ok"


def check(presented: str | None) -> tuple[bool, int, str]:
    """Return (allowed, http_status, reason). Reason never contains either token."""
    token = configured_token()
    usable, why = token_is_usable(token)
    if not usable:
        # 503, not 401: the endpoint is unavailable because the deployment is unconfigured.
        # Answering 401 would imply that some credential could work, and there is none.
        return False, 503, f"control plane disabled - {why}"
    if not presented:
        return False, 401, f"missing {HEADER_NAME} header"
    if not hmac.compare_digest(str(presented), str(token)):
        return False, 401, "control token rejected"
    return True, 200, "ok"


def require_control_token(x_control_token: str | None = None) -> None:
    """FastAPI dependency. Raises HTTPException unless the presented token matches."""
    from fastapi import HTTPException

    allowed, status, reason = check(x_control_token)
    if not allowed:
        raise HTTPException(status_code=status, detail=reason)


def allowed_origins() -> list[str]:
    """Explicit origins from BTC_ALLOWED_ORIGINS, defaulting to loopback only.

    `allow_origins=["*"]` let any site the operator had open issue cross-origin requests to the
    local API. Combined with unauthenticated control routes that was a drive-by control plane."""
    raw = os.environ.get("BTC_ALLOWED_ORIGINS", "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:8000", "http://127.0.0.1:8000",
    ]


def selftest() -> int:
    ok = True

    def chk(cond: object, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok = ok and bool(cond)

    original = os.environ.get(TOKEN_ENV)
    try:
        print("unconfigured deployments are CLOSED, not open")
        os.environ.pop(TOKEN_ENV, None)
        allowed, status, reason = check("anything")
        chk(not allowed and status == 503,
            f"with no token set, mutating endpoints are refused ({status})")
        chk("not set" in reason, f"and the reason names the missing variable ({reason})")
        chk(configured_token() is None, "there is NO default token to fall back to")

        print("a weak token is refused rather than accepted")
        os.environ[TOKEN_ENV] = "short"
        allowed, status, _ = check("short")
        chk(not allowed and status == 503, "a token under the minimum length disables the plane")

        print("a configured token gates correctly")
        secret = "x" * 40
        os.environ[TOKEN_ENV] = secret
        chk(check(secret)[0] is True, "the correct token is accepted")
        chk(check("wrong" * 8)[0] is False, "an incorrect token of equal length is rejected")
        chk(check(None)[0] is False, "a missing header is rejected")
        chk(check("")[0] is False, "an empty header is rejected")
        chk(check(secret[:-1])[0] is False, "a truncated token is rejected")
        chk(check(secret + "a")[0] is False, "an extended token is rejected")

        print("failures never disclose the secret")
        for presented in (None, "", "wrong", secret[:-1]):
            _allowed, _status, reason = check(presented)
            chk(secret not in reason,
                f"refusal for {presented!r} does not contain the token")

        print("CORS is not a wildcard")
        os.environ.pop("BTC_ALLOWED_ORIGINS", None)
        origins = allowed_origins()
        chk("*" not in origins, f"default origins are explicit, not '*' ({len(origins)} entries)")
        chk(all(o.startswith("http://localhost") or o.startswith("http://127.0.0.1")
                for o in origins), "and default to loopback only")
        os.environ["BTC_ALLOWED_ORIGINS"] = "https://a.example, https://b.example"
        chk(allowed_origins() == ["https://a.example", "https://b.example"],
            "an explicit list overrides the default")
    finally:
        os.environ.pop("BTC_ALLOWED_ORIGINS", None)
        if original is None:
            os.environ.pop(TOKEN_ENV, None)
        else:
            os.environ[TOKEN_ENV] = original

    print("\nCONTROL AUTH", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
