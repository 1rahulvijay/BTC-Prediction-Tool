"""
STRUCTURAL_FAIR_VALUE_V1 - what an UP share is worth from geometry alone.

A Polymarket BTC Up/Down round settles UP if the official price at expiry is at or above a
FIXED anchor. Given the current price, the distance to that anchor, the time remaining and a
volatility estimate, that probability has a closed form under a driftless random walk:

    z = ln(P_t / A) / (sigma * sqrt(T))
    P(UP) = Phi(z)

This is deliberately NOT a learned model. It is the baseline a learned model has to beat, and
it is the thing an ML residual should CORRECT rather than rediscover. Asking a gradient
booster to infer "distance over root-time" from raw features is asking it to spend its
capacity on arithmetic that is already known exactly.

Three properties this has that the current direction head does not:

  * It answers the question the venue settles. Not "will BTC rise", not "which barrier is
    touched first" - will the price at THIS timestamp be at or above THIS anchor.
  * It is calibrated by construction under its own assumptions, so a calibration failure is
    informative about the assumptions rather than about fitting.
  * It is monotone in every input in the direction physics requires, which is testable.

WHAT IT DOES NOT MODEL, deliberately: drift (a 5-15 minute BTC drift estimate is noise),
jumps, volatility clustering within the round, and the settlement-source basis between the
observed feed and the official resolver. Each is a candidate residual term, and each should be
ADDED as a measured correction rather than assumed.

The volatility input is per-second. `sigma_from_path` estimates it from the observed price
series rather than from a column whose units cannot be verified.
"""

from __future__ import annotations

import math

#: Below this, the round is decided by microstructure rather than by diffusion and the
#: closed form stops being meaningful. Callers should treat it as "no structural opinion".
MIN_SECONDS_FOR_DIFFUSION = 2.0

#: A floor on per-second sigma. With sigma at zero the z-score is infinite and Phi saturates
#: to a certainty the data cannot support.
MIN_SIGMA_PER_SEC = 1e-7


