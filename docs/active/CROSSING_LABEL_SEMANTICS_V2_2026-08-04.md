# Crossing label semantics — v1 → v2

**`2026-08-04`.** `label_version = crossing_labels_v2`. Commit `7545674`.

## The defect

`reverted_Ns` never measured reversion. It computed:

```python
window = [s for s in after if int(s["ts"]) <= deadline]
side = window[-1]["current_position"]          # LAST sample at or before the deadline
labels[f"reverted_{horizon}s"] = (side == event["from_side"])
```

That is **state at the horizon**. A path that crossed, reverted at 5s and re-crossed by 20s was
labelled `False` — "did not revert" — despite having reverted within five seconds.

Verified on exactly that path:

| field | value |
|---|---|
| `reverted_30s` | `None` (v1 column, NULL under v2) |
| `state_original_side_at_30s` | **`False`** — what v1 actually measured |
| `ever_reverted_by_30s` | **`True`** — what the name always claimed |
| `first_reversion_ts` | `5000` |
| `n_recrossings` | `1` |

The two disagree, which is the proof they are different targets rather than a cosmetic rename.

## Nothing was reinterpreted

Columns were **added, not renamed in place.** Renaming would have silently changed the meaning of
15,428 existing rows — the exact failure mode being eliminated. Confirmed against the live
database after migration:

```text
rows total                  15,428
label_version = v1          15,428   untouched
reverted_30s populated      14,844
state_original_side_at_30s       0   new column, NULL for v1 rows
COALESCE resolves           14,844   what the trainer reads
```

## The migration was the part that would have broken

`CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so a v1 database has
none of the v2 columns and any query naming one **fails to bind**. `_migrate_v2_columns` adds
them as NULL, additively — no existing row is read, rewritten or reinterpreted.

This was caught by checking the columns existed *before* trusting the query, not after.

## `ever_reverted_by_Ns` is deliberately not trained

An assertion enforces it. The target has no v1 equivalent, so it exists only for rows recorded
after this change. Training on it would change what the head predicts partway through the sample,
and backfilling it from v1 rows would be inventing data.

It is recorded now so a future protocol can use it once enough rows exist.

## The AUC reproduces exactly

Which is the proof the rename is semantic only — same 14,844 rows, same measurement:

| target | AUC | clock baseline | gain | CI |
|---|---:|---:|---:|---|
| `state_original_side_at_30s` | 0.6715 | 0.5196 | +0.1519 | [+0.1175, +0.1840] |
| `state_original_side_at_60s` | 0.6373 | 0.5061 | +0.1312 | [+0.1121, +0.1517] |
| `is_final_crossing` | 0.7144 | 0.6755 | +0.0389 | [+0.0243, +0.0556] |

Train/test splits, base rates and null floors also match. Any movement would have meant the
`COALESCE` was selecting different rows.

## Why this is not the same defect as the vacuum failure

`LIQUIDITY_VACUUM_CONTINUATION_V1` **inflated** its number: it admitted anchors whose sign was
undefined, so uninformative events were counted as informative. A selection rule admitting things
it should not.

This is the opposite. The rule is well-defined, applied identically to train and test, and it
**fails closed** — `if not window: continue` writes no label rather than reaching for a stale
sample. The evidence is in the data itself:

```text
reverted_5s     n = 0        unresolvable at ~15s cadence
reverted_15s    n = 6
reverted_30s    n = 14,844
reverted_60s    n = 14,273
```

Zero labels where zero are knowable is the behaviour whose absence caused the vacuum failure. A
misnamed but consistently applied target does not inflate AUC. **The 0.6715 is real; it answers a
narrower question than the old name advertised.**

## Still open

`is_final_crossing` remains *final among 15-second observations*. A cross-and-recross between
snapshots is invisible, so the honest name is `is_final_observed_crossing_at_snapshot_cadence`.

Settling it needs the 15s-vs-1s disagreement audit, which is **not yet answerable**: the HF
recorder has produced **2 crossings from 2.5 minutes** of total runtime, against 15,428 from the
15s recorder. The audit's current output is "run the HF recorder long enough to produce a
comparable sample."
