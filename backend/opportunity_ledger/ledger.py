"""ATOMIC_CAUSAL_DECISION_LEDGER_V1 - causality guaranteed by construction, not by SQL discipline.

WHY THIS EXISTS
    Five economic studies in this repository were invalidated by one defect: they reconstructed
    "which market state belonged to which quote" AFTER the fact, with a join that never required
    the state to precede the decision. Measured on the live sample, the state was observed AFTER
    the decision in 93.5% of rows - median +8.1s, max +17.8s - and one of those studies produced
    the only positive candidate the project ever had.

    Every one of them passed CI, passed its own preregistered gates, carried matched controls and
    day-block lower bounds, and was wrong anyway, because nothing asked whether the inputs existed
    when the decision was made.

    A better SQL query does not fix that. The fix is to stop reconstructing the pairing at all:
    write ONE immutable row at the instant of decision, containing the exact inputs used, and
    refuse to write it if those inputs are not causally ordered.

THE INVARIANTS - enforced by raising, never by warning
    quote_recv_ts      <= decision_ts       the price was received before it was acted on
    state_snapshot_ts  <= decision_ts       the model state existed before the decision
    feature_cutoff_ts  <= decision_ts       no feature used data from after the decision
    decision_ts        <= written_ts        a row cannot be written before it is decided

    A violation is a programming error in the caller, not a market condition, so `record()`
    RAISES. Degrading to a warning would reproduce exactly the failure this module exists to
    prevent: a row that looks like evidence and is not.

    Staleness is separate from causality. A state 90 seconds old is causal and probably useless,
    so `state_age_ms` is STORED rather than judged, and research filters on it with a declared
    threshold. Enforcing an age here would silently reshape the population.

CLOCK DOMAINS - why quote_exchange_ts is measured, not policed
    The four invariants above compare timestamps taken from ONE clock: ours. `quote_exchange_ts`
    comes from the venue's clock, and `venue_ts <= our_ts` is therefore not a causality test at
    all - it is a comparison between two clocks that were never synchronised. Enforcing it
    strictly would drop real decisions for ordinary NTP drift, punching coverage holes in the
    exact table whose value depends on recording EVERY opportunity.

    So the skew is STORED (`exchange_skew_ms`, positive when the venue stamp is ahead of our
    receipt) and only a gross inversion beyond MAX_CLOCK_SKEW_MS raises - that bound exists to
    catch a caller passing the wrong field, not to adjudicate time.

REPRODUCIBILITY - a decision nobody can re-derive is not evidence
    A causal row still proves nothing if it cannot say WHAT produced its number. So any action
    where the strategy actually evaluated (ENTER or WAIT) must carry all four identity hashes:
    model artifact, calibrator, policy and the exact feature values. Missing one raises.

    UNAVAILABLE / NO_QUOTE / BLOCKED are exempt by definition: they record that the strategy
    could NOT evaluate, so demanding the identity of a computation that never happened would
    force the caller to invent one. That is the exemption's entire scope - it is not a way to
    log an evaluated decision without provenance.

EVERY OPPORTUNITY IS RECORDED, INCLUDING THE ONES NOT TAKEN
    ENTER        the strategy acted
    WAIT         evaluated, declined - the edge was not there
    NO_QUOTE     market data unavailable at the decision instant
    UNAVAILABLE  the strategy COULD NOT evaluate: artifact, calibrator or feed invalid
    BLOCKED      a risk, identity or data gate refused authority

    WAIT and UNAVAILABLE must never collapse into each other. "The strategy looked and declined"
    and "the strategy was dead and nobody noticed" produce the same empty P&L and mean opposite
    things. PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1 is UNAVAILABLE today - its calibrator
    cannot deploy while the source artifacts fail identity - and that must be visible as a
    distinct state.

IMMUTABILITY
    A decision row is never updated. Outcomes arrive later and are appended to a SEPARATE table
    keyed by decision_id, so what was known at decision time cannot be edited by what happened
    afterwards. That is the same discipline the timestamps enforce, applied to the schema.

    python -m backend.opportunity_ledger.ledger --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "opportunity-ledger-v3"

# Declared, not tuned. A bound on cross-clock disagreement, NOT a causality threshold.
MAX_CLOCK_SKEW_MS = 2_000

# The four hashes that make a decision re-derivable. Required for evaluated actions.
IDENTITY_FIELDS = ("model_artifact_hash", "calibrator_hash", "policy_hash",
                   "feature_values_hash")


class Action(str, Enum):
    ENTER = "ENTER"
    WAIT = "WAIT"
    NO_QUOTE = "NO_QUOTE"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"


#: Actions in which the strategy actually ran and produced a number, so it must be able to say
#: what produced it. The other three record that it could not run at all.
EVALUATED_ACTIONS = (Action.ENTER, Action.WAIT)


class LedgerRefusal(Exception):
    """Base for every refusal to store a row. Callers catch this to catch all of them."""


class NonCausalDecision(LedgerRefusal):
    """Raised when a decision row would use an input that did not yet exist."""


class UnreproducibleDecision(LedgerRefusal):
    """Raised when an EVALUATED decision cannot say what produced its number."""


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class Decision:
    """One opportunity, evaluated once, with every input it used and when that input existed."""

    # identity
    round_id: str
    strategy_id: str
    market_id: str
    venue: str

    # the decision instant and everything that must precede it
    decision_ts: int
    quote_exchange_ts: int | None
    quote_recv_ts: int | None
    state_snapshot_id: str | None
    state_snapshot_ts: int | None
    feature_cutoff_ts: int | None

    # what was seen
    side: str | None
    ask: float | None
    bid: float | None
    fee: float | None
    probability: float | None

    # what produced it - hashes, so a later run can prove it used the same thing
    model_artifact_hash: str | None
    calibrator_hash: str | None
    policy_hash: str | None
    feature_values_hash: str | None
    # Canonical JSON-compatible payload containing the exact feature values and model outputs
    # used by this decision. A hash without its preimage detects changes but cannot reproduce a
    # decision, so evaluated actions require both.
    decision_context: dict[str, Any] | None

    # what was decided
    action: Action
    reason: str
    requested_size: float = 0.0
    risk_state: str = "PAPER_ONLY"

    # populated by the ledger
    written_ts: int = 0
    decision_id: str = ""
    state_age_ms: int | None = None
    quote_age_ms: int | None = None
    exchange_skew_ms: int | None = None

    def causal_violations(self) -> list[str]:
        """Every ordering breach, not just the first - a caller fixing one should see them all."""
        problems: list[str] = []
        if self.quote_recv_ts is not None and self.quote_recv_ts > self.decision_ts:
            problems.append(
                f"quote_recv_ts {self.quote_recv_ts} is after decision_ts {self.decision_ts} "
                f"(+{self.quote_recv_ts - self.decision_ts} ms)")
        if self.state_snapshot_ts is not None and self.state_snapshot_ts > self.decision_ts:
            problems.append(
                f"state_snapshot_ts {self.state_snapshot_ts} is after decision_ts "
                f"{self.decision_ts} (+{self.state_snapshot_ts - self.decision_ts} ms) - this is "
                f"the exact defect that invalidated five studies")
        if self.feature_cutoff_ts is not None and self.feature_cutoff_ts > self.decision_ts:
            problems.append(
                f"feature_cutoff_ts {self.feature_cutoff_ts} is after decision_ts "
                f"{self.decision_ts}")
        # Cross-clock, so bounded rather than strict - see CLOCK DOMAINS above. Ordinary drift
        # is stored as exchange_skew_ms; only a gross inversion is a caller error.
        if (self.quote_exchange_ts is not None and self.quote_recv_ts is not None
                and self.quote_exchange_ts - self.quote_recv_ts > MAX_CLOCK_SKEW_MS):
            problems.append(
                f"quote_exchange_ts {self.quote_exchange_ts} is "
                f"{self.quote_exchange_ts - self.quote_recv_ts} ms after quote_recv_ts "
                f"{self.quote_recv_ts}, beyond the {MAX_CLOCK_SKEW_MS} ms clock-skew bound - "
                f"this is a swapped or wrong field, not drift")
        if self.action in EVALUATED_ACTIONS:
            # WAIT is an evaluated decision too. Dropping the quote/probability from declined
            # opportunities destroys the denominator and makes later threshold analysis
            # impossible without another retrospective join.
            for name in ("quote_recv_ts", "state_snapshot_ts", "feature_cutoff_ts",
                         "side", "ask", "probability"):
                if getattr(self, name) is None:
                    problems.append(f"action is {self.action.value} but {name} is None")
            if self.state_snapshot_id is None:
                problems.append(
                    f"action is {self.action.value} but state_snapshot_id is None - the EXACT state used must "
                    "be referenced, never reconstructed later by an as-of join")
        return problems

    def identity_violations(self) -> list[str]:
        """Missing provenance on an EVALUATED action - a number nobody can re-derive.

        A causally-timestamped row that cannot name the artifact, calibrator, policy and feature
        values behind its probability proves only that SOMETHING ran at that instant. That is
        one step short of evidence, and the step is the whole point."""
        if self.action not in EVALUATED_ACTIONS:
            return []
        missing = [name for name in IDENTITY_FIELDS if not getattr(self, name)]
        problems = [f"action is {self.action.value} but {name} is empty - the decision cannot be "
                    f"reproduced without it" for name in missing]
        if not isinstance(self.decision_context, dict):
            problems.append(
                f"action is {self.action.value} but decision_context is absent - a hash without "
                "its exact input payload is not reproducible")
            return problems
        feature_values = self.decision_context.get("feature_values")
        model_outputs = self.decision_context.get("model_outputs")
        if not isinstance(feature_values, dict):
            problems.append("decision_context.feature_values must be an object")
        elif self.feature_values_hash and stable_hash(feature_values) != self.feature_values_hash:
            problems.append("feature_values_hash does not match decision_context.feature_values")
        if not isinstance(model_outputs, dict):
            problems.append("decision_context.model_outputs must be an object")
        elif self.probability is not None:
            recorded = model_outputs.get("calibrated_probability")
            if recorded is None or abs(float(recorded) - float(self.probability)) > 1e-12:
                problems.append(
                    "decision probability does not match decision_context.model_outputs")
        return problems


class OpportunityLedger:
    """Append-only. Refuses non-causal rows. Outcomes live in a separate table."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._ensure_schema()

    def _connect(self):
        import duckdb
        return duckdb.connect(self.db_path)

    def _ensure_schema(self) -> None:
        con = self._connect()
        try:
            con.execute("""
                CREATE TABLE IF NOT EXISTS opportunity_decisions (
                    decision_id VARCHAR PRIMARY KEY,
                    schema_version VARCHAR NOT NULL,
                    round_id VARCHAR NOT NULL,
                    strategy_id VARCHAR NOT NULL,
                    market_id VARCHAR,
                    venue VARCHAR NOT NULL,
                    decision_ts BIGINT NOT NULL,
                    quote_exchange_ts BIGINT,
                    quote_recv_ts BIGINT,
                    state_snapshot_id VARCHAR,
                    state_snapshot_ts BIGINT,
                    feature_cutoff_ts BIGINT,
                    state_age_ms BIGINT,
                    quote_age_ms BIGINT,
                    side VARCHAR,
                    ask DOUBLE, bid DOUBLE, fee DOUBLE,
                    probability DOUBLE,
                    model_artifact_hash VARCHAR,
                    calibrator_hash VARCHAR,
                    policy_hash VARCHAR,
                    feature_values_hash VARCHAR,
                    action VARCHAR NOT NULL,
                    reason VARCHAR,
                    requested_size DOUBLE,
                    risk_state VARCHAR,
                    written_ts BIGINT NOT NULL,
                    exchange_skew_ms BIGINT,
                    decision_context_json VARCHAR
                )""")
            # Additive migration for ledgers created under v1. A ledger is append-only
            # evidence, so a schema change may only ADD - never rewrite what was recorded.
            existing = {row[0] for row in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'opportunity_decisions'").fetchall()}
            if "exchange_skew_ms" not in existing:
                con.execute("ALTER TABLE opportunity_decisions "
                            "ADD COLUMN exchange_skew_ms BIGINT")
            if "decision_context_json" not in existing:
                con.execute("ALTER TABLE opportunity_decisions "
                            "ADD COLUMN decision_context_json VARCHAR")
            # Outcomes are SEPARATE so a decision row can never be edited by hindsight.
            con.execute("""
                CREATE TABLE IF NOT EXISTS opportunity_outcomes (
                    decision_id VARCHAR NOT NULL,
                    outcome_kind VARCHAR NOT NULL,
                    outcome_ts BIGINT NOT NULL,
                    filled_size DOUBLE,
                    fill_price DOUBLE,
                    exit_price DOUBLE,
                    settled_direction VARCHAR,
                    fees_paid DOUBLE,
                    net_pnl DOUBLE,
                    detail VARCHAR
                )""")
        finally:
            con.close()

    def record(self, decision: Decision, *, now_ms: int | None = None) -> str:
        """Write one decision. Raises NonCausalDecision rather than storing a bad row."""
        problems = decision.causal_violations()
        if problems:
            raise NonCausalDecision(
                f"refusing to record {decision.strategy_id}/{decision.round_id}: "
                + "; ".join(problems))
        unprovable = decision.identity_violations()
        if unprovable:
            raise UnreproducibleDecision(
                f"refusing to record {decision.strategy_id}/{decision.round_id}: "
                + "; ".join(unprovable))

        written = int(now_ms if now_ms is not None else _now_ms())
        if written < decision.decision_ts:
            raise NonCausalDecision(
                f"written_ts {written} precedes decision_ts {decision.decision_ts}")

        identity = {
            "round_id": decision.round_id,
            "strategy_id": decision.strategy_id,
            "decision_ts": decision.decision_ts,
            "action": decision.action.value,
            "side": decision.side,
        }
        decision_id = stable_hash(identity)
        state_age = (decision.decision_ts - decision.state_snapshot_ts
                     if decision.state_snapshot_ts is not None else None)
        quote_age = (decision.decision_ts - decision.quote_recv_ts
                     if decision.quote_recv_ts is not None else None)
        skew = (decision.quote_exchange_ts - decision.quote_recv_ts
                if decision.quote_exchange_ts is not None
                and decision.quote_recv_ts is not None else None)

        # Columns named explicitly. A positional INSERT silently rebinds every value the day
        # someone adds a column, which is the same species of defect as an implicit join.
        values = {
            "decision_id": decision_id, "schema_version": SCHEMA_VERSION,
            "round_id": decision.round_id, "strategy_id": decision.strategy_id,
            "market_id": decision.market_id, "venue": decision.venue,
            "decision_ts": decision.decision_ts,
            "quote_exchange_ts": decision.quote_exchange_ts,
            "quote_recv_ts": decision.quote_recv_ts,
            "state_snapshot_id": decision.state_snapshot_id,
            "state_snapshot_ts": decision.state_snapshot_ts,
            "feature_cutoff_ts": decision.feature_cutoff_ts,
            "state_age_ms": state_age, "quote_age_ms": quote_age,
            "exchange_skew_ms": skew,
            "side": decision.side, "ask": decision.ask, "bid": decision.bid,
            "fee": decision.fee, "probability": decision.probability,
            "model_artifact_hash": decision.model_artifact_hash,
            "calibrator_hash": decision.calibrator_hash,
            "policy_hash": decision.policy_hash,
            "feature_values_hash": decision.feature_values_hash,
            "decision_context_json": (json.dumps(decision.decision_context, sort_keys=True,
                                                   separators=(",", ":"), default=str)
                                      if decision.decision_context is not None else None),
            "action": decision.action.value, "reason": decision.reason,
            "requested_size": decision.requested_size, "risk_state": decision.risk_state,
            "written_ts": written,
        }
        con = self._connect()
        try:
            con.execute(
                f"INSERT INTO opportunity_decisions ({','.join(values)}) "
                f"VALUES ({','.join(['?'] * len(values))})",
                list(values.values()))
        finally:
            con.close()
        return decision_id

    def append_outcome(self, decision_id: str, *, kind: str, outcome_ts: int,
                       **fields: Any) -> None:
        """Outcomes append; they never touch the decision row."""
        con = self._connect()
        try:
            decision_row = con.execute(
                "SELECT decision_ts FROM opportunity_decisions WHERE decision_id = ?",
                [decision_id]).fetchone()
            if not decision_row:
                raise KeyError(f"no decision {decision_id}; an outcome cannot invent one")
            if int(outcome_ts) < int(decision_row[0]):
                raise NonCausalDecision(
                    f"outcome_ts {outcome_ts} precedes decision_ts {decision_row[0]}")
            con.execute(
                "INSERT INTO opportunity_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)",
                [decision_id, kind, int(outcome_ts),
                 fields.get("filled_size"), fields.get("fill_price"),
                 fields.get("exit_price"), fields.get("settled_direction"),
                 fields.get("fees_paid"), fields.get("net_pnl"),
                 json.dumps(fields.get("detail") or {}, sort_keys=True, default=str)])
        finally:
            con.close()

    def append_settlement_for_round(self, round_id: str, *, settled_direction: str,
                                    outcome_ts: int, kind: str,
                                    settlement_price: float | None = None,
                                    source: str = "") -> int:
        """Append one settlement outcome for every decision on a round.

        ENTER is valued as one paper share bought at the recorded ask plus recorded entry fee.
        WAIT receives zero PnL; unavailable/blocked rows receive no economic value. `kind` makes
        proxy and later official settlements separate immutable lifecycle events.
        """
        direction = str(settled_direction).upper()
        if direction not in ("UP", "DOWN"):
            raise ValueError("settled_direction must be UP or DOWN")
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT decision_id, action, side, ask, fee FROM opportunity_decisions "
                "WHERE round_id = ?", [str(round_id)]).fetchall()
            existing = {row[0] for row in con.execute(
                "SELECT decision_id FROM opportunity_outcomes WHERE outcome_kind = ?",
                [str(kind)]).fetchall()}
        finally:
            con.close()

        appended = 0
        for decision_id, action, side, ask, fee in rows:
            if decision_id in existing:
                continue
            pnl = None
            filled_size = None
            fill_price = None
            if action == Action.ENTER.value:
                filled_size = 1.0
                fill_price = float(ask)
                pnl = (1.0 if side == direction else 0.0) - float(ask) - float(fee or 0.0)
            elif action == Action.WAIT.value:
                filled_size = 0.0
                pnl = 0.0
            self.append_outcome(
                decision_id,
                kind=str(kind),
                outcome_ts=int(outcome_ts),
                filled_size=filled_size,
                fill_price=fill_price,
                settled_direction=direction,
                fees_paid=float(fee or 0.0) if action == Action.ENTER.value else 0.0,
                net_pnl=pnl,
                detail={"source": source, "settlement_price": settlement_price,
                        "paper_fill_assumption": "one share at recorded ask"},
            )
            appended += 1
        return appended

    def coverage(self) -> dict[str, Any]:
        """What a data-quality dashboard needs: are WAIT and UNAVAILABLE distinguishable?"""
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT action, count(*) FROM opportunity_decisions GROUP BY 1").fetchall()
            ages = con.execute(
                "SELECT median(state_age_ms), max(state_age_ms), median(quote_age_ms) "
                "FROM opportunity_decisions WHERE state_age_ms IS NOT NULL").fetchone()
            violations = con.execute(
                "SELECT count(*) FROM opportunity_decisions "
                "WHERE state_snapshot_ts > decision_ts OR quote_recv_ts > decision_ts"
            ).fetchone()[0]
            # An evaluated row with no provenance is not reproducible. It must be 0, and it is
            # reported rather than assumed so a dashboard can show it going wrong.
            unprovable = con.execute(
                "SELECT count(*) FROM opportunity_decisions WHERE action IN ('ENTER','WAIT') "
                "AND (model_artifact_hash IS NULL OR calibrator_hash IS NULL "
                "     OR policy_hash IS NULL OR feature_values_hash IS NULL "
                "     OR decision_context_json IS NULL)").fetchone()[0]
            skew = con.execute(
                "SELECT median(exchange_skew_ms), max(abs(exchange_skew_ms)) "
                "FROM opportunity_decisions WHERE exchange_skew_ms IS NOT NULL").fetchone()
        finally:
            con.close()
        return {"by_action": dict(rows),
                "median_state_age_ms": ages[0] if ages else None,
                "max_state_age_ms": ages[1] if ages else None,
                "median_quote_age_ms": ages[2] if ages else None,
                "median_exchange_skew_ms": skew[0] if skew else None,
                "max_abs_exchange_skew_ms": skew[1] if skew else None,
                "stored_causal_violations": int(violations),
                "stored_unreproducible_evaluations": int(unprovable)}


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


