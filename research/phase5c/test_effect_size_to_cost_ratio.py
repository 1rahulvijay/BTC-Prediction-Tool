"""PHASE5C_130 - is an effect remotely large enough to monetize? The universal prefilter.

THE RULE THIS ENFORCES
    A test should not merely ask whether something is predictable. It should first establish
    whether the predicted effect is large enough, lasts long enough, and maps to an instrument
    capable of monetizing it.

    Everything measured in this repository so far failed on the FIRST of those, not on
    predictability:

        hold-vs-exit classifier   AUC 0.8731   realised value -0.0004/share vs doing nothing
        Binance opportunity head  AUC 0.6462   realised -4.59 bps against a 0.00 bar
        market vs model           market wins on Brier, log loss, ECE and AUC

    Prediction was never the bottleneck. So this scores every candidate as a RATIO against the
    cost it must clear, before anyone builds a model for it.

THE BANDS, DECLARED
        < 0.25x cost   economically irrelevant - do not build
        0.25 - 0.75x   research only, cannot pay at current costs
        0.75 - 1.25x   execution-sensitive - only viable if costs fall
        > 1.25x        eligible for deeper testing

    A ratio is not a p-value. An effect can clear 1.25x and still be noise, which is why the
    companion test (136) reports what this window can detect at all. Both must pass: an effect
    must be big enough to pay AND big enough to see.

    python research/phase5c/test_effect_size_to_cost_ratio.py
    python research/phase5c/test_effect_size_to_cost_ratio.py --selftest
"""
from __future__ import annotations

RESEARCH_STATUS = "VALID_DIAGNOSTIC"
CAPITAL_AUTHORITY = False

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "binance_alpha"))

BANDS = ((0.25, "IRRELEVANT      do not build"),
         (0.75, "RESEARCH_ONLY   cannot pay at current cost"),
         (1.25, "EXECUTION_SENSITIVE  viable only if cost falls"),
         (float("inf"), "ELIGIBLE        deeper testing justified"))


def classify(gross_effect: float, cost: float) -> tuple[float, str]:
    """Ratio of gross effect to the cost it must clear, and its declared band."""
    if cost <= 0:
        raise ValueError("cost must be positive - a free trade is not a trade")
    ratio = float(gross_effect) / float(cost)
    for bound, label in BANDS:
        if ratio < bound:
            return ratio, label
    return ratio, BANDS[-1][1]


def break_even_cost(gross_effect: float) -> float:
    """The cost at which this effect exactly breaks even."""
    return max(0.0, float(gross_effect))


def measured_candidates() -> list[dict]:
    """Every effect this repository has actually measured, with its own cost basis.

    Hand-entered from the committed studies rather than recomputed, because the point of a
    prefilter is to be cheap. Each row cites where the number came from so it can be checked."""
    from action_value import round_trip_bps
    binance = round_trip_bps()

    return [
        # --- Binance perpetual, basis points ------------------------------------------------
        {"candidate": "direction @60m", "gross": 0.0, "cost": binance, "unit": "bps",
         "source": "binance_opportunity_head_v1: AUC 0.4853, below chance"},
        {"candidate": "direction @120m", "gross": 0.0, "cost": binance, "unit": "bps",
         "source": "binance_opportunity_head_v1: AUC 0.4910, below chance"},
        {"candidate": "opportunity gate @60m", "gross": 7.41, "cost": binance, "unit": "bps",
         "source": "head -4.59 vs always-short -11.12 vs WAIT 0; gain over the fixed bar"},
        {"candidate": "perfect exit @60m (ceiling)", "gross": 31.45, "cost": binance,
         "unit": "bps", "source": "action engine +19.45 net, +12.0 cost added back"},
        {"candidate": "perfect exit @120m (ceiling)", "gross": 42.23, "cost": binance,
         "unit": "bps", "source": "action engine +30.23 net, +12.0 cost added back"},
        {"candidate": "15m lane (ceiling)", "gross": 14.75, "cost": binance, "unit": "bps",
         "source": "action engine +2.75 net, +12.0 added back; MEDIAN is -2.63"},
    ]


