from __future__ import annotations

import sys
from pathlib import Path

# `python backend/venues/test_deribit_option_chain_recorder.py` used to die on
# ModuleNotFoundError: No module named 'backend' - the absolute import below only resolved when
# the file was invoked as `python -m backend.venues....`. A test that fails depending on how it
# is launched will be recorded as a product failure by any runner that launches it the other
# way. Putting the repository root on sys.path makes both invocations equivalent.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.venues.deribit_option_chain_recorder import (  # noqa: E402
    normalize_summary,
    parse_instrument_name,
)


def test_parse_deribit_btc_option_name() -> None:
    parsed = parse_instrument_name("BTC-31JUL26-70000-P")
    assert parsed is not None
    expiry_ms, strike, option_type = parsed
    assert expiry_ms > 0
    assert strike == 70_000.0
    assert option_type == "P"


def test_normalizer_preserves_missing_exchange_time() -> None:
    row = normalize_summary(
        {
            "instrument_name": "BTC-31JUL26-70000-C",
            "underlying_price": 70_000,
            "bid_price": 0.01,
            "ask_price": 0.02,
        },
        123_000_000_000,
    )
    assert row is not None
    assert row["exchange_ts_ms"] is None
    assert row["receive_ts_ns"] == 123_000_000_000
    assert row["bid_price"] == 0.01
    assert row["ask_price"] == 0.02


def test_recorder_module_has_no_credential_or_order_surface() -> None:
    source = (
        Path(__file__)
        .with_name("deribit_option_chain_recorder.py")
        .read_text(encoding="utf-8")
        .lower()
    )
    assert "private/" not in source
    assert "client_secret" not in source
    assert "buy(" not in source
    assert "sell(" not in source
