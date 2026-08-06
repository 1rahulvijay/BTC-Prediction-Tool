# Settlement vs path: the head split

The deepest defect both audits identified, and the one that made every downstream economic
number suspect.

---

## The problem

`build_sequences` labelled a **triple barrier**: UP if the upper barrier is touched *first*.
Polymarket resolves on **where price ends** relative to the strike. The Binance
`model_consensus` EV is `(2p − 1) × move − costs`, which treats `p` as the probability the
**endpoint** lands on the predicted side.

So a first-touch probability was pricing endpoint questions. Measured on random walks, the two
contracts **disagree 24.9% of the time** — a quarter of paths, not a corner case.

Nothing about the value revealed the substitution. Both are floats in `[0, 1]`. Both look like
probabilities. Only the *question they answer* differs, and that was never carried anywhere.

---

## What was built

### 1. Both heads are now labelled

`build_sequences(..., return_settlement_labels=True)` returns `Ysettle` alongside `Y`:

```
Y        first-touch triple barrier   "which barrier is hit first"
Ysettle  endpoint settlement          "where does price END"
```

On one test row where price touches the upper barrier then reverses: **PATH says UP,
SETTLEMENT says DOWN.** Same row, same features, different training target — which is the
point. Without this, "use a settlement probability" was advice with no way to follow it.

Every settlement row is one-hot: endpoint direction has no ambiguous case, unlike first-touch
where a single bar can touch both barriers.

### 2. Contracts are grouped by the question they answer

```python
PATH_CONTRACTS        = {FIRST_TOUCH_TRIPLE_BARRIER_V1}
SETTLEMENT_CONTRACTS        = {ENDPOINT_SETTLEMENT_V1}
BINARY_SETTLEMENT_CONTRACTS = {POLYMARKET_BINARY_SETTLEMENT_V1}
```

Pairwise disjoint, and asserted to be.

**Update 2026-08-05.** A third contract was added. `ENDPOINT_SETTLEMENT_V1` answers "where does
price end" using an *adaptive volatility band*, and that band does not exist on the venue -
Polymarket resolves on a strict comparison with two outcomes. Measured on real `build_sequences`
rows the two settlement contracts disagree on **65.2%** of endpoints; every one of those is a
real payout the banded contract calls NEUTRAL. The banded contract is kept for the Binance perp
lane, where the band is the region a trade does not clear its costs.

The venue's tie rule is named once, as `TIE_RESOLVES_TO = DOWN` ("Up" requires strictly greater),
and pinned by a selftest so changing it is deliberate rather than a refactor of a bare `>`.

### 3. Consumers declare their purpose and are refused on mismatch

```python
POLYMARKET_SETTLEMENT_EV  -> BINARY_SETTLEMENT_CONTRACTS
POLYMARKET_HOLD_EXIT_EV   -> BINARY_SETTLEMENT_CONTRACTS
BINANCE_DIRECTIONAL_EV    -> SETTLEMENT_CONTRACTS
STOP_TARGET_PLANNING      -> PATH_CONTRACTS
PATH_STOP_MANAGEMENT      -> PATH_CONTRACTS
PATH_EXCURSION_FORECAST   -> PATH_CONTRACTS
```

`HOLD_EXIT_DECISION` is **gone**. One name covered two different questions - "will my stop be
hit before my target" (path) and "is holding to settlement worth more than selling at the
current bid" (settlement, and on Polymarket a binary one) - so it had to require one contract
for both, and answering the second with a first-touch probability was the very substitution
this layer exists to refuse. It was happening *inside a purpose name broad enough to hide it*.
Splitting the name is what lets the guard see it.

The live table is generated from the source in `docs/active/CURRENT_STATE.md`; this section is
narrative and can drift, that one cannot.

`assert_admissible(purpose, contract)` **raises** `ContractMisuse` rather than returning a
boolean, so it cannot be ignored by a caller that only checks a truthy value.

The guard runs **both ways**: a settlement probability is refused for stop/target planning too.
It is not simply refusing everything.

An **undeclared purpose** is also refused — an unchecked consumer is how the mismatch happened
in the first place.

### 4. Predictions carry their contract

`server.py` stamps `p["targetContract"]` where it stamps `calibratedConfidence`, and
`model_consensus` calls `assert_admissible(BINANCE_DIRECTIONAL_EV, ...)` before using the
probability. A mismatch returns `NO_EDGE` with reason `target_contract_inadmissible`, recording
the offending contract in the decision features.

An **unlabelled** probability is refused too, and with a *different message* from an unknown
one — "no contract was declared" and "that contract name is unrecognised" need different fixes.

---

## The operational consequence, stated plainly

`TRAINING_CONTRACT` is still `FIRST_TOUCH_TRIPLE_BARRIER_V1`. So:

> **`model_consensus` now refuses every live prediction**, because the only head that exists
> answers a path question and the EV it computes is an endpoint question.

That is the honest state of the mismatch made operational. Before this change the strategy
consumed the path probability silently and produced EV numbers that were not measuring what
their name claimed. Now it abstains until a settlement head is trained.

This is pinned as a test — `test_binance_model_consensus_refuses_a_path_probability` asserts
that the *current* training contract is inadmissible for an endpoint EV — so the refusal cannot
be quietly undone by relaxing the requirement instead of training the head.

---

## What is NOT done

- **No settlement head has been trained.** The labels exist and the contract is enforced; the
  training run that produces a `ENDPOINT_SETTLEMENT_V1` artifact has not happened. Until it
  does, the settlement lane is blocked by design rather than by accident.
- **The path head is not retired.** It answers real questions — stop/target planning,
  hold/exit, excursion forecasting — and remains admissible for exactly those.
- **Polymarket pricing is not wired to any head.** `POLYMARKET_SETTLEMENT_EV` is a declared
  purpose with no consumer yet.

---

## Testing

```
target_contract          --selftest          31 checks
test_target_contract_parity                  18 checks (incl. one row -> two labels)
tests/test_model_driven_paper_strategies     10 passed
mutation suite                               6/6 CAUGHT
```

Mutations caught: settlement EV accepting a path contract; Binance EV accepting one; an
unlabelled contract passing; an undeclared purpose passing unchecked; the consumer swapping to
a path purpose; and settlement labels copying the path label.

One mutation was initially an **equivalent mutant** — removing the `contract is None` branch
still raised via the unknown-contract check. Rather than call it caught, the selftest now pins
that the two failures produce *different diagnostics*, which is the real product of that branch.
