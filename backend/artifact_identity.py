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
    """Schema hash from an iterable of column names.

    `feature_names or []` raised on the real caller. `build_research_matrix` passes
    `merged.columns`, a pandas Index, and `or` evaluates truthiness - which an Index with more
    than one element refuses to answer:

        ValueError: The truth value of a Index is ambiguous.

    It killed a 1,000-day rebuild at the manifest write, AFTER every day had been downloaded
    and 1,440,000 rows built. The idiom is only safe for containers whose emptiness is a bool;
    an explicit None check works for any iterable, including generators, where `or` would also
    consume nothing and silently hash an empty list.
    """
    if feature_names is None:
        return hash_json([])
    return hash_json([str(name) for name in feature_names])


def configured_model_training_days() -> int | None:
    """Was an EXPLICIT model-span override set? `None` means "nobody said", not "zero days".

    NEVER pass this into `current_training_identity`. It answers a different question from
    `resolve_history_days`, and the difference is a silent hole:

        save  ->  resolve_history_days()          env x3 -> matrix manifest -> 60, never None
        load  ->  configured_model_training_days() ONE env var, else None

    and `artifact_compatibility` SKIPS any expected key that is None. So a bundle stamped
    requested_days=1000 loaded into a process where BTC_MODEL_TRAINING_DAYS happened to be
    unset did not fail the window check - the check silently ceased to exist, which is the
    "absent reads as pass" defect this module exists to prevent. Reached whenever the server
    is started by anything other than start.bat, since only the launcher sets that variable;
    `HISTORY_DAYS_ENV_ORDER` below already calls that "a convention, not a control".

    Use `resolve_history_days()` for identity. Use this only to ask whether an operator set
    the override, which is what it was written for.
    """
    raw = os.environ.get("BTC_MODEL_TRAINING_DAYS")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


#: Every environment name that has meant "how many days of history does this run use", in
#: precedence order. The FIRST one set wins, and this order applies EVERYWHERE.
#:
#: It did not. The same setting was resolved at nine sites with five different defaults (30, 0,
#: 60, 360, "na") and three different precedences - `model.py` read HISTORICAL then BACKFILL when
#: training and BACKFILL then HISTORICAL when saving, so one run could stamp two disagreeing
#: identities. `start.bat` papered over it with
#:     if not defined BTC_MODEL_TRAINING_DAYS set "BTC_MODEL_TRAINING_DAYS=%BTC_HISTORICAL_DAYS%"
#: which is the P0-21 lesson again: alignment that only holds when someone uses the right
#: launcher is a convention, not a control.
HISTORY_DAYS_ENV_ORDER = (
    "BTC_MODEL_TRAINING_DAYS",
    "BTC_HISTORICAL_DAYS",
    "BTC_BACKFILL_DAYS",
)

#: Reached only when nothing else answers: no override set AND no matrix manifest on disk.
HISTORY_DAYS_LAST_RESORT = 60


def resolve_history_days_verbose() -> tuple[int, str]:
    """THE training window for this process, and WHERE it came from.

    The source matters as much as the number. A window that silently differs from the matrix
    the artifacts are stamped against does not fail at boot - it fails ~90 seconds later,
    inside a worker thread, as

        training-data identity contract failed: requested_days is missing;
        matrix requested_days=60 does not match requested_days=0

    which names neither the environment variable nor the manifest. Measured: a direct
    `python server.py` booted on 30 days (server's own default), built features from them, and
    then refused to train because the manifest on disk records 60. The app came up healthy,
    served, and could never produce a model.

    Falling back to the MANIFEST rather than to a literal is the point. The manifest records
    what the training data actually is; a hardcoded default is a guess that happened to
    disagree with it.
    """
    for name in HISTORY_DAYS_ENV_ORDER:
        raw = os.environ.get(name)
        if raw is None or not str(raw).strip():
            continue
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            # A malformed override is NOT silently treated as unset - that would hand back a
            # different window than the operator asked for, which is the whole defect.
            raise ValueError(
                f"{name}={raw!r} is not an integer number of days; refusing to guess a "
                f"training window")
        if value > 0:
            return value, f"env:{name}"
    manifest_days = int((load_json(MATRIX_MANIFEST_PATH) or {}).get("requested_days", 0) or 0)
    if manifest_days > 0:
        return manifest_days, f"manifest:{MATRIX_MANIFEST_PATH.name}"
    return HISTORY_DAYS_LAST_RESORT, "last_resort_default"


def resolve_history_days() -> int:
    """The window only. Use `resolve_history_days_verbose` when the source should be logged."""
    return resolve_history_days_verbose()[0]


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


