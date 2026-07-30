"""Executing serving-integration tests: real bundles, real pointer swaps, real loads.

WHY THIS FILE EXISTS
    The previous "serving integration" test grepped loader source for the string
    `resolve_artifact` and called `resolve_artifact()` on its own. It never executed a loader
    against a real bundle, never swapped `champion.json`, and never checked that the loaded model
    actually changed. That is the same inspect-only pattern that let `own_book` crash a full
    green suite.

    It also could not have caught the defect it was supposed to guard: loaders cached on file
    MTIME, so two bundles sharing an mtime meant a champion swap left the OLD model loaded while
    `MODEL_PATH` reported the new one.

    Every test here builds bundles on disk and calls `load_model()` for real.

    python -m backend.trade_forecast.test_serving_integration
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import joblib

from ..verified_io import file_sha256, write_manifest as write_integrity_manifest
from .trade_schema import CONFIG_VERSION, MODE

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _write_bundle(directory: Path, artifact: str, marker: str, mtime: float) -> Path:
    """A minimal loadable bundle whose model carries an identifying marker."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / artifact
    joblib.dump({"version": CONFIG_VERSION, "mode": MODE, "marker": marker,
                 "horizons": {}}, path)
    write_integrity_manifest(path)
    manifest = {"artifact_sha256": file_sha256(path), "policy_hash": "p" * 64,
                "feature_columns": [], "training_status": "TEST"}
    # NOTE: model_common.artifact_issues() resolves the manifest with .with_suffix(), i.e.
    # "x.manifest.json" (suffix REPLACED), while artifact_identity.artifact_manifest_path()
    # builds "x.pkl.manifest.json" (suffix APPENDED). Two conventions coexist in this repo; the
    # complete-trade loaders use the first, and that is what a bundle for them must contain.
    manifest_path = path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    # Identical mtimes on BOTH bundles is the whole point of this fixture.
    for target in (path, manifest_path, Path(f"{path}.integrity.json")):
        os.utime(target, (mtime, mtime))
    return path


def _point(pointer: Path, bundle: Path) -> None:
    from .champion_resolver import _file_hash, bundle_hash

    manifest_path = bundle / "bundle_manifest.json"
    entries = []
    for path in sorted(bundle.rglob("*")):
        if path.is_file() and path != manifest_path:
            entries.append({
                "path": path.relative_to(bundle).as_posix(),
                "size": path.stat().st_size,
                "sha256": _file_hash(path),
            })
    manifest_path.write_text(
        json.dumps({"manifest_version": 1, "entries": entries}, sort_keys=True),
        encoding="utf-8",
    )
    pointer.write_text(
        json.dumps({
            "bundle_hash": bundle_hash(bundle),
            "bundle_manifest_sha256": _file_hash(manifest_path),
            "path": str(bundle),
            "promoted_at": 1_700_000_000.0,
        }),
        encoding="utf-8",
    )


def _run_for(module, artifact: str) -> None:
    from . import champion_resolver

    name = module.__name__.rsplit(".", 1)[-1]
    print(f"  -- {name}")
    # ISOLATION, stated plainly: this file tests RESOLUTION and CACHE IDENTITY - does a champion
    # swap change the served model. Full artifact-manifest validation (dataset version, policy
    # hash, feature schema, artifact hash, code hash, training-dataset presence) is a separate
    # concern with its own coverage in promote_challenger and artifact_identity; reproducing a
    # fully valid manifest here would test that instead of the thing under test. The stub returns
    # "no issues" so a real pointer swap is the only variable.
    original_issues = module.artifact_issues
    module.artifact_issues = lambda *a, **k: ({"artifact_sha256": "stub"}, [])
    try:
        _exercise(module, artifact, champion_resolver)
    finally:
        module.artifact_issues = original_issues


