# Event Execution And Anchor Crossing V1

Date: 2026-07-28

Status: completed research campaign. No production or paper-trading behavior changed.

## Decision

The 5-second and 15-second event models should not be used to change the
Polymarket settlement side.

They do contain useful information about the next contract-price change:

- The UP-ask repricing head passed the frozen research gate.
- The DOWN-ask repricing head passed the frozen research gate.
- Event disagreement did not improve the baseline settlement trade.
- Event evidence did not add enough calibrated anchor-crossing value.
- Direct 5-second and 15-second BTC trades lost after a conservative 2 bps
  round-trip cost.

The only justified next step is forward-shadow testing of contract repricing.
Nothing from this campaign is approved for live or paper execution.

## Why This Campaign Exists

Earlier locked tests established:

- Event direction is useful for the immediate 5-15 second BTC path.
- Persisting that direction does not improve 5-minute or 15-minute settlement.
- Anchor distance, time remaining, and market price dominate settlement.
- The event ACT/SKIP head produces too few trustworthy actions.

This campaign therefore separates four jobs:

1. Settlement side selection.
2. Entry timing and veto.
3. Anchor-crossing risk.
4. Executable contract repricing.

The event signal may inform jobs 2-4. It may never flip job 1.

## Frozen Design

Protocol:

`backend/research/event_execution_v1/frozen_protocol.json`

The protocol was frozen before the locked economic outcomes were evaluated.
It contains exactly ten experiments:

| ID | Experiment |
|---|---|
| E01 | Baseline immediate Polymarket entry |
| E02 | Event-disagreement veto |
| E03 | Event-disagreement fixed 5-second delay |
| E04 | Anchor crosses within 5 seconds |
| E05 | Anchor crosses within 15 seconds |
| E06 | Anchor crosses and recrosses within 15 seconds |
| E07 | UP ask worsens by at least 1 cent within 5 seconds |
| E08 | DOWN ask worsens by at least 1 cent within 5 seconds |
| E09 | Matched-horizon 5-second BTC proxy trade |
| E10 | Matched-horizon 15-second BTC proxy trade |

Fixed execution assumptions:

- One share at archived top-of-book ask.
- Canonical crypto taker fee from `backend/polymarket_fee.py`.
- Three-cent required edge buffer.
- One original entry candidate per market.
- Same 1,073 original candidates for E01-E03.
- Event disagreement threshold: absolute persistent score at least 0.12.
- Fixed delay: 5 seconds; a separate 2-second latency stress uses 7 seconds.

## Data Separation

The existing event predictions did not overlap the executable Kachoio book
archive. Two new research-only event-head runs were generated:

| Role | Event period | Artifact |
|---|---|---|
| Development | 2026-05-06 through 2026-05-11 | `20260728T055042Z` |
| Locked test | 2026-05-13 through 2026-05-18 10:35 UTC | `20260728T055245Z` |

The development period was split chronologically into 70% fit and 30%
calibration markets. The later period was never used for fit, calibration, or
threshold selection.

Inputs:

- Per-second executable UP/DOWN top-of-book and official outcomes from
  `Kaggle Data/archive (7).zip`.
- Per-second Binance spot reconstructed from cached aggregate trades.
- 5-second and 15-second event probabilities generated from 86 causal event
  features.

Important limitation: the Binance market-start price is an anchor proxy. The
official Polymarket outcome remains the settlement truth.

## Locked Results

Canonical run:

`data/research/event_execution_v1/20260728T063909Z`

### Execution Policies

| Policy | Accepted | Win rate | Total PnL | Delta vs immediate | Verdict |
|---|---:|---:|---:|---:|---|
| Immediate | 1,073 | 74.93% | +15.968 | baseline | Research baseline only |
| Disagreement veto | 1,052 | 74.71% | +15.205 | -0.763 | Reject |
| Delay 5 seconds | 1,073 | 74.93% | +15.725 | -0.242 | Reject |

All PnL values are share-dollars for one share per candidate after the canonical
entry fee. The immediate policy had profit factor 1.092, which is thin and is
not production proof.

The veto skipped 21 candidates:

- 18 skipped trades would have won.
- 3 skipped trades would have lost.

The delay paid an average 0.024 cents more per accepted entry. Adding two more
seconds of latency reduced total PnL to +15.630, or -0.338 versus immediate.

Conclusion: event disagreement is not a useful settlement-entry veto in this
locked period.

### Anchor Crossing

| Head | Baseline AUC | Event AUC | AUC delta | Brier delta | Verdict |
|---|---:|---:|---:|---:|---|
| Cross within 5s | 0.9141 | 0.9176 | +0.0035 | +0.00048 | Reject |
| Cross within 15s | 0.8729 | 0.8810 | +0.0081 | -0.00036 | Reject |
| Cross then recross within 15s | 0.9120 | 0.9187 | +0.0067 | +0.00018 | Reject |

The anchor geometry baseline was already strong. No event-enhanced crossing
head cleared the predeclared AUC, Brier, and day-block gates together.

This test is restricted to 5-minute Polymarket markets because the executable
archive contains no 15-minute book.

