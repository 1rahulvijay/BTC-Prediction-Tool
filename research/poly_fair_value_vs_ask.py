"""
The comparison that decides whether a taker lane exists at all.

The repository's own ledger has recorded this as the next decisive test for a long time and
never run it: is the structural fair value MORE INFORMATIVE than the price you would have to
pay? Not "is it better than a coin flip" - the coin flip is not the competitor. The
Polymarket ask is, and section 4.5 already found that ask beating both model vintages on
Brier, log loss, ECE and AUC.

So this asks three questions in order, and stops mattering if the first one fails:

  1. SKILL      Does the structural probability beat the market's own implied probability on
                proper scores, out of sample, on settled rounds?
  2. EDGE       Where they disagree, is the disagreement in the right direction?
  3. MONEY      Does any of it survive the real Polymarket taker fee,
                fee = 0.07 * price * (1 - price), which is 1.75c/share at 50c?

A negative answer here is the most valuable output available, because it closes the taker
lane before any capital or further modelling is spent on it.

Read-only. Exits non-zero only on a data problem, never on an unfavourable finding.

    python research/poly_fair_value_vs_ask.py
"""

from __future__ import annotations

import math
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from polymarket_fair_value import (  # noqa: E402
    net_edge_per_share, sigma_from_path, structural_p_up, taker_fee_per_share,
)

DB = os.environ.get("BTC_EXECUTION_DB", str(ROOT / "data" / "execution_layer.duckdb"))

#: A round is only usable if it settled AND we hold enough live snapshots to estimate its own
#: volatility from its own path.
MIN_SNAPSHOTS_PER_ROUND = 20


def brier(pairs) -> float:
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs, eps: float = 1e-6) -> float:
    total = 0.0
    for p, y in pairs:
        q = min(max(p, eps), 1.0 - eps)
        total += -(y * math.log(q) + (1 - y) * math.log(1 - q))
    return total / len(pairs)


def calibration_table(pairs, bins: int = 10):
    buckets = defaultdict(list)
    for p, y in pairs:
        buckets[min(bins - 1, int(p * bins))].append((p, y))
    out = []
    for b in sorted(buckets):
        rows = buckets[b]
        out.append((b / bins, (b + 1) / bins, len(rows),
                    sum(p for p, _ in rows) / len(rows),
                    sum(y for _, y in rows) / len(rows)))
    return out


def load(conn):
    """Live in-window snapshots on rounds that settled, with each round's own path."""
    rows = conn.execute("""
        SELECT s.slug, s.horizon, s.ts, s.seconds_left, s.anchor_price, s.btc_price,
               s.up_ask, s.up_bid, t.up_win
        FROM pm_round_snapshots s
        JOIN pm_round_settlements t USING (slug)
        WHERE s.up_ask IS NOT NULL AND s.up_ask > 0 AND s.up_ask < 1
          AND s.btc_price IS NOT NULL AND s.anchor_price IS NOT NULL
          AND s.seconds_left > 0 AND s.seconds_left <= s.horizon * 60
          AND t.up_win IS NOT NULL
        ORDER BY s.slug, s.ts
    """).fetchall()
    by_round = defaultdict(list)
    for r in rows:
        by_round[r[0]].append(r)
    return by_round


