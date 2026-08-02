"""Append-only evidence for every live ensemble revision and its later price outcomes.

The ordinary prediction tables intentionally keep one prediction per horizon cadence. Research
on forecast stability, confidence collapse and revision overshoot needs the revisions *between*
those cadence points too. This ledger stores those revisions without mutating them later:

* one compressed, exact float32 model-input snapshot per prediction cycle;
* one revision row per model/horizon, linked to the previous revision;
* later markouts in a separate table, with observed latency recorded explicitly.

No trading authority reads this database. It is forward evidence only.

    python backend/model_revision_ledger.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Iterable

import numpy as np


SCHEMA_VERSION = "model-revision-ledger-v1"
STANDARD_MARKOUTS_MS = (1_000, 5_000, 15_000, 30_000, 60_000, 120_000)
VALID_DIRECTIONS = frozenset({"UP", "DOWN", "NEUTRAL"})


class RevisionRefusal(ValueError):
    """A revision or outcome cannot be stored without corrupting causal evidence."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _finite_probability(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RevisionRefusal(f"{name} is not numeric: {value!r}") from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise RevisionRefusal(f"{name} must be finite and in [0,1], got {result!r}")
    return result


def forecast_identity(prediction: dict[str, Any]) -> tuple[str, float, str]:
    """Separate the model forecast from a later server-side no-trade decision."""
    direction = str(
        prediction.get("preServerDirection", prediction.get("direction", "NEUTRAL"))
    ).upper()
    if direction not in VALID_DIRECTIONS:
        raise RevisionRefusal(f"invalid pre-server prediction {direction!r}")
    if direction in ("UP", "DOWN"):
        calibrated = prediction.get("calibratedConfidence")
        source = "live_isotonic" if calibrated is not None else "ensemble_confidence"
        if calibrated is None:
            calibrated = prediction.get("confidence", 0.0)
    else:
        calibrated = prediction.get("probNeutral", prediction.get("confidence", 0.0))
        source = "ensemble_neutral_probability"
    return direction, _finite_probability(calibrated, "calibrated_probability"), source


