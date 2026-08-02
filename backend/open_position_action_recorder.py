"""Same-time open-position action snapshots for Polymarket paper research.

The recorder does not choose or execute an action. It stores one causally ordered paired-book
snapshot, the exact paper inventory that existed then, and executable counterfactual mechanics for
HOLD, EXIT, REDUCE_50, SWITCH and LOCK. All fills walk the recorded ladders and charge the recorded
crypto fee rule. Incomplete depth remains a partial fill with residual inventory.

    python backend/open_position_action_recorder.py --selftest
    python backend/open_position_action_recorder.py --report
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from polymarket.l2_book import L2Book


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_DB = DATA_DIR / "open_position_actions.duckdb"
SCHEMA_VERSION = "open-position-action-snapshot-v1"
MAX_BOOK_AGE_MS = 5_000
MAX_PAIR_RECV_SKEW_MS = 1_000
ACTIONS = ("HOLD", "EXIT", "REDUCE_50", "SWITCH", "LOCK")


class SnapshotRefusal(ValueError):
    """A capture cannot be stored as action evidence without inventing state."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: Any, name: str, *, minimum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SnapshotRefusal(f"{name} is not numeric: {value!r}") from exc
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise SnapshotRefusal(f"{name} is invalid: {number!r}")
    return number


def _seconds_to_ms(value: Any) -> int:
    return int(round(_finite(value, "timestamp", minimum=0.0) * 1_000.0))


@dataclass(frozen=True)
class PaperInventory:
    position_id: str
    round_id: str
    strategy_id: str
    horizon_min: int
    opened_ts: int
    up_shares: float
    down_shares: float
    net_cost_basis: float
    entry_fees: float
    inventory_source: str
    state: dict[str, Any]


def _leg_inventory(state: dict[str, Any], leg: str) -> tuple[float, float, float, float]:
    data = state.get(leg) or {}
    if not isinstance(data, dict) or not data:
        return 0.0, 0.0, 0.0, 0.0
    requested = _finite(
        data.get("requested_size", data.get("quantity", 1.0)),
        f"{leg}.requested_size",
        minimum=0.0,
    )
    filled = _finite(data.get("filled_size", requested), f"{leg}.filled_size", minimum=0.0)
    if filled > requested + 1e-9:
        raise SnapshotRefusal(f"{leg}.filled_size exceeds requested_size")
    entry = _finite(data.get("entry"), f"{leg}.entry", minimum=0.0)
    fee_total = data.get("entry_fee_total")
    if fee_total is None:
        fee_total = _finite(data.get("fee_in", 0.0), f"{leg}.fee_in", minimum=0.0) * filled
    else:
        fee_total = _finite(fee_total, f"{leg}.entry_fee_total", minimum=0.0)
    entry_cost = entry * filled + fee_total
    prior_proceeds = 0.0
    remaining = filled
    if data.get("exit_bid") is not None:
        exit_bid = _finite(data.get("exit_bid"), f"{leg}.exit_bid", minimum=0.0)
        exit_fee = _finite(data.get("exit_fee", 0.0), f"{leg}.exit_fee", minimum=0.0)
        prior_proceeds = exit_bid * filled - exit_fee
        remaining = 0.0
    return remaining, entry_cost, fee_total, prior_proceeds