# ------------------------------------------------------------------------------------------
def _selftest() -> int:
    import tempfile
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    feature_values = {"raw_p_hold": 0.82, "ask": 0.70, "seconds_left": 25}
    decision_context = {
        "feature_values": feature_values,
        "model_outputs": {"calibrated_probability": 0.80},
    }
    base = dict(round_id="r1", strategy_id="PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1",
                market_id="m1", venue="polymarket",
                decision_ts=1_000_000, quote_exchange_ts=999_000, quote_recv_ts=999_500,
                state_snapshot_id="s-abc", state_snapshot_ts=998_000,
                feature_cutoff_ts=998_000, side="UP", ask=0.70, bid=0.68, fee=0.01,
                probability=0.80, model_artifact_hash="m", calibrator_hash="c",
                policy_hash="p", feature_values_hash=stable_hash(feature_values),
                decision_context=decision_context,
                action=Action.ENTER, reason="edge above margin")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        ledger = OpportunityLedger(Path(tmp) / "ledger.duckdb")

        did = ledger.record(Decision(**base), now_ms=1_000_050)
        check(bool(did), "a causal ENTER is recorded")

        # --- the defect that invalidated five studies must be impossible ------------------
        for field_name, bad_value, label in (
            ("state_snapshot_ts", 1_008_100, "a state from AFTER the decision is REFUSED"),
            ("quote_recv_ts", 1_000_500, "a quote received after the decision is REFUSED"),
            ("feature_cutoff_ts", 1_000_100, "a feature cutoff after the decision is REFUSED"),
        ):
            try:
                ledger.record(Decision(**{**base, field_name: bad_value}), now_ms=1_000_050)
                check(False, "unreachable")
            except NonCausalDecision as exc:
                check(field_name in str(exc), label)

        # --- cross-clock: drift is MEASURED, a swapped field is REFUSED --------------------
        ledger.record(Decision(**{**base, "round_id": "skew", "quote_exchange_ts": 999_900}),
                      now_ms=1_000_050)
        check(True, "venue stamp 400 ms ahead of receipt is STORED as skew, not refused - "
                    "two unsynchronised clocks are not a causality claim")
        try:
            ledger.record(Decision(**{**base, "round_id": "swap",
                                      "quote_exchange_ts": 999_500 + MAX_CLOCK_SKEW_MS + 1}),
                          now_ms=1_000_050)
            check(False, "unreachable")
        except NonCausalDecision as exc:
            check("clock-skew bound" in str(exc),
                  "a venue stamp beyond the declared skew bound is REFUSED as a wrong field")

        # --- an evaluated decision must be able to say what produced it -------------------
        for field_name in IDENTITY_FIELDS:
            for action in EVALUATED_ACTIONS:
                try:
                    ledger.record(Decision(**{**base, "round_id": f"id-{field_name}-{action}",
                                              "action": action, field_name: None}),
                                  now_ms=1_000_050)
                    check(False, "unreachable")
                except UnreproducibleDecision as exc:
                    assert field_name in str(exc)
        check(True, f"every evaluated action missing any of {len(IDENTITY_FIELDS)} identity "
                    f"hashes is REFUSED - a number nobody can re-derive is not evidence")

        try:
            ledger.record(Decision(**{**base, "round_id": "bad-context",
                                      "decision_context": {
                                          "feature_values": {"raw_p_hold": 0.01},
                                          "model_outputs": {"calibrated_probability": 0.80},
                                      }}), now_ms=1_000_050)
            check(False, "unreachable")
        except UnreproducibleDecision as exc:
            check("feature_values_hash" in str(exc),
                  "a context whose payload does not match its hash is REFUSED")

        try:
            ledger.record(Decision(**{**base, "round_id": "r2", "state_snapshot_id": None}),
                          now_ms=1_000_050)
            check(False, "unreachable")
        except NonCausalDecision as exc:
            check("state_snapshot_id" in str(exc),
                  "an ENTER without the EXACT state snapshot id is REFUSED")

        try:
            ledger.record(Decision(**{**base, "round_id": "r3"}), now_ms=999_000)
            check(False, "unreachable")
        except NonCausalDecision:
            check(True, "a row written before it was decided is REFUSED")

        # --- the non-acting states must be recordable AND distinguishable ------------------
        # The three non-evaluated actions are written with NO identity at all, exactly as the
        # live path writes them: there was no computation, so there is nothing to name.
        ledger.record(Decision(**{**base, "round_id": "w1", "action": Action.WAIT,
                                  "reason": "edge below margin"}), now_ms=1_000_050)
        for action, rid in ((Action.UNAVAILABLE, "u1"), (Action.NO_QUOTE, "n1"),
                            (Action.BLOCKED, "b1")):
            blank = {f: None for f in IDENTITY_FIELDS}
            ledger.record(Decision(**{**base, "round_id": rid, "action": action,
                                      "side": None, "ask": None, "probability": None,
                                      "decision_context": None,
                                      **blank,
                                      "reason": f"{action.value} path"}), now_ms=1_000_050)
        check(True, "a NON-evaluated action records with no identity - it computed nothing")
        cov = ledger.coverage()
        check(cov["by_action"].get("WAIT") == 1 and cov["by_action"].get("UNAVAILABLE") == 1,
              "WAIT and UNAVAILABLE are recorded as DISTINCT states, never merged")
        check(cov["by_action"].get("NO_QUOTE") == 1 and cov["by_action"].get("BLOCKED") == 1,
              "NO_QUOTE and BLOCKED are recorded separately")
        check(cov["stored_causal_violations"] == 0,
              "no stored row violates causality - the table cannot contain one")
        check(cov["stored_unreproducible_evaluations"] == 0,
              "no stored ENTER/WAIT lacks provenance - every evaluated row is re-derivable")
        check(cov["median_exchange_skew_ms"] is not None,
              "venue-vs-local clock skew is MEASURED and reportable, not silently discarded")
        check(cov["median_state_age_ms"] == 2000,
              "state age is STORED (2000 ms), so staleness is a research filter not a silent drop")

        # --- outcomes append, never edit ---------------------------------------------------
        ledger.append_outcome(did, kind="SETTLEMENT", outcome_ts=1_300_000,
                              settled_direction="UP", net_pnl=0.29)
        check(ledger.append_settlement_for_round(
            "r1", settled_direction="UP", outcome_ts=1_300_001,
            kind="SETTLEMENT_OFFICIAL", settlement_price=101.0,
            source="official:test") == 1,
              "official settlement is appended automatically for the ENTER decision")
        check(ledger.append_settlement_for_round(
            "w1", settled_direction="DOWN", outcome_ts=1_300_001,
            kind="SETTLEMENT_OFFICIAL", source="official:test") == 1,
              "WAIT receives an explicit zero-PnL outcome instead of disappearing")
        check(ledger.append_settlement_for_round(
            "r1", settled_direction="UP", outcome_ts=1_300_002,
            kind="SETTLEMENT_OFFICIAL", source="official:test") == 0,
              "reprocessing the same official settlement is idempotent")
        try:
            ledger.append_outcome(did, kind="IMPOSSIBLE", outcome_ts=999_999)
            check(False, "unreachable")
        except NonCausalDecision:
            check(True, "an outcome that predates its decision is REFUSED")
        try:
            ledger.append_outcome("not-a-decision", kind="SETTLEMENT", outcome_ts=1_300_000)
            check(False, "unreachable")
        except KeyError:
            check(True, "an outcome cannot invent a decision that was never recorded")

        con = ledger._connect()
        try:
            cols = {r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'opportunity_decisions'").fetchall()}
        finally:
            con.close()

        wait_row = ledger._connect()
        try:
            stored_wait = wait_row.execute(
                "SELECT side, ask, probability, decision_context_json "
                "FROM opportunity_decisions WHERE round_id = 'w1'").fetchone()
        finally:
            wait_row.close()
        check(stored_wait[:3] == ("UP", 0.70, 0.80) and stored_wait[3],
              "WAIT retains the exact quote, probability and context it declined")
        check("net_pnl" not in cols,
              "the decision table has NO outcome column - hindsight cannot be written into it")

    print(f"\nOPPORTUNITY LEDGER SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return _selftest()
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
