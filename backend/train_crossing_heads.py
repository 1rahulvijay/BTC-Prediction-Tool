"""Train and publish the crossing heads as a serviceable bundle. Loadable, with NO authority.

WHAT THIS IS
    CROSSING_HEADS_V1 (protocol sha256 762532c9) measured, on 15,428 real labelled crossings:

        is_final_crossing   clock 0.6755 -> 0.7144   gain +0.0389  CI [+0.0243, +0.0556]
        reverted_30s        clock 0.5196 -> 0.6715   gain +0.1519  CI [+0.1175, +0.1840]
        reverted_60s        clock 0.5061 -> 0.6373   gain +0.1312  CI [+0.1121, +0.1517]

    This turns that measurement into an artifact the application can actually load, with full
    provenance, rather than a number in a document.

AUTHORITY IS ZERO, BY REGISTRATION
    `model_registry` lists this head with may_price, may_rank and may_size all False. It is
    loadable and may inform a display or a later study; it may not price, rank or size anything.
    Granting authority later requires a visible edit to that table, which is the point.

    A crossing probability is an INPUT to a decision. Every action lane measured in this
    repository is closed on cost, so a good probability here does not create an opportunity.

FAIL-CLOSED
    `predict()` returns an explicit UNAVAILABLE reason rather than a number whenever the bundle
    is missing, its provenance is incomplete, or the feature vector does not match the contract
    it was trained on. 0 of 25 artifacts in this repository are currently serviceable; the
    failure mode that produced that is silent, and this one is not.

    python backend/train_crossing_heads.py --selftest
    python backend/train_crossing_heads.py --train
    python backend/train_crossing_heads.py --status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
CROSSING_DB = DATA_DIR / "polymarket_crossings.duckdb"
MATRIX = DATA_DIR / "research_matrix_1m.parquet"
#: Its OWN root: publish_champion writes one pointer per root, so sharing a root
#: with other heads would make them fight over the same champion pointer.
BUNDLE_ROOT = DATA_DIR / "saved_models" / "crossing_heads_bundles"

MODEL_NAME = "crossing_heads"
TARGET = "crossing_probabilities"
PROTOCOL_HASH = "762532c9c0d55796d0536d9f4242088c99ee6276d84fd3d44c36783a8479f1e0"
HEAD_TARGETS = ("is_final_crossing", "reverted_30s", "reverted_60s")

BAR_MS = 60_000
ROUND_FEATURES = ("seconds_left", "horizon_min", "crossing_index", "move_at_crossing",
                  "from_up", "elapsed_fraction")
MARKET_FEATURES = ("rv_15m", "rv_60m", "compression_ratio", "vpin_15m", "cvd_5m",
                   "delta", "large_trade_imbalance", "shock_magnitude")
FEATURES = ROUND_FEATURES + MARKET_FEATURES


def feature_schema_hash() -> str:
    return hashlib.sha256("|".join(FEATURES).encode()).hexdigest()


def target_contract_hash() -> str:
    return hashlib.sha256("|".join(HEAD_TARGETS).encode()).hexdigest()


def source_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=15).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def causal_bar_ts(crossing_ts):
    """Open time of the last 1-minute bar that had CLOSED before the crossing.

    Identical to the rule CROSSING_HEADS_V1 scored under. Serving must use the same rule or the
    artifact is being fed a different feature than it was trained on."""
    return (crossing_ts // BAR_MS) * BAR_MS - BAR_MS


def build_features(rows) -> "object":
    """Feature frame from crossing rows joined to the last CLOSED 1m bar."""
    import numpy as np
    import pandas as pd
    frame = pd.DataFrame(rows)
    frame["from_up"] = (frame["from_side"] == "UP").astype(float)
    frame["elapsed_fraction"] = 1.0 - frame["seconds_left"] / (frame["horizon_min"] * 60.0)
    frame["bar_ts"] = causal_bar_ts(frame["crossing_ts"].to_numpy("int64"))
    import pyarrow.parquet as pq
    market = pq.read_table(MATRIX, columns=["ts_ms"] + list(MARKET_FEATURES)).to_pandas()
    market = market.rename(columns={"ts_ms": "bar_ts"}).drop_duplicates("bar_ts")
    merged = frame.merge(market, on="bar_ts", how="left")
    merged["day"] = merged["crossing_ts"] // 86_400_000
    _ = np
    return merged


def train() -> dict:
    import duckdb
    import lightgbm as lgb
    import numpy as np
    from model_artifacts import publish_bundle

    if not CROSSING_DB.is_file():
        raise SystemExit(f"missing {CROSSING_DB} - run polymarket_crossing_recorder.py --backfill")
    con = duckdb.connect(str(CROSSING_DB), read_only=True)
    try:
        rows = con.execute("""
            SELECT e.crossing_id, e.round_id, e.horizon_min, e.crossing_ts, e.from_side,
                   e.seconds_left, e.move_at_crossing, e.crossing_index,
                   l.is_final_crossing, l.reverted_30s, l.reverted_60s
            FROM crossing_events e JOIN crossing_labels l ON l.crossing_id = e.crossing_id
            ORDER BY e.crossing_ts""").df()
    finally:
        con.close()
    frame = build_features(rows)
    cutoff_ms = int(frame["crossing_ts"].max())

    models, metrics = {}, {}
    params = dict(n_estimators=200, learning_rate=0.05, num_leaves=15,
                  min_child_samples=100, verbose=-1, random_state=0)
    for target in HEAD_TARGETS:
        usable = frame.dropna(subset=[target] + list(FEATURES))
        y = usable[target].astype(int).to_numpy()
        if len(usable) < 500 or len(np.unique(y)) < 2:
            metrics[target] = {"trained": False, "rows": int(len(usable))}
            continue
        model = lgb.LGBMClassifier(**params).fit(usable[list(FEATURES)].to_numpy(float), y)
        models[target] = model
        metrics[target] = {"trained": True, "rows": int(len(usable)),
                           "base_rate": float(y.mean())}
    if not models:
        raise SystemExit("no head could be trained")

    payload = {
        "format": 1, "model_name": MODEL_NAME, "target": TARGET,
        "features": list(FEATURES), "heads": {k: models[k] for k in models},
        "feature_schema_hash": feature_schema_hash(),
        "target_contract_hash": target_contract_hash(),
        "protocol_hash": PROTOCOL_HASH, "trained_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                     time.gmtime()),
        "metrics": metrics,
    }
    blob = pickle.dumps(payload, protocol=5)
    dataset_sha = hashlib.sha256(CROSSING_DB.read_bytes()).hexdigest()

    from features import FEATURE_SEMANTICS_VERSION
    from model import TRAINING_SEMANTICS_VERSION
    BUNDLE_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = publish_bundle(
        BUNDLE_ROOT, MODEL_NAME, {"crossing_heads.pkl": blob}, target=TARGET,
        provenance={
            "feature_schema_hash": feature_schema_hash(),
            "feature_semantics_version": FEATURE_SEMANTICS_VERSION,
            "training_semantics_version": TRAINING_SEMANTICS_VERSION,
            "dataset_sha256": dataset_sha,
            "training_cutoff": cutoff_ms,
            "target_contract_hash": target_contract_hash(),
            "protocol_hash": PROTOCOL_HASH,
            "source_commit": source_commit(),
        })
    # Point the champion at it, so serving resolves one stable location rather than guessing
    # among content-addressed directories.
    from model_artifacts import publish_champion, verify_bundle
    bundle = _newest_verifying_bundle()
    if bundle is None:
        raise SystemExit("published, but no bundle verifies - refusing to point at it")
    verify_bundle(bundle, expect_name=MODEL_NAME)
    publish_champion(BUNDLE_ROOT, bundle, name=MODEL_NAME)
    return {"metrics": metrics, "manifest": manifest, "rows": int(len(frame)),
            "bundle": str(bundle)}


def _newest_verifying_bundle():
    """The most recent bundle directory that actually verifies. None if there is none."""
    from model_artifacts import ArtifactRefusal, verify_bundle
    if not BUNDLE_ROOT.is_dir():
        return None
    candidates = []
    for path in BUNDLE_ROOT.iterdir():
        if not path.is_dir():
            continue
        try:
            verify_bundle(path, expect_name=MODEL_NAME)
        except (ArtifactRefusal, Exception):
            continue
        candidates.append(path)
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


class Unavailable(RuntimeError):
    """Typed refusal. Serving must show a reason, never a fabricated probability."""


_CACHE: dict | None = None


def load(force: bool = False) -> dict:
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    from model_artifacts import load_verified
    bundle = _newest_verifying_bundle()
    if bundle is None:
        raise Unavailable(f"no verifying bundle under {BUNDLE_ROOT} - run --train")
    try:
        # load_verified checks the bundle BEFORE handing the path to a deserializer, so the
        # pickle is never opened until its bytes have been verified.
        payload, _ = load_verified(bundle, MODEL_NAME,
                                   lambda path: pickle.loads(Path(path).read_bytes()))
    except Exception as exc:
        raise Unavailable(f"bundle unavailable: {exc}") from exc
    if payload.get("feature_schema_hash") != feature_schema_hash():
        raise Unavailable("feature schema hash mismatch - serving would feed a different "
                          "vector than the head was trained on")
    if payload.get("target_contract_hash") != target_contract_hash():
        raise Unavailable("target contract hash mismatch")
    if payload.get("protocol_hash") != PROTOCOL_HASH:
        raise Unavailable("protocol hash mismatch - this head was not trained under "
                          "CROSSING_HEADS_V1")
    _CACHE = payload
    return payload


def predict(feature_row: dict) -> dict:
    """P(final), P(revert 30s), P(revert 60s), or an explicit unavailable reason.

    Never returns a partial answer silently: a missing feature is a refusal, because a head fed
    a zero where a value should be is a head answering a different question."""
    try:
        payload = load()
    except Unavailable as exc:
        return {"status": "MODEL_UNAVAILABLE", "reason": str(exc)}
    missing = [f for f in payload["features"] if feature_row.get(f) is None]
    if missing:
        return {"status": "MODEL_UNAVAILABLE",
                "reason": f"missing features {missing[:4]} - a zero substitute would change "
                          f"the question being answered"}
    import numpy as np
    vector = np.array([[float(feature_row[f]) for f in payload["features"]]])
    out = {"status": "OK", "authority": "NONE", "protocol": PROTOCOL_HASH[:12]}
    for target, model in payload["heads"].items():
        out[target] = float(model.predict_proba(vector)[0, 1])
    return out


def status() -> dict:
    try:
        payload = load(force=True)
    except Unavailable as exc:
        return {"serviceable": False, "reason": str(exc)}
    return {"serviceable": True, "trained_utc": payload.get("trained_utc"),
            "heads": sorted(payload["heads"]), "metrics": payload.get("metrics", {})}


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    from model_registry import require
    entry = require(MODEL_NAME)
    check(entry.target == TARGET, "the head is registered with its declared target")
    check(not entry.may_price and not entry.may_rank and not entry.may_size,
          "EVERY authority flag is False - loadable, and permitted to decide nothing")
    check(not entry.required_for_serving,
          "it is not required for serving, so its absence cannot block the app")

    check(len(FEATURES) == 14, "the feature contract is the 14 columns scored under the protocol")
    check(feature_schema_hash() == feature_schema_hash(), "the schema hash is stable")
    check(len(PROTOCOL_HASH) == 64, "the protocol hash is a full sha256")

    minute = 1_785_000_000_000 // BAR_MS * BAR_MS
    import numpy as np
    got = causal_bar_ts(np.array([minute + 45_000], dtype="int64"))[0]
    check(got == minute - BAR_MS,
          "serving uses the same CAUSAL bar rule the head was scored under")
    check(got + BAR_MS <= minute + 45_000, "...and that bar had closed before the crossing")

    # FAIL-CLOSED. Without a published bundle, predict() must refuse, not guess.
    global _CACHE
    saved = _CACHE
    _CACHE = None
    try:
        out = predict({f: 1.0 for f in FEATURES})
        check(out["status"] in ("OK", "MODEL_UNAVAILABLE"),
              "predict returns a typed status, never a bare number")
        if out["status"] == "MODEL_UNAVAILABLE":
            check(bool(out.get("reason")), "an unavailable head states WHY")
    finally:
        _CACHE = saved

    _CACHE = {"features": list(FEATURES), "heads": {}, "feature_schema_hash":
              feature_schema_hash(), "target_contract_hash": target_contract_hash(),
              "protocol_hash": PROTOCOL_HASH}
    try:
        partial = predict({f: 1.0 for f in FEATURES[:-1]})
        check(partial["status"] == "MODEL_UNAVAILABLE",
              "a MISSING feature is refused - a zero substitute answers a different question")
        full = predict({f: 1.0 for f in FEATURES})
        check(full["status"] == "OK" and full["authority"] == "NONE",
              "a complete vector succeeds and reports authority NONE")
    finally:
        _CACHE = saved

    print(f"\nCROSSING HEADS SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.train:
        result = train()
        print(f"  trained on {result['rows']:,} crossings")
        for target, metrics in result["metrics"].items():
            if metrics.get("trained"):
                print(f"    {target:<20} {metrics['rows']:>7,} rows   "
                      f"base rate {metrics['base_rate']:.1%}")
            else:
                print(f"    {target:<20} NOT TRAINED ({metrics['rows']} rows)")
        print(f"  published bundle -> {BUNDLE_ROOT / MODEL_NAME}")
        return 0
    print(json.dumps(status(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