def normalize_paper_position(row: dict[str, Any]) -> PaperInventory:
    round_id = str(row.get("round_id") or "").strip()
    strategy = str(row.get("rule") or row.get("strategy_id") or "").strip()
    opened_ts = int(row.get("ts") or row.get("opened_ts") or 0)
    horizon = int(row.get("horizon") or row.get("horizon_min") or 0)
    if not round_id or not strategy or opened_ts <= 0 or horizon <= 0:
        raise SnapshotRefusal("round, strategy, opened timestamp and horizon are required")
    state = row.get("state") or {}
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except json.JSONDecodeError as exc:
            raise SnapshotRefusal("paper state is not valid JSON") from exc
    if not isinstance(state, dict):
        raise SnapshotRefusal("paper state must be an object")
    if state.get("open") is False:
        raise SnapshotRefusal("paper ledger says open but stored position state says closed")

    up = down = entry_cost = entry_fees = prior_proceeds = 0.0
    if state.get("up") or state.get("dn"):
        up, up_cost, up_fee, up_proceeds = _leg_inventory(state, "up")
        down, down_cost, down_fee, down_proceeds = _leg_inventory(state, "dn")
        entry_cost = up_cost + down_cost
        entry_fees = up_fee + down_fee
        prior_proceeds = up_proceeds + down_proceeds
        source = "paper_leg_state"
    else:
        side = str(row.get("side") or state.get("side") or "").upper()
        requested = _finite(
            state.get("requested_size", state.get("quantity", 1.0)),
            "requested_size",
            minimum=0.0,
        )
        filled = _finite(state.get("filled_size", requested), "filled_size", minimum=0.0)
        if filled > requested + 1e-9:
            raise SnapshotRefusal("filled_size exceeds requested_size")
        unit_entry = _finite(row.get("ask", state.get("entry")), "entry price", minimum=0.0)
        unit_fee = _finite(row.get("fee", state.get("fee_in", 0.0)),
                           "entry fee per share", minimum=0.0)
        if side == "UP":
            up = filled
        elif side == "DOWN":
            down = filled
        elif side == "BOTH":
            up = down = filled
        else:
            raise SnapshotRefusal(f"paper side is not UP, DOWN or BOTH: {side!r}")
        entry_cost = (unit_entry + unit_fee) * filled
        entry_fees = unit_fee * filled
        source = "paper_assumed_taker_fill"
    if up <= 0.0 and down <= 0.0:
        raise SnapshotRefusal("paper position has no remaining inventory")
    position_id = _hash({"round_id": round_id, "strategy_id": strategy,
                         "opened_ts": opened_ts})
    return PaperInventory(
        position_id=position_id,
        round_id=round_id,
        strategy_id=strategy,
        horizon_min=horizon,
        opened_ts=opened_ts,
        up_shares=up,
        down_shares=down,
        net_cost_basis=entry_cost - prior_proceeds,
        entry_fees=entry_fees,
        inventory_source=source,
        state=state,
    )


def _book(asset_id: str, side: dict[str, Any], recv_ms: int) -> L2Book:
    book = L2Book(asset_id)
    bids = [{"price": level[0], "size": level[1]} for level in side.get("bid_ladder") or []]
    asks = [{"price": level[0], "size": level[1]} for level in side.get("ask_ladder") or []]
    book.load_snapshot(
        bids,
        asks,
        market=asset_id,
        exchange_ts_ms=_seconds_to_ms(side.get("book_ts") or 0.0),
        recv_ts_ns=recv_ms * 1_000_000,
        book_hash=str(side.get("book_hash") or ""),
    )
    if not book.valid:
        raise SnapshotRefusal(f"{asset_id} book invalid: {book.invalid_reason}")
    return book


@dataclass(frozen=True)
class PairedBooks:
    snapshot_id: str
    quote_ts: int
    up_recv_ts: int
    down_recv_ts: int
    pair_skew_ms: int
    fee_rate: float
    fees_enabled: bool
    up: L2Book
    down: L2Book
    payload: dict[str, Any]


