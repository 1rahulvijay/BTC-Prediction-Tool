"""The control plane must be closed by default, over real HTTP.

WHAT WAS WRONG
    /start, /pause, /close-all, /positions/{id}/close and PATCH /strategies/{id} had no
    authentication dependency, while the app served allow_origins=["*"]. Any page open in the
    operator's browser could POST to the local API and start, pause or flatten the engine.

    Paper-only today, so the blast radius was bounded - which is the reason to fix it now rather
    than later. The control-plane pattern is what gets reused for a live venue, and a pattern
    that was never authenticated does not become authenticated by being pointed at real money.

WHAT THIS ASSERTS
    Real requests through the ASGI stack, not a rules inspection. An earlier defect in this repo
    was a test that read code instead of executing it and passed while the function was broken.

    python backend/tests/test_control_plane_security.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_OK = True
TOKEN = "t" * 40

MUTATING = [
    ("post", "/api/binance-paper/start", None),
    ("post", "/api/binance-paper/pause", None),
    ("post", "/api/binance-paper/close-all", {"confirm": True}),
    ("post", "/api/binance-paper/positions/abc/close", {"confirm": True}),
    ("patch", "/api/binance-paper/strategies/abc", {"enabled": False}),
]


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from binance_paper.routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _call(client, method: str, path: str, body, headers=None):
    fn = getattr(client, method)
    if body is None:
        return fn(path, headers=headers or {})
    return fn(path, json=body, headers=headers or {})


def test_unconfigured_deployment_refuses_every_mutation() -> None:
    print("with no token configured, EVERY mutating route is refused")
    os.environ.pop("BTC_CONTROL_TOKEN", None)
    client = _client()
    for method, path, body in MUTATING:
        response = _call(client, method, path, body)
        chk(response.status_code == 503,
            f"{method.upper()} {path} -> {response.status_code} (expected 503)")


def test_missing_and_wrong_tokens_are_rejected() -> None:
    print("with a token configured, unauthenticated calls are rejected")
    os.environ["BTC_CONTROL_TOKEN"] = TOKEN
    client = _client()
    for method, path, body in MUTATING:
        no_header = _call(client, method, path, body)
        chk(no_header.status_code == 401,
            f"{method.upper()} {path} with NO header -> {no_header.status_code} (expected 401)")
        wrong = _call(client, method, path, body,
                      headers={"X-Control-Token": "w" * 40})
        chk(wrong.status_code == 401,
            f"{method.upper()} {path} with a WRONG token -> {wrong.status_code} (expected 401)")


def test_a_correct_token_passes_the_gate() -> None:
    """The gate must let the right token THROUGH - a gate that rejects everything is not a gate."""
    print("a correct token passes the gate")
    os.environ["BTC_CONTROL_TOKEN"] = TOKEN
    client = _client()
    for method, path, body in MUTATING:
        response = _call(client, method, path, body, headers={"X-Control-Token": TOKEN})
        # The service is unconfigured in this harness and ALSO answers 503, so the status code
        # alone cannot tell the two apart. The auth refusal is identified by its detail text.
        detail = ""
        try:
            detail = str(response.json().get("detail", ""))
        except Exception:                                          # noqa: BLE001
            detail = response.text
        chk(response.status_code != 401 and "control plane disabled" not in detail,
            f"{method.upper()} {path} with the right token -> {response.status_code} "
            f"reached the handler (detail: {detail[:60]!r})")


def test_read_only_routes_stay_open() -> None:
    print("read-only routes carry no authority and stay open")
    os.environ["BTC_CONTROL_TOKEN"] = TOKEN
    client = _client()
    for path in ("/api/binance-paper/status", "/api/binance-paper/metrics"):
        response = client.get(path)
        detail = ""
        try:
            detail = str(response.json().get("detail", ""))
        except Exception:                                          # noqa: BLE001
            detail = response.text
        chk(response.status_code != 401 and "control plane disabled" not in detail,
            f"GET {path} -> {response.status_code} (reaches the handler, not gated)")


def test_the_token_never_appears_in_a_response() -> None:
    print("no response body discloses the token")
    os.environ["BTC_CONTROL_TOKEN"] = TOKEN
    client = _client()
    leaked = []
    for method, path, body in MUTATING:
        for headers in ({}, {"X-Control-Token": "w" * 40}):
            response = _call(client, method, path, body, headers=headers)
            if TOKEN in response.text:
                leaked.append(f"{method} {path}")
    chk(not leaked, f"the configured token appears in no refusal body ({leaked})")


def test_cors_is_not_a_wildcard() -> None:
    print("the app does not advertise a wildcard origin")
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#"))
    chk('allow_origins=["*"]' not in code,
        "server.py no longer passes allow_origins=['*']")
    chk("_allowed_origins()" in code,
        "and sources its origins from the explicit allowlist")


def test_every_mutating_route_is_gated_none_forgotten() -> None:
    """A route added later without the dependency must be caught."""
    print("every state-changing route on the router carries the dependency")
    from binance_paper.routes import router

    ungated = []
    for route in router.routes:
        methods = getattr(route, "methods", set()) or set()
        if not (methods & {"POST", "PATCH", "PUT", "DELETE"}):
            continue
        names = [getattr(getattr(d, "dependency", None), "__name__", "")
                 for d in getattr(route, "dependencies", [])]
        if "require_control_token" not in names:
            ungated.append(f"{sorted(methods)} {route.path}")
    chk(not ungated, f"no mutating route is missing the gate ({ungated})")


def main() -> int:
    original = os.environ.get("BTC_CONTROL_TOKEN")
    try:
        test_unconfigured_deployment_refuses_every_mutation()
        test_missing_and_wrong_tokens_are_rejected()
        test_a_correct_token_passes_the_gate()
        test_read_only_routes_stay_open()
        test_the_token_never_appears_in_a_response()
        test_cors_is_not_a_wildcard()
        test_every_mutating_route_is_gated_none_forgotten()
    finally:
        if original is None:
            os.environ.pop("BTC_CONTROL_TOKEN", None)
        else:
            os.environ["BTC_CONTROL_TOKEN"] = original

    print("\nCONTROL PLANE SECURITY", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