def main() -> int:
    if not Path(DB).exists():
        print(f"no execution database at {DB}")
        return 2

    conn = duckdb.connect(DB, read_only=True)
    try:
        by_round = load(conn)
    finally:
        conn.close()

    if not by_round:
        print("no settled rounds with live snapshots")
        return 2

    print("=" * 78)
    print("STRUCTURAL FAIR VALUE vs THE EXECUTABLE ASK")
    print("=" * 78)

    model_pairs, market_pairs, records = [], [], []
    skipped_short, skipped_sigma = 0, 0

    for slug, snaps in by_round.items():
        if len(snaps) < MIN_SNAPSHOTS_PER_ROUND:
            skipped_short += 1
            continue
        # Sigma from THIS round's own path. Using a global sigma would leak the volatility of
        # rounds that had not happened yet into the ones being priced.
        sigma = sigma_from_path([s[5] for s in snaps], [s[2] for s in snaps])
        if sigma is None or sigma <= 0:
            skipped_sigma += 1
            continue
        for slug_, horizon, ts, secs, anchor, btc, ask, bid, up_win in snaps:
            fair = structural_p_up(btc, anchor, secs, sigma)
            if fair is None:
                continue
            y = 1.0 if up_win else 0.0
            model_pairs.append((fair, y))
            market_pairs.append((float(ask), y))
            records.append({"slug": slug_, "horizon": int(horizon), "seconds_left": float(secs),
                            "fair": fair, "ask": float(ask), "y": y})

    if not records:
        print("no gradeable snapshots after sigma estimation")
        return 2

    n_rounds = len({r["slug"] for r in records})
    print(f"\n{len(records):,} snapshots across {n_rounds:,} settled rounds"
          f"   (skipped {skipped_short} short, {skipped_sigma} without a sigma)")

    print("\n1. SKILL - proper scores against the market's OWN implied probability")
    print("-" * 78)
    mb, kb = brier(model_pairs), brier(market_pairs)
    ml, kl = log_loss(model_pairs), log_loss(market_pairs)
    base = sum(y for _, y in model_pairs) / len(model_pairs)
    print(f"  base rate (share of UP settlements)   {base:.4f}")
    print(f"  {'':<26}{'structural':>12}{'market ask':>13}{'verdict':>26}")
    print(f"  {'Brier (lower better)':<26}{mb:>12.5f}{kb:>13.5f}"
          f"{('MODEL better' if mb < kb else 'MARKET better'):>26}")
    print(f"  {'log loss (lower better)':<26}{ml:>12.5f}{kl:>13.5f}"
          f"{('MODEL better' if ml < kl else 'MARKET better'):>26}")

    print("\n  structural calibration (bin, n, mean forecast, realised):")
    for lo, hi, n, mp, my in calibration_table(model_pairs):
        flag = "" if abs(mp - my) < 0.05 else "   <- off"
        print(f"    [{lo:.1f},{hi:.1f})  n={n:>6,}  forecast {mp:.3f}  realised {my:.3f}{flag}")

    print("\n2. EDGE - where they disagree, who is right?")
    print("-" * 78)
    for lo, hi in ((0.02, 0.05), (0.05, 0.10), (0.10, 1.00)):
        sel = [r for r in records if lo <= (r["fair"] - r["ask"]) < hi]
        if len(sel) < 50:
            print(f"  model over market by [{lo:.0%},{hi:.0%}): n={len(sel)} (too few)")
            continue
        hit = sum(r["y"] for r in sel) / len(sel)
        avg_ask = sum(r["ask"] for r in sel) / len(sel)
        print(f"  model over market by [{lo:.0%},{hi:.0%}): n={len(sel):>6,}  "
              f"UP settled {hit:.4f}  avg ask {avg_ask:.3f}  "
              f"{'CONFIRMS' if hit > avg_ask else 'REFUTES'} the model")

    print("\n3. MONEY - does anything survive the real taker fee?")
    print("-" * 78)
    print(f"  {'min raw edge':<16}{'n':>8}{'gross/share':>14}{'fee/share':>12}"
          f"{'NET/share':>12}{'verdict':>12}")
    for threshold in (0.02, 0.03, 0.05, 0.08):
        sel = [r for r in records if (r["fair"] - r["ask"]) >= threshold]
        if len(sel) < 50:
            print(f"  {threshold:<16.0%}{len(sel):>8}  too few")
            continue
        gross = sum(r["y"] - r["ask"] for r in sel) / len(sel)
        fee = sum(taker_fee_per_share(r["ask"]) for r in sel) / len(sel)
        net = gross - fee
        print(f"  {threshold:<16.0%}{len(sel):>8,}{gross * 100:>13.2f}c"
              f"{fee * 100:>11.2f}c{net * 100:>11.2f}c"
              f"{('PROFIT' if net > 0 else 'LOSS'):>12}")

    best = [r for r in records if (r["fair"] - r["ask"]) >= 0.05]
    if len(best) >= 50:
        rng = random.Random(20260808)
        rounds = sorted({r["slug"] for r in best})
        per_round = defaultdict(list)
        for r in best:
            per_round[r["slug"]].append(r["y"] - r["ask"] - taker_fee_per_share(r["ask"]))
        # Bootstrap over ROUNDS, not snapshots: snapshots inside one round share an outcome,
        # so resampling them independently would shrink the interval by a factor of the
        # snapshot count and manufacture significance.
        means = []
        for _ in range(2000):
            pick = [rng.choice(rounds) for _ in rounds]
            vals = [v for s in pick for v in per_round[s]]
            means.append(sum(vals) / len(vals))
        means.sort()
        lo = means[int(0.05 * len(means))]
        print(f"\n  round-clustered bootstrap on the >=5c bucket "
              f"({len(rounds)} rounds): 5th pct net = {lo * 100:+.2f}c/share")
        print(f"  {'LOWER BOUND IS POSITIVE' if lo > 0 else 'LOWER BOUND IS NEGATIVE'}"
              f" -> {'a taker lane may exist' if lo > 0 else 'no taker lane on this evidence'}")

    print("\n" + "=" * 78)
    print("A model that cannot beat the ask on proper scores has no taker edge, however")
    print("good its accuracy looks. The ask is the competitor, not 50%.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
