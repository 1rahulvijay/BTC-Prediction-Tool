"""Canonical research status registry - which studies may be quoted, and which are retracted.

WHY THIS EXISTS
    Five economic studies in this directory produced numbers from a non-causal join: the market
    state was selected independently of the quote, and in 93.5% of rows it was observed AFTER
    the decision it was traded against (median +8.1s, max +17.8s). Their headline results are
    retracted.

    Deleting them would destroy the record of what was measured and how it was wrong. Leaving
    them runnable is worse: they still print `+0.0430/$1, day LCB +0.0164, 2 of 3 splits`, and
    the next summary - written by a person or a model - will find that number and treat it as
    evidence. It reads exactly like a result.

    So a retracted study REFUSES TO RUN by default, prints why, and requires an explicit
    --run-retracted-study flag to reproduce history.

THE RULE
    Any study that joins a market STATE to an executable QUOTE must prove the state was
    available at the decision timestamp. backend/test_causal_join_guard.py enforces that every
    such script either declares a causal timestamp constraint or is registered here as
    RETRACTED.
"""
from __future__ import annotations

import sys

VALID = "VALID"
RETRACTED = "RETRACTED"
DIAGNOSTIC = "VALID_DIAGNOSTIC_NEGATIVE"

NONCAUSAL = "NONCAUSAL_STATE_QUOTE_JOIN"
REPLACEMENT = "research/causal_decision_join.py"

# status, reason, and whether any number in the script may be quoted as evidence.
REGISTRY: dict[str, dict] = {
    "phold_auc_and_expectancy.py": {
        "status": RETRACTED, "reason": NONCAUSAL, "replaced_by": REPLACEMENT,
        "retracted_claim": "p_hold 0.97-0.99 bucket, +0.0371/$1 with day LCB +0.0069",
        "note": "The AUC section (0.7762) is NOT affected - it joins state to outcome with no "
                "quote, so it has no decision timestamp to violate. Only the EXPECTANCY "
                "section is retracted.",
    },
    "phold_calibrated_fair_value.py": {
        "status": RETRACTED, "reason": NONCAUSAL, "replaced_by": REPLACEMENT,
        "retracted_claim": "THE CANDIDATE EDGE: +0.0430/$1, day LCB +0.0164, 2 of 3 splits",
        "note": "Causally this is 0 of 3 with negative lower bounds in every window.",
    },
    "meta_label_head_test.py": {
        "status": RETRACTED, "reason": NONCAUSAL, "replaced_by": REPLACEMENT,
        "retracted_claim": "META vs CALIBRATED expectancy comparison across 3 splits",
        "note": "The label-identity finding (net>0 == held, 100% of rows) is arithmetic and "
                "survives; the ECONOMIC comparison does not.",
    },
    "settlement_fragility_test.py": {
        "status": RETRACTED, "reason": NONCAUSAL, "replaced_by": REPLACEMENT,
        "retracted_claim": "fragility AUC +0.0699/+0.0820/+0.0595 and its expectancy arms",
        "note": "Also unsupported for a second reason: the FRAGILITY arm carries 13 features "
                "including `ask` and was compared against calibrated p_hold ALONE, so the AUC "
                "delta was never attributable to fragility. And its calibrator was fitted on "
                "in-sample predictions.",
    },
    "policy_threshold_size_test.py": {
        "status": RETRACTED, "reason": NONCAUSAL, "replaced_by": REPLACEMENT,
        "retracted_claim": "'0 of 5 beat the pre-declared control'",
        "note": "Also flawed independently: FLAT weights 1.00 while KELLY caps at 0.05, so the "
                "two sizing rules risk 20x different amounts and were never comparable. The "
                "qualitative lesson (selection on 21 days overfits) survives; the counts do not.",
    },
    "causal_decision_join.py": {
        "status": DIAGNOSTIC, "reason": "", "replaced_by": "",
        "retracted_claim": "",
        "note": "The corrected construction. Its result is a NEGATIVE (0 of 3), which is a "
                "valid finding. Classified CAUSAL_BEST_EFFORT_HISTORICAL: rule_paper_trades "
                "has one generic `ts`, so it cannot prove that ts is exactly when the stored "
                "ask was observed. A forward ledger with separate quote/state/decision "
                "timestamps is what would make this gold-standard.",
    },
}


def guard(script_name: str, argv: list[str] | None = None) -> None:
    """Refuse to run a retracted study unless history is explicitly requested."""
    entry = REGISTRY.get(script_name)
    if entry is None or entry["status"] != RETRACTED:
        return
    argv = sys.argv if argv is None else argv
    if "--run-retracted-study" in argv:
        print("=" * 96)
        print(f"REPRODUCING A RETRACTED STUDY: {script_name}")
        print(f"  reason      : {entry['reason']}")
        print(f"  retracted   : {entry['retracted_claim']}")
        print(f"  replaced by : {entry['replaced_by']}")
        print("  The numbers below are NOT evidence. They are the record of a defect.")
        print("=" * 96)
        return
    print("=" * 96)
    print(f"RETRACTED STUDY - REFUSING TO RUN: {script_name}")
    print("=" * 96)
    print(f"  reason           : {entry['reason']}")
    print(f"  retracted claim  : {entry['retracted_claim']}")
    print(f"  replaced by      : {entry['replaced_by']}")
    if entry.get("note"):
        print(f"  note             : {entry['note']}")
    print()
    print("  The market state was selected independently of the quote and was observed AFTER")
    print("  the decision in 93.5% of rows (median +8.1s). Any number this script prints")
    print("  describes a trade nobody could have made.")
    print()
    print("  To reproduce the history anyway:")
    print(f"      python research/{script_name} --run-retracted-study")
    raise SystemExit(0)


def summary_table() -> str:
    width = max(len(name) for name in REGISTRY)
    lines = [f"{'study'.ljust(width)}  status"]
    lines.append("-" * (width + 30))
    for name, entry in sorted(REGISTRY.items()):
        lines.append(f"{name.ljust(width)}  {entry['status']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary_table())
