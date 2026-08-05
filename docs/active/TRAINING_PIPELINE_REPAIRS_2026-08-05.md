# Training-pipeline repairs — 2026-08-05

Companion to [`TRUTH_LAYER_REMEDIATION_2026-08-05.md`](TRUTH_LAYER_REMEDIATION_2026-08-05.md),
which covers the grading/decision layer. This one covers the **training** layer plus the
execution and provenance fixes that were previously recorded only in commit messages.

Commit messages are durable but not discoverable: nobody reading `docs/active/` finds them.

---

## The pattern, again

Every item here is the same shape as the truth-layer set:

> **A check that passes while the property it guarantees is false**, or a value that looks like
> an observation and is not.

Three of them were defects in *my own* fixes from earlier in the same session.

---

## Execution and evidence

### Fill engine — a defaulted gate is a disabled gate (`fd46d51`)

Every eligibility gate in the shared executable fill engine read itself with a permissive
default:

```text
max_book_staleness_s -> 1e9      (~31 years of staleness allowed)
min_ask / max_ask    -> 0.0/1.0  (the entire probability range)
max_spread           -> 1.0      (any spread whatsoever)
min_top_ask_size     -> 0.0      (any size, including none)
```

A missing or misspelled key did not raise — it **deleted that gate**, and the run then reported
more fills at better prices. Every default failed toward flattering the result, in the engine
whose output decides whether a strategy is profitable.

Gates are now required. The distinction preserved: `build_complete_trade_dataset` sets
`max_spread: 1.0` *on purpose*; an explicit 1.0 is a decision, an absent key that became 1.0 was
an accident, and in the output the two were identical.

**Two findings inside its own selftest.** It called `simulate_trade` with no eligibility at all
— validating the engine in a gates-disabled mode production never runs — and its one
"vetoes fail closed" check passed `{"max_ask": 0.97}` alone, asserting one veto while silently
disabling four. The engine's selftest was also in **no CI job**.

### Backtest neutral band came from a fabricated range (`b09df6e`)

```python
highs = closes * 1.001   # "Approximate - real highs not available here"
```

Every bar got an identical 0.2% range, so ATR became a constant multiple of price and the
ATR-derived neutral band stopped varying with volatility. That band decides UP/DOWN/NEUTRAL, so
every hit rate was graded against a barrier matching no real market condition.

```text
real band       widens 3.6x from calm to violent
fabricated band moves  1.00x
violent-half band: fabricated 0.0008   real 0.0029
```

A 0.2% move is NEUTRAL under the real band and DIRECTIONAL under the fabricated one. **The
comment was also false** — both callers build `closes` from kline dicts carrying `high`/`low`
and simply never passed them.

### Oracle release freeze — pinned vs scheduled-refit (`8dccc26`)

The freeze reported five DRIFTED artifacts and had been failing continuously. It was not
detecting drift; it was watching `auto_finetune.py` run at 04:00 nightly and rewrite exactly
the five cheap heads it owns. Corroborated by the producer: the job's own log prints
`sha256=1f0488f68153f6cc` for `round_state_heads.pkl` and the freeze records the same hash.

Re-freezing would have gone green and failed again the next morning. Byte-immutability is the
wrong contract for an artifact **designed** to be refit, so the freeze now separates **PINNED
(20)** from **SCHEDULED_REFIT (5)**, importing the exempt set from the job that causes the
rewrites. Every refit is appended to `refit_history.jsonl` with what it *was* frozen as —
muting a check without recording what it would have reported is just deleting the check.

---

## Model-bundle isolation

### Training a challenger repartitioned the incumbent (`80d577f`, P0-1)

`train_model()` called `regime_engine.fit_hmm(...)` on the **module-global** engine the serving
loop reads for every prediction. The moment a retrain began, the incumbent's live regime routing
switched to a candidate's parameters — before training finished, before gating, before
promotion. **A challenger that was ultimately rejected still permanently repartitioned the model
that kept serving.**

Separately, `state_dict()`/`load_state_dict()` existed and were tested but had **no production
caller**, so every restart came up `hmm_ready=False` and served HMM-trained experts through the
heuristic fallback — which raises nothing, because the fallback answers every call.

Now: training fits a local candidate engine, its parameters ride with the bundle, and one
installer is the only path into the live engine — called at boot and all three promotion swaps.

### Promotion evaluated an expert mix that never serves (`80d577f`, P0-8)

`_predict_probabilities` passed `None` as `data_state` for every holdout row.
`_get_regime_from_state(None)` returns `"RANGE"`, so the entire holdout ran through the RANGE
experts. `regime_labels` was already computed, already aligned 1:1 with `X`, and already in
scope — it just wasn't passed.

Made unambiguous by a model voting UP in TREND and DOWN elsewhere against an all-UP holdout:
RANGE-default scores **0.0**, regime-routed scores **1.0**.

---

## OOF / serving parity (`e9a394f`, `376ba87`)

The stacker was trained on replicas that differed from the seats it is served.

| mismatch | measured |
|---|---|
| calibration stripped (`.estimator` on a `CalibratedClassifierCV`) | 0.132 mean probability gap |
| sample weights discarded into `_` | 0.157 mean probability gap |
| purge gap `min(required, len//8)` | under-purged exactly where samples are thin |
| TCN fitted at `epochs*0.5` | the stacker trusted a weaker model than it is served |
| dynamic weights = raw accuracy | see below |

