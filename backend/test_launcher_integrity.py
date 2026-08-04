"""start.bat is the launch gate. This proves it can actually launch.

WHY THIS EXISTS
    start.bat referenced `python backend<0x0c>eed_writer.py` for an unknown period. The path was
    written through a Python string where `\\f` is a FORMFEED, not a backslash followed by 'f'.
    Windows could not find the file, python exited non-zero, the `if errorlevel 1` beneath it
    jumped to :selftest_abort, and startup stopped - every launch, before any server started.

    Nothing caught it. Module selftests do not read start.bat. Reading the file looks fine,
    because a lone control byte renders as whitespace in most viewers. The only reliable check
    is a machine reading the bytes.

    The same escape hazard applies to \\t \\n \\r \\v \\b \\a and \\0, and every one of them
    produces a path that looks correct on screen.

WHAT IT CHECKS
    1. no stray C0 control bytes - the corruption class above, for every escape, not just \\f
    2. every script start.bat invokes EXISTS on disk - a typo cannot reach an operator
    3. every `goto :label` has a matching label - a failure handler cannot silently vanish
    4. the selftest block still guards each invocation with an errorlevel check

    python backend/test_launcher_integrity.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "start.bat"

# Everything below 0x20 except CR and LF. These are what the escape mistakes produce.
FORBIDDEN = {code: name for code, name in {
    0x00: "NUL (\\0)", 0x07: "BEL (\\a)", 0x08: "BS (\\b)", 0x09: "TAB (\\t)",
    0x0B: "VT (\\v)", 0x0C: "FF (\\f)", 0x1B: "ESC",
}.items()}

INVOCATION = re.compile(
    r"^\s*python\s+(?:-m\s+(?P<module>[\w.]+)|(?P<script>[^\s>|]+\.py))",
    re.IGNORECASE | re.MULTILINE,
)


def _lines(raw: bytes) -> list[bytes]:
    return raw.replace(b"\r\n", b"\n").split(b"\n")


def check_control_bytes(raw: bytes) -> list[str]:
    problems = []
    for number, line in enumerate(_lines(raw), 1):
        for code, name in FORBIDDEN.items():
            if bytes([code]) in line:
                shown = line.decode("latin-1").replace(chr(code), f"<{name}>")
                problems.append(f"line {number}: stray {name} -> {shown.strip()}")
    return problems


def check_invocations(text: str) -> tuple[list[str], int]:
    problems, count = [], 0
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.upper().startswith("REM ") or stripped.startswith("::"):
            continue
        match = INVOCATION.match(line)
        if not match:
            continue
        count += 1
        if match.group("module"):
            module = match.group("module")
            # Only repo-local modules are checkable here. `-m uvicorn` is an installed
            # third-party package with no file under the repo root, and demanding one would
            # make this guard fail for a correct launcher.
            if not module.startswith("backend"):
                continue
            target = REPO / (module.replace(".", "/") + ".py")
            package = REPO / module.replace(".", "/") / "__init__.py"
            if not target.is_file() and not package.is_file():
                problems.append(f"line {number}: -m {module} resolves to no file")
        else:
            script = match.group("script").replace("\\", "/")
            if not (REPO / script).is_file():
                problems.append(f"line {number}: {match.group('script')} does not exist")
    return problems, count


def check_labels(text: str) -> list[str]:
    declared = {m.group(1).lower() for m in re.finditer(r"^\s*:([A-Za-z_]\w*)", text, re.MULTILINE)}
    problems = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in re.finditer(r"\bgoto\s+:?([A-Za-z_]\w*)", line, re.IGNORECASE):
            if match.group(1).lower() not in declared and match.group(1).lower() != "eof":
                problems.append(f"line {number}: goto :{match.group(1)} has no label")
    return problems


def check_selftests_are_guarded(text: str) -> list[str]:
    """Inside the selftest block, every python invocation needs an errorlevel check under it.

    An unguarded selftest runs, fails, prints nothing, and startup continues as if it passed -
    which is worse than not running it at all."""
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if ":selftests_start" in l or
                     "[selftest] a." in l)
        end = next(i for i, l in enumerate(lines) if "goto :selftests_done" in l)
    except StopIteration:
        return ["could not locate the selftest block"]
    problems = []
    for i in range(start, end):
        if INVOCATION.match(lines[i]) and "--selftest" in lines[i] or (
                INVOCATION.match(lines[i]) and "test_" in lines[i]):
            following = lines[i + 1] if i + 1 < len(lines) else ""
            if "errorlevel" not in following.lower():
                problems.append(f"line {i + 1}: {lines[i].strip()[:60]} is not errorlevel-guarded")
    return problems


def check_artifact_identity_gate(text: str) -> list[str]:
    """Strict identity must be the default AND must be gated before launch.

    P0-8. Turning the flag on is not enough: verify_artifact_identity states plainly that with
    strict on and no manifests, "Zero heads would load; the app would serve blind while logging
    one ERROR per artifact". Serving blind is fail-OPEN. Without a gate, enabling the flag
    produces no models AND no refusal - strictly worse than leaving it off.

    So this asserts both halves: the honest default, and the refusal that makes it mean
    something.
    """
    problems: list[str] = []
    if 'set "BTC_STRICT_ARTIFACT_IDENTITY=0"' in text:
        problems.append("start.bat still defaults strict artifact identity OFF")
    if 'set "BTC_STRICT_ARTIFACT_IDENTITY=1"' not in text:
        problems.append("start.bat does not default strict artifact identity ON")

    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if 'if "%BTC_STRICT_ARTIFACT_IDENTITY%"=="1"' in ln), None)
    if start is None:
        problems.append("no launch gate keyed on BTC_STRICT_ARTIFACT_IDENTITY")
        return problems

    # Walk to the matching close paren, then keep only lines that DO something. A first
    # version searched a 2000-character window for substrings, and two mutations survived it:
    # commenting out the verifier left its name in an `echo`, and deleting the refusal left an
    # `exit /b 1` belonging to the next block. Presence in a window is not execution.
    def is_prose(line: str) -> bool:
        return not line or line.lower().startswith(("rem ", "echo", "::"))

    depth = 0
    body: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        body.append(stripped)
        # Parens are counted ONLY on executable lines. The gate's own echo text contains
        # escaped `^)` characters, and counting those closed the block early - which made the
        # check report a missing refusal on a file that has one.
        if not is_prose(stripped):
            depth += stripped.count("(") - stripped.count(")")
        if depth <= 0 and len(body) > 1:
            break
    executable = [ln for ln in body if not is_prose(ln)]
    joined = " ; ".join(executable)

    if "verify_artifact_identity.py" not in joined:
        problems.append("the strict gate does not RUN verify_artifact_identity "
                        "(mentioning it in an echo is not running it)")
    if "errorlevel 1" not in joined:
        problems.append("the strict gate does not check the verifier's exit code")
    if "exit /b 1" not in joined:
        problems.append("the strict gate does not REFUSE to launch - it would serve blind")
    return problems


def main() -> int:
    if not LAUNCHER.is_file():
        print(f"FAIL start.bat not found at {LAUNCHER}")
        return 1
    raw = LAUNCHER.read_bytes()
    text = raw.decode("latin-1")
    ok = True

    for label, problems in (
        ("no stray control bytes in any path", check_control_bytes(raw)),
        ("every goto has a matching label", check_labels(text)),
        ("every selftest invocation is errorlevel-guarded", check_selftests_are_guarded(text)),
        ("strict artifact identity is default AND gated before launch",
         check_artifact_identity_gate(text)),
    ):
        print(f"  {'OK  ' if not problems else 'FAIL'} {label}")
        for problem in problems:
            print(f"       {problem}")
        ok = ok and not problems

    problems, count = check_invocations(text)
    print(f"  {'OK  ' if not problems else 'FAIL'} every invoked script exists ({count} checked)")
    for problem in problems:
        print(f"       {problem}")
    ok = ok and not problems

    print("LAUNCHER INTEGRITY", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
