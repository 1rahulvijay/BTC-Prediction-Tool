# Deployed-head live calibration (2026-07-31)

Every strategy consumes these probabilities. If they are biased, every downstream edge calculation is wrong before a trade is placed. Joined to officially resolved outcomes over the 21-day Oracle deployment. **One observation per round per head** (never per tick - pooling ticks would inflate n ~13x and manufacture false confidence).

## P(Hold) - the app's most-used probability

n = **6,727** rounds (last snapshot in the 15-120s window). Brier **0.0910** | ECE **0.0678** | skill vs base rate **+0.044** | base rate 89.3%

| predicted band | n | mean predicted | realized | gap |
|---|---|---|---|---|
| 50%-55% | 8 (too few) | - | - | - |
| 55%-60% | 50 | 58.6% | 54.0% | -4.6% |
| 60%-65% | 48 | 61.7% | 52.1% | -9.6% **<-** |
| 65%-70% | 161 | 68.2% | 62.1% | -6.0% **<-** |
| 70%-75% | 183 | 73.2% | 67.2% | -6.0% **<-** |
| 75%-80% | 91 | 77.4% | 80.2% | +2.8% |
| 80%-85% | 194 | 82.2% | 71.6% | -10.5% **<-** |
| 85%-90% | 177 | 88.0% | 72.3% | -15.7% **<-** |
| 90%-95% | 298 | 92.8% | 81.2% | -11.6% **<-** |
| 95%-100% | 5,512 | 99.6% | 93.4% | -6.2% **<-** |

**Overall: predicted 96.1% vs realized 89.3% (-6.7%).**
**Systematically OVER-confident** by 6.7%. Any gate using a raw P(Hold) threshold is mis-set by that much and needs recalibration before use.

## Flip risk - does a higher number mean more actual flips?

n = **6,727** rounds. Brier **0.1105** | ECE **0.0872** | skill **+0.002** | actual flip rate 12.7%

| predicted band | n | mean predicted | realized | gap |
|---|---|---|---|---|
| 0%-5% | 5,487 | 0.8% | 8.4% | +7.6% **<-** |
| 5%-10% | 411 | 7.5% | 30.7% | +23.2% **<-** |
| 10%-15% | 144 | 13.3% | 28.5% | +15.2% **<-** |
| 15%-20% | 104 | 17.3% | 28.8% | +11.5% **<-** |
| 20%-25% | 166 | 22.1% | 28.9% | +6.8% **<-** |
| 25%-30% | 82 | 27.9% | 30.5% | +2.5% |
| 30%-35% | 119 | 31.9% | 36.1% | +4.2% |
| 35%-40% | 67 | 37.9% | 40.3% | +2.4% |
| 40%-45% | 85 | 41.9% | 32.9% | -9.0% **<-** |
| 45%-50% | 23 (too few) | - | - | - |

**Ranks flips better than a constant.**

## Late shock >= $20

n = **6,727** | Brier **0.1290** | ECE **0.0932** | skill **-0.013** | actual rate 15.0% | mean predicted 13.1%

| predicted band | n | mean predicted | realized | gap |
|---|---|---|---|---|
| 0%-10% | 4,565 | 0.6% | 8.7% | +8.0% **<-** |
| 10%-20% | 599 | 13.9% | 15.5% | +1.6% |
| 20%-30% | 325 | 24.7% | 23.4% | -1.3% |
| 30%-40% | 334 | 34.8% | 31.4% | -3.4% |
| 40%-50% | 320 | 45.7% | 29.4% | -16.3% **<-** |
| 50%-60% | 190 | 56.6% | 32.6% | -24.0% **<-** |
| 60%-70% | 62 | 67.1% | 38.7% | -28.4% **<-** |
| 70%-80% | 117 | 73.5% | 39.3% | -34.2% **<-** |
| 80%-90% | 111 | 83.1% | 44.1% | -38.9% **<-** |
| 90%-100% | 104 | 95.8% | 60.6% | -35.3% **<-** |

## Late shock >= $50

n = **6,727** | Brier **0.0248** | ECE **0.0198** | skill **+0.032** | actual rate 2.6% | mean predicted 1.4%

| predicted band | n | mean predicted | realized | gap |
|---|---|---|---|---|
| 0%-10% | 6,507 | 0.4% | 2.0% | +1.6% |
| 10%-20% | 92 | 14.7% | 13.0% | -1.6% |
| 20%-30% | 32 | 25.5% | 21.9% | -3.6% |
| 30%-40% | 31 | 34.5% | 12.9% | -21.5% **<-** |
| 40%-50% | 20 (too few) | - | - | - |
| 50%-60% | 22 (too few) | - | - | - |
| 60%-70% | 3 (too few) | - | - | - |
| 70%-80% | 5 (too few) | - | - | - |
| 80%-90% | 11 (too few) | - | - | - |
| 90%-100% | 4 (too few) | - | - | - |

## Late shock >= $100

n = **6,727** | Brier **0.0022** | ECE **0.0018** | skill **+0.068** | actual rate 0.2% | mean predicted 0.2%

| predicted band | n | mean predicted | realized | gap |
|---|---|---|---|---|
| 0%-10% | 6,708 | 0.0% | 0.2% | +0.1% |
| 10%-20% | 6 (too few) | - | - | - |
| 20%-30% | 3 (too few) | - | - | - |
| 30%-40% | 1 (too few) | - | - | - |
| 40%-50% | 3 (too few) | - | - | - |
| 50%-60% | 2 (too few) | - | - | - |
| 60%-70% | 1 (too few) | - | - | - |
| 70%-80% | 2 (too few) | - | - | - |
| 80%-90% | 1 (too few) | - | - | - |
| 90%-100% | 0 (too few) | - | - | - |

## Champion action tiers - do they stratify monotonically?

| action | rounds | leader held |
|---|---|---|
| PAPER | 755 | 69.4% |
| WAIT | 5,834 | 89.6% |
| AVOID | 138 | 89.1% |

**NOT monotone** (PAPER 69% > WAIT 90% > AVOID 89%). A non-monotone tier must not be presented as a confidence ranking - per the stratifier rule it is noise.

## How to read this

- **Skill vs base rate (BSS)** is the load-bearing number. <= 0 means the head carries no usable information no matter how good its AUC looked in training.
- **ECE / per-band gap** says whether a *threshold* on the head is set where you think. A head can rank well (good BSS) and still be mis-scaled (bad ECE) - then it needs recalibration, not replacement.
- Bands marked **<-** are off by more than 5 points.
- One row per round; the 21-day window can kill a head but cannot promote one (the promotion contract needs 8 calendar weeks).

**Nothing here changes a threshold or promotes anything. Measurement only.**