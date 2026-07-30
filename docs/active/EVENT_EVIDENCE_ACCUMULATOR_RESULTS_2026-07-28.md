# Event-Time Evidence Accumulator: Locked Results

Date: 2026-07-28

Status: **ARCHITECTURE VALIDATED - INCREMENTAL DIRECTION REJECTED - RESEARCH ONLY**

## Question

The event-time specialists showed repeatable short-horizon direction ranking:

```text
5s AUC approximately 0.77
15s AUC approximately 0.69
30s AUC approximately 0.65
60s AUC approximately 0.61
```

This campaign tested whether repeated short-lived forecasts can be accumulated into independent
5m/15m settlement candidates without counting every overlapping observation as a trade.

It did not test live profitability. The later locked period has no synchronized executable
Polymarket ask/depth history.

## Implementation

| Item | Path |
|---|---|
| Frozen protocol | `backend/research/event_evidence_accumulator/frozen_protocol.json` |
| Standalone runner | `backend/research/event_evidence_accumulator/run_accumulator_campaign.py` |
| Artifact validator | `backend/research/event_evidence_accumulator/validate_result.py` |
| Windows launcher | `research\launchers\run_event_evidence_accumulator.bat` |
| Completed run | `data/research/event_evidence_accumulator/20260728T053541Z/` |

No serving model, paper policy or production artifact was changed.

## Frozen Design

### Inputs

| Role | Locked prediction period |
|---|---|
| Development/calibration/selection | 2026-06-19 through 2026-06-24 |
| Final locked test | 2026-07-19 through 2026-07-24 |

The periods use separately trained event-head generations and do not overlap.

Raw Binance spot aggregate trades were reconstructed to one-second causal last prices. Synthetic
5m and 15m UTC rounds supplied:

- current BTC price;
- round-start price to beat;
- settlement price;
- distance from the price to beat;
- time remaining;
- trailing 60-second realized volatility.

### Accumulator

The runner causally carries the latest 5s/15s/30s/60s prediction forward only up to its own
horizon. Each head contributes:

```text
logit(P(up-first)) x P(movement)
```

Three predeclared weight schemes and three persistence half-lives produced exactly nine
configurations. No threshold search was added after seeing outcomes.

The state machine uses:

```text
NEUTRAL
WATCH_LONG
WATCH_SHORT
CONFIRMED_LONG
CONFIRMED_SHORT
COOLDOWN
```

Confirmation requires:

- 5s and 15s agreement;
- no strong 30s/60s contradiction;
- sufficient movement probability;
- 8-of-10 sign persistence;
- a non-collapsing score;
- valid source ages;
- one candidate at most per market.

### Settlement aggregators

Separate 5m and 15m logistic models were fitted.

Baseline inputs:

```text
standardized anchor distance
time remaining
current anchor side
60-second realized volatility
```

Evidence inputs added:

```text
persistent event score
late-window event interaction
movement score
```

Complete markets were separated chronologically into 60% fit, 20% calibration and 20% selection.
The later six-day period was opened once.

## Configuration Selection

Selected on the older period:

```text
scheme: short_dominant
weights: 5s 0.65, 15s 0.25, 30s 0.10, 60s 0.00
half-life: 5 seconds
```

Important warning: even the selected configuration was worse than the baseline on the selection
period:

```text
evidence Brier minus baseline Brier = +0.003932
```

All nine configurations had positive Brier deltas, so none added probability quality during
selection. The frozen v1 protocol still selected the least-bad configuration and opened the later
period. Future campaigns should use an explicit stop-before-locked-test rule when every
configuration loses to baseline.

## Locked Result

| Scope | Independent candidates | Candidate side accuracy | Wilson lower bound | Baseline Brier | Evidence Brier | Baseline log loss | Evidence log loss |
|---|---:|---:|---:|---:|---:|---:|---:|
| All | 1,188 | 64.56% | 61.80% | 0.1761 | 0.1800 | 0.5282 | 0.5454 |
| 5m | 785 | 66.11% | 62.73% | 0.1628 | 0.1657 | 0.4973 | 0.5075 |
| 15m | 403 | 61.54% | 56.70% | 0.2020 | 0.2078 | 0.5883 | 0.6193 |

The 64.56% candidate accuracy is not an incremental edge. At the exact same candidate moments:

| Decision rule | Accuracy |
|---|---:|
| Event accumulator candidate side | 64.56% |
| Current side of the BTC anchor | 75.00% |
| Distance/time baseline probability | 74.83% |
| Baseline plus event evidence | 74.49% |

The accumulator side was:

```text
-10.44 percentage points versus the current anchor side
-10.27 percentage points versus the distance/time baseline
```

The event-adjusted probability was also worse than the baseline on AUC, Brier and log loss for
both 5m and 15m.

## Frozen Gates

| Gate | Result |
|---|---|
| At least 200 independent candidates | PASS |
| At least 50 candidates per horizon | PASS |
| Evidence Brier better for all/5m/15m | FAIL |
| Evidence log loss better for all/5m/15m | FAIL |
| Candidate side accuracy above 50% | PASS |
| Overall Wilson lower bound above 50% | PASS |
| More than 75% of days above 50% | PASS |

Overall continuation gate: **FAIL**.

The support gates pass because persistence successfully converts overlapping observations into
independent episodes. The incremental-information gates fail because anchor geometry explains
settlement better than the accumulated micro-direction signal.

## What Is Reusable

The following engineering is useful and should be retained for a future shadow lane:

- causal multi-horizon as-of alignment with explicit source ages;
- logit evidence weighted by movement probability;
- exponentially decayed persistence;
- explicit WATCH/CONFIRMED/COOLDOWN transitions;
- one candidate per market;
- market-grouped fit/calibration/selection;
- separate 5m and 15m aggregators;
- baseline-versus-incremental probability comparison;
- candidate, market and day effective-sample reporting;
- immutable protocol/script/input hashes.

These are governance and measurement improvements, not proven alpha.

## What Must Not Be Promoted

Do not promote:

- the selected `short_dominant` weights;
- the 5-second persistence half-life;
- the candidate LONG/SHORT side;
- the event-adjusted settlement probability;
- the saved research aggregators.

`selected_research_aggregators.joblib` does not include the fitted event-time heads and is marked
production-ineligible.

## Forward Requirement

The exact historical event-head models were not saved by the original trainer. Only their locked
predictions remain. Therefore, a genuine frozen forward experiment requires a new, separately
validated build that:

1. saves every event-head model, feature order, scaler, calibration map, base rate and input-range
   manifest;
2. verifies replay parity against stored predictions;
3. freezes those bundles before forward collection;
4. logs synchronized BTC events, source ages, Polymarket bid/ask/depth and official settlement;
5. forms independent episodes without changing the frozen rules;
6. measures incremental value over the anchor-distance baseline;
7. requires at least 500 episodes, eight weeks and 40 trading days;
8. evaluates executable post-fee EV and realistic delay.

Until that evidence exists, the result supports only the state-machine/episode architecture. It
does not support a directional or profitable trading claim.

## Validation

The validator confirmed:

- protocol, script and both prediction input hashes;
- exactly nine configurations;
- one candidate per market;
- finite bounded probabilities;
- exact 5m/15m scopes;
- one confirmed transition per candidate;
- positive time remaining at every candidate;
- no production write or eligibility;
- no economic-evidence claim.

Machine-readable validation:

`data/research/event_evidence_accumulator/20260728T053541Z/validation.json`

