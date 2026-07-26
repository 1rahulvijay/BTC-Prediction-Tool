# CLARIFICATION 001 — `BINANCE_VOLATILITY_MOMENTUM_V1`

A clarification completes a parameter the frozen preregistration named but left unvalued. It is
**not** an amendment: the original protocol file is untouched and its hash is unchanged. This
record exists so the completed value lives in a separately hashed audit artifact rather than only
in source code, where it could later be edited without trace.

```
Declared:                        2026-07-26
Production rows at declaration:  0
M0 results available:            no
Any Binance analysis run:        no
Any model fitted:                no

Clarification:
    CLASS_B_MAX_AGE_S = 60.0

Reason:
    completes an unspecified frozen parameter
    before evidence collection begins

Original preregistration:
    unchanged
    sha256 0973744b73651e8287b44309c976530f72a3964ceb082703c6b49400564c72f7
```

## What it completes

Section 10 of the preregistration lists, among the data-quality gates that suppress a decision:

> `REST source age > frozen limit`

The limit is named as frozen but no number is assigned anywhere in the document. `60.0` seconds
completes it.

## Why this value

Section 4 of the same preregistration already requires Class B features to be aggregated over
`>= 60s`. A staleness limit equal to that aggregation window is the value the document's own
structure implies; it was not selected by looking at outcomes, because no outcomes exist.

Measured steady-state ages from collector smoke runs on 2026-07-26, all far below the limit:

| stream | class | observed age |
|---|---|---|
| `binance_perp/premiumIndex` | B | ~0.7-1.0 s |
| `binance_perp/openInterest` | B | ~5.9-8.2 s |
| `binance_perp/aggTrade_rest` | B | ~1-2 s in steady state |

The limit therefore excludes **malfunction**, not normal operation. The one systematically
excluded population is the reconnect backfill poll (`poll_id = 1`), whose measured ages were
**255-447 seconds** — and that population is prohibited from features outright by a separate,
stronger rule, not merely by this age limit.

## Where it is enforced

`backend/venues/venue_admissibility.py`, as `CLASS_B_MAX_AGE_S`, applied in SQL inside the only
sanctioned path from `venue_events` to a decision feature — so it cannot be relaxed by
post-filtering a DataFrame. Covered by `--selftest`.

## Binding condition

**Revising this value after seeing any M0 result invalidates the experiment**, exactly as a
threshold change would. A near miss is a miss. If the value proves unworkable, the correct action
is to abandon and archive the experiment, not to amend this record.

## Audit chain

```
PREREG_BINANCE_VOLATILITY_MOMENTUM_V1.md   0973744b73651e82...   (frozen 2026-07-26, unchanged)
PREREG_BINANCE_V1_CLARIFICATION_001.md     <this file, hashed in PREREG_HASH.txt>
```

Both hashes are recorded in the deployment completion record.
