"""Readiness report - can any family be evaluated yet?

Phase 1 ends here, deliberately with an EMPTY result. The machinery exists; the
evidence does not. Reporting NOT_READY against a real archive query is the honest
terminal state, and it is the one thing that must not be faked to look like progress.

    python -m backend.research.event_conditional_v1.readiness
    python -m backend.research.event_conditional_v1.readiness --json out.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from .contracts import Family, load_protocol
from .data_contract import (
    PREFERRED_DAYS, REQUIRED_DAYS_MIN, evaluate_archive,
)
from .viability import viability_table


def build_report(db_path=None) -> dict:
    p = load_protocol()
    arch = evaluate_archive(db_path)
    return {
        "protocol_id": p.protocol_id,
        "protocol_sha256": p.sha256(),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "research_only": True,
        # UNREADABLE is reported separately from NOT_READY. "the recorder holds the writer lock"
        # and "there is not enough data" are different facts, and collapsing them would let a
        # locked archive be read as an empty one.
        "status": (
            "ARCHIVE_UNREADABLE" if arch.unreadable_reason
            else "READY" if arch.any_ready else "NOT_READY"
        ),
        "archive": {
            "db_path": arch.db_path,
            "db_exists": arch.db_exists,
            "unreadable_reason": arch.unreadable_reason,
            "total_rows": arch.total_rows,
            "span_days": round(arch.span_days, 3),
            "required_days_min": REQUIRED_DAYS_MIN,
            "preferred_days": PREFERRED_DAYS,
            "streams": [
                {"key": s.key, "venue": s.venue, "stream": s.stream,
                 "rows": s.rows, "present": s.present, "span_days": round(s.span_days, 3)}
                for s in arch.streams
            ],
        },
        "families": {
            f.value: {"ready": arch.family_ready[f.value],
                      "blockers": arch.family_blockers[f.value]}
            for f in Family
        },
        "horizon_gate": viability_table(p),
        "selected_horizons": {"taker": list(p.selected_horizons(False)),
                              "maker": list(p.selected_horizons(True))},
        "horizon_selection_dataset_role": (
            p.raw["horizon_viability_gate"]["measured_evidence"]["dataset_role"]),
        "results": [],
        "results_note": (
            "Empty by design. No family may report a result until its archive "
            "blockers clear. No result is ever computed from the PROFIT_CAMPAIGN_V1 "
            "one-day archive."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--db", default=None)
    a = ap.parse_args()

    rep = build_report(a.db)
    arch = rep["archive"]

    print("=" * 78)
    print(f"READINESS - {rep['protocol_id']}")
    print("=" * 78)
    print(f"protocol sha256 : {rep['protocol_sha256'][:16]}...")
    print(f"status          : {rep['status']}")
    print(f"archive         : {arch['db_path']}")
    print(f"                  exists={arch['db_exists']}  rows={arch['total_rows']:,}  "
          f"span={arch['span_days']:.2f}d  (need >= {arch['required_days_min']}d)")
    print()
    print("REQUIRED STREAMS")
    print(f"  {'stream':<34}{'rows':>12}   status")
    print("  " + "-" * 60)
    for s in arch["streams"]:
        label = f"{s['venue']}/{s['stream']}"
        print(f"  {label:<34}{s['rows']:>12,}   {'present' if s['present'] else 'ABSENT'}")
    print()
    print("FAMILIES")
    for fam, st in rep["families"].items():
        print(f"  {fam:<26}{'READY' if st['ready'] else 'NOT_READY'}")
        for b in st["blockers"]:
            print(f"      blocker: {b}")
    print()
    print("HORIZON GATE - admission on day-block LB95, not the point estimate")
    for r in rep["horizon_gate"]:
        adm = [x for x, ok in (("taker", r["taker_admissible"]),
                               ("maker", r["maker_admissible"])) if ok]
        if adm:
            print(f"  {r['horizon_s']:>5}s  taker LB {r['taker_lb95']:>6.2%}  "
                  f"maker LB {r['maker_lb95']:>6.2%}   {', '.join(adm)}")
    print("  selected grid (capped, deterministic):")
    for style, hz in rep["selected_horizons"].items():
        print(f"    {style:<6} {hz}")
    print()
    print(f"RESULTS: {len(rep['results'])}")
    print(f"  {rep['results_note']}")

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1)
        print(f"\nwrote {a.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
