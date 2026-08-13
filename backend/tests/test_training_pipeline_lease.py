"""Full retraining and nightly recalibration may not rewrite one matrix concurrently."""
from __future__ import annotations

import runpy as _bootstrap_runpy
import sys
import tempfile
from pathlib import Path

_bootstrap_runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

import training_pipeline_lease as lease


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "pipeline.json"
        first = lease.acquire("full_retrain", days=1000, path=path)
        assert first is not None
        assert lease.acquire("nightly_recalibration", days=360, path=path) is None
        state = lease.describe(path)
        assert state["role"] == "full_retrain" and state["days"] == 1000
        assert state["alive"] is True
        assert lease.release(first, path=path) is True

        # A just-created but not-yet-written lease is an in-flight owner, not stale data.
        path.touch()
        assert lease.acquire("racer", days=1000, path=path) is None
        path.unlink()

        path.write_text(
            '{"token":"dead","role":"crashed","days":90,"owner_pid":2147483647}',
            encoding="utf-8",
        )
        recovered = lease.acquire("nightly_recalibration", days=1000, path=path)
        assert recovered is not None, "a dead owner's stale lease must not wedge training"
        assert lease.release(recovered, path=path) is True

        # Exercise the actual nightly entry point: it must return without invoking a trainer
        # while the full retrain owns the canonical-data lease.
        import auto_finetune as af
        saved = (lease.LEASE_PATH, af.LOCK_PATH, af.CANDIDATE_ROOT, sys.argv, af._run)
        lease.LEASE_PATH = path
        af.LOCK_PATH = str(Path(tmp) / "nightly-local.json")
        af.CANDIDATE_ROOT = str(Path(tmp) / "candidates")
        owner = lease.acquire("full_retrain", days=1000, path=path)
        assert owner is not None
        af._run = lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("nightly trainer ran while full retrain owned the lease")
        )
        sys.argv = ["auto_finetune.py"]
        try:
            assert af.main() == 0
        finally:
            sys.argv = saved[3]
            af._run = saved[4]
            af.CANDIDATE_ROOT = saved[2]
            af.LOCK_PATH = saved[1]
            lease.LEASE_PATH = saved[0]
            assert lease.release(owner, path=path) is True

    root = Path(__file__).resolve().parents[2]
    auto = (root / "backend" / "auto_finetune.py").read_text(encoding="utf-8")
    launcher = (root / "start.bat").read_text(encoding="utf-8")
    assert "FULL_DAYS = resolve_history_days()" in auto
    assert 'or 360' not in auto.split("FULL_DAYS =", 1)[1].split("\n", 1)[0]
    begin = launcher.index("training_pipeline_lease.py begin")
    matrix = launcher.index("build_research_matrix.py --days")
    dispatch = launcher.index("call :run_head_training", matrix)
    heads = launcher.index("python backend\\train_heads.py --force")
    end = launcher.index("training_pipeline_lease.py end", dispatch)
    assert begin < matrix < dispatch < end < heads

    print("training-pipeline-lease: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
