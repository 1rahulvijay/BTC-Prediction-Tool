"""Mechanically flags research results that cannot be believed.

WHY
    31 standalone scripts (research/v1..v31) were produced and reported headline numbers such
    as "+5,209,276.4% profit", "100% win rate" and "77.6% accuracy". Auditing them by reading
    prose does not scale and does not stick. These checks read the SCRIPTS and fail on the
    anti-patterns that made those numbers meaningless.

THE FOUR DISQUALIFYING PATTERNS
    1. ASSUMED OUTCOME - the win rate is drawn from a random number generator against a
       hardcoded constant rather than measured from a prediction. v19 does
       `if np.random.rand() < 0.78`, so its 78% "accuracy" is an input, and the +5.2M% return
       is that assumption compounded. Nothing is tested.

    2. DECLARED OUTCOME - the result is simply assigned. v17 contains
       `win_rate = 100.0  # ... We already know the future price.` A 100% win rate is never a
       measurement, and the comment states the lookahead outright.

    3. UNBOUNDED-LOSS ACCOUNTING - `capital += NOTIONAL * bps` with a FIXED notional, applied
       regardless of remaining capital. Capital goes arbitrarily negative, which is how
       -212%, -834%, -879% and -10032% "returns" appear. You cannot lose more than 100% of
       unleveraged capital; those magnitudes are artifacts, not losses.

    4. NO OUT-OF-SAMPLE SEPARATION - a performance number reported from the same data the rule
       was chosen on. Worst in v26, where a genetic algorithm evolves DNA against
       `evaluate_fitness(df, dna)` on one dataset and reports the best-of-N as +32,598%.

WHAT SURVIVES
    A negative result on real data with no lookahead is worth more than any of the above. v18
    is the model: it splits the data, reports 50.07% -> 49.44%, and says so plainly.

    python backend/research/audit_research_claims.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = Path(__file__).resolve().parents[2]
RESEARCH = REPO / "research"

ASSUMED_OUTCOME = re.compile(r"random\.rand\(\)\s*<\s*0?\.\d+|rand\(\)\s*<\s*0?\.\d+")
DECLARED_OUTCOME = re.compile(r"win_rate\s*=\s*(100(\.0)?|1\.0)\b")
FIXED_NOTIONAL = re.compile(r"capital\s*\+=\s*\d[\d_.]*\s*\*")
SPLIT = re.compile(r"train_test_split|TimeSeriesSplit|\[:\s*split|\[split\s*:|fold|walk.?forward",
                   re.I)
PERFORMANCE = re.compile(r"win_rate|sharpe|profit_pct|cumulative|accuracy", re.I)


def audit_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
    findings = []
    if ASSUMED_OUTCOME.search(code):
        findings.append("ASSUMED_OUTCOME: win rate drawn from RNG against a constant")
    if DECLARED_OUTCOME.search(code):
        findings.append("DECLARED_OUTCOME: win rate assigned, not measured")
    if FIXED_NOTIONAL.search(code):
        findings.append("UNBOUNDED_LOSS: fixed notional added to capital regardless of balance")
    if PERFORMANCE.search(code) and not SPLIT.search(code):
        findings.append("NO_OUT_OF_SAMPLE: performance reported with no train/test separation")
    return findings


def main() -> int:
    if not RESEARCH.is_dir():
        print(f"no research directory at {RESEARCH} - nothing to audit")
        return 0

    scripts = sorted(RESEARCH.glob("v*.py"),
                     key=lambda p: (int("".join(c for c in p.name.split("_")[0]
                                                if c.isdigit()) or 0), p.name))
    print("=" * 88)
    print("RESEARCH CLAIM AUDIT - these scripts are NOT repository evidence")
    print("=" * 88)
    clean, flagged = [], {}
    for script in scripts:
        findings = audit_file(script)
        if findings:
            flagged[script.name] = findings
        else:
            clean.append(script.name)

    for name, findings in flagged.items():
        print(f"\n  {name}")
        for finding in findings:
            print(f"      - {finding}")

    print(f"\n{'-' * 88}")
    print(f"  scripts audited      : {len(scripts)}")
    print(f"  flagged              : {len(flagged)}")
    print(f"  no pattern detected  : {len(clean)}  {clean}")
    print()
    print("  A clean scan does NOT mean a result is valid. It means these four specific")
    print("  disqualifying patterns were absent. Out-of-sample economic evidence is still")
    print("  required before any of this influences a model, a threshold or a size.")
    print()
    print("  NONE of these scripts is wired into the application, and none may be")
    print("  without passing the repository's normal promotion gates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
