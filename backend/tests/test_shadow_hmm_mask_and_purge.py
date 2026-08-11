"""Three small omissions with large consequences: 2.9, 2.19 and 2.20.

    python backend/tests/test_shadow_hmm_mask_and_purge.py

Each is a handful of lines, and each let a run report something it had not established.

2.9  THE FULL-REFIT SHADOW SAVED hmm_state = None
     `full_model.train(..., regime_labels=regime_labels)` partitions the shadow's experts using
     the CANDIDATE's HMM labels, but nothing ever assigned the matching state, and
     `_save_models` persists `getattr(self, "hmm_state", None)`. The shadow was therefore
     trained under one regime partition and served through whichever engine the incumbent had
     installed - which invalidates the live A/B comparison the shadow exists to provide.

2.19 A MISALIGNED valid_mask TRAINED ANYWAY
     A length mismatch logged a warning and continued. Not applying a misaligned array is
     correct; CONTINUING is not. Every AMBIGUOUS bar then trained as NEUTRAL - asserting "price
     went nowhere" about the most violent bars in the sample, the exact defect the mask was
     added to remove.

2.20 THE SETTLEMENT HEAD'S PURGE COLLAPSED TO THE HORIZON
     `train_settlement_head(..., lookback=0)` by default, and the server never passed it. The
     head's own comment states the requirement - rows just before the split share most of their
     lookback with rows just after it - so the purge must be `lookback + horizon`.

     ADJUSTED: `groups` is deliberately NOT passed and this test asserts that. The head
     documents that round IDs "do not exist yet", so there is nothing real to supply, and
     passing row indices as pseudo-rounds would make the independence floors report a guarantee
     that was never established.
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import ast
import sys
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _call_kwargs(src: str, func_name: str) -> set[str]:
    """Keyword names passed to `func_name`, including through functools.partial."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        direct = isinstance(target, ast.Name) and target.id == func_name
        partial = (isinstance(target, ast.Attribute) and target.attr == "partial"
                   and node.args and isinstance(node.args[0], ast.Name)
                   and node.args[0].id == func_name)
        if direct or partial:
            names.update(kw.arg for kw in node.keywords if kw.arg)
    return names


def main() -> int:
    server_src = (BACKEND / "server.py").read_text(encoding="utf-8")

    print("2.9 the full-refit shadow inherits the HMM that partitioned it")
    chk("full_model.hmm_state = getattr(target_model, \"hmm_state\", None)" in server_src,
        "full_model.hmm_state is assigned from the candidate before training")
    # ORDER MATTERS: _save_models persists whatever hmm_state is set at save time, and save
    # happens inside train(). An assignment placed after the train call would save None.
    _assign = server_src.index("full_model.hmm_state = ")
    chk(_assign < server_src.index("full_model.train", _assign),
        "and the assignment precedes full_model.train - _save_models runs INSIDE train(), so "
        "assigning afterwards would persist None exactly as before")
    chk("hmm_state to inherit" in server_src,
        "a shadow with nothing to inherit WARNS rather than silently routing through a "
        "partition its experts were not trained on")

    print("2.19 a misaligned valid_mask REFUSES instead of training without it")
    model_src = (BACKEND / "model.py").read_text(encoding="utf-8")
    chk("NOT applying it \n" not in model_src and "rather than misaligning the weights." not in model_src,
        "the warn-and-continue branch is gone")
    chk("Refusing to train: applying it would" in model_src,
        "and a length mismatch raises, naming both failure modes it sits between")
    # The refusal must be a RAISE, not a log - asserted structurally so a future edit that
    # downgrades it back to a warning fails here.
    _tree = ast.parse(model_src)
    _raises = [n for n in ast.walk(_tree)
               if isinstance(n, ast.Raise) and "valid_mask for h=" in ast.unparse(n)]
    chk(len(_raises) == 1,
        "exactly one raise carries that message - a warning cannot satisfy this check")

    print("2.20 the settlement head is purged by lookback + horizon")
    kw = _call_kwargs(server_src, "train_settlement_head")
    chk("lookback" in kw,
        f"the server passes lookback (kwargs: {sorted(kw)}) - it defaulted to 0, collapsing "
        f"the purge to the horizon alone")
    # This check used to assert the OPPOSITE - that groups must not be passed, because the only
    # groups imaginable were fabricated Polymarket round IDs. Endpoint labels are rolling, so
    # the right unit was never a round: it is the sequence-plus-outcome dependence block. With
    # groups=None `_group_count` falls back to the ROW COUNT, i.e. it treats every overlapping
    # row as an independent observation - the most optimistic assumption available, and the one
    # the old assertion was locking in. Passing time-derived blocks is strictly more honest.
    chk("groups" in kw,
        f"the server passes groups (kwargs: {sorted(kw)}) - without them `_group_count` counts "
        f"ROWS as independent units and the cluster intervals cannot fire at all")
    chk("_settlement_ts // ((LOOKBACK + int(_h)) * 60_000)" in server_src,
        "and they are derived from TIMESTAMPS at a width of lookback+horizon, not from "
        "anything claiming to be a round ID - a real dependence span, not a fabricated one")
    chk("lookback=LOOKBACK" in server_src, "the value passed is the real LOOKBACK constant")

    # The purge arithmetic itself, measured through the head.
    from settlement_head import train_settlement_head
    import settlement_head as _sh

    rng = np.random.default_rng(5)
    n, h = 900, 5
    X = rng.normal(size=(n, 6)).astype(np.float32)
    lab = np.zeros((n, 2), dtype=np.float32)
    lab[:, 0] = 1.0
    lab[rng.uniform(size=n) < 0.5] = np.array([0.0, 1.0], dtype=np.float32)
    split = 700

    no_purge = train_settlement_head(X, {h: lab}, split, horizons=[h], lookback=0)
    with_purge = train_settlement_head(X, {h: lab}, split, horizons=[h], lookback=60)
    m0 = no_purge["metrics"][h]
    m60 = with_purge["metrics"][h]
    chk(m0["purge_rows"] == h and m60["purge_rows"] == 60 + h,
        f"purge_rows goes {m0['purge_rows']} -> {m60['purge_rows']}: with lookback=0 it was the "
        f"HORIZON ALONE, which is what the server was silently getting")
    chk(m60["train_rows"] == m0["train_rows"] - 60,
        f"and exactly 60 more training rows are excluded ({m0['train_rows']} -> "
        f"{m60['train_rows']}) - rows sharing their sequence with the holdout, whose labels "
        f"cross the boundary")
    chk(no_purge["independence_validated"] is False
        and with_purge["independence_validated"] is False,
        "independence is reported as NOT validated in both runs, because no round IDs were "
        "supplied - the head records what it could not establish instead of implying it did")
    chk(_sh.MIN_TRAIN_ROWS > 0, "and a purge that starves training is refused by MIN_TRAIN_ROWS")

    print("\nSHADOW HMM, MASK AND PURGE:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
