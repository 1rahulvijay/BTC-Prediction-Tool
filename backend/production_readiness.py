"""Fail-closed production preflight for the paper/shadow decision-support service.

This does not train, download data, mutate artifacts, or enable real orders. It proves that
the environment, frontend build, main ensemble and active specialist heads are serviceable
before Uvicorn starts.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
REQUIREMENTS = BACKEND / "requirements-prod.txt"


def _is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def environment_issues(env: dict[str, str], *, mode: str) -> list[str]:
    issues: list[str] = []
    if mode != "paper":
        issues.append("only paper mode is implemented; real-money execution remains unavailable")
    if env.get("BTC_DEPLOYMENT_ENV", "").strip().lower() != "production":
        issues.append("BTC_DEPLOYMENT_ENV must be production")
    if not _is_true(env.get("BTC_STRICT_ARTIFACT_IDENTITY")):
        issues.append("BTC_STRICT_ARTIFACT_IDENTITY must be 1")
    if env.get("BTC_FREEZE_MODEL", "1") == "0":
        issues.append("BTC_FREEZE_MODEL must be 1")
    if env.get("BTC_RUN_STARTUP_BACKTEST", "0") != "0":
        issues.append("BTC_RUN_STARTUP_BACKTEST must be 0")
    for name in ("BTC_FORCE_MAIN_RETRAIN", "BTC_FORCE_HEAD_RETRAIN",
                 "BTC_OVERNIGHT_TRAIN_ALL"):
        if _is_true(env.get(name)):
            issues.append(f"{name} must be 0")
    if not _is_true(env.get("BTC_REQUIRE_ADMIN_TOKEN")):
        issues.append("BTC_REQUIRE_ADMIN_TOKEN must be 1")
    if not _is_true(env.get("BTC_SERVE_FRONTEND")):
        issues.append("BTC_SERVE_FRONTEND must be 1")
    if not _is_true(env.get("BTC_EVIDENCE_MODE")):
        issues.append(
            "BTC_EVIDENCE_MODE must be 1 so unverified complete-trade artifacts are refused"
        )
    for name in ("BTC_REQUIRE_POLYMARKET_FEED", "BTC_REQUIRE_PROTOCOL_HEALTH"):
        if not _is_true(env.get(name)):
            issues.append(f"{name} must be 1")
    bind_host = (env.get("BTC_BIND_HOST") or "127.0.0.1").strip().lower()
    if bind_host not in {"127.0.0.1", "localhost", "::1"} and not _is_true(
        env.get("BTC_ALLOW_PUBLIC_BIND")
    ):
        issues.append(
            f"BTC_BIND_HOST={bind_host!r} is not loopback; keep Uvicorn private or "
            "explicitly set BTC_ALLOW_PUBLIC_BIND=1 behind a secured reverse proxy"
        )
    for name in (
        "BTC_ENABLE_LIVE_TRADING",
        "BTC_ENABLE_REAL_ORDERS",
        "BTC_BINANCE_LIVE",
        "BTC_POLYMARKET_LIVE",
    ):
        if _is_true(env.get(name)):
            issues.append(f"{name} must remain disabled")

    sys.path.insert(0, str(BACKEND))
    from control_auth import token_is_usable

    admin = (env.get("BTC_ADMIN_TOKEN") or "").strip()
    control = (env.get("BTC_CONTROL_TOKEN") or "").strip()
    for name, value in (
        ("BTC_ADMIN_TOKEN", admin),
        ("BTC_CONTROL_TOKEN", control),
    ):
        usable, why = token_is_usable(value or None, env_name=name)
        if not usable:
            issues.append(why)
    if admin and control and admin == control:
        issues.append("BTC_ADMIN_TOKEN and BTC_CONTROL_TOKEN must be different secrets")

    raw_origins = (env.get("BTC_ALLOWED_ORIGINS") or "").strip()
    if not raw_origins:
        issues.append("BTC_ALLOWED_ORIGINS must be explicit in production")
    else:
        origins = [item.strip().rstrip("/") for item in raw_origins.split(",") if item.strip()]
        if "*" in origins:
            issues.append("BTC_ALLOWED_ORIGINS may not contain *")
        allow_http = _is_true(env.get("BTC_ALLOW_INSECURE_HTTP"))
        for origin in origins:
            loopback = origin.startswith(("http://127.0.0.1", "http://localhost"))
            if not (origin.startswith("https://") or loopback or allow_http):
                issues.append(
                    f"origin {origin!r} is not HTTPS; set BTC_ALLOW_INSECURE_HTTP=1 "
                    "only for an isolated private network"
                )
    return issues


def _dependency_issues() -> list[str]:
    issues: list[str] = []
    lines: list[str] = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        if raw.strip().startswith("-r "):
            included = BACKEND / raw.strip()[3:].strip()
            lines.extend(included.read_text(encoding="utf-8").splitlines())
        else:
            lines.append(raw)
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        package, expected = line.split("==", 1)
        dist_name = package.split("[", 1)[0]
        try:
            actual = importlib.metadata.version(dist_name)
        except importlib.metadata.PackageNotFoundError:
            issues.append(f"dependency missing: {dist_name}=={expected}")
            continue
        if actual != expected:
            issues.append(f"dependency mismatch: {dist_name} {actual} != {expected}")
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix) and not _is_true(
        os.environ.get("BTC_ALLOW_SYSTEM_PYTHON")
    ):
        issues.append(
            "production must use a dedicated virtual environment "
            "(or explicitly set BTC_ALLOW_SYSTEM_PYTHON=1)"
        )
    return issues


def _canonical_datastore_issues() -> list[str]:
    """P0-7: production must NAME its store, not materialise one.

    `database.py` resolves BTC_DB_PATH or BTC_DATA_DIR/analytics.duckdb and `init_db()` will
    happily create it. Three analytics.duckdb files exist with DISJOINT spans (07-02..07-04,
    07-05..07-25), so "a correct query against the wrong archive" is not hypothetical - it
    already happened, and it is invisible because the query succeeds.

    Being writable is not the same as being correct.
    """
    issues: list[str] = []
    explicit = os.environ.get("BTC_DB_PATH", "").strip()
    if not explicit:
        issues.append(
            "BTC_DB_PATH must name an explicit canonical database file in production; "
            "falling back to BTC_DATA_DIR/analytics.duckdb lets a correct query run against "
            "the wrong evidence archive")
        return issues
    path = Path(explicit)
    if not path.is_absolute():
        issues.append(f"BTC_DB_PATH must be absolute, got {explicit!r}")
    if not path.is_file():
        # The whole point: a missing production store is an ERROR, never a new empty store.
        issues.append(
            f"BTC_DB_PATH {explicit} does not exist. Production must not create its own "
            f"datastore - an empty store answers every query with silence.")
        return issues
    try:
        from datastore_identity import resolve as _resolve
        identity = _resolve(strict=True)
        if getattr(identity, "warning", None):
            issues.append(f"datastore identity warning: {identity.warning}")
    except Exception as exc:
        issues.append(f"datastore identity could not be resolved strictly: {exc}")
    return issues


def _recorder_progress_issues() -> list[str]:
    """P0-7/P0-10: required recorders must be ADVANCING, measured from row timestamps."""
    issues: list[str] = []
    try:
        from forward_evidence_gate import evidence_status
        status = evidence_status()
        if status["forward_evidence"] != "ADVANCING":
            issues.append(
                f"{status['banner']}; required recorders: "
                f"{', '.join(status['required'])}")
    except Exception as exc:
        issues.append(f"recorder row-progress could not be evaluated: {exc}")
    return issues


def _storage_issues() -> list[str]:
    issues: list[str] = []
    # NOT created. A production data directory that does not exist is a deployment error, and
    # `mkdir(exist_ok=True)` here is exactly how an empty store gets silently manufactured.
    if not DATA.is_dir():
        issues.append(f"data directory {DATA} does not exist; production must not create it")
        return issues
    try:
        with tempfile.NamedTemporaryFile(dir=DATA, prefix=".prod-write-", delete=True):
            pass
    except OSError as exc:
        issues.append(f"data directory is not writable: {type(exc).__name__}")
    free_gb = shutil.disk_usage(DATA).free / (1024 ** 3)
    if free_gb < 10.0:
        issues.append(f"free disk {free_gb:.1f}GB is below the 10GB runtime floor")
    return issues


def _complete_trade_issues() -> list[str]:
    if not _is_true(os.environ.get("BTC_REQUIRE_COMPLETE_TRADE", "1")):
        return []
    from trade_forecast import (
        btc_path_serving,
        execution_serving,
        share_path_serving,
    )

    issues: list[str] = []
    for name, module in (
        ("share_path", share_path_serving),
        ("btc_path", btc_path_serving),
        ("execution", execution_serving),
    ):
        status = module.status()
        if not status.get("loaded") or status.get("bundle_verified") is not True:
            issues.append(
                f"complete-trade {name} is not a loaded verified champion: "
                f"{status.get('error') or status.get('resolution_note') or 'unavailable'}"
            )
    return issues


def _run_gate(label: str, command: list[str]) -> tuple[bool, str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env=os.environ.copy(),
    )
    output = (result.stdout + result.stderr).strip()
    print(f"  {'PASS' if result.returncode == 0 else 'FAIL'}  {label}")
    if result.returncode:
        for line in output.splitlines()[-8:]:
            print(f"        {line}")
    return result.returncode == 0, output


def selftest() -> int:
    base = {
        "BTC_DEPLOYMENT_ENV": "production",
        "BTC_STRICT_ARTIFACT_IDENTITY": "1",
        "BTC_FREEZE_MODEL": "1",
        "BTC_RUN_STARTUP_BACKTEST": "0",
        "BTC_FORCE_MAIN_RETRAIN": "0",
        "BTC_FORCE_HEAD_RETRAIN": "0",
        "BTC_OVERNIGHT_TRAIN_ALL": "0",
        "BTC_REQUIRE_ADMIN_TOKEN": "1",
        "BTC_SERVE_FRONTEND": "1",
        "BTC_EVIDENCE_MODE": "1",
        "BTC_REQUIRE_COMPLETE_TRADE": "1",
        "BTC_REQUIRE_POLYMARKET_FEED": "1",
        "BTC_REQUIRE_PROTOCOL_HEALTH": "1",
        "BTC_ADMIN_TOKEN": "a" * 32,
        "BTC_CONTROL_TOKEN": "b" * 32,
        "BTC_ALLOWED_ORIGINS": "https://btc.example",
    }
    ok = True

    def check(condition: bool, message: str) -> None:
        nonlocal ok
        print(f"  {'PASS' if condition else 'FAIL'}  {message}")
        ok = ok and condition

    check(not environment_issues(base, mode="paper"), "valid paper environment passes")
    check(
        any("real-money" in issue for issue in environment_issues(base, mode="live")),
        "live mode is refused",
    )
    weak = {**base, "BTC_ADMIN_TOKEN": "short"}
    check(
        any("shorter" in issue for issue in environment_issues(weak, mode="paper")),
        "weak admin token is refused",
    )
    wildcard = {**base, "BTC_ALLOWED_ORIGINS": "*"}
    check(
        any("contain *" in issue for issue in environment_issues(wildcard, mode="paper")),
        "wildcard origin is refused",
    )
    public_bind = {**base, "BTC_BIND_HOST": "0.0.0.0"}
    check(
        any(
            "is not loopback" in issue
            for issue in environment_issues(public_bind, mode="paper")
        ),
        "an accidental public Uvicorn bind is refused",
    )
    unsafe = {**base, "BTC_ENABLE_REAL_ORDERS": "1"}
    check(
        any("must remain disabled" in issue for issue in environment_issues(unsafe, mode="paper")),
        "real-order flags remain hard off",
    )
    print("PRODUCTION PREFLIGHT SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def preflight_issues(env: dict[str, str] | None = None) -> list[str]:
    """The FAST production checks, safe to run inside the server's own startup.

    `main()` also shells out to gate subprocesses with 180s timeouts. That is right for an
    operator wrapper and wrong for a lifespan hook - it would make startup slow and give a
    hung gate the power to hang the server. These are the in-process checks only.

    WHY THE SERVER MUST RUN THEM ITSELF
        Preflight lived solely in start_production.bat. `server.py` creates the data directory
        and initialises the database on its own, so `uvicorn backend.server:app` started
        against a fresh or wrong datastore with every safety check skipped - and looked
        healthy doing it. A check that only runs when someone uses the right launcher is a
        convention, not a control.
    """
    env = dict(os.environ if env is None else env)
    issues = list(environment_issues(env, mode="production"))
    for collector in (_canonical_datastore_issues, _storage_issues):
        try:
            issues.extend(collector())
        except Exception as exc:                      # a check that cannot run is not a pass
            issues.append(f"{collector.__name__} could not run: {exc}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("paper", "live"), default="paper")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    print("=" * 78)
    print("PRODUCTION READINESS - PAPER/SHADOW SERVICE")
    print("=" * 78)
    failures: list[str] = []
    failures.extend(environment_issues(dict(os.environ), mode=args.mode))
    failures.extend(_dependency_issues())
    failures.extend(_storage_issues())
    failures.extend(_canonical_datastore_issues())
    failures.extend(_recorder_progress_issues())
    failures.extend(_complete_trade_issues())
    if not (ROOT / "dist" / "index.html").is_file():
        failures.append("frontend dist/index.html is missing; run npm run build")

    for issue in failures:
        print(f"  FAIL  {issue}")

    gates = [
        ("main ensemble compatibility", [sys.executable, "backend/check_model_compatibility.py"]),
        (
            "active head feature contracts",
            [sys.executable, "backend/check_feature_contract.py", "--enforce-serving"],
        ),
        (
            "active head training identity",
            [sys.executable, "backend/verify_artifact_identity.py", "--strict"],
        ),
    ]
    for label, command in gates:
        passed, _ = _run_gate(label, command)
        if not passed:
            failures.append(label)

    print()
    if failures:
        print(f"BLOCKED - {len(failures)} production prerequisite(s) failed.")
        print("No server was started and no artifact was changed.")
        return 1
    print("READY for paper/shadow decision-support production.")
    print("Real-money execution remains unavailable and unauthorized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
