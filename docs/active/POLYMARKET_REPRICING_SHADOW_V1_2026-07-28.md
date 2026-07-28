# Polymarket Repricing Shadow V1

Date: 2026-07-28

Status: implemented and validated as an isolated forward-shadow recorder.
No production, paper-trading, settlement-side, eligibility, or position-sizing
behavior consumes its output.

## Purpose

The surviving event-time result is narrow:

> Causal Binance event features improve prediction of whether the already
> selected Polymarket ask worsens by at least one cent within five seconds.

This result does not predict final UP/DOWN settlement and does not establish
profit after fills. `POLYMARKET_REPRICING_SHADOW_V1` tests only whether that
forecast can route an existing eligible order more cheaply than always taking
the current ask.

## Frozen Responsibility Split

```text
Settlement/P(Hold) model
    selects UP, DOWN, or WAIT
        |
Economic edge gate
    decides whether an original candidate is eligible
        |
Repricing shadow
    observes the same candidate and compares execution routes
```

The repricing score cannot:

- flip UP to DOWN or DOWN to UP;
- create a candidate rejected by the economic gate;
- change requested position size;
- submit an order;
- update the production paper ledger.

## Research Inputs

### Original candidate

The existing read-only Polymarket recorder atomically publishes:

- exact market and condition IDs;
- selected side from the existing `BUY_UP_SHADOW`/`BUY_DOWN_SHADOW` decision;
- executable bid, ask, spread, and complete public ladders;
- P(Hold)-derived UP/DOWN settlement probabilities;
- three-cent-buffer expected edge;
- anchor distance, time remaining, and quote provenance.

Only a fresh positive-edge 5-minute candidate is admitted. The same candidate
ID is written for all four routing policies.

### Event model

The standalone event bundle uses 86 causal, one-second Binance spot/perpetual
features:

- cyclical hour and weekday;
- spot/perpetual basis;
- 1/3/5/10/30/60-second spot and perpetual returns;
- perpetual lead and basis change;
- aggressive-flow ratio, divergence, and agreement;
- log volume and trade intensity;
- 10/30/60-second realized RMS volatility and spot range.

For each 5-second and 15-second horizon, four models are fit sequentially:

- Logistic Regression;
- HistGradientBoosting;
- LightGBM;
- CatBoost.

They produce calibrated direction, movement, and round-trip probabilities.
The portable bundle has 24 fitted members across six heads. It is trained from
2026-04-12 through 2026-05-11, contains research-only activation flags, and is
cryptographically bound to the frozen protocol.

### Contract repricing models

The UP and DOWN contract heads come from the portable canonical campaign:

`data/research/event_execution_v1/20260728T063909Z`

Each artifact includes:

- a current-book/anchor/time baseline probability;
- the event-enhanced evidence probability;
- isotonic calibration;
- a fixed feature schema.

The shadow records both probabilities so forward incremental Brier score and
log loss can be measured instead of assuming the historical improvement
persists.

## Four Same-Denominator Policies

| Policy | Frozen behavior | Promotion scope |
|---|---|---|
| A baseline | take the current executable ask | control only |
| B urgency | take now and tag high/low worsening risk | control only |
| C maker-first TTL2 | high risk takes now; low risk tries one tick inside for 2s, then crosses | only future paper candidate |
| D size-aware TTL5 | rejects abnormal books; otherwise high risk/thin depth takes now, low risk tries maker for 5s | diagnostic only |

Policy D remains outside promotion scope because skipping a bad book can alter
whether the eligible candidate receives a fill. The only promotable question
in V1 is maker-first versus immediate taker.

## Persistence

The shadow owns a separate database:

`data/research/polymarket_repricing_shadow_v1/shadow.duckdb`

Tables:

- `repricing_shadow_meta`: frozen protocol, model hashes, contract input-manifest
  hash, ordered feature-schema hash, development cutoff, source run, Git commit
  and dirty-worktree state;
- `repricing_candidates`: original candidate, both baseline/evidence
  probabilities, features, and complete decision ladder;
- `repricing_routes`: all four route states, limit, TTL, fill quantity, fee,
  latency, and fallback;
