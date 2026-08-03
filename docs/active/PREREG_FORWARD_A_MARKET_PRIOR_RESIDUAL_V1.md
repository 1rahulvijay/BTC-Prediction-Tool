# PREREG A — MARKET_PRIOR_RESIDUAL_V1

**Frozen `2026-08-02`, before the forward window opens.** No `FORWARD_UNTOUCHED` rows exist at
the time of freezing. Any edit to this text invalidates every result scored under it; the hash
in `docs/active/PREREG_HASH.txt` is checked in CI.

## Question

Does any model input add **resolution** beyond the executable market price?

## Why this and not recalibration

Measured (`RESEARCH_LEDGER` §10.5): P(hold) loses `+0.0144` of Brier to the market, of which
`+0.0143` is **resolution** and `+0.0001` is calibration. A resolution deficit cannot be
repaired by isotonic, Platt or beta calibration — those re-map a forecast, they do not add
information. **No pure recalibration arm is included, deliberately.**

## Arms

| arm | definition |
|---|---|
| **CHAMPION** | executable market probability (the ask on the side considered) |
| **CHALLENGER** | `logit(market ask) + f(x)` — a residual model over the market prior |
| CONTROL | matched-count random selection |

`f(x)` is fitted to the residual only. The champion is never re-fitted, never re-scaled, and
never replaced by the model — it enters the challenger as a fixed offset.

## Inputs to `f(x)`

Only columns admitted by `causal_validation.feature_columns()` on the causal checkpoint
dataset: no label, no outcome, no identity. The clock is admitted.

## Required result — all four, no substitutions

1. lower **Brier** than the champion
2. higher **resolution** under Murphy decomposition (the component that is actually missing)
3. lower **log loss**
4. positive **executable net PnL** at the ask, after fees, with a positive day-block lower bound

A win on 1, 3 and 4 without 2 does **not** pass. Improving Brier without improving resolution
means the residual is re-mapping the market, not adding to it.

## Population and partitions

- population: every causally eligible checkpoint with an official settlement
- **evidence class must be `FORWARD_UNTOUCHED`** — rows from 2026-08-02 onward only
- train / calibrate / evaluate split strictly chronological, evaluation last and never reused

## Data gate before scoring

- ≥ 8 uninterrupted forward weeks
- ≥ 1,000 independently resolved rounds
- zero causal-timestamp violations in the ledger
- artifact serviceability > 0

## Stopping rule

Scored **once**, when the data gate passes. Not scored early, not scored repeatedly. A second
look requires a new preregistration with its own hash and its own forward window.
