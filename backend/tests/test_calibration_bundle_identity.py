"""P1-9: calibration must learn only from the bundle that is actually serving.

    python backend/tests/test_calibration_bundle_identity.py

THE DEFECT
    The era filter was a filesystem timestamp:

        min_ts = mtime(architecture_version.pkl)
        ... WHERE timestamp >= min_ts

    A file's modification time is a fact about the filesystem, not about which model produced a
    row. The failure is concrete:

        challenger trained Monday      -> artifact mtime = Monday
        incumbent keeps predicting Mon-Fri
        challenger promoted Friday     -> mtime is STILL Monday; the file was not rewritten

    Every incumbent prediction from Monday to Friday satisfies `timestamp >= mtime`, so the
    newly promoted challenger's calibrator is fitted on five days of a DIFFERENT model's
    confidence distribution - exactly the skew the era filter exists to prevent.

    The bundle identity was already being written, under the column `model_version`. Selection
    is now by that, with the mtime rule kept only for rows written before it was populated -
    and the mode is recorded, so a fallback fit is never mistaken for an exact one.
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import database                                      # noqa: E402
from calibration import PrecisionEngine              # noqa: E402

_OK = True
BASE_TS = 1_785_000_000_000


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _insert(conn, pid, ts, bundle, correct):
    """One resolved 5m row. `correct` decides whether the raw lean matched the OUTCOME.

    `actual_direction` and `target_contract` are written because that is what a production
    row carries: the calibrator grades by the contract's own outcome, not by the sign of
    `actual_move`, and it selects rows whose contract it can name. The assertions below are
    unchanged - this fixture is about BUNDLE selection, and it still proves exactly that.
    """
    import target_contract as _tc
    conn.execute(
        "INSERT INTO predictions_5m (id, timestamp, horizon, confidence, regime, "
        " raw_direction, actual_move, actual_direction, target_contract, resolved, "
        " model_version, conviction) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [pid, ts, 5, 0.70, "RANGE", "UP", (12.0 if correct else -12.0),
         ("UP" if correct else "DOWN"), _tc.TRAINING_CONTRACT, True, bundle, 0.5])


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        database.close_db()
        original = database.DB_PATH
        database.DB_PATH = os.path.join(tmp, "analytics.duckdb")
        try:
            database.init_db()
            conn = database._connect()
            # INCUMBENT: every lean correct.  CHALLENGER: every lean wrong.
            for i in range(40):
                _insert(conn, f"inc_{i}", BASE_TS + i * 1000, "incumbent_v1", True)
            for i in range(40):
                _insert(conn, f"chg_{i}", BASE_TS + 100_000 + i * 1000, "challenger_v2", False)
            # Closed explicitly: this is a SECOND handle on the same file, and on Windows an
            # open DuckDB handle blocks the temp directory from being removed. PrecisionEngine
            # opens and closes its own per fit, which is why only this one leaks.
            conn.close()

            print("selection is by exact bundle identity")
            eng = PrecisionEngine()
            eng.active_bundle_id = "incumbent_v1"
            clause, mode = eng._era_clause()
            chk("model_version" in clause and "incumbent_v1" in clause,
                f"the predicate selects on the bundle column ({clause})")
            chk(mode == "bundle:incumbent_v1", "and the mode names the bundle it bound to")

            eng.fit_from_db()
            chk(eng.global_rate.get(5) == 1.0,
                f"fitting as the INCUMBENT sees only its own rows -> rate "
                f"{eng.global_rate.get(5)} (all correct)")

            other = PrecisionEngine()
            other.active_bundle_id = "challenger_v2"
            other.fit_from_db()
            chk(other.global_rate.get(5) == 0.0,
                f"fitting as the CHALLENGER sees only ITS rows -> rate "
                f"{other.global_rate.get(5)} (all wrong)")

            chk(eng.global_rate.get(5) != other.global_rate.get(5),
                "the two bundles produce different calibrators from the same table - which is "
                "the whole point, and what the mtime rule could not do")

            print("the mtime fallback is used only when identity is unknown, and says so")
            blind = PrecisionEngine()
            blind.active_bundle_id = ""
            clause_b, mode_b = blind._era_clause()
            chk("timestamp >=" in clause_b and "model_version" not in clause_b,
                "with no bundle id it falls back to the timestamp rule")
            chk(mode_b == "mtime_fallback",
                "and labels itself, so a fallback fit is never mistaken for an exact one")
            blind.fit_from_db()
            chk(blind.global_rate.get(5) == 0.5,
                f"the fallback pools BOTH bundles -> rate {blind.global_rate.get(5)}, which is "
                f"the contamination this fix removes")
            chk(getattr(blind, "era_mode", None) == "mtime_fallback",
                "and the mode is recorded on the engine after fitting, not just returned")

            print("a bundle id containing a quote cannot break the predicate")
            weird = PrecisionEngine()
            weird.active_bundle_id = "it's_v3"
            wclause, _ = weird._era_clause()
            chk("it''s_v3" in wclause,
                "single quotes are doubled rather than terminating the string literal")
            weird.fit_from_db()          # must not raise
            chk(weird.global_rate.get(5) is None or weird.global_rate.get(5) == 0.0
                or not weird.global_rate,
                "and a bundle with no rows simply yields no calibration")
        finally:
            database.close_db()
            database.DB_PATH = original

    print("\nCALIBRATION BUNDLE IDENTITY:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