def normalize_paired_books(market: dict[str, Any], *, recorded_ts: int) -> PairedBooks:
    if not isinstance(market, dict):
        raise SnapshotRefusal("paired executable books are unavailable")
    quote_ts = _seconds_to_ms(market.get("ts"))
    if quote_ts > recorded_ts:
        raise SnapshotRefusal("paired quote snapshot is after recorded_ts")
    if recorded_ts - quote_ts > MAX_BOOK_AGE_MS:
        raise SnapshotRefusal("paired quote snapshot is stale")
    up_data = market.get("up")
    down_data = market.get("down")
    if not isinstance(up_data, dict) or not isinstance(down_data, dict):
        raise SnapshotRefusal("both UP and DOWN books are required")
    up_recv = _seconds_to_ms(up_data.get("quote_recv_ts") or market.get("ts"))
    down_recv = _seconds_to_ms(down_data.get("quote_recv_ts") or market.get("ts"))
    if up_recv > recorded_ts or down_recv > recorded_ts:
        raise SnapshotRefusal("a book receive timestamp is after recorded_ts")
    skew = abs(up_recv - down_recv)
    if skew > MAX_PAIR_RECV_SKEW_MS:
        raise SnapshotRefusal(
            f"paired book receive skew {skew}ms exceeds {MAX_PAIR_RECV_SKEW_MS}ms"
        )
    fee_rate = _finite(market.get("fee_rate", 0.07), "fee_rate", minimum=0.0)
    fees_enabled = market.get("fees_enabled") is not False
    up = _book("UP", up_data, up_recv)
    down = _book("DOWN", down_data, down_recv)
    snapshot_id = _hash({
        "slug": market.get("slug"), "quote_ts": quote_ts,
        "up_recv_ts": up_recv, "down_recv_ts": down_recv,
        "up_hash": up.book_hash, "down_hash": down.book_hash,
        "up_ladders": [up_data.get("bid_ladder"), up_data.get("ask_ladder")],
        "down_ladders": [down_data.get("bid_ladder"), down_data.get("ask_ladder")],
    })
    return PairedBooks(
        snapshot_id, quote_ts, up_recv, down_recv, skew,
        fee_rate if fees_enabled else 0.0, fees_enabled, up, down, market,
    )


def _empty_execution(action: str, size: float) -> dict[str, Any]:
    return {
        "side": action, "requested_size": size, "filled_size": 0.0,
        "unfilled_size": size, "complete": size <= 0.0, "fee": 0.0,
        "total_cash": 0.0, "reject_reason": None,
    }


def _execute(book: L2Book, action: str, size: float, fee_rate: float) -> dict[str, Any]:
    if size <= 0.0:
        return _empty_execution(action, 0.0)
    return book.execution_vwap(action, size, fee_rate=fee_rate).to_dict()


def _arm(
    action: str,
    position: PaperInventory,
    *,
    up_after: float,
    down_after: float,
    cash_flow: float,
    fees: float,
    executions: dict[str, Any],
    complete: bool,
    reject_reason: str | None = None,
) -> dict[str, Any]:
    floor = min(up_after, down_after)
    return {
        "action": action,
        "research_only": True,
        "executable": reject_reason is None,
        "complete": bool(complete),
        "reject_reason": reject_reason,
        "cash_flow": float(cash_flow),
        "fees": float(fees),
        "up_shares_after": max(0.0, float(up_after)),
        "down_shares_after": max(0.0, float(down_after)),
        "settlement_floor": float(floor),
        "settlement_floor_net": float(floor + cash_flow - position.net_cost_basis),
        "executions": executions,
    }


