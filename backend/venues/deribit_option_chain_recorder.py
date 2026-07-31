#!/usr/bin/env python
"""Public, read-only Deribit BTC option-chain recorder.

The application currently stores only aggregate option statistics. This
recorder preserves the per-expiry, per-strike executable chain needed for
physical-versus-implied volatility research. It has no credentials and no
order-submission path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import requests

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "deribit_options.duckdb"
SUMMARY_URL = (
    "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
)
SCHEMA_VERSION = "deribit-option-chain-v1"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS deribit_chain_batches(
    batch_id VARCHAR PRIMARY KEY,
    request_start_ns BIGINT NOT NULL,
    response_receive_ns BIGINT NOT NULL,
    duration_ms DOUBLE NOT NULL,
    http_status INTEGER,
    rpc_error VARCHAR,
    response_sha256 VARCHAR,
    response_rpc_id VARCHAR,
    raw_rows INTEGER NOT NULL,
    stored_rows INTEGER NOT NULL,
    dropped_rows INTEGER NOT NULL,
    minimum_exchange_ts_ms BIGINT,
    maximum_exchange_ts_ms BIGINT,
    schema_version VARCHAR NOT NULL,
    code_commit VARCHAR NOT NULL,
    code_dirty BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS deribit_chain_snapshots(
    batch_id VARCHAR NOT NULL,
    receive_ts_ns BIGINT NOT NULL,
    exchange_ts_ms BIGINT,
    instrument_name VARCHAR NOT NULL,
    expiry_ts_ms BIGINT NOT NULL,
    strike DOUBLE NOT NULL,
    option_type VARCHAR NOT NULL,
    underlying_index VARCHAR,
    underlying_price DOUBLE,
    bid_price DOUBLE,
    ask_price DOUBLE,
    mid_price DOUBLE,
    mark_price DOUBLE,
    mark_iv_pct DOUBLE,
    bid_iv_pct DOUBLE,
    ask_iv_pct DOUBLE,
    open_interest DOUBLE,
    volume DOUBLE,
    interest_rate DOUBLE,
    estimated_delivery_price DOUBLE,
    base_currency VARCHAR,
    quote_currency VARCHAR,
    PRIMARY KEY(batch_id, instrument_name)
);

"""

SNAPSHOT_INSERT = """
INSERT OR IGNORE INTO deribit_chain_snapshots VALUES (
    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
)
"""


def _log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"{stamp} {message}", flush=True)


def _code_identity() -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        )
        return commit, dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", True


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _nonnegative(value: Any) -> float | None:
    parsed = _finite_float(value)
    if parsed is None:
        return None
    return parsed if parsed >= 0.0 else None


def parse_instrument_name(name: str) -> tuple[int, float, str] | None:
    parts = str(name).split("-")
    if len(parts) != 4 or parts[0].upper() != "BTC":
        return None
    option_type = parts[3].upper()
    if option_type not in {"C", "P"}:
        return None
    try:
        expiry = datetime.strptime(parts[1].title(), "%d%b%y").replace(
            tzinfo=timezone.utc, hour=8
        )
        strike = float(parts[2])
    except (TypeError, ValueError):
        return None
    if strike <= 0.0:
        return None
    return int(expiry.timestamp() * 1_000), strike, option_type


