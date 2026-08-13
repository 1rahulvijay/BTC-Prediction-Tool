# Batch 3 - Entry Timing, Settlement Sensitivity and Maker Markout

Validated run: `20260813T063543Z`

Canonical report: [Standalone Alpha Laboratory](../docs/active/STANDALONE_ALPHA_LAB_COMPLETE_CAMPAIGN_2026-08-13.md)

Inputs: immutable copies of the paired Polymarket snapshots/official settlements. Authority:
`RESEARCH_ONLY`.

## Verdict

No causal entry-timing rule cleared zero, settlement sensitivity is a risk surface rather than
a direction signal, and maker profitability remains blocked by missing actual fills and queue
position.

## Wait Versus Buy

The minimum future ask is retained only as a hindsight upper bound. It appears to improve by
1.94c at 10s, 5.24c at 30s and 8.34c at 60s, but no live policy can select the future minimum.

The corrected test also evaluates the causal policy "always wait exactly N seconds, then buy at
the first available ask":

| fixed wait | net delta/share | round-clustered 95% interval |
|---|---:|---:|
| 10s | +0.0009 | -0.0001 to +0.0018 |
| 30s | +0.0023 | -0.0002 to +0.0049 |
| 60s | +0.0046 | -0.0003 to +0.0093 |

Every interval spans zero. `WAIT_VS_BUY_V1` therefore has no causal entry-timing edge.

## Settlement Sensitivity

Each cell fits `change in UP midpoint ~ BTC change in bps`, with whole-round bootstrap intervals
and a Bonferroni correction across the surface.

| cell | point cents/BTC-bp | family-wise interval |
|---|---:|---:|
| `<60s | 0-3bps` | 0.762 | -0.611 to +2.035 |
| `5-10m | 0-3bps` | 0.310 | +0.002 to +0.614 |
| `>10m | 0-3bps` | 0.237 | +0.098 to +0.399 |
| `>10m | 3-8bps` | 0.197 | +0.030 to +0.408 |

The final-minute near-anchor point estimate is large but imprecise and not family-wise
separated. The broader surface confirms that PM prices react to BTC; it does not predict the
next BTC move. Use it only for exposure, quote-toxicity and sizing research.

## Maker Markout Surface

The batch now regenerates the markout table in code. For each snapshot it assumes a resting UP
bid fills, then marks that hypothetical fill to the first observed UP midpoint at +5s, +15s and
+30s.

This is an optimistic diagnostic, not maker PnL. Actual fills are not random: they occur when a
taker chooses to trade against the resting order, which is precisely when adverse selection can
be worst. The dataset contains no queue position or actual maker-fill event.

Representative +30s hypothetical markouts:

| cell | markout | observations |
|---|---:|---:|
| `>5m | 3-8bps` | +1.12c | 18,967 |
| `2-5m | 0-3bps` | +0.55c | 36,998 |
| `60-120s | 3-8bps` | +0.50c | 6,479 |
| `2-5m | >8bps` | -0.02c | 8,306 |

About 0.5c is mechanically explained by marking a bid fill to the midpoint of a one-cent
spread. `HEDGED_POLY_MM_V1` stays `PARTIAL_DATA_BLOCKED` until real shadow-posted orders produce
fill-conditioned markouts.

No result here is approved for serving or capital.
