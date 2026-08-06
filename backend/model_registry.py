"""The canonical model registry. One list, and everything else derives from it.

WHY THIS EXISTS
    `check_feature_contract.ARTIFACTS` is a hand-maintained list of twelve filenames. A model
    added anywhere else in the codebase and not typed into that list is a model that bypasses
    every identity check silently - the checker simply never looks at it, and reports the other
    twelve as healthy.

    A registry only helps if it is the single source. Save names, load verification, readiness,
    startup health and tests must all read from here, so "not in the registry" becomes an
    explicit refusal rather than an invisible gap.

STATUS: ACTIVE_CONTRACT.
    Readiness derives the serving artifact set from this registry. Active standalone loaders
    enforce provenance and integrity before deserializing.

    python backend/model_registry.py            # print the registry
    python backend/model_registry.py --selftest
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Typed refusals. Serving must be able to say WHICH failure occurred rather than emitting an
# unexplained absence of prediction. The first four already exist in check_feature_contract and
# are repeated verbatim so the vocabulary does not fork.
MODEL_UNAVAILABLE_MISSING = "MODEL_UNAVAILABLE_MISSING"
MODEL_UNAVAILABLE_UNKNOWN_IDENTITY = "MODEL_UNAVAILABLE_UNKNOWN_IDENTITY"
MODEL_UNAVAILABLE_STALE_ARTIFACT = "MODEL_UNAVAILABLE_STALE_ARTIFACT"
MODEL_UNAVAILABLE_TAMPERED = "MODEL_UNAVAILABLE_TAMPERED"
MODEL_UNAVAILABLE_MIXED_BUNDLE = "MODEL_UNAVAILABLE_MIXED_BUNDLE"
MODEL_UNAVAILABLE_WRONG_TARGET = "MODEL_UNAVAILABLE_WRONG_TARGET"
MODEL_UNAVAILABLE_UNAUTHORIZED = "MODEL_UNAVAILABLE_UNAUTHORIZED"
MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE = "MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE"

REFUSAL_CODES = frozenset({
    MODEL_UNAVAILABLE_MISSING,
    MODEL_UNAVAILABLE_UNKNOWN_IDENTITY,
    MODEL_UNAVAILABLE_STALE_ARTIFACT,
    MODEL_UNAVAILABLE_TAMPERED,
    MODEL_UNAVAILABLE_MIXED_BUNDLE,
    MODEL_UNAVAILABLE_WRONG_TARGET,
    MODEL_UNAVAILABLE_UNAUTHORIZED,
    MODEL_UNAVAILABLE_INCOMPLETE_BUNDLE,
})


@dataclass(frozen=True)
class ModelRegistryEntry:
    """One model's identity contract."""

    name: str                       # registry key, stable across renames of the file
    filename: str                   # canonical artifact filename inside a bundle
    target: str                     # what it predicts; a bundle claiming another target is WRONG_TARGET
    owner: str                      # subsystem that may write it
    # Authority is declared here, not inferred at the call site. A head may be loadable and
    # still not permitted to price or size.
    may_price: bool = False
    may_rank: bool = False
    may_size: bool = False
    required_for_serving: bool = False
    notes: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)


