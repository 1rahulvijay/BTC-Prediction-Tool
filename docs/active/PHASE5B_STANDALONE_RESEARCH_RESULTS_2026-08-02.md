# Phase 5B Standalone Research Suite

Date: 2026-08-02

Status: implemented, executed, audited, and paper-only

Canonical run:

`data/research/phase5b_standalone/_suite_runs/20260802T_phase5b_100k_v4/suite_summary.json`

## Decision

All 46 requested Phase 5B experiments, numbered 43 through 88, now have a standalone
directory, frozen protocol, runner, self-test, and experiment README under
`research/phase5b_standalone/`.

The canonical real-data pass completed all 46 processes. It produced:

| Result | Count | Meaning |
|---|---:|---|
| `PASS_CANDIDATE` | 0 | Nothing is eligible for Champion, paper-strategy, or capital promotion. |
| `FAIL_UNSTABLE` | 21 | Interesting or descriptive result, but no robust executable edge. |
| `FAIL_NO_EDGE` | 8 | The frozen comparison did not show useful incremental information. |
| `FAIL_AFTER_COSTS` | 3 | Gross behavior existed, but declared costs removed it. |
| `INSUFFICIENT_SAMPLE` | 4 | The available independent history was below the frozen gate. |
| `BLOCKED_DATA` | 10 | The causal source needed to answer the question does not exist yet. |

This suite does not change the live application, model serving, paper-trading authority,
Champion decisions, or capital authority. Every report contains `capital_authority=false`.

## Research Controls

- Inputs are loaded read-only through allowlisted contracts.
- Modeled tests use chronological TRAIN 55%, CALIBRATION 15%, POLICY_SELECTION 15%, and
  UNTOUCHED_TEST 15% partitions, with a purge gap.
- Thresholds and model choices are locked before the untouched test is scored.
- Economic tests use the declared Binance or Polymarket cost model and expose 1.0x, 1.5x,
  and 2.0x cost stress where execution is modeled.
- Diagnostic AUC, clustering, calibration, or correlation cannot produce
  `PASS_CANDIDATE` without executable after-cost economics.
- Missing timestamps, source columns, action arms, fills, or independent day/week blocks
  fail closed.
- Each experiment runs in a separate process, so memory is released before the next test.
- The canonical campaign capped each source at its newest 100,000 rows. It is a broad
  real-data validation pass, not a claim that every multi-million-row source was exhausted.

## Complete Result Ledger

