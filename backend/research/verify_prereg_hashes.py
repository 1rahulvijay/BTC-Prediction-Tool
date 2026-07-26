"""Verify every preregistration hash recorded in PREREG_HASH.txt still matches its file.

A frozen protocol whose bytes changed is not a frozen protocol. The hash file is the integrity
record for every experiment in this repository; if a protocol is edited after freezing, every
result scored under it is invalid. Running this in CI means such an edit cannot pass review
unnoticed - which is the entire point of recording the hash in the first place.

Exit 0 = all recorded hashes match. Exit 1 = at least one drifted or its file is missing.

    python backend/research/verify_prereg_hashes.py
    python backend/research/verify_prereg_hashes.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
HASH_FILE = ROOT / "docs" / "active" / "PREREG_HASH.txt"
DOCS = ROOT / "docs" / "active"

# "NAME.md" on one line, "sha256 = <64 hex>" shortly after.
_ENTRY = re.compile(
    r"^(?P<name>[A-Za-z0-9_\-.]+\.md)\s*\n\s*sha256\s*=\s*(?P<sha>[0-9a-fA-F]{64})",
    re.MULTILINE,
)


def parse_entries(text: str) -> list[tuple[str, str]]:
    return [(m.group("name"), m.group("sha").lower()) for m in _ENTRY.finditer(text)]


def verify(hash_file: Path = HASH_FILE, docs: Path = DOCS) -> int:
    if not hash_file.is_file():
        print(f"MISSING hash record: {hash_file}")
        return 1
    entries = parse_entries(hash_file.read_text(encoding="utf-8"))
    if not entries:
        print(f"no hash entries parsed from {hash_file} - the record is unusable")
        return 1
    failures = 0
    for name, recorded in entries:
        path = docs / name
        if not path.is_file():
            print(f"  MISSING  {name}  (recorded {recorded[:12]}...)")
            failures += 1
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual == recorded:
            print(f"  MATCH    {name}  {recorded[:12]}...")
        else:
            print(f"  DRIFTED  {name}")
            print(f"           recorded {recorded}")
            print(f"           actual   {actual}")
            failures += 1
    print(f"\n{len(entries) - failures}/{len(entries)} preregistration hashes intact")
    if failures:
        print("INTEGRITY FAILURE - a frozen protocol was edited after freezing.")
        print("Every result scored under a drifted protocol is invalid.")
        return 1
    print("INTEGRITY INTACT")
    return 0


def selftest() -> int:
    import tempfile

    ok = True

    def chk(cond: bool, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok &= bool(cond)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        docs = root / "docs"
        docs.mkdir()
        good = docs / "PREREG_GOOD.md"
        good.write_text("frozen content\n", encoding="utf-8")
        digest = hashlib.sha256(good.read_bytes()).hexdigest()
        record = root / "HASH.txt"

        record.write_text(f"PREREG_GOOD.md\nsha256 = {digest}\n", encoding="utf-8")
        chk(verify(record, docs) == 0, "an unmodified protocol verifies clean")

        good.write_text("frozen content EDITED\n", encoding="utf-8")
        chk(verify(record, docs) == 1, "an EDITED protocol fails verification")

        good.unlink()
        chk(verify(record, docs) == 1, "a missing protocol file fails verification")

        record.write_text("nothing parseable here\n", encoding="utf-8")
        chk(verify(record, docs) == 1, "an unparseable hash record fails rather than passing")

        chk(verify(root / "absent.txt", docs) == 1, "a missing hash record fails")

    entries = parse_entries(HASH_FILE.read_text(encoding="utf-8")) if HASH_FILE.is_file() else []
    chk(len(entries) >= 4, f"LIVE: parsed {len(entries)} recorded prereg hashes from the repo")

    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    return selftest() if args.selftest else verify()


if __name__ == "__main__":
    raise SystemExit(main())
