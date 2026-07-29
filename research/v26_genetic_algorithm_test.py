"""V26 - REWRITTEN. The original evolved and scored on one dataset.

WHAT THE ORIGINAL DID
    evaluate_fitness(df, dna) ranked genomes on the SAME df they were evolved against, then
    reported the best-of-N as +32,598%. Searching a large genome space against one sample and
    reporting the winner is the definition of overfitting - the number measures search effort,
    not edge.

WHAT THIS DOES
    Evolves ONLY on the training period, freezes the winning genome, and reports it on data
    the search never saw. The in-sample/out-of-sample gap IS the result.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Backtest, forward_returns, load_btc, report, split  # noqa: E402


POPULATION = 40
GENERATIONS = 8
RNG = np.random.default_rng(20260729)


def evaluate(part, dna) -> Backtest:
    entry_z, tp, sl = dna
    book = Backtest()
    signal = part["z"] < -abs(entry_z)
    for gross in part.loc[signal, "fwd"]:
        book.trade(float(np.clip(gross, -abs(sl), abs(tp))))
    return book


def main() -> int:
    frame = load_btc(200_000).copy()
    close = frame["close"]
    frame["z"] = ((close - close.rolling(60).mean()) / close.rolling(60).std())
    frame["fwd"] = forward_returns(frame, 15)
    frame = frame.dropna()
    train, test = split(frame)

    population = [(RNG.uniform(0.5, 3.0), RNG.uniform(0.002, 0.02),
                   RNG.uniform(0.002, 0.02)) for _ in range(POPULATION)]
    for _ in range(GENERATIONS):
        scored = sorted(population, key=lambda d: -evaluate(train, d).total_return_pct)
        elite = scored[: POPULATION // 4]
        population = list(elite)
        while len(population) < POPULATION:
            a, b = elite[RNG.integers(len(elite))], elite[RNG.integers(len(elite))]
            child = tuple(float((x + y) / 2 * RNG.uniform(0.9, 1.1)) for x, y in zip(a, b))
            population.append(child)

    best = max(population, key=lambda d: evaluate(train, d).total_return_pct)
    print(f"[V26] genome selected on TRAIN ONLY: entry_z={best[0]:.3f} "
          f"tp={best[1]:.4f} sl={best[2]:.4f}")
    report("V26 - genetic search, frozen genome on unseen data (was: +32,598% in-sample)",
           evaluate(train, best), evaluate(test, best),
           notes="the original scored the winner on the data it was evolved against")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