def evaluate_action_arms(position: PaperInventory, books: PairedBooks) -> list[dict[str, Any]]:
    up, down = position.up_shares, position.down_shares
    fee_rate = books.fee_rate
    arms = [_arm(
        "HOLD", position, up_after=up, down_after=down, cash_flow=0.0, fees=0.0,
        executions={}, complete=True,
    )]

    def sell_inventory(action: str, fraction: float) -> dict[str, Any]:
        up_result = _execute(books.up, "SELL", up * fraction, fee_rate)
        down_result = _execute(books.down, "SELL", down * fraction, fee_rate)
        cash = float(up_result["total_cash"]) + float(down_result["total_cash"])
        fees = float(up_result["fee"]) + float(down_result["fee"])
        return _arm(
            action,
            position,
            up_after=up - float(up_result["filled_size"]),
            down_after=down - float(down_result["filled_size"]),
            cash_flow=cash,
            fees=fees,
            executions={"sell_up": up_result, "sell_down": down_result},
            complete=bool(up_result["complete"] and down_result["complete"]),
        )

    arms.append(sell_inventory("EXIT", 1.0))
    arms.append(sell_inventory("REDUCE_50", 0.5))

    if up > 0.0 and down > 0.0:
        arms.append(_arm(
            "SWITCH", position, up_after=up, down_after=down, cash_flow=0.0, fees=0.0,
            executions={}, complete=False, reject_reason="two_sided_inventory",
        ))
    else:
        held_side = "UP" if up > 0.0 else "DOWN"
        held_size = up if up > 0.0 else down
        source_book = books.up if held_side == "UP" else books.down
        target_book = books.down if held_side == "UP" else books.up
        sold = _execute(source_book, "SELL", held_size, fee_rate)
        # SWITCH is causal exit-then-enter mechanics. Only inventory actually sold can be
        # replaced on the opposite side; otherwise shallow exit depth could manufacture a
        # larger two-sided position than the action released.
        bought = _execute(target_book, "BUY", float(sold["filled_size"]), fee_rate)
        up_after = up - (float(sold["filled_size"]) if held_side == "UP" else 0.0)
        down_after = down - (float(sold["filled_size"]) if held_side == "DOWN" else 0.0)
        if held_side == "UP":
            down_after += float(bought["filled_size"])
        else:
            up_after += float(bought["filled_size"])
        arms.append(_arm(
            "SWITCH", position, up_after=up_after, down_after=down_after,
            cash_flow=float(sold["total_cash"]) - float(bought["total_cash"]),
            fees=float(sold["fee"]) + float(bought["fee"]),
            executions={"sell_held": sold, "buy_opposite": bought},
            complete=bool(sold["complete"] and bought["complete"]),
        ))

    difference = abs(up - down)
    if difference <= 1e-12:
        arms.append(_arm(
            "LOCK", position, up_after=up, down_after=down, cash_flow=0.0, fees=0.0,
            executions={}, complete=True,
        ))
    else:
        target_side = "DOWN" if up > down else "UP"
        target_book = books.down if target_side == "DOWN" else books.up
        bought = _execute(target_book, "BUY", difference, fee_rate)
        up_after = up + (float(bought["filled_size"]) if target_side == "UP" else 0.0)
        down_after = down + (float(bought["filled_size"]) if target_side == "DOWN" else 0.0)
        arms.append(_arm(
            "LOCK", position, up_after=up_after, down_after=down_after,
            cash_flow=-float(bought["total_cash"]), fees=float(bought["fee"]),
            executions={"buy_opposite": bought}, complete=bool(bought["complete"]),
        ))
    if tuple(arm["action"] for arm in arms) != ACTIONS:
        raise AssertionError("action-arm order drifted")
    return arms


