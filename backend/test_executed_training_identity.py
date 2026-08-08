"""The manifest must describe the data the model TRAINED on. (2.1 keystone, and 1.7)

    python backend/test_executed_training_identity.py

2.1 - THE ARTIFACT HASHED A DATASET THE MODEL DID NOT TRAIN ON

`current_training_identity` describes `research_matrix_1m.parquet` and its manifest -
`training_data_hash`, `row_count`, `coverage_ok`, `actual_start/end`,
`monthly_quality_passed`, `source_manifest_hash`. `train()` is handed in-memory X and Y built
from `data_state["klines"]`, fetched fresh from Binance at boot.

Measured: the manifest records 86,400 rows and hash `281657b2...`, written at 04:05; the live
run trains on 86,400 freshly-fetched bars. SAME COUNT, DIFFERENT DATA. So the manifest could
truthfully hash the matrix while certifying a model trained on something else, and every gate
reading it - artifact_compatibility, the oracle freeze, check_feature_contract - was certifying
provenance for the wrong dataset.

The fix records BOTH, under `executed_*` keys, with an explicit agreement flag. "Two datasets,
and they differ" is a far more useful answer than one number describing the wrong one.

1.7 - PREDICTIONS COULD NOT SAY WHICH QUESTION THEY ANSWERED

`predictions_{5,15}m` had no `target_contract` and no `release_id`, so no query could separate a
first-touch row from an endpoint row - which is why PrecisionEngine still declares
`contract_provenance=UNRECORDED` and refuses. The columns exist now, and the WRITER REQUIRES
them: a default that also stamps fresh rows would be the fill-engine defect (fd46d51) again.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def main() -> int:
    from artifact_identity import current_training_identity, executed_training_identity

    rng = np.random.default_rng(7)
    X = rng.normal(size=(400, 8)).astype(np.float32)
    Y = {5: np.eye(3, dtype=np.float32)[rng.integers(0, 3, 400)]}
    vm = {5: np.ones(400, dtype=bool)}

    print("1. the executed identity hashes the actual arrays")
    e = executed_training_identity(X, Y, valid_mask=vm)
    chk(e["executed_rows"] == 400 and e["executed_shape"] == [400, 8],
        f"shape and row count come from the array in hand ({e['executed_shape']})")
    chk(len(e["executed_feature_matrix_sha256"]) == 64, "the matrix is hashed")
    chk(e["executed_labels"]["5"]["labels_sha256"] is not None
        and e["executed_labels"]["5"]["rows"] == 400,
        "labels are hashed PER HORIZON, so a horizon swapped after the fact is detectable")
    chk(e["executed_labels"]["5"]["valid_mask_sha256"] is not None,
        "and the ambiguity mask is hashed too - it decides which rows train")

    print("2. a ONE-CELL change moves the hash")
    X2 = X.copy()
    X2[123, 4] += np.float32(1e-3)
    e2 = executed_training_identity(X2, Y, valid_mask=vm)
    chk(e2["executed_feature_matrix_sha256"] != e["executed_feature_matrix_sha256"],
        "one changed cell in 3,200 gives a different hash - the bytes are hashed, not a "
        "summary. A mean or a min/max collides trivially and would let two different training "
        "sets certify as one")
    chk(e2["executed_rows"] == e["executed_rows"],
        "while the row COUNT is identical - which is exactly why row count cannot stand in for "
        "identity: the matrix and the live fetch both hold 86,400 rows")

    e3 = executed_training_identity(X, {5: Y[5][::-1].copy()}, valid_mask=vm)
    chk(e3["executed_labels"]["5"]["labels_sha256"] != e["executed_labels"]["5"]["labels_sha256"],
        "reversing the labels moves the label hash while X is untouched")

    print("3. the identity records BOTH datasets and says whether they agree")
    ident = current_training_identity(requested_days=60, feature_names=["a", "b"], executed=e)
    chk(ident.get("executed_identity_recorded") is True,
        "an executed identity is recorded")
    chk(ident.get("executed_feature_matrix_sha256") == e["executed_feature_matrix_sha256"],
        "and merged into the artifact identity, beside the matrix fields rather than replacing "
        "them - a reader can now see both")
    # THIS ASSERTION USED TO REQUIRE `is False`, AND IT COULD NOT FAIL.
    #
    # The flag compared a NumPy tensor digest against a Parquet FILE digest. Those never
    # agree - measured, even on logically identical data - so False was structural, not a
    # finding about these arrays. The assertion read like evidence and was a tautology, and
    # the obvious next step (enforce the flag) would have rejected every honest retrain.
    chk(ident.get("executed_matches_matrix") is None,
        f"the agreement flag is NOT COMPARABLE ({ident.get('executed_matches_matrix')}) "
        f"rather than a False that merely restates the hash domains")
    chk("HASH_DOMAINS_NOT_COMPARABLE" in str(ident.get("executed_matrix_comparison_basis")),
        f"and it says why: {ident.get('executed_matrix_comparison_basis')}")
    chk(ident.get("executed_rows_match_matrix_rows") in (True, False),
        "while the comparison that CAN be made - row counts - is reported separately, so "
        "nothing that is knowable is thrown away with the part that is not")

    bare = current_training_identity(requested_days=60, feature_names=["a", "b"])
    chk(bare.get("executed_identity_recorded") is False
        and bare.get("executed_matches_matrix") is None,
        "with no executed identity supplied it records UNKNOWN (None), not True - absent must "
        "never read as agreement")

    print("4. train() builds it BEFORE fitting and passes it in")
    src = (BACKEND / "model.py").read_text(encoding="utf-8")
    chk("_executed = executed_training_identity(" in src,
        "train() builds the executed identity")
    chk("executed=_executed," in src,
        "and passes it into current_training_identity - the trainer can no longer discover a "
        "different global dataset than the one it was handed")
    _built = src.index("_executed = executed_training_identity(")
    _fit = src.index("X_model = self._select_model_features(X)")
    chk(_built < _fit,
        "and it is hashed BEFORE any feature selection or fitting touches the arrays")

    print("5. 1.7 - a prediction must state its contract and release")
    import database
    import inspect

    sig = inspect.signature(database.log_prediction)
    for name in ("target_contract", "release_id"):
        chk(name in sig.parameters, f"log_prediction takes {name}")
    try:
        database.log_prediction(
            "x", 0, 5, 1.0, 1.0, 0.0, 0.5, "UP", 0.0, 0.0)
        refused = False
    except ValueError as exc:
        refused = "target_contract" in str(exc)
    chk(refused,
        "and REFUSES a row that omits them - UNKNOWN_LEGACY exists for rows written before the "
        "column did, not for new ones. A default that also stamps fresh rows is the fill-engine "
        "defect in a different costume")

    db_src = (BACKEND / "database.py").read_text(encoding="utf-8")
    for col in ("target_contract", "release_id", "resolution_basis",
                "resolution_event_ts", "label_version"):
        chk(f'ADD COLUMN {col}' in db_src, f"the {col} column is migrated in")
    server_src = (BACKEND / "server.py").read_text(encoding="utf-8")
    chk("target_contract=str(" in server_src and "release_id=str(" in server_src,
        "and the live writer supplies both from the prediction itself, not from an assumption")

    print("\nEXECUTED TRAINING IDENTITY:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
