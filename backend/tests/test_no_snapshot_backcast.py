"""Today's market state must never appear in a historical feature row.

    python backend/tests/test_no_snapshot_backcast.py

THE DEFECT
    features.build_features_from_klines contained:

        def series(key, snapshot_val):
            arr = sh.get(key)
            if arr is not None and len(arr) == n:
                return np.asarray(arr, dtype=np.float64)
            return np.full(n, float(snapshot_val or 0.0))     # <- today, painted across history

    When per-candle history was absent, the CURRENT order-flow / derivatives / sentiment value
    was written into every historical row. Nothing raises, the column looks populated, and the
    model is trained on a constant that carries end-of-sample information into the start of the
    sample.

    The docstring called this "inert during training". It is not: a constant column still forms
    interactions with real columns, still shifts tree split points, and still differs between
    training and inference in a way no schema hash can see.

    The serving path had already worked around it by overlaying only the final row - but when it
    could not, it POPPED the key, which sent control back into this same fallback and produced
    the broadcast it was trying to avoid. Its own variable for those keys was named `_broadcast`.
    That is why the fix had to be here and not in the caller.

THE PROPERTY
    Changing the live snapshot may change AT MOST the final row.
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import math
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import features as F                                        # noqa: E402

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _klines(n=240, seed=3):
    rng = random.Random(seed)
    out, price, t = [], 100_000.0, 1_785_000_000
    for _ in range(n):
        price *= math.exp(rng.gauss(0, 0.0008))
        hi, lo = price * 1.0004, price * 0.9996
        out.append({"time": t, "open": price, "high": hi, "low": lo,
                    "close": price, "volume": abs(rng.gauss(20, 4))})
        t += 60
    return out


def main() -> int:
    kl = _klines()

    quiet = {"cvd_1m": 0.0, "cvd_5m": 0.0, "cvd_change": 0.0,
             "large_trade_delta": 0.0, "large_trade_imbalance": 0.0, "vpin": 0.0}
    loud = {"cvd_1m": -1_500.0, "cvd_5m": -9_000.0, "cvd_change": 750.0,
            "large_trade_delta": 4_200.0, "large_trade_imbalance": 0.93, "vpin": 0.88}

    print("no per-candle history: the snapshot may touch ONLY the final row")
    avail_a, avail_b = {}, {}
    a = F.build_features_from_klines(kl, quiet, {}, {}, availability_out=avail_a)
    b = F.build_features_from_klines(kl, loud, {}, {}, availability_out=avail_b)
    chk(a.shape == b.shape and a.shape[0] > 100,
        f"both matrices built and aligned ({a.shape})")

    diff = np.abs(np.nan_to_num(a) - np.nan_to_num(b))
    changed_rows = np.flatnonzero(diff.sum(axis=1) > 1e-12)
    last = a.shape[0] - 1
    chk(set(changed_rows.tolist()) <= {last},
        f"changing the live snapshot changed rows {changed_rows.tolist()[:8]} - only the final "
        f"row ({last}) may move")
    chk(diff[:last].max() < 1e-12,
        f"every historical row is byte-identical under a wildly different snapshot "
        f"(max delta {diff[:last].max():.3e})")
    chk(diff[last].max() > 0,
        "while the final row DOES carry the live value - the current bar is the one instant "
        "the snapshot actually describes")

    print("the degradation is reported, not inferred")
    chk(avail_a.get("degraded") is True and avail_a.get("degraded_live_signal_keys"),
        f"missing history is named ({len(avail_a.get('degraded_live_signal_keys') or [])} keys)")
    chk("cvd_1m" in (avail_a.get("degraded_live_signal_keys") or []),
        "including the order-flow columns the serving path used to broadcast")

    print("real per-candle history is used unchanged")
    n = len(kl)
    rng = np.random.default_rng(5)
    hist = {"cvd_1m": rng.normal(0, 500, n), "cvd_5m": rng.normal(0, 900, n),
            "cvd_change": rng.normal(0, 100, n),
            "large_trade_delta": rng.normal(0, 300, n),
            "large_trade_imbalance": rng.uniform(-1, 1, n), "vpin": rng.uniform(0, 1, n)}
    avail_h = {}
    h1 = F.build_features_from_klines(kl, quiet, {}, {}, signal_history=hist,
                                      availability_out=avail_h)
    h2 = F.build_features_from_klines(kl, loud, {}, {}, signal_history=hist,
                                      availability_out={})
    supplied = set(hist)
    still_degraded = supplied & set(avail_h.get("degraded_live_signal_keys") or [])
    chk(not still_degraded,
        f"none of the SUPPLIED keys is reported degraded (other columns in this 136-feature "
        f"matrix legitimately have no history in this fixture): {sorted(still_degraded)}")
    chk(np.abs(np.nan_to_num(h1) - np.nan_to_num(h2)).max() < 1e-12,
        "and the snapshot then has NO effect at all - history wins on every row, including "
        "the last")
    chk(np.abs(np.nan_to_num(h1) - np.nan_to_num(a)).sum() > 0,
        "history genuinely produces a different matrix from the no-history case, so this "
        "test is not comparing two identical code paths")

    print("the broadcast is gone from the source")
    # AST, not substring. The fix's own docstring QUOTES the removed line in order to record
    # what was wrong with it, so a raw text search fails on the sentence documenting the fix.
    # Third time this trap has fired in this repository; it is stripped properly here.
    import ast as _ast

    src = (Path(__file__).resolve().parents[1] / "features.py").read_text(encoding="utf-8")
    _doc: set[int] = set()
    for _node in _ast.walk(_ast.parse(src)):
        if not isinstance(_node, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef,
                                  _ast.ClassDef)):
            continue
        _body = getattr(_node, "body", None)
        if _body and isinstance(_body[0], _ast.Expr):
            _v = _body[0].value
            if isinstance(_v, _ast.Constant) and isinstance(_v.value, str):
                _doc.update(range(_v.lineno, (_v.end_lineno or _v.lineno) + 1))
    code = chr(10).join(
        ln for i, ln in enumerate(src.splitlines(), start=1)
        if i not in _doc and not ln.strip().startswith("#"))
    chk("np.full(n, float(snapshot_val or 0.0)" not in code,
        "the backward-broadcast call no longer exists")
    chk("_degraded_keys.append(key)" in code,
        "and the fallback records the key instead of hiding it")

    print("\nNO SNAPSHOT BACKCAST:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
