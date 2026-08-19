# ROUND_TO_ROUND_TRANSFER_V1

Date: 2026-08-14
Runner: `run.py` · Raw: `results.json`
Question: does a settled Polymarket round predict the next one?

## Verdict

**No rule clears the cost hurdle. 0 of 24.** The single rule that appeared to was a
multiple-comparisons artifact, and it is instructive enough to be the main content below.

This is lane 24 with no positive lower bound.

## The lane was queued on a premise that was half wrong

It was picked as the best-powered remaining lane on the reasoning that it needs only settled
rounds, and settlements span more dates than quote snapshots. Measured:

```
settled rounds with a DIRECTION   3,336  across 19 UTC days   <- real gain
settled rounds with PRICES        1,053  across 10 UTC days   <- same hole as quotes
```

`anchor_price` and `expiry_btc` are NULL for **2,283 of 3,336** settled rounds, and the 1,053 that
have them are exactly the snapshotted rounds on exactly the same 11 dates. The settlement table
does not carry independent price history.

So direction rules (momentum, reversion, run length) get 19 days; margin rules get 10. The runner
keeps the two families separate rather than intersecting everything down to the smaller sample —
which is what the first version did, throwing away 69% of the direction data for no reason.

## Setup

Adjacency is clean: **2,470 of 2,494** 5m rounds start exactly one horizon after the previous one,
and each anchor chains to the prior expiry within ~$4. This is genuine round-boundary
autocorrelation, not an overlapping-window artifact.

| horizon | adjacent pairs | UTC days | P(UP) | mean abs margin |
|---|---|---|---|---|
| 5m | 2,470 | 19 | 0.4944 | 4.78 bps |
| 15m | 821 | 18 | 0.4869 | 9.53 bps |

**The hurdle is 0.5235, not 0.50.** A binary contract bought at ask `a` needs win rate
`a + 0.07·a·(1−a)`. At 0.50 plus half the observed 1.21c spread, that is 52.35%. A rule that is
51% accurate is statistically interesting and economically worthless.

## Headline results

| rule | n | days | accuracy | LCB95 | vs hurdle |
|---|---|---|---|---|---|
| 5m base rate: always DOWN | 2,470 | 19 | 0.5069 | 0.4881 | −3.55 pp |
| 5m momentum | 2,470 | 19 | 0.4984 | 0.4793 | −4.42 pp |
| 5m reversion | 2,470 | 19 | 0.5016 | 0.4820 | −4.15 pp |
| 15m reversion | 821 | 18 | 0.5104 | 0.4707 | −5.28 pp |
| 5m reversion on last settled 15m | 2,428 | 18 | 0.5148 | 0.4946 | −2.89 pp |

Round-to-round direction is indistinguishable from a coin flip at both horizons, in both
directions, and across horizons. The closest any rule comes is 2.89 percentage points short.

## The artifact, and why it mattered

The first run — before the sample split was fixed — produced this:

```
5m reversion after run>=3 :  n=163, accuracy 0.6196, LCB 0.5466  vs hurdle 0.5235  -> CLEARS
```

A rule clearing the hurdle by 2.3 points. Two independent checks killed it.

**1. More data destroyed it.** The 163-round sample existed only because the price filter had
needlessly restricted the direction rules. Removing that filter took the same rule to 619 rounds
across 18 days:

```
n=163  accuracy 0.6196   (price-filtered, 9 days)
n=619  accuracy 0.4879   (full sample, 18 days)
```

The effect did not shrink — it **crossed to the wrong side of 50%**. It was noise in a subsample
one quarter the available size.

**2. It is what chance produces.** 24 rules were scored at 5%, so roughly one false winner is the
*expected* outcome under a pure null. Reporting the best rule's own interval reports the maximum
of 24 draws as though it were a single pre-declared test.

A max-statistic permutation settles it — outcomes shuffled **within each UTC day**, preserving
each day's base rate and round count while destroying only the ordering every rule depends on,
with the whole family re-scored 2,000 times:

```
observed best accuracy        0.5508
best under shuffled labels    median 0.5404,  p95 0.5818
family-wise p-value           0.2985   -> DOES NOT SURVIVE
```

Under pure noise, the best of these 24 rules routinely reaches 0.54–0.58. The observed best of
0.5508 sits squarely inside that distribution. **In a family this size, an apparent 55% rule is
the null hypothesis, not evidence against it.**

## What to carry forward

1. **Score the family, not the winner.** Any lane that sweeps buckets, thresholds or run lengths
   must report a family-wise p-value. Permuting within day is the right null here: it preserves
   the base rate and the day structure and destroys only the sequence.
2. **Check whether a filter is load-bearing before applying it.** A price filter needed by two
   rules silently removed 69% of the sample from twenty others, and manufactured the winner.
3. **Never compare accuracy to 50%.** The hurdle is `ask + fee`. Every rule here beats 50% or
   loses to it by a point or two; all of them lose to 52.35%.
4. **`pm_round_settlements` is not an independent price history.** Prices exist only where a
   snapshot exists. Lanes queued on the assumption that settlements widen coverage should assume
   19 days of direction and 10 days of anything price-derived.
