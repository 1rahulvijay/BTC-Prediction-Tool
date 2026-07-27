# CLARIFICATION 001 — `COMPLETE_TRADE_M0_V2`

Completes the decision procedure the V2 preregistration specifies in outcome terms but not in
executable terms. **Declared before any threshold artifact exists, before any forward evidence has
been collected, and before any V2 score has been computed.** The original protocol file is
untouched and its hash is unchanged.

```
Declared:                        2026-07-26
Threshold artifact exists:       no
Forward evidence rows:           0
V2 results available:            no
Any V2 score computed:           no

Original preregistration:
    unchanged
    sha256 138616d3893c5034bddd29be562f73c452e16f570af67ffcb1adda209df793a5
```

## Why this is required

The frozen V2 text describes the primary comparison in bucket terms inherited from the earlier
protocols — a top score bucket against a matched-random control, five buckets, a
chance-monotonicity control. The evaluator that was built applies a single calibration-derived
threshold and a first-qualifying causal entry rule instead.

Those are not the same procedure, and the honest options were to change the evaluator, or to
declare the procedure and hash it. **Silently claiming the evaluator executes the existing frozen
document was not an option**, so this record states exactly what will run.

Bucketing and thresholding answer different questions. A five-bucket table asks *does score
ordering track economics*. An absolute threshold asks *does this deployable rule make money*.
Only the second is executable live, because a live system has no access to the score distribution
it would need to form buckets.

## Binding decision procedure

```
entry threshold derivation   quantile of the calibration-period score distribution at the
                             target entry rate; computed ONLY on calibration data, frozen into
                             an immutable ThresholdArtifact before evidence collection begins,
                             and never recomputed by the evaluator

target entry rate            0.20

causal checkpoint rule       walk a round's checkpoints earliest -> latest (largest seconds_left
                             first); take the FIRST candidate whose score >= the frozen
                             threshold; a round with no qualifying checkpoint is NO_TRADE

candidate gates              candidate_valid == 1, entry_complete == 1, all FEATURE_COLUMNS
                             present and finite, conservative capacity (q10) >= requested size

independent unit             one trade per round_id

primary metric               plan_take_3c_or_stop_3c_net of the selected trade

bucket role                  DIAGNOSTIC ONLY. Score buckets and any monotonicity observation may
                             be reported for interpretation. They carry NO promotion authority
                             and no monotonicity claim may be cited as evidence.
```

## Matched-random construction

```
pool          the OTHER candidates available at the SAME causal checkpoint as the selected
              trade - same round, same instant, same information set

excluded      candidates from later checkpoints. Including them would hand the random control
              opportunities that did not exist when the decision was made, which inverts the
              comparison in the control's favour and makes the selected policy look worse than
              it is - a bias, even though it is the conservative direction

statistic     mean realized net PnL of the selected trades minus the mean of one randomly drawn
              candidate per round, over 2,000 draws

p-value       (1 + #{random_mean >= selected_mean}) / (draws + 1)
```

## Multiplicity family

```
declared family   every p-value the evaluator computes in a scoring run
procedure         Benjamini-Hochberg, q <= 0.10
```

**A family of one is not a correction.** If the run computes a single p-value, BH is recorded as
applied but must be reported as a family of size 1 with no multiplicity protection. The family
size is stated with every result.

## Minimum counts

```
minimum independent rounds observed    1,000
minimum calendar weeks OCCUPIED         8
minimum selected trades                 100
resolved outcomes                       ALL selected trades; a partial resolution is NOT_READY
```

## What is NOT changed

Every promotion threshold in the frozen V2 text stands unchanged: day-block lower bound > 0,
profit factor > 1.20, matched-random control mandatory, hour and week profit-share caps at 50%,
1000ms latency survival, forward-only evidence post-dating every freeze.

## Binding condition

Revising any element of this record after a V2 score is computed invalidates the experiment,
exactly as editing the preregistration would. If the procedure proves unworkable the experiment is
abandoned and archived, not amended.

## Audit chain

```
PREREG_COMPLETE_TRADE_M0_V2.md                    138616d3893c...   (frozen, unchanged)
PREREG_COMPLETE_TRADE_M0_V2_CLARIFICATION_001.md  <this file, hashed in PREREG_HASH.txt>
```
