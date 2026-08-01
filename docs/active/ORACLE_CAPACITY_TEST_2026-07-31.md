# Oracle Capacity Test — does the alive rule survive AT SIZE? (2026-07-31)

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

## Honest limits
- Conservative band pricing (worst price in each band). A real fill lands somewhere between the band edges, so true VWAP sits between this and the top-of-book price.
- Displayed size is not guaranteed size: quotes can be pulled between decision and fill.
- Entry-side only. Valid for THIS rule because it holds to settlement; any early-exit strategy also needs bid-side depth, which the recorder does not yet store.
- One decision per round, 5m only, deployment window only. Not a promotion.

**Nothing here changes any threshold or promotes anything. PAPER research only.**