"""Static contract for the fail-closed paper/shadow production launcher."""
from __future__ import annotations

from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    text = (root / "start_production.bat").read_text(encoding="utf-8").lower()

    canonical = r"%btc_data_dir%\btc_duckdbs\analytics.duckdb"
    assert canonical in text, "production launcher must name the committed canonical store"
    assert r"%btc_data_dir%\analytics.duckdb" not in text.replace(canonical, ""), (
        "production launcher names the divergent root analytics store"
    )
    assert 'set "btc_enable_real_orders=0"' in text
    assert 'set "btc_enable_live_trading=0"' in text
    assert 'set "btc_binance_live=0"' in text
    assert 'set "btc_polymarket_live=0"' in text
    assert 'set "btc_model_training_days=1000"' in text
    assert 'set "btc_python_exe=%python_exe%"' in text

    recorder = text.index("start_recorders_once.ps1")
    wait = text.index("wait_for_forward_evidence.py --timeout")
    readiness = text.index("production_readiness.py --mode paper")
    server = text.index("-m uvicorn")
    assert recorder < wait < readiness < server, "production startup order is unsafe"
    assert "--reload" not in text, "production must not auto-reload during evidence collection"

    print("production launcher contract: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
