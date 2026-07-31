# Frontier Review Reconciliation

Date: 2026-07-31

Status: **current evidence map, one completed replication, one new recorder**

## Executive Verdict

The four external frontier reviews are directionally sound about governance,
execution and capital safety, but their repository snapshot predates several
completed experiments. Their proposed work must not be treated as a fresh
backlog without reconciling it against current `master`.

The current conclusions are:

1. The claims that path information, complete-set arbitrage, funding carry and
   basic cross-market coherence were untested are stale. Those campaigns have
   been run and did not produce a promotable economic edge.
2. The claim that VPIN, signed flow, volatility term structure and shock
   features were never tested is stale. `research/path_information_test.py`
   tested them. Volatility/magnitude ranking survived; signed direction and the
   tested costed conversion did not.
3. A Polymarket market-prior residual had already failed in
   `ECONOMIC_V2_BLUEPRINT_RESULTS_2026-07-28.md`. The new campaign below is a
   stricter independent replication, not the first test of that idea.
4. The genuinely missing P0 dataset was a per-strike Deribit option chain.
   That recorder now exists and passed a real public-API smoke test.
5. No current result authorizes paper-policy promotion, live orders or capital
   scaling.

## POLY_MARKET_PRIOR_RESIDUAL_V1

### Why run a replication

The earlier Economic V2 campaign predicted an additive settlement residual
from multiple checkpoints. The external review proposed a more specific model:

```text
logit(p_model) = logit(p_market) + beta * residual_features
```

The replication freezes:

- one decision per market;
- the checkpoint immediately before 60 seconds remain;
- official CLOB/Gamma settlements only;
- a normalized Polymarket midpoint as the mandatory market baseline;
- expanding chronological day-block fitting with a 15-minute purge;
- anchor/time-only and full path/volatility residuals;
- recorded top ask, at least one displayed share, the canonical crypto taker
  fee formula and a fixed two-cent edge buffer;
- no artifact save and no paper/live order path.

Implementation:

- `research/polymarket_market_prior_residual_v1/frozen_protocol.json`
- `research/polymarket_market_prior_residual_v1/run.py`
- `tests/test_polymarket_market_prior_residual_v1.py`
- `research/launchers/run_polymarket_market_prior_residual_v1.bat`

The protocol and implementation were committed at `0f8eed0` before the
historical result was observed.

### Evidence population

| Item | Count |
|---|---:|
| Raw quote snapshots | 106,854 |
| Official settlements in the store | 2,298 |
| Trustworthy open-observed rounds | 527 |
| Valid fixed-60-second rounds | 439 |
| Chronological OOS rounds | 146 |
| OOS calendar days | 1 |
| Valid expanding folds | 1 |

The recorder has observations on several dates, but the frozen minimum of 150
training rounds excluded the preceding candidate fold by two rows. This is the
correct consequence of a frozen gate, not a reason to lower it after seeing
the data.

### Probability result

| Model | Brier | Log loss | ECE | AUC |
|---|---:|---:|---:|---:|
| Polymarket market prior | **0.1162** | 0.3485 | **0.0560** | 0.9187 |
| Anchor/time only | 0.1521 | 0.4586 | 0.0902 | 0.8669 |
| Market + anchor residual | 0.1195 | 0.3551 | 0.0951 | 0.9146 |
| Market + full residual | 0.1164 | **0.3442** | 0.0609 | **0.9249** |

The full residual improved log loss and AUC slightly but missed Brier by
0.0002. The predeclared primary gate required both Brier and log loss to beat
the market, so it failed.

The horizon split reinforces restraint:

- 5m full residual: Brier 0.1250 versus market 0.1302;
- 15m full residual: Brier 0.0877 versus market 0.0702;
- 15m has only 34 OOS rounds.

The pooled gain is not stable across horizons.

### Economic diagnostic

The full residual emitted 71 one-share actions:

| Metric | Result |
|---|---:|
| Coverage | 48.6% |
| Win rate | 73.2% |
| Average entry ask | 69.3c |
| Net PnL | +$1.97 |
| Mean PnL/action | +2.78c |
| Profit factor | 1.197 |

This is **not evidence of profit**:

- every action came from one OOS day;
- the day-block lower confidence bound is undefined;
- 5m produced +$3.35 while 15m produced -$1.38;
- the Brier gate failed;
- only 71 actions exist versus the frozen minimum of 100;
- the experiment has 146 OOS rounds versus 500 required;
- it has one day versus eight weeks required.

The correct interpretation is a hypothesis for new forward data, not a model
to deploy. It independently confirms the prior Economic V2 decision: the
Polymarket price remains champion.