# The twelve artifacts check_feature_contract currently lists by hand, plus their declared
# targets and authority. Extending the system means adding a row HERE; anything else is a
# bypass and `unregistered()` will name it.
REGISTRY: tuple[ModelRegistryEntry, ...] = (
    ModelRegistryEntry("persistence", "persistence_model.pkl", "p_hold", "train_heads",
                       may_rank=True, required_for_serving=True,
                       notes="P(hold) is measurably overconfident live; pricing stays off"),
    ModelRegistryEntry("path_forecaster", "path_forecaster.pkl", "path_quantiles", "train_heads",
                       may_rank=True),
    ModelRegistryEntry(
        "fade", "fade_model.pkl", "fade_roundtrip", "research_only",
        may_rank=False,
        notes="Dormant: causal 1m and honest 1s challengers missed frozen promotion gates",
    ),
    ModelRegistryEntry("signed_quantile", "signed_quantile_model.pkl", "signed_move_quantiles",
                       "train_signed_quantiles", may_rank=True),
    ModelRegistryEntry("round_state", "round_state_heads.pkl", "round_state",
                       "train_round_state_heads", may_rank=True),
    ModelRegistryEntry("bigmove_keeper", "bigmove_keeper_model.pkl", "big_move", "train_heads",
                       may_rank=True),
    ModelRegistryEntry("bigdrop_keeper", "bigdrop_keeper_model.pkl", "big_drop", "train_heads",
                       may_rank=True),
    ModelRegistryEntry("directional_keeper", "directional_keeper_model.pkl", "direction",
                       "train_heads", may_rank=True),
    ModelRegistryEntry("activity_keeper", "activity_keeper_model.pkl", "activity", "train_heads",
                       may_rank=True),
    # A binary endpoint head, and NOT the Polymarket settlement model. Its labels use exchange
    # closes (the venue settles on Chainlink) and compare the horizon end to the DECISION-time
    # price rather than the round's fixed anchor - which inverts the outcome on up to ~35% of
    # rounds late in a round. `POLYMARKET_SETTLEMENT_EV` therefore refuses it, by design.
    # Authority is NONE: it exists to be MEASURED, not to price anything.
    ModelRegistryEntry("settlement", "settlement_head.pkl", "rolling_exchange_return_sign_v1",
                       "settlement_head.train_settlement_head",
                       may_price=False, may_rank=False, may_size=False,
                       notes="Exchange-proxy binary endpoint probability. Wrong price series "
                             "and wrong reference point for Polymarket settlement; measurable "
                             "only. No authority until round-aligned oracle labels exist."),
    ModelRegistryEntry("selectivity", "selectivity_models.pkl", "selectivity",
                       "decision.train_selectivity_models", may_rank=True),
    ModelRegistryEntry("champion_meta", "champion_meta_model.pkl", "champion_decision",
                       "train_heads", may_rank=True),
    ModelRegistryEntry("magnitude", "magnitude_model.pkl", "move_magnitude", "train_heads",
                       may_rank=True),
    # Measured in CROSSING_HEADS_V1 (protocol sha256 762532c9): reversion at 30s reaches AUC
    # 0.6715 against a 0.5196 clock baseline - the first head here to beat its incumbent by a
    # material margin, on a target that is not forward direction.
    #
    # EVERY AUTHORITY FLAG IS FALSE, deliberately. A crossing probability is an input to a
    # decision, not a decision, and every action lane measured in this repository is closed on
    # cost. The head is loadable and may inform a display or a later study; it may not price,
    # rank or size. Authority is declared here rather than at the call site precisely so that
    # granting it later is a visible edit to this table.
    ModelRegistryEntry("crossing_heads", "crossing_heads.pkl", "crossing_probabilities",
                       "train_crossing_heads",
                       notes="P(final observed crossing), P(state on original side at 30s/60s). "
                             "Targets renamed 2026-08-04: the old bundle predicted the same "
                             "quantity under the misleading name reverted_Ns and is refused "
                             "by target-contract hash. No authority: an input to a decision, "
                             "never a decision. 5s/15s pending HF recorder data"),
)

BY_NAME = {entry.name: entry for entry in REGISTRY}
BY_FILENAME = {entry.filename: entry for entry in REGISTRY}


def lookup(identifier: str) -> ModelRegistryEntry | None:
    """Resolve by registry name, filename, or declared alias."""
    if identifier in BY_NAME:
        return BY_NAME[identifier]
    if identifier in BY_FILENAME:
        return BY_FILENAME[identifier]
    for entry in REGISTRY:
        if identifier in entry.aliases:
            return entry
    return None