| # | Experiment | Result | Canonical evidence and conclusion |
|---:|---|---|---|
| 43 | Forecast revision path | `BLOCKED_DATA` | Per-checkpoint forecasts are not joined to immutable model release and settlement. |
| 44 | Forecast revision overshoot | `BLOCKED_DATA` | Causal forecast jumps and 30/60/120-second retracement paths are not preserved. |
| 45 | Forecast stability versus accuracy | `BLOCKED_DATA` | Stability, settlement, and executable entry price do not coexist in one causal record. |
| 46 | Minority-model correctness | `FAIL_NO_EDGE` | 3,180 disagreement groups; minority selector AUC 0.4996 and zero accuracy lift over majority voting. |
| 47 | Shared-information false consensus | `FAIL_UNSTABLE` | Six high-coverage models behave like 4.93 independent models. Mean prediction correlation is 0.147 and mean error correlation is 0.341; per-model feature lineage is missing. |
| 48 | Time-to-expiry calibration | `FAIL_NO_EDGE` | Bucket-calibrated P(Hold) Brier 0.1618 versus better market Brier 0.1410. The model did not beat the market probability. |
| 49 | Confidence-collapse hazard | `BLOCKED_DATA` | Continuous, release-bound per-model revisions are absent. |
| 50 | Prediction freshness decay | `BLOCKED_DATA` | Model-specific 1/5/15/30/60/120-second markouts are absent. |
| 51 | Market-state novelty gate | `FAIL_AFTER_COSTS` | The novelty veto improved net PnL by 0.0796 versus its base, but final net PnL was -0.1023 and profit factor 0.07. |
| 52 | Local sample support | `FAIL_NO_EDGE` | 51-neighbor analogue probability produced untouched AUC 0.5031. |
| 53 | Feature-sign stability | `FAIL_UNSTABLE` | Five sign reversals appeared across month, session, trend, volatility, and week-part slices. Descriptive only. |
| 54 | Worst-environment model selection | `FAIL_NO_EDGE` | Worst-environment accuracy lift was zero and no executable threshold policy was established. |
| 55 | Feature-value drift | `FAIL_UNSTABLE` | Monthly effect slopes and sign changes are available for monitoring, not direct feature retirement. |
| 56 | Information-time clock | `FAIL_NO_EDGE` | Combined information AUC 0.6946 versus volatility-clock AUC 0.6980, a -0.00345 lift. More features did not beat the simpler clock. |
| 57 | Information exhaustion | `FAIL_UNSTABLE` | Untouched AUC 0.6988 predicts lower future activity, but no causal executable policy or after-cost edge exists. |
| 58 | Event burstiness | `INSUFFICIENT_SAMPLE` | The 100,000-row slice spans 0.017 day; the complete qualifying event archive spans only about 0.95 day, below the five-day gate. |
| 59 | Resilience after aggressive flow | `FAIL_UNSTABLE` | Mean retained markout was 0.795 bps; spread recovery 50.7% and depth recovery 20.3%. This does not clear execution costs. |
| 60 | Bid/ask replenishment asymmetry | `FAIL_UNSTABLE` | Ask replenishment 82.3%, bid replenishment 80.7%; top-of-book snapshots cannot prove maker queue fills. |
| 61 | Spread-shock asymmetry | `FAIL_UNSTABLE` | Shock absolute markout 5.17 bps versus matched 2.36 bps, but direction and executable net edge were not established. |
| 62 | Microprice markout | `FAIL_AFTER_COSTS` | Signed markout grows from 0.093 bps at 1s to 0.419 bps at 30s, far below the declared 9 bps round-trip hurdle. |
| 63 | Buy/sell impact asymmetry | `FAIL_UNSTABLE` | Proxy impact asymmetry was 0.665 bps. OFI is not exact aggressive notional and no net action rule passed. |
| 64 | Toxic-flow veto | `FAIL_AFTER_COSTS` | The veto added zero net PnL; final net was -0.5496 with profit factor below 0.01. |
| 65 | Path-efficiency ratio | `FAIL_NO_EDGE` | Untouched AUC 0.5289 did not clear 0.55. |
| 66 | Trade-sign entropy | `INSUFFICIENT_SAMPLE` | AUC 0.589 on the capped slice, but the entire event archive is below five independent days. |
| 67 | Path roughness versus terminal outcome | `FAIL_NO_EDGE` | Path-augmented direction AUC 0.5233, only +0.00192 over the frozen baseline. |
| 68 | Volatility-of-volatility transition | `FAIL_UNSTABLE` | Untouched AUC 0.7083 for a shock label. Useful state information, but no executable after-cost direction policy. |
| 69 | Anchor pinning versus escape | `FAIL_UNSTABLE` | State-classification accuracy beat the majority baseline by 0.165, but entry prices and executable economics were not proved. |
| 70 | Probability stickiness | `FAIL_UNSTABLE` | Extreme-to-central elasticity ratio 0.144. Extreme prices are stickier, but no locked action rule exists. |
| 71 | YES/NO expression asymmetry | `FAIL_UNSTABLE` | Direct-side purchase was cheaper in about 1% of rows; complement shorting/redeem mechanics were not established. |
| 72 | Token-liquidity persistence | `FAIL_UNSTABLE` | Depth persistence was 0.43 UP and 0.59 DOWN; spread persistence was about 0.15. Routing information is not alpha. |
| 73 | Polymarket quote lead/lag | `BLOCKED_DATA` | No atomic, clock-admissible BTC-event and paired-token L2 join exists. |
| 74 | Polymarket response decomposition | `FAIL_UNSTABLE` | Withdrawal/addition/trade accounting is descriptive; paired-token mechanical repricing remains unidentified. |
| 75 | Settlement-source basis hazard | `BLOCKED_DATA` | Continuous official settlement-reference observations are unavailable. |
| 76 | Sequential value of information | `FAIL_NO_EDGE` | Waiting lost 0.9795 versus act-now; final net PnL -24.289 and profit factor 0.799 on 867 untouched decisions. |
| 77 | Skip-reason economic value | `FAIL_UNSTABLE` | 10,676 resolved decisions were grouped, but avoided-loss and opportunity-cost fields are estimates rather than executable counterfactual fills. |
| 78 | Data-quality-conditioned performance | `FAIL_UNSTABLE` | Checkpoint-age surfaces were computed, but recorder restart proximity and clock skew are absent from the atomic row. |
| 79 | Model-error taxonomy | `FAIL_UNSTABLE` | 1,937 wrong-direction and 8,739 unclassified failures; causal path/fill telemetry is needed for the missing classes. |
| 80 | PnL source attribution | `FAIL_UNSTABLE` | 250 paper trades, total PnL 0.6148, fees 4.0029 and spread proxy 2.11. Attribution fields are incomplete for scaling. |
| 81 | Capital efficiency | `FAIL_UNSTABLE` | Mean PnL per capital-minute was -0.000148 across 250 paper trades. |
| 82 | Online regime discovery | `FAIL_UNSTABLE` | KMeans separated future absolute-return states, but cluster separation alone is not a strategy. |
| 83 | State-transition graph | `FAIL_UNSTABLE` | KMeans next-state accuracy 0.5754; no economic action mapping passed. |
| 84 | Horizon consistency | `INSUFFICIENT_SAMPLE` | 132 near-aligned 5m/15m/30m snapshots existed, but all 132 were NEUTRAL. Zero directional rows means no valid divergence test. |
| 85 | Candidate-evidence completeness | `BLOCKED_DATA` | Canonical per-decision candidate evidence does not exist. |
| 86 | Counterfactual action-arm completeness | `BLOCKED_DATA` | HOLD/EXIT/REDUCE/SWITCH/LOCK values are not recorded at the same causal timestamp. |
| 87 | Recorder-gap selection bias | `INSUFFICIENT_SAMPLE` | 274 episodes were inspected; all were excluded and zero qualified for a healthy-versus-gap comparison. |
| 88 | Timestamp-uncertainty stress | `BLOCKED_DATA` | Candidate PnL markouts at 100/250/500ms and 1/2s pessimistic delays are unavailable. |

