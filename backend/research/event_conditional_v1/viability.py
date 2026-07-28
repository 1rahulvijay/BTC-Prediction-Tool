"""Horizon admission gate - refuse horizons the cost structure has already ruled out.

THE MEASURE

    endpoint cost-clearance rate
      = P(|endpoint move over h| > round_trip_cost)

This is the share of anchor timestamps at which a trade held for exactly `h` and
closed at the endpoint would clear the assumed round-trip cost, given PERFECT
direction. It is deliberately NOT called a profitability ceiling: it excludes
maximum favourable excursion before the endpoint, stop/target exits, variable
holding periods, spread and depth changes, funding, partial fills, and maker fill
probability and adverse selection. It is a screen on one specific execution
assumption - fixed horizon, endpoint exit - and nothing more.

WHY IT STILL DECIDES ADMISSION

    At 30s the rate is 2.49% (LB95 2.08%). An indiscriminate fixed-horizon 30s taker
    system is therefore overwhelmingly likely to lose: 97.5% of its anchors cannot
    clear cost even with perfect foresight. That does not mathematically force a
    profit factor of 0 - a sufficiently selective signal could in principle trade
    only the eligible 2.49% - but it makes PROFIT_CAMPAIGN_V1's measured PF of 0.0000
    across 374 trades economically unsurprising for a system that traded every 15s.

ADMISSION USES A LOWER BOUND, NOT A POINT ESTIMATE

    Anchors within a day overlap heavily and are strongly autocorrelated; millions of
    rows are nowhere near millions of independent samples. Admission therefore uses the
    day-block bootstrap 95% lower bound. This is not cosmetic: 60s maker has a point
    estimate of 21.46% (passes a 20% floor) and an LB95 of 19.59% (fails), and is
    excluded on that basis.

    python -m backend.research.event_conditional_v1.viability
"""
from __future__ import annotations

from .contracts import Protocol, load_protocol

# Cost scenarios. Only PRIMARY scenarios may admit a horizon.
PRIMARY_TAKER = "taker_12bps"
PRIMARY_MAKER = "maker_6bps"
SENSITIVITY_ONLY = ("maker_4bps",)


class HorizonNotViable(ValueError):
    """Raised when a family tries to register a disqualified horizon."""


def clearance(protocol: Protocol, horizon_s: int, maker: bool,
              lower_bound: bool = True) -> float | None:
    """Endpoint cost-clearance rate for this horizon under the PRIMARY cost scenario.

    `lower_bound=True` returns the day-block bootstrap 95% lower bound, which is what
    admission uses. The point estimate is available for reporting only.
    """
    row = protocol.clearance_points.get(str(horizon_s))
    if not row:
        return None
    key = PRIMARY_MAKER if maker else PRIMARY_TAKER
    if lower_bound:
        lb = (protocol.raw["horizon_viability_gate"]["measured_evidence"]
              .get("clearance_lb95_by_horizon_s", {}).get(str(horizon_s), {}))
        val = lb.get(key)
        return float(val) if val is not None else None
    return float(row[key])


def check_horizon(protocol: Protocol, horizon_s: int, maker: bool) -> tuple[bool, str]:
    """(admissible, reason). Unmeasured horizons are refused, not assumed viable."""
    style = "maker" if maker else "taker"
    lb = clearance(protocol, horizon_s, maker, lower_bound=True)
    pt = clearance(protocol, horizon_s, maker, lower_bound=False)
    if lb is None or pt is None:
        return False, (f"{horizon_s}s {style}: no measured clearance rate "
                       f"- refuse rather than assume")
    floor = protocol.viability_floor
    if lb < floor:
        return False, (f"{horizon_s}s {style}: clearance LB95 {lb:.2%} < floor {floor:.0%} "
                       f"(point {pt:.2%}) - too few anchors can clear cost even with "
                       f"perfect direction")
    if horizon_s not in protocol.admissible_horizons(maker):
        return False, f"{horizon_s}s {style}: not in the protocol's admissible list"
    return True, f"{horizon_s}s {style}: clearance LB95 {lb:.2%} >= floor {floor:.0%}"


def require_horizon(protocol: Protocol, horizon_s: int, maker: bool) -> None:
    ok, why = check_horizon(protocol, horizon_s, maker)
    if not ok:
        raise HorizonNotViable(why)


def viability_table(protocol: Protocol) -> list[dict]:
    out = []
    for h_str, row in sorted(protocol.clearance_points.items(), key=lambda kv: int(kv[0])):
        h = int(h_str)
        t_ok, _ = check_horizon(protocol, h, maker=False)
        m_ok, _ = check_horizon(protocol, h, maker=True)
        out.append({
            "horizon_s": h,
            "median_abs_bps": row["median_abs_bps"],
            "taker_point": row[PRIMARY_TAKER],
            "taker_lb95": clearance(protocol, h, False, True),
            "maker_point": row[PRIMARY_MAKER],
            "maker_lb95": clearance(protocol, h, True, True),
            "taker_admissible": t_ok,
            "maker_admissible": m_ok,
        })
    return out


def main() -> int:
    p = load_protocol()
    ev = p.raw["horizon_viability_gate"]["measured_evidence"]
    s = ev["sample"]
    print("=" * 92)
    print("HORIZON ADMISSION GATE - " + p.protocol_id)
    print("=" * 92)
    print("measure  : endpoint cost-clearance rate (NOT a profitability ceiling)")
    print(f"evidence : {ev['source']}")
    print(f"span     : {ev['span']}   role={ev['dataset_role']}")
    print(f"sample   : {s['days_sampled']} of {s['days_available']} days  "
          f"manifest={s['manifest_sha256'][:16]}...")
    print(f"bootstrap: day-block, {s['bootstrap_resamples']} resamples, seed {s['bootstrap_seed']}")
    print(f"floor    : clearance LB95 must be >= {p.viability_floor:.0%}")
    print()
    print(f"{'horizon':>8} {'med|mv|':>8} {'taker pt':>9} {'taker LB':>9} "
          f"{'maker pt':>9} {'maker LB':>9}  admissible")
    print("-" * 92)
    for r in viability_table(p):
        adm = [x for x, ok in (("taker", r["taker_admissible"]), ("maker", r["maker_admissible"])) if ok]
        print(f"{r['horizon_s']:>7}s {r['median_abs_bps']:>8.2f} "
              f"{r['taker_point']:>8.2%} {r['taker_lb95']:>8.2%} "
              f"{r['maker_point']:>8.2%} {r['maker_lb95']:>8.2%}  "
              + (", ".join(adm) if adm else "NONE - disqualified"))
    print("-" * 92)
    print("PRIMARY scenarios: taker 12bps, maker 6bps.")
    print("SENSITIVITY ONLY (cannot admit a horizon, cannot support promotion): "
          + ", ".join(SENSITIVITY_ONLY))
    print()
    print("selected grid (deterministic: shortest / middle / longest eligible)")
    for style in ("taker", "maker"):
        print(f"  {style:<6} {p.raw['selected_horizons_seconds'][style]}")
    print()
    print(ev["regime_note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