def polymarket_candidates() -> list[dict]:
    """Polymarket effects, per share. Cost is the round trip on a representative quote."""
    from polymarket_policy.execution_cost import round_trip_cost

    # A 0.70 ask against a 0.68 bid is a typical late-round leader quote.
    cost = round_trip_cost(0.70, 0.68)
    return [
        {"candidate": "hold-vs-exit classifier", "gross": 0.0004, "cost": cost, "unit": "$/share",
         "source": "hold_vs_exit_head_v1: -0.0107 vs always-hold -0.0103"},
        {"candidate": "EV magnitude rule", "gross": 0.0, "cost": cost, "unit": "$/share",
         "source": "ev_magnitude_rule_v1: -0.0215, worse than random at matched count"},
        {"candidate": "perfect exit (ceiling)", "gross": 0.1005 + cost, "cost": cost,
         "unit": "$/share", "source": "action_value_builder PERFECT +0.1005 net"},
        {"candidate": "complete-set lock", "gross": 0.0, "cost": cost, "unit": "$/share",
         "source": "best action on 3 of 50,272 checkpoints; mean margin -0.0302"},
    ]


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    check(classify(0.419, 9.0)[1].startswith("IRRELEVANT"),
          "the 0.419 bps microprice effect against a 9 bps hurdle is IRRELEVANT - the lane "
          "this prefilter would have killed on sight")
    check(classify(20.0, 12.0)[1].startswith("ELIGIBLE"),
          "a 20 bps effect against a 12 bps round trip is eligible for deeper testing")
    check(classify(12.0, 12.0)[1].startswith("EXECUTION_SENSITIVE"),
          "an effect exactly equal to cost is execution-sensitive, not eligible")
    check(classify(6.0, 12.0)[1].startswith("RESEARCH_ONLY"),
          "half the cost is research-only - real, and unable to pay")
    check(classify(0.0, 12.0)[0] == 0.0,
          "a zero effect scores zero rather than raising")

    try:
        classify(1.0, 0.0)
        check(False, "unreachable")
    except ValueError:
        check(True, "a zero cost is REFUSED - a free trade is not a trade")

    check(abs(break_even_cost(7.41) - 7.41) < 1e-9,
          "break-even cost equals the gross effect by definition")
    check(all(row["cost"] > 0 for row in measured_candidates() + polymarket_candidates()),
          "every declared candidate carries a positive cost basis")

    print(f"\nEFFECT-SIZE PREFILTER SELFTEST: PASS ({checks} checks)")
    return 0


def report(title: str, rows: list[dict]) -> None:
    print()
    print(f"  --- {title} " + "-" * (74 - len(title)))
    print(f"{'candidate':<32}{'gross':>10}{'cost':>9}{'ratio':>8}  band")
    for row in rows:
        ratio, band = classify(row["gross"], row["cost"])
        print(f"{row['candidate']:<32}{row['gross']:>10.4f}{row['cost']:>9.4f}"
              f"{ratio:>8.2f}  {band}")
        print(f"{'':<32}{row['source']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    print("=" * 96)
    print("PHASE5C-130  EFFECT SIZE TO COST - is it big enough to be worth predicting?")
    print("=" * 96)
    report("Binance perpetual (bps)", measured_candidates())
    report("Polymarket (dollars per share)", polymarket_candidates())
    print()
    print("  READ THIS WITH 136. An effect must be big enough to PAY and big enough to SEE.")
    print("  Only the hindsight ceilings clear 1.25x, and a ceiling is not a strategy - it is")
    print("  the most a perfect head could win. Every REALISABLE effect measured so far scores")
    print("  below 0.75x, which is why none of them converted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