## Findings That Matter

### Do not promote

- P(Hold) calibration does not beat the Polymarket market price on the untouched period.
- Ensemble minority selection is coin-flip behavior and does not improve majority voting.
- Local analogues, path efficiency, and path roughness do not improve direction.
- Microprice and toxic-flow effects are much smaller than full execution costs.
- The sequential wait model is worse than acting immediately and both require more robust
  independent evidence.
- No Phase 5B result is approved for live or paper Champion wiring.

### Keep as research or monitoring signals

- Information exhaustion AUC 0.699.
- Volatility-of-volatility shock AUC 0.708.
- Anchor path-state classification lift 0.165.
- Learned regime transition accuracy 0.575.
- Liquidity persistence, spread-shock, calibration-by-expiry, drift, and error-taxonomy
  surfaces are useful diagnostics, not trade instructions.

These values are hypotheses for later executable tests. They are not expected returns and must
not be displayed as BUY/SELL confidence.

## Audit Corrections

Earlier Phase 5B smoke and v2/v3 reports are superseded by the v4 campaign.

The final audit found that `str(value or "")` treated numeric side value `0` as missing. Because
the recorder encodes DOWN as `0`, this removed DOWN-led checkpoints or scored them incorrectly.
The normalizers now preserve `0 -> DOWN`, and a regression test covers integer and floating-point
zero. Correcting it changed two important conclusions:

- Time-to-expiry calibration changed from an apparent model advantage to a valid
  `FAIL_NO_EDGE`: model Brier 0.1618 versus market Brier 0.1410.
- Sequential value of information changed to `FAIL_NO_EDGE`: waiting lost 0.9795 versus
  act-now and had negative untouched net PnL.

Additional audit fixes made before v4:

- `NEUTRAL` is an abstention, not a directional vote or agreement.
- Path roughness predicts terminal 5-minute direction and is compared to the same baseline.
- Information clock is compared against calendar, volatility, trade-count, and volume clocks.
- Readiness tests validate required Parquet columns when a blocked artifact appears.
- Sequential value-of-information policy selection and reporting use the requested cost stress.
- Ensemble-independence analysis uses high-coverage core models and aligned correctness rows.

## Missing Data Needed To Continue

Post-campaign implementation status is tracked in
`docs/active/PHASE5_EVIDENCE_SUBSTRATE_2026-08-02.md`.

1. Persist every model revision with release ID, horizon, prediction timestamp, and outcome.
   **Implemented for the live main ensemble; forward collection begins after restart.**
2. Persist model-specific 1/5/15/30/60/120-second markouts.
   **Implemented for the main ensemble with actual observation latency; data has not accrued yet.**
3. Build an atomic BTC-event plus paired Polymarket token L2 clock join.
4. Record the official settlement reference continuously around the anchor.
5. Materialize canonical candidate evidence with exact quote, state, action, costs, and outcome.
   **Builder implemented; production export remains blocked until ledger rows exist.**
6. Record same-timestamp HOLD/EXIT/REDUCE/SWITCH/LOCK counterfactual paths.
7. Record latency-stressed candidate PnL at 100/250/500ms and 1/2s.
8. Keep multi-venue and recorder episode collection running for at least five independent days,
   then for the predeclared day/week robustness gates.

## Validation Commands

```powershell
python -m compileall -q research\phase5b_standalone
python -m pyflakes research\phase5b_standalone
python -m pytest -q research\phase5b_standalone\test_phase5b_suite.py
python research\phase5b_standalone\run_all.py --selftest
python research\phase5b_standalone\run_all.py --smoke --maximum-rows 100000
```

Use `--run` only with the application and model training stopped. Some sources contain millions
of rows and a full pass can exceed a 16 GB laptop's practical memory budget.

## Final Interpretation

Phase 5B improved the research platform by making 46 questions reproducible and fail-closed. It
did not discover a profitable strategy. The strongest next work is recorder and evidence quality,
followed by predeclared executable tests. Adding these diagnostic AUCs to the live ensemble would
be overfitting and would reduce, not improve, confidence in the system.
