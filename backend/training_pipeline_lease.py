"""Cross-process lease for jobs that may rewrite canonical training data.

The full retrain and the scheduled recalibration both own
``research_matrix_1m.parquet``. Running them together used to let the nightly 360-day job
replace a 1,000-day matrix while a specialist head was fitting. The head provenance guard
correctly rejected the artifact, but only after hours of wasted work.

This lease is deliberately separate from model serving locks: it protects mutable training
inputs and candidate construction, never live market recorders or paper trading stores.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data").resolve()
LEASE_PATH = Path(
    os.environ.get("BTC_TRAINING_PIPELINE_LEASE")
    or DATA_DIR / ".training_pipeline_lease.json"
).resolve()


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        # Existing but inaccessible is still alive; treating it as stale would permit two
        # writers precisely when OS permissions prevent us from proving otherwise.
        return True
    except (OSError, ProcessLookupError):
        return False


def _read(path: Path = LEASE_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def acquire(
    role: str,
    *,
    days: int,
    owner_pid: int | None = None,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    """Acquire the canonical-training lease, recovering a dead owner's stale file."""
    target = Path(path).resolve() if path is not None else LEASE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    pid = int(owner_pid or os.getpid())
    for _ in range(2):
        payload = {
            "schema_version": 1,
            "token": uuid.uuid4().hex,
            "role": str(role),
            "days": int(days),
            "owner_pid": pid,
            "created_at": time.time(),
        }
        try:
            fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            current = _read(target)
            if not current:
                # Another process may be between O_EXCL creation and its first write. An
                # empty, brand-new file is BUSY, not stale; deleting it here would allow two
                # owners. Only recover an unreadable lease after a conservative grace period.
                try:
                    if time.time() - target.stat().st_mtime < 30.0:
                        return None
                except FileNotFoundError:
                    continue
            if _process_alive(int(current.get("owner_pid", 0) or 0)):
                return None
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            continue
        try:
            os.write(fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        payload["path"] = str(target)
        return payload
    return None


def release(lease: dict[str, Any], *, path: str | os.PathLike[str] | None = None) -> bool:
    """Release only the lease whose unguessable token the caller holds."""
    target = Path(path).resolve() if path is not None else Path(lease.get("path") or LEASE_PATH)
    current = _read(target)
    if not current or current.get("token") != lease.get("token"):
        return False
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False


def describe(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = Path(path).resolve() if path is not None else LEASE_PATH
    current = _read(target)
    if current:
        current["alive"] = _process_alive(int(current.get("owner_pid", 0) or 0))
        current["path"] = str(target)
    return current


def _write_token_file(path: str, lease: dict[str, Any]) -> None:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(lease, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)


def _read_token_file(path: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    begin = sub.add_parser("begin")
    begin.add_argument("--role", required=True)
    begin.add_argument("--days", required=True, type=int)
    begin.add_argument("--token-file", required=True)
    end = sub.add_parser("end")
    end.add_argument("--token-file", required=True)
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.command == "begin":
        # This short helper exits immediately; the parent cmd.exe running start.bat owns the
        # lease for the full matrix+head transaction.
        lease = acquire(args.role, days=args.days, owner_pid=os.getppid())
        if lease is None:
            current = describe()
            print(
                "TRAINING PIPELINE BUSY: "
                f"role={current.get('role')} pid={current.get('owner_pid')} "
                f"days={current.get('days')}"
            )
            return 2
        _write_token_file(args.token_file, lease)
        print(
            f"[training-lease] acquired role={args.role} days={args.days} "
            f"owner_pid={lease['owner_pid']}"
        )
        return 0

    if args.command == "end":
        lease = _read_token_file(args.token_file)
        ok = bool(lease) and release(lease)
        try:
            Path(args.token_file).unlink()
        except FileNotFoundError:
            pass
        print("[training-lease] released" if ok else "[training-lease] already absent")
        return 0

    current = describe()
    if args.json:
        print(json.dumps(current, sort_keys=True))
    elif current:
        print(current)
    else:
        print("TRAINING PIPELINE IDLE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