class ModelRevisionLedger:
    """Thread-safe append-only DuckDB ledger for model revisions and outcomes."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self, *, read_only: bool = False):
        import duckdb

        return duckdb.connect(str(self.db_path), read_only=read_only)

    def _ensure_schema(self) -> None:
        with self._lock:
            con = self._connect()
            try:
                con.execute("""
                    CREATE TABLE IF NOT EXISTS model_state_snapshots (
                        state_snapshot_id VARCHAR PRIMARY KEY,
                        schema_version VARCHAR NOT NULL,
                        snapshot_ts BIGINT NOT NULL,
                        feature_cutoff_ts BIGINT NOT NULL,
                        feature_names_json VARCHAR NOT NULL,
                        feature_shape_json VARCHAR NOT NULL,
                        feature_values_zlib BLOB NOT NULL,
                        feature_values_hash VARCHAR NOT NULL,
                        written_ts BIGINT NOT NULL
                    )
                """)
                con.execute("""
                    CREATE TABLE IF NOT EXISTS model_revisions (
                        revision_id VARCHAR PRIMARY KEY,
                        schema_version VARCHAR NOT NULL,
                        release_id VARCHAR NOT NULL,
                        model_id VARCHAR NOT NULL,
                        horizon_min INTEGER NOT NULL,
                        prediction_ts BIGINT NOT NULL,
                        prediction VARCHAR NOT NULL,
                        calibrated_probability DOUBLE NOT NULL,
                        probability_up DOUBLE NOT NULL,
                        probability_down DOUBLE NOT NULL,
                        probability_neutral DOUBLE NOT NULL,
                        previous_revision_id VARCHAR,
                        previous_prediction VARCHAR,
                        state_snapshot_id VARCHAR NOT NULL,
                        feature_values_hash VARCHAR NOT NULL,
                        reference_price DOUBLE NOT NULL,
                        market_quote_json VARCHAR NOT NULL,
                        model_outputs_json VARCHAR NOT NULL,
                        revision_payload_hash VARCHAR NOT NULL,
                        written_ts BIGINT NOT NULL
                    )
                """)
                con.execute("""
                    CREATE TABLE IF NOT EXISTS model_revision_outcomes (
                        revision_id VARCHAR NOT NULL,
                        outcome_kind VARCHAR NOT NULL,
                        target_ts BIGINT NOT NULL,
                        observed_ts BIGINT NOT NULL,
                        observed_price DOUBLE NOT NULL,
                        markout_usd DOUBLE NOT NULL,
                        actual_direction VARCHAR NOT NULL,
                        correct BOOLEAN NOT NULL,
                        observation_latency_ms BIGINT NOT NULL,
                        detail_json VARCHAR NOT NULL,
                        PRIMARY KEY (revision_id, outcome_kind)
                    )
                """)
            finally:
                con.close()

    @staticmethod
    def _encode_state(feature_values: Any, feature_names: Iterable[str]) -> dict[str, Any]:
        array = np.ascontiguousarray(np.asarray(feature_values, dtype=np.float32))
        if array.ndim != 2 or array.shape[0] < 1 or array.shape[1] < 1:
            raise RevisionRefusal(f"model input must be a non-empty 2D array, got {array.shape}")
        names = [str(name) for name in feature_names]
        if len(names) != array.shape[1]:
            raise RevisionRefusal(
                f"feature-name count {len(names)} does not match input width {array.shape[1]}"
            )
        names_json = _canonical_json(names)
        raw = array.tobytes(order="C")
        feature_hash = hashlib.sha256(names_json.encode("utf-8") + b"\0" + raw).hexdigest()
        return {
            "feature_names_json": names_json,
            "feature_shape_json": _canonical_json(list(array.shape)),
            "feature_values_zlib": zlib.compress(raw, level=6),
            "feature_values_hash": feature_hash,
        }

    @staticmethod
    def decode_state(blob: bytes, shape_json: str) -> np.ndarray:
        """Decode an exact input snapshot for audit/reproduction tooling."""
        shape = tuple(int(value) for value in json.loads(shape_json))
        raw = zlib.decompress(bytes(blob))
        expected = int(np.prod(shape)) * np.dtype(np.float32).itemsize
        if len(raw) != expected:
            raise RevisionRefusal(
                f"state payload has {len(raw)} bytes; shape {shape} requires {expected}"
            )
        return np.frombuffer(raw, dtype=np.float32).reshape(shape).copy()

    def record_batch(
        self,
        revisions: Iterable[dict[str, Any]],
        *,
        feature_values: Any,
        feature_names: Iterable[str],
        snapshot_ts: int,
        feature_cutoff_ts: int,
        now_ms: int | None = None,
    ) -> list[str]:
        """Store one prediction cycle atomically and return revision ids in input order."""
        rows = [dict(row) for row in revisions]
        if not rows:
            return []
        snapshot_ts = int(snapshot_ts)
        feature_cutoff_ts = int(feature_cutoff_ts)
        written_ts = int(_now_ms() if now_ms is None else now_ms)
        if feature_cutoff_ts > snapshot_ts:
            raise RevisionRefusal("feature_cutoff_ts is after state snapshot_ts")
        if written_ts < snapshot_ts:
            raise RevisionRefusal("written_ts is before state snapshot_ts")

        encoded = self._encode_state(feature_values, feature_names)
        state_snapshot_id = _stable_hash({
            "snapshot_ts": snapshot_ts,
            "feature_cutoff_ts": feature_cutoff_ts,
            "feature_values_hash": encoded["feature_values_hash"],
        })

        with self._lock:
            con = self._connect()
            try:
                con.execute("BEGIN TRANSACTION")
                con.execute(
                    """
                    INSERT INTO model_state_snapshots VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT (state_snapshot_id) DO NOTHING
                    """,
                    [state_snapshot_id, SCHEMA_VERSION, snapshot_ts, feature_cutoff_ts,
                     encoded["feature_names_json"], encoded["feature_shape_json"],
                     encoded["feature_values_zlib"], encoded["feature_values_hash"], written_ts],
                )
                revision_ids: list[str] = []
                for row in rows:
                    release_id = str(row.get("release_id") or "").strip()
                    model_id = str(row.get("model_id") or "").strip()
                    horizon = int(row.get("horizon_min") or 0)
                    prediction_ts = int(row.get("prediction_ts") or 0)
                    prediction = str(row.get("prediction") or "").upper()
                    if not release_id or not model_id or horizon <= 0 or prediction_ts <= 0:
                        raise RevisionRefusal("release_id, model_id, positive horizon and timestamp are required")
                    if prediction not in VALID_DIRECTIONS:
                        raise RevisionRefusal(f"invalid prediction {prediction!r}")
                    if snapshot_ts > prediction_ts or feature_cutoff_ts > prediction_ts:
                        raise RevisionRefusal("model state or feature cutoff is after prediction_ts")

                    probability_up = _finite_probability(row.get("probability_up"), "probability_up")
                    probability_down = _finite_probability(row.get("probability_down"), "probability_down")
                    probability_neutral = _finite_probability(
                        row.get("probability_neutral"), "probability_neutral"
                    )
                    if abs(probability_up + probability_down + probability_neutral - 1.0) > 0.02:
                        raise RevisionRefusal("UP/DOWN/NEUTRAL probabilities do not sum to one")
                    calibrated_probability = _finite_probability(
                        row.get("calibrated_probability"), "calibrated_probability"
                    )
                    reference_price = float(row.get("reference_price") or 0.0)
                    if not math.isfinite(reference_price) or reference_price <= 0:
                        raise RevisionRefusal("reference_price must be positive and finite")
                    market_quote = row.get("market_quote")
                    model_outputs = row.get("model_outputs")
                    if not isinstance(market_quote, dict) or not isinstance(model_outputs, dict):
                        raise RevisionRefusal("market_quote and model_outputs must be objects")

                    latest = con.execute(
                        """
                        SELECT revision_id, prediction, prediction_ts FROM model_revisions
                        WHERE release_id = ? AND model_id = ? AND horizon_min = ?
                        ORDER BY prediction_ts DESC LIMIT 1
                        """,
                        [release_id, model_id, horizon],
                    ).fetchone()
                    if latest and int(latest[2]) > prediction_ts:
                        raise RevisionRefusal(
                            f"out-of-order revision {prediction_ts} follows stored {latest[2]}"
                        )
                    previous = con.execute(
                        """
                        SELECT revision_id, prediction FROM model_revisions
                        WHERE release_id = ? AND model_id = ? AND horizon_min = ?
                          AND prediction_ts < ?
                        ORDER BY prediction_ts DESC LIMIT 1
                        """,
                        [release_id, model_id, horizon, prediction_ts],
                    ).fetchone()
                    previous_id = str(previous[0]) if previous else None
                    previous_prediction = str(previous[1]) if previous else None
                    revision_id = _stable_hash({
                        "release_id": release_id,
                        "model_id": model_id,
                        "horizon_min": horizon,
                        "prediction_ts": prediction_ts,
                    })
                    payload = {
                        "prediction": prediction,
                        "calibrated_probability": calibrated_probability,
                        "probability_up": probability_up,
                        "probability_down": probability_down,
                        "probability_neutral": probability_neutral,
                        "state_snapshot_id": state_snapshot_id,
                        "reference_price": reference_price,
                        "market_quote": market_quote,
                        "model_outputs": model_outputs,
                    }
                    payload_hash = _stable_hash(payload)
                    existing = con.execute(
                        "SELECT revision_payload_hash FROM model_revisions WHERE revision_id = ?",
                        [revision_id],
                    ).fetchone()
                    if existing:
                        if str(existing[0]) != payload_hash:
                            raise RevisionRefusal(
                                f"revision {revision_id} already exists with a different payload"
                            )
                        revision_ids.append(revision_id)
                        continue
                    con.execute(
                        """
                        INSERT INTO model_revisions VALUES (
                            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                        )
                        """,
                        [revision_id, SCHEMA_VERSION, release_id, model_id, horizon,
                         prediction_ts, prediction, calibrated_probability, probability_up,
                         probability_down, probability_neutral, previous_id, previous_prediction,
                         state_snapshot_id, encoded["feature_values_hash"], reference_price,
                         _canonical_json(market_quote), _canonical_json(model_outputs),
                         payload_hash, written_ts],
                    )
                    revision_ids.append(revision_id)
                con.execute("COMMIT")
                return revision_ids
            except Exception:
                try:
                    con.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                con.close()

    def resolve_due(
        self,
        *,
        observed_price: float,
        observed_ts: int,
        maximum_lateness_ms: int = 10_000,
    ) -> int:
        """Append due outcomes observed close enough to their declared target timestamp.

        Missing observations remain missing. A price seen after a restart is never backfilled as
        though it existed at an earlier target time.
        """
        price = float(observed_price)
        observed_ts = int(observed_ts)
        maximum_lateness_ms = max(0, int(maximum_lateness_ms))
        if not math.isfinite(price) or price <= 0:
            raise RevisionRefusal("observed_price must be positive and finite")
        maximum_offset = max(STANDARD_MARKOUTS_MS[-1], 24 * 60 * 60 * 1000)
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT revision_id, prediction_ts, horizon_min, prediction, reference_price
                    FROM model_revisions
                    WHERE prediction_ts <= ? AND prediction_ts >= ?
                    """,
                    [observed_ts, observed_ts - maximum_offset - maximum_lateness_ms],
                ).fetchall()
                appended = 0
                for revision_id, prediction_ts, horizon, prediction, reference_price in rows:
                    offsets = [(f"MARKOUT_{offset}MS", offset) for offset in STANDARD_MARKOUTS_MS]
                    offsets.append((f"HORIZON_{int(horizon)}M", int(horizon) * 60_000))
                    for kind, offset in offsets:
                        target_ts = int(prediction_ts) + int(offset)
                        latency = observed_ts - target_ts
                        if latency < 0 or latency > maximum_lateness_ms:
                            continue
                        markout = price - float(reference_price)
                        actual = "UP" if markout > 0 else "DOWN" if markout < 0 else "NEUTRAL"
                        before = con.execute(
                            "SELECT 1 FROM model_revision_outcomes "
                            "WHERE revision_id = ? AND outcome_kind = ?",
                            [revision_id, kind],
                        ).fetchone()
                        if before:
                            continue
                        con.execute(
                            """
                            INSERT INTO model_revision_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)
                            """,
                            [revision_id, kind, target_ts, observed_ts, price, markout, actual,
                             str(prediction) == actual, latency,
                             _canonical_json({"source": "live_binance_price",
                                              "maximum_lateness_ms": maximum_lateness_ms})],
                        )
                        appended += 1
                return appended
            finally:
                con.close()

    def coverage(self) -> dict[str, Any]:
        with self._lock:
            con = self._connect(read_only=True)
            try:
                revisions = int(con.execute("SELECT count(*) FROM model_revisions").fetchone()[0])
                outcomes = int(con.execute(
                    "SELECT count(*) FROM model_revision_outcomes").fetchone()[0])
                horizons = dict(con.execute(
                    "SELECT horizon_min, count(*) FROM model_revisions GROUP BY 1 ORDER BY 1"
                ).fetchall())
                linked = int(con.execute(
                    "SELECT count(*) FROM model_revisions WHERE previous_revision_id IS NOT NULL"
                ).fetchone()[0])
                causal = int(con.execute(
                    """
                    SELECT count(*) FROM model_revisions r
                    JOIN model_state_snapshots s USING (state_snapshot_id)
                    WHERE s.snapshot_ts > r.prediction_ts
                       OR s.feature_cutoff_ts > r.prediction_ts
                    """
                ).fetchone()[0])
            finally:
                con.close()
        return {
            "revisions": revisions,
            "outcomes": outcomes,
            "by_horizon": horizons,
            "linked_to_previous": linked,
            "stored_causal_violations": causal,
        }


