"""One canonical answer to "which database am I reading?", and a refusal when that is unclear.

WHY THIS EXISTS
    Four `analytics.duckdb` files exist on this machine with different spans - the configured
    `data/` copy, a `data/btc_duckdbs/` copy, a nested `btc_full_project/` copy, and an
    851 MB two-month-old file under AppData from an earlier install. Modules resolve their own
    path from `BTC_DB_PATH` or `BTC_DATA_DIR` independently, so the app can serve one file
    while research scores another.

    A correct query against the wrong database is still wrong, and nothing in the result says
    so. Coverage, row counts, "latest timestamp" and settlement counts all look plausible
    whichever file answered them.

    So: resolve once, record the identity, and make an unresolved choice an ERROR rather than a
    silent default. `describe()` returns the identity any report or manifest should carry.

    python backend/datastore_identity.py                # identity of the configured store
    python backend/datastore_identity.py --scan         # every candidate found, with spans
    python backend/datastore_identity.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]

#: The canonical name. Every store below is one of these.
STORE_FILENAME = "analytics.duckdb"

#: Where a stray copy has actually been found. Used by --scan and by the ambiguity check.
#: These are LOOKED FOR, not read: naming them is how a known trap stays visible.
KNOWN_STRAY_LOCATIONS = (
    Path("data") / "btc_duckdbs" / STORE_FILENAME,
    Path("btc_full_project") / "btc-tool" / "data" / STORE_FILENAME,
)


class DataStoreAmbiguous(RuntimeError):
    """Raised when more than one plausible production store exists and none was chosen.

    Defaulting here is what allows a silent wrong answer. Naming the candidates and refusing
    costs one environment variable and removes an entire class of invisible error."""


class DataStoreMissing(RuntimeError):
    """Raised when the configured store does not exist. Never silently created."""


def configured_path() -> Path | None:
    """The explicitly configured store, or None when nothing is set.

    Mirrors `database.py`: BTC_DB_PATH names a FILE, BTC_DATA_DIR names its FOLDER."""
    explicit = os.environ.get("BTC_DB_PATH")
    if explicit:
        return Path(explicit).resolve()
    folder = os.environ.get("BTC_DATA_DIR")
    if folder:
        return (Path(folder) / STORE_FILENAME).resolve()
    return None


def default_path() -> Path:
    """Where the store lives when nothing is configured."""
    return (ROOT / "data" / STORE_FILENAME).resolve()


def find_candidates(root: Path | None = None) -> list[Path]:
    """Every store this repository could plausibly be pointed at, in preference order."""
    base = (root or ROOT).resolve()
    found = [base / "data" / STORE_FILENAME]
    found.extend(base / stray for stray in KNOWN_STRAY_LOCATIONS)
    return [p for p in found if p.is_file()]


def resolve(*, strict: bool = False, root: Path | None = None) -> Path:
    """The one store to use.

    `strict=True` (production) refuses to guess: with several candidates present and nothing
    configured, it raises rather than picking the default. Research keeps the default so
    ordinary work is not blocked, but the identity is still recorded."""
    chosen = configured_path()
    if chosen is not None:
        if not chosen.is_file():
            raise DataStoreMissing(
                f"BTC_DB_PATH/BTC_DATA_DIR points at {chosen}, which does not exist. "
                "The store is never created implicitly - a fresh empty database would read as "
                "an honest empty one.")
        return chosen
    candidates = find_candidates(root)
    if strict and len(candidates) > 1:
        listed = "\n  ".join(f"{p}  ({p.stat().st_size:,} bytes)" for p in candidates)
        raise DataStoreAmbiguous(
            f"{len(candidates)} candidate stores exist and none was chosen:\n  {listed}\n"
            "Set BTC_DB_PATH explicitly. Production must not default when the answer is "
            "genuinely unclear - a correct query against the wrong store is still wrong.")
    return candidates[0] if candidates else default_path()


def describe(path: Path | None = None, *, strict: bool = False) -> dict:
    """Identity of the store in use. Belongs in every report and artifact manifest.

    Spans come from the store itself where a timestamped table exists, because file size and
    mtime do not distinguish a live archive from a stale copy of similar size."""
    target = path or resolve(strict=strict)
    identity = {
        "path": str(target),
        "exists": target.is_file(),
        "bytes": target.stat().st_size if target.is_file() else 0,
        "configured": configured_path() is not None,
        "candidates": [str(p) for p in find_candidates()],
    }
    if not identity["exists"]:
        return identity
    try:
        import duckdb
        con = duckdb.connect(str(target), read_only=True)
    except Exception as exc:
        identity["readable"] = False
        identity["error"] = str(exc)
        return identity
    try:
        identity["readable"] = True
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' ORDER BY table_name").fetchall()]
        identity["tables"] = len(tables)
        for name in ("pm_round_snapshots", "rule_paper_trades", "predictions"):
            if name in tables:
                columns = {r[0] for r in con.execute(f"DESCRIBE {name}").fetchall()}
                column = next((c for c in ("ts", "ts_ms", "created_ts") if c in columns), None)
                if column is None:
                    continue
                rows, low, high = con.execute(
                    f"SELECT count(*), min({column}), max({column}) FROM {name}").fetchone()
                identity[f"{name}_rows"] = int(rows)
                identity[f"{name}_span"] = [low, high]
                break
    finally:
        con.close()
    return identity


def selftest() -> int:
    import tempfile
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "data").mkdir()
        (base / "data" / STORE_FILENAME).write_bytes(b"x")
        check(len(find_candidates(base)) == 1, "one store present is unambiguous")
        check(resolve(strict=True, root=base).name == STORE_FILENAME,
              "...and strict mode resolves it without complaint")

        (base / "data" / "btc_duckdbs").mkdir()
        (base / "data" / "btc_duckdbs" / STORE_FILENAME).write_bytes(b"yy")
        check(len(find_candidates(base)) == 2, "a stray copy IS discovered, not ignored")
        try:
            resolve(strict=True, root=base)
            check(False, "unreachable")
        except DataStoreAmbiguous as exc:
            check("btc_duckdbs" in str(exc),
                  "strict mode REFUSES and names both candidates - it never guesses")
        check(resolve(strict=False, root=base).parent.name == "data",
              "research mode still resolves, so ordinary work is not blocked")

    saved = os.environ.get("BTC_DB_PATH")
    try:
        os.environ["BTC_DB_PATH"] = str(Path(tempfile.gettempdir()) / "definitely_absent.duckdb")
        try:
            resolve(strict=True)
            check(False, "unreachable")
        except DataStoreMissing:
            check(True, "a configured but MISSING store raises - it is never created implicitly")
    finally:
        if saved is None:
            os.environ.pop("BTC_DB_PATH", None)
        else:
            os.environ["BTC_DB_PATH"] = saved

    identity = describe()
    check("path" in identity and "candidates" in identity,
          "describe() reports the path and every candidate, for reports and manifests")
    print(f"\nDATASTORE IDENTITY SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--scan", action="store_true", help="report every candidate store")
    parser.add_argument("--strict", action="store_true", help="refuse to guess (production)")
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    if args.scan:
        candidates = find_candidates()
        print(f"{len(candidates)} candidate store(s):")
        for path in candidates:
            info = describe(path)
            span = info.get("pm_round_snapshots_span") or info.get("rule_paper_trades_span")
            print(f"  {path}")
            print(f"    {info['bytes']:,} bytes | tables {info.get('tables', '?')}"
                  + (f" | span {span}" if span else ""))
        if len(candidates) > 1:
            print("\n  More than one store exists. Production must set BTC_DB_PATH explicitly;")
            print("  otherwise a correct query can still be answered by the wrong file.")
        return 0

    try:
        identity = describe(strict=args.strict)
    except (DataStoreAmbiguous, DataStoreMissing) as exc:
        print(f"DATASTORE UNRESOLVED: {exc}")
        return 1
    print("datastore identity")
    for key, value in identity.items():
        print(f"  {key:<28}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