def normalize_summary(
    item: dict[str, Any], receive_ts_ns: int
) -> dict[str, Any] | None:
    name = str(item.get("instrument_name") or "")
    parsed = parse_instrument_name(name)
    if parsed is None:
        return None
    expiry_ts_ms, strike, option_type = parsed
    exchange_ts = item.get("creation_timestamp")
    try:
        exchange_ts_ms = int(exchange_ts) if exchange_ts is not None else None
    except (TypeError, ValueError):
        exchange_ts_ms = None
    if exchange_ts_ms is not None and exchange_ts_ms <= 0:
        exchange_ts_ms = None
    return {
        "receive_ts_ns": int(receive_ts_ns),
        "exchange_ts_ms": exchange_ts_ms,
        "instrument_name": name,
        "expiry_ts_ms": expiry_ts_ms,
        "strike": strike,
        "option_type": option_type,
        "underlying_index": str(item.get("underlying_index") or ""),
        "underlying_price": _nonnegative(item.get("underlying_price")),
        "bid_price": _nonnegative(item.get("bid_price")),
        "ask_price": _nonnegative(item.get("ask_price")),
        "mid_price": _nonnegative(item.get("mid_price")),
        "mark_price": _nonnegative(item.get("mark_price")),
        "mark_iv_pct": _nonnegative(item.get("mark_iv")),
        "bid_iv_pct": _nonnegative(item.get("bid_iv")),
        "ask_iv_pct": _nonnegative(item.get("ask_iv")),
        "open_interest": _nonnegative(item.get("open_interest")),
        "volume": _nonnegative(item.get("volume")),
        "interest_rate": _finite_float(item.get("interest_rate")),
        "estimated_delivery_price": _nonnegative(
            item.get("estimated_delivery_price")
        ),
        "base_currency": str(item.get("base_currency") or ""),
        "quote_currency": str(item.get("quote_currency") or ""),
    }


def _batch_id(request_start_ns: int, response_receive_ns: int) -> str:
    payload = f"{request_start_ns}:{response_receive_ns}:{SCHEMA_VERSION}"
    return hashlib.sha256(payload.encode("ascii")).hexdigest()[:24]


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(path))
    connection.execute(SCHEMA_SQL)
    connection.execute(
        "ALTER TABLE deribit_chain_batches "
        "ADD COLUMN IF NOT EXISTS response_sha256 VARCHAR"
    )
    connection.execute(
        "ALTER TABLE deribit_chain_batches "
        "ADD COLUMN IF NOT EXISTS response_rpc_id VARCHAR"
    )
    snapshot_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info('deribit_chain_snapshots')"
        ).fetchall()
    }
    for old, new in (
        ("mark_iv", "mark_iv_pct"),
        ("bid_iv", "bid_iv_pct"),
        ("ask_iv", "ask_iv_pct"),
    ):
        if old in snapshot_columns and new not in snapshot_columns:
            connection.execute("DROP INDEX IF EXISTS deribit_chain_time_idx")
            connection.execute(
                f"ALTER TABLE deribit_chain_snapshots "
                f"RENAME COLUMN {old} TO {new}"
            )
            snapshot_columns.remove(old)
            snapshot_columns.add(new)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS deribit_chain_time_idx "
        "ON deribit_chain_snapshots("
        "receive_ts_ns, expiry_ts_ms, strike, option_type)"
    )
    connection.close()


