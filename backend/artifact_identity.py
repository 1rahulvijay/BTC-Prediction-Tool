"""Training-data and model-artifact identity helpers.

Every production artifact must be tied to the exact data, feature schema, code,
split, and calibration interval that produced it.  Sidecar manifests are used so
existing joblib/pickle formats do not need to change.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import tempfile
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
MATRIX_PATH = DATA_DIR / "research_matrix_1m.parquet"
MATRIX_MANIFEST_PATH = DATA_DIR / "research_matrix_1m.manifest.json"
MODEL_RUNTIME_DISTRIBUTIONS = (
    "numpy",
    "scikit-learn",
    "xgboost",
    "lightgbm",
    "catboost",
    "joblib",
    "torch",
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def hash_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def hash_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_paths(paths: Iterable[str | os.PathLike[str]]) -> str:
    rows: list[dict[str, str]] = []
    for raw in sorted({str(Path(path).resolve()) for path in paths}):
        path = Path(raw)
        rows.append(
            {
                "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                "sha256": hash_file(path) if path.is_file() else "missing",
            }
        )
    return hash_json(rows)


def hash_directory(
    path: str | os.PathLike[str],
    exclude_names: Iterable[str] = ("artifact_manifest.json",),
) -> str:
    root = Path(path)
    excluded = set(exclude_names)
    rows: list[dict[str, str]] = []
    for file_path in sorted(p for p in root.rglob("*") if p.is_file()):
        if file_path.name in excluded:
            continue
        rows.append(
            {
                "path": file_path.relative_to(root).as_posix(),
                "sha256": hash_file(file_path),
            }
        )
    return hash_json(rows)


def hash_directory_files(
    path: str | os.PathLike[str], relative_files: Iterable[str]
) -> str:
    root = Path(path)
    rows: list[dict[str, str]] = []
    for relative in sorted(set(relative_files)):
        file_path = root / relative
        rows.append(
            {
                "path": Path(relative).as_posix(),
                "sha256": hash_file(file_path) if file_path.is_file() else "missing",
            }
        )
    return hash_json(rows)


def load_json(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def atomic_write_json(path: str | os.PathLike[str], value: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    finally:
        try:
            os.remove(tmp_name)
        except OSError:
            pass


def feature_schema_hash(feature_names: Iterable[str] | None) -> str:
    return hash_json(list(feature_names or []))


def configured_model_training_days() -> int | None:
    """Return the optional model span, independent of live boot-candle history."""
    raw = os.environ.get("BTC_MODEL_TRAINING_DAYS")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def model_runtime_versions() -> dict[str, str]:
    versions = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
    }
    for distribution in MODEL_RUNTIME_DISTRIBUTIONS:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "missing"
    return versions


def current_training_identity(
    *,
    requested_days: int | None = None,
    feature_names: Iterable[str] | None = None,
    code_paths: Iterable[str | os.PathLike[str]] | None = None,
    split_timestamps: dict[str, Any] | None = None,
    calibration_timestamps: dict[str, Any] | None = None,
    full_refit: bool = False,
) -> dict[str, Any]:
    matrix_manifest = load_json(MATRIX_MANIFEST_PATH)
    summary = matrix_manifest.get("summary") or {}
    matrix_hash = matrix_manifest.get("training_data_hash")
    if not matrix_hash and MATRIX_PATH.is_file():
        matrix_hash = hash_file(MATRIX_PATH)

    source_identity = {
        "source_files": matrix_manifest.get("source_files") or {},
        "source_mtimes": matrix_manifest.get("source_mtimes") or {},
        "source_coverage": matrix_manifest.get("source_coverage") or {},
        "monthly_quality_hash": matrix_manifest.get("monthly_quality_hash"),
        "ohlc_provenance": matrix_manifest.get("ohlc_provenance") or {},
    }
    schema_hash = (
        feature_schema_hash(feature_names)
        if feature_names is not None
        else matrix_manifest.get("feature_schema_hash")
    )
    code_files = list(code_paths or [])
    runtime_versions = model_runtime_versions()
    return {
        "manifest_version": 1,
        "requested_days": int(
            requested_days
            if requested_days is not None
            else matrix_manifest.get("requested_days", 0) or 0
        ),
        "actual_start_ts_ms": summary.get("min_ts_ms"),
        "actual_end_ts_ms": summary.get("max_ts_ms"),
        "actual_span_days": summary.get("span_days"),
        "row_count": summary.get("rows"),
        "matrix_requested_days": int(
            matrix_manifest.get("requested_days", 0) or 0
        ),
        "matrix_coverage_ok": bool(matrix_manifest.get("coverage_ok", False)),
        "matrix_monthly_quality_passed": bool(
            (matrix_manifest.get("monthly_quality") or {}).get("passed", False)
        ),
        "training_data_hash": matrix_hash,
        "source_manifest_hash": hash_json(source_identity),
        "feature_schema_hash": schema_hash,
        "code_hash": hash_paths(code_files) if code_files else None,
        "runtime_versions": runtime_versions,
        "runtime_dependency_hash": hash_json(runtime_versions),
        "split_timestamps": split_timestamps or {},
        "calibration_timestamps": calibration_timestamps or {},
        "full_refit": bool(full_refit),
    }


def training_identity_issues(identity: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    requested = int(identity.get("requested_days", 0) or 0)
    matrix_requested = int(identity.get("matrix_requested_days", 0) or 0)
    span = float(identity.get("actual_span_days", 0.0) or 0.0)
    rows = int(identity.get("row_count", 0) or 0)
    if requested <= 0:
        issues.append("requested_days is missing")
    if matrix_requested != requested:
        issues.append(
            f"matrix requested_days={matrix_requested} does not match requested_days={requested}"
        )
    if requested and span < requested * 0.90:
        issues.append(
            f"actual span {span:.1f}d is below 90% of requested {requested}d"
        )
    if requested and rows < int(requested * 1440 * 0.80):
        issues.append(
            f"row_count {rows} is below 80% of requested minute rows"
        )
    if not identity.get("matrix_coverage_ok"):
        issues.append("matrix coverage gate is not recorded as passed")
    if not identity.get("matrix_monthly_quality_passed"):
        issues.append("matrix monthly-quality gate is not recorded as passed")
    for key in (
        "training_data_hash",
        "source_manifest_hash",
        "feature_schema_hash",
        "runtime_dependency_hash",
    ):
        if not identity.get(key):
            issues.append(f"{key} is missing")
    return issues


def artifact_manifest_path(
    artifact_path: str | os.PathLike[str],
) -> Path:
    path = Path(artifact_path)
    if path.is_dir() or (not path.suffix and not path.exists()):
        return path / "artifact_manifest.json"
    return path.with_name(f"{path.name}.manifest.json")


def write_artifact_manifest(
    artifact_path: str | os.PathLike[str],
    identity: dict[str, Any],
    *,
    artifact_type: str,
    extra: dict[str, Any] | None = None,
) -> Path:
    artifact = Path(artifact_path)
    extra_values = extra or {}
    if artifact.is_dir():
        artifact_files = extra_values.get("artifact_files")
        artifact_hash = (
            hash_directory_files(artifact, artifact_files)
            if artifact_files
            else hash_directory(artifact)
        )
    else:
        artifact_hash = hash_file(artifact)
    manifest = {
        **identity,
        "artifact_type": artifact_type,
        "artifact_hash": artifact_hash,
        **extra_values,
    }
    path = artifact_manifest_path(artifact)
    atomic_write_json(path, manifest)
    return path


def artifact_compatibility(
    artifact_path: str | os.PathLike[str],
    expected: dict[str, Any],
    *,
    strict: bool = True,
) -> tuple[bool, list[str]]:
    artifact = Path(artifact_path)
    manifest = load_json(artifact_manifest_path(artifact))
    if not manifest:
        return (not strict, ["missing artifact manifest"])

    reasons: list[str] = []
    keys = (
        "requested_days",
        "matrix_requested_days",
        "actual_start_ts_ms",
        "actual_end_ts_ms",
        "actual_span_days",
        "row_count",
        "matrix_coverage_ok",
        "matrix_monthly_quality_passed",
        "training_data_hash",
        "source_manifest_hash",
        "feature_schema_hash",
        "code_hash",
        "runtime_dependency_hash",
    )
    for key in keys:
        expected_value = expected.get(key)
        if expected_value is None:
            continue
        if manifest.get(key) != expected_value:
            reasons.append(
                f"{key} mismatch: artifact={manifest.get(key)!r} current={expected_value!r}"
            )

    if artifact.exists():
        if artifact.is_dir() and manifest.get("artifact_files"):
            actual_hash = hash_directory_files(
                artifact, manifest["artifact_files"]
            )
        else:
            actual_hash = (
                hash_directory(artifact)
                if artifact.is_dir()
                else hash_file(artifact)
            )
        if manifest.get("artifact_hash") != actual_hash:
            reasons.append("artifact hash mismatch")
    else:
        reasons.append("artifact is missing")
    return not reasons, reasons


def artifact_matches_current_training(
    artifact_path: str | os.PathLike[str],
    *,
    requested_days: int | None = None,
    strict: bool | None = None,
) -> tuple[bool, list[str]]:
    if strict is None:
        strict = os.environ.get(
            "BTC_STRICT_ARTIFACT_IDENTITY", "1"
        ).strip().lower() not in ("0", "false", "no")
    if requested_days is None:
        requested_days = configured_model_training_days()
    expected = current_training_identity(requested_days=requested_days)
    return artifact_compatibility(
        artifact_path, expected, strict=bool(strict)
    )


def selftest() -> None:
    old_boot_days = os.environ.get("BTC_HISTORICAL_DAYS")
    old_model_days = os.environ.get("BTC_MODEL_TRAINING_DAYS")
    try:
        os.environ["BTC_HISTORICAL_DAYS"] = "3"
        os.environ.pop("BTC_MODEL_TRAINING_DAYS", None)
        assert configured_model_training_days() is None
        os.environ["BTC_MODEL_TRAINING_DAYS"] = "1265"
        assert configured_model_training_days() == 1265
    finally:
        if old_boot_days is None:
            os.environ.pop("BTC_HISTORICAL_DAYS", None)
        else:
            os.environ["BTC_HISTORICAL_DAYS"] = old_boot_days
        if old_model_days is None:
            os.environ.pop("BTC_MODEL_TRAINING_DAYS", None)
        else:
            os.environ["BTC_MODEL_TRAINING_DAYS"] = old_model_days
    with tempfile.TemporaryDirectory() as tmp:
        artifact = Path(tmp) / "model.pkl"
        artifact.write_bytes(b"model")
        identity = {
            "requested_days": 90,
            "actual_end_ts_ms": 123,
            "training_data_hash": "data",
            "source_manifest_hash": "sources",
            "feature_schema_hash": "schema",
            "code_hash": "code-v1",
            "runtime_dependency_hash": "runtime-v1",
        }
        write_artifact_manifest(artifact, identity, artifact_type="selftest")
        ok, reasons = artifact_compatibility(artifact, identity)
        assert ok and not reasons
        changed_code = {**identity, "code_hash": "code-v2"}
        ok, reasons = artifact_compatibility(artifact, changed_code)
        assert not ok and any("code_hash mismatch" in reason for reason in reasons)
        changed_runtime = {**identity, "runtime_dependency_hash": "runtime-v2"}
        ok, reasons = artifact_compatibility(artifact, changed_runtime)
        assert not ok and any(
            "runtime_dependency_hash mismatch" in reason for reason in reasons
        )
        artifact.write_bytes(b"tampered")
        ok, reasons = artifact_compatibility(artifact, identity)
        assert not ok and "artifact hash mismatch" in reasons
    print("artifact_identity self-test: ALL PASS")


if __name__ == "__main__":
    selftest()