def _current_semantics_versions() -> dict[str, Any]:
    """The versions the checker compares against. Imported lazily to avoid an import cycle.

    Absent means ABSENT - never a placeholder. A fabricated version would let a stale artifact
    certify itself as current, which is the exact failure this provenance exists to prevent.
    """
    feature = training = None
    try:
        from features import FEATURE_SEMANTICS_VERSION as _f
        feature = _f
    except Exception:
        pass
    try:
        from model import TRAINING_SEMANTICS_VERSION as _t
        training = _t
    except Exception:
        pass
    return {"feature": feature, "training": training}


def _source_commit_state() -> tuple[str | None, bool | None]:
    """(commit, dirty). None on either side means unprovable, and the checker refuses that.

    `code_dirty` is not decoration: an artifact trained from a modified working tree cannot be
    reproduced from its commit, so the checker treats anything other than a definite False as
    unverifiable.
    """
    import subprocess

    root = str(Path(__file__).resolve().parents[1])
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, timeout=15).stdout.strip() or None
    except Exception:
        return None, None
    try:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                capture_output=True, text=True, timeout=15)
        if status.returncode != 0:
            return commit, None
        dirty = bool(status.stdout.strip())
    except Exception:
        return commit, None
    return commit, dirty


def current_training_identity(
    *,
    requested_days: int | None = None,
    feature_names: Iterable[str] | None = None,
    code_paths: Iterable[str | os.PathLike[str]] | None = None,
    split_timestamps: dict[str, Any] | None = None,
    calibration_timestamps: dict[str, Any] | None = None,
    full_refit: bool = False,
    executed: dict[str, Any] | None = None,
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
    # Same shape as feature_schema_hash above: `or` on a non-list iterable either raises
    # (pandas) or, worse, consumes a generator and silently records ZERO code files - a
    # training identity asserting no semantic source was hashed. Explicit None check instead.
    code_files = [] if code_paths is None else [str(path) for path in code_paths]
    runtime_versions = model_runtime_versions()

    # ---- THE SERVING CONTRACT -------------------------------------------------------------
    # check_feature_contract.verdict_for is what serving consults, and it fails CLOSED on any
    # key it cannot read. It demanded nine keys; this function emitted NONE of them under those
    # names, and four did not exist in any form:
    #
    #   feature_semantics_version   never written  <- the entire point of the check
    #   training_semantics_version  never written
    #   training_cutoff             never written  <- places the artifact in time
    #   code_dirty                  never written  <- was it trained from a dirty tree
    #   artifact_sha256             written as artifact_hash
    #   feature_schema_sha256       written as feature_schema_hash
    #   training_dataset_sha256     written as training_data_hash
    #   code_commit                 written as code_hash (a DIFFERENT thing: content vs commit)
    #
    # So a retrain wrote a manifest that the checker still rejected as UNKNOWN, and the gate
    # could never go green no matter how many times the model was rebuilt. That is why the
    # right repair is to make the two sides agree on one contract - not to relax the checker,
    # which is the one component correctly refusing to certify what it cannot read.
    #
    # The legacy names are kept alongside so existing readers (artifact_compatibility, the
    # oracle freeze) continue to work.
    semantics = _current_semantics_versions()
    commit, dirty = _source_commit_state()
    identity = {
        "manifest_version": 1,
        "feature_semantics_version": semantics["feature"],
        "training_semantics_version": semantics["training"],
        "training_cutoff": (split_timestamps or {}).get("train_end_ts_ms")
                           or summary.get("max_ts_ms"),
        "code_commit": commit,
        "code_dirty": dirty,
        "feature_schema_sha256": schema_hash,
        "training_dataset_sha256": matrix_hash,
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
    # THE EXECUTED DATASET, beside the matrix one rather than instead of it.
    #
    # Every field above describes research_matrix_1m.parquet. `train()` is handed in-memory
    # X/Y built from freshly fetched klines, so the manifest could truthfully hash the matrix
    # while certifying a model trained on different data (2.1). Both are now recorded, and
    # `executed_matches_matrix` says whether they agree - "two, and they differ" is a far more
    # useful answer than one number that describes the wrong dataset.
    if executed:
        identity.update(executed)
        _mrows = int((summary or {}).get("rows") or 0)
        _erows = int(executed.get("executed_rows") or 0)
        # THE TWO HASHES ARE NOT COMPARABLE, AND SAYING SO IS THE HONEST ANSWER.
        #
        # This compared `executed_feature_matrix_sha256` - a sha256 over the in-memory NumPy
        # X bytes - against `matrix_hash`, which is `hash_file(research_matrix_1m.parquet)`
        # or the manifest's file hash. A tensor-byte digest and a Parquet-file digest are
        # different domains: measured, logically IDENTICAL data hashes differently, so the
        # flag was structurally False for every training run that ever recorded one.
        #
        # That matters more than it looks. The flag reads like "the executed data disagrees
        # with the matrix", so enforcing it - which is the obvious next step and was
        # explicitly proposed - would have rejected every honest retrain forever.
        #
        # A real comparison needs ONE canonical logical-row hash computed the same way on
        # both sides. Until that exists, this reports None (not comparable) with the reason,
        # and publishes the facts that ARE comparable so a reader can judge.
        _ehash = executed.get("executed_feature_matrix_sha256")
        if not (_ehash and matrix_hash):
            identity["executed_matches_matrix"] = None
            identity["executed_matrix_comparison_basis"] = "HASH_MISSING"
        else:
            identity["executed_matches_matrix"] = None
            identity["executed_matrix_comparison_basis"] = (
                "HASH_DOMAINS_NOT_COMPARABLE:"
                "executed=numpy_tensor_bytes vs matrix=parquet_file_digest")
        identity["executed_rows_match_matrix_rows"] = bool(
            _mrows and _erows and _mrows == _erows)
        identity["executed_identity_recorded"] = True
    else:
        identity["executed_identity_recorded"] = False
        identity["executed_matches_matrix"] = None
        identity["executed_matrix_comparison_basis"] = "NO_EXECUTED_IDENTITY"
        identity["executed_rows_match_matrix_rows"] = None
    return identity


def executed_training_identity(X, Y, *, valid_mask=None, regime_labels=None,
                               decision_timestamps=None) -> dict[str, Any]:
    """Hash the arrays the model is ACTUALLY about to train on.

    THE DEFECT THIS CLOSES (scan-2 item 2.1, the keystone).

    `current_training_identity` describes `research_matrix_1m.parquet` and its manifest -
    training_data_hash, row_count, coverage_ok, actual_start/end, monthly_quality_passed - while
    `train()` receives in-memory X and Y built from `data_state["klines"]`, fetched fresh from
    Binance at boot. Measured: the manifest records 86,400 rows and hash 281657b2..., written at
    04:05, and the live run trains on 86,400 freshly-fetched bars. SAME COUNT, DIFFERENT DATA.

    So the manifest could truthfully hash the research matrix while certifying a model trained
    on something else, and every downstream gate that reads it - artifact_compatibility, the
    oracle freeze, check_feature_contract - was certifying provenance for the wrong dataset.

    This does not replace the matrix identity; it sits BESIDE it under `executed_*` keys, so a
    reader can see both and tell whether they agree. Answering "which dataset was this trained
    on?" with a number that describes a different dataset is worse than answering "two, and they
    differ" - and that is exactly what `executed_matches_matrix` now reports.
    """
    import numpy as _np

    def _arr_hash(a) -> str | None:
        if a is None:
            return None
        arr = _np.ascontiguousarray(_np.asarray(a))
        h = hashlib.sha256()
        h.update(str(arr.dtype).encode())
        h.update(str(arr.shape).encode())
        # The BYTES, not a summary. A mean or a min/max collides trivially and would let two
        # different training sets certify as one.
        h.update(arr.tobytes())
        return h.hexdigest()

    x = _np.asarray(X)
    per_horizon = {}
    for key in sorted((Y or {}).keys(), key=lambda k: str(k)):
        per_horizon[str(key)] = {
            "labels_sha256": _arr_hash((Y or {})[key]),
            "rows": int(len(_np.asarray((Y or {})[key]))),
            "valid_mask_sha256": _arr_hash((valid_mask or {}).get(key)),
        }
    ts = None if decision_timestamps is None else _np.asarray(decision_timestamps)
    return {
        "executed_feature_matrix_sha256": _arr_hash(x),
        "executed_rows": int(x.shape[0]) if x.ndim else 0,
        "executed_shape": list(x.shape),
        "executed_labels": per_horizon,
        "executed_regime_labels_sha256": _arr_hash(
            None if regime_labels is None else _np.asarray(list(regime_labels), dtype=object).astype("U")),
        "executed_decision_ts_sha256": _arr_hash(ts),
        "executed_first_ts_ms": int(ts.min()) if ts is not None and ts.size else None,
        "executed_last_ts_ms": int(ts.max()) if ts is not None and ts.size else None,
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
        # The serving checker reads `artifact_sha256`; existing readers read `artifact_hash`.
        # Both are the same value, written under both names so neither side has to guess.
        "artifact_sha256": artifact_hash,
        **extra_values,
    }
    path = artifact_manifest_path(artifact)
    atomic_write_json(path, manifest)
    return path


#: The identity fields `artifact_compatibility` compares. Declared at module scope so
#: `unverifiable_identity_keys` reports on the SAME list the comparison walks, rather than a
#: copy that can drift away from it.
COMPARED_IDENTITY_KEYS = (
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


def unverifiable_identity_keys(expected: dict[str, Any]) -> list[str]:
    """Which compared keys will be SKIPPED because the expected side is None.

    `artifact_compatibility` cannot refuse on these - several are legitimately absent (a matrix
    manifest that records no row_count, for instance), and failing closed on all of them would
    reject every honest bundle on disk today. But a skipped check that leaves no trace is
    indistinguishable from a passed one, and that is what let the requested_days hole sit open.

    So the skip stays and becomes VISIBLE: callers log what they could not establish instead of
    implying they established it.
    """
    return [key for key in COMPARED_IDENTITY_KEYS if expected.get(key) is None]


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
    for key in COMPARED_IDENTITY_KEYS:
        expected_value = expected.get(key)
        if expected_value is None:
            continue
        if manifest.get(key) != expected_value:
            reasons.append(
                f"{key} mismatch: artifact={manifest.get(key)!r} current={expected_value!r}"
            )

    # THE EXECUTED IDENTITY IS ENFORCED FOR WHAT IT CAN PROVE.
    #
    # `executed_training_identity` hashes the arrays the model was actually fitted on, and
    # nothing consulted it - a manifest could record that the executed data differed from the
    # research matrix and still pass compatibility.
    #
    # What is enforceable HERE is the RECORDING, not the hash. At load time the training
    # arrays are long gone, so `executed_feature_matrix_sha256` cannot be recomputed and
    # compared against anything. And `executed_matches_matrix` must NOT be enforced: it
    # compares a NumPy tensor digest against a Parquet file digest, which never agree even
    # for identical data, so requiring it would reject every honest retrain.
    #
    # So the rule is: under strict identity, a bundle must be able to SAY what it trained on.
    # A bundle that cannot is unprovable, which is the same standard the manifest requirement
    # itself applies.
    if strict and manifest.get("executed_identity_recorded") is not True:
        reasons.append(
            "executed_identity_recorded is not True: the bundle does not record the data it "
            "was actually fitted on, so the training set cannot be attested"
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
        # resolve_history_days, NOT configured_model_training_days: the latter returns None
        # when BTC_MODEL_TRAINING_DAYS is unset, and a None expected value makes
        # artifact_compatibility SKIP the window check rather than fail it.
        requested_days = resolve_history_days()
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

        # THE ASYMMETRY THAT OPENED THE HOLE. With only BTC_HISTORICAL_DAYS set, the narrow
        # resolver answers None and the canonical one answers 3. A None expected value makes
        # artifact_compatibility SKIP the window comparison, so the check does not fail - it
        # stops existing. Both ends of save/load must therefore use the canonical resolver.
        os.environ.pop("BTC_MODEL_TRAINING_DAYS", None)
        assert configured_model_training_days() is None
        assert resolve_history_days() == 3, "canonical resolver always answers"
        assert "requested_days" in unverifiable_identity_keys({"requested_days": None})
        assert "requested_days" not in unverifiable_identity_keys({"requested_days": 3})
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
            "executed_identity_recorded": True,
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
        # A WRONG window is caught when the expected side answers...
        wrong_window = {**identity, "requested_days": 1000}
        ok, reasons = artifact_compatibility(artifact, wrong_window)
        assert not ok and any("requested_days mismatch" in reason for reason in reasons), \
            "a 90d bundle must not satisfy a 1000d expectation"

        # ...and SILENTLY ACCEPTED when it does not. This is the hole, asserted rather than
        # described: same artifact, same disagreement, no complaint - because None skips.
        blind_window = {**identity, "requested_days": None}
        ok, reasons = artifact_compatibility(artifact, blind_window)
        assert ok and not reasons, "None must skip - the reason the caller may never pass one"
        assert "requested_days" in unverifiable_identity_keys(blind_window), \
            "and the skip must be REPORTABLE, or it is indistinguishable from a pass"

        artifact.write_bytes(b"tampered")
        ok, reasons = artifact_compatibility(artifact, identity)
        assert not ok and "artifact hash mismatch" in reasons
    print("artifact_identity self-test: ALL PASS")


if __name__ == "__main__":
    selftest()