def persist_payload(
    path: Path,
    payload: dict[str, Any],
    *,
    request_start_ns: int,
    response_receive_ns: int,
    duration_ms: float,
    http_status: int | None,
) -> dict[str, Any]:
    initialize_database(path)
    raw_result = payload.get("result")
    raw_rows = raw_result if isinstance(raw_result, list) else []
    normalized = [
        row
        for item in raw_rows
        if isinstance(item, dict)
        for row in [normalize_summary(item, response_receive_ns)]
        if row is not None
    ]
    exchange_times = [
        int(row["exchange_ts_ms"])
        for row in normalized
        if row["exchange_ts_ms"] is not None
    ]
    rpc_error = payload.get("error")
    rpc_error_text = (
        json.dumps(rpc_error, sort_keys=True, default=str)[:2_000]
        if rpc_error
        else ""
    )
    canonical_response = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    response_sha256 = hashlib.sha256(canonical_response).hexdigest()
    response_rpc_id = str(payload.get("id") or "")
    batch_id = _batch_id(request_start_ns, response_receive_ns)
    code_commit, code_dirty = _code_identity()
    connection = duckdb.connect(str(path))
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(
            """
            INSERT OR IGNORE INTO deribit_chain_batches (
                batch_id, request_start_ns, response_receive_ns, duration_ms,
                http_status, rpc_error, response_sha256, response_rpc_id,
                raw_rows, stored_rows, dropped_rows, minimum_exchange_ts_ms,
                maximum_exchange_ts_ms, schema_version, code_commit, code_dirty
            ) VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                batch_id,
                int(request_start_ns),
                int(response_receive_ns),
                float(duration_ms),
                int(http_status) if http_status is not None else None,
                rpc_error_text,
                response_sha256,
                response_rpc_id,
                len(raw_rows),
                len(normalized),
                len(raw_rows) - len(normalized),
                min(exchange_times) if exchange_times else None,
                max(exchange_times) if exchange_times else None,
                SCHEMA_VERSION,
                code_commit,
                code_dirty,
            ),
        )
        for row in normalized:
            connection.execute(
                SNAPSHOT_INSERT,
                (
                    batch_id,
                    row["receive_ts_ns"],
                    row["exchange_ts_ms"],
                    row["instrument_name"],
                    row["expiry_ts_ms"],
                    row["strike"],
                    row["option_type"],
                    row["underlying_index"],
                    row["underlying_price"],
                    row["bid_price"],
                    row["ask_price"],
                    row["mid_price"],
                    row["mark_price"],
                    row["mark_iv_pct"],
                    row["bid_iv_pct"],
                    row["ask_iv_pct"],
                    row["open_interest"],
                    row["volume"],
                    row["interest_rate"],
                    row["estimated_delivery_price"],
                    row["base_currency"],
                    row["quote_currency"],
                ),
            )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return {
        "batch_id": batch_id,
        "raw_rows": len(raw_rows),
        "stored_rows": len(normalized),
        "dropped_rows": len(raw_rows) - len(normalized),
        "rpc_error": rpc_error_text,
        "response_sha256": response_sha256,
    }


def fetch_once(path: Path, timeout_seconds: float = 15.0) -> dict[str, Any]:
    request_start_ns = time.time_ns()
    status: int | None = None
    try:
        response = requests.get(
            SUMMARY_URL,
            params={"currency": "BTC", "kind": "option"},
            timeout=timeout_seconds,
            headers={"User-Agent": "BTC-Prediction-Tool/DeribitRecorder-v1"},
        )
        status = int(response.status_code)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Deribit response is not a JSON object")
    except Exception as exc:
        payload = {"result": [], "error": {"message": str(exc)}}
    response_receive_ns = time.time_ns()
    duration_ms = (response_receive_ns - request_start_ns) / 1_000_000.0
    result = persist_payload(
        path,
        payload,
        request_start_ns=request_start_ns,
        response_receive_ns=response_receive_ns,
        duration_ms=duration_ms,
        http_status=status,
    )
    result["http_status"] = status
    result["duration_ms"] = duration_ms
    return result


def _latest_chain(connection: duckdb.DuckDBPyConnection):
    batch = connection.execute(
        """
        SELECT batch_id, response_receive_ns
        FROM deribit_chain_batches
        WHERE stored_rows > 0
        ORDER BY response_receive_ns DESC
        LIMIT 1
        """
    ).fetchone()
    if batch is None:
        return None, None
    frame = connection.execute(
        """
        SELECT *
        FROM deribit_chain_snapshots
        WHERE batch_id = ?
        ORDER BY expiry_ts_ms, strike, option_type
        """,
        [batch[0]],
    ).fetchdf()
    frame = frame.rename(
        columns={
            "mark_iv": "mark_iv_pct",
            "bid_iv": "bid_iv_pct",
            "ask_iv": "ask_iv_pct",
        }
    )
    return batch, frame


def report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "status": "NO_DATA",
            "database": str(path),
            "instruction": "run the recorder before requesting a report",
        }
    connection = duckdb.connect(str(path), read_only=True)
    try:
        batches = connection.execute(
            """
            SELECT count(*), count(*) FILTER (WHERE stored_rows > 0),
                   min(response_receive_ns), max(response_receive_ns),
                   sum(stored_rows), sum(dropped_rows)
            FROM deribit_chain_batches
            """
        ).fetchone()
        batch_gaps = connection.execute(
            """
            WITH ordered AS (
                SELECT request_start_ns,
                       lag(request_start_ns) OVER (
                           ORDER BY request_start_ns
                       ) AS previous_start_ns
                FROM deribit_chain_batches
                WHERE stored_rows > 0
            )
            SELECT max(
                (request_start_ns - previous_start_ns) / 1000000000.0
            )
            FROM ordered
            WHERE previous_start_ns IS NOT NULL
            """
        ).fetchone()[0]
        hashed_batches = connection.execute(
            """
            SELECT count(*)
            FROM deribit_chain_batches
            WHERE response_sha256 IS NOT NULL
              AND length(response_sha256) = 64
            """
        ).fetchone()[0]
        latest_batch, chain = _latest_chain(connection)
    finally:
        connection.close()
    if latest_batch is None or chain is None or chain.empty:
        return {
            "status": "NO_VALID_CHAIN",
            "database": str(path),
            "batches": int(batches[0] or 0),
        }
    receive_ns = int(latest_batch[1])
    now_ns = time.time_ns()
    quoted = chain[
        chain["bid_price"].notna()
        & chain["ask_price"].notna()
        & (chain["ask_price"] >= chain["bid_price"])
    ].copy()
    spot_values = chain.loc[
        chain["underlying_price"].notna() & (chain["underlying_price"] > 0),
        "underlying_price",
    ]
    spot = float(spot_values.median()) if len(spot_values) else math.nan
    straddles: list[dict[str, Any]] = []
    if math.isfinite(spot):
        for expiry, expiry_chain in quoted.groupby("expiry_ts_ms"):
            calls = expiry_chain[expiry_chain["option_type"] == "C"].set_index(
                "strike"
            )
            puts = expiry_chain[expiry_chain["option_type"] == "P"].set_index(
                "strike"
            )
            strikes = sorted(set(calls.index) & set(puts.index))
            if not strikes:
                continue
            strike = min(strikes, key=lambda value: abs(float(value) - spot))
            call = calls.loc[strike]
            put = puts.loc[strike]
            straddles.append(
                {
                    "expiry_ts_ms": int(expiry),
                    "hours_to_expiry": (
                        int(expiry) / 1_000.0 - receive_ns / 1_000_000_000.0
                    )
                    / 3_600.0,
                    "strike": float(strike),
                    "call_ask_btc": float(call["ask_price"]),
                    "put_ask_btc": float(put["ask_price"]),
                    "straddle_ask_btc": float(
                        call["ask_price"] + put["ask_price"]
                    ),
                    "call_mark_iv_pct": (
                        float(call["mark_iv_pct"])
                        if math.isfinite(float(call["mark_iv_pct"]))
                        else None
                    ),
                    "put_mark_iv_pct": (
                        float(put["mark_iv_pct"])
                        if math.isfinite(float(put["mark_iv_pct"]))
                        else None
                    ),
                }
            )
    return {
        "status": (
            "FRESH"
            if (now_ns - receive_ns) / 1_000_000_000.0 <= 90.0
            else "STALE"
        ),
        "database": str(path),
        "schema_version": SCHEMA_VERSION,
        "batches": int(batches[0] or 0),
        "successful_batches": int(batches[1] or 0),
        "source_hashed_batches": int(hashed_batches or 0),
        "maximum_batch_gap_seconds": (
            float(batch_gaps) if batch_gaps is not None else None
        ),
        "first_receive_ts": (
            datetime.fromtimestamp(
                int(batches[2]) / 1_000_000_000.0, tz=timezone.utc
            ).isoformat()
            if batches[2]
            else None
        ),
        "last_receive_ts": datetime.fromtimestamp(
            receive_ns / 1_000_000_000.0, tz=timezone.utc
        ).isoformat(),
        "latest_age_seconds": (now_ns - receive_ns) / 1_000_000_000.0,
        "stored_snapshot_rows": int(batches[4] or 0),
        "dropped_rows": int(batches[5] or 0),
        "latest_chain_rows": int(len(chain)),
        "latest_quoted_rows": int(len(quoted)),
        "latest_expiries": int(chain["expiry_ts_ms"].nunique()),
        "latest_call_rows": int((chain["option_type"] == "C").sum()),
        "latest_put_rows": int((chain["option_type"] == "P").sum()),
        "latest_underlying_price": spot if math.isfinite(spot) else None,
        "latest_atm_straddles": sorted(
            straddles, key=lambda item: item["expiry_ts_ms"]
        )[:8],
        "research_ready": False,
        "research_blocker": (
            "forward history is required before physical-versus-implied "
            "volatility can be evaluated"
        ),
    }


def selftest() -> None:
    parsed = parse_instrument_name("BTC-31JUL26-70000-C")
    assert parsed is not None
    assert parsed[1:] == (70_000.0, "C")
    assert parse_instrument_name("ETH-31JUL26-70000-C") is None
    assert parse_instrument_name("BTC-BAD-70000-C") is None
    receive_ns = 2_000_000_000
    sample = {
        "result": [
            {
                "instrument_name": "BTC-31JUL26-70000-C",
                "creation_timestamp": 1_900,
                "underlying_index": "SYN.BTC-31JUL26",
                "underlying_price": 69_900,
                "bid_price": 0.01,
                "ask_price": 0.02,
                "mid_price": 0.015,
                "mark_price": 0.016,
                "mark_iv": 45.0,
                "open_interest": 12.0,
                "volume": 3.0,
                "base_currency": "BTC",
                "quote_currency": "BTC",
            },
            {
                "instrument_name": "BTC-31JUL26-70000-P",
                "creation_timestamp": 1_900,
                "underlying_index": "SYN.BTC-31JUL26",
                "underlying_price": 69_900,
                "bid_price": 0.012,
                "ask_price": 0.022,
                "mid_price": 0.017,
                "mark_price": 0.018,
                "mark_iv": 47.0,
                "open_interest": 10.0,
                "volume": 2.0,
                "base_currency": "BTC",
                "quote_currency": "BTC",
            },
            {"instrument_name": "MALFORMED"},
        ]
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "test.duckdb"
        result = persist_payload(
            path,
            sample,
            request_start_ns=1_000_000_000,
            response_receive_ns=receive_ns,
            duration_ms=1.0,
            http_status=200,
        )
        assert result["stored_rows"] == 2
        assert result["dropped_rows"] == 1
        assert len(result["response_sha256"]) == 64
        connection = duckdb.connect(str(path), read_only=True)
        assert connection.execute(
            "SELECT count(*) FROM deribit_chain_snapshots"
        ).fetchone()[0] == 2
        connection.close()
        summary = report(path)
        assert summary["latest_chain_rows"] == 2
        assert summary["latest_quoted_rows"] == 2
        assert len(summary["latest_atm_straddles"]) == 1
        assert math.isclose(
            summary["latest_atm_straddles"][0]["straddle_ask_btc"],
            0.042,
        )
    print("deribit_option_chain_recorder selftest: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    path = args.db.resolve()
    if args.selftest:
        selftest()
        return 0
    if args.report:
        print(json.dumps(report(path), indent=2, default=str))
        return 0
    interval = max(10.0, float(args.interval))
    _log(
        f"[DERIBIT] recorder start db={path} interval={interval:.0f}s "
        "mode=public-read-only"
    )
    while True:
        result = fetch_once(path, timeout_seconds=max(2.0, float(args.timeout)))
        status = "OK" if result["stored_rows"] > 0 else "ERROR"
        _log(
            f"[DERIBIT] {status} batch={result['batch_id']} "
            f"http={result['http_status']} rows={result['stored_rows']} "
            f"dropped={result['dropped_rows']} elapsed={result['duration_ms']:.0f}ms"
        )
        if result["rpc_error"]:
            _log(f"[DERIBIT] error={result['rpc_error'][:300]}")
        if args.once:
            return 0 if result["stored_rows"] > 0 else 1
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            _log("[DERIBIT] stopped")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