class OpenPositionActionRecorder:
    """Append-only paired books, position states, capture attempts and action arms."""

    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self, *, read_only: bool = False):
        import duckdb

        return duckdb.connect(str(self.db_path), read_only=read_only)

    def _ensure_schema(self) -> None:
        with self._lock:
            con = self._connect()
            try:
                con.execute("""
                    CREATE TABLE IF NOT EXISTS paired_book_snapshots (
                        paired_snapshot_id VARCHAR PRIMARY KEY,
                        schema_version VARCHAR NOT NULL,
                        quote_ts BIGINT NOT NULL,
                        up_recv_ts BIGINT NOT NULL,
                        down_recv_ts BIGINT NOT NULL,
                        pair_skew_ms BIGINT NOT NULL,
                        fee_rate DOUBLE NOT NULL,
                        fees_enabled BOOLEAN NOT NULL,
                        up_book_hash VARCHAR,
                        down_book_hash VARCHAR,
                        payload_json VARCHAR NOT NULL,
                        written_ts BIGINT NOT NULL
                    )
                """)
                con.execute("""
                    CREATE TABLE IF NOT EXISTS open_position_capture_attempts (
                        attempt_id VARCHAR PRIMARY KEY,
                        schema_version VARCHAR NOT NULL,
                        position_id VARCHAR NOT NULL,
                        round_id VARCHAR NOT NULL,
                        strategy_id VARCHAR NOT NULL,
                        attempted_ts BIGINT NOT NULL,
                        status VARCHAR NOT NULL,
                        reason VARCHAR NOT NULL
                    )
                """)
                con.execute("""
                    CREATE TABLE IF NOT EXISTS open_position_recorder_refusals (
                        refusal_id VARCHAR PRIMARY KEY,
                        schema_version VARCHAR NOT NULL,
                        refused_ts BIGINT NOT NULL,
                        category VARCHAR NOT NULL,
                        reason VARCHAR NOT NULL,
                        raw_position_json VARCHAR NOT NULL
                    )
                """)
                con.execute("""
                    CREATE TABLE IF NOT EXISTS open_position_snapshots (
                        position_snapshot_id VARCHAR PRIMARY KEY,
                        schema_version VARCHAR NOT NULL,
                        position_id VARCHAR NOT NULL,
                        round_id VARCHAR NOT NULL,
                        strategy_id VARCHAR NOT NULL,
                        horizon_min INTEGER NOT NULL,
                        opened_ts BIGINT NOT NULL,
                        paired_snapshot_id VARCHAR NOT NULL,
                        snapshot_ts BIGINT NOT NULL,
                        recorded_ts BIGINT NOT NULL,
                        up_shares DOUBLE NOT NULL,
                        down_shares DOUBLE NOT NULL,
                        net_cost_basis DOUBLE NOT NULL,
                        entry_fees DOUBLE NOT NULL,
                        inventory_source VARCHAR NOT NULL,
                        position_state_json VARCHAR NOT NULL,
                        context_json VARCHAR NOT NULL,
                        payload_hash VARCHAR NOT NULL
                    )
                """)
                con.execute("""
                    CREATE TABLE IF NOT EXISTS open_position_action_arms (
                        position_snapshot_id VARCHAR NOT NULL,
                        action VARCHAR NOT NULL,
                        research_only BOOLEAN NOT NULL,
                        executable BOOLEAN NOT NULL,
                        complete BOOLEAN NOT NULL,
                        reject_reason VARCHAR,
                        cash_flow DOUBLE NOT NULL,
                        fees DOUBLE NOT NULL,
                        up_shares_after DOUBLE NOT NULL,
                        down_shares_after DOUBLE NOT NULL,
                        settlement_floor DOUBLE NOT NULL,
                        settlement_floor_net DOUBLE NOT NULL,
                        execution_json VARCHAR NOT NULL,
                        PRIMARY KEY (position_snapshot_id, action)
                    )
                """)
            finally:
                con.close()

    @staticmethod
    def _attempt(con: Any, position: PaperInventory, attempted_ts: int,
                 status: str, reason: str) -> None:
        attempt_id = _hash({"position_id": position.position_id, "attempted_ts": attempted_ts,
                            "status": status})
        con.execute(
            "INSERT INTO open_position_capture_attempts VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT (attempt_id) DO NOTHING",
            [attempt_id, SCHEMA_VERSION, position.position_id, position.round_id,
             position.strategy_id, attempted_ts, status, str(reason)[:500]],
        )

    @staticmethod
    def _refusal(con: Any, row: Any, refused_ts: int, category: str, reason: str) -> None:
        raw = _canonical(row)
        refusal_id = _hash({
            "refused_ts": int(refused_ts), "category": category,
            "reason": str(reason), "raw": raw,
        })
        con.execute(
            "INSERT INTO open_position_recorder_refusals VALUES (?,?,?,?,?,?) "
            "ON CONFLICT (refusal_id) DO NOTHING",
            [refusal_id, SCHEMA_VERSION, int(refused_ts), str(category),
             str(reason)[:500], raw],
        )

    def record_positions(
        self,
        positions: list[dict[str, Any]],
        *,
        market_snapshot: dict[str, Any] | None,
        recorded_ts: int,
        context: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        recorded_ts = int(recorded_ts)
        attempted = len(positions)
        normalized: list[PaperInventory] = []
        invalid_rows: list[tuple[Any, str]] = []
        for row in positions:
            try:
                normalized.append(normalize_paper_position(row))
            except SnapshotRefusal as exc:
                invalid_rows.append((row, str(exc)))
        if invalid_rows:
            with self._lock:
                con = self._connect()
                try:
                    for row, reason in invalid_rows:
                        self._refusal(con, row, recorded_ts, "INVALID_POSITION", reason)
                finally:
                    con.close()
        invalid = len(invalid_rows)
        if not normalized:
            return {"positions": attempted, "normalized_positions": 0,
                    "snapshots": 0, "arms": 0, "refused": invalid}
        try:
            books = normalize_paired_books(market_snapshot or {}, recorded_ts=recorded_ts)
        except SnapshotRefusal as exc:
            with self._lock:
                con = self._connect()
                try:
                    for position in normalized:
                        self._attempt(con, position, recorded_ts, "NO_PAIRED_BOOK", str(exc))
                finally:
                    con.close()
            return {"positions": attempted, "normalized_positions": len(normalized),
                    "snapshots": 0, "arms": 0, "refused": len(normalized) + invalid}

        snapshots = arms_written = 0
        with self._lock:
            con = self._connect()
            try:
                con.execute("BEGIN TRANSACTION")
                con.execute(
                    "INSERT INTO paired_book_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT (paired_snapshot_id) DO NOTHING",
                    [books.snapshot_id, SCHEMA_VERSION, books.quote_ts, books.up_recv_ts,
                     books.down_recv_ts, books.pair_skew_ms, books.fee_rate,
                     books.fees_enabled, books.up.book_hash, books.down.book_hash,
                     _canonical(books.payload), recorded_ts],
                )
                for position in normalized:
                    position_snapshot_id = _hash({
                        "position_id": position.position_id,
                        "paired_snapshot_id": books.snapshot_id,
                        "inventory": {
                            "up": position.up_shares,
                            "down": position.down_shares,
                            "net_cost_basis": position.net_cost_basis,
                            "entry_fees": position.entry_fees,
                            "state": position.state,
                        },
                    })
                    payload = {
                        "position": asdict(position), "paired_snapshot_id": books.snapshot_id,
                        "context": context or {},
                    }
                    before = con.execute(
                        "SELECT 1 FROM open_position_snapshots WHERE position_snapshot_id = ?",
                        [position_snapshot_id],
                    ).fetchone()
                    if before:
                        self._attempt(con, position, recorded_ts, "DUPLICATE", "idempotent retry")
                        continue
                    con.execute(
                        "INSERT INTO open_position_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [position_snapshot_id, SCHEMA_VERSION, position.position_id,
                         position.round_id, position.strategy_id, position.horizon_min,
                         position.opened_ts, books.snapshot_id,
                         max(books.up_recv_ts, books.down_recv_ts), recorded_ts,
                         position.up_shares, position.down_shares, position.net_cost_basis,
                         position.entry_fees, position.inventory_source,
                         _canonical(position.state), _canonical(context or {}), _hash(payload)],
                    )
                    snapshots += 1
                    for arm in evaluate_action_arms(position, books):
                        con.execute(
                            "INSERT INTO open_position_action_arms VALUES "
                            "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            [position_snapshot_id, arm["action"], True, arm["executable"],
                             arm["complete"], arm["reject_reason"], arm["cash_flow"],
                             arm["fees"], arm["up_shares_after"], arm["down_shares_after"],
                             arm["settlement_floor"], arm["settlement_floor_net"],
                             _canonical(arm["executions"])],
                        )
                        arms_written += 1
                    self._attempt(con, position, recorded_ts, "RECORDED", "paired books captured")
                con.execute("COMMIT")
            except Exception:
                try:
                    con.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                con.close()
        return {"positions": attempted, "normalized_positions": len(normalized),
                "snapshots": snapshots, "arms": arms_written, "refused": invalid}

    def coverage(self) -> dict[str, Any]:
        with self._lock:
            con = self._connect(read_only=True)
            try:
                attempts = {str(k): int(v) for k, v in con.execute(
                    "SELECT status, count(*) FROM open_position_capture_attempts GROUP BY 1"
                ).fetchall()}
                snapshots = int(con.execute(
                    "SELECT count(*) FROM open_position_snapshots"
                ).fetchone()[0])
                paired = int(con.execute(
                    "SELECT count(*) FROM paired_book_snapshots"
                ).fetchone()[0])
                arms = {str(k): int(v) for k, v in con.execute(
                    "SELECT action, count(*) FROM open_position_action_arms GROUP BY 1"
                ).fetchall()}
                partial = int(con.execute(
                    "SELECT count(*) FROM open_position_action_arms "
                    "WHERE executable AND NOT complete"
                ).fetchone()[0])
                refusals = {str(k): int(v) for k, v in con.execute(
                    "SELECT category, count(*) FROM open_position_recorder_refusals GROUP BY 1"
                ).fetchall()}
            finally:
                con.close()
        return {"paired_book_snapshots": paired, "position_snapshots": snapshots,
                "attempts_by_status": attempts, "arms_by_action": arms,
                "partial_fill_arms": partial, "refusals_by_category": refusals,
                "capital_authority": False}


