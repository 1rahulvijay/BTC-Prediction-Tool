"""CONDITIONAL_PATH_HEAD - structural probability that price sits above the round anchor.

    python backend/conditional_path_head.py --selftest

WHAT THIS IS
    P(price above the round anchor at checkpoint k), for a driftless random walk:

        z      = (price_now - anchor) / (sigma_per_min * sqrt(minutes_remaining) * anchor)
        P      = Phi(z)

    That is arithmetic, not a model. It has no parameters and nothing to train.

WHY IT IS THE HEAD, AND THE ML IS NOT
    `research/conditional_path_forecast_v1.py` tested three ML architectures against this
    formula on 200,000 1m bars, over 15m and 12m rounds. All three lost:

        15m   Brier      AUC          12m   Brier      AUC
        base 0.2083   0.7240          base 0.1978   0.7537
        full 0.2111   0.7170          full 0.1998   0.7477
        feat 0.2111   0.7170          feat 0.1998   0.7477
        offs 0.2096   0.7208          offs 0.1994   0.7494

    including a true log-odds offset model that structurally CANNOT damage the baseline. It
    applied a real correction (mean |log-odds| 0.25) and that correction was net harmful.

    Against a constant 0.50 (Brier 0.25) the formula is ~17% Brier skill at 15m and ~21% at
    12m. So this head is a good state-based probability estimator, and adding a model to it is
    not currently an improvement.

WHAT IT IS NOT
    It is NOT alpha, and it carries NO trading authority.

    Everything above is measured against the OUTCOME. None of it is measured against the
    Polymarket PRICE, which is the only comparison that decides tradeability - and the market
    can compute this same z as easily as we can. A late-round contract at 96.8% is probably
    priced near 0.97.

    So this head exists to be DISPLAYED and RECORDED, so that the geometry-vs-market experiment
    becomes possible once book snapshots accumulate. It must not gate, size, or authorise
    anything until it has beaten an executable ask after costs.

REFUSAL
    Every input that cannot support the formula returns None, never 0.5. A missing sigma is not
    a coin flip - it is an unknown, and a caller that cannot tell those apart will eventually
    show a fabricated 50% as though it were a forecast.
"""
from __future__ import annotations

import argparse
import math
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

#: Declared, and asserted by the selftest. This head informs; it never decides.
AUTHORITY = "NONE"
CONTRACT = "structural_anchor_geometry_v1"

#: Checkpoints offered per round length, in minutes from the round OPEN.
CHECKPOINTS = {
    5: (1, 2, 3, 4, 5),
    12: (3, 5, 7, 9, 12),
    15: (3, 5, 7, 10, 15),
}


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def anchor_z(price_now, anchor, sigma_per_min, minutes_remaining):
    """Distance to the anchor in units of the movement still available.

    Returns None when the inputs cannot support the calculation. That is the whole point of the
    normalisation: $120 above the anchor with 2 minutes left is a different state from $120
    above with 12 minutes left, and a z that silently becomes 0 would erase the difference.
    """
    try:
        price_now = float(price_now)
        anchor = float(anchor)
        sigma_per_min = float(sigma_per_min)
        minutes_remaining = float(minutes_remaining)
    except (TypeError, ValueError):
        return None
    if not all(map(math.isfinite, (price_now, anchor, sigma_per_min, minutes_remaining))):
        return None
    if anchor <= 0 or sigma_per_min <= 0 or minutes_remaining <= 0:
        return None
    scale = sigma_per_min * math.sqrt(minutes_remaining) * anchor
    if scale <= 0:
        return None
    return (price_now - anchor) / scale


def probability_above(price_now, anchor, sigma_per_min, minutes_remaining):
    """P(price above anchor at the checkpoint), or None if it cannot be computed."""
    z = anchor_z(price_now, anchor, sigma_per_min, minutes_remaining)
    if z is None:
        return None
    return normal_cdf(z)


def realized_sigma_per_min(closes, lookback: int = 30):
    """Per-minute log-return volatility from CLOSED bars only.

    `closes` must already exclude the forming bar. Returns None rather than a fallback
    constant: a made-up sigma propagates into every probability this head emits, and a fake
    0.001 would make a quiet market look decisive.
    """
    try:
        values = [float(c) for c in closes]
    except (TypeError, ValueError):
        return None
    if len(values) < lookback + 1:
        return None
    window = values[-(lookback + 1):]
    rets = []
    for prev, cur in zip(window, window[1:]):
        if prev <= 0 or cur <= 0:
            return None
        rets.append(math.log(cur / prev))
    n = len(rets)
    if n < 2:
        return None
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    sigma = math.sqrt(var)
    return sigma if sigma > 0 else None


def path(price_now, anchor, sigma_per_min, seconds_left, round_minutes: int) -> dict:
    """The full remaining checkpoint path for one open round.

    Every cell is independent geometry - this does not simulate a trajectory, and it does not
    claim the checkpoints are jointly consistent as a path. It answers each checkpoint's
    question separately, which is what the formula supports.
    """
    checkpoints = CHECKPOINTS.get(int(round_minutes) if round_minutes else 0, ())
    elapsed_min = (round_minutes * 60.0 - float(seconds_left or 0)) / 60.0 \
        if round_minutes else None
    out = {
        "contract": CONTRACT,
        "authority": AUTHORITY,
        "round_minutes": round_minutes,
        "anchor": anchor,
        "price_now": price_now,
        "sigma_per_min": sigma_per_min,
        "elapsed_min": round(elapsed_min, 2) if elapsed_min is not None else None,
        "checkpoints": [],
        "settlement_probability": None,
        "unavailable_reason": None,
    }
    if not checkpoints:
        out["unavailable_reason"] = f"no checkpoint grid for a {round_minutes}m round"
        return out
    if elapsed_min is None:
        out["unavailable_reason"] = "round length unknown"
        return out

    for k in checkpoints:
        remaining = k - elapsed_min
        if remaining <= 0:
            continue                       # already passed; not a forecast
        p = probability_above(price_now, anchor, sigma_per_min, remaining)
        cell = {
            "checkpoint_min": k,
            "minutes_remaining": round(remaining, 2),
            "p_above_anchor": None if p is None else round(p, 4),
            "z": None,
        }
        z = anchor_z(price_now, anchor, sigma_per_min, remaining)
        cell["z"] = None if z is None else round(z, 4)
        out["checkpoints"].append(cell)

    if not out["checkpoints"]:
        out["unavailable_reason"] = "no checkpoint remains in this round"
    elif all(c["p_above_anchor"] is None for c in out["checkpoints"]):
        out["unavailable_reason"] = "inputs insufficient (sigma, anchor or time)"
    else:
        out["settlement_probability"] = out["checkpoints"][-1]["p_above_anchor"]
    return out


