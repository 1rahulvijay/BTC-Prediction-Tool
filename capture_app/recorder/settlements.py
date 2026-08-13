"""Polymarket settlement fetcher. The other half of the pair, recorded by the same process.

WHY THIS LIVES HERE AND NOT IN A SEPARATE JOB
    This project already ran a quote recorder and a settlement fetcher at different times. The
    result: 916 rounds of quotes across ten days, 6,725 officially settled rounds across a
    different twenty days, and an anchor intersection of exactly ZERO. Both halves existed;
    neither was usable, because a residual model needs the price AND the outcome for the same
    round.

    Two processes with independent lifetimes will eventually diverge. One process cannot.

TERMINAL OUTCOMES ARE WRITE-ONCE
    A settlement is a fact. Once recorded it is never rewritten, and re-fetching the same round
    is a no-op rather than an update. This repository has already had a defect where a
    prediction writer could flip `resolved` back to FALSE on a settled row; the lesson is that
    terminal state needs structural protection, not a convention.

    Here that protection is an index of what has been written, plus append-only partitions. A
    round already in the index is skipped entirely - the fetcher cannot change history even if
    the upstream API later reports something different. Disagreements are recorded as a
    conflict row for a human, never applied silently.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from pathlib import Path

import pyarrow as pa

from .storage import PartitionWriter, write_status

GAMMA = "https://gamma-api.polymarket.com"

SETTLEMENT_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),           # when WE recorded it
    ("slug", pa.string()),
    ("condition_id", pa.string()),
    ("horizon", pa.int32()),         # minutes, parsed from the slug
    ("anchor_ts", pa.int64()),       # round start, seconds, parsed from the slug
    ("outcome", pa.string()),        # "UP" | "DOWN"
    ("up_win", pa.int32()),          # 1 | 0  - the join key research uses
    ("resolution_source", pa.string()),
    ("closed_ms", pa.int64()),
    ("raw_outcome_prices", pa.string()),
])

CONFLICT_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("slug", pa.string()),
    ("recorded_up_win", pa.int32()),
    ("upstream_up_win", pa.int32()),
    ("note", pa.string()),
])


def _get(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": "btc-capture/1.0",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def parse_slug(slug: str) -> tuple[int | None, int | None]:
    """`btc-updown-5m-1781626200` -> (horizon_minutes, anchor_ts_seconds)."""
    parts = str(slug).split("-")
    horizon = anchor = None
    for p in parts:
        if p.endswith("m") and p[:-1].isdigit():
            horizon = int(p[:-1])
        elif p.isdigit() and len(p) >= 9:
            anchor = int(p)
    return horizon, anchor


def resolve_outcome(market: dict) -> tuple[str | None, int | None, str]:
    """Winner from a closed market, or (None, None, reason).

    Gamma reports `outcomePrices` as strings that settle to "1"/"0". Anything that is not a
    clean 1/0 pair is UNRESOLVED - a market can be closed but not yet finalised, and recording
    a guess as a settlement is exactly the kind of fabricated evidence that poisons a research
    set.
    """
    try:
        prices = json.loads(market.get("outcomePrices") or "[]")
        outcomes = json.loads(market.get("outcomes") or "[]")
    except Exception:
        return None, None, "unparseable_outcome_fields"
    if len(prices) != 2 or len(outcomes) != 2:
        return None, None, "unexpected_outcome_arity"
    try:
        vals = [float(x) for x in prices]
    except Exception:
        return None, None, "non_numeric_prices"
    hi = [i for i, v in enumerate(vals) if v > 0.99]
    lo = [i for i, v in enumerate(vals) if v < 0.01]
    if len(hi) != 1 or len(lo) != 1:
        return None, None, f"not_finalised:{prices}"
    winner = str(outcomes[hi[0]]).strip().upper()
    if winner not in ("UP", "DOWN"):
        return None, None, f"unexpected_outcome_label:{winner}"
    return winner, (1 if winner == "UP" else 0), "ok"


class SettlementIndex:
    """What has already been written. Makes the fetcher idempotent and history immutable."""

    def __init__(self, state_dir: Path):
        self.path = state_dir / "settled_index.json"
        self.data: dict[str, int] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def key(self, slug: str, horizon) -> str:
        return f"{slug}|{horizon}"

    def has(self, slug: str, horizon) -> bool:
        return self.key(slug, horizon) in self.data

    def get(self, slug: str, horizon):
        return self.data.get(self.key(slug, horizon))

    def add(self, slug: str, horizon, up_win: int) -> None:
        self.data[self.key(slug, horizon)] = int(up_win)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data), encoding="utf-8")
        tmp.replace(self.path)


def fetch_closed(slug_contains: str, limit: int, offset: int = 0) -> list[dict]:
    url = (f"{GAMMA}/events?closed=true&limit={limit}&offset={offset}"
           f"&order=endDate&ascending=false")
    events = _get(url)
    return [e for e in events if slug_contains in str(e.get("slug", ""))]


async def poll_settlements(root: Path, stop: asyncio.Event, *,
                           slug_contains: str = "btc-updown",
                           interval_s: int = 300, pages: int = 3,
                           page_size: int = 100) -> None:
    """Poll closed markets and append newly-finalised outcomes. Never rewrites."""
    state_dir = root.parent / "state"
    idx = SettlementIndex(state_dir)
    w = PartitionWriter(root, "polymarket_settlement", SETTLEMENT_SCHEMA,
                        max_rows=200, max_seconds=60)
    wc = PartitionWriter(root, "polymarket_settlement_conflict", CONFLICT_SCHEMA,
                         max_rows=50, max_seconds=300)
    stats = {"written": 0, "skipped_known": 0, "unresolved": 0, "conflicts": 0, "errors": 0}

    while not stop.is_set():
        try:
            for page in range(pages):
                events = await asyncio.to_thread(
                    fetch_closed, slug_contains, page_size, page * page_size)
                for ev in events:
                    slug = str(ev.get("slug") or "")
                    horizon, anchor = parse_slug(slug)
                    for mk in (ev.get("markets") or []):
                        winner, up_win, why = resolve_outcome(mk)
                        if winner is None:
                            stats["unresolved"] += 1
                            continue
                        if idx.has(slug, horizon):
                            prior = idx.get(slug, horizon)
                            if prior != up_win:
                                # Upstream now disagrees with what we recorded. Do NOT apply it.
                                wc.add({"ts_ms": int(time.time() * 1000), "slug": slug,
                                        "recorded_up_win": int(prior),
                                        "upstream_up_win": int(up_win),
                                        "note": "upstream changed after first write; "
                                                "recorded value retained"})
                                stats["conflicts"] += 1
                            else:
                                stats["skipped_known"] += 1
                            continue
                        w.add({
                            "ts_ms": int(time.time() * 1000), "slug": slug,
                            "condition_id": str(mk.get("conditionId") or ""),
                            "horizon": int(horizon) if horizon else 0,
                            "anchor_ts": int(anchor) if anchor else 0,
                            "outcome": winner, "up_win": int(up_win),
                            "resolution_source": "polymarket_gamma",
                            "closed_ms": int(float(mk.get("closedTime") or 0) or 0),
                            "raw_outcome_prices": str(mk.get("outcomePrices") or ""),
                        })
                        idx.add(slug, horizon, up_win)
                        stats["written"] += 1
        except Exception as exc:                                   # noqa: BLE001
            stats["errors"] += 1
            stats["last_error"] = str(exc)[:200]
        w.flush(); wc.flush()
        write_status(root, "polymarket_settlement",
                     {**stats, "rows": stats["written"], "files": w.files_written,
                      "indexed_rounds": len(idx.data)})
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
    w.flush(); wc.flush()
