# Correctness and Evidence Remediation

Date: 2026-08-09  
Scope: model context, replay honesty, Binance paper authority, Polymarket settlement truth,
recorder health, and operator visibility.

## Executive verdict

The repaired code fails closed where evidence is missing. It does not establish a profitable
edge and it does not authorize real-money trading. The next startup may train models and run
paper engines, but promotion still requires untouched forward evidence and executable-policy
economics.

## Implemented

### 1. Horizon-specific regime weights reach inference

`backend/decision_snapshot.py` now includes `regime_model_weights_by_horizon`. Before this fix,
the server populated the map but the immutable snapshot stripped it, so inference silently used
the static fallback. The snapshot selftest pins the field and its nested value.

### 2. Settlement-head uncertainty uses dependence blocks

`backend/server.py` groups each horizon by a wall-clock block spanning `LOOKBACK + horizon`.
`backend/settlement_head.py` accepts horizon-specific groups and computes predeclared confidence
buckets with a grouped-bootstrap 95% lower observed-hit-rate bound. Sparse buckets are withheld.
The probability payload carries `confidenceLower95`, `uncertaintyMethod`,
`uncertaintyBucket`, and `independence_validated`.

These values become available only after a new compatible retrain. They validate an endpoint
probability bucket; they do not prove strategy PnL.

### 3. Binance paper entry requires exact policy evidence

`backend/binance_paper/strategies/model_consensus.py` is now `paper-v2`. It may use the grouped
probability lower bound for diagnostics, but entry requires positive `policyValueLowerBps`,
`policyValueMethod=policy_cluster_bootstrap_95`, and
`policyValueId=model_consensus:paper-v2`.

Those fields must come from a replay of the exact stop, target, latency and cost policy. Until
that producer exists and passes its gate, the strategy returns `NO_EDGE`. The explicit research
override remains non-production and disabled by default.

### 4. Historical backtest cannot be read as production proof

`backend/backtester.py` declares its model and policy scope, uses the configured 12 bps cost in
the server path, and scores the full already-isolated untouched tail after warm-up. Every result
is stamped `valid_for_promotion=false` because this replay omits production quality gates,
locks, sizing, executable fills and exits.

### 5. Polymarket terms are captured per market

`backend/polymarket/live_btc_updown_recorder.py` records the exact market description,
resolution URL, fee-enabled flag, fee rate and required TWAP source. Boolean strings are parsed
correctly, so `"false"` no longer becomes true. Live API verification on 2026-08-09 confirmed:

- 5m: Chainlink BTC/USD 30-second TWAP stream;
- 15m: Chainlink BTC/USD 60-second TWAP stream;
- fee-enabled crypto markets with the 0.07 category rate.

The recorder also consumes Polymarket RTDS Chainlink BTC/USD updates as a causal reference
feature. Generic RTDS is explicitly not settlement truth.

### 6. Exact settlement truth is quarantined unless it reconciles

The recorder creates strict `round_settlement_truth` and `settlement_checkpoint` tables. A row is
admissible only when the market rule explicitly defines equality, metadata names the expected
TWAP stream, exact-source boundary values exist, and the derived outcome agrees with the official
outcome. Missing/changed terms, generic RTDS, spot fallbacks and disagreement are quarantined.
Atomic Parquet exports replace silent failures, and `pm_export_health` records export state.
Until a broader boundary-report policy is empirically validated, strict truth requires the
source timestamp to equal the boundary exactly; nearest-before/after reports are quarantined.

### 7. Exact-truth health is visible

The recorder writes `data/pm_exact_truth_health.json` atomically. `/api/system-health` exposes it
as optional `polymarket_exact_settlement_truth`; the UI displays its explicit summary. It is not
a global blocker because Binance operation does not depend on Polymarket labels.

### 8. Recorder diagnostics no longer hide failures

`backend/venues/multi_venue_recorder.py` already collected Binance `forceOrder` liquidations and
correctly excluded that event-driven stream from continuous 9/9 health. Funding/OI poll failures
and venue-clock write failures are now visible with one-minute rate limiting. The older recorder
audit delegates to canonical timestamp-based health and preserves distinct failure states.

## Requires a new retrain

- grouped settlement-head confidence intervals;
- artifacts carrying current feature/training identity;
- compatible current-architecture ensemble and support artifacts.

Retraining does not manufacture the exact Binance policy-value interval or Polymarket TWAP
settlement labels. Those are separate evidence gates.

## Still evidence-gated

### Exact Polymarket settlement model

The code knows which TWAP stream is required but does not have authenticated historical/live
access to sponsored Chainlink TWAP reports. Until those values are ingested, strict truth remains
blocked/quarantined and the exchange-close endpoint head remains a research proxy with no
Polymarket pricing authority.

### Exact Binance policy replay

No current producer replays `model_consensus:paper-v2` with its exact entry, stop, target,
latency, spread, fees and slippage, then produces a day/group-clustered lower net-value bound.
The strategy therefore abstains by design.

### Forward profitability

Accuracy, precision and profit are not guaranteed by these repairs. Promotion still needs fresh
recorder data, sufficient calendar coverage, calibrated live outcomes, positive cost-adjusted
expectancy, and paper/live execution agreement. Gates must not be relaxed after seeing results.

## Validation performed

- Python compileall: clean for `backend`, `tests`, and `research`.
- Pyflakes on every changed Python file: clean.
- Pytest: 28 passed.
- Target-contract selftest: 57 passed.
- Decision-snapshot selftest: 29 passed.
- Settlement-head selftest: 34 passed.
- Settlement wiring: 27 passed.
- B/C readiness: 42 passed.
- Recorder health: 18 passed.
- Multi-venue recorder, collector-integrity and Binance paper-engine tests: passed.
- Artifact, promotion, paper-accounting and evidence-health selftests: passed.
- Vite production build: passed.
- Live Gamma metadata parser: 5m and 15m rules, sources and fees recognized.
- Live RTDS smoke: fresh BTC/USD reference updates received.
- `start.bat` with `BTC_SELFTEST_ONLY=1`: every launcher invariant passed; no app, recorder,
  backfill or training process was started.
- 1,000-day preflight: `REBUILD` mode, about 1,300 days of derived-source coverage, and free
  disk above the 80 GB rebuild floor.

## Expected behavior after `start.bat`

1. Compatible artifacts load; incompatible or unmanifested artifacts are refused.
2. A required retrain uses the staged release path rather than being trusted in place.
3. Binance `model_consensus` may display predictions but does not enter until exact `paper-v2`
   policy-value evidence exists.
4. Polymarket quotes and official outcomes may collect while exact TWAP truth remains blocked.
5. `DO NOT TRUST SIGNALS` remains correct whenever required feeds, recorders or models are bad.

Official references:

- Polymarket RTDS: https://docs.polymarket.com/market-data/realtime-data
- Polymarket fees: https://docs.polymarket.com/trading/fees
