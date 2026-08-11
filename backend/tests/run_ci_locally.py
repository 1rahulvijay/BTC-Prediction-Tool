"""Run the CI workflow's own steps locally, because CI has never actually run.

WHY THIS EXISTS
    Every push since 2026-06-11 triggered the `invariants` workflow, and every single run
    failed with zero steps executed:

        "The job was not started because your account is locked due to a billing issue."

    51 runs, 0 successes. So no invariant in .github/workflows/invariants.yml has ever been
    verified by CI. The red badge was read as "tests failing" when it actually meant "tests
    never started" - a far worse state, because a genuine regression would look identical.

    Until the billing lock is cleared, this file IS the gate.

WHY IT PARSES THE WORKFLOW INSTEAD OF LISTING COMMANDS
    A hand-maintained duplicate of the CI command list drifts, and drift is invisible. That
    exact failure already happened twice in this repo: the workflow's Windows job re-types its
    commands rather than reading start.bat, so a corrupted launcher line went unnoticed.

    This reads invariants.yml and runs what is actually declared there, so a step added to CI
    is a step run locally, with no second list to keep in sync.

    python backend/tests/run_ci_locally.py            # python steps (offline, deterministic)
    python backend/tests/run_ci_locally.py --all      # include the npm build/audit steps
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "invariants.yml"
BACKSLASH = chr(92)


def workflow_steps(job: str = "invariants") -> list[tuple[str, list[str]]]:
    import yaml

    spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = []
    for step in spec["jobs"][job]["steps"]:
        run = step.get("run")
        if not run:
            continue
        # A `run:` block may continue one shell command across lines with a trailing backslash.
        # Splitting on newlines alone turned a single `python -c "..."` into three broken
        # commands, two of which started with `from` and were reported as CI failures.
        joined, buffer = [], ""
        for line in run.strip().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.endswith(BACKSLASH):
                buffer += stripped[:-1] + " "
                continue
            joined.append((buffer + stripped).strip())
            buffer = ""
        if buffer.strip():
            joined.append(buffer.strip())
        commands = joined
        steps.append((step.get("name", "(unnamed)"), commands))
    return steps


def all_jobs() -> list[str]:
    import yaml

    return list(yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"])


def every_step() -> list[tuple[str, list[str]]]:
    """Every step of EVERY job, not just `invariants`.

    THE GAP THIS CLOSES
        This runner parsed job="invariants" and nothing else. The workflow has a second job,
        `startbat`, holding 27 commands that appear in NO other job - among them
        test_round_state_causal_contract (which pins the P0-01 version contract and the P0-02
        same-minute feature leak), recorder_health, datastore_identity, and every research study
        built since the crossing work began.

        GitHub Actions has never executed a step because the account is billing-locked, and this
        file skipped the job those 27 live in. So they were gated by nothing at all - the exact
        state this runner exists to prevent, reproduced one level up.

        `startbat` also invokes start.bat under BTC_VALIDATE_STARTUP=1. That is excluded here by
        operator instruction: launching it is the operator's action, not this runner's.
    """
    seen: set[str] = set()
    steps: list[tuple[str, list[str]]] = []
    for job in all_jobs():
        for name, commands in workflow_steps(job):
            # Skip the WHOLE step, not just the launcher line: dropping `call start.bat` alone
            # would leave its `set ...` and `if errorlevel` fragments to fail under sh.
            if any("start.bat" in c for c in commands):
                continue
            kept = []
            for command in commands:
                guarded_on_windows = "|| exit /b 1" in command.lower()
                # cmd-shell guards are noise under sh; the return code gates either way.
                command = command.replace("|| exit /b 1", "").strip()
                # The Windows job intentionally leaves reporting-only commands unguarded.
                # When the workflow runs them as one cmd block, a non-zero result is replaced
                # by the next command's result and does not fail the step. This runner splits
                # the block into subprocesses, so treating an unguarded line as a gate silently
                # strengthens CI and makes the documented artifact-readiness report fail before
                # a required retrain. Preserve the workflow's actual semantics explicitly.
                if job == "startbat" and not guarded_on_windows:
                    command = f"{command} || true"
                # Dedupe per COMMAND, not per step: startbat packs 99 commands into a single
                # step while invariants lists them individually, so a step-level key would
                # never match and the whole Windows block would re-run.
                key = command_identity(command)
                if key in seen:
                    continue
                seen.add(key)
                kept.append(command)
            if kept:
                steps.append((f"[{job}] {name}", kept))
    return steps


def command_identity(command: str) -> str:
    """Normalise so `python backend/x.py --selftest` and `python -m backend.x --selftest`
    are recognised as the same gate. They differ only in import style."""
    text = " ".join(command.split())
    return (text.replace("python -m ", "python ").replace("backend/", "backend.")
                .replace("research/", "research.").replace("tests/", "tests.")
                .replace(".py", ""))


def main() -> int:
    include_node = "--all" in sys.argv
    steps = every_step()

    selected: list[tuple[str, list[str]]] = []
    skipped: list[str] = []
    for name, commands in steps:
        if any(c.startswith(("pip ", "python -m pip")) for c in commands):
            skipped.append(f"{name} (dependency install - use your existing environment)")
            continue
        if any(c.startswith(("npm ", "node ")) for c in commands) and not include_node:
            skipped.append(f"{name} (node; re-run with --all)")
            continue
        selected.append((name, commands))

    print(f"Running {len(selected)} CI steps from {WORKFLOW.relative_to(REPO)}")
    print("CI itself has never executed these - the account is billing-locked.\n")

    failures: list[tuple[str, str, str]] = []
    started = time.time()
    for index, (name, commands) in enumerate(selected, 1):
        for command in commands:
            # `|| true` in the workflow marks a reporting step, not a gate. Preserve that.
            advisory = command.endswith("|| true")
            result = subprocess.run(command, shell=True, cwd=REPO,
                                    capture_output=True, text=True)
            if result.returncode == 0 or advisory:
                mark = "OK  " if result.returncode == 0 else "note"
            else:
                mark = "FAIL"
                failures.append((name, command, (result.stdout + result.stderr).strip()[-700:]))
            print(f"  {mark} [{index:>2}/{len(selected)}] {name[:62]}")

    print(f"\nelapsed {time.time() - started:.0f}s")
    if skipped:
        print("\nnot run here:")
        for item in skipped:
            print(f"  - {item}")
    if failures:
        print(f"\n{len(failures)} FAILING STEP(S)\n")
        for name, command, output in failures:
            print(f"--- {name}\n    $ {command}\n{output}\n")
        print("LOCAL CI: FAIL")
        return 1
    print("\nLOCAL CI: PASS - every gating step in the workflow succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
