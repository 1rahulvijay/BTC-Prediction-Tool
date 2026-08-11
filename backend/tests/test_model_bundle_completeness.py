"""A serving release is exactly the complete file set committed by its manifest."""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


from pathlib import Path
import tempfile

import numpy as np
from sklearn.dummy import DummyClassifier, DummyRegressor

import model as model_module
from artifact_identity import write_artifact_manifest


def _hmm_state() -> dict:
    return {
        "hmm_ready": True,
        "_means": [[0.0]],
        "_inv_covs": [[[1.0]]],
        "_logdets": [0.0],
        "_transmat": [[1.0]],
        "_k": 1,
        "_median_volume": 1.0,
        "state_labels": {0: "RANGE"},
    }


def _build(root: Path) -> list[str]:
    ensemble = model_module.MultiModelEnsemble(horizons=[5], model_dir=str(root))
    classifier = DummyClassifier(strategy="prior").fit(
        np.zeros((6, 2)), np.array([0, 1, 2, 0, 1, 2])
    )
    regressor = DummyRegressor(strategy="mean").fit(
        np.zeros((6, 2)), np.arange(6, dtype=float)
    )
    values = {
        "accuracies.pkl": {},
        "conformal_residuals.pkl": {reg: {} for reg in ensemble.regimes},
        "hmm_state.pkl": _hmm_state(),
        "feature_reference.pkl": {},
        "feature_reference_names.pkl": list(ensemble.model_feature_names),
        "model_feature_schema.pkl": {
            "mode": ensemble.model_feature_pruning,
            "raw_count": model_module.NUM_FEATURES,
            "model_count": ensemble.model_num_features,
            "hash": ensemble.model_feature_schema_hash,
            "names": list(ensemble.model_feature_names),
        },
        "move_size_stats.pkl": {reg: {} for reg in ensemble.regimes},
        "class_priors.pkl": {5: [1 / 3, 1 / 3, 1 / 3]},
        "stackers.pkl": {reg: {} for reg in ensemble.regimes},
        "calibration_provenance.pkl": {},
        "bundle_metadata.pkl": {
            "model_bundle_id": "bundle-completeness-test",
            "train_split_frac": 0.8,
            "train_split_idx": 100,
            "full_refit": False,
            "horizons": [5],
        },
        "architecture_version.pkl": model_module.MODEL_ARCH_VERSION,
    }
    for relative, value in values.items():
        model_module._atomic_joblib_dump(value, root / relative)
    (root / "GLOBAL").mkdir(parents=True, exist_ok=True)
    for name in ("xgb", "histgb", "lr", "rf"):
        relative = Path("GLOBAL") / f"{name}_5.pkl"
        model_module._atomic_joblib_dump(classifier, root / relative)
        values[str(relative)] = classifier
    relative = Path("GLOBAL") / "mag_5.pkl"
    model_module._atomic_joblib_dump(regressor, root / relative)
    values[str(relative)] = regressor
    files = list(values)
    write_artifact_manifest(
        root,
        {},
        artifact_type="multi_model_ensemble",
        extra={"artifact_files": files},
    )
    return files


def main() -> int:
    original_compatibility = model_module.artifact_compatibility
    model_module.artifact_compatibility = lambda *_args, **_kwargs: (True, [])
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = _build(root)

            # A stale, corrupt seat from an older release exists physically but is absent from
            # the manifest. It must be ignored rather than silently joining this release.
            stale = root / "RANGE" / "xgb_5.pkl"
            stale.parent.mkdir(parents=True, exist_ok=True)
            stale.write_bytes(b"not a model")
            loaded = model_module.MultiModelEnsemble(horizons=[5], model_dir=str(root))
            assert loaded.load_models(), loaded.load_refusal
            assert 5 not in loaded.models_by_regime["RANGE"]["xgb"]

            # Omitting one support artifact from the commit is a whole-release refusal even
            # though the file still exists on disk from the previous generation.
            reduced = [item for item in files if item != "stackers.pkl"]
            write_artifact_manifest(
                root,
                {},
                artifact_type="multi_model_ensemble",
                extra={"artifact_files": reduced},
            )
            incomplete = model_module.MultiModelEnsemble(horizons=[5], model_dir=str(root))
            assert not incomplete.load_models()
            assert "omits required support" in (incomplete.load_refusal or "")
            assert not incomplete.is_trained
            assert not incomplete.models_by_regime["GLOBAL"]["xgb"]

            # A failed reload on an ALREADY loaded object must clear the old generation too.
            # Returning False while leaving is_trained=True would let a caller report a refusal
            # yet continue serving stale in-memory models.
            assert loaded.is_trained
            assert not loaded.load_models()
            assert not loaded.is_trained
            assert not loaded.models_by_regime["GLOBAL"]["xgb"]

            # Recommit the complete list, then tamper one declared artifact. Per-file verified
            # loading must refuse it even when this test bypasses the separate identity gate.
            write_artifact_manifest(
                root,
                {},
                artifact_type="multi_model_ensemble",
                extra={"artifact_files": files},
            )
            (root / "class_priors.pkl").write_bytes(b"tampered")
            corrupt = model_module.MultiModelEnsemble(horizons=[5], model_dir=str(root))
            assert not corrupt.load_models()
            assert not corrupt.is_trained
    finally:
        model_module.artifact_compatibility = original_compatibility

    print("model bundle completeness: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
