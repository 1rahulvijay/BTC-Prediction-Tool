"""Does depth withdrawn from ONE side predict a move in THAT side's direction, big enough to pay?

PROTOCOL
    docs/active/PREREG_SIDE_SPECIFIC_VACUUM_V1.md, sha256 2df7042f..., frozen before any event
    was counted. Scored once.

WHAT THE PRIOR STUDY COULD NOT ANSWER
    The earlier vacuum test used min(bid_depth, ask_depth), which discards side, and reported a
    $10-in-30s move rising 27.70% -> 37.84%. Two problems this protocol was written around:

    COST.  BTC trades near $63,000 here and Bybit taker is 5.5 bps a side, so a round trip costs
    11 bps ~= $70. A $10 move is 1.6 bps - roughly seven times too small to pay for itself. The
    primary cell is therefore declared at $70, not at $10.

    REVERSE CAUSALITY.  Depth in a band collapses partly BECAUSE price moved through it.
    Measuring the vacuum after the move and then "predicting" continuation is circular, and is
    the most likely explanation of a naive positive. VACUUM_QUIET requires |dmid| <= $5 across
    the lookback window, so the liquidity is gone while price has not yet moved.

THE ESTIMATOR
    ASYM compares BID_VACUUM against ASK_VACUUM, not against an unconditional baseline. Both
    arms are vacuum states, so shared time-of-day, volatility and regime confounds cancel - and
    so does the upward bias of max-excursion, since both sides are computed identically. ASYM=0
    is the null "vacuums mark volatility but carry no direction", which is exactly what the
    unsigned prior result could not rule out.

    python research/side_specific_vacuum_v1.py --selftest
    python research/side_specific_vacuum_v1.py
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

PROTOCOL = "PREREG_SIDE_SPECIFIC_VACUUM_V1.md"
GRID_DIR = ROOT / "data" / "bybit_l2_grid"

CADENCE_MS = 100
BAND_BPS = 5.0                 # primary
WINDOW_S = 5                   # primary lookback
HORIZON_S = 30                 # primary burst horizon
COLLAPSE = 0.50                # "halved"
QUIET_DOLLARS = 5.0            # VACUUM_QUIET price-move cap

#: Declared cost model. Bybit linear taker 5.5 bps per side => 11 bps round trip. At ~$63,000
#: that is ~$70, which is the PRIMARY threshold. This may not be revised downward.
TAKER_BPS_PER_SIDE = 5.5
ROUND_TRIP_BPS = 2 * TAKER_BPS_PER_SIDE

THRESHOLDS = (10.0, 25.0, 50.0, 70.0, 100.0)
HORIZONS = (5, 15, 30)
PRIMARY_THRESHOLD = 70.0
MIN_EPISODES = 200
NULL_DRAWS = 200
BOOTSTRAP = 2000


def cost_clearing_dollars(price: float) -> float:
    return price * ROUND_TRIP_BPS / 10_000.0


def find_events(mid: np.ndarray, depth_bid: np.ndarray, depth_ask: np.ndarray,
                window_steps: int, horizon_steps: int, quiet: bool):
    """Indices of admitted BID and ASK vacuum events.

    An event at t requires depth_side(t) <= 0.50 * depth_side(t-w). Under `quiet` it also
    requires |mid(t) - mid(t-w)| <= $5, so the depth is gone before the move rather than
    because of it.

    Refractory: per side, no admitted event within the preceding HORIZON, so test episodes
    never overlap and each contributes one independent observation."""
    n = len(mid)
    out = {"b": [], "a": []}
    last = {"b": -(10 ** 9), "a": -(10 ** 9)}
    for side, depth in (("b", depth_bid), ("a", depth_ask)):
        for t in range(window_steps, n - horizon_steps):
            past = depth[t - window_steps]
            if not np.isfinite(past) or past <= 0 or not np.isfinite(depth[t]):
                continue
            if depth[t] > COLLAPSE * past:
                continue
            if quiet and abs(mid[t] - mid[t - window_steps]) > QUIET_DOLLARS:
                continue
            if t - last[side] < horizon_steps:
                continue
            last[side] = t
            out[side].append(t)
    return {k: np.array(v, dtype=int) for k, v in out.items()}


def excursions(mid: np.ndarray, events: np.ndarray, horizon_steps: int):
    """(down, up) maximum excursion in dollars over (t, t+H] for each event."""
    if len(events) == 0:
        return np.zeros(0), np.zeros(0)
    down = np.empty(len(events))
    up = np.empty(len(events))
    for i, t in enumerate(events):
        path = mid[t + 1: t + 1 + horizon_steps]
        down[i] = mid[t] - path.min()
        up[i] = path.max() - mid[t]
    return down, up


def asym(bid_down, bid_up, ask_down, ask_up, threshold: float) -> float:
    """The primary estimator. Zero under 'vacuums mark volatility but not direction'."""
    if len(bid_down) == 0 or len(ask_down) == 0:
        return float("nan")
    down_gap = (bid_down >= threshold).mean() - (ask_down >= threshold).mean()
    up_gap = (ask_up >= threshold).mean() - (bid_up >= threshold).mean()
    return 0.5 * (down_gap + up_gap)


def day_block_ci(per_day: list[dict], threshold: float, iterations: int = BOOTSTRAP,
                 seed: int = 909, level: float = 0.95) -> tuple:
    """Resample whole DAYS. With 6 days this is weak by construction and the protocol says so."""
    if len(per_day) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(iterations):
        pick = rng.integers(0, len(per_day), len(per_day))
        chosen = [per_day[j] for j in pick]
        value = asym(np.concatenate([c["bid_down"] for c in chosen]),
                     np.concatenate([c["bid_up"] for c in chosen]),
                     np.concatenate([c["ask_down"] for c in chosen]),
                     np.concatenate([c["ask_up"] for c in chosen]), threshold)
        if np.isfinite(value):
            draws.append(value)
    if len(draws) < 50:
        return (float("nan"), float("nan"))
    lo = (1 - level) / 2 * 100
    return (float(np.percentile(draws, lo)), float(np.percentile(draws, 100 - lo)))


def null_floor(days_raw: list[dict], threshold: float, window_steps: int, horizon_steps: int,
               draws: int = NULL_DRAWS, seed: int = 4242) -> tuple:
    """Circularly shift each day's PRICE PATH against its event times.

    Preserves the event count, the clustering and the volatility profile; destroys only the
    alignment. If the null floor is not centred near zero the estimator is biased and the
    study is void - which is a check on the measurement, not on the market."""
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(draws):
        parts = []
        for day in days_raw:
            mid = day["mid"]
            shift = int(rng.integers(horizon_steps + 1, max(len(mid) - horizon_steps - 1,
                                                            horizon_steps + 2)))
            rolled = np.roll(mid, shift)
            bd, bu = excursions(rolled, day["events"]["b"], horizon_steps)
            ad, au = excursions(rolled, day["events"]["a"], horizon_steps)
            parts.append({"bid_down": bd, "bid_up": bu, "ask_down": ad, "ask_up": au})
        value = asym(np.concatenate([p["bid_down"] for p in parts]),
                     np.concatenate([p["bid_up"] for p in parts]),
                     np.concatenate([p["ask_down"] for p in parts]),
                     np.concatenate([p["ask_up"] for p in parts]), threshold)
        if np.isfinite(value):
            values.append(value)
    if not values:
        return (float("nan"),) * 3
    return (float(np.mean(values)), float(np.percentile(values, 2.5)),
            float(np.percentile(values, 97.5)))


def verdict_for(primary_ci, null_mean, sub_cost_hits, episodes_ok) -> tuple[str, str]:
    if not episodes_ok:
        return ("VOID", f"fewer than {MIN_EPISODES} admitted episodes on a side")
    if not np.isfinite(null_mean) or abs(null_mean) > 0.02:
        return ("VOID", f"null floor is centred at {null_mean:+.4f}, not near zero - the "
                        f"estimator is biased and no market claim can be made")
    lo, hi = primary_ci
    if np.isfinite(lo) and lo > 0:
        return ("SIDE_SPECIFIC_AND_COST_CLEARING",
                f"ASYM CI [{lo:+.4f}, {hi:+.4f}] excludes 0 at the cost-clearing threshold")
    if sub_cost_hits:
        return ("SIDE_SPECIFIC_BUT_SUB_COST",
                f"direction is real at {sub_cost_hits} but not at ${PRIMARY_THRESHOLD:.0f} - "
                f"too small to pay the {ROUND_TRIP_BPS:.0f} bps round trip")
    return ("UNSIGNED_ONLY" if np.isfinite(lo) else "NO_EFFECT",
            f"ASYM CI [{lo:+.4f}, {hi:+.4f}] includes 0 at every threshold")


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    check(abs(cost_clearing_dollars(63_000) - 69.3) < 0.5,
          "the declared cost model puts the cost-clearing move at ~$70 on $63,000 BTC")
    check(PRIMARY_THRESHOLD >= cost_clearing_dollars(63_000) * 0.98,
          "the PRIMARY threshold is at or above cost-clearing - not the $10 the prior study used")
    check(10.0 / 63_000 * 10_000 < ROUND_TRIP_BPS / 3,
          "a $10 move is less than a third of the round-trip cost, which is why it is secondary")

    # --- event detection -------------------------------------------------------------
    n = 400
    flat = np.full(n, 63_000.0)
    depth = np.full(n, 10.0)
    depth[200:] = 4.0                       # halved at t=200
    events = find_events(flat, depth, np.full(n, 10.0), 50, 100, quiet=True)
    check(len(events["b"]) >= 1 and events["b"][0] == 200,
          "a bid-depth collapse is detected at the step it occurs")
    check(len(events["a"]) == 0, "an unchanged ask side produces no ask event")

    check(len(find_events(flat, np.full(n, 10.0), np.full(n, 10.0), 50, 100,
                          quiet=True)["b"]) == 0,
          "constant depth produces NO events - the detector can return empty")

    shallow = np.full(n, 10.0)
    shallow[200:] = 6.0                     # only 40% off, not halved
    check(len(find_events(flat, shallow, shallow, 50, 100, quiet=True)["b"]) == 0,
          "a 40% decline is not a vacuum - the 50% rule bites")

    # THE REVERSE-CAUSALITY GUARD. Same depth collapse, but price has already moved $50.
    moved = flat.copy()
    moved[175:] = 62_950.0          # INSIDE the [150, 200] lookback window, not before it
    # TRANSIENT collapse, so the ONLY candidate instants are ones whose window contains the
    # move. A permanently halved depth also makes t=225..249 candidates, and those windows sit
    # entirely after the drop - legitimately quiet, and they would mask the guard.
    transient = np.full(n, 10.0)
    transient[200:210] = 4.0
    loud = find_events(moved, transient, np.full(n, 10.0), 50, 100, quiet=False)
    quiet = find_events(moved, transient, np.full(n, 10.0), 50, 100, quiet=True)
    check(len(loud["b"]) >= 1,
          "VACUUM_ANY admits a collapse that happened alongside a $50 price move")
    check(len(quiet["b"]) == 0,
          "VACUUM_QUIET REFUSES it - the move already happened, so predicting it is circular")

    # Refractory: overlapping episodes must not both be admitted.
    sawtooth = np.full(n, 10.0)
    for start in range(200, 400, 10):
        sawtooth[start:start + 5] = 1.0
    admitted = find_events(flat, sawtooth, np.full(n, 10.0), 50, 100, quiet=True)["b"]
    raw = [t for t in range(50, n - 100)
           if sawtooth[t] <= COLLAPSE * sawtooth[t - 50] and sawtooth[t - 50] > 0]
    check(len(raw) > len(admitted) > 0,
          f"the refractory rule THINNED {len(raw)} overlapping candidates to {len(admitted)} - "
          f"asserted as a strict reduction, so an empty result cannot pass this trivially")
    check((np.diff(admitted) >= 100).all(),
          "and every surviving pair is at least one horizon apart, so no two test episodes "
          "share a price path")

    # --- estimator -------------------------------------------------------------------
    rng = np.random.default_rng(0)
    m = 4000
    # Null world: both sides get the SAME excursion distribution.
    same = rng.gamma(2.0, 30.0, m)
    value = asym(same, rng.gamma(2.0, 30.0, m), rng.gamma(2.0, 30.0, m),
                 rng.gamma(2.0, 30.0, m), 70.0)
    check(abs(value) < 0.05,
          "ASYM is ~0 when both vacuum sides have identical excursions - the null it must detect")

    # Directional world: bid vacuums really do fall, ask vacuums really do rise.
    strong = asym(rng.gamma(4.0, 30.0, m), rng.gamma(2.0, 20.0, m),
                  rng.gamma(2.0, 20.0, m), rng.gamma(4.0, 30.0, m), 70.0)
    check(strong > 0.15, "ASYM is strongly positive when the burst follows the emptied side")

    flipped = asym(rng.gamma(2.0, 20.0, m), rng.gamma(4.0, 30.0, m),
                   rng.gamma(4.0, 30.0, m), rng.gamma(2.0, 20.0, m), 70.0)
    check(flipped < -0.15,
          "and NEGATIVE when it follows the opposite side - the estimator is signed, so a "
          "wrong-way effect cannot be reported as a win")

    # An UNSIGNED volatility effect must NOT register. Both sides get fat tails together.
    fat = asym(rng.gamma(4.0, 30.0, m), rng.gamma(4.0, 30.0, m),
               rng.gamma(4.0, 30.0, m), rng.gamma(4.0, 30.0, m), 70.0)
    check(abs(fat) < 0.05,
          "a vacuum that raises volatility on BOTH sides gives ASYM ~0 - the exact confound "
          "the prior unsigned study could not separate from direction")

    # --- excursions ------------------------------------------------------------------
    path = np.array([100.0, 101.0, 99.0, 103.0, 97.0, 100.0])
    down, up = excursions(path, np.array([0]), 5)
    check(down[0] == 3.0 and up[0] == 3.0,
          "excursion measures the extreme reached after the event, both ways")
    check(excursions(path, np.array([0]), 2)[1][0] == 1.0,
          "a shorter horizon sees only what happened inside it")

    # --- verdicts --------------------------------------------------------------------
    kind, _ = verdict_for((0.05, 0.15), 0.001, None, True)
    check(kind == "SIDE_SPECIFIC_AND_COST_CLEARING", "a clean positive at $70 passes")
    kind, _ = verdict_for((-0.02, 0.08), 0.001, "$25", True)
    check(kind == "SIDE_SPECIFIC_BUT_SUB_COST", "significant only below cost is reported as such")
    kind, _ = verdict_for((-0.02, 0.08), 0.001, None, True)
    check(kind == "UNSIGNED_ONLY", "a CI spanning zero everywhere is UNSIGNED_ONLY")
    kind, reason = verdict_for((0.05, 0.15), 0.09, None, True)
    check(kind == "VOID" and "biased" in reason,
          "a null floor far from zero VOIDS the study even when the CI looks significant")
    kind, _ = verdict_for((0.05, 0.15), 0.001, None, False)
    check(kind == "VOID", "too few episodes VOIDS it regardless of the estimate")

    print(f"\nSIDE-SPECIFIC VACUUM SELFTEST: PASS ({checks} checks)")
    return 0


def load_day(path: Path, band_bps: float, window_steps: int) -> dict:
    """Depth on each side measured at t, in a band ANCHORED AT THE MID ONE WINDOW BACK.

    Both the current and the reference depth use the SAME anchor price, so the ratio answers
    "is the liquidity that was in this price region still here?". Anchoring each at its own mid
    would make a price move look like a withdrawal, which is the confound the protocol names."""
    import pandas as pd
    from research.bybit_l2.grid import depth_series, side_matrix

    frame = pd.read_parquet(path)
    mid = frame["mid"].to_numpy(dtype=np.float64)
    n = len(frame)
    anchors = np.empty(n)
    anchors[:window_steps] = np.nan
    anchors[window_steps:] = mid[:-window_steps] if window_steps else mid

    out = {"mid": mid, "day": path.stem, "rows": n,
           "ts_ms": frame["ts_ms"].to_numpy(dtype="int64")}
    refusals = 0
    for side, name in (("b", "bid"), ("a", "ask")):
        matrix = side_matrix(frame, side)
        now = depth_series(frame, anchors, side, band_bps, matrix)
        # The reference is the SAME price band one window earlier. At that row the anchor IS
        # that row's own mid, so it is the ordinary mid-relative depth - shifted forward.
        at_anchor = depth_series(frame, mid, side, band_bps, matrix)
        past = np.full(n, np.nan)
        past[window_steps:] = at_anchor[:-window_steps]
        out[f"depth_{name}"] = now
        out[f"past_{name}"] = past
        refusals += int((~np.isfinite(now)).sum())
    out["refusals"] = refusals
    return out


def find_events_ratio(mid, now, past, horizon_steps, quiet, window_steps):
    """Vacuum events from a current/reference depth pair (see load_day for the anchoring)."""
    admitted = []
    last = -(10 ** 9)
    valid = np.isfinite(now) & np.isfinite(past) & (past > 0)
    collapsed = valid & (now <= COLLAPSE * past)
    if quiet:
        moved = np.full(len(mid), np.inf)
        moved[window_steps:] = np.abs(mid[window_steps:] - mid[:-window_steps])
        collapsed &= moved <= QUIET_DOLLARS
    for t in np.flatnonzero(collapsed):
        if t < window_steps or t >= len(mid) - horizon_steps:
            continue
        if t - last < horizon_steps:
            continue
        last = t
        admitted.append(t)
    return np.array(admitted, dtype=int)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    from side_specific_vacuum_v1_run import run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
