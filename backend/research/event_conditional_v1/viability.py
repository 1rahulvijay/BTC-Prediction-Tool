"""Horizon viability gate - refuse horizons the cost structure has already ruled out.

    P(|move over h| > round_trip_cost)

upper-bounds the fraction of timestamps at which a PERFECT-direction oracle could
profit. A real model is strictly worse. If that ceiling is 2.5%, no amount of alpha
work rescues the horizon; the cost simply exceeds almost the whole move distribution.

This is the lesson PROFIT_CAMPAIGN_V1 paid for. It traded a 30s horizon at a 12 bps
round trip. Measured over 129 days spanning 2023-2026, the 30s oracle ceiling is
2.49% - so ~97.5% of its entries could not profit under ANY model. Its observed
profit factor was exactly 0.0000 across 374 trades, which is what that arithmetic
predicts.

The gate is mechanical: a family may not register a horizon/execution-style pair
whose measured ceiling is below the preregistered floor.

    python -m backend.research.event_conditional_v1.viability      # print the table
"""
from __future__ import annotations

from contracts import Protocol, load_protocol


class HorizonNotViable(ValueError):
    """Raised when a family tries to register a disqualified horizon."""


def oracle_ceiling(protocol: Protocol, horizon_s: int, maker: bool) -> float | None:
    """Measured P(|move| > round-trip cost) for this horizon and execution style."""
    row = protocol.oracle_ceilings.get(str(horizon_s))
    if not row:
        return None
    return float(row["maker_6bps" if maker else "taker_12bps"])


def check_horizon(protocol: Protocol, horizon_s: int, maker: bool) -> tuple[bool, str]:
    """(admissible, reason). Unmeasured horizons are refused, not assumed viable."""
    ceiling = oracle_ceiling(protocol, horizon_s, maker)
    style = "maker" if maker else "taker"
    if ceiling is None:
        return False, f"{horizon_s}s {style}: no measured ceiling - refuse rather than assume"
    floor = protocol.viability_floor
    if ceiling < floor:
        return False, (f"{horizon_s}s {style}: oracle ceiling {ceiling:.2%} < floor {floor:.0%} "
                       f"- a perfect-direction oracle could profit at only {ceiling:.2%} of "
                       f"timestamps, so a real model has no room")
    if horizon_s not in protocol.admissible_horizons(maker):
        return False, f"{horizon_s}s {style}: not in the protocol's admissible list"
    return True, f"{horizon_s}s {style}: ceiling {ceiling:.2%} >= floor {floor:.0%}"


def require_horizon(protocol: Protocol, horizon_s: int, maker: bool) -> None:
    ok, why = check_horizon(protocol, horizon_s, maker)
    if not ok:
        raise HorizonNotViable(why)


def viability_table(protocol: Protocol) -> list[dict]:
    out = []
    for h_str, row in sorted(protocol.oracle_ceilings.items(), key=lambda kv: int(kv[0])):
        h = int(h_str)
        t_ok, _ = check_horizon(protocol, h, maker=False)
        m_ok, _ = check_horizon(protocol, h, maker=True)
        out.append({
            "horizon_s": h,
            "median_abs_bps": row["median_abs_bps"],
            "taker_ceiling": row["taker_12bps"],
            "maker_ceiling": row["maker_6bps"],
            "taker_admissible": t_ok,
            "maker_admissible": m_ok,
        })
    return out


def main() -> int:
    p = load_protocol()
    ev = p.raw["horizon_viability_gate"]["measured_evidence"]
    print("=" * 78)
    print("HORIZON VIABILITY GATE - " + p.protocol_id)
    print("=" * 78)
    print(f"evidence : {ev['source']}")
    print(f"span     : {ev['span']}  ({ev['days_sampled']} days sampled)")
    print(f"method   : {ev['method']}")
    print(f"floor    : oracle ceiling must be >= {p.viability_floor:.0%}")
    print()
    print(f"{'horizon':>8} {'med|mv|':>9} {'taker@12bps':>12} {'maker@6bps':>12}  admissible")
    print("-" * 78)
    for r in viability_table(p):
        adm = []
        if r["taker_admissible"]:
            adm.append("taker")
        if r["maker_admissible"]:
            adm.append("maker")
        print(f"{r['horizon_s']:>7}s {r['median_abs_bps']:>9.2f} "
              f"{r['taker_ceiling']:>11.2%} {r['maker_ceiling']:>11.2%}  "
              + (", ".join(adm) if adm else "NONE - disqualified"))
    print("-" * 78)
    print(ev["regime_stability"])
    print()
    print(ev["consequence"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
