# Deep Accuracy And Research Audit

Date: 2026-07-31

## Verdict

The application is safer and more causally correct after this pass, but it is
not currently production-ready and it has no proven profitable strategy.

The strongest measured information is about **movement size, path range and
risk**, not 5-minute or 15-minute endpoint direction:

- ensemble direction: 47.7% at 5m and 47.1% at 15m;
- Price-to-Beat direction: 49.0% at 5m and 48.0% at 15m;
- RF is the best selective base model in the stored sample, at 52.1%/56.0%,
  but the 15m support is only 334 calls and this is not enough to declare edge;
- volatility-term inversion predicts larger absolute moves after
  multiple-testing correction;
- the tested Binance breakout bracket cannot monetize that magnitude signal:
  all nine configurations lose after costs.

The right next target is therefore **calibrated physical volatility versus
executable option-implied volatility**, plus better risk and execution
forecasting. It is not another generic UP/DOWN model.

## Code And Logic Corrections

### Causal labels

`backend/features.py` and `backend/backtester.py` now use a causal
per-decision ATR threshold. The previous implementation calculated one
threshold from the dataset's final rows and applied it to old labels, allowing
the newest volatility regime to rewrite historical targets.

If both barriers are touched inside one OHLC bar, the direction is now
NEUTRAL/no-trade. A one-minute candle does not reveal which barrier came first,
so using its close to invent ordering was not defensible.

### Causal feature construction

Two future-information paths were removed:

1. `cvd_slope_divergence` used the standard deviation of the full cumulative
   CVD series. Appending a future shock changed past feature rows. It now uses a
   trailing causal scale.
2. Leading missing cross-asset values were filled from the first future
   observation. Leading gaps now remain missing-as-zero; only already-seen
   observations are carried forward.

`FEATURE_SEMANTICS_VERSION` is now 4.

### Honest training and calibration

`backend/model.py` now:

- calibrates XGBoost and LightGBM with purged chronological folds instead of
  shuffled stratified folds;
- excludes synthetic class-presence compatibility rows from calibration and
  OOF stacking;
- estimates class priors from training rows only;
- builds the PSI/reference distribution from training rows only.

`TRAINING_SEMANTICS_VERSION` is now 3.

### Feature ownership

The direction model now has 63 selected features instead of 69.

Removed:

- `regime_transition_prob`;
- `regime_entropy`;
- `vol_forecast_1m`;
- `vol_forecast_5m`;
- `vol_forecast_15m`;
- duplicate `mtf_support_distance`.

The five regime/volatility forecasts were live post-fit snapshots without a
causal historical series at direction-model training time. The support feature
duplicated `dist_to_support`. Removing them reduces train/serve ambiguity and
does not remove unique historical information.

The main model architecture is now:

```text
2026-07-31-v14-pruned63-864622d65e85-...
```

Old main-ensemble artifacts are intentionally incompatible.

### Promotion safety

Manual, scheduled, auto-learning and startup relearns now share the same
candidate-evaluation path. Trigger origin can no longer bypass holdout gates.

The promotion gate also requires at least 200 directional calls by default.
A 52% result based on a handful of calls cannot replace an incumbent.

When no compatible model exists, startup uses the promotion flow even if an
old retrain-completion marker cleared `BTC_FORCE_MAIN_RETRAIN`. The old case
could otherwise install a new architecture without the intended holdout gate.

### Live snapshot synchronization

The feature worker now receives one copied candle/order-flow/derivatives/
sentiment snapshot. It cannot combine state dictionaries updated at slightly
different points while feature construction is running.

### Research preprocessing

The shared Binance UP/DOWN research matrix no longer fills missing values with
full-dataset medians. Missing values remain in the evidence matrix; every
downstream fold must fit its imputer on training rows only.

The maintained anchor bakeoff and directional-big-move scripts now follow that
contract. Existing results produced from an older globally imputed matrix are
screening evidence and must be rerun before promotion.

### Deribit analysis semantics

The old Deribit summary mixed calls, puts and expiries in one strike-to-IV map.
It also labeled the strike with maximum open interest as "max pain", which is
not the max-pain calculation.

The corrected feed:

- chooses one expiry with at least 24 hours remaining when possible;
- keeps call and put IV surfaces separate;
- calculates ATM IV from both sides at the nearest strike;
- calculates the existing 5%-OTM skew proxy from the correct option sides;
- calculates max pain by minimizing total open-interest-weighted intrinsic
  payout across candidate settlement strikes;
- reports the selected expiry timestamp.

These options fields are retired from the direction model, so this correction
improves analysis/UI meaning and does not require a direction-model retrain.

