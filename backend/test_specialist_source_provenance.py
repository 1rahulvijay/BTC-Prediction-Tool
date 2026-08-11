"""Specialist retrains must write manifests that strict serving can accept."""
from __future__ import annotations

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

    print("specialist source provenance: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