Machine-readable outputs:

```text
data/research/polymarket_market_prior_residual_v1/latest/
```

## Deribit Per-Strike Recorder

### What changed

`backend/venues/deribit_option_chain_recorder.py` now records the public BTC
option chain into:

```text
data/deribit_options.duckdb
```

It stores:

- request and receive timestamps;
- Deribit `creation_timestamp` when supplied, without inventing one when it is
  absent;
- instrument, expiry, strike and call/put side;
- underlying index and price;
- bid, ask, midpoint and mark price;
- mark, bid and ask IV;
- open interest and volume;
- interest rate and estimated delivery price;
- batch duration, HTTP/RPC status, dropped-row count, schema version and Git
  identity.

It uses one public endpoint, no credentials and no order API.

### Validation

Deterministic tests passed:

- instrument parsing;
- missing exchange timestamp remains missing;
- malformed rows are counted and dropped;
- transactionally persisted call/put pair;
- ATM-straddle report construction;
- static absence of credential/private/order surfaces.

The real one-shot public smoke at commit `4321979` produced:

| Item | Result |
|---|---:|
| HTTP status | 200 |
| Stored rows | 942 |
| Dropped rows | 0 |
| Expiries | 13 |
| Calls / puts | 471 / 471 |
| Two-sided quoted instruments | 602 |
| Underlying price in snapshot | $64,303.94 |

This single batch proves collection and parsing only. It cannot test physical
versus implied volatility, straddle PnL, quote survival or hedging.

Run continuously:

```powershell
research\launchers\run_deribit_option_chain_recorder.bat
```

Inspect:

```powershell
research\launchers\report_deribit_option_chain_recorder.bat
```

## External Backlog Reconciliation

| Proposed item | Current status | Honest next condition |
|---|---|---|
| Clean artifact retrain/promotion | Not completed | Deliberate v14 retrain; preserve untouched gate |
| Exact account fees/rebates/filters | Partial | Import actual account/market economics before economic authorization |
| Universal opportunity ledger | Infrastructure partial | Forecast ledger exists; all eligible actions and `WAIT` are not yet wired into one population |
| Full-population abstention audit | Partial | Needs universal opportunity denominator |
| Counterfactual action value | Partial | Complete-trade schemas exist but live tables contain zero forecasts/outcomes |
| Total regret decomposition | Partial | Some paper opportunity-cost fields exist; no universal decomposition |
| Market-prior residual | **Tested twice; rejected** | Only a new independent forward period may reopen it |
| Cross-contract coherence | Basic lane tested negative | A same-resolution coherent strike surface needs richer compatible markets |
| Funding/basis carry | Current retail-taker test unattractive | Reopen only with longer varying funding archive or different execution economics |
| Capital-time/capacity | Partial | Exact-depth tools exist; no positive engine to size |
| Fill survival/markout | Blocked | Restart and accumulate gap-free sequenced L2 |
| Resiliency/toxicity | Blocked | Same L2 prerequisite |
| Complete-set inventory optimizer | Partial | Arithmetic arb tested negligible; inventory conversion lifecycle remains separate work |
| Optimal stopping/dynamic hedge | Stopping lane closed | No causal exit beat hold; options hedging needs option-chain history |
| Conditional calibration/conformal | Partial | Global/head calibrators exist; action-state conditional authority is incomplete |
| Edge half-life/latency | Infrastructure partial | Requires a positive forward candidate and continuous observations |
| Strategy co-failure portfolio | Infrastructure only | Requires at least two independently positive engines |
| Chaos/failure lab | Partial | Safety tests exist; full venue/order fault matrix remains |
| Real-order adapter canary | Intentionally absent | Strict production and economic gates must pass first |
| Controlled capital scaling | Not authorized | No positive engine, no canary and no forward lower bound |

## Prioritized Research From Here

1. Keep the Deribit chain recorder running. Forward time cannot be backfilled
   from aggregate ATM IV.
2. Restart the Polymarket and sequenced-L2 recorders. Their last durable writes
   predate this review and execution research cannot advance while they are
   stopped.
3. After enough Deribit history exists, freeze a physical-versus-implied
   volatility campaign with HAR-RV as champion and executable straddle ask as
   the economic hurdle.
4. Add adaptive conformal path bands only after at least 100 resolved path
   rounds per horizon.
5. Do not add another endpoint-direction model. The surviving information is
   magnitude/volatility, while settlement direction and market-price residuals
   have repeatedly failed.

No item above guarantees accuracy or profit. The value of this pass is that it
closes a duplicated hypothesis honestly and starts collecting the one missing
dataset aligned with the surviving magnitude signal.