def normal_cdf(x: float) -> float:
    """Phi. `math.erf` is exact enough and avoids a scipy dependency in the serving path."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def sigma_from_path(prices, timestamps_s, *, minimum_observations: int = 8) -> float | None:
    """Per-second volatility from an observed price path.

    Returns None when the path cannot support an estimate. That is a refusal, not a zero:
    a zero sigma makes `Phi` return a certainty, so substituting one would manufacture
    confidence out of missing data.

    Uses log returns scaled by the ACTUAL elapsed time between observations, because the
    recorder's cadence is not uniform - assuming a fixed interval would misscale sigma by
    the square root of the cadence error.
    """
    if prices is None or timestamps_s is None:
        return None
    n = min(len(prices), len(timestamps_s))
    if n < minimum_observations:
        return None
    per_sec_sq = []
    for i in range(1, n):
        dt = float(timestamps_s[i]) - float(timestamps_s[i - 1])
        p0, p1 = float(prices[i - 1]), float(prices[i])
        if dt <= 0 or p0 <= 0 or p1 <= 0:
            continue
        r = math.log(p1 / p0)
        per_sec_sq.append(r * r / dt)
    if len(per_sec_sq) < minimum_observations - 1:
        return None
    return math.sqrt(sum(per_sec_sq) / len(per_sec_sq))


def structural_p_up(
    btc_price: float,
    anchor_price: float,
    seconds_left: float,
    sigma_per_sec: float,
) -> float | None:
    """P(settlement UP) from geometry. None when the inputs cannot support an opinion.

    The tie convention matters and follows the venue: settlement is UP when the final price
    is at or ABOVE the anchor. At zero distance with time remaining the answer is 0.5, which
    is what the formula gives.
    """
    try:
        p = float(btc_price)
        a = float(anchor_price)
        t = float(seconds_left)
        s = float(sigma_per_sec)
    except (TypeError, ValueError):
        return None
    if p <= 0 or a <= 0 or not math.isfinite(p) or not math.isfinite(a):
        return None
    if t < MIN_SECONDS_FOR_DIFFUSION or not math.isfinite(t):
        return None
    if s < MIN_SIGMA_PER_SEC or not math.isfinite(s):
        return None
    z = math.log(p / a) / (s * math.sqrt(t))
    return normal_cdf(z)


#: Polymarket crypto TAKER fee, from the published formula:
#:     fee = shares * 0.07 * price * (1 - price)
#: It is largest at 50c (1.75c/share) and vanishes at the extremes, which is the opposite of
#: a flat fee and changes which trades are viable: a 6c edge at 50c keeps 4.25c, the same 6c
#: edge at 90c keeps 5.37c. Makers currently pay no platform trading fee.
TAKER_FEE_COEFFICIENT = 0.07


def taker_fee_per_share(price: float) -> float:
    """Fee in probability units (= dollars per share) for a taker fill at `price`."""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return 0.0
    if not (0.0 <= p <= 1.0):
        return 0.0
    return TAKER_FEE_COEFFICIENT * p * (1.0 - p)


def net_edge_per_share(
    fair_probability: float,
    ask: float,
    *,
    slippage: float = 0.0,
    latency_allowance: float = 0.0,
) -> dict:
    """Raw edge, every cost, and what survives.

    Returned in probability units, which for a binary settling at 0 or 1 ARE dollars per
    share. Keeping one unit end to end is what stops a cents/fraction mix-up from looking
    like alpha.
    """
    try:
        fair = float(fair_probability)
        price = float(ask)
    except (TypeError, ValueError):
        return {"tradeable": False, "reason": "non_numeric"}
    if not (0.0 < price < 1.0):
        return {"tradeable": False, "reason": "ask_out_of_range"}
    raw = fair - price
    fee = taker_fee_per_share(price)
    net = raw - fee - float(slippage) - float(latency_allowance)
    return {
        "tradeable": True,
        "fair_probability": fair,
        "ask": price,
        "raw_edge": raw,
        "taker_fee": fee,
        "slippage": float(slippage),
        "latency_allowance": float(latency_allowance),
        "net_edge": net,
    }


def _selftest() -> int:
    failures = []

    def chk(cond, msg):
        print(("  OK   " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    print("STRUCTURAL FAIR VALUE V1")
    sigma = 1e-5   # per second

    at_anchor = structural_p_up(100_000.0, 100_000.0, 120.0, sigma)
    chk(abs(at_anchor - 0.5) < 1e-9,
        f"at the anchor with time left the answer is exactly 0.5 ({at_anchor})")

    above = structural_p_up(100_050.0, 100_000.0, 120.0, sigma)
    below = structural_p_up(99_950.0, 100_000.0, 120.0, sigma)
    chk(above > 0.5 > below, f"above the anchor {above:.4f} > 0.5 > below {below:.4f}")
    chk(abs((above - 0.5) - (0.5 - below)) < 1e-3,
        "and the two are symmetric about the anchor, as a driftless walk requires")

    near, far = (structural_p_up(100_050.0, 100_000.0, t, sigma) for t in (10.0, 600.0))
    chk(near > far,
        f"the SAME distance is worth more with less time left ({near:.4f} at 10s vs "
        f"{far:.4f} at 600s) - time is what lets the price come back")

    calm = structural_p_up(100_050.0, 100_000.0, 120.0, 1e-6)
    wild = structural_p_up(100_050.0, 100_000.0, 120.0, 1e-4)
    chk(calm > wild,
        f"and more with lower volatility ({calm:.4f} calm vs {wild:.4f} wild)")

    chk(structural_p_up(100_050.0, 100_000.0, 0.5, sigma) is None,
        "under 2 seconds it refuses - that regime is microstructure, not diffusion")
    chk(structural_p_up(100_050.0, 100_000.0, 120.0, 0.0) is None,
        "and a zero sigma refuses rather than returning a certainty")

    print("\n  the taker fee is largest exactly where these markets trade")
    fees = {p: taker_fee_per_share(p) for p in (0.10, 0.30, 0.50, 0.70, 0.90)}
    for p, f in fees.items():
        print(f"    price {p:.2f} -> fee {f * 100:.2f}c/share")
    chk(fees[0.50] > fees[0.10] and fees[0.50] > fees[0.90],
        "peaking at 0.50 (1.75c) and vanishing at the extremes")

    e = net_edge_per_share(0.585, 0.52)
    chk(abs(e["raw_edge"] - 0.065) < 1e-9 and abs(e["taker_fee"] - 0.017472) < 1e-6,
        f"a 6.5c raw edge at a 52c ask pays {e['taker_fee'] * 100:.2f}c in fee")
    chk(abs(e["net_edge"] - (0.065 - e["taker_fee"])) < 1e-12,
        f"leaving {e['net_edge'] * 100:.2f}c before slippage and latency")

    print("\n  sigma is estimated from the path, and refuses when it cannot be")
    ts = list(range(0, 200))
    px = [100_000.0 * math.exp(0.00001 * math.sin(i / 3.0)) for i in ts]
    s = sigma_from_path(px, ts)
    chk(s is not None and s > 0, f"a real path gives a per-second sigma ({s:.3e})")
    chk(sigma_from_path([1.0, 2.0], [0, 1]) is None,
        "too few observations refuses rather than returning a fabricated zero")

    print("\n" + ("FAIR VALUE SELFTEST: FAIL" if failures else "FAIR VALUE SELFTEST: PASS"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