### Contract Repricing

| Head | Quote states | Baseline AUC | Event AUC | AUC delta | Brier delta | Day-block Brier gain LB |
|---|---:|---:|---:|---:|---:|---:|
| UP ask worsens >=1c in 5s | 391,713 | 0.6897 | 0.7120 | +0.0223 | -0.00557 | +0.00358 |
| DOWN ask worsens >=1c in 5s | 391,713 | 0.6866 | 0.7108 | +0.0242 | -0.00574 | +0.00403 |

Both heads cleared every frozen incremental research gate. This means the event
features add contract-repricing information beyond current book, anchor, time,
volatility, spread, and depth features.

It does not mean the heads are ready to trade:

- The rows are quote states clustered inside about 1,458 markets and six
  calendar days.
- There is no multi-week forward shadow result.
- No maker-fill model was tested.
- No queue position or sub-second latency was available.
- The passing label predicts price worsening, not settlement profit.

Their status is **forward-shadow candidate**, not production promotion.

### Lead-Lag Diagnostic

For strong event states, the ask on the event-predicted side changed as follows:

| Delay | UP mean ask change | DOWN mean ask change |
|---|---:|---:|
| 1 second | +0.33c | +0.39c |
| 2 seconds | +0.65c | +0.74c |
| 5 seconds | +1.19c | +1.46c |
| 10 seconds | +1.35c | +1.81c |

The source is one-second data. It cannot test 100ms, 250ms, or 500ms capture.
The incremental repricing models are stronger evidence than these unconditional
means because they compare against a current-book baseline.

### BTC Microtrade Proxy

| Horizon | Calls | Gross win rate | Gross mean | Gross PF | Mean after 2 bps |
|---|---:|---:|---:|---:|---:|
| 5s | 5,362 | 60.74% | +0.613 bps | 2.039 | -1.387 bps |
| 15s | 4,787 | 58.58% | +0.611 bps | 1.706 | -1.389 bps |

The event direction is real at the prediction horizon, but the average movement
is smaller than a conservative 2 bps round-trip cost. Both policies are rejected
for execution. Historical executable Binance bid/ask was unavailable, so no
positive gross result could have qualified for promotion anyway.

## Correct Architecture After This Test

```text
Settlement engine
    chooses UP or DOWN from anchor, time, volatility and market price
        |
        v
Contract repricing shadow
    estimates whether the desired ask is likely to worsen within 5 seconds
        |
        v
Executable EV and fill engine
    compares enter-now, wait, maker, taker, fees, depth and missed-fill risk
        |
        v
Paper decision only after forward gates pass
```

The event head remains prohibited from changing the settlement side.

## LSTM Review Decision

The reviewed daily-close LSTM repository is useful as an architecture reference,
not as a reusable trading model.

Retain these ideas for a later isolated experiment:

- Separate direction and magnitude sequence encoders.
- Robust scaling and stationary features.
- Compact LayerNorm -> LSTM 96 -> LSTM 48 -> Dense 32 architecture.
- Huber and quantile magnitude losses.
- Versioned model, scaler, feature schema, and data-span manifests.
- Incremental comparison against linear and tree scores.

Do not copy:

- Daily close inputs.
- Raw price-level prediction.
- MinMax scaling as the live default.
- Recursive multi-step forecasts.
- Gaussian uncertainty bands.
- Test-set-driven early stopping.

The LSTM is deferred. The cheaper direct contract-repricing experiment found a
specific signal first. Neural work is justified only after that signal survives
forward shadow and only if the LSTM improves Brier, log loss, and executable EV
over the existing repricing model.

## Artifacts And Commands

Run:

```powershell
.\research\launchers\run_event_execution_anchor_crossing.bat
```

Implementation:

- `backend/research/event_execution_v1/frozen_protocol.json`
- `backend/research/event_execution_v1/run_campaign.py`
- `backend/research/event_execution_v1/validate_result.py`
- `research\launchers\run_event_execution_anchor_crossing.bat`

Canonical outputs:

- `results.json`
- `experiment_metrics.csv`
- `execution_policy_candidates.parquet`
- `execution_slices.csv`
- `contract_lead_lag.csv`
- `locked_incremental_predictions.parquet`
- `btc_proxy_trades.parquet`
- `input_manifest.json`
- `split_manifest.json`
- `validation.json`
- `models/*.joblib`

All validator checks pass, including protocol equality, input hashes, exact
E01-E10 coverage, chronological separation, same-candidate execution policies,
artifact counts, and production-promotion blocking.

## Next Gate

E07/E08 are now wired only into the isolated
`POLYMARKET_REPRICING_SHADOW_V1` recorder. Its stricter frozen gate requires
1,000 decisions, 500 per side, eight continuous weeks, incremental calibration
and monotonicity per side, positive day-block/weekly/latency/size results, and a
positive untouched final period. See
`POLYMARKET_REPRICING_SHADOW_V1_2026-07-28.md`.

Until those conditions pass and a separate reviewed promotion is made, the app
displays no new action and makes no paper or live trade from these models.