_RECORDER: OpenPositionActionRecorder | None = None
_RECORDER_LOCK = threading.Lock()


def recorder() -> OpenPositionActionRecorder:
    global _RECORDER
    with _RECORDER_LOCK:
        if _RECORDER is None:
            _RECORDER = OpenPositionActionRecorder(DEFAULT_DB)
        return _RECORDER


def _sample_market(ts: float = 1000.0, *, shallow_up_bid: bool = False) -> dict[str, Any]:
    up_bid_size = 0.5 if shallow_up_bid else 2.0
    return {
        "slug": "btc-updown-test", "ts": ts, "fee_rate": 0.07, "fees_enabled": True,
        "artifact_hash": "bridge-hash",
        "up": {
            "bid": 0.69, "ask": 0.71, "quote_recv_ts": ts, "book_ts": ts,
            "book_hash": "up-hash", "bid_ladder": [[0.69, up_bid_size], [0.68, 4.0]],
            "ask_ladder": [[0.71, 4.0], [0.72, 5.0]],
        },
        "down": {
            "bid": 0.27, "ask": 0.29, "quote_recv_ts": ts, "book_ts": ts,
            "book_hash": "down-hash", "bid_ladder": [[0.27, 4.0], [0.26, 5.0]],
            "ask_ladder": [[0.29, 4.0], [0.30, 5.0]],
        },
    }


