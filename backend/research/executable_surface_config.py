"""PHASE 4 -- the FROZEN research grid for the executable-surface suite.

Canonical blueprint (2026-07-25). Everything a result depends on is declared HERE, before any
outcome is computed, and hashed. If this file changes, the hash changes and previously written
results are stamped with the OLD hash -- so a moved goalpost is always visible in the record.

Nothing in this module reads data or computes a result. It is pure declaration.

THE FEE RULE (non-negotiable, from the Phase-2 finding): every `fee_rate_bps` recorded in the
L2 snapshot is 0.0. That column is UNUSABLE. Costs are charged with the documented Polymarket
taker formula, identical to the one the live champion and paper ledger use. Trusting the
recorded zero would manufacture a fake ~1.7c/share edge on every single cell.
"""
from __future__ import annotations

import hashlib
import json

CONFIG_VERSION = "2026-07-25-executable-surface-v1"

# ---------------------------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------------------------
FEE_RATE = 0.07                 # Polymarket crypto taker rate
FEE_MODEL = "taker: fee = FEE_RATE * p * (1 - p), charged on BOTH legs at that leg's price"
TRUST_RECORDED_FEE_BPS = False  # Phase-2: recorded fee_rate_bps is all-zero -> unusable


def taker_fee(price: float, fee_rate: float = FEE_RATE) -> float:
    """Per-share taker fee. Mirrors decision_champion.polymarket_taker_fee_per_share."""
    p = max(0.0, min(1.0, float(price)))
    return round(max(0.0, float(fee_rate)) * p * (1.0 - p), 5)


# ---------------------------------------------------------------------------------------------
# The grid (frozen)
# ---------------------------------------------------------------------------------------------
HORIZONS = (5, 15)

# Seconds remaining at the DECISION instant. The engine then takes the first book at or after
# decision + latency -- never the book that produced the decision.
ENTRY_CHECKPOINTS_S = {
    5:  (240, 180, 120, 90, 60, 30),
    15: (720, 600, 480, 360, 240, 180, 120, 90, 60, 30),
}

# Contract cents, not percentages: a 20% gain on a 0.10 share and on a 0.80 share are not the
# same trade. Levels are net-of-all-fees thresholds on realized per-share P/L.
TP_CENTS = (1, 2, 3, 5, 7, 10)
SL_CENTS = (1, 2, 3, 5, 7, 10)

LATENCIES_MS = (100, 500, 1000, 2000)
QUANTITIES = (1, 5, 10, 25)          # shares; ladder VWAP is walked for each
SIDES = ("LEADER", "TRAILER")        # relative to the BTC anchor at the decision instant

# Entry eligibility (frozen). Deliberately permissive -- the FIRST pass is unconditional; the
# job of pass 1 is to measure the surface, not to select winners.
MIN_ASK = 0.03
MAX_ASK = 0.97
MAX_SPREAD = 0.10
MIN_TOP_ASK_SIZE = 1.0
MAX_BOOK_STALENESS_S = 5.0           # decision book must be fresher than this

# One independent entry per (round, configuration). No pyramiding, no re-entry.
ONE_ENTRY_PER_ROUND_PER_CONFIG = True

# ---------------------------------------------------------------------------------------------
# Promotion gate -- NOT lowered to fit available data
# ---------------------------------------------------------------------------------------------
GATE = {
    "min_independent_rounds": 500,
    "min_calendar_weeks": 8,
    "min_expectancy_per_share": 0.0,     # strictly positive required
    "min_bootstrap_lb_95": 0.0,          # strictly positive required
    "min_profit_factor": 1.20,
    "require_positive_at_latency_ms": 1000,
    "max_week_sign_reversals": 0,
    "must_survive": ("shuffled_control", "ask_matched_control",
                     "trailing_side_control", "time_shift_control",
                     "benjamini_hochberg"),
}

# Multiple-testing family = the ENTIRE declared grid, not the interesting cells.
def family_size() -> int:
    n = 0
    for h in HORIZONS:
        n += (len(ENTRY_CHECKPOINTS_S[h]) * len(TP_CENTS) * len(SL_CENTS)
              * len(LATENCIES_MS) * len(QUANTITIES) * len(SIDES))
    return n


BH_ALPHA = 0.05
NULL_CONTROLS = {
    "shuffled_control": "shuffle selection within week x ask-bucket x seconds-left bucket",
    "ask_matched_control": "match each entry on ask, seconds-left, spread, leader state, week",
    "trailing_side_control": "identical policy applied to the opposite side",
    "time_shift_control": "shift the signal to unrelated rounds, keep market structure",
}


def frozen_config() -> dict:
    return {
        "config_version": CONFIG_VERSION,
        "fee_rate": FEE_RATE, "fee_model": FEE_MODEL,
        "trust_recorded_fee_bps": TRUST_RECORDED_FEE_BPS,
        "horizons": list(HORIZONS),
        "entry_checkpoints_s": {str(k): list(v) for k, v in ENTRY_CHECKPOINTS_S.items()},
        "tp_cents": list(TP_CENTS), "sl_cents": list(SL_CENTS),
        "latencies_ms": list(LATENCIES_MS), "quantities": list(QUANTITIES),
        "sides": list(SIDES),
        "eligibility": {"min_ask": MIN_ASK, "max_ask": MAX_ASK, "max_spread": MAX_SPREAD,
                        "min_top_ask_size": MIN_TOP_ASK_SIZE,
                        "max_book_staleness_s": MAX_BOOK_STALENESS_S,
                        "one_entry_per_round_per_config": ONE_ENTRY_PER_ROUND_PER_CONFIG},
        "gate": GATE, "family_size": family_size(), "bh_alpha": BH_ALPHA,
        "null_controls": NULL_CONTROLS,
    }


def config_hash() -> str:
    blob = json.dumps(frozen_config(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


if __name__ == "__main__":
    cfg = frozen_config()
    print(json.dumps(cfg, indent=2))
    print(f"\nfamily_size = {cfg['family_size']:,} declared cells")
    print(f"config_hash = {config_hash()}")
