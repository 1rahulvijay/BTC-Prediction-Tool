"""Score LIQUIDITY_VACUUM_CONTINUATION_V1 on the frozen protocol. Scored ONCE.

Every parameter here is read from the frozen preregistration, not chosen now:

    5s vacuum window · 0.50 depth ratio · 30s continuation window · 1s anchor grid
    move_during == 0 EXCLUDED · replenishment within 60s
    2026-08-02 is DESIGN_ONLY and contributes nothing to the endpoint

    PASS  day-block LB95(lift) > +0.15  AND  >= 5 days  AND  no day > 40% of episodes

The baseline is the SAME-DAY unconditional rate, not an assumed 50%.

    python research/score_liquidity_vacuum_continuation_v1.py
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bybit_l2_maker_v1 import L2_DIR, SYMBOL, replay  # noqa: E402

PROTOCOL = "LIQUIDITY_VACUUM_CONTINUATION_V1"
DESIGN_ONLY_DAY = "2026-08-02"
GRID_MS = 1_000
VACUUM_LAG_S = 5
VACUUM_RATIO = 0.50
CONT_S = 30
REPLENISH_WINDOW_S = 60
LIFT_FLOOR = 0.15
MIN_DAYS = 5
MAX_DAY_SHARE = 0.40
SEED, RESAMPLES = 20260804, 2000
MOVES_USD = (10.0, 25.0, 50.0)


def grid_for(path: Path):
    out, nxt = [], None
    for ts, book in replay(path):
        if nxt is not None and ts < nxt:
            continue
        try:
            b, bs, a, asz = book.best()
        except Exception:
            continue
        nxt = ts + GRID_MS
        out.append((ts, (b + a) / 2.0, min(bs, asz)))
    return out


def score_day(path: Path) -> dict | None:
    g = grid_for(path)
    n = len(g)
    if n < 3600:
        return None
    ts = [x[0] for x in g]
    mid = [x[1] for x in g]
    dep = [x[2] for x in g]

    def at(t):
        lo, hi = 0, n
        while lo < hi:
            m = (lo + hi) // 2
            if ts[m] < t:
                lo = m + 1
            else:
                hi = m
        return lo if lo < n else None

    uncond, cond, after_moves = [], [], []
    for i in range(n):
        j5 = at(ts[i] + VACUUM_LAG_S * 1000)
        if j5 is None:
            continue
        j35 = at(ts[j5] + CONT_S * 1000)
        if j35 is None:
            continue
        during = mid[j5] - mid[i]
        if during == 0.0:                       # sign undefined - EXCLUDED, not a coin flip
            continue
        after = mid[j35] - mid[j5]
        cont = 1.0 if during * after > 0 else 0.0
        uncond.append(cont)

        if dep[i] <= 0 or dep[j5] > VACUUM_RATIO * dep[i]:
            continue                            # not a vacuum
        back = None                             # replenished within 60s?
        for k in range(j5, min(j5 + REPLENISH_WINDOW_S, n)):
            if dep[k] >= dep[i]:
                back = k
                break
        if back is None:
            continue
        cond.append(cont)
        after_moves.append(abs(after))

    if not cond or not uncond:
        return None
    return {
        "day": path.name.split("_")[0],
        "n_anchors": n,
        "n_uncond": len(uncond),
        "n_cond": len(cond),
        "p_uncond": statistics.mean(uncond),
        "p_cond": statistics.mean(cond),
        "lift": statistics.mean(cond) - statistics.mean(uncond),
        "median_after_usd": statistics.median(after_moves),
        "p_move": {str(x): sum(1 for m in after_moves if m >= x) / len(after_moves)
                   for x in MOVES_USD},
    }


def lb95(vals, seed=SEED, n=RESAMPLES):
    k = len(vals)
    if k < 2:
        return None
    rng = random.Random(seed)
    draws = sorted(sum(vals[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return draws[int(0.05 * n)]


def main() -> int:
    files = sorted(p for p in L2_DIR.glob(f"*_{SYMBOL}_ob200.data.zip")
                   if not p.name.startswith(DESIGN_ONLY_DAY))
    print("=" * 88)
    print(f"SCORING {PROTOCOL}   (scored once, per the frozen protocol)")
    print("=" * 88)
    print(f"  test days       : {len(files)}   ({DESIGN_ONLY_DAY} excluded, DESIGN_ONLY)")
    print(f"  frozen          : {VACUUM_LAG_S}s vacuum, ratio {VACUUM_RATIO}, "
          f"{CONT_S}s continuation, {GRID_MS}ms grid")
    print(f"  PASS            : LB95(lift) > +{LIFT_FLOOR:.2f}, >={MIN_DAYS} days, "
          f"no day > {MAX_DAY_SHARE:.0%} of episodes\n")

    rows = []
    for f in files:
        r = score_day(f)
        if r:
            rows.append(r)
            print(f"  {r['day']}  anchors {r['n_anchors']:>6,}  "
                  f"vacuum-episodes {r['n_cond']:>6,}  "
                  f"P(cont|vac) {r['p_cond']:6.2%}  base {r['p_uncond']:6.2%}  "
                  f"lift {r['lift']:+7.2%}")
        else:
            print(f"  {f.name.split('_')[0]}  NO QUALIFYING DATA")

    if len(rows) < MIN_DAYS:
        print(f"\n  INSUFFICIENT DAYS: {len(rows)} < {MIN_DAYS}. Not scored.")
        return 1

    lifts = [r["lift"] for r in rows]
    tot_ep = sum(r["n_cond"] for r in rows)
    max_share = max(r["n_cond"] for r in rows) / tot_ep
    lb = lb95(lifts)
    point = statistics.mean(lifts)

    print("\n" + "-" * 88)
    print(f"  days                : {len(rows)}")
    print(f"  total vacuum episodes: {tot_ep:,}")
    print(f"  largest day share   : {max_share:.1%}  (limit {MAX_DAY_SHARE:.0%})")
    print(f"  lift point estimate : {point:+.2%}")
    print(f"  lift day-block LB95 : {lb:+.2%}   (seed {SEED}, {RESAMPLES} resamples)")

    gates = {
        "LB95 > +15pp": lb is not None and lb > LIFT_FLOOR,
        f">= {MIN_DAYS} days": len(rows) >= MIN_DAYS,
        f"no day > {MAX_DAY_SHARE:.0%}": max_share <= MAX_DAY_SHARE,
    }
    print()
    for k, v in gates.items():
        print(f"    {'PASS' if v else 'FAIL'}  {k}")
    verdict = "PASS" if all(gates.values()) else "FAIL"
    print(f"\n  VERDICT: {verdict}")

    print("\n  MATERIALITY (declared in advance: expected statistically positive,")
    print("  economically insufficient)")
    med = statistics.median([r["median_after_usd"] for r in rows])
    print(f"    median |move| after vacuum : ${med:.2f}  ({med / 63000 * 1e4:.2f} bps)")
    for x in MOVES_USD:
        p = statistics.mean([r["p_move"][str(x)] for r in rows])
        print(f"    P(|move| >= ${x:>5.0f} in {CONT_S}s) : {p:6.2%}   "
              f"({x / 63000 * 1e4:.1f} bps)")
    print("    Bybit maker round trip ~2.0 bps; measured passive adverse selection")
    print("    -0.56 bps (BYBIT_L2_MAKER_V2_TRADE_DRIVEN).")

    out = Path(__file__).resolve().parents[1] / "data" / "research" / f"{PROTOCOL}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"protocol": PROTOCOL, "verdict": verdict,
                               "lift_point": point, "lift_lb95": lb,
                               "days": rows, "gates": gates}, indent=1), encoding="utf-8")
    print(f"\n  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
