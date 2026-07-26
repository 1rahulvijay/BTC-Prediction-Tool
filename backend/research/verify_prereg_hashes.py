"""Verify every preregistration hash recorded in PREREG_HASH.txt still matches its file.

HASH CONTRACT: sha256 of the LF-canonicalised TEXT, not of the raw file bytes.

Recorded value = sha256( file_bytes.replace(CRLF, LF) )

An external auditor must not assume `sha256sum <file>` reproduces the recorded value on a
Windows checkout - it will not, because git rewrites .md to CRLF. Canonicalising means the hash
identifies the protocol's CONTENT rather than the checkout that produced it, while any real edit
to the text still changes it.

A frozen protocol whose TEXT changed is not a frozen protocol. The hash file is the integrity
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

def _canonical_bytes(path: Path) -> bytes:
    """File bytes with line endings normalised to LF.

    A preregistration hash must identify its CONTENT, not the checkout that produced it. Git's
    autocrlf rewrites .md files to CRLF on Windows, so hashing raw bytes reported
    PREREG_COMPLETE_TRADE_M0_V2 as "edited after freezing" purely because the branch had been
    checked out again - a false integrity alarm, which is corrosive precisely because this check
    is supposed to be the thing you trust.

    This does NOT weaken the guarantee: any real change to the text still changes the hash. It
    only removes a platform artifact. Recorded hashes were computed on LF content and continue
    to verify unchanged.
    """
    return path.read_bytes().replace(b"\r\n", b"\n")


NL = chr(10)
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


HASH_MODE_LINE = "hash_mode = lf_canonical_text_v1"

# The registry is a SCHEMA, not a permissive text file. Every non-empty line must match one of
# these constructs; anything else is corruption. A tolerant parser is how the file silently grew
# to 635 lines with 69 malformed headers while this verifier stayed green.
_ALLOWED = (
    re.compile(r"^hash_mode = .+$"),
    re.compile(r"^[A-Za-z0-9_\-.]+\.md$"),
    re.compile(r"^sha256 = [0-9a-fA-F]{64}$"),
    re.compile(r"^frozen = \d{4}-\d{2}-\d{2}$"),
    re.compile(r"^bytes  = \d+$"),
    re.compile(r"^note   = .*$"),
    re.compile(r"^=+$"),
    re.compile(r"^Every sha256 below .*$"),
    re.compile(r"^CRLF replaced by LF\..*$"),
    re.compile(r"^Verify every entry: .*$"),
)
EXPECTED_PROTOCOLS = frozenset({
    "PREREG_CONDITIONAL_STOPPING_V1.md",
    "PREREG_BINANCE_VOLATILITY_MOMENTUM_V1.md",
    "PREREG_BINANCE_V1_CLARIFICATION_001.md",
    "PREREG_BINANCE_V1_CLARIFICATION_002.md",
    "PREREG_BINANCE_V1_CLARIFICATION_003.md",
    "PREREG_COMPLETE_TRADE_M0_V2.md",
    "PREREG_COMPLETE_TRADE_M0_V2_CLARIFICATION_001.md",
})


def validate_registry(text: str) -> list[str]:
    """Structural problems, or an empty list. Strict: unknown content is corruption."""
    problems: list[str] = []
    stripped = [ln.strip() for ln in text.splitlines()]
    meaningful = [ln for ln in stripped if ln]
    if not meaningful:
        return ["registry is empty"]
    if meaningful[0] != HASH_MODE_LINE:
        problems.append(
            "first line must be exactly " + repr(HASH_MODE_LINE)
            + ", found " + repr(meaningful[0]))
    headers = [ln for ln in meaningful if ln.startswith("hash_mode")]
    if len(headers) != 1:
        problems.append("expected exactly 1 hash_mode header, found " + str(len(headers)))
    unknown = [ln for ln in meaningful if not any(r.match(ln) for r in _ALLOWED)]
    if unknown:
        problems.append("unrecognised registry line(s): " + repr(unknown[:3]))
    names = [n for n, _ in parse_entries(text)]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        problems.append("duplicate protocol entries: " + str(duplicates))
    sha_lines = len([ln for ln in meaningful if ln.startswith("sha256 = ")])
    if sha_lines != len(names):
        problems.append(str(sha_lines) + " sha256 lines but " + str(len(names))
                        + " parsed entries")
    return problems


def validate_repo_registry(text: str) -> list[str]:
    """Registry validation PLUS the exact expected protocol set for this repository."""
    problems = validate_registry(text)
    names = {n for n, _ in parse_entries(text)}
    missing = sorted(EXPECTED_PROTOCOLS - names)
    extra = sorted(names - EXPECTED_PROTOCOLS)
    if missing:
        problems.append("missing expected protocol(s): " + str(missing))
    if extra:
        problems.append("unknown protocol(s) in registry: " + str(extra))
    return problems


def verify(hash_file: Path = HASH_FILE, docs: Path = DOCS,
           *, repo_strict: bool = True) -> int:
    if not hash_file.is_file():
        print(f"MISSING hash record: {hash_file}")
        return 1
    text = hash_file.read_text(encoding="utf-8")
    # STRUCTURAL VALIDATION. The registry was silently corrupted to 635 lines with 69 malformed
    # headers while this verifier stayed green, because the regex simply found the 7 good entries
    # among the noise. A registry that can rot undetected is not an integrity record.
    # Repo-strict: also demands the exact expected protocol set, so a silently
    # dropped or added protocol is corruption rather than a smaller registry.
    # repo_strict=False is for synthetic fixtures that exercise HASHING rather than
    # repository membership; the real run is always strict.
    structural = (validate_repo_registry(text) if repo_strict
                  else validate_registry(text))
    if structural:
        for problem in structural:
            print("  REGISTRY  " + problem)
        print("")
        print("REGISTRY MALFORMED - rebuild it before trusting any hash below.")
        return 1

    entries = parse_entries(text)
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
        actual = hashlib.sha256(_canonical_bytes(path)).hexdigest()
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
        # Compute the expected digest the SAME way verify() does, or the fixture is
        # testing a different rule than the code implements.
        digest = hashlib.sha256(_canonical_bytes(good)).hexdigest()
        record = root / "HASH.txt"

        record.write_text(
            NL.join(["hash_mode = lf_canonical_text_v1", "PREREG_GOOD.md",
                     "sha256 = " + digest]) + NL, encoding="utf-8")
        chk(verify(record, docs, repo_strict=False) == 0, "an unmodified protocol verifies clean")

        # THE FALSE ALARM THIS PREVENTS: git autocrlf rewrites .md on Windows checkout, which
        # reported PREREG_COMPLETE_TRADE_M0_V2 as "edited after freezing" when its text was
        # byte-identical. A hash record nobody trusts is worse than none.
        good.write_bytes(b"frozen content" + bytes([13, 10]))
        chk(verify(record, docs, repo_strict=False) == 0,
            "a CRLF checkout of IDENTICAL text still verifies (no platform false alarm)")
        good.write_bytes(b"frozen content" + bytes([10]))
        chk(verify(record, docs, repo_strict=False) == 0, "and the LF original still verifies")

        good.write_text("frozen content EDITED\n", encoding="utf-8")
        chk(verify(record, docs, repo_strict=False) == 1, "an EDITED protocol fails verification")

        good.unlink()
        chk(verify(record, docs, repo_strict=False) == 1, "a missing protocol file fails verification")

        record.write_text("nothing parseable here\n", encoding="utf-8")
        chk(verify(record, docs, repo_strict=False) == 1, "an unparseable hash record fails rather than passing")

        chk(verify(root / "absent.txt", docs, repo_strict=False) == 1, "a missing hash record fails")
        good.write_bytes(b"frozen content" + bytes([10]))
        def registry(*body: str) -> None:
            record.write_text(NL.join(body) + NL, encoding="utf-8")
        entry = ["PREREG_GOOD.md", "sha256 = " + digest]
        registry("hash_mode = lf_canonical_text_v1", *entry)
        chk(verify(record, docs, repo_strict=False) == 0, "a well-formed registry with one header verifies")
        registry("hash_mode = x", "hash_mode = x", *entry)
        chk(verify(record, docs, repo_strict=False) == 1, "a DUPLICATED header is rejected as malformed")
        registry("hash_mode = x", "=hash_mode = x", *entry)
        chk(verify(record, docs, repo_strict=False) == 1, "a MALFORMED =hash_mode line is rejected")
        registry("hash_mode = x", *entry, *entry)
        chk(verify(record, docs, repo_strict=False) == 1, "a DUPLICATE protocol entry is rejected")


    print("registry schema (strict)")
    good = NL.join([HASH_MODE_LINE, "", "PREREG_X.md", "sha256 = " + "a" * 64])
    chk(validate_registry(good) == [], "a minimal well-formed registry validates")
    chk(bool(validate_registry(NL.join(["hash_mode = something_wrong", "PREREG_X.md",
                                        "sha256 = " + "a" * 64]))),
        "a WRONG hash_mode value is rejected, not merely a missing one")
    chk(bool(validate_registry(good + NL + "arbitrary garbage")),
        "arbitrary unparsed content is rejected")
    chk(bool(validate_registry(NL.join(["PREREG_X.md", "sha256 = " + "a" * 64]))),
        "a registry with no header is rejected")
    live = HASH_FILE.read_text(encoding="utf-8") if HASH_FILE.is_file() else ""
    chk(validate_repo_registry(live) == [],
        "LIVE: the repository registry passes repo-strict validation")
    chk(bool(validate_repo_registry(NL.join(
        l for l in live.splitlines() if "CONDITIONAL_STOPPING" not in l))),
        "silently DROPPING a protocol from the registry is rejected")

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
