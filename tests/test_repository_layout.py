"""Verify that runtime and offline launchers remain cleanly separated."""
from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RESEARCH_LAUNCHERS = REPO / "research" / "launchers"
TEST_LAUNCHERS = REPO / "tests" / "launchers"
ALLOWED_ROOT_BATCH = {
    "backfill.bat",
    "frontend.bat",
    "run_backend.bat",
    "run_polymarket_l2_recorder.bat",
    "start.bat",
    "start_instant.bat",
    "start_production.bat",
    "start_microstructure_recorder.bat",
    "start_recorder.bat",
}
FORBIDDEN_CONTROL_BYTES = set(range(0x20)) - {0x0A, 0x0D}
SCRIPT_PATH = re.compile(
    r"(?P<path>(?:backend|research|tests)[\\/][A-Za-z0-9_.\\/-]+\.py)",
    re.IGNORECASE,
)
MODULE_PATH = re.compile(r"(?:^|\s)-m\s+(?P<module>backend(?:\.[A-Za-z_]\w*)+)")


def _repo_target(raw: str) -> Path:
    return REPO / raw.replace("\\", "/")


def _check_launcher(path: Path) -> list[str]:
    problems: list[str] = []
    raw = path.read_bytes()
    for index, value in enumerate(raw):
        if value in FORBIDDEN_CONTROL_BYTES:
            problems.append(f"{path}: forbidden control byte 0x{value:02x} at byte {index}")

    text = raw.decode("utf-8")
    lines = text.splitlines()
    expected_root = 'for %%I in ("%~dp0..\\..") do set "PROJECT_ROOT=%%~fI\\"'
    if expected_root not in lines:
        problems.append(f"{path}: missing repository-root bootstrap")
    for number, line in enumerate(lines, 1):
        if "%~dp0" in line and line != expected_root:
            problems.append(f"{path}:{number}: location-relative path escaped bootstrap")

        stripped = line.strip()
        if stripped.upper().startswith("REM ") or stripped.startswith("::"):
            continue
        for match in SCRIPT_PATH.finditer(line):
            target = _repo_target(match.group("path"))
            if not target.is_file():
                problems.append(f"{path}:{number}: missing script {match.group('path')}")
        for match in MODULE_PATH.finditer(line):
            module_path = match.group("module").replace(".", "/")
            module = REPO / f"{module_path}.py"
            package = REPO / module_path / "__init__.py"
            if not module.is_file() and not package.is_file():
                problems.append(f"{path}:{number}: missing module {match.group('module')}")
    return problems


def main() -> int:
    problems: list[str] = []
    root_batch = {path.name for path in REPO.glob("*.bat")}
    if root_batch != ALLOWED_ROOT_BATCH:
        problems.append(
            "root batch layout mismatch: "
            f"extra={sorted(root_batch - ALLOWED_ROOT_BATCH)}, "
            f"missing={sorted(ALLOWED_ROOT_BATCH - root_batch)}"
        )

    launchers = sorted(RESEARCH_LAUNCHERS.glob("*.bat"))
    test_launchers = sorted(TEST_LAUNCHERS.glob("*.bat"))
    if not launchers:
        problems.append("no research launchers found")
    if not test_launchers:
        problems.append("no test launchers found")
    for path in launchers + test_launchers:
        problems.extend(_check_launcher(path))

    for stale in ("test.py", "test2.py", "polymarket_price_probe.py"):
        if (REPO / stale).exists():
            problems.append(f"root ad-hoc script still present: {stale}")

    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        return 1
    print(
        "REPOSITORY LAYOUT PASS "
        f"research_launchers={len(launchers)} test_launchers={len(test_launchers)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
