# Crossing heads as an application artifact

**Built** `2026-08-03` · **Trainer/server** `backend/train_crossing_heads.py` ·
**Registry** `crossing_heads` · **Bundle** `data/saved_models/crossing_heads_bundles/`

Turns the `CROSSING_HEADS_V1` measurement into something the application can actually load,
with full provenance, rather than a number in a document.

## It is published and it loads

```
trained on 15,428 crossings
  is_final_crossing   15,428 rows   base rate 37.2%
  reverted_30s        14,844 rows   base rate 18.7%
  reverted_60s        14,273 rows   base rate 30.1%
```

A live prediction, from the published bundle:

```
status               OK
authority            NONE
protocol             762532c9c0d5
is_final_crossing    0.599
reverted_30s         0.182
reverted_60s         0.407

missing-feature case -> MODEL_UNAVAILABLE
```

**This is the first artifact in the repository that carries complete provenance and loads
through the real verification path.** The standing count is 0 of 25 serviceable; those 25 are
untouched, and this is a 26th that was built to the contract rather than retrofitted to it.

## Authority is zero, and it is declared in the registry

```python
ModelRegistryEntry("crossing_heads", "crossing_heads.pkl", "crossing_probabilities",
                   "train_crossing_heads",
                   notes="...No authority: an input to a decision, never a decision...")
```

`may_price`, `may_rank`, `may_size` and `required_for_serving` are all `False` — the defaults,
left deliberately. The head is loadable and may inform a display or a later study. It may not
price, rank or size anything.

Authority lives in that table rather than at the call site, so **granting it later is a visible
one-line edit to a reviewed file**, not an emergent property of whoever calls `predict()`.

Every prediction echoes `authority: NONE` in its own payload, so a consumer cannot read the
number without also reading what it is allowed to do.

## Why zero authority, concretely

A crossing probability is an input to a decision. Every action lane measured in this repository
is closed on cost:

```
Polymarket taker   ~149 bps floor; 0.1% of 15m windows move that far
Binance taker        14 bps against a 0.97-1.97 bps gross edge
Binance maker         2 bps round trip; fills lose 1.53 bps to adverse selection
```

AUC 0.67 on reversion does not create an opportunity. It would have to improve the post-cost
value of a *specific affordable action*, and none is currently affordable.

## Fail-closed, three ways

```
no verifying bundle      -> MODEL_UNAVAILABLE, with the path it looked in
schema/target/protocol   -> MODEL_UNAVAILABLE, naming which hash mismatched
hash mismatch
a missing feature        -> MODEL_UNAVAILABLE, naming the missing columns
```

The last one matters most. Substituting `0.0` for an absent feature would return a confident
probability answering a **different question** — the defect class that produced 0/25 serviceable
artifacts and a readiness report that printed a healthy status while unable to read its source.

Verification order is also enforced: `load_verified` checks the bundle **before** the pickle is
opened, so bytes are never deserialized until they have been verified.

## Provenance

All eight `REQUIRED_PROVENANCE` fields are populated — a bundle missing any is refused by
`model_artifacts`, not accepted with a warning:

```
feature_schema_hash          sha256 of the 14 frozen feature names
feature_semantics_version    from features.FEATURE_SEMANTICS_VERSION
training_semantics_version   from model.TRAINING_SEMANTICS_VERSION
dataset_sha256               sha256 of polymarket_crossings.duckdb
training_cutoff              last crossing timestamp in training
target_contract_hash         sha256 of the three head targets
protocol_hash                762532c9... - the sealed CROSSING_HEADS_V1
source_commit                git HEAD at training time
```

Binding `protocol_hash` into the artifact means a head trained under a different protocol cannot
be loaded by this server: `load()` refuses on mismatch. The measurement and the artifact cannot
drift apart silently.

## Serving uses the same causal rule it was scored under

Market features are joined from the **last 1-minute bar that had CLOSED** before the crossing.
The selftest asserts this in the serving module, not only in the research script — feeding the
containing bar at serving time would hand the head a different feature than it was trained on,
which is the defect fixed earlier in `train_round_state_heads`.

## Not wired into the serving path

Nothing calls `predict()` from `price_to_beat.py` or the API. The head is registered, published,
loadable and inert.

Wiring it in is a separate decision, and a cheap one to make later precisely because the
authority flags already say what it is allowed to do.

## Limits

- **30s and 60s only.** The 5s and 15s horizons are absent — `round_state_snapshots` samples
  every ~15 seconds. `crossing_recorder_hf` resolves them at 1s cadence and needs about three
  weeks of running to reach comparable volume.
- **Not calibrated.** AUC is discrimination. Using these probabilities in an expected-value
  calculation needs calibration, which `CROSSING_HEADS_V1` did not measure and this artifact
  does not add.
- **Trained on the full 21 days**, unlike the protocol run which held out 7 test days. The
  scored result is the honest one; this artifact is the deployable one, and they are not the
  same object. Retraining on all data is standard, and it means the artifact's own numbers are
  in-sample and are not quoted as evidence anywhere.
- The anchor is derived from the app's own round state, so it inherits whatever the round
  recorder's anchor convention is.

Selftest: 12 checks, including that every authority flag is False, that a missing feature is
refused rather than defaulted, and that serving reproduces the protocol's causal bar rule.
