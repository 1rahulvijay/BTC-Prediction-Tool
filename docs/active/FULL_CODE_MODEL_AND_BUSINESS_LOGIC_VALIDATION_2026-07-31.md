# Full Code, Model And Business-Logic Validation

Date: 2026-07-31

Source audited: `master` at `8ba94e7`

Purpose: record what was actually executed and inspected before the next long-window retrain. This
is a source, contract, safety and paper-accounting audit. It is not a claim that an unavailable
model is accurate, that a paper fill will occur live, or that any strategy is profitable.

## Verdict

The maintained source tree is internally consistent and passes its complete local validation gate.
The Windows launcher, frontend, model contracts, causal labels, model/head permissions, forecast
ledger, paper execution, accounting, risk controls and fail-closed serving behavior all passed.

The application is suitable to start for **retraining, recording, shadow forecasting and paper
trading**. It is not currently suitable for trusted model serving or real-money production:

- the saved main ensemble is v11 and is correctly rejected by the current v14 contract;
- 0 of 11 standalone serving artifacts have a current serviceable identity manifest;
- production preflight reports 17 blockers and starts no server;
- strict artifact identity cannot default on while 14 legacy raw-load sites remain;
- no strategy has demonstrated robust forward post-cost profitability;
- no real-order adapter is implemented or authorized.

## Executed Validation

| validation | result |
|---|---|
| canonical local CI | PASS, 71/71 steps, 243.1 seconds |
| exact Windows `start.bat` self-test-only path | PASS, 170.3 seconds; no server or training started |
| Python compilation | PASS, all 485 Python files under `backend/`, `research/` and `tests/` |
| maintained Python static checks | PASS |
| pytest | PASS, 5 tests |
| frontend production build | PASS |
| frontend high-severity dependency audit | PASS, 0 vulnerabilities |
| launcher path/control-flow integrity | PASS, 61 invoked paths |
| repository launcher layout | PASS, 42 research and 4 test launchers |
| documentation feature/model contract | PASS, 136 raw / 63 model features, hash `864622d65e85` |
| mutable-default AST scan | PASS, 0 findings |
| duplicate top-level definition AST scan | PASS, 0 findings |
| blocking calls inside async-function AST scan | PASS, 0 findings |

The global Python installation does not pass `pip check`: unrelated packages in the shared
environment require incompatible Starlette, Packaging and PyArrow versions. This does not change
the source-test result, but it is a deployment blocker. Production must use the dedicated
`requirements-prod.txt` virtual environment required by preflight.

The launcher preflight measured 445 GB free disk and found the cross-venue and trade-feature
backfills cover 1,289 and 1,291 days respectively, exceeding the 1,265-day request. No completion
marker exists, so the next normal launch correctly forces one full retrain. Self-test mode did not
download, train or start either server.

## Model And Head Logic

The following executable contracts passed:

- training target alignment and class mapping;
- 63-feature serving/training mask and feature semantics;
- trailing/time-based VWAP causality;
- TCN per-sample weighting and stable inference device;
- causal regime forward filtering, one update per closed bar and train/serve regime parity;
- artifact hash verification before deserialization on migrated serving paths;
- fail-closed head permissions and typed model-unavailable reasons;
- challenger provenance, monthly coverage, policy hash, dataset hash and atomic promotion gates;
- P(Hold) calibration default-off behavior and required-mode refusal;
- round-state corrupt/stale reload refusal;
- frozen complete-trade artifact pinning and verified champion-pointer swaps;
- no post-expiry exact-price targets and no missing-value-to-neutral substitution;
- one complete-trade action per independent round, with the ranked and settled unit matched.

Current artifact checks are expected to fail and did fail safely:

| check | result | interpretation |
|---|---|---|
| main ensemble compatibility | exit 5, v11 saved versus v14 required | retrain required; stale bundle is not served |
| specialist feature/identity gate | exit 1, 0/11 serviceable | retrain and manifest publication required |
| current P(Hold) calibrator | not deployable | raw probability cannot receive pricing authority |

The main model predicts triple-barrier DOWN/NEUTRAL/UP classes for 5m and 15m. A separate regressor
estimates move size. These outputs are probabilistic estimates, not exact candle promises. Historical
research continues to show direction near coin-flip more often than not; path/range/magnitude can be
more predictable but have not established executable post-cost edge.

## Paper Trading And Business Logic

The Binance paper engine passed end-to-end tests for:

- disabled-by-default startup and inability to create orders while off;
- LONG and SHORT position accounting;
- spread, fees, slippage, funding and partial-liquidity handling;
- close-before-flip behavior for opposing signals;
- pending, submitted, acknowledged, partial, filled, rejected and unknown order states;
- idempotent settlement, duplicate-event handling and durable restart recovery;
- transactional fill/order/account mutation and rollback on failure;
- leverage, notional, loss, drawdown, cooldown and duplicate-position gates;
- stale/missing feed refusal, kill-switch behavior and emergency paper flattening;
- reduce-only permission during degraded states without allowing position flips;
- control-token authentication, explicit CORS and no default credentials.

The complete-trade evidence path passed real DuckDB write/read/resolve/evaluate tests. It refuses
partial evidence, missing net P/L, mixed run identities, reused holdout evidence, unverified bundles
and invalid candidate rows. Evidence writes are append-only, monitored and dead-lettered on failure.

No code path was found that can submit a real Binance or Polymarket order. The trading-authority
suite confirms that authority is absent by default and bounded capabilities are required even for a
future adapter.

## Residual Debt And Non-Claims

These items remain and must not be described as completed:

1. **Artifact migration:** 39 raw save sites and 14 raw load sites remain, primarily research and
   offline tooling. The ratchet prevents growth, and active serving paths are verified, but strict
   repository-wide identity cannot be enabled until the load count reaches zero.
2. **Legacy research hygiene:** full-tree Pyflakes reports unused imports/variables and seven bare
   exception handlers in quarantined/probe scripts. All files compile; these scripts are not serving
   or order paths. They should be cleaned when a research lane is reopened, not treated as promoted
   evidence now.
3. **Compatible artifacts:** the next long-window retrain must generate current manifests, pass the
   purged untouched-tail gate, refit accepted models on all admitted data, publish to staging,
   smoke-test and require explicit promotion.
4. **Forward evidence:** model accuracy, calibration, fills, queue behavior and profitability remain
   unproven until enough independent live outcomes accrue.
5. **Data coverage:** the event archive is about 0.95 day, the sequenced Binance L2 archive is absent,
   the liquidation stream has no qualifying events, and the Deribit chain history is stale/short.
6. **Exact passive fills:** public aggregate L2 cannot reveal exact order priority. Any maker fill
   remains a conservative queue simulation until reconciled against forward orders.
7. **Profitability:** deterministic tests prove arithmetic and refusal logic, not trading edge.

## Required Post-Training Checks

Run these after the long-window training completes and before trusting a model output:

```powershell
python backend\check_model_compatibility.py
python backend\check_feature_contract.py --enforce-serving
python backend\promote_challenger.py --challenger data\saved_models_challenger_1265d --days 1265
python backend\production_readiness.py --mode paper
```

Only apply promotion after the dry run clears every frozen gate. Continue paper/shadow verification
after promotion because a full-data production refit no longer owns an untouched test tail.

## Audit Conclusion

No maintained-path compile, target-alignment, model-permission, forecast-ledger, paper-accounting,
risk-control, authentication or frontend-build defect remains from this audit. The code fails closed
where current artifacts or deployment prerequisites are missing. The next legitimate step is the
compatible retrain and forward paper evidence, not enabling real orders or claiming guaranteed
accuracy.
