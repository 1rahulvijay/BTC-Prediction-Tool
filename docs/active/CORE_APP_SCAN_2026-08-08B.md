# Core-app scan — two defects, one of them the third closed gate — `2026-08-08`

A sweep of the core app for logic defects, run as automated pattern searches over
`server.py`, `model.py`, `prediction_verifier.py`, `database.py`, `price_to_beat.py`,
`decision_gate.py`, `regime.py`, `features.py`, `calibration.py`, `ab_testing.py`,
`model_promotion.py`, `model_verifier.py` and `target_contract.py`, then each candidate
read and either dismissed or proven.

```text
swept clean   mutable default arguments               0
swept clean   `except` returning a success value      0
swept clean   millisecond quantity vs time.time()     0
dismissed     swallowed DB exceptions                 18 of 21 are schema ALTERs (P0-24, recorded)
FOUND         a declared zero replaced by a default   5 sites
FOUND         a "not a gate" label deciding every trade
```

---

## 1. A declared band of zero was silently widened to 8bps

Every consumer resolved the grading barrier the same way:

```python
float(pred.get("neutralBand", 0.0008) or 0.0008)
```

`0.0` is falsy, so a declared zero became 8bps. That would be harmless if zero were
unreachable. **It is not:**

```text
$ BTC_LABEL_COST_FLOOR=0
  causal_neutral_band(...)          0.0      <- what the model declares
  training labels built at          0.0      <- same env var, features.py
  what the verifier recorded        0.0008   <- the `or`
```

`causal_neutral_band` clamps with `min(max(cost_floor, 0.0), 0.003)`, so the floor really
can be zero, and the *same* environment variable sets the training label floor. A zero-cost
study would have trained at 0.0 and been graded against a barrier the model never saw —
which is precisely the train/serve mismatch `causal_neutral_band` was written to eliminate,
reintroduced by an `or` in its consumers.

Worse, one of the five sites was inside the **5.4 fix itself**:
`model_verifier.py` `threshold=float(p.get("neutral_band") or self.neutral_band)` — the fix
that made a seat vote grade at the parent's band discarded that band whenever it was zero.

One rule now, in the contract module where the barrier belongs:

```text
resolve_neutral_band(0.0)      -> 0.0        a width
resolve_neutral_band(0.0029)   -> 0.0029     a width
resolve_neutral_band(None)     -> 0.0008     absent
resolve_neutral_band("x")      -> 0.0008     not a number
resolve_neutral_band(-1.0)     -> 0.0008     not a width
resolve_neutral_band(nan)      -> 0.0008     not a width
```

Applied at all five sites: `prediction_verifier` (record and grade), `server` (two log
paths), `model_verifier`, and `historical_replay` — the last one matters because replay is
what research reads, so a wrong barrier there has a longer half-life.

---

## 2. The label that documented itself as "not a gate" was the only gate

`decision_gate.py` defines its own three-state verdict, and the module docstring calls
WEAK_LEAN *"a committed side but with caveats"*. It has a `# SOFT reasons` section. **The
distinction was never implemented** — any entry in `no_trade_reasons` blocked TRADE:

```python
elif p.get("actionable") and not ordered:      # `ordered` = ALL reasons
    p["trade_verdict"] = "TRADE"
```

One of those reasons cannot be cleared:

```text
_confluence(...)                returns "A", "B" or "C" UNCONDITIONALLY (server.py:3681)
server attaches it              to every prediction, as setupQuality + confluence
decision_gate                   raises grade_unproven unless grade_validated is set
grade_validated                 has ZERO production writers - only a test fixture and
                                grade_scorecard.py, whose verdict is a human decision
```

So `trade_verdict` **could never be `TRADE`**, and `model_consensus`'s entry condition is
literally `verdict != "TRADE" -> no edge`. A third closed gate, on top of the calibration
and contract gates found in the previous commit — and this one closed by accident rather
than by measurement.

And `_confluence`'s own docstring said:

> *logged/displayed only — NOT a live gate; no bet/abstain/champion decision reads this*

which was false. The grade decided every trade, by never permitting one. That is this
repository's recurring defect in its purest form: **a comment guaranteeing a property the
code does not have.**

### The fix respects the earlier refusal

Removing `grade_unproven` as a reason was proposed once and **refused** — the grade is
measured *inverted* (§5br), and that refusal was right. This does not remove it. It stays in
`no_trade_reasons` and in the human text. It simply stops being the thing that decides,
which is what the module already said it was:

```python
VERDICT_CAVEATS = frozenset({"grade_unproven"})
p["verdict_blocked_by"] = [r for r in ordered if r not in VERDICT_CAVEATS]
p["verdict_caveats"]    = [r for r in ordered if r in VERDICT_CAVEATS]
```

The reasoning is that a label measured **not to stratify** carries no information in either
direction, so it cannot be the deciding input. Everything else still blocks, including
`fallback_lean_only`, whose own comment calls it "a real skip", and `decision_gate_error`,
so an analysis that failed midway can still only produce NO_TRADE.

### Nothing starts trading because of this

Checked explicitly, and asserted in the test:

```text
assert_admissible(BINANCE_DIRECTIONAL_EV, first_touch_triple_barrier_v1)  -> REFUSED
```

`model_consensus` refuses at the contract gate before EV is ever computed. This change
reopens a gate that closed by accident; the gate that closed **on purpose** still holds.

---

## Tests

```text
backend/test_core_scan_band_and_verdict.py    22 checks   6/6 mutation
```

One assertion in `test_scan5_barrier_and_gates.py` had to be retargeted: it quoted
`float(p.get("neutral_band") or self.neutral_band)` verbatim, so it broke when that `or` was
replaced. The invariant it guards — *fall back only when absent* — is unchanged and now
actually holds; the assertion was checking the expression rather than the rule, and is now
proven against `resolve_neutral_band` directly.

The test's own hostile fixture reproduced a known trap: an **empty** `dict` subclass is
falsy, so `p.get("setupQuality") or p.get("confluence") or {}` skips past it, `.get` is
never called, and the test passes proving nothing. The fixture is non-empty and asserts its
own truthiness before use.

---

## Where the three gates now stand

| gate | state |
|---|---|
| calibration provenance | fixed; blocked on new stamped rows accumulating, not on code |
| `trade_verdict` never TRADE | **fixed here** — closed by accident, now open |
| contract admissibility | **still refuses, correctly** — needs an endpoint head or a first-touch EV |

The third is the only remaining one, and it is the one that should hold.
