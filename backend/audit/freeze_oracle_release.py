"""FREEZE_ORACLE_2026_07_04_RELEASE - pin what was deployed, and say plainly what is already lost.

WHAT A RELEASE FREEZE IS FOR
    The July 4 build has been making live predictions since 2026-07-06. It is the only
    benchmark in this repository with real forward exposure, so every later challenger has to
    beat IT rather than beat a backtest. That comparison is worthless unless the champion's
    identity is pinned: which code, which artifacts, which policy constants.

WHAT THIS SCRIPT REFUSES TO PRETEND
    A freeze written today cannot conjure bytes that were overwritten last week. Measured:

        17 of 25 artifacts still carry their pre-deployment mtime  -> AS_DEPLOYED
         8 were rewritten AFTER 2026-07-06                         -> CHANGED_SINCE_DEPLOYMENT

    The eight include persistence_model.pkl (P(hold)) and round_state_heads.pkl - both central.
    Their July 4 bytes are gone. So the blueprint's acceptance test, "given any historical
    Oracle prediction, reproduce the same feature vector, model output, calibrated probability
    and action", is NOT satisfiable for any prediction that consumed them, and this manifest
    records that as a hard fact rather than an asterisk. Freezing the survivors is still worth
    doing: it stops the remaining seventeen from silently becoming the ninth and tenth losses.

SECRETS
    Environment capture is NAMES ONLY. No value, and no hash of a value - a hash of a
    low-entropy secret is still a liability. Whether a key is set is recorded; what it is set
    to never leaves the process.

USAGE
    python backend/audit/freeze_oracle_release.py              # write the manifests
    python backend/audit/freeze_oracle_release.py --copy       # also copy AS_DEPLOYED bytes
    python backend/audit/freeze_oracle_release.py --verify     # re-hash and diff vs the freeze
    python backend/audit/freeze_oracle_release.py --selftest   # prove drift is detected
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

RELEASE_ID = "ORACLE_2026_07_04"
# The build date. Predictions start flowing two days later; anything whose bytes changed after
# that instant was NOT what served the live sample.
DEPLOYMENT_START = "2026-07-06"
DEPLOYMENT_START_TS = datetime(2026, 7, 6, tzinfo=timezone.utc).timestamp()
TRAINING_WINDOW_DAYS = 400

RELEASE_DIR = REPO / "releases" / RELEASE_ID
MODEL_DIR = Path(os.environ.get("BTC_DATA_DIR") or REPO / "data") / "saved_models"

AS_DEPLOYED = "AS_DEPLOYED"
CHANGED = "CHANGED_SINCE_DEPLOYMENT"

#: Artifacts the NIGHTLY refit owns. Imported from the job itself so the two cannot disagree.
#:
#: WHY THIS CLASS HAD TO EXIST
#:     `auto_finetune.py` runs nightly at 04:00 and rewrites exactly these five heads. The
#:     freeze pinned them as immutable, so `--verify` reported five DRIFTED artifacts every
#:     morning and had been failing continuously. That is not drift being detected - it is a
#:     scheduled job doing its job while a check that cannot tell the two apart cries wolf. A
#:     check that always fails is one people learn to skip, and then it catches nothing.
#:
#:     Byte-immutability is the wrong contract for an artifact that is DESIGNED to be refit.
#:     The thing the freeze actually needs to guarantee - "which bytes served this prediction"
#:     - is served for these by a version HISTORY, and for everything else by immutability.
#: WHY THE EXEMPTION IS NOW EMPTY (2026-08-05)
#:     The premise above was that a scheduled job overwrites serving artifacts nightly. That
#:     is no longer true: `auto_finetune.py` writes to data/saved_models/candidates/<stamp>/
#:     and verifies afterwards that every serving digest is unchanged. Nothing scheduled
#:     rewrites serving any more, so the exemption has no remaining justification and these
#:     five are PINNED again.
#:
#:     The only writer left is `train_heads.py` at boot, on a VERSION CHANGE - a deliberate
#:     deploy. A freeze reporting CHANGED after a deliberate deploy is the freeze working;
#:     you re-freeze. That is not the daily false alarm this exemption was created to stop.
#:
#:     The import is KEPT so a broken import is still detectable, and so this file still
#:     cannot disagree with the job about which artifacts the nightly produces.
try:                                              # pragma: no cover - import shape only
    from auto_finetune import REFIT_ARTIFACTS as _REFIT_ARTIFACTS
    _REFIT_IMPORT_OK = True
except Exception:                                 # the freeze must still verify without it
    _REFIT_ARTIFACTS = ()
    _REFIT_IMPORT_OK = False

#: Set False when a scheduled job overwrites serving artifacts again. It does not today.
NIGHTLY_OVERWRITES_SERVING = False
SCHEDULED_REFIT_ARTIFACTS = (
    frozenset(_REFIT_ARTIFACTS) if NIGHTLY_OVERWRITES_SERVING else frozenset())

PINNED = "PINNED"
SCHEDULED_REFIT = "SCHEDULED_REFIT"

#: Every observed refit is appended here. Muting a check without recording what it would have
#: reported is just deleting the check.
REFIT_HISTORY = RELEASE_DIR / "refit_history.jsonl"


def freeze_class(name: str) -> str:
    return SCHEDULED_REFIT if name in SCHEDULED_REFIT_ARTIFACTS else PINNED


def nightly_overwrites_serving_in_source() -> bool:
    """Read auto_finetune.py and decide whether it still overwrites serving artifacts.

    Parsed, not grepped, and not taken on trust from a constant: the exemption below removes
    five serving artifacts from the release freeze, and a one-word edit to a flag should not
    be able to do that. The job is considered safe only if main() both redirects output away
    from serving AND verifies afterwards that serving is untouched.
    """
    import ast
    source = Path(__file__).resolve().parent.parent / "auto_finetune.py"
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except Exception:
        return True                       # unreadable: assume the unsafe case, stay pinned
    main_fn = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    if main_fn is None:
        return True
    redirects = any(
        isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute)
        and n.value.attr == "environ"
        and isinstance(n.slice, ast.Constant) and n.slice.value == "BTC_MODEL_OUTPUT_DIR"
        for n in ast.walk(main_fn))
    guards = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "protect_serving" for n in ast.walk(main_fn))
    return not (redirects and guards)

# Names only. The value of any of these never enters a file this script writes.
ENV_KEYS_OF_RECORD = (
    "BTC_DATA_DIR", "BTC_FREEZE_MODEL", "BTC_STRICT_ARTIFACT_IDENTITY",
    "BTC_TRADING_MODE", "BTC_REAL_ORDERS", "BTC_ADMIN_TOKEN",
    "POLYMARKET_API_KEY", "POLYMARKET_SECRET", "POLYMARKET_PASSPHRASE",
    "BINANCE_API_KEY", "BINANCE_API_SECRET",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=str(REPO), capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def code_identity() -> dict:
    """The last commit that existed when the Oracle started serving."""
    commit = _git("log", "-1", "--format=%H", f"--until={DEPLOYMENT_START}")
    return {
        "commit": commit or None,
        "commit_date": _git("log", "-1", "--format=%ad", "--date=iso-strict", commit)
        if commit else None,
        "subject": _git("log", "-1", "--format=%s", commit) if commit else None,
        "head_now": _git("rev-parse", "HEAD") or None,
        "commits_since_deployment": len(
            [line for line in _git("log", "--format=%H", f"{commit}..HEAD").splitlines()])
        if commit else None,
        "note": "The working tree's cleanliness at deployment time cannot be recovered "
                "retroactively. This records the commit, not a proof that it was what ran.",
    }


def artifact_inventory() -> list[dict]:
    """Every artifact, its hash, and whether it can still be the one that was deployed."""
    try:
        from check_feature_contract import verdict_for
    except Exception:
        def verdict_for(_path):                       # noqa: ANN001
            return ("VERDICT_UNAVAILABLE", "check_feature_contract did not import")

    rows = []
    for artifact in sorted(MODEL_DIR.glob("*.pkl")):
        mtime = artifact.stat().st_mtime
        code, detail = verdict_for(str(artifact))
        rows.append({
            "name": artifact.name,
            "sha256": sha256_file(artifact),
            "size_bytes": artifact.stat().st_size,
            "mtime_utc": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
            "state": AS_DEPLOYED if mtime <= DEPLOYMENT_START_TS else CHANGED,
            "has_provenance_manifest": (Path(f"{artifact}.manifest.json").is_file()
                                        or artifact.with_suffix(".manifest.json").is_file()),
            "serviceable": code is None,
            "refusal": None if code is None else f"{code}: {detail}",
        })
    return rows


def policy_identity() -> dict:
    """Declared constants that can move a decision, read from the modules that own them."""
    policy: dict = {}
    try:
        from polymarket_paper import calibrated_fair_value as fair_value
        policy["polymarket_fair_value_benchmark"] = {
            "strategy_id": fair_value.STRATEGY_ID,
            "evidence_status": fair_value.EVIDENCE_STATUS,
            "historical_economic_claim": fair_value.HISTORICAL_ECONOMIC_CLAIM,
            "capital_authority": fair_value.CAPITAL_AUTHORITY,
            "entry_margin": fair_value.ENTRY_MARGIN,
            "exit_margin": fair_value.EXIT_MARGIN,
            "stop_drop": fair_value.STOP_DROP,
            "min_seconds_left": fair_value.MIN_SECONDS_LEFT,
            "max_seconds_left": fair_value.MAX_SECONDS_LEFT,
            "eval_window_s": [fair_value.EVAL_MIN_SECONDS_LEFT,
                              fair_value.EVAL_MAX_SECONDS_LEFT],
        }
    except Exception as exc:
        policy["polymarket_fair_value_benchmark"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        from check_feature_contract import FEATURE_SEMANTICS_VERSION, TRAINING_SEMANTICS_VERSION
        policy["feature_semantics_version"] = FEATURE_SEMANTICS_VERSION
        policy["training_semantics_version"] = TRAINING_SEMANTICS_VERSION
    except Exception as exc:
        policy["feature_semantics_version"] = f"error: {type(exc).__name__}"
    policy["note"] = ("These are TODAY's constants. They are recorded so a later drift is "
                      "visible; they are not evidence of what the constants were on 2026-07-04.")
    return policy


def environment_redacted() -> dict:
    """Key NAMES and whether they are set. No values, and no hashes of values."""
    return {
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "keys": {key: ("set" if os.environ.get(key) else "unset")
                 for key in ENV_KEYS_OF_RECORD},
        "note": "Names only, by policy. Secrets belong in the deployment environment, never "
                "in a file under version control.",
    }


def recorder_inventory() -> dict:
    """Which recorders exist in the tree, and whether their stores hold anything."""
    data_dir = Path(os.environ.get("BTC_DATA_DIR") or REPO / "data")
    stores = []
    for store in sorted(data_dir.glob("*.duckdb")):
        stat = store.stat()
        stores.append({
            "store": store.name,
            "size_bytes": stat.st_size,
            "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })
    return {"stores": stores,
            "note": "Per-table coverage, gaps and clock skew are measured separately by "
                    "backend/audit/build_oracle_data_manifest.py."}


def build() -> dict:
    artifacts = artifact_inventory()
    deployed = [a for a in artifacts if a["state"] == AS_DEPLOYED]
    changed = [a for a in artifacts if a["state"] == CHANGED]
    return {
        "release_id": RELEASE_ID,
        "deployment_start": DEPLOYMENT_START,
        "training_window_days": TRAINING_WINDOW_DAYS,
        "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "code": code_identity(),
        "artifacts": artifacts,
        "artifact_summary": {
            "total": len(artifacts),
            "as_deployed": len(deployed),
            "changed_since_deployment": len(changed),
            "serviceable": sum(1 for a in artifacts if a["serviceable"]),
        },
        "reproducibility": {
            "can_reproduce_any_oracle_prediction": False,
            "why": ("%d artifacts were rewritten after %s, including %s. Their deployed bytes "
                    "no longer exist, so a prediction that consumed them cannot be "
                    "re-derived." % (len(changed), DEPLOYMENT_START,
                                     ", ".join(a["name"] for a in changed[:3]) or "none")),
            "changed": [a["name"] for a in changed],
        },
        "policy": policy_identity(),
        "environment": environment_redacted(),
        "recorders": recorder_inventory(),
    }


def write(manifest: dict, *, copy_artifacts: bool) -> None:
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("release_manifest.json", {k: v for k, v in manifest.items()
                                   if k not in ("policy", "environment", "recorders")}),
        ("model_manifest.json", {"release_id": RELEASE_ID,
                                 "artifacts": manifest["artifacts"],
                                 "artifact_summary": manifest["artifact_summary"]}),
        ("policy_manifest.json", {"release_id": RELEASE_ID, **manifest["policy"]}),
        ("environment_redacted.json", {"release_id": RELEASE_ID, **manifest["environment"]}),
        ("recorder_manifest.json", {"release_id": RELEASE_ID, **manifest["recorders"]}),
    ):
        (RELEASE_DIR / name).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n",
                                        encoding="utf-8")
        print(f"  wrote {(RELEASE_DIR / name).relative_to(REPO).as_posix()}")

    if not copy_artifacts:
        return
    target = RELEASE_DIR / "artifacts"
    target.mkdir(exist_ok=True)
    copied = total = 0
    for row in manifest["artifacts"]:
        if row["state"] != AS_DEPLOYED:
            continue
        destination = target / row["name"]
        if destination.is_file() and sha256_file(destination) == row["sha256"]:
            continue
        shutil.copy2(MODEL_DIR / row["name"], destination)
        copied += 1
        total += row["size_bytes"]
    print(f"  copied {copied} AS_DEPLOYED artifacts ({total / 1e6:.1f} MB) into "
          f"{target.relative_to(REPO).as_posix()} (git-ignored; the hashes are the record)")


def verify() -> int:
    """Re-hash and diff against the freeze. This is what makes the freeze mean anything."""
    stored = RELEASE_DIR / "model_manifest.json"
    if not stored.is_file():
        print(f"  no freeze at {stored.relative_to(REPO).as_posix()} - run without --verify")
        return 1
    frozen = {row["name"]: row for row in json.loads(stored.read_text(encoding="utf-8"))["artifacts"]}
    current = {row["name"]: row for row in artifact_inventory()}

    recorded = recorded_refit_hashes()
    drifted, vanished, appeared, refit, legacy = [], [], [], [], []
    for name, row in frozen.items():
        if name not in current:
            vanished.append(name)
        elif current[name]["sha256"] != row["sha256"]:
            # A PINNED artifact that changed is drift. A SCHEDULED_REFIT artifact that changed
            # is the nightly job having run - expected, and recorded rather than ignored.
            if freeze_class(name) == SCHEDULED_REFIT:
                refit.append(name)
            elif current[name]["sha256"] in recorded.get(name, ()):
                legacy.append(name)
            else:
                drifted.append(name)
    for name in current:
        if name not in frozen:
            appeared.append(name)

    print(f"  pinned {sum(1 for n in frozen if freeze_class(n) == PINNED)} | "
          f"scheduled-refit {sum(1 for n in frozen if freeze_class(n) == SCHEDULED_REFIT)} | "
          f"drifted {len(drifted)} | legacy {len(legacy)} | refit {len(refit)} | "
          f"vanished {len(vanished)} | new {len(appeared)}")
    for label, names in (("DRIFTED", drifted), ("VANISHED", vanished)):
        for name in names:
            print(f"    {label}: {name}")
    for name in sorted(legacy):
        print(f"    LEGACY (moved under the retired nightly regime, hash on record): "
              f"{name}  {current[name]['sha256'][:12]}")
    for name in sorted(refit):
        print(f"    REFIT (nightly, expected): {name}  {current[name]['sha256'][:12]}")
    if appeared:
        print(f"    new since the freeze (not an error): {', '.join(sorted(appeared))}")

    if refit:
        _record_refits(refit, frozen, current)

    if drifted or vanished:
        print("\n  FAIL - a PINNED artifact changed. If this was a deliberate retrain, the "
              "champion identity has moved and a NEW release id is required; overwriting the "
              "freeze in place would erase the only record of what produced the live sample.")
        return 1
    if refit:
        print(f"\n  PASS - every PINNED artifact still hashes to its recorded value. "
              f"{len(refit)} scheduled-refit head(s) changed as designed; each new hash is "
              f"appended to {REFIT_HISTORY.name}, so which bytes served a given prediction "
              f"stays answerable by TIME rather than by immutability.")
        return 0
    if legacy:
        print(f"\n  PASS - no NEW drift. {len(legacy)} artifact(s) still carry bytes they moved "
              f"to under the retired nightly regime; each hash is on record in "
              f"{REFIT_HISTORY.name}. They are PINNED again, so the next change to any of them "
              f"is a hard failure.")
        print("  OPEN ITEM: the serving bytes do not match ORACLE_2026_07_04. Closing that is "
              "an operator act - issue a new release id, or restore the frozen bytes.")
        return 0
    print("\n  PASS - every frozen artifact still hashes to its recorded value.")
    return 0


def recorded_refit_hashes() -> dict:
    """{artifact -> {sha256, ...}} already written to refit_history.jsonl.

    This set is CLOSED. `_record_refits` only ever appends for SCHEDULED_REFIT artifacts, and
    that class is now empty, so nothing can add to it - an artifact cannot explain its own
    future drift by drifting.

    It exists because un-exempting the five nightly heads surfaced a real, pre-existing fact:
    their bytes had already moved away from the freeze under the retired nightly regime, and
    the exemption was using this very record to mute the failure. Reporting that history as
    fresh drift forever would make the check fail every run for something that already
    happened, which is how a check becomes something people skip.
    """
    out: dict = {}
    if not REFIT_HISTORY.is_file():
        return out
    for line in REFIT_HISTORY.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        name, sha = row.get("name"), row.get("sha256")
        if name and sha:
            out.setdefault(name, set()).add(sha)
    return out


def _record_refits(names, frozen: dict, current: dict) -> None:
    """Append each newly observed refit hash, once.

    This is what separates 'we understand why this changed' from 'we stopped looking'. Without
    it, exempting the nightly heads would delete the only record of which bytes were live when.
    """
    seen = set()
    if REFIT_HISTORY.is_file():
        for line in REFIT_HISTORY.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            seen.add((row.get("name"), row.get("sha256")))
    new_rows = []
    for name in sorted(names):
        sha = current[name]["sha256"]
        if (name, sha) in seen:
            continue                                  # already recorded; do not duplicate
        new_rows.append({
            "name": name,
            "sha256": sha,
            "size_bytes": current[name].get("size_bytes"),
            "mtime_utc": current[name].get("mtime_utc"),
            "observed_utc": datetime.now(timezone.utc).isoformat(),
            "froze_as": frozen[name]["sha256"],
            "owner": "auto_finetune.py nightly REFIT",
        })
    if not new_rows:
        return
    REFIT_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with REFIT_HISTORY.open("a", encoding="utf-8") as handle:
        for row in new_rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(f"    recorded {len(new_rows)} new refit hash(es) -> "
          f"{REFIT_HISTORY.relative_to(REPO).as_posix()}")


def selftest() -> int:
    """A freeze that cannot notice a changed byte is decoration."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.bin"
        probe.write_bytes(b"deployed")
        first = sha256_file(probe)
        probe.write_bytes(b"retrained")
        assert sha256_file(probe) != first, "hashing cannot detect a changed artifact"
    inventory = artifact_inventory()
    assert inventory, "no artifacts found to freeze"
    assert {row["state"] for row in inventory} <= {AS_DEPLOYED, CHANGED}
    assert all(len(row["sha256"]) == 64 for row in inventory)
    # The classifier must be able to say BOTH things, or "as deployed" is meaningless.
    states = {row["state"] for row in inventory}
    assert states == {AS_DEPLOYED, CHANGED}, (
        f"expected both states present, got {states} - if every artifact reads AS_DEPLOYED the "
        "cutoff is wrong and the freeze would hide real drift")
    # The refit exemption must be NARROW. Exempting the nightly heads is only defensible if a
    # PINNED artifact changing is still a hard failure - otherwise this stopped being a freeze.
    # The set being empty is now the CORRECT state, so emptiness can no longer stand in for
    # "the import broke". Those two are asserted separately.
    assert _REFIT_IMPORT_OK, (
        "auto_finetune.REFIT_ARTIFACTS did not import, so this file can no longer be checked "
        "against the job that owns those artifacts")
    # NIGHTLY_OVERWRITES_SERVING must not be a claim anyone can just edit. Flipping it back to
    # True silently re-exempted all five artifacts from the freeze and every other assertion
    # here still passed, so the flag is checked against what the JOB ACTUALLY DOES.
    assert nightly_overwrites_serving_in_source() == NIGHTLY_OVERWRITES_SERVING, (
        f"NIGHTLY_OVERWRITES_SERVING={NIGHTLY_OVERWRITES_SERVING} disagrees with "
        f"auto_finetune.py's source. The exemption must be justified by the job's behaviour, "
        "not by a flag - setting it by hand is how five serving artifacts get quietly unfrozen")
    names = {row["name"] for row in inventory}
    if not NIGHTLY_OVERWRITES_SERVING:
        assert not SCHEDULED_REFIT_ARTIFACTS, (
            "nothing scheduled overwrites serving, so nothing may be exempt from the freeze")
        for name in _REFIT_ARTIFACTS:
            if name in names:
                assert freeze_class(name) == PINNED, (
                    f"{name} is still exempt, but the nightly no longer overwrites serving - "
                    "a stale exemption leaves a serving artifact permanently unfrozen")
    unknown = SCHEDULED_REFIT_ARTIFACTS - names
    assert not unknown, (
        f"scheduled-refit names not present in the inventory: {sorted(unknown)} - a stale "
        "exemption silently un-pins nothing, or worse, hides a renamed artifact")
    pinned = [n for n in names if freeze_class(n) == PINNED]
    assert pinned, "every artifact was exempted - that is not a freeze"
    assert len(pinned) > len(SCHEDULED_REFIT_ARTIFACTS), (
        f"more artifacts are exempt ({len(SCHEDULED_REFIT_ARTIFACTS)}) than pinned "
        f"({len(pinned)}); the exemption has outgrown the rule")
    for name in SCHEDULED_REFIT_ARTIFACTS:
        assert freeze_class(name) == SCHEDULED_REFIT, name
    for name in pinned:
        assert freeze_class(name) == PINNED, name
    # The exemption is by NAME, so a pinned artifact can never be silently reclassified by
    # changing its bytes.
    assert freeze_class("architecture_version.pkl") == PINNED, (
        "the direction bundle must never be exempt")

    # LEGACY must not become the exemption by another name. It tolerates ONE specific hash per
    # artifact - the one already on record - so the very next change is a hard failure.
    recorded = recorded_refit_hashes()
    assert recorded, (
        "refit_history.jsonl is empty, so the LEGACY class can never match and the freeze "
        "would report already-recorded history as fresh drift on every run")
    for name, hashes in recorded.items():
        assert all(len(h) == 64 for h in hashes), name
        assert "0" * 64 not in hashes, (
            f"{name}: any hash outside the recorded set must read as DRIFT - LEGACY tolerates "
            "the bytes already on record, not arbitrary new bytes")
    # And the record cannot grow: appends happen only for SCHEDULED_REFIT, which is empty.
    assert not SCHEDULED_REFIT_ARTIFACTS, (
        "a non-empty scheduled-refit set would let an artifact append its own new hash and so "
        "explain its own drift")

    print(f"  SELFTEST PASS - drift detectable; {len(inventory)} artifacts classified into "
          f"{sorted(states)}; {len(pinned)} PINNED / "
          f"{len(SCHEDULED_REFIT_ARTIFACTS)} SCHEDULED_REFIT")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--copy", action="store_true",
                        help="copy AS_DEPLOYED artifact bytes into the release directory")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    print("=" * 96)
    print(f"ORACLE RELEASE FREEZE - {RELEASE_ID} (serving since {DEPLOYMENT_START})")
    print("=" * 96)
    if args.selftest:
        return selftest()
    if args.verify:
        return verify()

    manifest = build()
    summary = manifest["artifact_summary"]
    code = manifest["code"]
    print(f"  code commit          : {str(code['commit'])[:12]}  ({code['subject']})")
    print(f"  commits since        : {code['commits_since_deployment']}")
    print(f"  artifacts            : {summary['total']}")
    print(f"    AS_DEPLOYED        : {summary['as_deployed']}")
    print(f"    CHANGED since      : {summary['changed_since_deployment']}"
          "   <- deployed bytes are GONE")
    print(f"    serviceable now    : {summary['serviceable']}")
    print()
    for name in manifest["reproducibility"]["changed"]:
        print(f"    lost: {name}")
    print()
    print("  Acceptance test 'reproduce any historical Oracle prediction': NOT SATISFIABLE")
    print("  for predictions consuming the artifacts above. Recorded as fact, not hidden.")
    print()
    write(manifest, copy_artifacts=args.copy)
    print()
    print("  Re-run with --verify in CI to detect a frozen artifact changing underneath you.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
