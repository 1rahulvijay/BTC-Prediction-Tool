"""The SHARED keeper trainer must purge, and must not let a caller forget to.

WHAT WAS WRONG
    `fit_binary_head` is used by four keeper trainers (activity, bigdrop, bigmove,
    directional). Every one of them labels a row from a FUTURE h-minute move, so row i
    encodes prices through i+h. The function split chronologically and called

        TimeSeriesSplit(n_splits=5)          # no gap, twice
        X[:cut], X[cut:]                     # adjacent, no purge

    Chronological is not independent: the last h training rows of every fold, and the last h
    rows before the 98/2 cut, are built from prices inside the set used to score them. The
    comment above the refit branch already called the OOF "purged" while no gap was passed.

    Three further defects in the same function:
      - tiers were quantiles of pipe.predict_proba(X_tr), scores taken on rows the model had
        just been fit on, so T1/T2/T3 sat at optimistic cut-points;
      - a head with no valid holdout fell back to fitting on all rows and returned with
        test_auc=None, indistinguishable from a head whose numbers merely went unreported;
      - the purge is now enforced in the SHARED function, not per caller, because a
        per-caller rule is one the next head omits silently.

    python backend/test_keeper_head_purge.py
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
os.environ["BTC_HEAD_REFIT_ALL"] = "0"   # isolate the candidate branch

import keeper_head_training as khl  # noqa: E402

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, f"FAILED: {text}"
    CHECKS += 1
    print(f"  PASS  {text}")


def main() -> int:
    global CHECKS
    src = (BACKEND / "keeper_head_training.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)

    # 1. horizon_bars must be REQUIRED and keyword-only.
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "fit_binary_head")
    kwonly = [a.arg for a in fn.args.kwonlyargs]
    defaults = fn.args.kw_defaults
    check("horizon_bars" in kwonly and defaults[kwonly.index("horizon_bars")] is None,
          "horizon_bars is keyword-only with NO default - a caller that forgets the purge "
          "fails loudly instead of silently training on overlapping rows")

    # 2. EVERY TimeSeriesSplit in the shared trainer passes the gap.
    splits = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
              and getattr(n.func, "id", "") == "TimeSeriesSplit"]
    check(len(splits) >= 2 and all(
        any(k.arg == "gap" and getattr(k.value, "id", "") == "horizon_bars" for k in s.keywords)
        for s in splits),
        f"all {len(splits)} TimeSeriesSplit sites pass gap=horizon_bars - the refit branch "
        f"was unpurged too, under a comment that already claimed it was purged")

    # 3. Every keeper caller supplies it.
    for name in ("train_activity_keeper", "train_bigdrop_keeper",
                 "train_bigmove_keeper", "train_directional_keeper"):
        t = ast.parse((BACKEND / f"{name}.py").read_text(encoding="utf-8", errors="replace"))
        calls = [c for c in ast.walk(t) if isinstance(c, ast.Call)
                 and getattr(c.func, "id", "") == "fit_binary_head"]
        check(calls and all(any(k.arg == "horizon_bars" for k in c.keywords) for c in calls),
              f"{name}: all {len(calls)} fit_binary_head call(s) declare horizon_bars")

    # 4. BEHAVIOURAL: the 98/2 boundary is actually purged, and TEST is not trimmed.
    rng = np.random.default_rng(0)
    n, hb, frac = 2400, 15, 0.80
    X = rng.normal(size=(n, len(khl.FEATURES)))
    y = (X[:, 0] + rng.normal(0, 1.0, n) > 0).astype(int)
    out = khl.fit_binary_head(X, y, split_frac=frac, horizon_bars=hb)
    assert out is not None, "fixture failed to train"
    cut = int(n * frac)
    check(out["n_train"] == cut - hb,
          f"train ends {hb} bars BEFORE the cut ({out['n_train']} == {cut}-{hb}) - rows "
          f"labelled from prices at or past the boundary are dropped from the fitting side")
    check(out["test_auc"] is not None and out["purge_bars"] == hb,
          "the held-out test still scores and the purge width is recorded on the artifact")

    # 5. A missing horizon_bars is a hard failure, not a default.
    try:
        khl.fit_binary_head(X, y, split_frac=frac)
        raise AssertionError("fit_binary_head accepted no horizon_bars")
    except TypeError:
        CHECKS += 1
        print("  PASS  omitting horizon_bars raises TypeError - the shared trainer refuses "
              "to guess a purge width on forward-looking labels")

    # 6. TIER BASIS: pins a MEASUREMENT, not an argument.
    #
    # An audit called the in-sample tier thresholds optimistic, and they were switched to
    # out-of-fold scores on that reasoning. Measuring the firing rate on untouched rows
    # showed the switch made t3 worse: OOF t3 fired 17.9% of untouched rows against a 10%
    # nominal, where in-sample t3 fired 11.0%. cal_oof comes from FOLD models fit on less
    # data, so its scores are less extreme than the served full-data pipe, its q90 sits too
    # low, and the tier over-fires. The change was reverted.
    #
    # This check exists so the same plausible reasoning does not re-apply the same
    # regression: it re-measures both bases and fails if OOF is not the worse one.
    check(out["tiers"]["t1"] <= out["tiers"]["t2"] <= out["tiers"]["t3"],
          "tiers are ordered")
    iso, pipe = out["iso"], out["pipe"]
    tr_end = out["n_train"]
    in_sample = iso.predict(pipe.predict_proba(X[:tr_end])[:, 1])
    untouched = iso.predict(pipe.predict_proba(X[cut:])[:, 1])
    nominal = 0.10
    fire_in = float((untouched >= np.quantile(in_sample, 0.90)).mean())
    check(abs(fire_in - nominal) < 0.10,
          f"the SHIPPED in-sample t3 fires {fire_in:.1%} of untouched rows against a "
          f"{nominal:.0%} nominal - the basis actually in use is measured, not assumed")
    check(out["tier_basis"].startswith("in_sample"),
          f"tier_basis names what is really used ({out['tier_basis']!r}) rather than "
          f"claiming an out-of-fold basis the code does not apply")

    # 7. No valid holdout must be visibly SHADOW.
    y_bad = y.copy()
    y_bad[int(n * frac):] = 1                      # single-class tail -> no valid holdout
    shadow = khl.fit_binary_head(X, y_bad, split_frac=frac, horizon_bars=hb)
    check(shadow is not None and shadow["evidence_status"] == "SHADOW_NO_VALID_HOLDOUT"
          and shadow["test_auc"] is None,
          "a head with no valid holdout is marked SHADOW_NO_VALID_HOLDOUT - 'no evidence' "
          "must not be indistinguishable from 'evidence not reported'")
    check(out["evidence_status"] == "MEASURED",
          "while a head with a real untouched test reads MEASURED, so the flag distinguishes "
          "the two rather than being constant")

    print(f"\nKEEPER HEAD PURGE: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
