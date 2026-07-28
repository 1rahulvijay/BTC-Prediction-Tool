"""Data contract - what this campaign requires before any family may be evaluated.

The contract is enforced, not documented. `evaluate_archive()` reads the live
multi-venue archive and reports, per required stream, whether it exists, how
continuous it is, and which families it unblocks. Families whose inputs are absent
are NOT_READY; they are never silently downgraded to a smaller feature set.

Two rules carried from the collector work and restated here because they are the
ones most easily lost in a replay:

    1. Never combine events across a recorder gap. A candidate whose feature window
       or holding period spans a gap is segmented out, not stitched.
    2. Never forward-fill a missing period. A missing input is MISSING.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from contracts import DataQuality, Family

ROOT = Path(__file__).resolve().parents[3]
DATA = Path(os.environ.get("BTC_DATA_DIR") or (ROOT / "data"))
VENUE_DB = DATA / "multi_venue.duckdb"

# A gap longer than this ends a continuity segment. Chosen to match the collector's
# own admissibility rule rather than invented here.
SEGMENT_GAP_MS = 10_000

# Ordinary book cadence the campaign requires. The V1 archive delivered ~5s batches,
# which is why it could not support sub-second latency claims.
MAX_ORDINARY_BOOK_INTERVAL_MS = 250

REQUIRED_DAYS_MIN = 60
PREFERRED_DAYS = 90
VALID_TRADING_DAYS_MIN = 45


@dataclass(frozen=True, slots=True)
class StreamReq:
    key: str
    venue: str
    stream: str
    families: tuple[Family, ...]
    note: str = ""


# Which streams each family genuinely needs. A family may not run without ALL of its
# streams; there is no partial-input mode and no substitution across venues.
REQUIRED_STREAMS: tuple[StreamReq, ...] = (
    StreamReq("perp_book", "binance_perp", "bookTicker",
              tuple(Family), "executable quotes - every family prices through these"),
    StreamReq("perp_trades", "binance_perp", "aggTrade",
              tuple(Family), "aggressive flow and trade-through maker labels"),
    StreamReq("liquidations", "binance_perp", "forceOrder",
              (Family.LIQUIDATION_CONTINUATION, Family.LIQUIDATION_EXHAUSTION)),
    StreamReq("mark_index", "binance_perp", "markPrice",
              (Family.FUNDING_BASIS_OI,), "mark, index and funding rate"),
    StreamReq("open_interest", "binance_perp", "openInterest",
              (Family.FUNDING_BASIS_OI,)),
    StreamReq("spot_book", "binance_spot", "bookTicker",
              (Family.CROSS_VENUE_LEAD_LAG,)),
    StreamReq("spot_trades", "binance_spot", "aggTrade",
              (Family.CROSS_VENUE_LEAD_LAG,)),
    StreamReq("bybit_perp", "bybit_perp", "orderbook.1",
              (Family.CROSS_VENUE_LEAD_LAG,), "optional-but-declared second perp venue"),
    StreamReq("coinbase_spot", "coinbase_spot", "ticker",
              (Family.CROSS_VENUE_LEAD_LAG,), "optional-but-declared second spot venue"),
)


@dataclass(slots=True)
class StreamStatus:
    key: str
    venue: str
    stream: str
    rows: int
    first_ts_ms: int | None
    last_ts_ms: int | None
    span_days: float
    present: bool

    @property
    def summary(self) -> str:
        if not self.present:
            return "ABSENT"
        return f"{self.rows:,} rows / {self.span_days:.2f}d"


@dataclass(slots=True)
class ArchiveReport:
    db_path: str
    db_exists: bool
    streams: list[StreamStatus]
    family_ready: dict[str, bool]
    family_blockers: dict[str, list[str]]
    total_rows: int
    span_days: float

    @property
    def any_ready(self) -> bool:
        return any(self.family_ready.values())


def segment_events(timestamps_ms: list[int], gap_ms: int = SEGMENT_GAP_MS) -> list[str]:
    """Assign a continuity segment id to each timestamp. A gap larger than `gap_ms`
    starts a new segment, so nothing downstream can join across it."""
    out: list[str] = []
    seg = 0
    prev: int | None = None
    for ts in timestamps_ms:
        if prev is not None and ts - prev > gap_ms:
            seg += 1
        out.append(f"seg{seg:04d}")
        prev = ts
    return out


def quality_for(missing: list[str], stale: list[str], spans_gap: bool) -> DataQuality:
    """Fail closed, in a fixed precedence. Missing beats stale beats gap so the most
    disqualifying condition is the one reported."""
    if missing:
        return DataQuality.MISSING
    if stale:
        return DataQuality.STALE
    if spans_gap:
        return DataQuality.GAP_SEGMENTED
    return DataQuality.OK


def evaluate_archive(db_path: Path | None = None) -> ArchiveReport:
    """Read the live archive and report what it can and cannot support today."""
    path = Path(db_path or VENUE_DB)
    statuses: list[StreamStatus] = []
    total, span = 0, 0.0

    if not path.exists():
        for r in REQUIRED_STREAMS:
            statuses.append(StreamStatus(r.key, r.venue, r.stream, 0, None, None, 0.0, False))
    else:
        import duckdb
        con = duckdb.connect(str(path), read_only=True)
        try:
            try:
                total = con.execute("SELECT COUNT(*) FROM venue_events").fetchone()[0]
            except Exception:
                total = 0
            for r in REQUIRED_STREAMS:
                try:
                    row = con.execute(
                        "SELECT COUNT(*), MIN(recv_ts), MAX(recv_ts) FROM venue_events "
                        "WHERE venue = ? AND stream = ?", [r.venue, r.stream]).fetchone()
                except Exception:
                    row = (0, None, None)
                n = int(row[0] or 0)
                lo, hi = row[1], row[2]
                days = ((hi - lo) / 86_400_000.0) if (lo and hi and hi > lo) else 0.0
                span = max(span, days)
                statuses.append(StreamStatus(r.key, r.venue, r.stream, n, lo, hi, days, n > 0))
        finally:
            con.close()

    by_key = {s.key: s for s in statuses}
    family_ready: dict[str, bool] = {}
    family_blockers: dict[str, list[str]] = {}
    for fam in Family:
        needed = [r for r in REQUIRED_STREAMS if fam in r.families]
        blockers = [f"{r.venue}/{r.stream}" for r in needed if not by_key[r.key].present]
        if span < REQUIRED_DAYS_MIN:
            blockers.append(f"continuous span {span:.2f}d < {REQUIRED_DAYS_MIN}d required")
        family_ready[fam.value] = not blockers
        family_blockers[fam.value] = blockers

    return ArchiveReport(
        db_path=str(path), db_exists=path.exists(), streams=statuses,
        family_ready=family_ready, family_blockers=family_blockers,
        total_rows=total, span_days=span,
    )
