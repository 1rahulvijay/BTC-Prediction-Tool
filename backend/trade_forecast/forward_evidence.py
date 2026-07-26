"""Frozen threshold artifact + immutable forward-evidence manifest for COMPLETE_TRADE_M0_V2.

Two artifacts, both immutable once written:

`ThresholdArtifact`
    The absolute action threshold, plus everything needed to prove where it came from: the
    calibration window, the dataset it was derived on, the model that produced the scores, the
    objective, the code hash. The forward evaluator LOADS this. It must never call
    `derive_entry_threshold()` - recomputing the threshold on the evidence period is the same
    leak as picking a quantile of the test set.

`ForwardEvidenceManifest`
    Proves an evidence set is admissible BEFORE any score is computed:

        every prediction post-dates the preregistration freeze
        every prediction post-dates the model freeze
        exactly one model hash, feature hash, policy hash and threshold
        zero pre-freeze rows
        no overwritten forecasts

    A mixed-hash evidence set FAILS. That is the point: if two models or two thresholds produced
    the rows, the set does not describe a single frozen policy and cannot support promotion.

    python backend/trade_forecast/forward_evidence.py --selftest
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ThresholdArtifact:
    """Immutable record of a frozen action threshold."""

    threshold: float
    objective: str
    target_entry_rate: float
    calibration_start_ts: float
    calibration_end_ts: float
    calibration_rows: int
    dataset_sha256: str
    model_sha256: str
    policy_sha256: str
    code_sha256: str
    created_at: float = field(default_factory=time.time)

    def threshold_hash(self) -> str:
        """Identity of this threshold AND its provenance. Changing any input changes the hash."""
        payload = {k: v for k, v in asdict(self).items() if k != "created_at"}
        return _sha256_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "threshold_sha256": self.threshold_hash()}

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            # Immutable. Silently overwriting a frozen threshold would invalidate every forward
            # row already collected under it, with no trace that it happened.
            raise FileExistsError(
                f"threshold artifact already exists and is immutable: {path}"
            )
        path.write_text(json.dumps(self.to_json(), indent=2), encoding="utf-8")
        return path

    @staticmethod
    def load(path: Path) -> "ThresholdArtifact":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        recorded = data.pop("threshold_sha256", None)
        artifact = ThresholdArtifact(**data)
        if recorded and recorded != artifact.threshold_hash():
            raise ValueError(
                f"threshold artifact hash mismatch: recorded {recorded}, "
                f"computed {artifact.threshold_hash()} - the artifact was edited"
            )
        return artifact


REQUIRED_SINGLETON_FIELDS = (
    "model_sha256",
    "feature_schema_sha256",
    "policy_sha256",
    "threshold_sha256",
)


def build_forward_manifest(
    rows: list[dict[str, Any]],
    *,
    prereg_sha256: str,
    prereg_frozen_at: float,
    model_frozen_at: float,
    min_rounds: int,
    min_weeks: int,
) -> dict[str, Any]:
    """Admissibility manifest for a forward evidence set. Computed BEFORE any scoring."""
    blockers: list[str] = []
    if not rows:
        return {
            "admissible": False,
            "blockers": ["forward evidence set is empty"],
            "rows": 0,
        }

    ts = [float(r["prediction_ts"]) for r in rows]
    freeze_ts = max(float(prereg_frozen_at), float(model_frozen_at))
    pre_freeze = [t for t in ts if t <= freeze_ts]
    if pre_freeze:
        blockers.append(
            f"{len(pre_freeze)} prediction(s) at or before the freeze boundary "
            f"({time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(freeze_ts))}); "
            f"promotion evidence must post-date BOTH the prereg and model freezes"
        )

    singletons: dict[str, list[str]] = {}
    for name in REQUIRED_SINGLETON_FIELDS:
        values = sorted({str(r.get(name)) for r in rows})
        singletons[name] = values
        if len(values) != 1 or values[0] in ("None", ""):
            blockers.append(
                f"{name} is not a singleton across the evidence set ({len(values)} distinct: "
                f"{values[:3]}) - the rows do not describe ONE frozen policy"
            )

    # Duplicate forecast ids mean a row was re-written; the ledger is append-only by contract.
    ids = [r.get("forecast_id") for r in rows if r.get("forecast_id") is not None]
    if ids and len(ids) != len(set(ids)):
        blockers.append(
            f"{len(ids) - len(set(ids))} duplicate forecast_id(s) - evidence was overwritten"
        )

    rounds = {r.get("round_id") for r in rows if r.get("round_id") is not None}
    span_weeks = (max(ts) - min(ts)) / (7 * 86400.0)
    if len(rounds) < int(min_rounds):
        blockers.append(f"{len(rounds)} independent rounds < {min_rounds} required")
    if span_weeks < float(min_weeks):
        blockers.append(f"{span_weeks:.2f} calendar weeks < {min_weeks} required")

    return {
        "admissible": not blockers,
        "blockers": blockers,
        "rows": len(rows),
        "prereg_sha256": prereg_sha256,
        "prereg_frozen_at": prereg_frozen_at,
        "model_frozen_at": model_frozen_at,
        "freeze_boundary_ts": freeze_ts,
        "pre_freeze_rows": len(pre_freeze),
        "first_forward_prediction_ts": min(ts),
        "last_forward_prediction_ts": max(ts),
        "independent_rounds": len(rounds),
        "calendar_weeks": round(span_weeks, 2),
        **{k: (v[0] if len(v) == 1 else v) for k, v in singletons.items()},
    }


def selftest() -> int:
    import tempfile

    ok = True

    def chk(cond: bool, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok &= bool(cond)

    art = ThresholdArtifact(
        threshold=0.71, objective="P(plan_take_3c_or_stop_3c_net > 0)",
        target_entry_rate=0.20, calibration_start_ts=1000.0, calibration_end_ts=2000.0,
        calibration_rows=5000, dataset_sha256="d" * 64, model_sha256="m" * 64,
        policy_sha256="p" * 64, code_sha256="c" * 64,
    )
    print("threshold artifact")
    chk(len(art.threshold_hash()) == 64, "threshold hash is a sha256 over value AND provenance")
    other = ThresholdArtifact(**{**asdict(art), "threshold": 0.72})
    chk(other.threshold_hash() != art.threshold_hash(), "a different threshold -> different hash")
    other = ThresholdArtifact(**{**asdict(art), "model_sha256": "z" * 64})
    chk(
        other.threshold_hash() != art.threshold_hash(),
        "the same threshold from a DIFFERENT model is a different artifact",
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.json"
        art.save(path)
        chk(ThresholdArtifact.load(path).threshold == 0.71, "round-trips through disk")
        try:
            art.save(path)
            chk(False, "re-saving over a frozen threshold is refused")
        except FileExistsError:
            chk(True, "re-saving over a frozen threshold is refused")
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["threshold"] = 0.10
        path.write_text(json.dumps(tampered), encoding="utf-8")
        try:
            ThresholdArtifact.load(path)
            chk(False, "an EDITED threshold artifact fails its hash check")
        except ValueError:
            chk(True, "an EDITED threshold artifact fails its hash check")

    print("forward evidence manifest")
    base = {
        "model_sha256": "m" * 64, "feature_schema_sha256": "f" * 64,
        "policy_sha256": "p" * 64, "threshold_sha256": art.threshold_hash(),
    }
    freeze = 1_000_000.0
    good = [
        {**base, "forecast_id": f"f{i}", "round_id": f"r{i}",
         "prediction_ts": freeze + 100 + i * 700.0}
        for i in range(1200)
    ]
    m = build_forward_manifest(
        good, prereg_sha256="a" * 64, prereg_frozen_at=freeze,
        model_frozen_at=freeze - 10, min_rounds=1000, min_weeks=1,
    )
    chk(m["admissible"], f"a clean post-freeze set is admissible ({m['blockers']})")
    chk(m["pre_freeze_rows"] == 0, "pre_freeze_rows is zero")

    leaked = [*good, {**base, "forecast_id": "old", "round_id": "rold",
                      "prediction_ts": freeze - 5.0}]
    m = build_forward_manifest(leaked, prereg_sha256="a" * 64, prereg_frozen_at=freeze,
                               model_frozen_at=freeze - 10, min_rounds=1000, min_weeks=1)
    chk(not m["admissible"] and m["pre_freeze_rows"] == 1,
        "ONE pre-freeze row makes the whole set inadmissible")

    mixed = [*good[:600],
             *[{**g, "model_sha256": "n" * 64} for g in good[600:]]]
    m = build_forward_manifest(mixed, prereg_sha256="a" * 64, prereg_frozen_at=freeze,
                               model_frozen_at=freeze - 10, min_rounds=1000, min_weeks=1)
    chk(not m["admissible"] and any("model_sha256" in b for b in m["blockers"]),
        "a MIXED model hash fails - the rows do not describe one frozen policy")

    dup = [*good, dict(good[0])]
    m = build_forward_manifest(dup, prereg_sha256="a" * 64, prereg_frozen_at=freeze,
                               model_frozen_at=freeze - 10, min_rounds=1000, min_weeks=1)
    chk(any("duplicate forecast_id" in b for b in m["blockers"]),
        "a duplicate forecast_id is detected as overwritten evidence")

    m = build_forward_manifest(good[:50], prereg_sha256="a" * 64, prereg_frozen_at=freeze,
                               model_frozen_at=freeze - 10, min_rounds=1000, min_weeks=1)
    chk(not m["admissible"] and any("independent rounds" in b for b in m["blockers"]),
        "too few rounds is refused")

    m = build_forward_manifest(good, prereg_sha256="a" * 64, prereg_frozen_at=freeze,
                               model_frozen_at=freeze - 10, min_rounds=1000, min_weeks=52)
    chk(not m["admissible"] and any("calendar weeks" in b for b in m["blockers"]),
        "too short a span is refused")

    m = build_forward_manifest([], prereg_sha256="a" * 64, prereg_frozen_at=freeze,
                               model_frozen_at=freeze, min_rounds=1, min_weeks=0)
    chk(not m["admissible"], "an empty evidence set is inadmissible, never vacuously true")

    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