**Raw accuracy paid for abstaining.** On a 70% NEUTRAL bucket:

```text
accuracy   abstainer 0.695   informed 0.695   <- cannot tell them apart
skill      abstainer ~0      informed 0.184   <- weight now follows the information
```

Replaced with log-loss skill against the class prior, clipped at 0 so a seat worse than the
prior contributes nothing. Raw accuracy retained under its own name for the panels.

**A leak in my own repair.** Slicing `sw_stack[tr_idx]` into a fold is the obvious fix and is
wrong: the global weights reference `split_idx` — the end of the whole slice — through both
recency and regime similarity, so for an early fold that point is in its own future. It would
have swapped an unweighted fit for a *future-referenced* one, subtler and harder to see. Recency
and class balance are now rebuilt against the fold's own last training row; ambiguity exclusion
carries across as a mask because it is row-local.

**One audit claim not implemented, deliberately.** Missing class-presence augmentation was
called a parity defect. It isn't: production adds up to three *tiny-noise* synthetic rows purely
so multiclass estimators accept a non-contiguous label set, and excluding them from OOF is
correct — injecting them into the stacker's inputs would be worse. The consequence (a 2-class
fold pads the absent column with exactly `0.0` while production emits a small positive) is
**counted and logged** instead.

---

## Historical leakage (`6b0bb1a`)

```python
return np.full(n, float(snapshot_val or 0.0))   # today, painted across history
```

When per-candle history was absent, the current order-flow value was written into **every**
historical row. The docstring called it *"inert during training"* — it isn't; a constant column
still forms interactions, still shifts split points, and still differs between train and serve
in a way no schema hash can see.

**Why the fix had to be in `features.py`:** the serving path already overlaid only the final row
(P0-3), but when it couldn't it *popped* the key — which sent control back into this fallback
and produced the broadcast it was avoiding. Its own variable for those keys was named
`_broadcast`.

Now: history present → used unchanged; history absent → `0.0` across history, snapshot on the
**final row only**, key recorded as degraded. Measured: changing the snapshot leaves every
historical row byte-identical (`max delta 0.000e+00`).

`0.0` rather than `NaN` because the tree seats accept NaN but the logistic and TCN seats do not
— a NaN would silently drop those *seats* instead of the feature.

---

## Provenance (`154cccf`)

`check_feature_contract` reported **0 STALE, 12 UNKNOWN**. The failure was never "the models are
stale" — the two halves of the contract had never been introduced. `verdict_for` demanded nine
keys; `artifact_identity` wrote none under those names and four did not exist at all
(`feature_semantics_version`, `training_semantics_version`, `training_cutoff`, `code_dirty`).

**A retrain could not have fixed this.** The rebuilt bundle would have written the same
unreadable manifest and the gate would have stayed red forever. Every instruction to "retrain to
clear the VWAP failure" — including in my own gate document — was unachievable as written.

### The VWAP difference, measured

On the real 518,400-bar matrix, v1 cumulative-from-bar-0 vs the current trailing window, on
`vwap_deviation`:

```text
v1 cumulative   std 1464.9 bps   range [-6618.2,  885.1]
current         std  109.8 bps   range [-1244.6,  874.1]
correlation     +0.0605          std ratio 13.3x
```

Essentially uncorrelated — a v1-trained model learned a **different variable wearing the same
name**. A synthetic 3,000-bar walk gave `r=+0.95`; a cumulative anchor needs a long real history
to drift, so the synthetic fixture would have understated this catastrophically.

---

## Geometry endpoint head (`cadd2a6`, `01aba03`)

Wired as **display and record only**, `AUTHORITY = "NONE"`, enforced by a frozen dataclass and an
AST import test proving no decision, sizing or order module imports it — a keyword grep would
have missed `score`, `edge`, `rank`, `eligible`.

Renamed from `conditional_path_head`: it estimates `P(settlement > anchor)`, an **endpoint**
question, in a repository whose central defect class is first-touch-vs-endpoint confusion.
Emitting several checkpoint probabilities does not make it a path forecast.

**A causality bug found by taking the audit seriously:** the forming-bar guard was
`if closed and closed is kl:` — dead code, because a comprehension always allocates. With a feed
omitting `is_closed`, the forming bar entered sigma.

Units are pinned *and* asserted numerically: feeding seconds where minutes are expected changes
`z` by exactly `7.746` (`√60`).

---

## Test errors found by running the tests

Recorded because they are the same defect class as the code they guard.

| error | consequence |
|---|---|
| `[0:0] == []` tautology | a check that could never fail |
| 6000-byte scan window | truncated before the key it was written to catch |
| first-touch fixture at bar 0 | "index 0", "first bar" and "the touching bar" coincided |
| skill fixture was an oracle (1.000 accuracy) | proved nothing about the metric being replaced |
| substring matched a variable name | survived the mutation that deleted the counting |
| substring matched the fix's own docstring | **three separate times**; now stripped via AST |
| guessed probe value outside both bands | classified identically by both, proving nothing |
| median demo on three venues | duplicate landed on the same median |

---

## Status

```text
local CI                171 OK / 144 checks
1 FAIL                  check_feature_contract - no artifact trained under the repaired
                        contract yet; clears on the first clean-tree retrain
promotable strategies   0
real-money authority    NONE
```

Gate: see [`PRE_RETRAIN_GATE_2026-08-05.md`](PRE_RETRAIN_GATE_2026-08-05.md). **4.4, the
settlement head, is the only remaining hard stop.**
