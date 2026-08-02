"""Atomic immutable report output and data/code identity capture."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from .protocol import VERDICTS


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_commit(repo: Path) -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                            text=True, timeout=15)
    return result.stdout.strip() or None if result.returncode == 0 else None


def write_report(output: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(output).resolve()
    report = target / "report.json"
    if report.exists():
        raise FileExistsError(f"immutable report already exists: {report}")
    status = payload.get("status")
    if status not in VERDICTS:
        raise ValueError(f"invalid final status: {status!r}")
    target.mkdir(parents=True, exist_ok=True)
    full = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "capital_authority": False,
        **payload,
    }
    encoded = json.dumps(full, indent=2, sort_keys=True, allow_nan=False) + "\n"
    temporary = target / "report.json.tmp"
    temporary.write_text(encoded, encoding="utf-8", newline="\n")
    temporary.replace(report)
    (target / "STATUS.txt").write_text(str(status) + "\n", encoding="ascii")
    return report


def selftest(tmp_path: Path) -> None:
    path = write_report(tmp_path, {"status": "BLOCKED_DATA", "experiment_id": "SELFTEST"})
    assert path.is_file()
    try:
        write_report(tmp_path, {"status": "BLOCKED_DATA"})
    except FileExistsError:
        pass
    else:
        raise AssertionError("immutable report was overwritten")

