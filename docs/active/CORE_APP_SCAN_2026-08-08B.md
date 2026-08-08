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

---

# Continued scan — the betting-accuracy column was not measuring the contract

Second pass. More automated sweeps came back clean: **un-awaited coroutines** (2 candidates,
both false positives — `create_task` and `asyncio.run` on the preceding line), **bare
`except:`** (none), **bounded buffers whose consumers demand more samples than they hold**
(none). One large defect remained.

## `lean_hit` — right by accident on half the rows, wrong on the other half

`database.py` states the rule in its own schema comment: *"betting-accuracy consumer must
use `lean_hit`, never `hit`"*. That column was computed as:

```python
lean_hit = (_raw_lean == "UP") == (actual_move_usd > 0)
```

`actual_move_usd` is `resolution_price - entry`. Under first touch:

```text
TOUCHING row   resolution_price is the BARRIER   -> sign IS the graded direction, correct by accident
TIMEOUT row    resolution_price is the last CLOSE -> sign is a small residual drift
```

On a timeout the contract graded the row **NEUTRAL** — no barrier was reached, the bet did
not win — and the rule credited the lean as correct whenever that residual drift happened to
agree with it. So the column was neither the endpoint question nor the first-touch question,
but a mixture: barrier sign on some rows, drift on others.

**Timeouts are 46.6% of 5m rows** (measured at boot: DOWN 23,009 / NEUTRAL 40,206 / UP
23,110). Simulated on that distribution with a zero-skill model:

```text
old rule (move sign)   0.501      a coin flip
contract               0.268
gap                   +0.233 of all rows
```

The graded direction was in scope the whole time, produced by the same `grade()` call as the
price. It is now what decides.

## Correcting the metric without its thresholds would have been defect 5.21 again

Three consumers take **0.5 as no-skill, explicitly**:

```text
CASCADE_MIN_ACCURACY = 0.62
bias_strength = (recent_accuracy - 0.5) * 0.6
needs_retrain: trend == "degrading" and lean_accuracy < 0.45
```

The corrected all-rows rate cannot exceed ~0.534 at 5m and sits near 0.27 for a zero-skill
model. Feeding it to those constants would have made the cascade **permanently inert** and
latched `needs_retrain` **permanently on** — a bound derived from one quantity enforced
against another, which is exactly the defect fixed earlier this session.

So there are two named quantities now:

| metric | over | no-skill point |
|---|---|---|
| `lean_accuracy` | every directional lean; NEUTRAL counts as a miss | ~0.27 at 5m |
| `lean_decisive_accuracy` | rows the contract actually decided | 0.5 |

The cascade gate, its sample floor, the retrain trigger **and the trend series it is compared
against** all read the decisive one. An absent decisive rate never triggers a retrain: 40
timeouts is not evidence a model is broken.

## The stored column and the restore path agree

The boot backfill used the same endpoint rule and now grades from `actual_direction`, and
only where that outcome was recorded — a row that cannot say what the contract decided is
left NULL, unknown and excluded, rather than assigned an answer by a rule the contract does
not use. The in-memory fallback for older rows was corrected the same way.

## Tests

```text
backend/test_lean_hit_contract_truth.py    17 checks   7/7 mutation
```

The source-extraction helper needed hardening **twice**, both times the same error — the
test matching the fix's own description of the code it removed. `ast.get_docstring` returns
*cleaned* text, so subtracting it from raw source removes nothing; and
`ast.get_source_segment` per statement still carries every comment nested inside a compound
statement, and the comment above a fix always quotes the line it replaced. The helper now
uses `ast.unparse`, which emits code and nothing else.

---

# Third pass — "historical EV" was accuracy wearing a dollar sign

Sweeps clean this round: **unit mixing** (`_bps` compared against a non-bps name — 2 hits,
both genuine bps-vs-bps), **float equality on computed quantities** (all in test files),
**mutation of a container during iteration** (none). Two defects found.

## `expectancy_usd` — the 5.5/5.6/5.7 family, in a consumer that was named and not converted

```python
gross_profit = sum(abs(v.get("actual_move_usd", 0.0)) for v in dir_preds if v["hit"])
gross_loss   = sum(abs(v.get("actual_move_usd", 0.0)) for v in dir_preds if not v["hit"])
expectancy_usd = (gross_profit - gross_loss) / dir_total
```

`actual_move_usd` is `resolution_price - entry`, and under first touch `resolution_price` is
the **barrier**. Its distance is `entry * threshold` — **identical on every touching row**.
So the statistic collapses:

```text
accuracy 30%  ->  -$32.00   = barrier x (2*acc - 1)
accuracy 50%  ->    $0.00
accuracy 70%  ->  +$32.00
```

A linear rescaling of accuracy, displayed as *"historical EV"*. On timeout rows the same
expression instead used the closing residual, so it was not even one quantity consistently.

`prediction_verifier.py:334` — a comment written during the 5.5/5.6/5.7 work — **names this
consumer**: *"the forward-EV ledger and the live gate's `expectancy_usd`, which the UI calls
'historical EV'"*. The ledger was converted; this was not.

It now averages the **endpoint move signed by the side actually served**, which is a realised
per-trade return. The difference is not cosmetic:

```text
70% accurate, barrier +-80, endpoint +3 when right / -25 when wrong
  old formula      +$32.00 per trade
  endpoint truth    -$5.40 per trade      the same model is LOSING money
```

`expectancy_basis` and `expectancy_n` are carried beside it. Where no row has an endpoint
observation the value is **None**, not 0.0 — reporting the barrier-derived number would be
reporting accuracy in dollars again.

### The consumer had to be fixed in the same change

```python
expectancy = float(acc.get("expectancy_usd", 0.0) or 0.0)   # float(None or 0.0) -> 0.0
if expectancy <= 0: threshold += 0.03
```

`None` would have coerced to `0.0`, tripped the `<= 0` branch, and raised the safety bar
citing a **negative EV that was never measured**. Unmeasurable and negative now take separate
branches with separate messages, so a raised bar can always be traced to evidence.

## The venue "median" was the upper middle

```python
consensus = sorted(valid)[len(valid) // 2]   # median
```

On an even count that is the upper of the two middles. With two venues reporting it returned
the **higher price** and called it a consensus, biasing every per-venue `deviation_bps` and
the `lead_venue` pick. Venues drop in and out on staleness, so even counts are routine.

Display-only — nothing in the feature matrix or the decision path reads it — but it is
labelled `# median` and now is one.

## Tests

```text
backend/test_expectancy_and_consensus.py    17 checks   5/5 mutation
```

The median test initially **reimplemented** the median rather than calling
`build_exchanges_block` — the same "test measures itself" error caught twice already this
session. It now feeds crafted venue prices through the shipped function and reads its output.
