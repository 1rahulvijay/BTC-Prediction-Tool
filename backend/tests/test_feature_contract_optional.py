"""Optional model heads may abstain, but a present unverifiable head must still be refused."""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import contextlib
import io
import tempfile
from pathlib import Path

import check_feature_contract as contract


def _run_gate(model_dir: Path, *, required: list[str], optional: list[str]) -> int:
    old = (
        contract.MODELS,
        contract.REQUIRED_SERVING_ARTIFACTS,
        contract.OPTIONAL_SERVING_ARTIFACTS,
    )
    try:
        contract.MODELS = str(model_dir)
        contract.REQUIRED_SERVING_ARTIFACTS = required
        contract.OPTIONAL_SERVING_ARTIFACTS = optional
        with contextlib.redirect_stdout(io.StringIO()):
            return contract.enforce_serving()
    finally:
        (
            contract.MODELS,
            contract.REQUIRED_SERVING_ARTIFACTS,
            contract.OPTIONAL_SERVING_ARTIFACTS,
        ) = old


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        model_dir = Path(raw)
        assert _run_gate(model_dir, required=["required.pkl"], optional=[]) == 1
        assert _run_gate(model_dir, required=[], optional=["optional.pkl"]) == 0

        # Optional means it may be absent. Once present, it participates in decisions and its
        # identity must be as strong as a required head's identity.
        (model_dir / "optional.pkl").write_bytes(b"unverifiable")
        assert _run_gate(model_dir, required=[], optional=["optional.pkl"]) == 1

    print("feature-contract optional-head policy: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
