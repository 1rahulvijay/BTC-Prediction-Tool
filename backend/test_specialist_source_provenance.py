"""Specialist retrains must write manifests that strict serving can accept."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_identity import (  # noqa: E402
    artifact_compatibility,
    current_training_identity,
    multihead_training_identity,
    source_file_training_identity,
    write_artifact_manifest,
)
import train_heads  # noqa: E402
import train_activity_keeper  # noqa: E402
import train_bigdrop_keeper  # noqa: E402
import train_bigmove_keeper  # noqa: E402
import train_directional_keeper  # noqa: E402
import train_path_forecaster  # noqa: E402
import train_persistence_model  # noqa: E402
import train_round_state_heads  # noqa: E402
import train_signed_quantiles  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        matrix = root / "matrix.parquet"
        specialist = root / "persistence.parquet"
        artifact = root / "head.pkl"
        matrix.write_bytes(b"matrix-v1")
        specialist.write_bytes(b"specialist-v1")
        artifact.write_bytes(b"fitted-head")

        receipt = source_file_training_identity({
            "research_matrix": matrix,
            "persistence_dataset": specialist,
        })
        assert receipt["executed_identity_recorded"] is True
        assert receipt["training_dataset_sha256"]
        assert set(receipt["executed_source_files"]) == {
            "research_matrix", "persistence_dataset"
        }

        identity = current_training_identity(
            requested_days=1,
            feature_names=["a"],
            code_paths=[__file__],
            executed=receipt,
        )
        identity["code_dirty"] = False
        write_artifact_manifest(artifact, identity, artifact_type="specialist_head:test")
        manifest = json.loads(Path(f"{artifact}.manifest.json").read_text(encoding="utf-8"))
        assert manifest["executed_identity_recorded"] is True
        assert manifest["training_dataset_sha256"] == receipt["training_dataset_sha256"]
        ok, reasons = artifact_compatibility(artifact, identity, strict=True)
        assert ok, reasons

        specialist.write_bytes(b"specialist-v2")
        changed = source_file_training_identity({
            "research_matrix": matrix,
            "persistence_dataset": specialist,
        })
        assert changed["training_dataset_sha256"] != receipt["training_dataset_sha256"]

        multi = multihead_training_identity({
            "5": ([[1.0], [2.0]], [0, 1], [1000, 2000]),
            "15": ([[3.0]], [1], [1000]),
        })
        assert multi["executed_identity_recorded"] is True
        assert multi["executed_rows"] == 3
        assert multi["executed_heads"]["5"]["rows"] == 2
        try:
            multihead_training_identity({"bad": ([[1.0]], [0, 1], None)})
            raise AssertionError("misaligned source rows were accepted")
        except ValueError:
            pass

        receipt_sidecar = Path(f"{artifact}.training_source_identity.json")
        multi["artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        receipt_sidecar.write_text(json.dumps(multi), encoding="utf-8")
        assert train_heads._artifact_training_receipt(str(artifact)) == multi
        artifact.write_bytes(b"changed-after-receipt")
        assert train_heads._artifact_training_receipt(str(artifact)) is None
        Path(f"{artifact}.integrity.json").write_text("{}", encoding="utf-8")
        Path(f"{artifact}.manifest.json").write_text("{}", encoding="utf-8")
        train_heads._remove_artifact_family(str(artifact))
        assert not artifact.exists()
        assert not receipt_sidecar.exists()
        assert not Path(f"{artifact}.integrity.json").exists()
        assert not Path(f"{artifact}.manifest.json").exists()

    declared = train_heads._source_paths({"name": "round_state"})
    actual_state_dir = train_round_state_heads.STATE_DIR.resolve()
    assert Path(declared["round_state_late_snapshots"]).resolve() == (
        actual_state_dir / "late_snapshots.parquet"
    )
    assert Path(declared["round_state_transition_drought"]).resolve() == (
        actual_state_dir / "transition_drought.parquet"
    )
    assert Path(declared["research_matrix"]).resolve() == train_round_state_heads.MATRIX.resolve()

    matrix_trainers = {
        "signed_quantile": train_signed_quantiles.MATRIX,
        "path_forecaster": train_path_forecaster.MATRIX,
        "bigmove": train_bigmove_keeper.MATRIX,
        "bigdrop": train_bigdrop_keeper.MATRIX,
        "directional": train_directional_keeper.MATRIX,
        "activity": train_activity_keeper.MATRIX,
    }
    for head_name, actual_matrix in matrix_trainers.items():
        source = train_heads._source_paths({"name": head_name})["research_matrix"]
        assert Path(source).resolve() == Path(actual_matrix).resolve(), head_name
    persistence_sources = train_heads._source_paths({"name": "persistence"})
    assert Path(persistence_sources["research_matrix"]).resolve() == Path(
        train_heads.MATRIX_PATH
    ).resolve()
    assert Path(persistence_sources["persistence_dataset"]).resolve() == Path(
        train_persistence_model.IN_PATH
    ).resolve()

    print("specialist source provenance: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
