"""Slow, bounded online expert weighting from resolved forward evidence only."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
from threading import RLock
from typing import Iterator, Mapping

import duckdb

from .forecast_ledger import EvidenceKind


_SCHEMA = """
CREATE TABLE IF NOT EXISTS online_expert_loss_events(
    event_index BIGINT UNIQUE,
    update_id VARCHAR NOT NULL,
    resolved_at_ns BIGINT NOT NULL,
    ensemble_key VARCHAR NOT NULL,
    regime VARCHAR NOT NULL,
    model_id VARCHAR NOT NULL,
    evidence_kind VARCHAR NOT NULL,
    loss DOUBLE NOT NULL,
    previous_sha256 VARCHAR NOT NULL,
    event_sha256 VARCHAR PRIMARY KEY,
    UNIQUE(update_id, model_id)
)
"""


def _bounded_normalize(
    values: Mapping[str, float],
    minimum: float,
    maximum: float,
) -> dict[str, float]:
    total = sum(values.values())
    if total <= 0:
        raise ValueError("online weights lost all mass")
    result = {
        key: min(maximum, max(minimum, value / total))
        for key, value in values.items()
    }
    for _ in range(100):
        difference = 1.0 - sum(result.values())
        if abs(difference) <= 1e-12:
            return result
        if difference > 0:
            room = {
                key: maximum - value
                for key, value in result.items()
                if value < maximum
            }
        else:
            room = {
                key: value - minimum
                for key, value in result.items()
                if value > minimum
            }
        available = sum(room.values())
        if available <= 0:
            raise ValueError("weight bounds cannot be normalized")
        amount = min(abs(difference), available)
        sign = 1.0 if difference > 0 else -1.0
        for key, capacity in room.items():
            result[key] += sign * amount * capacity / available
    raise RuntimeError("bounded weight normalization did not converge")


class OnlineExpertWeighting:
    """Append-only replayable shadow weights.

    This class has no active-model pointer and no serving integration. Rollback
    is achieved by replaying only events up to a chosen timestamp.
    """

    shadow_only = True

    def __init__(
        self,
        path: str | Path,
        *,
        learning_rate: float = 0.02,
        minimum_weight: float = 0.05,
        maximum_weight: float = 0.70,
        minimum_resolved_updates: int = 50,
    ):
        if not 0 < learning_rate <= 0.10:
            raise ValueError("learning_rate must be in (0, 0.10]")
        if not 0 <= minimum_weight < maximum_weight <= 1:
            raise ValueError("invalid weight bounds")
        if minimum_resolved_updates <= 0:
            raise ValueError("minimum_resolved_updates must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.learning_rate = learning_rate
        self.minimum_weight = minimum_weight
        self.maximum_weight = maximum_weight
        self.minimum_resolved_updates = minimum_resolved_updates
        self._lock = RLock()
        with self._connect() as con:
            con.execute(_SCHEMA)
            con.execute(
                "CREATE INDEX IF NOT EXISTS online_expert_replay_idx "
                "ON online_expert_loss_events("
                "ensemble_key, regime, resolved_at_ns, event_index)"
            )

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(str(self.path))
        try:
            yield con
        finally:
            con.close()

    def append_resolved_losses(
        self,
        *,
        update_id: str,
        resolved_at_ns: int,
        ensemble_key: str,
        regime: str,
        losses: Mapping[str, float],
        evidence_kind: EvidenceKind,
    ) -> None:
        if evidence_kind is not EvidenceKind.FORWARD:
            raise ValueError("online weights accept resolved forward evidence only")
        if (
            not update_id.strip()
            or not ensemble_key.strip()
            or not regime.strip()
            or resolved_at_ns <= 0
            or not losses
        ):
            raise ValueError("complete online update identity is required")
        if any(
            not model_id.strip()
            or not math.isfinite(float(loss))
            or float(loss) < 0
            for model_id, loss in losses.items()
        ):
            raise ValueError("model losses must be finite and non-negative")
        with self._lock, self._connect() as con:
            con.execute("BEGIN TRANSACTION")
            try:
                existing = con.execute(
                    "SELECT resolved_at_ns, ensemble_key, regime, evidence_kind, "
                    "model_id, loss FROM online_expert_loss_events "
                    "WHERE update_id = ? ORDER BY model_id",
                    [update_id],
                ).fetchall()
                expected = sorted(
                    (
                        resolved_at_ns,
                        ensemble_key,
                        regime,
                        evidence_kind.value,
                        model_id,
                        float(loss),
                    )
                    for model_id, loss in losses.items()
                )
                if existing:
                    stored = [
                        (
                            int(row[0]),
                            str(row[1]),
                            str(row[2]),
                            str(row[3]),
                            str(row[4]),
                            float(row[5]),
                        )
                        for row in existing
                    ]
                    if stored != expected:
                        raise ValueError(
                            "update_id collision with different immutable losses"
                        )
                    con.execute("COMMIT")
                    return
                last = con.execute(
                    "SELECT event_index, event_sha256 "
                    "FROM online_expert_loss_events ORDER BY event_index DESC LIMIT 1"
                ).fetchone()
                event_index = int(last[0]) + 1 if last else 0
                previous = str(last[1]) if last else "GENESIS"
                for _, _, _, _, model_id, loss in expected:
                    payload = {
                        "update_id": update_id,
                        "resolved_at_ns": resolved_at_ns,
                        "ensemble_key": ensemble_key,
                        "regime": regime,
                        "model_id": model_id,
                        "evidence_kind": evidence_kind.value,
                        "loss": loss,
                        "previous_sha256": previous,
                    }
                    encoded = json.dumps(
                        payload, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")
                    digest = hashlib.sha256(encoded).hexdigest()
                    con.execute(
                        "INSERT INTO online_expert_loss_events "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        [
                            event_index,
                            update_id,
                            resolved_at_ns,
                            ensemble_key,
                            regime,
                            model_id,
                            evidence_kind.value,
                            loss,
                            previous,
                            digest,
                        ],
                    )
                    event_index += 1
                    previous = digest
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise

    def weights(
        self,
        *,
        ensemble_key: str,
        regime: str,
        model_ids: tuple[str, ...],
        through_ns: int | None = None,
    ) -> dict[str, float]:
        if not model_ids or len(set(model_ids)) != len(model_ids):
            raise ValueError("model_ids must be unique and non-empty")
        count = len(model_ids)
        if count * self.minimum_weight > 1 or count * self.maximum_weight < 1:
            raise ValueError("weight bounds are infeasible for model count")
        query = (
            "SELECT update_id, resolved_at_ns, model_id, loss "
            "FROM online_expert_loss_events "
            "WHERE ensemble_key = ? AND regime = ?"
        )
        params: list[object] = [ensemble_key, regime]
        if through_ns is not None:
            query += " AND resolved_at_ns <= ?"
            params.append(through_ns)
        query += " ORDER BY resolved_at_ns, update_id, model_id"
        with self._connect() as con:
            rows = con.execute(query, params).fetchall()
        by_update: dict[str, dict[str, float]] = {}
        for update_id, _, model_id, loss in rows:
            by_update.setdefault(str(update_id), {})[str(model_id)] = float(loss)
        base = {model_id: 1.0 / count for model_id in model_ids}
        complete = [
            losses
            for losses in by_update.values()
            if set(losses) == set(model_ids)
        ]
        if len(complete) < self.minimum_resolved_updates:
            return base
        weights = base
        for losses in complete:
            unnormalized = {
                model_id: weights[model_id]
                * math.exp(-self.learning_rate * losses[model_id])
                for model_id in model_ids
            }
            total = sum(unnormalized.values())
            normalized = {
                model_id: value / total for model_id, value in unnormalized.items()
            }
            weights = _bounded_normalize(
                normalized,
                self.minimum_weight,
                self.maximum_weight,
            )
        return weights

    def verify_chain(self) -> tuple[bool, list[str]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT update_id, resolved_at_ns, ensemble_key, regime, model_id, "
                "evidence_kind, loss, previous_sha256, event_sha256 "
                "FROM online_expert_loss_events ORDER BY event_index"
            ).fetchall()
        previous = "GENESIS"
        reasons: list[str] = []
        for row in rows:
            (
                update_id,
                resolved_at_ns,
                ensemble_key,
                regime,
                model_id,
                evidence_kind,
                loss,
                stored_previous,
                digest,
            ) = row
            payload = {
                "update_id": update_id,
                "resolved_at_ns": resolved_at_ns,
                "ensemble_key": ensemble_key,
                "regime": regime,
                "model_id": model_id,
                "evidence_kind": evidence_kind,
                "loss": loss,
                "previous_sha256": stored_previous,
            }
            expected = hashlib.sha256(
                json.dumps(
                    payload, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            if stored_previous != previous:
                reasons.append(f"chain_break:{update_id}:{model_id}")
            if digest != expected:
                reasons.append(f"hash_mismatch:{update_id}:{model_id}")
            previous = str(digest)
        return not reasons, reasons
