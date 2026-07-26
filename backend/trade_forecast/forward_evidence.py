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

    def validate(self) -> None:
        """Reject a structurally impossible artifact before it can be frozen."""
        import math as _math

        if not _math.isfinite(self.threshold) or not 0.0 <= self.threshold <= 1.0:
            raise ValueError(f"threshold must be finite in [0,1], got {self.threshold}")
        if self.calibration_end_ts <= self.calibration_start_ts:
            raise ValueError("calibration_end_ts must be after calibration_start_ts")
        if self.calibration_rows <= 0:
            raise ValueError("calibration_rows must be positive")
        if not 0.0 < self.target_entry_rate <= 1.0:
            raise ValueError(f"target_entry_rate out of range: {self.target_entry_rate}")
        for name in ("dataset_sha256", "model_sha256", "policy_sha256", "code_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64:
                raise ValueError(f"{name} must be a 64-character sha256, got {value!r}")

    def save(self, path: Path) -> Path:
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            # Immutable. Silently overwriting a frozen threshold would invalidate every forward
            # row already collected under it, with no trace that it happened.
            raise FileExistsError(
                f"threshold artifact already exists and is immutable: {path}"
            )
        # Atomic write: a crash mid-save must not leave a truncated threshold artifact that
        # later loads as valid-looking JSON.
        import os as _os

        temporary = path.with_suffix(path.suffix + ".tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.to_json(), indent=2))
            handle.flush()
            _os.fsync(handle.fileno())
        temporary.replace(path)
        return path

    @staticmethod
    def load(path: Path) -> "ThresholdArtifact":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        recorded = data.pop("threshold_sha256", None)
        if not recorded:
            # An artifact with no recorded hash cannot be verified, so it is not an artifact.
            # Accepting it would let a hand-written file bypass integrity entirely.
            raise ValueError("threshold artifact missing threshold_sha256")
        artifact = ThresholdArtifact(**data)
        artifact.validate()
        if recorded != artifact.threshold_hash():
            raise ValueError(
                f"threshold artifact hash mismatch: recorded {recorded}, "
                f"computed {artifact.threshold_hash()} - the artifact was edited"
            )
        return artifact


# ===================================================================================
# EVIDENCE CLASS - which sources may support which claims
# ===================================================================================
# Free historical Polymarket L2 DOES exist from third parties (PMXT archives, Resolved Markets,
# Polyfun, HF recordings). That is genuinely useful and shortens development from months to days.
# It is also the single most dangerous thing that could enter this pipeline, because a downloaded
# archive is indistinguishable from own-recorder output once it is inside a parquet.
#
# What a third-party archive CANNOT carry, however complete its ladders are:
#
#   recv_ts on THIS host   -> so 500ms entry simulation and latency sensitivity are unprovable
#   this collector's gaps  -> so continuous-coverage claims describe someone else's uptime
#   this host's outages    -> so stream health is not measurable at all
#
# Those are exactly the quantities the promotion contract is built on. So the classes are kept
# apart MECHANICALLY, not by discipline: a forward evidence set containing even one third-party
# row is inadmissible, and says so.
THIRD_PARTY_HISTORICAL = "THIRD_PARTY_HISTORICAL"     # development, kill-only authority
OWN_FORWARD_RECORDER = "OWN_FORWARD_RECORDER"         # promotion-authoritative

PROMOTION_AUTHORITATIVE_CLASSES = frozenset({OWN_FORWARD_RECORDER})

# Sources seen so far. Extend deliberately; an unrecognised source is treated as third-party,
# which is the safe direction.
KNOWN_SOURCES = {
    "l2_recorder": OWN_FORWARD_RECORDER,
    "pmxt": THIRD_PARTY_HISTORICAL,
    "resolved_markets": THIRD_PARTY_HISTORICAL,
    "polyfun": THIRD_PARTY_HISTORICAL,
    "huggingface": THIRD_PARTY_HISTORICAL,
    "polymarket_prices_history": THIRD_PARTY_HISTORICAL,
}


def classify_source(source: str | None) -> str:
    """Unknown or absent source -> THIRD_PARTY_HISTORICAL. Never the promoting class by default."""
    return KNOWN_SOURCES.get(str(source or "").strip().lower(), THIRD_PARTY_HISTORICAL)


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

    # EVIDENCE CLASS FIRST. Checked before anything else, because a mixed set is not a weaker
    # evidence set - it is a different claim wearing the same shape.
    classes = sorted({classify_source(r.get("evidence_source")) for r in rows})
    non_promoting = [c for c in classes if c not in PROMOTION_AUTHORITATIVE_CLASSES]
    if non_promoting:
        offenders = sorted({
            str(r.get("evidence_source") or "<unset>")
            for r in rows
            if classify_source(r.get("evidence_source")) not in PROMOTION_AUTHORITATIVE_CLASSES
        })
        blockers.append(
            f"evidence set contains non-promoting sources {offenders} "
            f"(classes {non_promoting}). Third-party history has KILL-ONLY authority: it cannot "
            f"carry this host's recv_ts, gaps or outages, so it cannot prove live execution."
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
        blockers.append(f"{span_weeks:.2f} weeks elapsed < {min_weeks} required")

    # ELAPSED SPAN IS NOT COVERAGE. 500 rounds on day 1 and 500 on day 57 span eight weeks while
    # observing almost none of it - the strategy would be scored on two bursts and credited with
    # two months of stability. Require real occupancy of distinct ISO weeks, and surface the
    # largest internal silence so a burst pattern is visible rather than averaged away.
    from collections import Counter

    weeks = Counter(
        time.strftime("%G-%V", time.gmtime(float(r["prediction_ts"]))) for r in rows
    )
    ordered = sorted(ts)
    gaps = [b - a for a, b in zip(ordered, ordered[1:])] or [0.0]
    longest_gap_days = max(gaps) / 86400.0
    # Weeks inside the span that contain no evidence at all.
    first_week = time.gmtime(min(ts))
    empty_internal = 0
    if len(weeks) >= 1:
        expected_weeks = int(span_weeks) + 1
        empty_internal = max(0, expected_weeks - len(weeks))
    if len(weeks) < int(min_weeks):
        blockers.append(
            f"{len(weeks)} distinct calendar weeks contain evidence < {min_weeks} required "
            f"(elapsed span was {span_weeks:.2f}w - elapsed time is not coverage)"
        )
    if empty_internal > 0:
        blockers.append(
            f"{empty_internal} calendar week(s) inside the evidence span contain no forecasts"
        )

    return {
        "admissible": not blockers,
        "blockers": blockers,
        "rows": len(rows),
        "evidence_classes": classes,
        "promotion_authoritative": classes == [OWN_FORWARD_RECORDER],
        "prereg_sha256": prereg_sha256,
        "prereg_frozen_at": prereg_frozen_at,
        "model_frozen_at": model_frozen_at,
        "freeze_boundary_ts": freeze_ts,
        "pre_freeze_rows": len(pre_freeze),
        "first_forward_prediction_ts": min(ts),
        "last_forward_prediction_ts": max(ts),
        "independent_rounds": len(rounds),
        "elapsed_weeks": round(span_weeks, 2),
        "distinct_calendar_weeks": len(weeks),
        "rounds_by_week": dict(sorted(weeks.items())),
        "empty_internal_weeks": empty_internal,
        "longest_gap_between_forecasts_days": round(longest_gap_days, 3),
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
        "evidence_source": "l2_recorder",
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

    # SPARSE BURSTS: 500 rounds on day 1 and 500 on day 57 span eight weeks while observing
    # almost none of it. Elapsed time is not coverage.
    burst = (
        [{**base, "forecast_id": f"e{i}", "round_id": f"e{i}",
          "prediction_ts": freeze + 100 + i * 60.0} for i in range(500)]
        + [{**base, "forecast_id": f"l{i}", "round_id": f"l{i}",
            "prediction_ts": freeze + 57 * 86400 + i * 60.0} for i in range(500)]
    )
    m = build_forward_manifest(burst, prereg_sha256="a" * 64, prereg_frozen_at=freeze,
                               model_frozen_at=freeze - 10, min_rounds=1000, min_weeks=8)
    chk(m["elapsed_weeks"] >= 8.0, f"the burst set DOES span 8 weeks ({m['elapsed_weeks']}w)")
    chk(m["independent_rounds"] == 1000, "and it DOES have 1,000 rounds")
    chk(
        not m["admissible"],
        "yet it is INADMISSIBLE - elapsed span alone cannot pass the gate",
    )
    chk(m["distinct_calendar_weeks"] < 8,
        f"only {m['distinct_calendar_weeks']} calendar weeks actually contain evidence")
    chk(m["empty_internal_weeks"] > 0,
        f"{m['empty_internal_weeks']} internal weeks are empty and are reported")
    chk(m["longest_gap_between_forecasts_days"] > 50,
        f"the {m['longest_gap_between_forecasts_days']:.0f}-day silence is surfaced")

    # A genuinely continuous set with the same round count DOES pass.
    steady = [{**base, "forecast_id": f"s{i}", "round_id": f"s{i}",
               "prediction_ts": freeze + 100 + i * (60 * 86400 / 1200)} for i in range(1200)]
    m = build_forward_manifest(steady, prereg_sha256="a" * 64, prereg_frozen_at=freeze,
                               model_frozen_at=freeze - 10, min_rounds=1000, min_weeks=8)
    chk(m["admissible"], f"continuous coverage passes ({m['blockers']})")
    chk(m["distinct_calendar_weeks"] >= 8, "and it occupies at least 8 distinct weeks")

    print("evidence class separation")
    chk(classify_source("l2_recorder") == OWN_FORWARD_RECORDER,
        "own recorder is promotion-authoritative")
    for third in ("pmxt", "resolved_markets", "polyfun", "huggingface"):
        chk(classify_source(third) == THIRD_PARTY_HISTORICAL,
            f"{third} is third-party historical (kill-only)")
    chk(classify_source(None) == THIRD_PARTY_HISTORICAL,
        "an UNSET source defaults to third-party, never to the promoting class")
    chk(classify_source("some_new_vendor") == THIRD_PARTY_HISTORICAL,
        "an UNRECOGNISED source defaults to third-party (safe direction)")

    # A single imported row poisons the set - it cannot carry this host's recv_ts or outages.
    contaminated = [*steady[:1199],
                    {**steady[1199], "evidence_source": "pmxt"}]
    m = build_forward_manifest(contaminated, prereg_sha256="a" * 64, prereg_frozen_at=freeze,
                               model_frozen_at=freeze - 10, min_rounds=1000, min_weeks=8)
    chk(not m["admissible"], "ONE third-party row makes the whole forward set inadmissible")
    chk(not m["promotion_authoritative"], "and the set is flagged non-authoritative")
    chk(any("KILL-ONLY" in b for b in m["blockers"]),
        "the blocker explains WHY third-party history cannot promote")

    m = build_forward_manifest(steady, prereg_sha256="a" * 64, prereg_frozen_at=freeze,
                               model_frozen_at=freeze - 10, min_rounds=1000, min_weeks=8)
    chk(m["promotion_authoritative"] and m["evidence_classes"] == [OWN_FORWARD_RECORDER],
        "a pure own-recorder set remains promotion-authoritative")

    print("threshold artifact hardening")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t2.json"
        art.save(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("threshold_sha256")
        path.write_text(json.dumps(raw), encoding="utf-8")
        try:
            ThresholdArtifact.load(path)
            chk(False, "an artifact with NO recorded hash is refused")
        except ValueError:
            chk(True, "an artifact with NO recorded hash is refused")
    for bad, why in (
        ({"threshold": 1.5}, "threshold above 1"),
        ({"threshold": float("nan")}, "non-finite threshold"),
        ({"calibration_end_ts": 500.0}, "calibration end before start"),
        ({"calibration_rows": 0}, "zero calibration rows"),
        ({"model_sha256": "short"}, "a malformed sha256"),
    ):
        try:
            ThresholdArtifact(**{**asdict(art), **bad}).validate()
            chk(False, f"{why} is rejected")
        except ValueError:
            chk(True, f"{why} is rejected")

    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
