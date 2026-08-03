"""PHASE5C_123 - is P(hold) monotone in the things that matter, or only in AUC?

THE QUESTION
    AUC says a forecast ORDERS outcomes correctly. Threshold trading needs more than that: as
    the probability rises, realised correctness and realised net value must rise too. If the
    economic column is non-monotone, no threshold on the level can work - which is precisely
    what the hold-vs-exit head demonstrated at AUC 0.8731.

    So this reports, by fixed decile of the forecast: realised win rate, Brier contribution,
    and net value per share of acting at that decile.

DESCRIPTIVE ONLY - 21 days supports no effect below ~25 points.

    python research/phase5c/test_probability_monotonicity.py
"""
from __future__ import annotations

RESEARCH_STATUS = "VALID_DIAGNOSTIC"
CAPITAL_AUTHORITY = False

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from _common import assert_descriptive_only, load_checkpoints, side_ask  # noqa: E402
from polymarket_fee import polymarket_taker_fee_per_share  # noqa: E402

DECILES = 10


def decile_profile(probability, won, net) -> list[dict]:
    """Fixed deciles of the forecast, with realised correctness and realised value."""
    edges = np.quantile(probability, np.linspace(0, 1, DECILES + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    rows = []
    for index in range(DECILES):
        mask = (probability >= edges[index]) & (probability < edges[index + 1])
        if mask.sum() < 50:
            continue
        rows.append({"decile": index + 1, "n": int(mask.sum()),
                     "mean_p": float(probability[mask].mean()),
                     "win_rate": float(won[mask].mean()),
                     "brier": float(np.mean((probability[mask] - won[mask]) ** 2)),
                     "net": float(net[mask].mean())})
    return rows


def monotone_violations(rows, key: str) -> int:
    """How many adjacent deciles move the wrong way in `key`."""
    values = [row[key] for row in rows]
    return sum(1 for a, b in zip(values, values[1:]) if b < a)


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    rng = np.random.default_rng(5)
    probability = rng.uniform(0, 1, 5000)
    won = (rng.uniform(0, 1, 5000) < probability).astype(float)   # perfectly calibrated
    net = won - 0.5
    rows = decile_profile(probability, won, net)
    check(len(rows) == DECILES, "every decile with enough rows is reported")
    check(monotone_violations(rows, "win_rate") == 0,
          "a perfectly calibrated forecast is monotone in realised win rate")

    # A forecast whose ECONOMICS invert while its ordering stays intact - the failure mode.
    inverted_net = -net
    inverted = decile_profile(probability, won, inverted_net)
    check(monotone_violations(inverted, "win_rate") == 0
          and monotone_violations(inverted, "net") == DECILES - 1,
          "win rate can be perfectly monotone while NET VALUE is monotone the wrong way - "
          "the exact reason AUC does not license threshold trading")
    check(decile_profile(probability[:10], won[:10], net[:10]) == [],
          "a sample too thin per decile reports nothing rather than noise")

    print(f"\nMONOTONICITY SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    print("=" * 96)
    print("PHASE5C-123  PROBABILITY MONOTONICITY - does higher confidence mean higher value?")
    print("=" * 96)
    frame = load_checkpoints()
    if frame.empty:
        print("  BLOCKED: no eligible settled checkpoints.")
        return 0
    print(f"  {assert_descriptive_only()}")

    won = frame["won"].to_numpy(float)
    ask = side_ask(frame)
    fee = np.array([polymarket_taker_fee_per_share(float(a)) for a in ask])
    net = np.where(won == 1, 1.0 - ask - fee, -(ask + fee))

    for name, probability in (("P(hold)", np.clip(frame["p_hold_cur"].to_numpy(float),
                                                  1e-6, 1 - 1e-6)),
                              ("MARKET ask", np.clip(ask, 1e-6, 1 - 1e-6))):
        rows = decile_profile(probability, won, net)
        print()
        print(f"  --- {name} " + "-" * (70 - len(name)))
        print(f"{'decile':>7}{'n':>8}{'mean p':>9}{'win rate':>10}{'Brier':>9}{'net/$1':>10}")
        for row in rows:
            print(f"{row['decile']:>7}{row['n']:>8,}{row['mean_p']:>9.3f}"
                  f"{row['win_rate']:>10.3f}{row['brier']:>9.4f}{row['net']:>10.4f}")
        print(f"  monotone violations - win rate {monotone_violations(rows, 'win_rate')}, "
              f"net value {monotone_violations(rows, 'net')}")

    print()
    print("  A forecast can be perfectly monotone in win rate and still non-monotone in NET")
    print("  VALUE, because a higher probability costs a higher ask. That gap is why AUC does")
    print("  not license threshold trading, and it is what the hold-vs-exit head ran into at")
    print("  AUC 0.8731.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
