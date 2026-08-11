"""A/B promotion uncertainty must resample time clusters, not overlapping rows."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from ab_testing import MIN_BOOTSTRAP_DAYS, _paired_day_bootstrap_lower  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  PASS  {message}")


def main() -> int:
    day_ms = 86_400_000
    one_day = [(1_700_000_000_000 + index, False, True) for index in range(1000)]
    lower, days = _paired_day_bootstrap_lower(one_day)
    check(days == 1 and lower == 0.0,
          "one thousand overlapping rows from one day cannot produce promotion evidence")

    independent_days = []
    for day in range(MIN_BOOTSTRAP_DAYS):
        independent_days.extend([
            (1_700_000_000_000 + day * day_ms + offset, False, True)
            for offset in range(20)
        ])
    lower, days = _paired_day_bootstrap_lower(independent_days)
    check(days == MIN_BOOTSTRAP_DAYS and lower == 1.0,
          "five distinct uniformly winning days produce the exact paired lower bound")

    clustered = []
    for day in range(10):
        challenger_hit = day < 6
        clustered.extend([
            (1_700_000_000_000 + day * day_ms + offset, not challenger_hit, challenger_hit)
            for offset in range(100)
        ])
    lower, days = _paired_day_bootstrap_lower(clustered, draws=4000)
    check(days == 10 and lower < 0.0,
          "day clustering exposes uncertainty hidden by a positive row-level point estimate")

    print("\nA/B UTC-day cluster bootstrap: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
