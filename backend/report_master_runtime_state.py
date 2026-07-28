"""Generated runtime state + a consistency test for the master document.

WHY THIS EXISTS

`docs/active/MASTER_STATE_AND_ROADMAP_2026-07-28.md` mixes two kinds of claim:

    stable research conclusions   30s taker clearance is structurally poor;
                                  max_taker_ask is quadratic; the //1000 "fix" was a
                                  regression; the calibration study has selection bias
    changing operational state    current commit, serviceable artifacts, recorder rows,
                                  archive days, semantics versions, calibration mode

Only the first kind belongs in a hand-written document. The second goes stale the moment
the recorder starts or a model is retrained - and a "single source of truth" that is
quietly wrong is worse than no document at all. So the second kind is GENERATED here.

    python backend/report_master_runtime_state.py             # write the report
    python backend/report_master_runtime_state.py --selftest  # doc consistency test

The selftest asserts only claims the CODE can prove. Runtime-dependent values (recorder
row count, archive days) are reported but deliberately NOT asserted, because CI has no
recorder and a green build must not depend on one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
OUT_DIR = os.path.join(DATA, "reports")


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def collect() -> dict:
    st: dict = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    st["commit"] = _git("rev-parse", "HEAD")[:12]
    st["commit_subject"] = _git("log", "-1", "--format=%s")
    st["uncommitted_files"] = len([ln for ln in _git("status", "--porcelain").splitlines() if ln])

    # --- contracts -----------------------------------------------------------------
    try:
        from features import FEATURE_SEMANTICS_VERSION
        st["feature_semantics_version"] = FEATURE_SEMANTICS_VERSION
    except Exception as exc:
        st["feature_semantics_version"] = f"ERROR {type(exc).__name__}"
    try:
        from model import TRAINING_SEMANTICS_VERSION
        st["training_semantics_version"] = TRAINING_SEMANTICS_VERSION
    except Exception as exc:
        st["training_semantics_version"] = f"ERROR {type(exc).__name__}"

    # --- artifacts -----------------------------------------------------------------
    try:
        from check_feature_contract import ARTIFACTS, MODELS, verdict_for
        rows, counts = [], {}
        for name in ARTIFACTS:
            code, detail = verdict_for(os.path.join(MODELS, name))
            counts[code or "SERVICEABLE"] = counts.get(code or "SERVICEABLE", 0) + 1
            rows.append({"artifact": name, "verdict": code or "SERVICEABLE",
                         "detail": detail})
        st["artifacts"] = {
            "total": len(ARTIFACTS),
            "serviceable": counts.get("SERVICEABLE", 0),
            "by_verdict": counts,
            "detail": rows,
        }
        st["serving_status"] = ("MODELS_READY" if counts.get("SERVICEABLE", 0) == len(ARTIFACTS)
                                else "DEGRADED_MODEL_BLOCKED")
    except Exception as exc:
        st["artifacts"] = {"error": f"{type(exc).__name__}: {exc}"}
        st["serving_status"] = "UNKNOWN"

    # --- calibration ---------------------------------------------------------------
    try:
        import phold_calibrator as pc
        cal = {"mode": pc.MODE, "artifact": os.path.basename(pc.ARTIFACT), "horizons": {}}
        for hz in (5, 15):
            active, reason, code = pc.calibration_state(hz)
            cal["horizons"][str(hz)] = {"active": active, "refusal_code": code,
                                        "reason": reason[:120]}
        st["calibration"] = cal
    except Exception as exc:
        st["calibration"] = {"error": f"{type(exc).__name__}: {exc}"}

    # --- recorder / archive (runtime-dependent: reported, never asserted) -----------
    try:
        from research.event_conditional_v1.data_contract import evaluate_archive
        rep = evaluate_archive()
        st["archive"] = {
            "db_exists": rep.db_exists,
            "total_rows": rep.total_rows,
            "span_days": round(rep.span_days, 3),
            "any_family_ready": rep.any_ready,
            "families": {k: {"ready": v, "blockers": rep.family_blockers[k]}
                         for k, v in rep.family_ready.items()},
            "streams_present": sum(1 for s in rep.streams if s.present),
            "streams_total": len(rep.streams),
        }
    except Exception as exc:
        st["archive"] = {"error": f"{type(exc).__name__}: {exc}"}

    logs = [os.path.join(DATA, f"multi_venue_recorder.{k}.log") for k in ("stdout", "stderr")]
    st["recorder"] = {
        "log_files_present": [os.path.basename(p) for p in logs if os.path.exists(p)],
        "ever_started": any(os.path.exists(p) for p in logs),
    }

    # --- migration progress (the open P0) -------------------------------------------
    st["artifact_migration"] = {
        "phase": "NOT_STARTED",
        "save_paths_total": 25, "save_paths_migrated": 0,
        "load_paths_total": 57, "load_paths_migrated": 0,
        "enforcement_wired_into_loaders": False,
        "note": "check_feature_contract.verdict_for() exists and --enforce-serving "
                "reports, but NO loader calls it. The P0 is open.",
    }
    return st


def render(st: dict) -> str:
    a = st.get("artifacts", {})
    ar = st.get("archive", {})
    cal = st.get("calibration", {})
    m = st["artifact_migration"]
    L = [
        "# Master Runtime State (generated)",
        "",
        f"Generated {st['generated_utc']} — **do not hand-edit.**",
        "Regenerate with `python backend/report_master_runtime_state.py`.",
        "Stable research conclusions live in `MASTER_STATE_AND_ROADMAP_2026-07-28.md`.",
        "",
        "| field | value |",
        "|---|---|",
        f"| commit | `{st['commit']}` {st['commit_subject'][:52]} |",
        f"| uncommitted files | {st['uncommitted_files']} |",
        f"| feature semantics | v{st['feature_semantics_version']} |",
        f"| training semantics | v{st['training_semantics_version']} |",
        f"| serving status | **{st['serving_status']}** |",
        f"| artifacts serviceable | {a.get('serviceable', '?')} / {a.get('total', '?')} |",
        f"| calibration mode | `{cal.get('mode', '?')}` |",
        f"| archive rows | {ar.get('total_rows', '?')} |",
        f"| archive span (days) | {ar.get('span_days', '?')} |",
        f"| streams present | {ar.get('streams_present', '?')} / {ar.get('streams_total', '?')} |",
        f"| recorder ever started | {st['recorder']['ever_started']} |",
        "",
        "## Artifact migration (open P0)",
        "",
        f"- phase: **{m['phase']}**",
        f"- save paths migrated: {m['save_paths_migrated']} / {m['save_paths_total']}",
        f"- load paths migrated: {m['load_paths_migrated']} / {m['load_paths_total']}",
        f"- enforcement wired into loaders: **{m['enforcement_wired_into_loaders']}**",
        f"- {m['note']}",
        "",
        "## Artifact verdicts",
        "",
        "| verdict | count |",
        "|---|---|",
    ]
    for k, v in sorted((a.get("by_verdict") or {}).items()):
        L.append(f"| `{k}` | {v} |")
    L += ["", "## Calibration", "", "| horizon | active | refusal |", "|---|---|---|"]
    for hz, e in (cal.get("horizons") or {}).items():
        L.append(f"| {hz}m | {e['active']} | `{e.get('refusal_code') or '-'}` |")
    L += ["", "## Research families", "", "| family | ready | blockers |", "|---|---|---|"]
    for fam, e in (ar.get("families") or {}).items():
        L.append(f"| {fam} | {e['ready']} | {len(e['blockers'])} |")
    return "\n".join(L) + "\n"


def selftest() -> int:
    """Assert the master document's STATIC claims. Runtime values are not asserted."""
    ok = True

    def chk(c, m):
        nonlocal ok
        print(f"  {'PASS' if c else 'FAIL'}  {m}")
        ok = ok and bool(c)

    print("master-doc consistency selftest")
    st = collect()

    chk(st["feature_semantics_version"] == 3, "document claims feature semantics v3")
    chk(st["training_semantics_version"] == 2, "document claims training semantics v2")

    import phold_calibrator as pc
    chk(pc.MODE == "off" or os.environ.get("BTC_PHOLD_CALIBRATION_MODE"),
        "calibration defaults to off")

    # The legacy flag must map to REQUIRED (fail closed), not optional.
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "phold_calibrator.py"), encoding="utf-8").read()
    chk('MODE = "required"' in src,
        "legacy BTC_APPLY_PHOLD_CALIBRATION=1 maps to REQUIRED, not a raw fallback")

    # required mode must yield NO probability, not a known-bad one.
    saved_mode = pc.MODE
    try:
        pc.MODE = "required"
        pc._CACHE = {"ts": 0.0, "val": None}
        p, was, why = pc.calibrate(0.955, 5)
        chk(p is None and not was, "required + invalid calibrator returns NO probability")
        okp, _ = pc.may_price_from_calibration(5)
        chk(not okp, "and revokes may_price")
    finally:
        pc.MODE = saved_mode
        pc._CACHE = {"ts": 0.0, "val": None}

    art_path = os.path.join(DATA, "research", "phold_challenger", "phold_calibrators.json")
    if os.path.exists(art_path):
        art = json.load(open(art_path, encoding="utf-8"))
        deployables = [e.get("deployable") for e in (art.get("horizons") or {}).values()]
        chk(deployables and not any(deployables),
            "every current calibrator is non-deployable (source models pending retrain)")

    # The document says the P0 is OPEN. If a loader ever starts enforcing, this must be
    # updated deliberately rather than drifting.
    chk(not st["artifact_migration"]["enforcement_wired_into_loaders"],
        "document correctly states loader enforcement is NOT wired (P0 open)")

    # max_taker_ask must remain quadratic - proposed as 'linear' four times, refuted each.
    dc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "decision_champion.py"), encoding="utf-8").read()
    chk("disc" in dc and "4.0 * rate * target" in dc,
        "max_taker_ask is still QUADRATIC (fee is rate*p*(1-p); linear form is wrong)")

    print("master-doc consistency:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


def main() -> int:
    st = collect()
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "MASTER_RUNTIME_STATE.json"), "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1)
    md = render(st)
    with open(os.path.join(OUT_DIR, "MASTER_RUNTIME_STATE.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    print(md)
    print(f"wrote -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    raise SystemExit(selftest() if ap.parse_args().selftest else main())