## Current Evidence

### Direction

From `docs/active/DUCKDB_METRICS_ANALYSIS_2026-07-31.md`:

| Source | 5m | 15m | Interpretation |
|---|---:|---:|---|
| Main ensemble | 47.7% (n=1,282) | 47.1% (n=620) | no demonstrated direction edge |
| Price-to-Beat | 49.0% (n=3,304) | 48.0% (n=1,061) | coin-flip baseline |
| RF selective calls | 52.1% (n=858) | 56.0% (n=334) | research lead, not promotion evidence |

Confluence grade A did not beat B, and the model lean did not beat fallback in
the stored period. Confidence presentation must not be treated as proof of
accuracy.

### Head health

Current head-health output:

- P(Hold): n=6,725, ECE 0.0678, `CALIBRATION_ONLY`;
- flip risk: n=6,725, ECE 0.0655, `CALIBRATION_ONLY`;
- champion action tiers are not monotone in their presented order.

These heads may rank states; they may not price a real bet.

### Path head

The local path scorecard has only 12 resolved rounds. Current high-band
coverage is 0.17 against a nominal target near 0.50. That sample is too small
to diagnose the model, but it is enough to prohibit promotion or position
sizing from the displayed band.

### Magnitude research

`docs/PATH_INFORMATION_RESULTS.md` establishes:

- directional flow does not predict signed path excursions;
- clock-based exit timing is not learnable;
- `rv_term_inversion` predicts larger absolute movement and survives
  Bonferroni correction;
- the apparent asymmetric first-passage edge is structural and dies after
  costs;
- `BREAKOUT_BRACKET_V1` loses after costs in all nine configurations.

The magnitude signal is real statistical information. The tested Binance
instrument is not a profitable conversion.

### Structural-market research

The latest `docs/PATH_INFORMATION_RESULTS.md` additions close three more
apparently simple profit routes:

- complete-set UP+DOWN arbitrage exists in only 0.155% of synchronized quotes
  before costs, typically around ten shares and one cent ($0.10);
- the median complete-set book is 1.0100 ask / 0.9900 bid, a one-cent maker
  spread centered on fair settlement value;
- apparent 5m/15m cross-market inconsistencies disappear when the barrier order
  is known at adequate resolution: the unambiguous subset has 0% violations;
- retail-taker funding carry needs about 12 days merely to cover the modeled
  two-leg entry and exit cost, before basis risk.

These results reinforce two priorities: sequenced-L2 maker/fill research and an
instrument that directly pays on magnitude. They do not justify a taker
arbitrage or carry strategy.

## Remaining Blockers

### Runtime and artifacts

Strict preflight currently refuses service:

- the saved main ensemble is incompatible with v14;
- 0/11 specialist heads have a serviceable feature/provenance contract;
- 0/11 specialist heads have a valid training identity;
- complete-trade/path/execution artifacts are not verified champions;
- the qualifying archive span is still insufficient.

Do not manufacture manifests for old bytes. Retrain, evaluate and save new
artifacts through the manifest-writing path.

### Specialist bundle promotion

`backend/promote_challenger.py` verifies provenance, matrix coverage, monthly
quality, head health and atomic bundle publication. It does not yet require a
standardized challenger-versus-incumbent holdout report for each specialist
head.

Until that contract exists, a specialist bundle must not be manually promoted
merely because its manifests are valid. Valid provenance proves what an
artifact is; it does not prove that it is better.

### Path-production refit contract

The path trainer uses a chronological fit/calibration/test split, but its
post-gate production refit still retains the freshest 2% for calibration and
marks the result `refit_on_all`. That label is inaccurate, and the head does not
yet implement purged OOF calibration followed by a true 100% production refit.
Treat the current head as shadow information until this contract is upgraded
and measured.

## Research Campaigns

### P0 - Deribit executable volatility campaign

The newly confirmed magnitude signal needs an instrument that pays on
magnitude without requiring a directional stop entry.

**Recorder status update:** implemented in
`backend/venues/deribit_option_chain_recorder.py`. Its first public smoke
stored 942 BTC option rows across 13 expiries (471 calls and 471 puts), with
602 two-sided quoted instruments and zero parser drops. One batch validates
collection only; the volatility campaign remains blocked on forward history.

The public, read-only recorder stores:

- exchange and receive timestamps;
- instrument, expiry, strike and call/put side;
- underlying price;
- bid, ask, mark and mark IV;
- open interest and volume;
- source-response hash and sequence/gap state.

Then compare:

```text
physical move distribution from BTC path model
vs
option-implied move and executable straddle ask
```

Targets:

