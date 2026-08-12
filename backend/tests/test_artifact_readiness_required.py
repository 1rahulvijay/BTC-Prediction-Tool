"""Required missing artifacts may never collapse out of the readiness denominator."""
from __future__ import annotations

import contextlib
import io
import runpy as _bootstrap_runpy
import tempfile
from pathlib import Path

_bootstrap_runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

import verify_artifact_identity as verify  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        original = (
            verify.MODELS,
            verify.ARTIFACTS,
            verify.REQUIRED_ARTIFACTS,
            verify.current_training_identity,
            verify.training_identity_issues,
        )
        try:
            verify.MODELS = raw
            verify.ARTIFACTS = ["required.pkl", "optional.pkl"]
            verify.REQUIRED_ARTIFACTS = {"required.pkl"}
            verify.current_training_identity = lambda **_kwargs: {}
            verify.training_identity_issues = lambda _identity: []
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                result = verify.main()
            assert result == 1, captured.getvalue()
            assert "1 required missing" in captured.getvalue()
        finally:
            (
                verify.MODELS,
                verify.ARTIFACTS,
                verify.REQUIRED_ARTIFACTS,
                verify.current_training_identity,
                verify.training_identity_issues,
            ) = original
    print("artifact-readiness-required: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
