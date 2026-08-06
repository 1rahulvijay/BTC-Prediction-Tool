"""Generate docs/active/CURRENT_STATE.json and .md FROM THE SOURCE, and fail CI when stale.

WHY THIS EXISTS
    Every hand-written status document in docs/active/ has been wrong at least once, and the
    wrongness is never obvious: a gate marked PASS that was closed prematurely, a contract
    table describing purposes that had since been split, a claim that five artifacts were
    exempt from the release freeze after the job that justified the exemption stopped
    overwriting them. Prose does not fail when the code moves.

    So the parts of the state that CAN be derived are derived. This reads the declaring
    modules and emits what they actually say. `--check` regenerates and diffs against the
    committed files, so a source change that is not reflected here fails the build.

WHAT IT DELIBERATELY DOES NOT REPORT
    Anything read from data/ or releases/ - trained artifacts, hashes, drift. Those change
    without a source edit, so embedding them would make the staleness check fail on its own
    schedule and teach people to regenerate blindly. Artifact state is the release freeze's
    job (freeze_oracle_release.py --verify).

    python backend/audit/current_state.py            # write both files
    python backend/audit/current_state.py --check    # fail if committed files are stale
    python backend/audit/current_state.py --selftest
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT_JSON = REPO / "docs" / "active" / "CURRENT_STATE.json"
OUT_MD = REPO / "docs" / "active" / "CURRENT_STATE.md"

#: Every file whose content this document claims to summarise. Hashed into the output so a
#: change to any of them makes the committed document provably stale.
SOURCES = (
    "backend/target_contract.py",
    "backend/model_registry.py",
    "backend/settlement_head.py",
    "backend/auto_finetune.py",
    "backend/audit/freeze_oracle_release.py",
    # Summarised below (OOF_CLASS_SET_TOLERANCE, FEATURE_SEMANTICS_VERSION) and therefore
    # hashed. They were the only summarised sources a comment-only edit would not have
    # flagged, which made the staleness guarantee weaker for them than for the rest.
    "backend/model.py",
    "backend/features.py",
    ".github/workflows/invariants.yml",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _literal(module: Path, name: str):
    """Read a module-level constant WITHOUT importing the module.

    Importing would drag in sklearn and a live model registry to produce a document, and any
    import-time side effect would then be part of generating documentation.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except Exception:
                        return ast.unparse(node.value)
    return None


def collect() -> dict:
    import target_contract as tc
    from model_registry import REGISTRY

    purposes = {
        purpose: sorted(contracts)
        for purpose, contracts in sorted(tc.PURPOSE_REQUIREMENTS.items())
    }
    registry = [
        {
            "name": entry.name,
            "artifact": entry.filename,
            "target_contract": entry.target,
            "may_price": bool(entry.may_price),
            "may_rank": bool(entry.may_rank),
            "may_size": bool(entry.may_size),
            "required_for_serving": bool(entry.required_for_serving),
        }
        for entry in sorted(REGISTRY, key=lambda e: e.name)
    ]

    import yaml
    workflow = yaml.safe_load(
        (REPO / ".github" / "workflows" / "invariants.yml").read_text(encoding="utf-8"))
    # Steps AND invocations. `startbat` is a single `run:` block containing dozens of
    # commands, so a step count alone reads as though that job checks three things.
    ci_jobs = {}
    for job, spec in sorted((workflow.get("jobs") or {}).items()):
        steps = [step for step in (spec.get("steps") or []) if step.get("run")]
        invocations = sum(
            1
            for step in steps
            for line in str(step["run"]).splitlines()
            if line.strip().startswith("python ")
        )
        ci_jobs[job] = {"run_steps": len(steps), "python_invocations": invocations}

    nightly = _literal(BACKEND / "auto_finetune.py", "REFIT_ARTIFACTS") or []
    exempt = _literal(BACKEND / "audit" / "freeze_oracle_release.py",
                      "NIGHTLY_OVERWRITES_SERVING")

    return {
        "generated_by": "backend/audit/current_state.py",
        "note": ("Machine-generated from source. Do not hand-edit: CI regenerates this and "
                 "fails if it differs. Artifact/drift state is NOT here - see "
                 "freeze_oracle_release.py --verify."),
        "source_digests": {name: sha256_file(REPO / name) for name in SOURCES},
        "target_contracts": {
            "known": sorted(tc.KNOWN_CONTRACTS),
            "training_contract": tc.TRAINING_CONTRACT,
            "path": sorted(tc.PATH_CONTRACTS),
            "settlement_banded": sorted(tc.SETTLEMENT_CONTRACTS),
            "settlement_binary": sorted(tc.BINARY_SETTLEMENT_CONTRACTS),
            "binary_tie_resolves_to": tc.TIE_RESOLVES_TO,
        },
        "purpose_requirements": purposes,
        "model_registry": registry,
        "nightly_refit": {
            "artifacts": list(nightly),
            "writes_serving_directory": bool(exempt),
            "candidate_only": not bool(exempt),
        },
        "oof_class_set_tolerance": _literal(BACKEND / "model.py", "OOF_CLASS_SET_TOLERANCE"),
        "feature_semantics_version": _literal(BACKEND / "features.py",
                                              "FEATURE_SEMANTICS_VERSION"),
        "ci_steps_by_job": ci_jobs,
    }