- realized variance through expiry;
- straddle terminal payoff;
- delta-hedged PnL after bid/ask, fees and hedge costs;
- maximum adverse/favorable PnL;
- quote survival and executable size.

Required controls:

- implied-volatility-only baseline;
- HAR-RV/GARCH baseline;
- same expiry/moneyness/liquidity buckets;
- latency and spread stress;
- day/week block confidence intervals;
- correction for all tested configurations.

The Deribit public API provides active instruments and option book summaries
without authentication:

- https://docs.deribit.com/api-reference/market-data/public-get_instruments
- https://docs.deribit.com/api-reference/market-data/public-get_book_summary_by_currency

This is a recorder/research proposal, not authorization to trade options.

### P0 - Incremental volatility head

Do not train another broad path ensemble first. Test whether new features add
lift over the current five-feature path head.

Baseline:

- HAR-RV or simple RV-term model;
- current `rv_15m`, `rv_30m`, `rv_60m`, compression and shock features.

Challengers:

- signed realized semivariance;
- bipower variation and jump variation;
- Lee-Mykland jump flags on one-second data;
- sequenced-L2 OFI and depth only when archive coverage is sufficient.

Targets:

- P(|move| >= threshold);
- range quantiles;
- time to touch;
- jump/cascade hazard.

Promotion requires incremental OOS Brier/log-loss/calibration improvement over
the baseline, not merely AUC above 0.5.

References:

- Corsi HAR-RV:
  https://statmath.wu.ac.at/~hauser/LVs/FinEtricsQF/References/Corsi2009JFinEtrics_LMmodelRealizedVola.pdf
- Lee-Mykland jump detection:
  https://public.econ.duke.edu/~get/browse/courses/201/spr11/DOWNLOADS/VolatilityMeasures/SpecificlPapers/lee_mykland_rfs_08.pdf
- Bitcoin spot/options volatility comparison:
  https://arxiv.org/abs/2010.07402

### P0 - Adaptive path intervals

Replace static conformal width with an online adaptive conformal shadow per
horizon and regime. It may adjust interval width, but it may not change the
point forecast or trade action.

Measure:

- rolling empirical coverage;
- average width;
- miss size;
- coverage after regime transitions;
- comparison with the unchanged static interval.

Reference:

- https://arxiv.org/abs/2202.07282

### P1 - Sequenced-L2 adverse-selection and fill research

The new Binance L2 archive supports deterministic replay, but not exact order
priority. Research should therefore predict conservative:

- quote survival;
- displayed-size fill bounds;
- post-fill adverse movement;
- queue depletion time;
- maker-to-taker fallback cost.

It must beat a queue-size/event-rate baseline. Do not introduce DeepLOB or
Hawkes before the simple baseline and archive-coverage gates pass.

Reference:

- https://arxiv.org/abs/1011.6402

### P1 - Calibration and abstention

Train a meta-failure/abstention challenger only from purged OOF and forward
evidence. Inputs may include:

- calibrated probability;
- interval width;
- regime transition risk;
- model disagreement;
- source age and sequence health;
- recent calibration error.

The objective is lower loss and better retained-call precision at a declared
coverage, not more BUY/SELL calls.

### P1 - Multiple-testing and dependency governance

Every campaign must record:

- all attempted model/feature/threshold variants;
- their correlation/dependency;
- selected and untouched periods;
- Deflated Sharpe/PBO where economic PnL is tested;
- strategy co-failure during volatile days.

The repository has many closed experiments. Ignoring that trial count would
overstate the significance of the next apparent winner.

## Stop List

Do not spend the next cycle on:

- another generic XGBoost/CatBoost/Transformer endpoint-direction vote;
- changing thresholds after looking at test results;
- promoting RF from the current 334-call 15m sample;
- converting magnitude into a Binance breakout bracket again;
- retesting taker complete-set arbitrage, noisy cross-market barrier ordering
  or retail-taker funding carry without structurally different execution;
- calling a rank-calibrated probability fair value;
- real-money execution before strict preflight and independent economic gates
  pass.

## Required Next Run

1. Run the manifest-writing v14 retrain.
2. Preserve the untouched 2% candidate evaluation.
3. Promote only if the main-ensemble gate passes.
4. Keep the full-data model in shadow until live paired evidence passes.
5. Re-run strict preflight.
6. Accumulate at least 100 resolved path rounds per horizon before diagnosing
   interval calibration.
7. Keep the Deribit chain recorder running before attempting an options campaign.

No code change in this audit guarantees accuracy, win rate or profit. The
changes remove ways the system could fool itself and focus new research on the
one signal family that currently survives honest testing.
