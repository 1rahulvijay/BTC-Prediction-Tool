"""Timestamp-aware windows. Observation COUNT is not elapsed time.

THE DEFECT
    The adapter appends a sample when enough wall-clock time has passed, and stores the
    timestamps. Every strategy then reads `mid_history` positionally and nothing reads
    `sample_ts_history`. So after a feed interruption a history like

        10:00:01  10:00:02  10:00:03  10:04:51  10:04:52

    is consumed as five adjacent "one-second samples". A 4m48s hole is invisible, and it
    corrupts EMA periods, momentum lookbacks, breakout windows, volatility estimates, z-scores,
    maximum-holding assumptions and any sqrt(time) scaling - because all of them use index
    distance as a proxy for elapsed time.

    The same mistake sized `agg_trade_count_60s`: it was emitted whenever TWO samples fell
    inside the last minute, with no requirement that they SPAN it. Two samples a second apart
    at startup produced a "60-second" trade count, which mean reversion then compared against
    an absolute threshold of 1,700 and breakout against a minimum of 20.

    python -m backend.binance_paper.sample_window --selftest
"""
from __future__ import annotations

import argparse
import statistics
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

#: A one-second sampler should never be quiet longer than this inside a usable window.
DEFAULT_MAX_GAP_MS = 2_000
#: Fraction of the requested window that must actually be covered by samples.
DEFAULT_MIN_COVERAGE = 0.95


def window(values, timestamps, now_ms: int, seconds: float) -> dict:
    """Samples inside the last `seconds`, with the coverage facts needed to trust them."""
    span_ms = float(seconds) * 1000.0
    cutoff = float(now_ms) - span_ms
    pairs = [(int(t), float(v)) for t, v in zip(timestamps or (), values or ())
             if t is not None and float(t) >= cutoff]
    pairs.sort()
    times = [t for t, _ in pairs]
    picked = [v for _, v in pairs]

    if len(picked) < 2:
        return {"values": tuple(picked), "timestamps": tuple(times), "count": len(picked),
                "elapsed_ms": 0, "max_gap_ms": None, "median_gap_ms": None,
                "coverage_ratio": 0.0, "complete": False,
                "reason": "fewer_than_two_samples"}

    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    elapsed = times[-1] - times[0]
    # Coverage is measured against the REQUESTED span, not against the samples that happen to
    # exist - otherwise two samples a second apart score 100% coverage of a 60s window.
    coverage = min(1.0, elapsed / span_ms) if span_ms > 0 else 0.0
    max_gap = max(gaps)
    return {
        "values": tuple(picked), "timestamps": tuple(times), "count": len(picked),
        "elapsed_ms": elapsed, "max_gap_ms": max_gap,
        "median_gap_ms": statistics.median(gaps),
        "coverage_ratio": coverage, "complete": True, "reason": None,
    }


def usable(win: dict, max_gap_ms: int = DEFAULT_MAX_GAP_MS,
           min_coverage: float = DEFAULT_MIN_COVERAGE) -> tuple[bool, str | None]:
    """Fail CLOSED: an unmeasurable window is not a usable one."""
    if not win.get("complete"):
        return False, win.get("reason") or "incomplete_window"
    if win["coverage_ratio"] < min_coverage:
        return False, (f"coverage {win['coverage_ratio']:.0%} < {min_coverage:.0%} "
                       f"({win['elapsed_ms']}ms of span)")
    if win["max_gap_ms"] is not None and win["max_gap_ms"] > max_gap_ms:
        return False, f"max gap {win['max_gap_ms']}ms > {max_gap_ms}ms"
    return True, None


