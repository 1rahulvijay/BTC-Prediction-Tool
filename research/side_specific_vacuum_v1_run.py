"""Scoring runner for SIDE_SPECIFIC_VACUUM_V1. Separated so the study module stays importable
without touching 530 MB of grid, and so `--selftest` never loads a day of data.

Scored once, per docs/active/PREREG_SIDE_SPECIFIC_VACUUM_V1.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from side_specific_vacuum_v1 import (  # noqa: E402
    BAND_BPS, CADENCE_MS, GRID_DIR, HORIZON_S, HORIZONS, MIN_EPISODES, PRIMARY_THRESHOLD,
    PROTOCOL, QUIET_DOLLARS, ROUND_TRIP_BPS, THRESHOLDS, WINDOW_S, asym, day_block_ci,
    excursions, find_events_ratio, load_day, null_floor, verdict_for)


def collect(band_bps=BAND_BPS, window_s=WINDOW_S, quiet=True, horizon_s=HORIZON_S):
    window_steps = int(window_s * 1000 / CADENCE_MS)
    horizon_steps = int(horizon_s * 1000 / CADENCE_MS)
    days = []
    for path in sorted(GRID_DIR.glob("*.parquet")):
        day = load_day(path, band_bps, window_steps)
        events = {}
        for side, name in (("b", "bid"), ("a", "ask")):
            events[side] = find_events_ratio(day["mid"], day[f"depth_{name}"],
                                             day[f"past_{name}"], horizon_steps, quiet,
                                             window_steps)
        bd, bu = excursions(day["mid"], events["b"], horizon_steps)
        ad, au = excursions(day["mid"], events["a"], horizon_steps)
        # CLIPPING DIAGNOSTIC. A band anchored above the current mid is clipped at the mid, so a
        # price FALL mechanically shrinks measured bid depth even with no order withdrawn. The
        # $5 quiet cap bounds that to ~16% of a $31.5 band - not enough to fake a 50% collapse
        # alone, but if admitted BID events skew toward falling price the effect is contributing
        # and the reader must be told.
        mid = day["mid"]
        drift = {}
        for side in ("b", "a"):
            idx = events[side]
            drift[side] = (mid[idx] - mid[idx - window_steps]) if len(idx) else np.zeros(0)
        # Depth actually lost at each event, for the cancellation-vs-consumption attribution.
        lost = {}
        for side, name in (("b", "bid"), ("a", "ask")):
            idx = events[side]
            lost[name] = ((day[f"past_{name}"][idx] - day[f"depth_{name}"][idx])
                          if len(idx) else np.zeros(0))
        days.append({"day": day["day"], "mid": mid, "events": events,
                     "ts_ms": day["ts_ms"],
                     "bid_down": bd, "bid_up": bu, "ask_down": ad, "ask_up": au,
                     "bid_drift": drift["b"], "ask_drift": drift["a"],
                     "lost_bid": lost["bid"], "lost_ask": lost["ask"],
                     "rows": day["rows"], "refusals": day["refusals"]})
    return days, window_steps, horizon_steps


def pooled(days, key):
    return np.concatenate([d[key] for d in days]) if days else np.zeros(0)


#: THE PRIMARY CELL AS SCORED, 2026-08-04. "Scored once" is enforced, not promised: a rerun
#: that moves these fails loudly rather than quietly producing a second, better-looking result.
FIRST_SCORING = {"asym": 0.0049, "ci": (-0.0082, 0.0155), "null_mean": -0.0001,
                 "verdict": "SIDE_SPECIFIC_BUT_SUB_COST"}
SCORING_TOLERANCE = 0.002


def check_first_scoring(point, ci, verdict, null_mean) -> None:
    drift = abs(point - FIRST_SCORING["asym"])
    state = "matches" if drift <= SCORING_TOLERANCE else "DRIFTED"
    print(f"\n  scored-once check: ASYM {point:+.4f} vs recorded "
          f"{FIRST_SCORING['asym']:+.4f} - {state}")
    if drift > SCORING_TOLERANCE or verdict != FIRST_SCORING["verdict"]:
        raise SystemExit(
            f"this run does not reproduce the single scoring of record (ASYM {point:+.4f} vs "
            f"{FIRST_SCORING['asym']:+.4f}, verdict {verdict} vs {FIRST_SCORING['verdict']}). "
            f"Something upstream changed; the protocol was scored once and may not be "
            f"re-scored to a new number.")


def traded_volume_in_band(day: str, event_ts_ms, window_s, band_bps, mid_at_event,
                          side: str) -> np.ndarray:
    """Aggressive volume that traded INSIDE the band during each event's lookback window.

    Separates a vacuum that was CONSUMED by trades from one that was CANCELLED. Both empty the
    book; only the second is a statement about intent rather than about flow that has already
    printed."""
    import gzip
    import csv
    path = ROOT / "data" / "bybit_trades" / f"BTCUSDT{day}.csv.gz"
    if not path.is_file() or len(event_ts_ms) == 0:
        return np.full(len(event_ts_ms), np.nan)

    times, prices, sizes = [], [], []
    with gzip.open(path, "rt") as handle:
        for row in csv.DictReader(handle):
            times.append(float(row["timestamp"]) * 1000.0)
            prices.append(float(row["price"]))
            sizes.append(float(row["size"]))
    times = np.asarray(times)
    prices = np.asarray(prices)
    sizes = np.asarray(sizes)
    order = np.argsort(times)
    times, prices, sizes = times[order], prices[order], sizes[order]

    out = np.zeros(len(event_ts_ms))
    for i, (ts, anchor) in enumerate(zip(event_ts_ms, mid_at_event)):
        lo_t, hi_t = ts - window_s * 1000.0, ts
        reach = anchor * band_bps / 10_000.0
        lo_p, hi_p = ((anchor - reach, anchor) if side == "b" else (anchor, anchor + reach))
        left, right = np.searchsorted(times, [lo_t, hi_t])
        window_prices = prices[left:right]
        inside = (window_prices >= lo_p) & (window_prices <= hi_p)
        out[i] = sizes[left:right][inside].sum()
    return out


def secondaries(days, price) -> None:
    """The declared secondary endpoints. No verdict attaches to any of them."""
    from side_specific_vacuum_v1 import BAND_BPS as _band

    print("\n" + "-" * 100)
    print("  SECONDARY 1 - VACUUM_ANY, quantifying the reverse-causality contamination")
    any_days, _ws, hs = collect(quiet=False)
    for threshold in THRESHOLDS:
        quiet_value = asym(pooled(days, "bid_down"), pooled(days, "bid_up"),
                           pooled(days, "ask_down"), pooled(days, "ask_up"), threshold)
        any_value = asym(pooled(any_days, "bid_down"), pooled(any_days, "bid_up"),
                         pooled(any_days, "ask_down"), pooled(any_days, "ask_up"), threshold)
        print(f"    ${threshold:>6.0f}   QUIET {quiet_value:+.4f}   ANY {any_value:+.4f}   "
              f"inflation {any_value - quiet_value:+.4f}")
    print(f"    episodes  QUIET bid {len(pooled(days, 'bid_down')):,} / ask "
          f"{len(pooled(days, 'ask_down')):,}      "
          f"ANY bid {len(pooled(any_days, 'bid_down')):,} / ask "
          f"{len(pooled(any_days, 'ask_down')):,}")

    print("\n  SECONDARY 2 - unsigned hazard, comparable to the prior study")
    all_down = np.concatenate([pooled(days, "bid_down"), pooled(days, "ask_down")])
    all_up = np.concatenate([pooled(days, "bid_up"), pooled(days, "ask_up")])
    worst = np.maximum(all_down, all_up)

    # THE BASELINE MUST BE THE SAME STATISTIC. A first version compared max EXCURSION after a
    # vacuum against a close-to-close move unconditionally. Max excursion is >= |close-close| by
    # construction, so most of that "lift" was the comparison, not the market. The baseline is
    # therefore random timestamps scored with the identical excursion function.
    horizon_steps = int(HORIZON_S * 1000 / CADENCE_MS)
    rng = np.random.default_rng(17)
    base_parts = []
    for d in days:
        n = len(d["mid"])
        picks = rng.integers(horizon_steps, n - horizon_steps - 1, 5_000)
        bdown, bup = excursions(d["mid"], picks, horizon_steps)
        base_parts.append(np.maximum(bdown, bup))
    base_worst = np.concatenate(base_parts)
    print(f"    baseline: {len(base_worst):,} random timestamps, scored with the SAME "
          f"max-excursion statistic")
    for threshold in THRESHOLDS:
        after = (worst >= threshold).mean()
        base = (base_worst >= threshold).mean()
        lift = after / base if base > 0 else float("inf")
        print(f"    P(|move| >= ${threshold:>6.0f} in {HORIZON_S}s)   after a vacuum "
              f"{after:.4f}   unconditional {base:.4f}   lift {lift:.1f}x")

    print("\n  SECONDARY 3 - cancellation attribution (was the depth CANCELLED or CONSUMED?)")
    cancelled = total = 0
    for day in days:
        for side, name in (("b", "bid"), ("a", "ask")):
            idx = day["events"][side]
            if len(idx) == 0:
                continue
            ts = day["ts_ms"][idx]
            anchor = day["mid"][idx - int(WINDOW_S * 1000 / CADENCE_MS)]
            volume = traded_volume_in_band(day["day"], ts, WINDOW_S, _band, anchor, side)
            lost = day[f"lost_{name}"]
            usable = np.isfinite(volume) & np.isfinite(lost) & (lost > 0)
            cancelled += int((volume[usable] < 0.20 * lost[usable]).sum())
            total += int(usable.sum())
    if total:
        print(f"    {cancelled:,} of {total:,} episodes ({cancelled / total:.1%}) had traded "
              f"volume < 20% of the depth lost")
        print("    -> the liquidity was WITHDRAWN, not bought or sold through")
    else:
        print("    no episodes could be attributed (trade archive missing or unaligned)")

    print("\n  SECONDARY 4 - band and window robustness")
    for band in (1.0, 2.0, 5.0, 10.0, 20.0):
        alt, _w, _h = collect(band_bps=band)
        value = asym(pooled(alt, "bid_down"), pooled(alt, "bid_up"),
                     pooled(alt, "ask_down"), pooled(alt, "ask_up"), PRIMARY_THRESHOLD)
        small = asym(pooled(alt, "bid_down"), pooled(alt, "bid_up"),
                     pooled(alt, "ask_down"), pooled(alt, "ask_up"), 10.0)
        print(f"    band {band:>4.0f} bps   ASYM@$70 {value:+.4f}   ASYM@$10 {small:+.4f}   "
              f"episodes {len(pooled(alt, 'bid_down')):,}/{len(pooled(alt, 'ask_down')):,}")
    for window in (1, 5, 15):
        alt, _w, _h = collect(window_s=window)
        value = asym(pooled(alt, "bid_down"), pooled(alt, "bid_up"),
                     pooled(alt, "ask_down"), pooled(alt, "ask_up"), PRIMARY_THRESHOLD)
        small = asym(pooled(alt, "bid_down"), pooled(alt, "bid_up"),
                     pooled(alt, "ask_down"), pooled(alt, "ask_up"), 10.0)
        print(f"    window {window:>3}s    ASYM@$70 {value:+.4f}   ASYM@$10 {small:+.4f}   "
              f"episodes {len(pooled(alt, 'bid_down')):,}/{len(pooled(alt, 'ask_down')):,}")


def run() -> int:
    if not list(GRID_DIR.glob("*.parquet")):
        print(f"no grid under {GRID_DIR} - run: python -m research.bybit_l2.grid --build")
        return 1

    print("=" * 100)
    print(f"SIDE-SPECIFIC VACUUM V1 - protocol {PROTOCOL} (frozen before any event was counted)")
    print("=" * 100)

    days, window_steps, horizon_steps = collect()
    price = float(np.median(np.concatenate([d["mid"][::5000] for d in days])))
    cost_move = price * ROUND_TRIP_BPS / 10_000.0
    total_rows = sum(d["rows"] for d in days)
    print(f"  {len(days)} days   {total_rows:,} grid rows at {CADENCE_MS}ms   "
          f"median mid ${price:,.0f}")
    print(f"  band {BAND_BPS:.0f}bps   window {WINDOW_S}s   horizon {HORIZON_S}s   "
          f"VACUUM_QUIET (|dmid| <= ${QUIET_DOLLARS:.0f})")
    print(f"  declared cost {ROUND_TRIP_BPS:.0f} bps round trip = ${cost_move:,.0f}   "
          f"PRIMARY threshold ${PRIMARY_THRESHOLD:.0f}")
    refusals = sum(d["refusals"] for d in days)
    print(f"  band-outside-cache refusals: {refusals:,} of {total_rows * 2:,} depth queries "
          f"({refusals / max(total_rows * 2, 1):.3%})")

    print("\n  admitted episodes per day (non-overlapping, one per horizon):")
    for d in days:
        print(f"    {d['day']}   BID {len(d['bid_down']):>6,}   ASK {len(d['ask_down']):>6,}")
    n_bid, n_ask = len(pooled(days, "bid_down")), len(pooled(days, "ask_down"))
    print(f"    {'TOTAL':<10}  BID {n_bid:>6,}   ASK {n_ask:>6,}")

    bd, bu = pooled(days, "bid_down"), pooled(days, "bid_up")
    ad, au = pooled(days, "ask_down"), pooled(days, "ask_up")

    b_drift, a_drift = pooled(days, "bid_drift"), pooled(days, "ask_drift")
    print("\n  CLIPPING DIAGNOSTIC - signed price drift over the lookback window, at admitted")
    print("  events. A band anchored above the current mid is clipped AT the mid, so a falling")
    print("  price shrinks measured bid depth with no order withdrawn.")
    if len(b_drift) and len(a_drift):
        print(f"    BID events: mean dmid {b_drift.mean():+.2f}   median {np.median(b_drift):+.2f}"
              f"   share falling {(b_drift < 0).mean():.1%}")
        print(f"    ASK events: mean dmid {a_drift.mean():+.2f}   median {np.median(a_drift):+.2f}"
              f"   share rising  {(a_drift > 0).mean():.1%}")
        worst = max(abs(b_drift).max(), abs(a_drift).max())
        print(f"    max |dmid| among admitted events ${worst:.2f} (cap ${QUIET_DOLLARS:.0f}); a "
              f"${worst:.2f} clip is at most {worst / (price * BAND_BPS / 10_000) * 100:.0f}% of "
              f"the band,\n    against the 50% collapse rule")

    print(f"\n  PRIMARY CELL   threshold ${PRIMARY_THRESHOLD:.0f}, horizon {HORIZON_S}s")
    p_bid_down = (bd >= PRIMARY_THRESHOLD).mean() if n_bid else float("nan")
    p_ask_down = (ad >= PRIMARY_THRESHOLD).mean() if n_ask else float("nan")
    p_ask_up = (au >= PRIMARY_THRESHOLD).mean() if n_ask else float("nan")
    p_bid_up = (bu >= PRIMARY_THRESHOLD).mean() if n_bid else float("nan")
    print(f"    P(down >= ${PRIMARY_THRESHOLD:.0f} | BID vacuum) {p_bid_down:.4f}   "
          f"| ASK vacuum {p_ask_down:.4f}   gap {p_bid_down - p_ask_down:+.4f}")
    print(f"    P(up   >= ${PRIMARY_THRESHOLD:.0f} | ASK vacuum) {p_ask_up:.4f}   "
          f"| BID vacuum {p_bid_up:.4f}   gap {p_ask_up - p_bid_up:+.4f}")
    point = asym(bd, bu, ad, au, PRIMARY_THRESHOLD)
    ci = day_block_ci(days, PRIMARY_THRESHOLD)
    print(f"    ASYM {point:+.4f}   day-block 95% CI [{ci[0]:+.4f}, {ci[1]:+.4f}]  "
          f"({len(days)} blocks - weak by construction, as declared)")

    null_mean, null_lo, null_hi = null_floor(days, PRIMARY_THRESHOLD, window_steps,
                                             horizon_steps)
    print(f"    null floor (price path circularly shifted): mean {null_mean:+.4f}   "
          f"95% band [{null_lo:+.4f}, {null_hi:+.4f}]")

    print("\n  SECONDARY grid - ASYM by threshold x horizon (no verdict attaches)")
    print(f"    {'threshold':>10}" + "".join(f"{h:>10}s" for h in HORIZONS))
    sub_cost_hits = []
    for threshold in THRESHOLDS:
        cells = []
        for horizon in HORIZONS:
            hs = int(horizon * 1000 / CADENCE_MS)
            parts = []
            for d in days:
                b_d, b_u = excursions(d["mid"], d["events"]["b"], hs)
                a_d, a_u = excursions(d["mid"], d["events"]["a"], hs)
                parts.append({"bid_down": b_d, "bid_up": b_u,
                              "ask_down": a_d, "ask_up": a_u})
            value = asym(pooled(parts, "bid_down"), pooled(parts, "bid_up"),
                         pooled(parts, "ask_down"), pooled(parts, "ask_up"), threshold)
            cells.append(value)
            if horizon == HORIZON_S and threshold < PRIMARY_THRESHOLD:
                lo, _hi = day_block_ci(parts, threshold, iterations=800)
                if np.isfinite(lo) and lo > 0:
                    sub_cost_hits.append(f"${threshold:.0f}")
        bps = threshold / price * 10_000
        flag = "  <- below cost" if threshold < cost_move else "  <- clears cost"
        print(f"    ${threshold:>8.0f}" + "".join(f"{c:>+11.4f}" for c in cells)
              + f"   ({bps:.1f} bps){flag}")

    episodes_ok = n_bid >= MIN_EPISODES and n_ask >= MIN_EPISODES
    verdict, reason = verdict_for(ci, null_mean, ", ".join(sub_cost_hits) or None, episodes_ok)
    print(f"\n  VERDICT: {verdict}")
    print(f"  {reason}")

    check_first_scoring(point, ci, verdict, null_mean)
    secondaries(days, price)

    print("\n  A $10 move is "
          f"{10 / price * 10_000:.1f} bps against a {ROUND_TRIP_BPS:.0f} bps round trip. Any "
          "result at that\n  threshold is a statement about the market, not about a trade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