def _exercise(module, artifact: str, champion_resolver) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pointer = root / "champion.json"
        shared_mtime = 1_700_000_000.0
        a = root / "bundle_a"
        b = root / "bundle_b"
        _write_bundle(a, artifact, "MARKER_A", shared_mtime)
        _write_bundle(b, artifact, "MARKER_B", shared_mtime)      # SAME mtime, different model
        chk(
            (a / artifact).stat().st_mtime == (b / artifact).stat().st_mtime,
            "fixture: both bundles share an identical mtime",
        )

        champion_resolver.POINTER = pointer
        champion_resolver._PINNED.clear()
        module._TOKEN = None
        module._MTIME = -1.0
        module._CHECKED = 0.0

        _point(pointer, a)
        loaded = module.load_model(force=True)
        chk(loaded is not None and loaded.get("marker") == "MARKER_A",
            "bundle A loads through the champion pointer")

        _point(pointer, b)
        module._CHECKED = 0.0                      # allow a re-check, as a later tick would
        loaded = module.load_model()
        chk(loaded is not None and loaded.get("marker") == "MARKER_B",
            "after the pointer swap the loader returns B (mtime is NOT identity)")
        chk(module.status().get("bundle_verified") is True,
            "status reports the bundle as verified")
        chk(module.status().get("resolution_source") == "champion_pointer",
            "status reports resolution through the pointer, not a silent fallback")

        # Freeze: a mid-run swap must not take effect.
        os.environ["BTC_FREEZE_MODEL"] = "1"
        champion_resolver._PINNED.clear()
        module._TOKEN = None
        module._MTIME = -1.0
        module._CHECKED = 0.0
        module.load_model(force=True)              # pins B
        _point(pointer, a)
        module._CHECKED = 0.0
        loaded = module.load_model()
        chk(loaded is not None and loaded.get("marker") == "MARKER_B",
            "under freeze a mid-run promotion does NOT change the served model")
        os.environ.pop("BTC_FREEZE_MODEL", None)

        # A restart (pin cleared) adopts the new pointer.
        champion_resolver._PINNED.clear()
        module._TOKEN = None
        module._MTIME = -1.0
        module._CHECKED = 0.0
        loaded = module.load_model(force=True)
        chk(loaded is not None and loaded.get("marker") == "MARKER_A",
            "after a controlled restart the new champion is adopted")

        # Missing artifact inside an otherwise valid bundle.
        empty = root / "bundle_empty"
        empty.mkdir()
        (empty / "unrelated.txt").write_text("x", encoding="utf-8")
        _point(pointer, empty)
        os.environ["BTC_EVIDENCE_MODE"] = "1"
        champion_resolver._PINNED.clear()
        module._TOKEN = None
        module._MTIME = -1.0
        module._CHECKED = 0.0
        loaded = module.load_model(force=True)
        chk(loaded is None,
            "EVIDENCE MODE: artifact missing from a valid bundle -> NO MODEL")
        chk(module.status().get("evidence_mode") is True,
            "status reports evidence mode during the refusal")

        # Corrupt pointer under evidence mode.
        pointer.write_text("{ not json", encoding="utf-8")
        champion_resolver._PINNED.clear()
        module._TOKEN = None
        module._MTIME = -1.0
        module._CHECKED = 0.0
        chk(module.load_model(force=True) is None,
            "EVIDENCE MODE: corrupt champion.json -> NO MODEL, never legacy bytes")
        os.environ.pop("BTC_EVIDENCE_MODE", None)
        champion_resolver._PINNED.clear()
        module._TOKEN = None


def run() -> int:
    from . import btc_path_serving, execution_serving, share_path_serving

    print("serving integration (executing, parametrized over all three loaders)")
    previous_freeze = os.environ.get("BTC_FREEZE_MODEL")
    previous_evidence = os.environ.get("BTC_EVIDENCE_MODE")
    try:
        for module, artifact in (
            (share_path_serving, "complete_trade_share_path.pkl"),
            (btc_path_serving, "complete_trade_btc_path.pkl"),
            (execution_serving, "complete_trade_execution_heads.pkl"),
        ):
            _run_for(module, artifact)
    finally:
        for key, value in (("BTC_FREEZE_MODEL", previous_freeze),
                           ("BTC_EVIDENCE_MODE", previous_evidence)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print("\nSERVING INTEGRATION", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    sys.exit(run())