def continuity(values, timestamps, max_gap_ms: int = DEFAULT_MAX_GAP_MS,
               minimum_samples: int = 2) -> dict:
    """Is the RETAINED history free of holes? The property strategies actually depend on.

    Coverage of a fixed 60s window is the wrong test for the availability flag: a strategy whose
    lookback is 30 samples is perfectly served by 35 contiguous seconds, and refusing it would
    block good decisions. What breaks those strategies is a HOLE - index distance stops meaning
    elapsed time - and a hole is visible as a gap regardless of how long the buffer is.

    Evaluated over every retained sample, not a trimmed window, so an outage that has already
    scrolled out of the last minute is still seen."""
    pairs = sorted((int(t), float(v)) for t, v in zip(timestamps or (), values or ())
                   if t is not None)
    if len(pairs) < max(2, minimum_samples):
        return {"continuous": False, "count": len(pairs), "max_gap_ms": None,
                "reason": f"only {len(pairs)} samples, need {max(2, minimum_samples)}"}
    times = [t for t, _ in pairs]
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    worst = max(gaps)
    if worst > max_gap_ms:
        return {"continuous": False, "count": len(pairs), "max_gap_ms": worst,
                "reason": f"gap of {worst}ms exceeds {max_gap_ms}ms - index distance no "
                          f"longer means elapsed time"}
    return {"continuous": True, "count": len(pairs), "max_gap_ms": worst, "reason": None}


def contiguous_tail(values, timestamps, max_gap_ms: int = DEFAULT_MAX_GAP_MS):
    """The samples since the most recent hole. This is the history that is actually usable.

    Publishing this instead of the whole buffer is what makes the guard EFFECTIVE. Strategies
    do not consult availability flags - they check `len(history) < slow_period` - so handing
    them a truncated history is what turns an outage into a refusal, through the length check
    they already perform. No strategy code has to change, and none can forget to look."""
    pairs = sorted((int(t), float(v)) for t, v in zip(timestamps or (), values or ())
                   if t is not None)
    if not pairs:
        return (), ()
    start = 0
    for i in range(1, len(pairs)):
        if pairs[i][0] - pairs[i - 1][0] > max_gap_ms:
            start = i          # everything before this hole is not adjacent to what follows
    kept = pairs[start:]
    return tuple(v for _, v in kept), tuple(t for t, _ in kept)