def require(identifier: str) -> ModelRegistryEntry:
    """Resolve or refuse. An unregistered artifact has no identity contract to check against."""
    entry = lookup(identifier)
    if entry is None:
        raise KeyError(
            f"{MODEL_UNAVAILABLE_UNKNOWN_IDENTITY}: '{identifier}' is not in the model registry. "
            f"Add a ModelRegistryEntry - an artifact outside the registry bypasses every "
            f"identity check silently."
        )
    return entry


def unregistered(filenames: list[str]) -> list[str]:
    """Filenames present on disk that no registry entry claims. These are the bypass risk."""
    return sorted(name for name in filenames if name not in BY_FILENAME)


def authority(identifier: str) -> dict[str, bool]:
    entry = require(identifier)
    return {"may_price": entry.may_price, "may_rank": entry.may_rank, "may_size": entry.may_size}


def selftest() -> int:
    ok = True

    def chk(cond: object, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok = ok and bool(cond)

    print("registry integrity")
    names = [e.name for e in REGISTRY]
    files = [e.filename for e in REGISTRY]
    chk(len(names) == len(set(names)), "registry names are unique")
    chk(len(files) == len(set(files)), "artifact filenames are unique")
    chk(all(e.target for e in REGISTRY), "every entry declares a target")
    chk(all(e.owner for e in REGISTRY), "every entry declares an owning subsystem")

    print("parity with the list this replaces")
    try:
        from check_feature_contract import ARTIFACTS as LEGACY
    except Exception:
        import os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from check_feature_contract import ARTIFACTS as LEGACY
    missing = sorted(set(LEGACY) - set(files))
    extra = sorted(set(files) - set(LEGACY))
    chk(not missing, f"registry covers every legacy artifact (missing: {missing})")
    chk(not extra, f"registry adds none the legacy list lacks (extra: {extra})")
    chk(len(REGISTRY) == len(LEGACY), f"{len(REGISTRY)} entries vs {len(LEGACY)} legacy")

    print("resolution and refusal")
    chk(lookup("persistence") is lookup("persistence_model.pkl"),
        "name and filename resolve to the SAME entry")
    chk(lookup("not_a_model") is None, "an unknown identifier resolves to None")
    try:
        require("not_a_model")
        chk(False, "require() must refuse an unknown identifier")
    except KeyError as exc:
        chk(MODEL_UNAVAILABLE_UNKNOWN_IDENTITY in str(exc),
            "require() refuses with UNKNOWN_IDENTITY, naming the registry as the fix")

    print("bypass detection")
    found = unregistered(["persistence_model.pkl", "rogue_model.pkl", "another.pkl"])
    chk(found == ["another.pkl", "rogue_model.pkl"],
        f"an artifact on disk that no entry claims is NAMED, not ignored ({found})")
    chk(unregistered(files) == [], "every registered artifact is recognised")

    print("authority is declared, not inferred")
    chk(authority("persistence")["may_price"] is False,
        "P(hold) may rank but may NOT price - the live miscalibration finding, encoded")
    chk(all(not e.may_size for e in REGISTRY),
        "no registry entry currently carries sizing authority")

    print("refusal vocabulary")
    for code in REFUSAL_CODES:
        chk(code.startswith("MODEL_UNAVAILABLE_"), f"{code} is a typed refusal")
    chk(len(REFUSAL_CODES) == 8, f"all eight refusal kinds are defined ({len(REFUSAL_CODES)})")

    chk(authority("fade")["may_rank"] is False,
        "the rejected fade challenger has no serving authority")
    print("\nSTATUS: ACTIVE_CONTRACT - readiness and serving permissions derive here.")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    print(f"{'name':<20}{'filename':<32}{'target':<22}{'rank':<6}{'price':<6}")
    for entry in REGISTRY:
        print(f"{entry.name:<20}{entry.filename:<32}{entry.target:<22}"
              f"{str(entry.may_rank):<6}{str(entry.may_price):<6}")
    print(f"\n{len(REGISTRY)} registered models. STATUS: ACTIVE_CONTRACT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
