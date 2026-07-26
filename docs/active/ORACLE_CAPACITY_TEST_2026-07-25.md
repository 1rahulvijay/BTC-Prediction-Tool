# Oracle Capacity Test — does the alive rule survive AT SIZE? (2026-07-25)

`LATE_LEADER_30S_V1` was measured live at **qty=1** (EV +0.90c, gate FAILED). This asks the business question it never answered: **how many shares wide is that edge?**

The rule **holds to settlement — it never sells** — so its capacity is purely an ENTRY-side question, and the Oracle recorder stored the full ask ladder. (Missing bid size only blocks capacity for early-exit strategies, all of which are already dead.)

Reconstructed independently from **2,474 settled 5m rounds** over **21 days** of deployment quotes (not from the paper ledger): first snapshot at 20-32s left, leader = higher bid, frozen ask gates 0.60-0.97, one decision per round. Conservative ladder walk: fills beyond the top level are charged at the WORST price of each depth band (+1c / +2c / +5c).

| intended size | rounds fillable | fill rate | avg entry VWAP | slippage vs top ask | win% | EV/share | EV LB (day-block) | total $ |
|---|---|---|---|---|---|---|---|---|
| **1** | 2,474 | 100.0% | 83.33c | +0.00c | 84.2% | **-0.07c** | -1.47c | $-2 |
| **5** | 2,474 | 100.0% | 83.36c | +0.02c | 84.2% | **-0.09c** | -1.49c | $-12 |
| **10** | 2,471 | 99.9% | 83.44c | +0.09c | 84.3% | **-0.07c** | -1.43c | $-17 |
| **25** | 2,454 | 99.2% | 83.69c | +0.26c | 84.3% | **-0.26c** | -1.58c | $-157 |
| **50** | 2,433 | 98.3% | 83.98c | +0.49c | 84.3% | **-0.55c** | -1.82c | $-663 |
| **100** | 2,388 | 96.5% | 84.53c | +0.87c | 84.3% | **-1.03c** | -2.35c | $-2,459 |
| **250** | 2,186 | 88.4% | 86.02c | +1.52c | 85.3% | **-1.48c** | -2.85c | $-8,071 |

## What this means

- At **1 share** the reconstruction gives EV **-0.07c** — the independent check on the ledger's +0.90c (different code path, same deployment window).
- At **25 shares**: EV **-0.26c/share**, fill rate 99%, day-block LB -1.58c
- **Best total dollars** across the window came at size **1**: $-2 over 21 days (= $-0.08/day at that size).
- Slippage is the mechanism: each extra depth band costs 1-5c, while the entire measured edge is under 1c. **Any size that walks past the top level cannot be profitable** — the first band alone is larger than the edge.

## The headline: THERE IS NO SIZE AT WHICH THIS IS A BUSINESS

Read the EV column top to bottom: it starts at ~zero and only goes down. The mechanism is
arithmetic, not bad luck:

```
gross edge = win% - ask = 84.2% - 83.33c        = +0.87c
taker fee  = 0.07*p*(1-p) at p=0.833            = -0.97c
------------------------------------------------------------
net at ONE share                                = -0.10c
cost of the FIRST depth band beyond the top     = +1.00c slippage
```

**The first extra penny of slippage is larger than the entire gross edge.** The moment an order
walks past the top level it is structurally unprofitable — which is why EV falls monotonically with
size and total dollars go from ~$0 to -$8,071 at 250 shares. Best total across 21 days at the best
size was **about $0**. Capacity here is not "small"; it is absent.

This also pre-answers, from a different direction, what the 8-week clock was going to ask: even if
`LATE_LEADER_30S_V1` passed its EV gate at qty=1 on 2026-08-30, **there is no size at which it
converts into meaningful dollars** — the depth required to matter costs more than the edge.

### Reconstruction vs ledger: a real discrepancy, stated plainly

This reconstruction gives **-0.07c at 1 share**; the live ledger reported **+0.90c** over the same
window. Both are defensible, and the gap is informative rather than a bug:

- **The round set differs.** The ledger counts only rounds where a *fresh bridge quote* existed at
  the decision moment (n=2,145). This reconstruction accepts any recorded snapshot in the 20-32s
  band (n=2,474) - it includes ~330 rounds the live rule declined, and those are worse on average.
- **The decision instant differs.** The reconstruction takes the snapshot nearest 32s left; the live
  rule fires on whichever tick it first observes inside the band.

The honest reading: the live +0.90c is **partly a selection effect of only trading when a fresh
quote happened to be available**, and a slightly wider, mechanically-defined version of the same
rule sits at zero. That is the dangerous direction of error - it means the live number is the
*optimistic* one, and it is already failing its gate.

## Honest limits
- Conservative band pricing (worst price in each band). A real fill lands somewhere between the band edges, so true VWAP sits between this and the top-of-book price.
- Displayed size is not guaranteed size: quotes can be pulled between decision and fill.
- Entry-side only. Valid for THIS rule because it holds to settlement; any early-exit strategy also needs bid-side depth, which the recorder does not yet store.
- One decision per round, 5m only, deployment window only. Not a promotion.

**Nothing here changes any threshold or promotes anything. PAPER research only.**