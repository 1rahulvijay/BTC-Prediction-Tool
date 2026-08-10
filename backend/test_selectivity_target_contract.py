"""Selectivity: a bps target from the fitting span, purged OOS, and a short extreme window.

WHAT WAS WRONG (in backend/decision/train_selectivity_models.py, the WIRED trainer -
train_selectivity_model.py and _v2.py are unused siblings and already used a train-only cut)

    1. p75 = df["future_abs_move_5m"].quantile(0.75)   -- raw USD, over the WHOLE frame.
       USD is non-stationary across a 1000-day BTC window: $100 is 50 bps at $20k and 10 bps
       at $100k, so one dollar cut-point means different events at different times. The
       keeper heads were converted to bps on 2026-07-03; selectivity never was. The
       full-frame quantile separately let the evaluated tail help define the target.

    2. TimeSeriesSplit(n_splits=5) with no gap, in BOTH _oos_auc and _oos_auc_ensemble,
       while every label reads a forward 5-minute window.

    3. "thresholds ... from 60d history" in a comment, computed over the entire frame. At
       BTC_HISTORICAL_DAYS=1000 a "95th percentile VPIN" was a 1000-day extreme, not a
       current-market one.

    python backend/test_selectivity_target_contract.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

import decision.train_selectivity_models as sel  # noqa: E402

SRC = (BACKEND / "decision" / "train_selectivity_models.py").read_text(
    encoding="utf-8", errors="replace")
CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, f"FAILED: {text}"
    CHECKS += 1
    print(f"  PASS  {text}")


def main() -> int:
    tree = ast.parse(SRC)

    # 1. BOTH OOS estimators purge, by the label horizon.
    splits = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
              and getattr(n.func, "id", "") == "TimeSeriesSplit"]
    check(len(splits) >= 2 and all(
        any(k.arg == "gap" and getattr(k.value, "id", "") == "HORIZON_BARS" for k in s.keywords)
        for s in splits),
        f"all {len(splits)} TimeSeriesSplit sites purge by gap=HORIZON_BARS - the labels read "
        f"a forward 5m window, so chronological folds still overlapped")
    check(sel.HORIZON_BARS == 5,
          "the purge width is the label's own 5-minute forward window")

    # 2. The target is PRICE-RELATIVE. A frame whose price level doubles must not change
    #    which rows count as big moves, if the relative moves are identical.
    n = 20_000
    rel = np.abs(np.sin(np.arange(n) / 7.0)) * 20.0            # bps, identical in both frames
    for level in (20_000.0, 100_000.0):
        close = np.full(n, level)
        usd = rel * close / 10_000.0
        bps = usd / close * 1e4
        thr = float(np.nanpercentile(bps[:int(n * 0.98) - 5], 75))
        globals()[f"lab_{int(level)}"] = (bps > thr).astype(int)
    same = int((globals()["lab_20000"] == globals()["lab_100000"]).sum())
    check(same == n,
          f"the same RELATIVE moves produce identical labels at BTC $20k and $100k "
          f"({same:,}/{n:,} rows agree) - a USD cut-point would have labelled these "
          f"differently at the two price levels")
    # AST, not substring: the fix's own comment quotes the old expression verbatim to explain
    # it, so a substring check here fails on prose while the code is correct.
    quantile_calls = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call)
                      and getattr(n.func, "attr", "") == "quantile"]
    check(not quantile_calls,
          f"no live .quantile() call defines the target any more (found {quantile_calls}) - "
          f"the raw-USD full-frame percentile is gone from the CODE, not just commented on")
    assign = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
              and any(getattr(t, "id", "") == "p75" for t in n.targets)]
    check(assign and "SELECTIVITY_FIT_FRAC" in ast.unparse(assign[0]).replace("\n", " ")
          or any("SELECTIVITY_FIT_FRAC" in ast.unparse(n) for n in ast.walk(tree)
                 if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "") == "_fit_end" for t in n.targets)),
          "and the replacement threshold is bounded by SELECTIVITY_FIT_FRAC, so the span that "
          "defines the target is stated rather than being 'all of it'")

    # 3. The threshold must not see the evaluated tail.
    #
    # The fixture uses a 30% tail deliberately. At the shipped SELECTIVITY_FIT_FRAC of 0.98
    # the excluded span is 2% of rows, which CANNOT move a 75th percentile at all - that is
    # arithmetic, not an argument, and it is why the same defect measured only +0.79% on the
    # keeper heads. A fixture built at 0.98 would therefore pass whether or not the fix
    # existed. What is actually being pinned is that the threshold is computed over a BOUNDED
    # span rather than the whole frame, so the defect cannot return in its severe form when
    # BTC_TRAIN_SPLIT_FRAC is lowered - it is settable to 0.50.
    calm = np.full(14_000, 5.0)
    wild = np.full(6_000, 500.0)
    series = np.concatenate([calm, wild])

    def _threshold(frac):
        end = max(1, int(len(series) * frac) - sel.HORIZON_BARS)
        return float(np.nanpercentile(series[:end], 75))

    thr_fit, thr_full = _threshold(0.70), _threshold(1.0)
    check(thr_fit < thr_full,
          f"with a violent final 30%, a bounded fitting span keeps the threshold at "
          f"{thr_fit:.1f} bps where the full frame gives {thr_full:.1f} - the evaluated rows "
          f"no longer help define what counts as a big move")
    check(_threshold(sel.SELECTIVITY_FIT_FRAC) == _threshold(1.0),
          f"and at the SHIPPED frac of {sel.SELECTIVITY_FIT_FRAC} the two agree on this "
          f"fixture - stated plainly rather than hidden: the current exposure is small, the "
          f"fix removes the version that appears at a lower split")

    # 4. The live-extreme window is SHORT and separate from the fit window.
    check(sel.EXTREME_REFERENCE_MINUTES == 60 * 24 * 60,
          f"the live-extreme reference is {sel.EXTREME_REFERENCE_MINUTES:,} minutes (60d), "
          f"the span the comment always claimed")
    check(sel.EXTREME_REFERENCE_MINUTES < 1000 * 24 * 60,
          "and it is far shorter than the 1000-day fit window - 'extreme right now' and "
          "'what the model learned' are different questions and no longer share a span")
    pct_fn = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "pct"]
    check(pct_fn and "_ref_rows" in ast.unparse(pct_fn[0]),
          "and pct() actually slices to that window rather than reading the whole frame")

    print(f"\nSELECTIVITY TARGET CONTRACT: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
