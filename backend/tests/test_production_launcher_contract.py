"""Static contract for the fail-closed paper/shadow production launcher."""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    text = (root / "start_production.bat").read_text(encoding="utf-8").lower()
    training = (root / "start.bat").read_text(encoding="utf-8").lower()
    instant = (root / "start_instant.bat").read_text(encoding="utf-8").lower()
    production_env = (root / "deploy" / "production.env.example").read_text(
        encoding="utf-8"
    ).lower()

    canonical = r"%btc_data_dir%\btc_duckdbs\analytics.duckdb"
    assert canonical in text, "production launcher must name the committed canonical store"
    assert r"%btc_data_dir%\analytics.duckdb" not in text.replace(canonical, ""), (
        "production launcher names the divergent root analytics store"
    )
    assert 'set "btc_enable_real_orders=0"' in text
    assert 'set "btc_enable_live_trading=0"' in text
    assert 'set "btc_binance_live=0"' in text
    assert 'set "btc_polymarket_live=0"' in text
    assert 'if not defined btc_historical_days set "btc_historical_days=900"' in training
    assert 'set "btc_serving_warmup_days=%btc_historical_days%"' in training
    assert (
        'if not defined btc_model_training_days '
        'set "btc_model_training_days=%btc_historical_days%"'
    ) in training
    assert (
        'if not defined btc_backfill_days '
        'set "btc_backfill_days=%btc_historical_days%"'
    ) in training
    assert (
        r'set "btc_retrain_completion_marker=%btc_data_dir%\saved_models\full_retrain_'
        r'%btc_historical_days%d_complete.json"'
    ) in training
    assert 'set "btc_model_training_days=900"' in instant
    assert 'set "btc_model_training_days=900"' in text
    assert "btc_model_training_days=900" in production_env
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