- `repricing_observations`: nominal and actual elapsed time plus ask/depth/ladder
  snapshots at +1s, +2s, and +5s;
- `repricing_settlements`: official Polymarket resolution imported by candidate.

Every unfilled or partial route remains in the denominator. A missed winning
fill receives an explicit missed-opportunity penalty. Settlement PnL and
execution improvement are both compared with the same immediate-taker
candidate.

New provenance fields are recorded when the shadow starts. Databases produced
before this schema enrichment remain valid campaign evidence, but the universal
forecast adapter blocks their admission because present-day provenance cannot
be assigned retroactively.

## Reports

`report.py` writes:

- `repricing_calibration.csv`;
- `repricing_probability_deciles.csv`;
- `routing_policy_metrics.csv`;
- `resolved_route_detail.parquet`;
- `delay_stress.csv` and its detail parquet;
- `size_depth_stress.csv`;
- `gate_status.json`.

Probability reports compare baseline versus event evidence independently for
UP and DOWN:

- AUC;
- Brier score;
- log loss;
- observed one-cent worsening rate;
- average ask change;
- probability-decile monotonicity.

Execution reports are split into `ALL`, `UP`, and `DOWN` cohorts for each
policy. This permits one contract side to pass calibration while the other
fails, without changing the original candidate denominator.

## Frozen Forward Gates

No paper-routing eligibility is possible unless every applicable check passes:

- at least 1,000 independent candidates;
- at least 500 UP and 500 DOWN candidates;
- at least eight continuous weeks;
- evidence Brier and log loss improve over baseline for the evaluated side;
- decile monotonicity is at least 0.50;
- positive mean execution improvement after missed-fill penalties;
- day-block 95% lower confidence bound above zero;
- at least 75% positive weeks;
- no week contributes more than 35% of positive improvement;
- +1s and +2s stress observations are present;
- median observation timing lag is no more than one second;
- 10-share depth is complete for at least 95% of original candidates;
- the fixed untouched period beginning 2026-09-08 remains positive;
- the policy is `C_MAKER_FIRST_TTL2`.

Passing `gate_status.json` still does not activate anything. A separate reviewed
promotion change would be required.

## Commands

Train or rebuild the isolated event bundle explicitly:

```powershell
.\train_polymarket_repricing_shadow.bat
```

Run the existing public Polymarket recorder in one terminal:

```powershell
.\start_recorder.bat
```

Run the repricing shadow in another terminal:

```powershell
.\run_polymarket_repricing_shadow.bat
```

Generate the forward report:

```powershell
.\report_polymarket_repricing_shadow.bat
```

The shadow launcher fails closed if any model artifact is absent, stale against
the frozen protocol, or has unsafe activation flags. It never auto-trains or
falls back to another model.

## Validation Completed

- Ruff lint: clean for both research packages.
- Python compile: clean.
- Recorder schema/self-test: pass.
- 86-feature offline/live numerical parity: pass.
- Four-policy same-candidate denominator: pass.
- Baseline/evidence probability persistence: pass.
- Calibration, delay, size, route, and gate math: pass on deterministic data.
- Production isolation AST scan: pass.
- Portable E07/E08 artifact load: pass.
- Research event bundle load and protocol hash: pass.
- Public spot/perpetual feed five-second smoke: pass.
- Empty forward report: returns `no forward candidates recorded`, not an error.

## Known Limits

1. A passive fill is currently a conservative touch proxy: a maker limit is
   counted only when a later executable ask reaches that limit. Historical
   queue position, cancellations, and trades-ahead are not reconstructed.
2. The bridge is REST-polled, so nominal +1s/+2s observations may arrive later.
   Actual elapsed time is persisted and timing quality is a hard gate.
3. Complete public depth is retained in the shadow ledger, but fill probability
   still needs queue-aware L2 evidence.
4. The executable archive and current shadow are 5-minute only.
5. Forward evidence count is initially zero. Historical AUC is not forward
   execution profit.

LSTM work remains deferred. It is justified only after V1 shows repeatable,
achievable execution improvement and then only as an incremental comparison
against the current repricing score.
