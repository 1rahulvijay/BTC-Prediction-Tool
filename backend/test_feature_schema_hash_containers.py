"""`feature_schema_hash` must accept any iterable, and hash it the same way.

THE DEFECT
    `hash_json(list(feature_names or []))`. The `or` evaluates truthiness, and the real caller
    passes `merged.columns` - a pandas Index, which refuses to answer:

        ValueError: The truth value of a Index is ambiguous.

    It killed a 1,000-day matrix rebuild at the MANIFEST WRITE, after every missing day had
    been downloaded and 1,440,000 rows built. The whole run was lost to an idiom that is only
    safe for containers whose emptiness is a bool.

    The container must also not change the hash. A schema is its ordered names; if an Index and
    a list of the same names hashed differently, artifacts built by different callers would
    read as different schemas and refuse each other.

    python backend/test_feature_schema_hash_containers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def main() -> int:
    import pandas as pd
    from artifact_identity import feature_schema_hash as fsh

    names = ["close", "rv_15m", "atr_norm"]
    index = pd.DataFrame(columns=names).columns

    # The exact call that failed: a multi-element pandas Index.
    got = fsh(index)
    check(isinstance(got, str) and len(got) == 64,
          "a pandas Index hashes without raising - `or` on a multi-element Index is the "
          "ValueError that killed a completed 1,000-day rebuild at the manifest write")

    check(fsh(names) == got,
          "and a plain list of the same names hashes IDENTICALLY - the container must not "
          "change the schema identity, or artifacts from different callers refuse each other")
    check(fsh(tuple(names)) == got, "so must a tuple")
    check(fsh(iter(names)) == got,
          "and a generator - which `or` would have consumed nothing from, silently hashing an "
          "empty schema instead of raising")

    empty = fsh([])
    check(fsh(None) == empty and fsh(pd.Index([])) == empty,
          "None and an empty Index agree, so 'no columns' has one identity")
    check(empty != got, "and an empty schema is not the same as a populated one")

    # Order is the schema. Reordering must change the hash.
    check(fsh(["rv_15m", "close", "atr_norm"]) != got,
          "reordered names hash DIFFERENTLY - column order is part of the contract a model "
          "was trained under, not an incidental detail")

    print("")
    print(f"FEATURE SCHEMA HASH CONTAINERS: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