def selftest() -> int:
    checks = 0

    def check(condition: bool, label: str) -> None:
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    row = {
        "round_id": "r1", "rule": "RULE", "ts": 999_000, "horizon": 5,
        "side": "UP", "ask": 0.60, "fee": 0.01,
        "state": {"side": "UP", "quantity": 2.0, "filled_size": 2.0},
    }
    position = normalize_paper_position(row)
    check(abs(position.net_cost_basis - 1.22) < 1e-12,
          "entry cost and fees scale with the filled share quantity")
    books = normalize_paired_books(_sample_market(), recorded_ts=1_000_100)
    arms = {arm["action"]: arm for arm in evaluate_action_arms(position, books)}
    check(set(arms) == set(ACTIONS), "all five action arms are valued from one paired snapshot")
    check(arms["HOLD"]["up_shares_after"] == 2.0 and arms["HOLD"]["cash_flow"] == 0.0,
          "HOLD preserves inventory and creates no imaginary fill")
    check(arms["EXIT"]["complete"] and arms["EXIT"]["up_shares_after"] == 0.0,
          "EXIT walks the bid ladder for the full current inventory")
    check(arms["REDUCE_50"]["up_shares_after"] == 1.0,
          "REDUCE_50 sells exactly half and preserves the residual share")
    check(arms["SWITCH"]["complete"] and arms["SWITCH"]["down_shares_after"] == 2.0,
          "SWITCH sells the held side and buys equal opposite inventory")
    check(arms["LOCK"]["complete"] and arms["LOCK"]["settlement_floor"] == 2.0,
          "LOCK buys the inventory difference and records its guaranteed settlement floor")
    check(arms["EXIT"]["fees"] > 0 and arms["LOCK"]["fees"] > 0,
          "sell and buy action arms both charge the crypto fee curve")

    shallow = normalize_paired_books(
        _sample_market(shallow_up_bid=True), recorded_ts=1_000_100,
    )
    partial = {arm["action"]: arm for arm in evaluate_action_arms(position, shallow)}["EXIT"]
    check(partial["complete"] and partial["executions"]["sell_up"]["levels_consumed"] == 2,
          "EXIT consumes deeper bid levels rather than assuming top-level capacity")
    scarce_market = _sample_market()
    scarce_market["up"]["bid_ladder"] = [[0.69, 0.5]]
    scarce = normalize_paired_books(scarce_market, recorded_ts=1_000_100)
    scarce_switch = {
        arm["action"]: arm for arm in evaluate_action_arms(position, scarce)
    }["SWITCH"]
    check(not scarce_switch["complete"] and
          scarce_switch["up_shares_after"] == 1.5 and
          scarce_switch["down_shares_after"] == 0.5,
          "a partial SWITCH buys only the quantity actually sold and preserves residual inventory")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        ledger = OpenPositionActionRecorder(Path(tmp) / "actions.duckdb")
        result = ledger.record_positions(
            [row], market_snapshot=_sample_market(), recorded_ts=1_000_100,
            context={"mode": "PAPER_ONLY"},
        )
        check(result["snapshots"] == 1 and result["arms"] == 5,
              "one position snapshot writes five normalized arms")
        retry = ledger.record_positions(
            [row], market_snapshot=_sample_market(), recorded_ts=1_000_100,
        )
        check(retry["snapshots"] == 0 and ledger.coverage()["position_snapshots"] == 1,
              "an identical paired snapshot retry is idempotent")
        unavailable = ledger.record_positions(
            [row], market_snapshot=None, recorded_ts=1_005_100,
        )
        check(unavailable["snapshots"] == 0 and
              ledger.coverage()["attempts_by_status"].get("NO_PAIRED_BOOK") == 1,
              "missing paired books remain visible as a failed capture attempt")
        malformed = ledger.record_positions(
            [{"round_id": "r-bad"}], market_snapshot=_sample_market(),
            recorded_ts=1_006_100,
        )
        check(malformed["positions"] == 1 and malformed["refused"] == 1 and
              ledger.coverage()["refusals_by_category"].get("INVALID_POSITION") == 1,
              "malformed positions remain visible in the refusal denominator")
        coverage = ledger.coverage()
        check(coverage["arms_by_action"] == {action: 1 for action in ACTIONS},
              "coverage preserves a separate denominator for every action")
        check(coverage["capital_authority"] is False,
              "the recorder exposes no capital or execution authority")

    try:
        normalize_paired_books(_sample_market(ts=1001.0), recorded_ts=1_000_100)
        check(False, "unreachable")
    except SnapshotRefusal:
        check(True, "a future-dated paired snapshot is refused")

    closed = dict(row)
    closed["state"] = {"side": "UP", "open": False}
    try:
        normalize_paper_position(closed)
        check(False, "unreachable")
    except SnapshotRefusal:
        check(True, "an open ledger row with closed stored state is refused")

    print(f"\nOPEN POSITION ACTION RECORDER SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.report:
        print(json.dumps(OpenPositionActionRecorder(args.db).coverage(), indent=2, sort_keys=True))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
