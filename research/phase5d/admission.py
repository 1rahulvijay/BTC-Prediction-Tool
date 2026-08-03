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
    REJECT_UNDERPOWERED, REJECT_SUBCOST, REJECT_NO_EXECUTABLE_ACTION or
    POWER_UNITS_UNRESOLVED.

POWER IS COMPUTED IN THE ENDPOINT'S OWN UNITS
    The binary-rate MDE is in PERCENTAGE POINTS. Most of this backlog is denominated in net
    bps, dollars per share, a Brier difference or a time to event. A first version applied the
    binary formula to all of them - including its own selftest, which declared
    monetized_quantity='net bps'. Every declaration now names its Endpoint, and a declaration
    that has not supplied what its endpoint needs reports POWER_UNITS_UNRESOLVED instead of a
    dimensionally invalid number.

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
    #: Structure is fine but the power arithmetic cannot be done in the endpoint's own units.
    #: Reported honestly rather than borrowing the binary-rate formula, which would claim
    #: mathematical underpowering from a calculation that does not apply.
    UNRESOLVED = "POWER_UNITS_UNRESOLVED"


class Endpoint:
    """What kind of quantity the experiment actually measures.

    The binary-rate MDE - z * sqrt(p(1-p)/k) * 100 - is in PERCENTAGE POINTS. Applying it to
    an endpoint denominated in bps, dollars per share or a Brier difference is dimensionally
    meaningless, and a first version of this file did exactly that while its own selftest
    declared monetized_quantity='net bps'."""

    BINARY_RATE = "BINARY_RATE"                        # win rate, crossing probability
    CONTINUOUS_CLUSTER_MEAN = "CONTINUOUS_CLUSTER_MEAN"  # net bps, PnL per share
    PAIRED_CONTINUOUS = "PAIRED_CONTINUOUS"            # action advantage vs HOLD
    PROPER_SCORE_DIFFERENCE = "PROPER_SCORE_DIFFERENCE"  # Brier / log loss / resolution delta
    # "2z/sqrt(events)" is a minimum detectable LOG HAZARD RATIO under Schoenfeld-style
    # assumptions. It is NOT an MDE in seconds, in median survival time, in event probability
    # or in restricted mean survival time. Naming it SURVIVAL_EVENT invited exactly the unit
    # confusion this class exists to prevent, so each survival estimand is now its own endpoint.
    SURVIVAL_LOG_HAZARD_RATIO = "SURVIVAL_LOG_HAZARD_RATIO"      # 2z / sqrt(events)
    SURVIVAL_PROBABILITY_DIFFERENCE = "SURVIVAL_PROBABILITY_DIFFERENCE"   # binary at a horizon
    RESTRICTED_MEAN_TIME_DIFFERENCE = "RESTRICTED_MEAN_TIME_DIFFERENCE"   # time units, needs SD
    MEDIAN_TIME_DIFFERENCE = "MEDIAN_TIME_DIFFERENCE"                     # time units, needs SD


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
    #: Which power formula applies. See Endpoint.
    endpoint_type: str = Endpoint.BINARY_RATE
    #: SD of the CLUSTER-LEVEL outcome (daily/weekly aggregate), in the endpoint's units.
    #: Required for continuous, paired and proper-score endpoints.
    cluster_sd: float | None = None
    #: Qualifying (uncensored) event count. Required for survival endpoints.
    qualifying_events: int | None = None
    #: DIAGNOSTIC studies deliberately have no executable action - 157 is specified as "this
    #: test should not generate trades". Labelling those REJECT_NO_EXECUTABLE_ACTION would
    #: report the most fundamental test in the backlog as refused, which is simply wrong.
    intended_as: str = "ECONOMIC"
    note: str = ""

    @property
    def mde(self) -> float | None:
        """Minimum detectable effect IN THE ENDPOINT'S OWN UNITS, or None if not computable.

        None is not a failure - it means the declaration has not supplied what the endpoint
        needs (a cluster-level SD, or a qualifying event count). Returning a binary-rate number
        anyway would be a dimensionally invalid claim dressed as rigour."""
        if self.available_clusters < 2:
            return float("inf")
        root_k = float(np.sqrt(self.available_clusters))

        if self.endpoint_type == Endpoint.BINARY_RATE:
            # percentage points
            return POWER_Z * float(np.sqrt(self.base_rate * (1 - self.base_rate)
                                           / self.available_clusters)) * 100.0

        if self.endpoint_type in (Endpoint.CONTINUOUS_CLUSTER_MEAN,
                                  Endpoint.PAIRED_CONTINUOUS,
                                  Endpoint.PROPER_SCORE_DIFFERENCE):
            # Units of the endpoint. The SD must be of DAILY (or weekly) aggregate outcomes,
            # never of individual rows - row-level SD understates it by the design effect.
            if self.cluster_sd is None or self.cluster_sd <= 0:
                return None
            return POWER_Z * float(self.cluster_sd) / root_k

        if self.endpoint_type == Endpoint.SURVIVAL_LOG_HAZARD_RATIO:
            # Power follows the QUALIFYING EVENT count, not the row count. The number is a
            # detectable LOG HAZARD RATIO - dimensionless, and not comparable to seconds.
            if self.qualifying_events is None or self.qualifying_events < 2:
                return None
            return POWER_Z * 2.0 / float(np.sqrt(self.qualifying_events))

        if self.endpoint_type == Endpoint.SURVIVAL_PROBABILITY_DIFFERENCE:
            # Survival probability at a fixed horizon is a BINARY rate, so it uses the binary
            # formula - but on the count of clusters contributing an uncensored observation.
            if self.qualifying_events is None or self.qualifying_events < 2:
                return None
            return POWER_Z * float(np.sqrt(self.base_rate * (1 - self.base_rate)
                                           / self.qualifying_events)) * 100.0

        if self.endpoint_type in (Endpoint.RESTRICTED_MEAN_TIME_DIFFERENCE,
                                  Endpoint.MEDIAN_TIME_DIFFERENCE):
            # Both are differences in TIME. They need a cluster-level SD in time units; the
            # event count alone cannot produce them.
            if self.cluster_sd is None or self.cluster_sd <= 0:
                return None
            return POWER_Z * float(self.cluster_sd) / root_k
        return None

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
    mde = declaration.mde
    if mde is None:
        return Status.UNRESOLVED, (
            f"structure passes, but a {declaration.endpoint_type} endpoint needs a "
            f"cluster-level SD (or event count) to compute power in its own units")
    if mde > declaration.minimum_effect_worth_detecting:
        return Status.UNDERPOWERED, (
            f"MDE {mde:.1f} exceeds the {declaration.minimum_effect_worth_detecting} "
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
    # Power must be computed in the endpoint's own units, or refused.
    continuous = {**base, "endpoint_type": Endpoint.CONTINUOUS_CLUSTER_MEAN}
    check(adjudicate(Declaration(**continuous))[0] == Status.UNRESOLVED,
          "a net-bps endpoint with no cluster SD is POWER_UNITS_UNRESOLVED, not a binary MDE")
    sized = Declaration(**{**continuous, "cluster_sd": 40.0})
    check(abs(sized.mde - 2.80 * 40.0 / np.sqrt(360)) < 1e-9,
          "a continuous endpoint uses z * cluster_sd / sqrt(k) in the endpoint's OWN units")
    check(adjudicate(sized)[0] == Status.ECONOMIC,
          "supplying the cluster SD resolves the power question and admits the study")
    survival = Declaration(**{**base, "endpoint_type": Endpoint.SURVIVAL_LOG_HAZARD_RATIO})
    check(adjudicate(survival)[0] == Status.UNRESOLVED,
          "a survival endpoint needs an EVENT count, not a row count")
    check(Declaration(**{**base, "endpoint_type": Endpoint.SURVIVAL_LOG_HAZARD_RATIO,
                         "qualifying_events": 400}).mde
          < Declaration(**{**base, "endpoint_type": Endpoint.SURVIVAL_LOG_HAZARD_RATIO,
                           "qualifying_events": 100}).mde,
          "more qualifying events detect a smaller hazard RATIO")
    # The four survival estimands are NOT interchangeable - each has its own units.
    hazard = Declaration(**{**base, "endpoint_type": Endpoint.SURVIVAL_LOG_HAZARD_RATIO,
                            "qualifying_events": 400}).mde
    probability = Declaration(**{**base, "endpoint_type": Endpoint.SURVIVAL_PROBABILITY_DIFFERENCE,
                                 "qualifying_events": 400}).mde
    check(abs(hazard - probability) > 1.0,
          "a log-hazard-ratio MDE and a survival-PROBABILITY MDE are different numbers in "
          "different units - the reason SURVIVAL_EVENT was split into four endpoints")
    check(Declaration(**{**base, "endpoint_type": Endpoint.RESTRICTED_MEAN_TIME_DIFFERENCE,
                         "qualifying_events": 400}).mde is None,
          "a restricted-mean-TIME difference needs an SD in time units; an event count alone "
          "cannot produce one")
    check(Declaration(**{**continuous, "cluster_sd": 40.0}).mde
          != Declaration(**base).mde,
          "the continuous and binary formulas give DIFFERENT numbers - the bug was treating "
          "them as interchangeable")

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
                note="forward only by its own readiness note", endpoint_type=Endpoint.CONTINUOUS_CLUSTER_MEAN),
    Declaration("142 model_revision_leads_market", "ENTER", "net $/share", "polymarket",
                "day", 0, 5.0, 5.0, None, "no revision", None,
                note="requires the revision ledger, which starts empty", endpoint_type=Endpoint.CONTINUOUS_CLUSTER_MEAN),
    Declaration("143 feature_family_incremental_resolution", None, "resolution", "polymarket",
                "day", 21, 5.0, 5.0, None, "market only", None, intended_as="DIAGNOSTIC", endpoint_type=Endpoint.PROPER_SCORE_DIFFERENCE),
    Declaration("144 open_position_action_value", "HOLD/EXIT/REDUCE/SWITCH/LOCK", "net $/share",
                "polymarket", "day", 0, 5.0, 5.0, None, "HOLD", None, endpoint_type=Endpoint.PAIRED_CONTINUOUS),
    Declaration("145 exit_regret_recoverability", "EXIT/REDUCE", "net $/share", "polymarket",
                "day", 0, 5.0, 5.0, None, "HOLD", None, endpoint_type=Endpoint.PAIRED_CONTINUOUS),
    Declaration("148 server_gate_value", "veto/allow", "net $/share", "polymarket",
                "day", 0, 5.0, 5.0, None, "current gate", None, endpoint_type=Endpoint.PAIRED_CONTINUOUS),
    Declaration("150 dynamic_horizon_selector", "EXIT_5M..EXIT_120M", "net bps", "binance_perp",
                "day", 360, 10.0, 12.0, None, "best fixed horizon", None,
                note="360-day archive; effect unknown until run", endpoint_type=Endpoint.CONTINUOUS_CLUSTER_MEAN),
    Declaration("151 thesis_survival_after_adverse_move", "HOLD/EXIT/REDUCE", "net bps",
                "binance_perp", "day", 360, 10.0, 12.0, None, "fixed stop", None, endpoint_type=Endpoint.PAIRED_CONTINUOUS),
    Declaration("152 conditional_barrier_asymmetry", "bracket", "net bps", "binance_perp",
                "day", 360, 10.0, 12.0, 2.3, "unconditional bracket", None,
                note="unconditional best cell was -9.70 bps; gross ~2.3 bps above nothing", endpoint_type=Endpoint.CONTINUOUS_CLUSTER_MEAN),
    Declaration("157 market_price_sufficiency_boundary", None, "resolution", "polymarket",
                "day", 21, 5.0, 5.0, None, "market only", None,
                note="DECISION test - gates Prereg A. Descriptive by design.", intended_as="DIAGNOSTIC", endpoint_type=Endpoint.PROPER_SCORE_DIFFERENCE),
    Declaration("164 cost_information_loss_decomposition", None, "$/share decomposition",
                "polymarket", "day", 21, 5.0, 5.0, None, "counterfactual prices", None,
                note="accounting identity, not an estimate - runnable now", intended_as="DIAGNOSTIC"),
    Declaration("158 future_market_repricing", "ENTER", "net $/share", "polymarket",
                "day", 0, 5.0, 5.0, None, "persistence", None, endpoint_type=Endpoint.CONTINUOUS_CLUSTER_MEAN),
    Declaration("159 market_error_episode_detector", "ENTER", "net $/share", "polymarket",
                "day", 0, 5.0, 5.0, None, "no trade", None, endpoint_type=Endpoint.CONTINUOUS_CLUSTER_MEAN),
    Declaration("166 open_position_action_advantage", "action vs HOLD", "net $/share",
                "polymarket", "day", 0, 5.0, 5.0, None, "HOLD", None, endpoint_type=Endpoint.PAIRED_CONTINUOUS),
    Declaration("167 tail_loss_action_head", "REDUCE/EXIT/LOCK", "expected shortfall",
                "polymarket", "day", 0, 5.0, 5.0, None, "HOLD", None, endpoint_type=Endpoint.CONTINUOUS_CLUSTER_MEAN),
    Declaration("168 opportunity_decay_latency_budget", None, "edge half-life", "polymarket",
                "day", 0, 5.0, 5.0, None, None, None, intended_as="DIAGNOSTIC", endpoint_type=Endpoint.SURVIVAL_LOG_HAZARD_RATIO),
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
