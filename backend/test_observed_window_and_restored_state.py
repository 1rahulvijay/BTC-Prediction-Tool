"""
5.2, P0-15 and P0-9 - three places where the app knew less than it appeared to.

5.2   A first-touch label is computed over 1-minute bars selected by
      `entry_ts < open_ms <= verify_ts`, while a prediction is issued at an arbitrary
      second. The scan called this "shorter than the declared horizon". It is not
      shorter - it is SHIFTED, and both ends cost something.

P0-15 The confidence percentile gate reads a module-level deque that started empty on
      every boot, so a restart left the learned bar unbounded until it refilled.

P0-9  Direction and magnitude cannot contradict each other in the served object, because
      the target is derived from the direction. What was lost is the magnitude head's own
      sign - discarded by `abs()` with no record that the two heads disagreed.

Run directly:  python backend/test_observed_window_and_restored_state.py
"""

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FAILURES = []
BASE = 1_800_000_000_000          # a minute boundary


def chk(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def bars(n, start, hi=100.0, lo=100.0):
    return [{"time": (start + i * 60_000) // 1000, "open": 100.0, "high": hi,
             "low": lo, "close": 100.0, "is_closed": True} for i in range(n)]


def test_observed_window():
    print("\n5.2 a graded row records the interval it actually WATCHED")
    import target_contract as tc

    measured = []
    for offset in (0, 20_000, 40_000, 59_000):
        entry_ts = BASE + offset
        r = tc.grade(contract=tc.FIRST_TOUCH_TRIPLE_BARRIER_V1, entry=100.0,
                     threshold=0.002, klines=bars(12, BASE),
                     entry_ts=entry_ts, verify_ts=entry_ts + 300_000)
        measured.append((offset, r))

    chk(all(r.observed_start_ms == BASE + 60_000 for _, r in measured),
        "every entry inside the same minute watches the SAME first bar - a touch between "
        "the entry and that bar's open is invisible to the label")
    chk(all(r.observed_end_ms == BASE + 360_000 for _, r in measured),
        "and the same last bar, whose close runs past the horizon end")
    shifts = [r.window_shift_ms for _, r in measured]
    chk(shifts == [60_000, 40_000, 20_000, 1_000],
        f"so the observed window is SHIFTED forward by {shifts[0] // 1000}s down to "
        f"{shifts[-1] // 1000}s, never shortened - the scan's stated mechanism was wrong, "
        f"and the tail is the half that matters: a barrier touched after the horizon "
        f"ended is attributed to a position that had already closed")

    lengths = {r.observed_end_ms - r.observed_start_ms for _, r in measured}
    chk(lengths == {300_000},
        "the length is exactly the declared horizon in every case, which is why "
        "tightening the selection to bars fully inside would be a REGRESSION: it would "
        "grade a 5-minute contract over 4 minutes")

    irregular = [{"time": t // 1000, "open": 100.0, "high": 100.0, "low": 100.0,
                  "close": 100.0, "is_closed": True}
                 for t in (BASE + 60_000, BASE + 120_000, BASE + 420_000)]
    r = tc.grade(contract=tc.FIRST_TOUCH_TRIPLE_BARRIER_V1, entry=100.0, threshold=0.002,
                 klines=irregular, entry_ts=BASE, verify_ts=BASE + 600_000)
    chk(r.observed_end_ms is None and r.window_shift_ms is None,
        "on an irregular list the cadence is NOT guessed - `min(diffs)` on such a list is "
        "what made the earlier P0-4 attempt unsafe, and a wrong duration would misreport "
        "the shift rather than leave it unknown")

    touched = tc.grade(contract=tc.FIRST_TOUCH_TRIPLE_BARRIER_V1, entry=100.0,
                       threshold=0.002, klines=bars(12, BASE, hi=101.0),
                       entry_ts=BASE + 20_000, verify_ts=BASE + 320_000)
    chk(touched.direction == "UP" and touched.window_shift_ms == 40_000,
        "a TOUCHING row carries it too, not only a timeout - both graded exits were "
        "threaded, which a fix applied to one branch would have missed")


def test_restored_percentile_window():
    print("\nP0-15 the percentile window is restored, scoped and in the right namespace")
    src = (BACKEND / "database.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "fetch_effective_confidence_window")
    body = ast.get_source_segment(src, fn) or ""
    body_no_doc = body.replace(ast.get_docstring(fn) or "", "")

    # The SELECT specifically - a WHERE clause mentioning the same expression is not the
    # value being loaded, and asserting on the whole body let a `SELECT confidence` mutant
    # survive.
    chk("SELECT COALESCE(calibrated_confidence, confidence)" in body_no_doc,
        "the restored VALUE is the EFFECTIVE confidence - calibrated where one exists, "
        "raw otherwise - because loading raw scores into a window a calibrated bar is "
        "compared against is defect 5.21 in a new place")
    chk("release_id = ?" in body_no_doc,
        "and it is scoped to the serving release: a percentile is a claim about ONE "
        "model's distribution, so mixing releases is the pooled-calibration defect")
    chk("if not release_id:" in body_no_doc and "return []" in body_no_doc,
        "an unknown release restores NOTHING rather than everything - the gate already "
        "handles an empty window, and a window of unattributable rows is worse than none")

    srv = (BACKEND / "server.py").read_text(encoding="utf-8")
    chk("fetch_effective_confidence_window" in srv,
        "and the server actually calls it at boot - the window is a module global that "
        "starts empty, which is exactly why the bar ran unbounded after every restart")
    boot = srv[srv.index("ab_restored = ab_runner.restore_from_db()"):]
    boot = boot[:boot.index("def ") if "def " in boot else 4000]
    chk("_win.clear()" in boot,
        "the window is cleared before refilling, so a restore can never double-count "
        "against whatever a partially-started process already appended")


def test_magnitude_sign_recorded():
    print("\nP0-9 a disagreement between the two heads leaves a trace")
    src = (BACKEND / "model.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "generate_ensemble_prediction")
    body = ast.get_source_segment(src, fn) or ""
    stripped = body.replace(ast.get_docstring(fn) or "", "")

    chk("_signed_move_frac = frac" in stripped,
        "the magnitude head's SIGNED output is captured before `abs()` discards it")
    chk('"magnitudeSignAgrees"' in stripped,
        "and every prediction says whether that sign agreed with the direction served")

    # The flag must be three-valued: unknown is not agreement.
    chk("None if _mag_sign == 0" in stripped,
        "a row where the head did not run reports None - an unmeasured agreement must "
        "not be published as agreement, which is the same rule the contract, the "
        "cascade and the A/B evidence scope all now follow")

    # Evaluate the SHIPPED expression, not a copy of it. Reimplementing the arithmetic
    # here would test this file rather than model.py - a mutant that inverted the real
    # mapping survived exactly that mistake.
    expr = None
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "magnitudeSignAgrees":
                    expr = compile(ast.Expression(value), "<model.py>", "eval")
    if expr is None:
        chk(False, "the magnitudeSignAgrees expression could not be located in model.py")
        return

    def agrees(sign, direction):
        mag = 1 if sign > 0 else (-1 if sign < 0 else 0)
        return eval(expr, {}, {"_mag_sign": mag, "direction": direction})
    chk(agrees(0.004, "UP") is True and agrees(-0.004, "UP") is False
        and agrees(-0.004, "DOWN") is True and agrees(0.004, "DOWN") is False,
        "and the mapping is the right way round in all four combinations")
    chk(agrees(0.0, "UP") is None and agrees(0.004, "NEUTRAL") is None,
        "with no claim made when there is nothing to compare")


def main():
    print("=" * 78)
    print("OBSERVED WINDOW AND RESTORED STATE (5.2 / P0-15 / P0-9)")
    print("=" * 78)
    test_observed_window()
    test_restored_percentile_window()
    test_magnitude_sign_recorded()
    print("\n" + "=" * 78)
    if FAILURES:
        print(f"OBSERVED WINDOW AND RESTORED STATE: FAIL ({len(FAILURES)})")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("OBSERVED WINDOW AND RESTORED STATE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
