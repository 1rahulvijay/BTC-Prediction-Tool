"""Executed-data provenance hashing must be byte-identical without whole-tensor copies."""
from __future__ import annotations

import hashlib
import runpy as _bootstrap_runpy
import tempfile
from pathlib import Path

import numpy as np

_bootstrap_runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from artifact_identity import _array_hash  # noqa: E402


def legacy_digest(array: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(np.asarray(array))
    digest = hashlib.sha256()
    digest.update(str(contiguous.dtype).encode())
    digest.update(str(contiguous.shape).encode())
    digest.update(contiguous.tobytes())
    return digest.hexdigest()


def main() -> int:
    contiguous = np.arange(120_000, dtype=np.float32).reshape(300, 400)
    noncontiguous = contiguous[:, ::3]
    assert _array_hash(contiguous) == legacy_digest(contiguous)
    assert _array_hash(noncontiguous) == legacy_digest(noncontiguous)

    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "sequence.mmap"
        mapped = np.memmap(path, dtype=np.float32, mode="w+", shape=(300, 400))
        mapped[:] = contiguous
        mapped.flush()
        assert _array_hash(mapped) == legacy_digest(contiguous)
        mapped._mmap.close()
        del mapped

    print("array-hash-streaming: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