def trade_count_window(samples, now_ms: int, seconds: float = 60.0,
                       min_coverage: float = DEFAULT_MIN_COVERAGE) -> dict:
    """`agg_trade_count_60s` with the span it claims.

    `samples` are (ts_ms, mid, cumulative_trade_count). Returns the raw count plus the coverage
    that produced it, and refuses to name it a 60-second figure when it is not one."""
    span_ms = float(seconds) * 1000.0
    eligible = sorted((int(s[0]), int(s[2])) for s in samples
                      if s[0] is not None and float(s[0]) >= now_ms - span_ms)
    if len(eligible) < 2:
        return {"count": None, "coverage_seconds": 0.0, "coverage_ratio": 0.0,
                "trades_per_second": None, "window_complete": False,
                "reason": "fewer_than_two_samples"}
    elapsed_ms = eligible[-1][0] - eligible[0][0]
    coverage = min(1.0, elapsed_ms / span_ms) if span_ms > 0 else 0.0
    raw = max(0, eligible[-1][1] - eligible[0][1])
    complete = coverage >= min_coverage
    return {
        # The count is NULL unless the window it names actually elapsed. A partial count
        # compared against an absolute threshold reads as a quiet market during warm-up.
        "count": raw if complete else None,
        "raw_count": raw,
        "coverage_seconds": elapsed_ms / 1000.0,
        "coverage_ratio": coverage,
        # Always available and scale-free, so a caller can use partial evidence knowingly.
        "trades_per_second": (raw / (elapsed_ms / 1000.0)) if elapsed_ms > 0 else None,
        "window_complete": complete,
        "reason": None if complete else f"only {elapsed_ms / 1000.0:.1f}s of {seconds:.0f}s",
    }


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    now = 1_000_000
    # A clean one-second series covering the last minute.
    times = [now - 60_000 + i * 1000 for i in range(61)]
    values = [100.0 + i * 0.1 for i in range(61)]
    clean = window(values, times, now, 60)
    check(clean["count"] == 61, "a clean series keeps every sample in the window")
    check(clean["max_gap_ms"] == 1000, "its largest gap is one second")
    check(clean["coverage_ratio"] >= 0.99, "and it covers the requested span")
    ok, why = usable(clean)
    check(ok and why is None, "so it is usable")

    # THE OUTAGE, exactly as described: 3 seconds, a 4m48s hole, 2 more seconds.
    gap_times = [now - 291_000, now - 290_000, now - 289_000, now - 1_000, now]
    gap_values = [100.0, 100.1, 100.2, 105.0, 105.1]
    holed = window(gap_values, gap_times, now, 60)
    check(holed["count"] == 2,
          "only samples INSIDE the 60s window are returned - the pre-outage rows are excluded "
          "by time, not by position")
    ok, why = usable(holed)
    check(not ok and "coverage" in why,
          f"and the window is REFUSED: {why} - five rows would have looked like five seconds")

    # A hole INSIDE the window.
    inner_times = [now - 60_000, now - 59_000, now - 20_000, now - 1_000, now]
    inner = window([1.0, 2.0, 3.0, 4.0, 5.0], inner_times, now, 60)
    ok, why = usable(inner)
    check(not ok and "max gap" in why,
          f"a 39-second hole inside a well-covered window is caught by the gap rule: {why}")
    check(inner["coverage_ratio"] >= 0.95,
          "...and coverage ALONE would have passed it, which is why both rules exist")

    check(usable(window([], [], now, 60))[0] is False, "an empty history is not usable")

    # CONTINUITY is the property the availability flag uses, and it must NOT over-block.
    contiguous_ts = [now - 34_000 + i * 1000 for i in range(35)]
    cont = continuity([100.0] * 35, contiguous_ts)
    check(cont["continuous"],
          "35 CONTIGUOUS one-second samples are continuous - a strategy with a 30-sample "
          "lookback is well served, and demanding 60s of coverage would wrongly block it")
    outage = continuity(gap_values, gap_times)
    check(not outage["continuous"] and "gap of" in outage["reason"],
          f"while the outage history is REFUSED on its hole: {outage['reason']}")
    check(outage["max_gap_ms"] > 60_000,
          "the hole is seen even though it has already scrolled out of the last minute - "
          "continuity is evaluated over the retained buffer, not a trimmed window")
    check(not continuity([1.0], [now])["continuous"], "a single sample is not continuous")

    # TRUNCATION is what actually enforces the guard, because strategies check history LENGTH
    # rather than any availability flag.
    kept_v, kept_t = contiguous_tail(gap_values, gap_times)
    check(len(kept_v) == 2 and kept_t == (now - 1_000, now),
          "after an outage only the post-outage tail is published - a strategy needing 30 "
          "samples now sees 2 and refuses through the length check it already performs")
    whole_v, _ = contiguous_tail([100.0] * 35, contiguous_ts)
    check(len(whole_v) == 35,
          "a contiguous history is passed through WHOLE - the truncation is not a blanket cut")
    multi_t = [now - 500_000, now - 499_000, now - 300_000, now - 299_000, now - 298_000]
    kept_multi, _ = contiguous_tail([1.0, 2.0, 3.0, 4.0, 5.0], multi_t)
    check(len(kept_multi) == 3,
          "with several holes it keeps only the tail after the LAST one")
    check(contiguous_tail([], []) == ((), ()), "an empty history truncates to empty")
    check(usable(window([1.0], [now], now, 60))[0] is False, "one sample is not a window")

    # --- TRADE COUNT ---------------------------------------------------------------
    warm = [(now - 1_000, 100.0, 1_000), (now, 100.0, 1_050)]
    result = trade_count_window(warm, now, 60)
    check(result["raw_count"] == 50, "the raw difference is still computed")
    check(result["count"] is None and not result["window_complete"],
          "but a 1-second sample may NOT be published as a 60-second count - the startup case "
          "that made 50 trades look like a quiet minute")
    check(abs(result["coverage_seconds"] - 1.0) < 0.01, "coverage is reported in seconds")
    check(abs(result["trades_per_second"] - 50.0) < 0.01,
          "and a scale-free rate IS available, so partial evidence can be used knowingly")

    full = [(now - 60_000 + i * 1000, 100.0, 1_000 + i * 30) for i in range(61)]
    full_result = trade_count_window(full, now, 60)
    check(full_result["count"] == 1800 and full_result["window_complete"],
          "a genuinely 60-second window publishes its count")
    check(full_result["coverage_ratio"] >= 0.99, "with full coverage")

    check(trade_count_window([], now, 60)["count"] is None,
          "no samples yields no count, never zero - zero is a market claim")
    check(trade_count_window([(now, 1.0, 5)], now, 60)["count"] is None,
          "a single sample yields no count either")

    print(f"\nSAMPLE WINDOW SELFTEST: PASS ({checks} checks)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.parse_args()
    raise SystemExit(selftest())