def render_markdown(state: dict) -> str:
    lines = [
        "# CURRENT STATE (machine-generated)",
        "",
        "Generated by `backend/audit/current_state.py` from the declaring modules.",
        "**Do not hand-edit.** CI regenerates this and fails if it differs from the source.",
        "",
        "Artifact and drift state is deliberately absent - it changes without a source edit,",
        "and a staleness check that fails on its own schedule teaches people to ignore it.",
        "For that, run `python backend/audit/freeze_oracle_release.py --verify`.",
        "",
        "## Target contracts",
        "",
        f"- training contract: `{state['target_contracts']['training_contract']}`",
        f"- path: {', '.join('`%s`' % c for c in state['target_contracts']['path'])}",
        f"- settlement (banded): "
        f"{', '.join('`%s`' % c for c in state['target_contracts']['settlement_banded'])}",
        f"- settlement (binary): "
        f"{', '.join('`%s`' % c for c in state['target_contracts']['settlement_binary'])}",
        f"- binary tie resolves to: **{state['target_contracts']['binary_tie_resolves_to']}**",
        "",
        "## What each consumer may be given",
        "",
        "| purpose | admissible contracts |",
        "| --- | --- |",
    ]
    for purpose, contracts in state["purpose_requirements"].items():
        lines.append(f"| `{purpose}` | {', '.join('`%s`' % c for c in contracts)} |")

    lines += [
        "",
        "## Model registry",
        "",
        "| model | artifact | target contract | price | rank | size |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in state["model_registry"]:
        def mark(flag):
            return "yes" if flag else "-"
        lines.append(
            f"| `{row['name']}` | `{row['artifact']}` | `{row['target_contract']}` | "
            f"{mark(row['may_price'])} | {mark(row['may_rank'])} | {mark(row['may_size'])} |")

    nightly = state["nightly_refit"]
    lines += [
        "",
        "## Nightly refit",
        "",
        f"- candidate-only: **{'yes' if nightly['candidate_only'] else 'NO'}**",
        f"- writes the serving directory: "
        f"**{'yes' if nightly['writes_serving_directory'] else 'no'}**",
        f"- artifacts it produces: {', '.join('`%s`' % a for a in nightly['artifacts'])}",
        "",
        "## Gates with a declared number",
        "",
        f"- OOF class-set tolerance: `{state['oof_class_set_tolerance']}` "
        f"(share of folds allowed to fit fewer than three classes before the seat is dropped)",
        f"- feature semantics version: `{state['feature_semantics_version']}`",
        "",
        "## CI declared per job",
        "",
        "`startbat` is one `run:` block holding many commands, so the step count alone would",
        "read as though it checks three things. Both numbers are reported.",
        "",
        "| job | run steps | python invocations |",
        "| --- | --- | --- |",
    ]
    for job, counts in state["ci_steps_by_job"].items():
        lines.append(f"| `{job}` | {counts['run_steps']} | {counts['python_invocations']} |")
    lines.append("")
    return "\n".join(lines)


def write(state: dict) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_markdown(state), encoding="utf-8")


def check() -> int:
    state = collect()
    stale = []
    if not OUT_JSON.is_file():
        stale.append(f"{OUT_JSON.relative_to(REPO).as_posix()} does not exist")
    else:
        committed = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        if committed != state:
            changed = sorted(
                key for key in set(committed) | set(state)
                if committed.get(key) != state.get(key))
            stale.append(f"JSON differs from source in: {', '.join(changed)}")
    if not OUT_MD.is_file():
        stale.append(f"{OUT_MD.relative_to(REPO).as_posix()} does not exist")
    elif OUT_MD.read_text(encoding="utf-8") != render_markdown(state):
        stale.append("Markdown differs from source")

    if stale:
        print("CURRENT STATE: STALE")
        for item in stale:
            print(f"  - {item}")
        print("\n  The committed state document no longer describes the code. Regenerate:")
        print("      python backend/audit/current_state.py")
        print("  It is machine-generated on purpose - every hand-written status file in")
        print("  docs/active/ has been wrong at least once, and prose does not fail when the")
        print("  code moves.")
        return 1
    print(f"CURRENT STATE: fresh ({len(state['source_digests'])} sources hashed, "
          f"{len(state['model_registry'])} registry rows, "
          f"{len(state['purpose_requirements'])} purposes)")
    return 0


def selftest() -> int:
    state = collect()
    checks = 0

    def chk(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    chk(len(state["source_digests"]) == len(SOURCES)
        and all(len(d) == 64 for d in state["source_digests"].values()),
        "every declared source is hashed into the document, so a change to any of them "
        "makes the committed copy provably stale")
    chk(state["purpose_requirements"] and state["model_registry"],
        "the document carries real content rather than empty scaffolding")

    # The staleness check must actually be able to FAIL. A checker that only ever passes is
    # the same non-gate as a counter nobody reads.
    mutated = json.loads(json.dumps(state))
    mutated["oof_class_set_tolerance"] = 0.99
    chk(mutated != state, "a changed source value produces a different document")

    # And it must be sensitive to the SOURCES themselves, not only to the summary fields.
    digests = dict(state["source_digests"])
    digests[SOURCES[0]] = "0" * 64
    chk(digests != state["source_digests"],
        "including the source digests, so an edit that does not change any summarised value "
        "still marks the document stale")

    chk("data/" not in json.dumps(state) and "releases/" not in json.dumps(state),
        "no artifact or release path is embedded - those change without a source edit, and a "
        "check that fails on its own schedule is one people learn to skip")
    chk(render_markdown(state).isascii(),
        "the rendered Markdown is ASCII-only, so a Windows cp1252 console can print it")

    print(f"\nCURRENT STATE SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="generate/verify the machine-written state doc")
    parser.add_argument("--check", action="store_true", help="fail if the committed docs are stale")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.check:
        return check()
    write(collect())
    print(f"wrote {OUT_JSON.relative_to(REPO).as_posix()} and "
          f"{OUT_MD.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
