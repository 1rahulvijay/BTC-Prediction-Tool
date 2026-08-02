from __future__ import annotations

import json
import math
from pathlib import Path

from research.phase5_standalone.common.engines import ENGINES
from research.phase5_standalone.common.protocol import load_protocol
from research.phase5_standalone.common.report_writer import source_tree_hash, write_report


ROOT = Path(__file__).resolve().parent


def test_all_42_frozen_protocols_are_unique_and_runnable() -> None:
    protocols = sorted(ROOT.glob("test_*/frozen_protocol.json"))
    assert len(protocols) == 42
    identities = set()
    hashes = set()
    for path in protocols:
        frozen = load_protocol(path)
        assert frozen.engine in ENGINES
        assert frozen.experiment_id not in identities
        assert frozen.sha256 not in hashes
        assert frozen.payload["capital_authority"] is False
        assert (path.parent / "run.py").is_file()
        assert (path.parent / "selftest.py").is_file()
        assert (path.parent / "README.md").is_file()
        identities.add(frozen.experiment_id)
        hashes.add(frozen.sha256)


def test_immutable_report_serializes_nonfinite_diagnostics_as_null(tmp_path: Path) -> None:
    report = write_report(tmp_path / "result", {
        "status": "FAIL_UNSTABLE",
        "experiment_id": "SELFTEST",
        "diagnostics": {"infinite_ceiling": math.inf},
    })
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["diagnostics"]["infinite_ceiling"] is None
    try:
        write_report(tmp_path / "result", {"status": "FAIL_NO_EDGE"})
    except FileExistsError:
        pass
    else:
        raise AssertionError("immutable report accepted an overwrite")


def test_every_protocol_declares_cost_stress_and_untouched_gate() -> None:
    for path in ROOT.glob("test_*/frozen_protocol.json"):
        payload = load_protocol(path).payload
        assert payload["cost_model"]["stress_multipliers"] == [1.0, 1.5, 2.0]
        gates = payload["promotion_gates"]
        assert gates["require_positive_day_lcb"] is True
        assert gates["require_positive_week_lcb"] is True
        assert gates["capital_authority"] is False


def test_suite_source_hash_is_stable_and_complete() -> None:
    first = source_tree_hash(ROOT)
    second = source_tree_hash(ROOT)
    assert first == second
    assert len(first) == 64
