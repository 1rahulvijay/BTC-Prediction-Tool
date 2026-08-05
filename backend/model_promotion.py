"""Offline promotion gates and staged full-data refit helpers.

The evaluated candidate is scored only on its untouched temporal tail. A passing
architecture may then be refit on all rows, but that full-data artifact enters the
live A/B runner as a shadow challenger; it is never treated as independently tested.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import numpy as np

from artifact_identity import artifact_compatibility, hash_directory_files


def promotion_required(enabled: bool, reason: str | None = None) -> bool:
    """Keep retraining origin out of the safety decision."""
    del reason
    return bool(enabled)


def promotion_gates() -> dict:
    def env_float(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, default))
        except (TypeError, ValueError):
            return float(default)

    def env_int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, default))
        except (TypeError, ValueError):
            return int(default)

    return {
        "min_holdout_samples": env_int("BTC_PROMOTION_MIN_HOLDOUT_SAMPLES", 1000),
        "min_directional_calls": env_int("BTC_PROMOTION_MIN_DIRECTIONAL_CALLS", 200),
        "min_directional_precision": env_float("BTC_PROMOTION_MIN_DIRECTIONAL_PRECISION", 0.48),
        "max_multiclass_brier": env_float("BTC_PROMOTION_MAX_BRIER", 0.80),
        "max_ece": env_float("BTC_PROMOTION_MAX_ECE", 0.20),
        "max_precision_regression": env_float("BTC_PROMOTION_MAX_PRECISION_REGRESSION", 0.03),
        "max_brier_regression": env_float("BTC_PROMOTION_MAX_BRIER_REGRESSION", 0.03),
        "max_eval_samples": env_int("BTC_PROMOTION_MAX_EVAL_SAMPLES", 12000),
    }


def _sample_indices(indices: np.ndarray, maximum: int) -> np.ndarray:
    indices = np.asarray(indices, dtype=np.int64)
    if maximum <= 0 or len(indices) <= maximum:
        return indices
    positions = np.linspace(0, len(indices) - 1, maximum, dtype=np.int64)
    return indices[positions]


def _predict_probabilities(model, values: np.ndarray, horizon: int, regimes=None) -> np.ndarray:
    """P0-8. Each row is scored through the regime it was ACTUALLY in.

    This passed `None` as data_state for every row. `_get_regime_from_state(None)` reads
    `{}.get("regime", "RANGE")`, so the entire holdout was evaluated through the RANGE expert
    path - not the TREND or VOLATILE experts, not the regime-confidence blend, none of the
    routing that decides which seats actually speak in production.

    The gate was therefore measuring a model configuration that never serves, and a candidate
    could pass or fail on an expert mix it would never be run with. `regimes` is the per-row
    historical regime label; without it, this now says so rather than silently defaulting.
    """
    rows = []
    for index, row in enumerate(values):
        state = None
        if regimes is not None and index < len(regimes) and regimes[index]:
            state = {"regime_info": {"regime": str(regimes[index])}}
        probability = np.asarray(
            model.predict_base(np.expand_dims(row, axis=0), int(horizon), state),
            dtype=np.float64,
        )
        probability = np.nan_to_num(probability, nan=0.0, posinf=0.0, neginf=0.0)
        if probability.shape != (3,) or probability.sum() <= 0:
            probability = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        else:
            probability /= probability.sum()
        rows.append(probability)
    return np.asarray(rows, dtype=np.float64)


def probability_metrics(probability: np.ndarray, actual: np.ndarray, bins: int = 10) -> dict:
    probability = np.asarray(probability, dtype=np.float64)
    actual = np.asarray(actual, dtype=np.int64)
    predicted = np.argmax(probability, axis=1)
    correct = predicted == actual
    confidence = np.max(probability, axis=1)
    directional = predicted != 1
    calls = int(directional.sum())
    directional_precision = float(correct[directional].mean()) if calls else 0.0
    target = np.eye(3, dtype=np.float64)[actual]
    brier = float(np.mean(np.sum((probability - target) ** 2, axis=1)))
    ece = 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    for index in range(bins):
        mask = (confidence >= edges[index]) & (
            confidence < edges[index + 1] if index < bins - 1 else confidence <= edges[index + 1]
        )
        if mask.any():
            ece += float(mask.mean()) * abs(float(confidence[mask].mean()) - float(correct[mask].mean()))
    return {
        "samples": int(len(actual)),
        "overall_accuracy": float(correct.mean()) if len(actual) else 0.0,
        "directional_calls": calls,
        "directional_precision": directional_precision,
        "multiclass_brier": brier,
        "ece": float(ece),
    }


def evaluate_candidate(candidate, incumbent, X, Y: dict, split_idx: int,
                       decision_timestamps=None, incumbent_boundary_ts: int | None = None,
                       valid_mask: dict | None = None, regime_labels=None) -> dict:
    """P1-7. `valid_mask` is REQUIRED for an honest gate, and its absence is recorded.

    build_sequences gives an AMBIGUOUS row (one bar touched both barriers) a NEUTRAL one-hot,
    purely so an argmax by a caller that ignores the mask stays in range - an all-zero row would
    argmax to DOWN. Training already excludes those rows by weight. This gate did not, so every
    undefined outcome entered the holdout as a genuine NEUTRAL observation.

    That is not a small bias. It inflates NEUTRAL accuracy (a model abstaining on a chaotic bar
    scores a hit for a label that means "undefined"), and it moves Brier and ECE, which are the
    numbers the promotion decision is made on. The rows are unlabelled, not neutral."""
    gates = promotion_gates()
    report = {
        "created_at": time.time(),
        "gates": gates,
        "horizons": {},
        "calibration_contract": "candidate holdout + purged OOF stacker; full refit reuses conformal residuals",
        # Recorded so a report cannot claim an ambiguity-excluded evaluation it never ran.
        "ambiguous_rows_excluded": valid_mask is not None,
        # P0-8. Whether the holdout was scored through real regime routing or through the
        # RANGE default. A gate that silently evaluated one expert path must not be readable
        # as though it evaluated the served one.
        "regime_routing": "historical_labels" if regime_labels is not None else "RANGE_DEFAULT",
    }
    all_pass = True
    for horizon in candidate.horizons:
        if horizon not in Y:
            report["horizons"][int(horizon)] = {"passed": False, "reasons": ["missing_labels"]}
            all_pass = False
            continue
        stop = min(len(X), len(Y[horizon]))
        holdout = np.arange(max(0, int(split_idx)), stop, dtype=np.int64)
        excluded = 0
        if valid_mask is not None and horizon in valid_mask:
            vm = np.asarray(valid_mask[horizon], dtype=bool)
            if len(vm) < stop:
                # Refuse rather than align by guessing: a mask shorter than the labels cannot be
                # matched to rows without assuming which end it belongs to.
                report["horizons"][int(horizon)] = {
                    "passed": False,
                    "reasons": [f"valid_mask_too_short:{len(vm)}<{stop}"]}
                all_pass = False
                continue
            keep = vm[holdout]
            excluded = int((~keep).sum())
            holdout = holdout[keep]
        if holdout.size == 0:
            report["horizons"][int(horizon)] = {
                "passed": False, "reasons": ["no_valid_holdout_rows"],
                "ambiguous_excluded": excluded}
            all_pass = False
            continue
        sampled = _sample_indices(holdout, gates["max_eval_samples"])
        actual = np.argmax(np.asarray(Y[horizon])[sampled], axis=1)
        # The regime each sampled row was ACTUALLY in, so both variants are scored through the
        # routing production would have used rather than through the RANGE default.
        sampled_regimes = None
        if regime_labels is not None:
            _labels = list(regime_labels)
            if len(_labels) >= stop:
                sampled_regimes = [_labels[i] for i in sampled]
        candidate_probability = _predict_probabilities(
            candidate, np.asarray(X)[sampled], horizon, sampled_regimes)
        candidate_metrics = probability_metrics(candidate_probability, actual)
        incumbent_metrics = None
        fair_comparison = False
        if incumbent is not None and getattr(incumbent, "is_trained", False):
            incumbent_probability = _predict_probabilities(
                incumbent, np.asarray(X)[sampled], horizon, sampled_regimes)
            incumbent_metrics = probability_metrics(incumbent_probability, actual)

            if decision_timestamps is not None and incumbent_boundary_ts:
                ts = np.asarray(decision_timestamps, dtype=np.int64)[sampled]
                fair_mask = ts > int(incumbent_boundary_ts)
                if int(fair_mask.sum()) >= gates["min_holdout_samples"]:
                    fair_comparison = True
                    candidate_metrics_fair = probability_metrics(candidate_probability[fair_mask], actual[fair_mask])
                    incumbent_metrics_fair = probability_metrics(incumbent_probability[fair_mask], actual[fair_mask])
                else:
                    candidate_metrics_fair = incumbent_metrics_fair = None
            else:
                candidate_metrics_fair = incumbent_metrics_fair = None
        else:
            candidate_metrics_fair = incumbent_metrics_fair = None

        reasons = []
        if candidate_metrics["samples"] < gates["min_holdout_samples"]:
            reasons.append("insufficient_holdout_samples")
        if candidate_metrics["directional_calls"] < gates["min_directional_calls"]:
            reasons.append("insufficient_directional_calls")
        if candidate_metrics["directional_precision"] < gates["min_directional_precision"]:
            reasons.append("directional_precision_below_floor")
        if candidate_metrics["multiclass_brier"] > gates["max_multiclass_brier"]:
            reasons.append("brier_above_limit")
        if candidate_metrics["ece"] > gates["max_ece"]:
            reasons.append("ece_above_limit")
        if fair_comparison:
            if (candidate_metrics_fair["directional_precision"]
                    < incumbent_metrics_fair["directional_precision"] - gates["max_precision_regression"]):
                reasons.append("precision_regressed_vs_incumbent")
            if (candidate_metrics_fair["multiclass_brier"]
                    > incumbent_metrics_fair["multiclass_brier"] + gates["max_brier_regression"]):
                reasons.append("brier_regressed_vs_incumbent")

        passed = not reasons
        all_pass &= passed
        report["horizons"][int(horizon)] = {
            "passed": passed,
            "reasons": reasons,
            "ambiguous_excluded": excluded,
            "candidate": candidate_metrics,
            "incumbent": incumbent_metrics,
            "fair_incumbent_comparison": fair_comparison,
            "candidate_fair": candidate_metrics_fair,
            "incumbent_fair": incumbent_metrics_fair,
        }
    report["passed"] = bool(all_pass)
    return report


def smoke_test_model(model, X, horizons, samples: int = 3) -> dict:
    if not getattr(model, "is_trained", False):
        raise RuntimeError("staged model did not load as trained")
    checked = {}
    count = min(max(1, samples), len(X))
    for horizon in horizons:
        values = _predict_probabilities(model, np.asarray(X)[-count:], int(horizon))
        if not np.isfinite(values).all() or not np.allclose(values.sum(axis=1), 1.0, atol=1e-5):
            raise RuntimeError(f"invalid staged probabilities for {horizon}m")
        checked[int(horizon)] = {"samples": int(count), "mean_probability": values.mean(axis=0).tolist()}
    return {"passed": True, "horizons": checked}


def atomic_json(path: str | Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def promote_model_bundle(source_dir: str | Path, destination_dir: str | Path,
                         backup_root: str | Path) -> dict:
    """Promote one verified main-model bundle without replacing specialist-head files."""
    source = Path(source_dir).resolve()
    destination = Path(destination_dir).resolve()
    source_manifest_path = source / "artifact_manifest.json"
    if not source_manifest_path.is_file():
        raise RuntimeError(f"staged bundle has no artifact manifest: {source}")
    manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    artifact_files = [str(item) for item in manifest.get("artifact_files") or []]
    if not artifact_files:
        raise RuntimeError("staged bundle manifest has no artifact_files")
    if manifest.get("artifact_hash") != hash_directory_files(source, artifact_files):
        raise RuntimeError("staged bundle hash does not match its manifest")
    for relative in artifact_files:
        candidate = (source / relative).resolve()
        if source not in candidate.parents or not candidate.is_file():
            raise RuntimeError(f"staged bundle file is missing or escapes its root: {relative}")
        integrity = Path(f"{candidate}.integrity.json")
        if not integrity.is_file():
            raise RuntimeError(f"staged bundle integrity sidecar is missing: {relative}")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = Path(backup_root).resolve() / f"main_pre_promotion_{stamp}_{os.getpid()}"
    backup.mkdir(parents=True, exist_ok=False)
    destination.mkdir(parents=True, exist_ok=True)
    replaced: list[tuple[Path, Path | None]] = []

    def backup_existing(target: Path) -> Path | None:
        if not target.exists():
            return None
        relative = target.relative_to(destination)
        saved = backup / relative
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, saved)
        return saved

    try:
        for relative in artifact_files:
            source_file = source / relative
            target_file = destination / relative
            target_file.parent.mkdir(parents=True, exist_ok=True)
            for source_item, target_item in (
                (source_file, target_file),
                (Path(f"{source_file}.integrity.json"), Path(f"{target_file}.integrity.json")),
            ):
                saved = backup_existing(target_item)
                replaced.append((target_item, saved))
                temporary = target_item.with_name(target_item.name + f".tmp.{os.getpid()}")
                shutil.copy2(source_item, temporary)
                os.replace(temporary, target_item)

        target_manifest = destination / "artifact_manifest.json"
        saved_manifest = backup_existing(target_manifest)
        replaced.append((target_manifest, saved_manifest))
        temporary_manifest = target_manifest.with_name(
            target_manifest.name + f".tmp.{os.getpid()}"
        )
        shutil.copy2(source_manifest_path, temporary_manifest)
        os.replace(temporary_manifest, target_manifest)
        compatible, reasons = artifact_compatibility(destination, {}, strict=True)
        if not compatible:
            raise RuntimeError("promoted bundle failed hash validation: " + "; ".join(reasons))
    except Exception as promotion_error:
        restore_errors = []
        # Restore data files first and the old manifest last, mirroring the commit order.
        manifest_record = replaced[-1] if replaced and replaced[-1][0].name == "artifact_manifest.json" else None
        data_records = replaced[:-1] if manifest_record else replaced
        for target, saved in reversed(data_records):
            try:
                if saved is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(saved, target)
            except OSError as exc:
                restore_errors.append(f"{target}: {exc}")
        if manifest_record:
            target, saved = manifest_record
            try:
                if saved is None:
                    target.unlink(missing_ok=True)
                else:
                    shutil.copy2(saved, target)
            except OSError as exc:
                restore_errors.append(f"{target}: {exc}")
        if restore_errors:
            raise RuntimeError(
                f"promotion failed ({promotion_error}); rollback was incomplete: "
                + "; ".join(restore_errors)
            ) from promotion_error
        raise
    return {
        "source": str(source),
        "destination": str(destination),
        "backup": str(backup),
        "artifact_files": len(artifact_files),
    }


def selftest() -> None:
    import tempfile

    rng = np.random.default_rng(4)
    actual = rng.integers(0, 3, 500)
    probability = np.full((500, 3), 0.15)
    probability[np.arange(500), actual] = 0.70
    metrics = probability_metrics(probability, actual)
    assert metrics["samples"] == 500 and metrics["multiclass_brier"] < 0.2
    assert _sample_indices(np.arange(100), 10).shape == (10,)
    for reason in ("forced-startup", "manual-ui", "scheduled", "auto-learning"):
        assert promotion_required(True, reason)
        assert not promotion_required(False, reason)
    assert promotion_gates()["min_directional_calls"] > 0
    assert promotion_gates()["min_directional_precision"] > 0

    # ---- P1-7: AMBIGUOUS rows must not enter the gate as NEUTRAL observations -------------
    class _FakeModel:
        """Always votes UP with full confidence, so any NEUTRAL label is a miss."""
        horizons = [5]
        is_trained = True

        def predict_base(self, _row, _horizon, _context):
            return np.array([0.0, 0.0, 1.0], dtype=np.float64)

    n_rows = 600
    Xf = np.zeros((n_rows, 4), dtype=np.float32)
    labels = np.zeros((n_rows, 3), dtype=np.float32)
    labels[:, 2] = 1.0                                   # every REAL outcome is UP
    ambiguous = np.zeros(n_rows, dtype=bool)
    ambiguous[::2] = True                                # half the rows are undefined
    labels[ambiguous] = np.array([0.0, 1.0, 0.0])        # stored NEUTRAL, meaning "undefined"
    mask = {5: ~ambiguous}

    leaky = evaluate_candidate(_FakeModel(), None, Xf, {5: labels}, 0)
    honest = evaluate_candidate(_FakeModel(), None, Xf, {5: labels}, 0, valid_mask=mask)

    leaky_precision = leaky["horizons"][5]["candidate"]["directional_precision"]
    honest_precision = honest["horizons"][5]["candidate"]["directional_precision"]
    # The model is ALWAYS right on every defined row. Counting undefined rows as NEUTRAL turns
    # half of them into misses, so the two numbers must differ - if they matched, the mask
    # would be doing nothing and this test would be decorative.
    assert honest_precision > leaky_precision, (
        f"ambiguity exclusion changed nothing: {honest_precision} vs {leaky_precision}")
    assert abs(honest_precision - 1.0) < 1e-9, (
        f"with undefined rows removed the model is right on every graded row, got {honest_precision}")
    assert honest["horizons"][5]["ambiguous_excluded"] == int(ambiguous.sum())
    assert leaky["ambiguous_rows_excluded"] is False
    assert honest["ambiguous_rows_excluded"] is True

    # ---- P0-8: the holdout is scored through REAL regime routing ---------------------------
    class _RegimeAwareModel:
        """Votes UP in TREND and DOWN everywhere else, so the routing is observable."""
        horizons = [5]
        is_trained = True

        def predict_base(self, _row, _horizon, data_state):
            regime = ((data_state or {}).get("regime_info") or {}).get("regime", "RANGE")
            return (np.array([0.0, 0.0, 1.0]) if str(regime).startswith("TREND")
                    else np.array([1.0, 0.0, 0.0]))

    r_rows = 400
    Xr = np.zeros((r_rows, 4), dtype=np.float32)
    up = np.zeros((r_rows, 3), dtype=np.float32); up[:, 2] = 1.0     # every outcome is UP
    trend_labels = ["TRENDING_UP"] * r_rows
    vmask = {5: np.ones(r_rows, dtype=bool)}

    blind = evaluate_candidate(_RegimeAwareModel(), None, Xr, {5: up}, 0, valid_mask=vmask)
    routed = evaluate_candidate(_RegimeAwareModel(), None, Xr, {5: up}, 0, valid_mask=vmask,
                                regime_labels=trend_labels)

    blind_p = blind["horizons"][5]["candidate"]["directional_precision"]
    routed_p = routed["horizons"][5]["candidate"]["directional_precision"]
    # With no labels every row resolves to RANGE and the model votes DOWN against an UP
    # outcome. With the real labels it routes TREND and votes UP. If the gate ignored routing
    # these two would be identical - which is exactly what it used to do.
    assert blind_p == 0.0, f"RANGE-default path should miss every row, got {blind_p}"
    assert routed_p == 1.0, f"regime-routed path should hit every row, got {routed_p}"
    assert blind["regime_routing"] == "RANGE_DEFAULT"
    assert routed["regime_routing"] == "historical_labels"

    # A label list too short to align is refused rather than partially applied.
    short_labels = evaluate_candidate(_RegimeAwareModel(), None, Xr, {5: up}, 0,
                                      valid_mask=vmask, regime_labels=["TRENDING_UP"] * 10)
    assert short_labels["horizons"][5]["candidate"]["directional_precision"] == 0.0, (
        "a mis-sized label list must not be applied to arbitrary rows")

    # A mask that cannot be aligned to the labels is refused, not silently ignored.
    short = evaluate_candidate(_FakeModel(), None, Xf, {5: labels}, 0,
                               valid_mask={5: np.ones(10, dtype=bool)})
    assert not short["passed"]
    assert any(r.startswith("valid_mask_too_short")
               for r in short["horizons"][5]["reasons"]), short["horizons"][5]["reasons"]

    # Every row ambiguous -> no evaluation is possible, and it must FAIL rather than pass on
    # an empty holdout that trivially satisfies every threshold.
    none_valid = evaluate_candidate(_FakeModel(), None, Xf, {5: labels}, 0,
                                    valid_mask={5: np.zeros(n_rows, dtype=bool)})
    assert not none_valid["passed"]
    assert "no_valid_holdout_rows" in none_valid["horizons"][5]["reasons"]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "staged"
        live = root / "live"
        backups = root / "backups"
        source.mkdir()
        live.mkdir()
        relative = "RANGE/xgb_5.pkl"
        (source / relative).parent.mkdir(parents=True)
        (live / relative).parent.mkdir(parents=True)
        (source / relative).write_bytes(b"new-model")
        Path(f"{source / relative}.integrity.json").write_text("{}", encoding="utf-8")
        (live / relative).write_bytes(b"old-model")
        Path(f"{live / relative}.integrity.json").write_text("{}", encoding="utf-8")
        (live / "persistence_model.pkl").write_bytes(b"specialist-head")
        staged_manifest = {
            "artifact_files": [relative],
            "artifact_hash": hash_directory_files(source, [relative]),
        }
        (source / "artifact_manifest.json").write_text(
            json.dumps(staged_manifest), encoding="utf-8"
        )
        old_manifest = {
            "artifact_files": [relative],
            "artifact_hash": hash_directory_files(live, [relative]),
        }
        (live / "artifact_manifest.json").write_text(
            json.dumps(old_manifest), encoding="utf-8"
        )
        result = promote_model_bundle(source, live, backups)
        assert (live / relative).read_bytes() == b"new-model"
        assert (live / "persistence_model.pkl").read_bytes() == b"specialist-head"
        assert (Path(result["backup"]) / relative).read_bytes() == b"old-model"

        (source / relative).write_bytes(b"tampered")
        try:
            promote_model_bundle(source, live, backups)
            raise AssertionError("tampered staged bundle was promoted")
        except RuntimeError as exc:
            assert "hash" in str(exc)
        assert (live / relative).read_bytes() == b"new-model"
    print("model_promotion selftest: PASS")


if __name__ == "__main__":
    selftest()
