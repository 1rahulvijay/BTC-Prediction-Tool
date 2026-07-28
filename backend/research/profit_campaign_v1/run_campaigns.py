#!/usr/bin/env python
"""Run both frozen PROFIT_CAMPAIGN_V1 campaigns without trading side effects."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

from . import cost_aware, dynamic_exit
from .book_replay import data_quality_summary, load_or_build_market_data
from .contracts import (
    DEFAULT_BINANCE_ARCHIVE,
    DEFAULT_INPUT_ROOT,
    DEFAULT_OUTPUT_ROOT,
    PROTOCOL_PATH,
    Protocol,
    protocol_manifest,
)
from .validation import finite_json


_LOG_PATH: Path | None = None


def log(message: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}"
    print(line, flush=True)
    if _LOG_PATH is not None:
        with _LOG_PATH.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _write_master_report(
    run_dir: Path,
    quality: dict[str, Any],
    summaries: list[dict[str, Any]],
    validation: dict[str, Any],
) -> None:
    campaign_rows = "\n".join(
        (
            f"| `{summary['campaign_id']}` | "
            f"`{summary.get('selected_policy', summary.get('model_incremental_ev', {})).get('total_net_pnl_usd')}` | "
            f"`{summary.get('selected_policy', summary.get('model_incremental_ev', {})).get('profit_factor')}` | "
            "RESEARCH ONLY |"
        )
        for summary in summaries
    )
    text = f"""# PROFIT_CAMPAIGN_V1 Run Report

Generated: {datetime.now(timezone.utc).isoformat()}

## Verdict

Both requested campaigns executed on the available exact Binance BTCUSDT L2
archive. Neither is eligible for promotion. This run has one 24-hour archive
window spanning two UTC dates, but the normalized receive stream
contains gaps and therefore is not one continuous session. Local receive batches
are approximately five seconds apart when present. The frozen promotion contract
requires at least 30 trading days, 8 weeks and forward paper evidence.

Artifact validation: **{validation["status"]}**. The validator reconciled
`{validation["cost_trade_rows_reconciled"]}` cost-aware trade rows and
`{validation["exit_trade_rows_reconciled"]}` exit-policy trade rows.

| Campaign | Untouched total net PnL | Profit factor | Status |
|---|---:|---:|---|
{campaign_rows}

## Data Quality

```json
{json.dumps(quality, indent=2, sort_keys=True)}
```

`subsecond_latency_resolvable=false` means the 100/250/500/1000 ms stress cells
often resolve to the same next received batch. They are recorded, but they are
not treated as independent latency evidence.

## Boundaries

- Exact bid/ask and visible L2 depth were used.
- Entry and exit use first eligible received books; midpoint fills are absent.
- Fees, depth slippage, an additional impact reserve and observed funding are
  included.
- No app, paper strategy, Polymarket campaign, production model or live-order
  permission changed.
"""
    (run_dir / "REPORT.md").write_text(text, encoding="utf-8")


def run(args: argparse.Namespace) -> Path:
    global _LOG_PATH
    protocol = Protocol.load()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (
        Path(args.run_dir).resolve()
        if args.run_dir
        else Path(args.output_root).resolve() / stamp
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    _LOG_PATH = run_dir / "campaign.log"
    shutil.copy2(PROTOCOL_PATH, run_dir / "frozen_protocol.json")
    log(
        "PROFIT_CAMPAIGN_V1 begins; research_only=true "
        f"protocol={protocol.sha256[:16]}"
    )
    start = time.perf_counter()
    books, trades, input_paths = load_or_build_market_data(
        archive_path=Path(args.archive).resolve(),
        input_root=Path(args.input_root).resolve(),
        maximum_capital_usd=max(protocol.capital_sizes),
        force=args.rebuild_books,
    )
    quality = data_quality_summary(
        books,
        trades,
        maximum_gap_ms=int(
            protocol.raw["execution"]["maximum_book_age_ms"]
        ),
    )
    log(
        f"market data books={len(books):,} trade_seconds={len(trades):,} "
        f"hours={quality['archive_span_hours']:.2f} "
        f"sessions={quality['fresh_book_sessions']} median_receive_ms="
        f"{quality['receive_interval_ms_q50']:.1f}"
    )
    manifest = protocol_manifest(protocol, list(input_paths.values()))
    manifest.update(
        {
            "git_commit": _git_commit(),
            "data_quality": quality,
            "run_started_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(finite_json(manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summaries = [
        cost_aware.run(
            books=books,
            trade_flow=trades,
            input_paths=input_paths,
            run_dir=run_dir,
            protocol=protocol,
            force=args.force,
            logger=log,
        ),
        dynamic_exit.run(
            books=books,
            trade_flow=trades,
            input_paths=input_paths,
            run_dir=run_dir,
            protocol=protocol,
            force=args.force,
            logger=log,
        ),
    ]
    completion = {
        "run_dir": str(run_dir),
        "elapsed_seconds": time.perf_counter() - start,
        "campaigns": summaries,
        "research_only": True,
        "paper_or_live_changed": False,
    }
    (run_dir / "completion.json").write_text(
        json.dumps(finite_json(completion), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    from .validate_result import validate_result

    validation = validate_result(run_dir)
    _write_master_report(run_dir, quality, summaries, validation)
    log(
        f"complete elapsed={completion['elapsed_seconds']:.1f}s "
        f"validation={validation['status']} "
        f"report={run_dir / 'REPORT.md'}"
    )
    return run_dir


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--archive", default=str(DEFAULT_BINANCE_ARCHIVE))
    value.add_argument("--input-root", default=str(DEFAULT_INPUT_ROOT))
    value.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    value.add_argument("--run-dir")
    value.add_argument("--force", action="store_true")
    value.add_argument("--rebuild-books", action="store_true")
    value.add_argument("--selftest", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    if args.selftest:
        from .selftest import main as selftest_main

        return selftest_main()
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
