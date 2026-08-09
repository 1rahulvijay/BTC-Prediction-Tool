"""Which analytics.duckdb is THE datastore — answered explicitly, or refused.

THE DEFECT
    `DB_PATH` silently defaults to `<data>/analytics.duckdb`. There is more than one such
    file on disk and they DISAGREE about history:

        data/analytics.duckdb              predictions_5m -> 07-04 (2,514)
                                           price_to_beat  -> 08-08 (19,122)
        data/btc_duckdbs/analytics.duckdb  predictions_5m -> 07-25 (5,836)
                                           price_to_beat  -> 07-25 (14,372)

    Neither is a superset. The second has three more weeks of MODEL rows; the first has two
    more weeks of PRICE rows. Different components have been writing to different files, so a
    study that takes the default gets a different history from one that names the archive -
    and neither is wrong on its own terms, which is what makes it dangerous.

    A retrain reads whichever the default happens to resolve to. Twelve to thirty hours of
    training against an unstated history is not an experiment, it is an anecdote.

WHAT THIS DOES, AND DELIBERATELY DOES NOT DO
    It does NOT choose. Which store is authoritative is an operator decision about which
    process owns the write path, and picking one here would bury that decision in code.

    It makes the ambiguity FAIL rather than resolve. When several candidates exist and none
    is declared, `resolve()` raises. When one is declared, it returns an identity - path,
    size, mtime, per-table row counts and spans - so a training run records WHICH history it
    consumed instead of implying there was only one.

    python backend/audit/datastore_identity.py            # report what is on disk
    python backend/audit/datastore_identity.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

#: Set this to the ONE store that owns the write path. Absolute or repo-relative.
DECLARATION_ENV = "BTC_CANONICAL_DB"

#: Tables whose spans distinguish these stores in practice - the ones observed to disagree.
WITNESS_TABLES = ("predictions_5m", "predictions_15m", "model_predictions", "price_to_beat")


class DatastoreAmbiguous(RuntimeError):
    """Several candidate stores exist and none is declared. Refusing to pick one."""


def candidates(root: Path | None = None) -> list[Path]:
    """Every analytics.duckdb under data/, newest first."""
    base = Path(root) if root else (REPO / "data")
    if not base.exists():
        return []
    found = sorted(base.rglob("analytics.duckdb"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return [p for p in found if p.is_file()]


def identity(path: Path) -> dict:
    """Path, size, mtime and per-table spans. NOT a content hash: these files are gigabytes
    and hashing one on every training start would cost more than it proves. Size + mtime +
    row counts + spans is enough to detect a substituted store, which is the actual risk."""
    stat = path.stat()
    out = {
        "path": str(path.resolve()),
        "size_bytes": stat.st_size,
        "mtime_utc_ms": int(stat.st_mtime * 1000),
        "path_sha256": hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16],
        "tables": {},
    }
    try:
        import duckdb
        con = duckdb.connect(str(path), read_only=True)
        try:
            for table in WITNESS_TABLES:
                try:
                    n, mx = con.execute(
                        f'SELECT count(*), max("timestamp") FROM "{table}"').fetchone()
                    out["tables"][table] = {"rows": int(n or 0), "max_ts_ms": mx}
                except Exception:
                    out["tables"][table] = {"rows": None, "max_ts_ms": None}
        finally:
            con.close()
    except Exception as exc:
        out["tables_error"] = str(exc)[:120]
    return out


def resolve(root: Path | None = None, declared: str | None = None) -> dict:
    """The canonical store, or raise.

    One candidate  -> no ambiguity to resolve, return it.
    Several + declared -> the declared one, with the others recorded as rejected.
    Several + none declared -> RAISE. Defaulting here is the defect.
    """
    found = candidates(root)
    if not found:
        raise DatastoreAmbiguous(
            "no analytics.duckdb found; refusing to create or assume one")
    decl = declared if declared is not None else os.environ.get(DECLARATION_ENV)
    if decl:
        want = Path(decl)
        if not want.is_absolute():
            want = REPO / want
        match = next((p for p in found if p.resolve() == want.resolve()), None)
        if match is None:
            raise DatastoreAmbiguous(
                f"{DECLARATION_ENV}={decl} does not name any store on disk "
                f"({[str(p) for p in found]}); refusing to fall back to a default")
        ident = identity(match)
        ident["declared"] = True
        ident["rejected"] = [str(p.resolve()) for p in found if p != match]
        return ident
    if len(found) == 1:
        ident = identity(found[0])
        ident["declared"] = False
        ident["rejected"] = []
        return ident
    raise DatastoreAmbiguous(
        f"{len(found)} analytics.duckdb stores exist and none is declared: "
        f"{[str(p) for p in found]}. They disagree about history, so a default selection "
        f"silently decides which past a study sees. Set {DECLARATION_ENV} to the one that "
        f"owns the write path.")


def selftest() -> int:
    import tempfile
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a").mkdir()
        (root / "b").mkdir()
        one = root / "a" / "analytics.duckdb"
        one.write_bytes(b"x")

        ident = resolve(root)
        check(ident["path"] == str(one.resolve()) and ident["declared"] is False,
              "a single store resolves without a declaration - there is no ambiguity to refuse")

        two = root / "b" / "analytics.duckdb"
        two.write_bytes(b"y")
        try:
            resolve(root)
            raise AssertionError("two stores resolved silently")
        except DatastoreAmbiguous as exc:
            checks += 1
            print(f"  PASS  two undeclared stores RAISE ({str(exc)[:56]}...) - a default "
                  f"selection is exactly how a study silently gets a different history")

        picked = resolve(root, declared=str(two))
        check(picked["path"] == str(two.resolve()) and picked["declared"] is True,
              "a declared store resolves to that store")
        check(picked["rejected"] == [str(one.resolve())],
              "and the REJECTED ones are recorded, so a manifest shows what was not chosen "
              "rather than implying only one existed")

        try:
            resolve(root, declared=str(root / "nope.duckdb"))
            raise AssertionError("a bogus declaration fell back")
        except DatastoreAmbiguous:
            checks += 1
            print("  PASS  a declaration naming no real store RAISES rather than falling back "
                  "to a default - a typo must not silently select the other history")

        check(set(identity(one)) >= {"path", "size_bytes", "mtime_utc_ms", "tables"},
              "identity carries path, size, mtime and per-table spans, so a training run "
              "records WHICH history it consumed")

    print(f"\nDATASTORE IDENTITY SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    found = candidates()
    print("=" * 78)
    print(f"ANALYTICS STORES ON DISK: {len(found)}")
    print("=" * 78)
    import datetime as dt
    for path in found:
        ident = identity(path)
        print(f"\n  {ident['path']}")
        print(f"    {ident['size_bytes'] / 1e6:,.0f} MB")
        for table, info in ident["tables"].items():
            mx = info.get("max_ts_ms")
            when = (dt.datetime.fromtimestamp(mx / 1000, dt.UTC).strftime("%Y-%m-%d")
                    if mx else "-")
            print(f"    {table:<22}{str(info.get('rows') or '-'):>10}  last {when}")
    print()
    try:
        chosen = resolve()
        print(f"CANONICAL: {chosen['path']}  (declared={chosen['declared']})")
        return 0
    except DatastoreAmbiguous as exc:
        print(f"REFUSED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
