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
    quote_exchange_ts  <= quote_recv_ts     an exchange event precedes its own receipt
    decision_ts        <= written_ts        a row cannot be written before it is decided

    A violation is a programming error in the caller, not a market condition, so `record()`
    RAISES. Degrading to a warning would reproduce exactly the failure this module exists to
    prevent: a row that looks like evidence and is not.

    Staleness is separate from causality. A state 90 seconds old is causal and probably useless,
    so `state_age_ms` is STORED rather than judged, and research filters on it with a declared
    threshold. Enforcing an age here would silently reshape the population.

EVERY OPPORTUNITY IS RECORDED, INCLUDING THE ONES NOT TAKEN
    ENTER        the strategy acted
    WAIT         evaluated, declined - the edge was not there
    NO_QUOTE     market data unavailable at the decision instant
    UNAVAILABLE  the strategy COULD NOT evaluate: artifact, calibrator or feed invalid
    BLOCKED      a risk, identity or data gate refused authority

    WAIT and UNAVAILABLE must never collapse into each other. "The strategy looked and declined"
    and "the strategy was dead and nobody noticed" produce the same empty P&L and mean opposite
    things. PM_CALIBRATED_FAIR_VALUE_V1 is UNAVAILABLE today - its calibrator cannot deploy while
    the source artifacts fail identity - and that must be visible as a distinct state.

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

SCHEMA_VERSION = "opportunity-ledger-v1"


class Action(str, Enum):
    ENTER = "ENTER"
    WAIT = "WAIT"
    NO_QUOTE = "NO_QUOTE"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"


class NonCausalDecision(Exception):
    """Raised when a decision row would use an input that did not yet exist."""


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
        if (self.quote_exchange_ts is not None and self.quote_recv_ts is not None
                and self.quote_exchange_ts > self.quote_recv_ts):
            problems.append(
                f"quote_exchange_ts {self.quote_exchange_ts} is after quote_recv_ts "
                f"{self.quote_recv_ts} - an exchange event cannot follow its own receipt")
        if self.action is Action.ENTER:
            # An ENTER with no price or no probability is not a decision anyone can audit.
            for name in ("side", "ask", "probability"):
                if getattr(self, name) is None:
                    problems.append(f"action is ENTER but {name} is None")
            if self.state_snapshot_id is None:
                problems.append(
                    "action is ENTER but state_snapshot_id is None - the EXACT state used must "
                    "be referenced, never reconstructed later by an as-of join")
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
                    written_ts BIGINT NOT NULL
                )""")
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

        con = self._connect()
        try:
            con.execute(
                "INSERT INTO opportunity_decisions VALUES ("
                + ",".join(["?"] * 28) + ")",
                [decision_id, SCHEMA_VERSION, decision.round_id, decision.strategy_id,
                 decision.market_id, decision.venue, decision.decision_ts,
                 decision.quote_exchange_ts, decision.quote_recv_ts,
                 decision.state_snapshot_id, decision.state_snapshot_ts,
                 decision.feature_cutoff_ts, state_age, quote_age,
                 decision.side, decision.ask, decision.bid, decision.fee,
                 decision.probability, decision.model_artifact_hash,
                 decision.calibrator_hash, decision.policy_hash,
                 decision.feature_values_hash, decision.action.value, decision.reason,
                 decision.requested_size, decision.risk_state, written])
        finally:
            con.close()
        return decision_id

    def append_outcome(self, decision_id: str, *, kind: str, outcome_ts: int,
                       **fields: Any) -> None:
        """Outcomes append; they never touch the decision row."""
        con = self._connect()
        try:
            exists = con.execute(
                "SELECT count(*) FROM opportunity_decisions WHERE decision_id = ?",
                [decision_id]).fetchone()[0]
            if not exists:
                raise KeyError(f"no decision {decision_id}; an outcome cannot invent one")
            con.execute(
                "INSERT INTO opportunity_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)",
                [decision_id, kind, int(outcome_ts),
                 fields.get("filled_size"), fields.get("fill_price"),
                 fields.get("exit_price"), fields.get("settled_direction"),
                 fields.get("fees_paid"), fields.get("net_pnl"),
                 json.dumps(fields.get("detail") or {}, sort_keys=True, default=str)])
        finally:
            con.close()

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
        finally:
            con.close()
        return {"by_action": dict(rows),
                "median_state_age_ms": ages[0] if ages else None,
                "max_state_age_ms": ages[1] if ages else None,
                "median_quote_age_ms": ages[2] if ages else None,
                "stored_causal_violations": int(violations)}


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

    base = dict(round_id="r1", strategy_id="PM_CALIBRATED_FAIR_VALUE_V1",
                market_id="m1", venue="polymarket",
                decision_ts=1_000_000, quote_exchange_ts=999_000, quote_recv_ts=999_500,
                state_snapshot_id="s-abc", state_snapshot_ts=998_000,
                feature_cutoff_ts=998_000, side="UP", ask=0.70, bid=0.68, fee=0.01,
                probability=0.80, model_artifact_hash="m", calibrator_hash="c",
                policy_hash="p", feature_values_hash="f",
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

        try:
            ledger.record(Decision(**{**base, "quote_exchange_ts": 999_900}), now_ms=1_000_050)
            check(False, "unreachable")
        except NonCausalDecision:
            check(True, "an exchange event after its own receipt is REFUSED")

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
        for action, rid in ((Action.WAIT, "w1"), (Action.UNAVAILABLE, "u1"),
                            (Action.NO_QUOTE, "n1"), (Action.BLOCKED, "b1")):
            ledger.record(Decision(**{**base, "round_id": rid, "action": action,
                                      "side": None, "ask": None, "probability": None,
                                      "reason": f"{action.value} path"}), now_ms=1_000_050)
        cov = ledger.coverage()
        check(cov["by_action"].get("WAIT") == 1 and cov["by_action"].get("UNAVAILABLE") == 1,
              "WAIT and UNAVAILABLE are recorded as DISTINCT states, never merged")
        check(cov["by_action"].get("NO_QUOTE") == 1 and cov["by_action"].get("BLOCKED") == 1,
              "NO_QUOTE and BLOCKED are recorded separately")
        check(cov["stored_causal_violations"] == 0,
              "no stored row violates causality - the table cannot contain one")
        check(cov["median_state_age_ms"] == 2000,
              "state age is STORED (2000 ms), so staleness is a research filter not a silent drop")

        # --- outcomes append, never edit ---------------------------------------------------
        ledger.append_outcome(did, kind="SETTLEMENT", outcome_ts=1_300_000,
                              settled_direction="UP", net_pnl=0.29)
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
