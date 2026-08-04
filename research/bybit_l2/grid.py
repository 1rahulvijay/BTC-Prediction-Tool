"""Reconstruct the Bybit 200-level book once, cache a fixed-cadence feature grid.

WHY A CACHE AND NOT A REPLAY PER STUDY
    The archive is 6 days x ~926k records. A replay costs ~11s/day, which is cheap once and
    expensive eighteen times - and every study that re-derives depth from raw deltas is a study
    that can derive it slightly differently. One grid, built once, means every L2 study is
    measuring the same book.

THE ANCHOR DECISION, WHICH IS THE WHOLE MEASUREMENT
    "Depth within 5 bps of mid" moves with the mid. If price walks away from resting liquidity,
    that metric collapses without a single order being cancelled - and a study built on it would
    report a "vacuum" caused by the very move it then claims to predict.

    So the grid stores depth in bands anchored at a FIXED price, and the study supplies that
    price (the mid at the start of its lookback window). What is cached here is the raw
    ingredient that makes such a query possible: the full price->size ladder, binned to a
    coarse absolute price grid, not a mid-relative summary.

    python -m research.bybit_l2.grid --selftest
    python -m research.bybit_l2.grid --build          # all days
    python -m research.bybit_l2.grid --build --day 2026-08-01
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))

L2_DIR = ROOT / "data" / "bybit_l2"
CACHE = ROOT / "data" / "bybit_l2_grid"
SYMBOL = "BTCUSDT"

#: Sampling cadence. The feed updates ~10.7x/s (926k records/day), so 100 ms neither aliases
#: the book nor stores redundant frames.
CADENCE_MS = 100

#: Ladder bins, as ABSOLUTE DISTANCE in dollars from the sampled mid. Absolute rather than bps
#: so a study can re-anchor to any price without re-reading the archive.
#:
#: Two resolutions, because one is unaffordable. The protocol declares bands out to 20 bps,
#: which at $63,000 is $126; a flat $1 ladder that wide costs ~1.9 GB/day at a 100 ms cadence.
#: Fine $1 bins cover the primary 5 bps band ($31.5) with sub-bin error; coarse $5 bins carry
#: the 10 and 20 bps robustness bands, where a $2.5 edge error is tolerable because those cells
#: are secondary and carry no verdict.
#:
#: Stored PER SIDE as distance, not signed offset: bids only ever rest below the mid and asks
#: above it, so a signed ladder would be half zeros.
FINE_TO, FINE_BIN = 40.0, 1.0
COARSE_TO, COARSE_BIN = 140.0, 5.0


def ladder_edges() -> np.ndarray:
    """Bin edges as distance from mid: 0..40 in $1, then 40..140 in $5."""
    return np.concatenate([np.arange(0.0, FINE_TO, FINE_BIN),
                           np.arange(FINE_TO, COARSE_TO + COARSE_BIN, COARSE_BIN)])


def bin_index(distance: float) -> int | None:
    """Bin holding `distance` dollars from mid, or None if beyond the cached window."""
    if distance < 0 or distance >= COARSE_TO:
        return None
    if distance < FINE_TO:
        return int(distance // FINE_BIN)
    return int(FINE_TO // FINE_BIN) + int((distance - FINE_TO) // COARSE_BIN)


def bin_centres() -> np.ndarray:
    edges = ladder_edges()
    return (edges[:-1] + edges[1:]) / 2.0


def bin_side(levels: dict, mid: float, is_bid: bool) -> np.ndarray:
    """Total resting size per bin, keyed by DISTANCE from mid.

    `is_bid` selects the direction that counts as "away from mid": a bid $2 below the mid and an
    ask $2 above it both land in the same distance bin of their own side's ladder.

    Vectorised. The obvious Python loop over ~200 levels runs 173M iterations per day at a
    100 ms cadence and did not finish a single day in ten minutes; np.digitize/np.bincount
    move that into C and cost ~6 array calls per sample instead."""
    width = len(ladder_edges()) - 1
    if not levels:
        return np.zeros(width, dtype=np.float32)
    prices = np.fromiter(levels.keys(), dtype=np.float64, count=len(levels))
    sizes = np.fromiter(levels.values(), dtype=np.float64, count=len(levels))
    distance = (mid - prices) if is_bid else (prices - mid)
    inside = (distance >= 0.0) & (distance < COARSE_TO)
    if not inside.any():
        return np.zeros(width, dtype=np.float32)
    index = np.digitize(distance[inside], ladder_edges()) - 1
    return np.bincount(index, weights=sizes[inside],
                       minlength=width)[:width].astype(np.float32)


def build_day(path: Path, out_path: Path, limit: int | None = None) -> dict:
    """Replay one day and write a 100 ms grid. Returns a provenance summary."""
    import pandas as pd
    from bybit_l2_maker_v1 import ReplayInvalid, replay

    width = len(ladder_edges()) - 1
    # Preallocated rather than a list of 864k small arrays, which costs ~830 MB of Python
    # objects before parquet ever sees it. Grown geometrically; trimmed at the end.
    capacity = 1 << 20
    ladder = np.zeros((capacity, 2 * width), dtype=np.float32)
    scalars = np.zeros((capacity, 8), dtype=np.float64)
    count = 0

    next_sample: int | None = None
    invalid = None
    records = 0
    try:
        for ts_ms, book in replay(path, max_records=limit):
            records += 1
            if next_sample is None:
                next_sample = ts_ms - (ts_ms % CADENCE_MS)
            if ts_ms < next_sample:
                continue
            bid, bid_size, ask, ask_size = book.best()
            mid = (bid + ask) / 2.0
            if count == capacity:
                capacity *= 2
                ladder = np.resize(ladder, (capacity, 2 * width))
                scalars = np.resize(scalars, (capacity, 8))
            scalars[count] = (next_sample, mid, bid, ask, bid_size, ask_size,
                              len(book.bids), len(book.asks))
            ladder[count, :width] = bin_side(book.bids, mid, True)
            ladder[count, width:] = bin_side(book.asks, mid, False)
            count += 1
            # Advance past any gap rather than emitting a burst of catch-up frames.
            next_sample += CADENCE_MS * (1 + (ts_ms - next_sample) // CADENCE_MS)
    except ReplayInvalid as exc:
        invalid = str(exc)

    scalars, ladder = scalars[:count], ladder[:count]
    frame = pd.DataFrame({
        "ts_ms": scalars[:, 0].astype("int64"), "mid": scalars[:, 1],
        "bid": scalars[:, 2], "ask": scalars[:, 3],
        "bid_size": scalars[:, 4], "ask_size": scalars[:, 5],
        "n_bid_levels": scalars[:, 6].astype("int32"),
        "n_ask_levels": scalars[:, 7].astype("int32")})
    for index in range(width):
        frame[f"b{index}"] = ladder[:, index]
        frame[f"a{index}"] = ladder[:, width + index]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out_path, index=False)
    return {"day": path.name.split("_")[0], "records": records, "samples": len(frame),
            "invalid": invalid, "path": str(out_path.relative_to(ROOT))}


def depth_in_band(frame, index, anchor_mid: float, side: str,
                  band_bps: float) -> float:
    """Resting size on `side` within `band_bps` of a FIXED `anchor_mid`, at grid row `index`.

    The band is anchored at a price the caller supplies, not at the row's own mid. That is the
    whole point of the cache: it lets a study ask "is the liquidity that was HERE still here?"
    rather than "is there liquidity near price now?" - questions that differ exactly when price
    has moved, which is exactly when the answer matters.

    Returns NaN when the requested band is not fully inside the cached window. FAIL-CLOSED: a
    silently truncated band would understate depth and read as a vacuum, manufacturing the very
    event the study counts. Callers must count the refusals rather than treat NaN as zero."""
    row_mid = _value(frame, "mid", index)
    reach = anchor_mid * band_bps / 10_000.0
    # The band runs from the anchor AWAY from mid, on the given side.
    near, far = (anchor_mid, anchor_mid - reach) if side == "b" else (anchor_mid,
                                                                     anchor_mid + reach)
    near_d = (row_mid - near) if side == "b" else (near - row_mid)
    far_d = (row_mid - far) if side == "b" else (far - row_mid)
    if far_d >= COARSE_TO:
        return float("nan")      # the band leaves the cache: refuse rather than under-count
    if far_d < 0:
        # The band lies entirely on the far side of the current mid. Bids never rest above the
        # mid, so the honest answer is a true zero - total depletion of that region - not NaN.
        return 0.0
    near_d = max(near_d, 0.0)    # a band straddling the mid is clipped at it, not refused

    centres = bin_centres()
    prefix = "b" if side == "b" else "a"
    total = 0.0
    for bucket, centre in enumerate(centres):
        if near_d <= centre <= far_d:
            total += _value(frame, f"{prefix}{bucket}", index)
    return total


def side_matrix(frame, side: str) -> np.ndarray:
    """The (n_rows x n_bins) ladder for one side, as a single array."""
    prefix = "b" if side == "b" else "a"
    width = len(ladder_edges()) - 1
    return np.column_stack([frame[f"{prefix}{i}"].to_numpy(dtype=np.float64)
                            for i in range(width)])


def depth_series(frame, anchors: np.ndarray, side: str, band_bps: float,
                 matrix: np.ndarray | None = None) -> np.ndarray:
    """`depth_in_band` for EVERY row at once, each with its own anchor.

    The scalar version costs ~104M Python operations per day at a 100 ms cadence, which does not
    finish. This is the same arithmetic as a masked matrix product, and the selftest asserts the
    two agree row by row - so the fast path cannot drift from the definition it implements."""
    row_mid = frame["mid"].to_numpy(dtype=np.float64)
    if matrix is None:
        matrix = side_matrix(frame, side)
    centres = bin_centres()[None, :]

    reach = anchors * band_bps / 10_000.0
    if side == "b":
        near_d = row_mid - anchors
        far_d = near_d + reach
    else:
        near_d = anchors - row_mid
        far_d = near_d + reach

    out = np.where(far_d < 0.0, 0.0, np.nan)          # wholly past the mid -> true zero
    usable = far_d >= 0.0
    inside = usable & (far_d < COARSE_TO)             # else stays NaN: refuse, never truncate
    if inside.any():
        low = np.maximum(near_d[inside], 0.0)[:, None]
        high = far_d[inside][:, None]
        mask = (centres >= low) & (centres <= high)
        out[inside] = (matrix[inside] * mask).sum(axis=1)
    return out


def _value(frame, column: str, index: int) -> float:
    series = frame[column]
    return float(series.iat[index] if hasattr(series, "iat") else series[index])


def selftest() -> int:
    import pandas as pd
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    edges = ladder_edges()
    check(len(edges) - 1 == 60, "the ladder has 60 bins per side: 40 fine, 20 coarse")
    check(edges[0] == 0.0 and edges[-1] == COARSE_TO,
          "it runs from the mid out to $140 of distance")
    check(COARSE_TO / 63_000 * 10_000 > 20,
          "$140 at this price level covers every band the protocol declares (max 20 bps)")
    check(FINE_TO / 63_000 * 10_000 > 5,
          "the FINE region alone covers the PRIMARY 5 bps band, so the primary cell never "
          "depends on a coarse bin")

    check(bin_index(0.5) == 0 and bin_index(39.5) == 39, "fine bins are $1 wide")
    check(bin_index(40.0) == 40 and bin_index(44.9) == 40, "coarse bins are $5 wide")
    check(bin_index(139.9) == 59, "the last coarse bin ends at $140")
    check(bin_index(140.0) is None and bin_index(-1.0) is None,
          "distances outside the window have no bin")

    mid = 63_000.0
    bids = {62_999.0: 2.0, 62_998.0: 3.0, 62_800.0: 99.0}   # the third is outside the window
    binned = bin_side(bids, mid, True)
    check(abs(binned.sum() - 5.0) < 1e-6,
          "size beyond the cached window is DROPPED, not folded into the edge bin")
    check(abs(binned[1] - 2.0) < 1e-6, "a bid $1 below mid lands in the $1-$2 distance bin")
    asks = {63_001.0: 2.0}
    check(abs(bin_side(asks, mid, False)[1] - 2.0) < 1e-6,
          "an ask $1 ABOVE mid lands in the same distance bin of the ask ladder")

    width = len(edges) - 1
    row = {"ts_ms": 0, "mid": mid, "bid": 62_999.0, "ask": 63_001.0,
           "bid_size": 2.0, "ask_size": 1.0, "n_bid_levels": 2, "n_ask_levels": 1}
    for index in range(width):
        row[f"b{index}"] = 0.0
        row[f"a{index}"] = 0.0
    row["b1"] = 2.0        # $1-$2 below mid
    row["b2"] = 3.0        # $2-$3 below mid
    frame = pd.DataFrame([row])

    check(abs(depth_in_band(frame, 0, mid, "b", 5.0) - 5.0) < 1e-6,
          "a 5 bps band ($31.5) at the anchor collects both bid levels")
    check(depth_in_band(frame, 0, mid, "b", 0.1) == 0.0,
          "a band tighter than the nearest level ($0.63) collects nothing")
    check(depth_in_band(frame, 0, mid, "a", 5.0) == 0.0,
          "the ask band does not pick up bid-side size")

    # THE ANCHOR PROPERTY. Re-anchor $20 BELOW: the levels sit ABOVE that anchor, so a bid band
    # running downward from it collects nothing - even though the row is unchanged. A
    # mid-relative metric cannot express this, and it is what separates "liquidity withdrawn"
    # from "price moved away".
    check(depth_in_band(frame, 0, mid - 20.0, "b", 5.0) == 0.0,
          "the same row reports NO depth when the band is anchored $20 lower - the cache "
          "answers 'is the liquidity still HERE', not 'is there liquidity near price'")

    # FAIL-CLOSED. A band that leaves the cached window must refuse, not silently truncate:
    # a short count understates depth and would read as a vacuum, manufacturing the event.
    far = depth_in_band(frame, 0, mid - 130.0, "b", 5.0)
    check(far != far, "a band beyond the cached window returns NaN rather than a short count")

    # An anchor ABOVE the mid is the normal case after a price drop: the band straddles the
    # mid, and the part above it holds no bids by construction. Clip at the mid, do not refuse.
    straddle = depth_in_band(frame, 0, mid + 2.0, "b", 5.0)
    check(straddle == straddle and abs(straddle - 5.0) < 1e-6,
          "a band anchored $2 ABOVE mid still collects the bids below it - straddling the mid "
          "is clipped, not refused, or every post-drop measurement would vanish")
    check(depth_in_band(frame, 0, mid + 100.0, "b", 5.0) == 0.0,
          "a bid band entirely above the mid is a true ZERO - that region is genuinely empty "
          "of bids - and reporting NaN would hide total depletion")

    # THE FAST PATH MUST EQUAL THE DEFINITION. depth_series is what every study actually calls;
    # depth_in_band is what the protocol describes. Drift between them would be invisible.
    rng = np.random.default_rng(3)
    many = []
    for _ in range(60):
        r = dict(row)
        r["mid"] = mid + float(rng.normal(0, 15))
        for bucket in rng.integers(0, width, 5):
            r[f"b{bucket}"] = float(rng.gamma(2.0, 3.0))
            r[f"a{bucket}"] = float(rng.gamma(2.0, 3.0))
        many.append(r)
    bulk = pd.DataFrame(many)
    anchors = bulk["mid"].to_numpy() + rng.normal(0, 4, len(bulk))
    for test_side in ("b", "a"):
        fast = depth_series(bulk, anchors, test_side, 5.0)
        slow = np.array([depth_in_band(bulk, i, anchors[i], test_side, 5.0)
                         for i in range(len(bulk))])
        agree = np.allclose(fast, slow, equal_nan=True)
        check(agree, f"the vectorised depth_series equals the scalar depth_in_band on {len(bulk)}"
                     f" random {test_side}-side rows, NaNs included")
    check(np.isfinite(depth_series(bulk, anchors, "b", 5.0)).any(),
          "and it returns real numbers, not an array of NaN that would agree trivially")

    # The agreement rows all sit within a few dollars of the mid, so they never reach the
    # out-of-cache path. Exercised explicitly here: a mutation that made depth_series TRUNCATE
    # instead of refusing survived every other check in this file.
    far_anchor = bulk["mid"].to_numpy() - (COARSE_TO + 10.0)
    check(np.isnan(depth_series(bulk, far_anchor, "b", 5.0)).all(),
          "depth_series REFUSES (NaN) when the band leaves the cached window - the fast path "
          "must fail closed exactly as the scalar one does, not silently under-count")
    check(np.isfinite(depth_series(bulk, bulk["mid"].to_numpy(), "b", 5.0)).all(),
          "...while an in-window band still returns real depth, so refusal is selective")

    check(CADENCE_MS == 100 and 86_400_000 // CADENCE_MS == 864_000,
          "a 100 ms cadence yields 864,000 sample slots per day")
    print(f"\nBYBIT L2 GRID SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--day")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not args.build:
        parser.error("nothing to do: pass --selftest or --build")

    days = sorted(L2_DIR.glob(f"*_{SYMBOL}_ob200.data.zip"))
    if args.day:
        days = [d for d in days if d.name.startswith(args.day)]
    if not days:
        print(f"no archive files under {L2_DIR}")
        return 1
    print(f"building {CADENCE_MS}ms grid for {len(days)} day(s) -> {CACHE}")
    for path in days:
        out = CACHE / f"{path.name.split('_')[0]}.parquet"
        summary = build_day(path, out, limit=args.limit)
        flag = f"  INVALID: {summary['invalid']}" if summary["invalid"] else ""
        print(f"  {summary['day']}  {summary['records']:>9,} records -> "
              f"{summary['samples']:>8,} samples{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
