from __future__ import annotations

import json
from pathlib import Path

from research.phase5_standalone.common.protocol import load_protocol
from research.phase5_standalone.common.report_writer import source_tree_hash
from research.phase5b_standalone.common.engines import ENGINES
from research.phase5b_standalone.common.engines_forecast import _clean_direction
from research.phase5b_standalone.common.engines_polymarket import _side


ROOT = Path(__file__).resolve().parent


def test_all_46_protocols_are_unique_and_runnable() -> None:
    protocols = sorted(ROOT.glob("test_*/frozen_protocol.json"))
    assert len(protocols) == 46
    ids, hashes = set(), set()
    numbers = []
    for path in protocols:
        frozen = load_protocol(path)
        number = int(frozen.experiment_id.split("_")[1])
        numbers.append(number)
        assert frozen.engine in ENGINES
        assert frozen.experiment_id not in ids
        assert frozen.sha256 not in hashes
        assert frozen.payload["capital_authority"] is False
        assert (path.parent / "run.py").is_file()
        assert (path.parent / "selftest.py").is_file()
        assert (path.parent / "README.md").is_file()
        ids.add(frozen.experiment_id)
        hashes.add(frozen.sha256)
    assert sorted(numbers) == list(range(43, 89))


def test_every_protocol_declares_cost_stress_and_no_capital_authority() -> None:
    for path in ROOT.glob("test_*/frozen_protocol.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["cost_model"]["stress_multipliers"] == [1.0, 1.5, 2.0]
        assert payload["promotion_gates"]["minimum_day_blocks"] >= 10
        assert payload["promotion_gates"]["minimum_week_blocks"] >= 4
        assert payload["promotion_gates"]["capital_authority"] is False


def test_suite_source_hash_is_stable() -> None:
    first = source_tree_hash(ROOT)
    second = source_tree_hash(ROOT)
    assert first == second and len(first) == 64


def test_numeric_zero_is_down_not_missing() -> None:
    assert _clean_direction(0) == "DOWN"
    assert _clean_direction(0.0) == "DOWN"
    assert _side(0) == "DOWN"
    assert _side(0.0) == "DOWN"
