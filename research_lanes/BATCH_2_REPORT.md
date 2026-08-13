# Batch 2 - Five Standalone Lanes

Validated run: `20260813T063543Z`

Canonical report: [Standalone Alpha Laboratory](../docs/active/STANDALONE_ALPHA_LAB_COMPLETE_CAMPAIGN_2026-08-13.md)

Inputs: immutable copies of the paired Polymarket snapshots/official settlements plus the
360-day Binance research matrix. Authority: `RESEARCH_ONLY`.

## Verdict

No lane produced executable net alpha.

| lane | result |
|---|---|
| `MARKET_DISAGREEMENT_RESOLUTION_V1` | Market wins; model win rate falls from 39.7% to 33.3% as disagreement widens |
| `STATE_VALUE_ATLAS_V1` | Underpowered; 43 cells, one nominal cell, zero family-wise cells beyond 2c |
| `POLY_STALE_QUOTE_V1` | No separated relationship; event-time clock quality is insufficient |
| `MFE_MAE_DISTRIBUTION_V1` | Mean 5m MFE 7.97 bps versus MAE 7.99 bps; symmetric |
| `IMPACT_ASYMMETRY_V1` | Real but negligible: -0.056 bps relative asymmetry versus 12 bps costs |

## Market Disagreement

| absolute model-market residual | model win rate | round-clustered 95% interval |
|---|---:|---:|
| 2-5c | 39.7% | 37.6%-41.7% |
| 5-8c | 35.1% | 32.5%-37.7% |
| 8-12c | 33.2% | 30.5%-36.5% |
| 12-20c | 31.0% | 27.4%-34.8% |
| 20c+ | 33.3% | 26.7%-39.0% |

Larger disagreement is evidence that the standalone model is wrong, not that the market is
offering a larger trade. A future residual must remain anchored to the market and regularized
toward zero.

## State Atlas

The original atlas selected the largest gaps from 43 cells. The validated version fixes two
research-integrity issues:

1. Every round receives equal outcome weight; repeated snapshots do not manufacture sample
   size.
2. Outcome intervals use Wilson bounds with a Bonferroni family correction across all 43 cells.

The largest point gap is `>10m | +5..15 bps | 55-65c`: realized UP 45.5%, market 62.2%, gap
-16.8c. Its family-wise interval is -37.8c to +6.0c. It is not an edge. Across the entire
atlas, one cell clears the nominal screen and **zero** clear the family-wise screen.

## Path and Flow

Five-minute MFE and MAE are equal to two decimals. About 20.9% of windows touch +12 bps, 21.2%
touch -12 bps, and 2.9% touch both. Bar extremes cannot resolve which side arrived first in
the both-touch cases, but the small ambiguous share cannot manufacture a 12 bps advantage.

Buy-heavy flow moves the next minute by about +0.072 bps; sell-heavy flow by +0.128 bps in the
sell direction. The asymmetry is informative as a feature but roughly 1/200 of the declared
round trip, so it is not a standalone trade.

## Data Limit

The stale-quote buckets overlap heavily, and the recorder clock can report slightly negative
quote ages. A real stale-quote test needs synchronized event timestamps and quote-revision
targets at 100ms-1s, not settlement labels.

No result here is approved for serving or capital.
