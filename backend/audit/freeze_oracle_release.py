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

    drifted, vanished, appeared = [], [], []
    for name, row in frozen.items():
        if name not in current:
            vanished.append(name)
        elif current[name]["sha256"] != row["sha256"]:
            drifted.append(name)
    for name in current:
        if name not in frozen:
            appeared.append(name)

    print(f"  frozen {len(frozen)} | drifted {len(drifted)} | vanished {len(vanished)} | "
          f"new {len(appeared)}")
    for label, names in (("DRIFTED", drifted), ("VANISHED", vanished)):
        for name in names:
            print(f"    {label}: {name}")
    if appeared:
        print(f"    new since the freeze (not an error): {', '.join(sorted(appeared))}")
    if drifted or vanished:
        print("\n  FAIL - a frozen artifact changed. If this was a deliberate retrain, the "
              "champion identity has moved and a NEW release id is required; overwriting the "
              "freeze in place would erase the only record of what produced the live sample.")
        return 1
    print("\n  PASS - every frozen artifact still hashes to its recorded value.")
    return 0


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
    print(f"  SELFTEST PASS - drift detectable; {len(inventory)} artifacts classified into "
          f"{sorted(states)}")
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