# ---------------------------------------------------------------------------------------------
def selftest() -> int:
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
        ok = ok and bool(cond)

    print("conditional_path_head selftest")

    chk(AUTHORITY == "NONE", "the head declares NO trading authority")

    print("\n the geometry")
    chk(abs(probability_above(100.0, 100.0, 0.001, 5.0) - 0.5) < 1e-12,
        "sitting exactly ON the anchor is 50/50, whatever the time left")
    near = probability_above(100.12, 100.0, 0.001, 2.0)
    far = probability_above(100.12, 100.0, 0.001, 12.0)
    chk(near > far > 0.5,
        f"the same edge is stronger with less time left ({near:.3f} vs {far:.3f}) - this is "
        f"the entire content of the head")
    chk(probability_above(99.88, 100.0, 0.001, 2.0) < 0.5,
        "and being below the anchor is below 50%")
    a = probability_above(100.20, 100.0, 0.001, 3.0)
    b = probability_above(100.10, 100.0, 0.001, 3.0)
    chk(a > b, "a bigger edge is a higher probability at equal time")

    print("\n refusal, not fabrication")
    for label, args in (
        ("sigma unknown", (100.5, 100.0, None, 5.0)),
        ("sigma zero", (100.5, 100.0, 0.0, 5.0)),
        ("no time left", (100.5, 100.0, 0.001, 0.0)),
        ("negative time", (100.5, 100.0, 0.001, -1.0)),
        ("anchor zero", (100.5, 0.0, 0.001, 5.0)),
        ("nan price", (float("nan"), 100.0, 0.001, 5.0)),
        ("garbage", ("x", 100.0, 0.001, 5.0)),
    ):
        chk(probability_above(*args) is None and anchor_z(*args) is None,
            f"{label} -> None, never 0.5")

    print("\n sigma is causal and refuses thin history")
    chk(realized_sigma_per_min([100.0] * 10) is None,
        "fewer bars than the lookback yields None rather than a fabricated constant")
    flat = realized_sigma_per_min([100.0] * 40)
    chk(flat is None, "a perfectly flat series has zero vol -> None, not a divide-by-zero")
    import random
    rng = random.Random(4)
    walk = [100.0]
    for _ in range(80):
        walk.append(walk[-1] * math.exp(rng.gauss(0, 0.001)))
    sig = realized_sigma_per_min(walk)
    chk(sig is not None and 0.0002 < sig < 0.005,
        f"a real walk yields a plausible per-minute sigma ({sig:.5f})")
    chk(realized_sigma_per_min(walk[:-1]) != realized_sigma_per_min(walk),
        "and it moves with the window, so it is not a constant in disguise")
    chk(realized_sigma_per_min([100.0, -5.0] * 40) is None,
        "a non-positive price refuses rather than taking log of a negative")

    print("\n the path")
    p = path(price_now=100.10, anchor=100.0, sigma_per_min=0.001,
             seconds_left=300, round_minutes=15)
    chk(p["authority"] == "NONE" and p["contract"] == CONTRACT,
        "the payload carries its contract and its lack of authority")
    chk(p["elapsed_min"] == 10.0, "elapsed time is derived from seconds_left")
    ks = [c["checkpoint_min"] for c in p["checkpoints"]]
    chk(ks == [15], "with 5 minutes left only the 15m checkpoint remains unforecast")
    chk(all(c["minutes_remaining"] > 0 for c in p["checkpoints"]),
        "and no cell forecasts a checkpoint that has already passed")
    chk(p["settlement_probability"] is not None and p["settlement_probability"] > 0.5,
        "being above the anchor late gives a settlement probability above 0.5")

    early = path(100.10, 100.0, 0.001, seconds_left=840, round_minutes=15)
    chk([c["checkpoint_min"] for c in early["checkpoints"]] == [3, 5, 7, 10, 15],
        "early in the round every checkpoint is still ahead")
    chk(early["settlement_probability"] < p["settlement_probability"],
        "and the same edge is worth LESS at minute 1 than at minute 10 - the head's whole "
        "reason for existing")

    chk(path(100.1, 100.0, 0.001, 300, 7)["unavailable_reason"] is not None,
        "an unsupported round length is refused with a reason, not silently empty")
    blind = path(100.1, 100.0, None, 300, 15)
    chk(blind["settlement_probability"] is None and blind["unavailable_reason"],
        "and a missing sigma yields no probability plus a stated reason")

    print("\n 12m and 5m grids exist and end at settlement")
    for minutes in (5, 12, 15):
        chk(CHECKPOINTS[minutes][-1] == minutes,
            f"the {minutes}m grid's last checkpoint IS its settlement minute")

    print(f"\nCONDITIONAL PATH HEAD SELFTEST: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
