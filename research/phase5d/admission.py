"""PHASE5D admission contract - what a study must DECLARE before it may run economically.

THE CONTRACT, TAKEN VERBATIM FROM THE PHASE 5D PROPOSAL
    Every script declares, before running:

        experiment_id / economic_action / monetized_quantity / instrument /
        cluster_unit / available_clusters / minimum_effect_worth_detecting /
        declared_cost / estimated_gross_effect / baseline_policy / untouched_period

    and may proceed as an ECONOMIC experiment only when all of:

        MDE <= economically meaningful effect
        estimated gross effect / declared cost >= 1.25
        an exact executable action exists
        baseline and counterfactual share the same opportunity population
        independent day/week evidence is sufficient

    Otherwise its status is one of DESCRIPTIVE_ONLY, COLLECT_MORE_DATA,
    REJECT_UNDERPOWERED, REJECT_SUBCOST, REJECT_NO_EXECUTABLE_ACTION.

WHY AS CODE AND NOT AS A CHECKLIST
    Phase 5C proposed 52 tests; the two prefilters killed six of the recommended fifteen before
    any modelling. Phase 5D and 5D-B propose about thirty more. A checklist that a person
    applies is a checklist that gets applied to the tests somebody already wants to run.

    This computes the verdict from the declaration, so a study cannot be promoted to an
    economic claim by enthusiasm.

    python research/phase5d/admission.py            # triage the declared Phase 5D backlog
    python research/phase5d/admission.py --selftest
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research" / "phase5c"))

#: Effect must clear this multiple of declared cost to be worth modelling. Phase 5C 130.
MIN_EFFECT_COST_RATIO = 1.25
POWER_Z = 2.80


class Status:
    ECONOMIC = "ECONOMIC_EXPERIMENT"
    DESCRIPTIVE = "DESCRIPTIVE_ONLY"
    COLLECT = "COLLECT_MORE_DATA"
    UNDERPOWERED = "REJECT_UNDERPOWERED"
    SUBCOST = "REJECT_SUBCOST"
    NO_ACTION = "REJECT_NO_EXECUTABLE_ACTION"


@dataclass(frozen=True)
class Declaration:
    experiment_id: str
    economic_action: str | None          # None means there is no executable action
    monetized_quantity: str
    instrument: str
    cluster_unit: str                    # day / week / round - the INDEPENDENT unit
    available_clusters: int
    minimum_effect_worth_detecting: float    # in the units of `declared_cost`
    declared_cost: float
    estimated_gross_effect: float | None     # None means unknown before collection
    baseline_policy: str | None
    untouched_period: str | None             # None means no untouched evidence exists yet
    base_rate: float = 0.5
    #: DIAGNOSTIC studies deliberately have no executable action - 157 is specified as "this
    #: test should not generate trades". Labelling those REJECT_NO_EXECUTABLE_ACTION would
    #: report the most fundamental test in the backlog as refused, which is simply wrong.
    intended_as: str = "ECONOMIC"
    note: str = ""

    @property
    def mde(self) -> float:
        """Minimum detectable shift given the number of INDEPENDENT clusters."""
        if self.available_clusters < 2:
            return float("inf")
        return POWER_Z * float(np.sqrt(self.base_rate * (1 - self.base_rate)
                                       / self.available_clusters)) * 100.0

    @property
    def effect_cost_ratio(self) -> float | None:
        if self.estimated_gross_effect is None or self.declared_cost <= 0:
            return None
        return float(self.estimated_gross_effect) / float(self.declared_cost)


def adjudicate(declaration: Declaration) -> tuple[str, str]:
    """Status and the single binding reason. Order matters: report the FIRST hard blocker."""
    if not declaration.economic_action:
        if declaration.intended_as == "DIAGNOSTIC":
            return Status.DESCRIPTIVE, "diagnostic by design; it informs a decision, not a trade"
        return Status.NO_ACTION, ("claims an economic result but defines no executable action")
    if declaration.untouched_period is None:
        return Status.COLLECT, "no untouched evidence period exists yet"
    if declaration.baseline_policy is None:
        return Status.DESCRIPTIVE, "no baseline policy declared to compare against"

    ratio = declaration.effect_cost_ratio
    if ratio is None:
        return Status.COLLECT, "gross effect is unknown until data accumulates"
    if ratio < MIN_EFFECT_COST_RATIO:
        return Status.SUBCOST, (f"effect/cost {ratio:.2f} is below the {MIN_EFFECT_COST_RATIO} "
                                f"bar - too small to monetize")
    # Power is checked LAST among the hard gates: an underpowered test of a big effect is
    # worth collecting for, whereas a well-powered test of a sub-cost effect never is.
    if declaration.mde > declaration.minimum_effect_worth_detecting:
        return Status.UNDERPOWERED, (
            f"MDE {declaration.mde:.1f} exceeds the {declaration.minimum_effect_worth_detecting} "
            f"worth detecting on {declaration.available_clusters} {declaration.cluster_unit} "
            f"clusters")
    return Status.ECONOMIC, "all admission conditions satisfied"


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    base = dict(experiment_id="probe", economic_action="ENTER", monetized_quantity="net bps",
                instrument="binance_perp", cluster_unit="day", available_clusters=360,
                minimum_effect_worth_detecting=10.0, declared_cost=12.0,
                estimated_gross_effect=20.0, baseline_policy="WAIT",
                untouched_period="2026-08-02 onward")

    check(adjudicate(Declaration(**base))[0] == Status.ECONOMIC,
          "a well-powered, above-cost, actionable declaration is admitted")
    check(adjudicate(Declaration(**{**base, "economic_action": None}))[0] == Status.NO_ACTION,
          "no executable action is refused BEFORE anything else is considered")
    check(adjudicate(Declaration(**{**base, "untouched_period": None}))[0] == Status.COLLECT,
          "no untouched period means collect, not reject - the idea may be fine")
    check(adjudicate(Declaration(**{**base, "estimated_gross_effect": 6.0}))[0]
          == Status.SUBCOST,
          "an effect at half the cost is REJECT_SUBCOST regardless of how well powered")
    check(adjudicate(Declaration(**{**base, "available_clusters": 21}))[0]
          == Status.UNDERPOWERED,
          "21 day-clusters cannot detect a 10-point effect and are refused")
    check(adjudicate(Declaration(**{**base, "baseline_policy": None}))[0] == Status.DESCRIPTIVE,
          "no baseline means the study may describe but not claim economics")

    # A sub-cost effect must NOT be rescued by having many clusters.
    plenty = adjudicate(Declaration(**{**base, "estimated_gross_effect": 6.0,
                                       "available_clusters": 5000}))
    check(plenty[0] == Status.SUBCOST,
          "more data cannot rescue an effect that is too small to pay - order of checks matters")

    check(Declaration(**{**base, "available_clusters": 21}).mde
          > Declaration(**base).mde,
          "fewer clusters give a larger minimum detectable effect")
    check(adjudicate(Declaration(**{**base, "economic_action": None,
                                    "intended_as": "DIAGNOSTIC"}))[0] == Status.DESCRIPTIVE,
          "a study that is DIAGNOSTIC by design is descriptive, not rejected - 157 is "
          "specified as 'should not generate trades' and must not read as refused")

    print(f"\nADMISSION CONTRACT SELFTEST: PASS ({checks} checks)")
    return 0


#: The Phase 5D and 5D-B backlog, declared. Costs: 12.0 bps Binance, ~5.0 points Polymarket
#: round trip on a representative late-round quote. `available_clusters` uses the source each
#: test would actually read - see RESEARCH_LEDGER 10.1.
BACKLOG: tuple[Declaration, ...] = (
    Declaration("141 market_prior_residual_act_skip", "ENTER/WAIT", "net $/share", "polymarket",
                "day", 21, 5.0, 5.0, None, "market price alone", None,
                note="forward only by its own readiness note"),
    Declaration("142 model_revision_leads_market", "ENTER", "net $/share", "polymarket",
                "day", 0, 5.0, 5.0, None, "no revision", None,
                note="requires the revision ledger, which starts empty"),
    Declaration("143 feature_family_incremental_resolution", None, "resolution", "polymarket",
                "day", 21, 5.0, 5.0, None, "market only", None, intended_as="DIAGNOSTIC"),
    Declaration("144 open_position_action_value", "HOLD/EXIT/REDUCE/SWITCH/LOCK", "net $/share",
                "polymarket", "day", 0, 5.0, 5.0, None, "HOLD", None),
    Declaration("145 exit_regret_recoverability", "EXIT/REDUCE", "net $/share", "polymarket",
                "day", 0, 5.0, 5.0, None, "HOLD", None),
    Declaration("148 server_gate_value", "veto/allow", "net $/share", "polymarket",
                "day", 0, 5.0, 5.0, None, "current gate", None),
    Declaration("150 dynamic_horizon_selector", "EXIT_5M..EXIT_120M", "net bps", "binance_perp",
                "day", 360, 10.0, 12.0, None, "best fixed horizon", None,
                note="360-day archive; effect unknown until run"),
    Declaration("151 thesis_survival_after_adverse_move", "HOLD/EXIT/REDUCE", "net bps",
                "binance_perp", "day", 360, 10.0, 12.0, None, "fixed stop", None),
    Declaration("152 conditional_barrier_asymmetry", "bracket", "net bps", "binance_perp",
                "day", 360, 10.0, 12.0, 2.3, "unconditional bracket", None,
                note="unconditional best cell was -9.70 bps; gross ~2.3 bps above nothing"),
    Declaration("157 market_price_sufficiency_boundary", None, "resolution", "polymarket",
                "day", 21, 5.0, 5.0, None, "market only", None,
                note="DECISION test - gates Prereg A. Descriptive by design.", intended_as="DIAGNOSTIC"),
    Declaration("164 cost_information_loss_decomposition", None, "$/share decomposition",
                "polymarket", "day", 21, 5.0, 5.0, None, "counterfactual prices", None,
                note="accounting identity, not an estimate - runnable now", intended_as="DIAGNOSTIC"),
    Declaration("158 future_market_repricing", "ENTER", "net $/share", "polymarket",
                "day", 0, 5.0, 5.0, None, "persistence", None),
    Declaration("159 market_error_episode_detector", "ENTER", "net $/share", "polymarket",
                "day", 0, 5.0, 5.0, None, "no trade", None),
    Declaration("166 open_position_action_advantage", "action vs HOLD", "net $/share",
                "polymarket", "day", 0, 5.0, 5.0, None, "HOLD", None),
    Declaration("167 tail_loss_action_head", "REDUCE/EXIT/LOCK", "expected shortfall",
                "polymarket", "day", 0, 5.0, 5.0, None, "HOLD", None),
    Declaration("168 opportunity_decay_latency_budget", None, "edge half-life", "polymarket",
                "day", 0, 5.0, 5.0, None, None, None, intended_as="DIAGNOSTIC"),
    Declaration("169 sparse_exceptional_state_discovery", None, "rule set", "both",
                "day", 21, 5.0, 5.0, None, "no rule", None, intended_as="DIAGNOSTIC"),
    Declaration("170 settlement_uncertainty_reserve", None, "reserve $/share", "polymarket",
                "day", 21, 5.0, 5.0, None, "no reserve", None, intended_as="DIAGNOSTIC"),
    Declaration("172 residual_capacity_surface", None, "max size", "polymarket",
                "day", 0, 5.0, 5.0, None, None, None, intended_as="DIAGNOSTIC"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    print("=" * 104)
    print("PHASE 5D ADMISSION - what may run as an ECONOMIC experiment, computed not chosen")
    print("=" * 104)
    print(f"{'experiment':<44}{'clusters':>9}{'MDE':>8}{'eff/cost':>10}  status")
    tally: dict[str, int] = {}
    for declaration in BACKLOG:
        status, reason = adjudicate(declaration)
        tally[status] = tally.get(status, 0) + 1
        ratio = declaration.effect_cost_ratio
        print(f"{declaration.experiment_id:<44}{declaration.available_clusters:>9}"
              f"{declaration.mde:>8.1f}"
              f"{('-' if ratio is None else f'{ratio:.2f}'):>10}  {status}")
    print()
    for status, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {status:<26}{count:>3}")
    print()
    economic = tally.get(Status.ECONOMIC, 0)
    print(f"  {economic} of {len(BACKLOG)} declared Phase 5D/5D-B experiments may currently run")
    print("  as economic experiments. The rest are descriptive, awaiting data, or refused.")
    print()
    print("  This is not a judgement about the ideas. Most are COLLECT_MORE_DATA, which means")
    print("  the design is fine and the evidence does not exist yet - the recorders decide,")
    print("  not the modelling.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
