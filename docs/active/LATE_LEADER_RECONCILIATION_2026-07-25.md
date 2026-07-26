# LATE_LEADER_30S_V1 - ledger vs replay reconciliation (2026-07-25)

Two measurements of the same rule over the same window disagreed. This settles it at the round level by joining both on the round anchor and splitting the gap into **round selection** vs **accounting**.

| set | rounds | EV/share |
|---|---|---|
| ledger, all its rounds | 2,145 | **+0.90c** |
| replay, all its rounds | 2,474 | **-0.07c** |
| **INTERSECTION - ledger** | 1,942 | **+1.23c** |
| **INTERSECTION - replay** | 1,942 | **+0.67c** |
| ledger only (replay skipped) | 203 | -2.20c |
| replay only (ledger skipped) | 532 | -2.76c |

## Field-level agreement on shared rounds

| field | disagreements | median gap | 95th pct |
|---|---|---|---|
| leader side | **7** of 1,942 (0.4%) | - | - |
| entry ask | 1,245 | 1.00c | 10.00c |
| realized pnl | 1,246 | 0.96c | 10.36c |
| decision time | - | 1.0s | 2.6s |

## Verdict

**The gap is BOTH selection and accounting - and the accounting half traces to a single cause: a
sub-second difference in entry timing. Neither implementation is buggy. The rule is fragile.**

Total gap: ledger +0.90c - replay -0.07c = +0.97c, which decomposes as:

| component | size | cause |
|---|---|---|
| round selection | ~0.41c | replay traded 532 rounds the live rule declined (those avg **-2.76c**); live rule only fires when a <=5s bridge quote exists |
| **entry timing** | **~0.56c** | on shared rounds, the ledger enters **0.8s earlier** at an ask that is **0.64c cheaper on average** |

Leader definition is NOT the cause: the two paths pick the same side in **99.6%** of shared rounds
(7 disagreements in 1,942). Fee formula and settlement mapping agree. The entire accounting gap is
*when* the entry is priced.

### The mechanism, and why it matters more than the number

Direct measurement on 1,936 same-side paired rounds:

```
ledger entry time vs replay      : 0.8s EARLIER  (median)
ledger ask vs replay ask         : 0.64c CHEAPER (mean)
same rounds, same outcomes:
    priced at the ledger's ask   : +1.34c
    priced at the replay's ask   : +0.72c
```

The leader's ask climbs toward $1 as the clock runs out, so **entering a fraction of a second
earlier books a systematically cheaper price.** That is not an edge - it is the clock.

**The implication is the important part: this rule loses roughly 0.6-0.8c of EV per second of
delay, and its entire measured edge is +0.90c.** So about **one second of additional latency
consumes the whole thing.** A live trader - human or bot - sits 1-3s behind the quote that the
paper ledger books at. The +0.90c is a best-case timing artifact of a paper implementation that
enters at the instant it observes a quote; it is not a price anyone can actually transact at.

This also explains, without any new hypothesis, three previously separate observations:
- offline +2.5c degraded to live +0.90c (**more realistic timing, less edge**);
- the maker variant lost -9.53c (**resting means being last, i.e. maximum delay**);
- the 90%-profitable-exit paradox is uncapturable (**the profitable moment is gone in ~a second**).

**Status: `LATE_LEADER_30S_V1` is not merely failing its gate - its measured edge is inside its own
latency sensitivity.** Even a passing 8-week result would not survive execution delay.

## Limits
- Round-anchor join; a round present twice in either source is collapsed to its first entry.
- The replay picks the snapshot nearest 32s left; the live rule fires on its first observed tick in the 20-32s band. Both are legitimate readings of the same frozen spec.
- Nothing here changes a threshold or promotes anything. PAPER research only.