def _selftest() -> int:
    import tempfile

    checks = 0

    def check(condition: bool, label: str) -> None:
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    with tempfile.TemporaryDirectory() as tmp:
        ledger = ModelRevisionLedger(Path(tmp) / "revisions.duckdb")
        state = np.arange(12, dtype=np.float32).reshape(3, 4)
        base = {
            "release_id": "release-a",
            "model_id": "main_ensemble",
            "horizon_min": 1,
            "prediction_ts": 10_000,
            "prediction": "UP",
            "calibrated_probability": 0.62,
            "probability_up": 0.62,
            "probability_down": 0.28,
            "probability_neutral": 0.10,
            "reference_price": 100.0,
            "market_quote": {"venue": "BINANCE", "bid": 99.9, "ask": 100.1},
            "model_outputs": {"raw": "UP", "final": "UP"},
        }
        ids = ledger.record_batch(
            [base], feature_values=state, feature_names=["a", "b", "c", "d"],
            snapshot_ts=9_900, feature_cutoff_ts=9_000, now_ms=10_001,
        )
        check(len(ids) == 1, "one revision is recorded")
        duplicate = ledger.record_batch(
            [base], feature_values=state, feature_names=["a", "b", "c", "d"],
            snapshot_ts=9_900, feature_cutoff_ts=9_000, now_ms=10_002,
        )
        check(duplicate == ids and ledger.coverage()["revisions"] == 1,
              "an identical retry is idempotent")

        second = {**base, "prediction_ts": 12_000, "prediction": "DOWN",
                  "calibrated_probability": 0.55, "probability_up": 0.35,
                  "probability_down": 0.55}
        ledger.record_batch(
            [second], feature_values=state + 1, feature_names=["a", "b", "c", "d"],
            snapshot_ts=11_900, feature_cutoff_ts=11_000, now_ms=12_001,
        )
        check(ledger.coverage()["linked_to_previous"] == 1,
              "the later revision links to its predecessor")

        con = ledger._connect(read_only=True)
        try:
            stored = con.execute(
                "SELECT feature_values_zlib, feature_shape_json FROM model_state_snapshots "
                "ORDER BY snapshot_ts LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        check(np.array_equal(ModelRevisionLedger.decode_state(stored[0], stored[1]), state),
              "the exact float32 model input round-trips")

        appended = ledger.resolve_due(observed_price=101.0, observed_ts=11_000,
                                      maximum_lateness_ms=0)
        check(appended == 1, "the exact 1-second markout is appended once")
        check(ledger.resolve_due(observed_price=101.0, observed_ts=11_000,
                                 maximum_lateness_ms=0) == 0,
              "outcome writes are idempotent")

        try:
            ledger.record_batch(
                [{**base, "prediction_ts": 13_000}], feature_values=state,
                feature_names=["a", "b", "c", "d"], snapshot_ts=13_001,
                feature_cutoff_ts=12_000, now_ms=13_002,
            )
            check(False, "unreachable")
        except RevisionRefusal:
            check(True, "future model state is refused")

        try:
            ledger.record_batch(
                [{**base, "probability_up": 0.9}], feature_values=state,
                feature_names=["a", "b", "c", "d"], snapshot_ts=9_900,
                feature_cutoff_ts=9_000, now_ms=10_002,
            )
            check(False, "unreachable")
        except RevisionRefusal:
            check(True, "a changed duplicate payload is refused")

        check(ledger.coverage()["stored_causal_violations"] == 0,
              "the stored ledger has no causal violations")

        gated = {
            "preServerDirection": "UP", "finalDirection": "NEUTRAL",
            "calibratedConfidence": 0.57, "confidence": 0.60, "probNeutral": 0.10,
        }
        check(forecast_identity(gated) == ("UP", 0.57, "live_isotonic"),
              "a server veto does not rewrite the underlying model forecast as NEUTRAL")
        check(forecast_identity({"preServerDirection": "NEUTRAL", "probNeutral": 0.44})
              == ("NEUTRAL", 0.44, "ensemble_neutral_probability"),
              "a genuine model NEUTRAL carries its own class probability")

    print(f"\nMODEL REVISION LEDGER SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return _selftest()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
