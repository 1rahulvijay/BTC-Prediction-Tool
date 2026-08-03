# PREREG B — FINAL_CROSSING_VS_REVERSION_V1

**Frozen `2026-08-02`, before the forward window opens.** Any edit invalidates every result
scored under it; the hash in `docs/active/PREREG_HASH.txt` is checked in CI.

## Question

After an anchor crossing, is it **final** or **temporary**?

## Why this target and not `P(anchor crossing)`

Measured (`RESEARCH_LEDGER` §4.4, §10.5): **57% of crossings revert**, and at T−4m only 50.8%
of rounds have already had their final crossing while the current leader still wins 68%.

So `anchor crossed` is **not** the same event as `position should switch`. Predicting that a
crossing will occur is close to worthless; predicting whether it will *stick* is the question
that maps to an action.

## Targets

```
label_first_cross_is_final     is the first crossing after this checkpoint also the last?
label_flip_reverts_5s/15s/30s/60s
```

Both already exist in `causal_checkpoint_labels_v1`, computed by
`backend/research_data/path_label_builder.py`, with reversion timed **from the first crossing**
rather than from the checkpoint.

## Permitted use — narrow, and this is the point

| permitted | forbidden |
|---|---|
| HOLD vs REDUCE vs EXIT on an existing position | automatic opposite-side entry |
| sizing down when reversion is likely | opening a new position on a flip signal |

An opposite-side entry needs the *opposite token's* executable economics, which this protocol
does not evaluate. A later preregistration may extend to it; this one may not be read as
authorising it.

## Required result

1. beats a fixed HOLD policy on realised net value per share
2. beats a matched-count random REDUCE/EXIT policy
3. positive day-block lower bound on `FORWARD_UNTOUCHED` rows
4. the reduction in adverse excursion is not achieved purely by trading less — coverage is
   reported beside the value

## Population

`FORWARD_UNTOUCHED` checkpoints where the leader is currently ahead and a position is held.
Same data gate as Preregistration A.

## Stopping rule

Scored once, when the data gate passes.
