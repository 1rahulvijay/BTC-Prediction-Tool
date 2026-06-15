"""
cleanup_artifacts.py — remove SUPERSEDED / unneeded research files (safe, conservative).
=========================================================================================
Removes ONLY a hard-coded allow-list of files that are clearly stale or superseded — old
model versions, one-off research outputs. It NEVER touches the active models, the live
DBs, or the data the app/training needs.

On "leakage data": the earlier leaked head models (beat/path/magnitude/fingerprints) were
already deleted and rebuilt LEAK-FREE — there is no standing "leakage file" to remove. If
you have a specific file you consider leaked, add it to STALE below.

NEVER removed (protected): analytics.duckdb, execution_layer.duckdb, everything in
saved_models/ (active heads), research_matrix_1m / persistence_dataset / trade_features_backfill
/ crossvenue_flow / signal_history (active training inputs), backfill_cache/.

Usage:
  python backend/cleanup_artifacts.py            # DRY-RUN: list what would be removed
  python backend/cleanup_artifacts.py --apply    # actually delete the stale allow-list
"""
from __future__ import annotations

import argparse
import os

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")

# Hard allow-list of superseded / one-off research outputs (relative to DATA_DIR).
STALE = [
    "selectivity_model_v1.pkl",          # superseded by saved_models/selectivity_models.pkl
    "selectivity_model_v2.pkl",          # superseded by saved_models/selectivity_models.pkl
    "model_bakeoff_predictions.parquet", # one-off bakeoff research output
    "model_bakeoff_report.json",         # one-off bakeoff research output
    "trading_edge_report.json",          # one-off research output
    "gamma_event_sample.json",           # one-off sample
]
# Large research artifacts — listed but NOT auto-deleted (remove manually if not researching).
LARGE_OPTIONAL = ["expanded_matrix.parquet"]

# Protected — never touched even if mistakenly added above.
PROTECTED = {"analytics.duckdb", "execution_layer.duckdb", "research_matrix_1m.parquet",
             "persistence_dataset.parquet", "trade_features_backfill.parquet",
             "crossvenue_flow.parquet", "signal_history.pkl"}


def _size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    args = ap.parse_args()

    removed, total = 0, 0
    print(f"[cleanup] {'APPLYING' if args.apply else 'DRY-RUN'} — stale-artifact cleanup in {DATA_DIR}")
    for name in STALE:
        if name in PROTECTED:
            continue
        p = os.path.join(DATA_DIR, name)
        if os.path.exists(p):
            sz = _size(p); total += sz
            if args.apply:
                try:
                    os.remove(p); removed += 1
                    print(f"  removed  {name}  ({sz/1e6:.1f} MB)")
                except OSError as e:
                    print(f"  FAILED   {name}: {e}")
            else:
                print(f"  would remove  {name}  ({sz/1e6:.1f} MB)")
    for name in LARGE_OPTIONAL:
        p = os.path.join(DATA_DIR, name)
        if os.path.exists(p):
            print(f"  (kept) large research artifact {name} ({_size(p)/1e6:.1f} MB) — "
                  f"delete manually if not researching")
    verb = "removed" if args.apply else "would free"
    print(f"[cleanup] {verb} {total/1e6:.1f} MB"
          + (f" ({removed} files)" if args.apply else " — run with --apply to delete"))


if __name__ == "__main__":
    main()
