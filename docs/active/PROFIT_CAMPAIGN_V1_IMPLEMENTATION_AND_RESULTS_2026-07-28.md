# PROFIT_CAMPAIGN_V1 Implementation And Results

Date: 2026-07-28

Status: **implemented, executed, validated, research only, not promoted**

This document is the canonical record for the two requested Binance economic
campaigns:

1. `BINANCE_COST_AWARE_NET_PNL_V1`
2. `BINANCE_DYNAMIC_EXIT_V1`

Neither campaign changes the live app, paper engine, production models,
Polymarket logic, or order permissions. No real-order path was added.

## Final Verdict

The available exact L2 archive does not show a promotable taker strategy.

- The cost-aware q20 selector correctly chose `WAIT` for every untouched
  decision.
- Forced LONG, SHORT, random, momentum, and mean-reversion baselines all lost
  after executable spread, fees, depth, and impact reserve.
- The learned incremental-EV exit policy lost `$481.04` over 398 untouched
  trades.
- The learned exit policy underperformed identical-entry maximum hold by
  `$2.42`.
- No result has enough days, weeks, independent latency evidence, or forward
  evidence for promotion.

This is a useful negative result. It prevents gross-price movement from being
misreported as executable profit and confirms that abstention is preferable on
this archive.

## Final Reproducible Run

Command:

```powershell
.\research\launchers\run_profit_campaign_v1.bat
```

Final run directory:

```text
data/research/profit_campaign_v1/20260728T173101Z
```

Integrity result:

```text
validation=PASS
protocol_sha256=2ef293666b51e74333d429a1479556d89a6d1ebf5242c3577925243569b99aa0
registered_trials=240
cost_trade_rows_reconciled=7636
exit_trade_rows_reconciled=10480
production_permissions_changed=false
```

The generated data directory is intentionally ignored by Git. The runner,
frozen contract, validator, and this result record are maintained in Git.

## Frozen Economic Contract

The contract lives at:

```text
backend/research/profit_campaign_v1/frozen_protocol.json
```

| Field | Frozen value |
|---|---|
| Instrument | Binance `BTCUSDT` perpetual |
| Decision interval | 15 seconds |
| Forecast/hold horizons | 30, 180, 900 seconds |
| Latency surface | 100, 250, 500, 1000 ms |
| Primary latency | 500 ms |
| Capital surface | $100, $500, $1,000, $5,000, $10,000 |
| Primary capital | $1,000 |
| Taker fee | 5 bps per leg |
| Additional impact reserve | 1 bp per leg |
| Maximum book age/gap | 10 seconds |
| Minimum entry fill | 25% |
| Exit fill | 100% of filled entry quantity |
| Selector | Best LONG/SHORT q20 must exceed zero plus 2 bps reserve |
| Development/test | 80% development, 20% untouched chronological test |
| Walk-forward | 4 development folds |
| Purge/embargo | 900 seconds / 900 seconds |

Executable accounting is:

```text
LONG gross  = quantity * (exit bid VWAP - entry ask VWAP)
SHORT gross = quantity * (entry bid VWAP - exit ask VWAP)

net PnL = gross PnL
          - entry and exit taker fees
          - entry and exit impact reserve
          + observed funding cash flow
```

Midpoint fills are prohibited. Entries and exits walk the visible L2 ladder.
Partial entry fills are retained only when they meet the frozen minimum; exits
must cover the full filled entry quantity.

## Exact Source Data

The source is the public local archive:

```text
Kaggle Data/archive (5).zip
```

Funding is read from:

```text
Kaggle Data/archive (4).zip
```

Final normalized quality:

| Measure | Value |
|---|---:|
| Archive span | 23.9996 hours |
| Normalized healthy book states | 15,836 |
| One-second trade-flow rows | 17,213 |
| Fresh-book sessions at the 10s rule | 127 |
| Gaps over the 10s rule | 126 |
| Gaps over 60s | 125 |
| Maximum receive gap | 65.16s |
| Median received-book interval | 5,011ms |
| p90 received-book interval | 5,032ms |
| Crossed emitted books | 0 |
| Healthy emitted sequence fraction | 100% |

The archive batches received books approximately every five seconds.
Consequently, 100/250/500/1000ms latency cells frequently select the same next
observable book. The cells are saved, but they are not independent subsecond
latency evidence.

## Causal Book And Feature Rules

The reconstruction:

1. consumes snapshots and diff events in local receive-time order;
2. validates Binance update-ID continuity;
3. stops emitting after a sequence break;
4. resumes only from a valid snapshot;
5. groups exchange events delivered in the same recorder poll into one
   observable state;
6. emits only non-crossed, sequence-healthy books;
7. stores enough ladder depth for the largest frozen capital size.

Feature windows reset after any receive gap longer than ten seconds. A decision
must rebuild a complete 180-second causal feature history after a gap. A trade
is rejected when its entry, exit, or holding path crosses stale/missing book
evidence.

The 28 model features are:

```text
ret_5s_bps
ret_15s_bps
ret_30s_bps
ret_60s_bps
ret_180s_bps
rv_30s_bps
rv_60s_bps
rv_180s_bps
vol_acceleration
spread_bps
spread_z_60s
top_imbalance
depth_imbalance_20
imbalance_change_15s
trade_count_5s
trade_count_30s
trade_count_60s
trade_notional_5s
trade_notional_30s
signed_flow_5s
signed_flow_30s
signed_flow_60s
flow_imbalance_5s
flow_imbalance_30s
quote_interval_ms
exchange_receive_lag_ms
hour_sin
hour_cos
```

No future high, future low, future close, future spread, or future fill value is
present in the feature matrix.

## Campaign 1: Cost-Aware Net PnL

The campaign builds both LONG and SHORT executable labels for every decision
and horizon. It predicts:

- `P(net PnL > 0)`;
- net-PnL q10/q20/q50/q80/q90;
- entry-plus-exit slippage q10/q20/q50/q80/q90;
- time-to-first-positive-net-PnL q20/q50/q80 when observable;
- fill probability;
- probability that maximum favorable net PnL exceeds round-trip cost.

Models:

- `HistGradientBoostingRegressor` with quantile loss for continuous
  distributions;
- scaled logistic regression for binary probabilities.

Saved baselines:

- always LONG;
- always SHORT;
- random side;
- 60-second momentum;
- 60-second mean reversion;
- cost-aware selector;
- WAIT;
- current ensemble placeholder.

`CURRENT_ENSEMBLE` is explicitly unavailable because no archived current-model
predictions overlap the exact L2 date. It is not synthesized.

Final denominator:

| Item | Rows |
|---|---:|
| Causal decisions | 4,796 |
| Primary LONG/SHORT labels | 28,776 |
| Latency/capital/horizon/side surface | 575,520 |
| Aggregated capacity cells | 120 |

Fail-closed gap rejections:

| Horizon | Rejected labels | Main reason |
|---:|---:|---|
| 30s | 4 | stale exit book |
| 180s | 24 | stale exit or holding-path gap |
| 900s | 116 | stale exit, path gap, or no exit |

Untouched economic result:

| Horizon | Policy | Trades | Total net PnL | Mean net PnL | Profit factor |
|---:|---|---:|---:|---:|---:|
| 30s | Always LONG | 374 | -$445.99 | -$1.1925 | 0.0000 |
| 30s | Always SHORT | 374 | -$451.97 | -$1.2085 | 0.0000 |
| 30s | Momentum | 374 | -$453.02 | -$1.2113 | 0.0000 |
| 30s | Mean reversion | 374 | -$444.94 | -$1.1897 | 0.0000 |
| 180s | Always LONG | 80 | -$95.44 | -$1.1930 | 0.0000 |
| 180s | Always SHORT | 80 | -$96.71 | -$1.2089 | 0.0025 |
| 900s | Always LONG | 17 | -$17.98 | -$1.0579 | 0.0630 |
| 900s | Always SHORT | 17 | -$22.82 | -$1.3425 | 0.0338 |
| All | Cost-aware selector | 0 | $0.00 | n/a | n/a |

The selector's zero trades are correct abstention, not profitable skill. Its
PBO value cannot be quoted as an alpha result because it primarily measures
stable selection of WAIT.

## Campaign 2: Dynamic Exit

The campaign uses one identical, non-overlapping causal momentum-entry cohort
for every exit policy. An early exit never creates an extra replacement entry.
This prevents optional-stopping policies from receiving more opportunities than
maximum hold.

Entry paths:

| Horizon | Paths |
|---:|---:|
| 30s | 1,640 |
| 180s | 392 |
| 900s | 85 |
| Total | 2,117 |

Exit-state targets:

- incremental net-PnL q20/q50/q80 for waiting 5/15/30/60 seconds;
- `P(profit improves)` for each valid wait;
- `P(current profit disappears in 30s)`;
- `P(stop occurs before target)`;
- `P(volatility state changes in 30s)`;
- exit-slippage q20/q50/q80.

A wait target exists only when the full wait is observable. For example, a
60-second target is null when fewer than 60 seconds remain. It is never silently
shortened to expiry.

Policies compared:

1. static stop/target;
2. maximum hold;
3. trailing stop;
4. break-even stop;
5. profit lock;
6. model incremental-EV close;
7. model half-exit then hold;
8. opposing-momentum close.

Untouched result:

| Horizon | Policy | Trades | Total net PnL | Delta vs same-horizon hold |
|---:|---|---:|---:|---:|
| 30s | Maximum hold | 308 | -$370.69 | $0.00 |
| 30s | Model incremental EV | 308 | -$371.47 | -$0.78 |
| 180s | Maximum hold | 74 | -$89.43 | $0.00 |
| 180s | Model incremental EV | 74 | -$90.23 | -$0.80 |
| 900s | Maximum hold | 16 | -$18.51 | $0.00 |
| 900s | Model incremental EV | 16 | -$19.34 | -$0.83 |
| All | Model incremental EV | 398 | -$481.04 | -$2.42 |

The model policy's profit factor is `0.0`, q20 net PnL is `-$1.2001`, PBO is
`0.4167`, and deflated-Sharpe probability is `0.0`. These are one-window
diagnostics, not independent multi-day estimates.

## Logic Defects Found And Fixed

The final audit found and corrected:

1. the protocol's 10-second maximum book age was declared but not enforced;
2. 180s/900s simulations could bridge recorder resynchronization gaps;
3. lag and volatility features could carry pre-gap history into a new session;
4. entry-to-current exit features used the first post-entry mid instead of the
   actual entry mid;
5. a 60-second exit target silently became “until expiry” when less than 60
   seconds remained;
6. generated reports described a gapped archive as continuous;
7. code identity was absent from trial provenance;
8. reused result directories could load label caches from older simulator code;
9. generated trade files did not expose every gross/fee/impact/funding component;
10. no executable result-level reconciliation gate existed.

All ten are covered by code paths or adversarial self-tests. The final runner
also performs result-level validation before it can print success.

## Result Integrity

`validate_result.py` checks:

- protocol snapshot hash;
- implementation hash;
- source/data hashes;
- research-only and production-permission flags;
- exact q20 LONG/SHORT/WAIT reconstruction;
- trade-level `net = gross - fees - impact + funding`;
- non-overlapping exposures;
- identical path IDs in model-exit versus maximum-hold comparison;
- reported paired-PnL delta;
- unique, implementation-current trial-registry rows.

Final validation:

| Check | Result |
|---|---|
| Cost-aware trade rows reconciled | 7,636 |
| Exit-policy trade rows reconciled | 10,480 |
| Registered trials | 240 |
| Duplicate trial IDs | 0 |
| Production permissions changed | No |
| Overall | PASS |

## Files Implemented

```text
backend/research/profit_campaign_v1/__init__.py
backend/research/profit_campaign_v1/book_replay.py
backend/research/profit_campaign_v1/contracts.py
backend/research/profit_campaign_v1/cost_aware.py
backend/research/profit_campaign_v1/dynamic_exit.py
backend/research/profit_campaign_v1/execution.py
backend/research/profit_campaign_v1/features.py
backend/research/profit_campaign_v1/frozen_protocol.json
backend/research/profit_campaign_v1/models.py
backend/research/profit_campaign_v1/run_campaigns.py
backend/research/profit_campaign_v1/selftest.py
backend/research/profit_campaign_v1/validate_result.py
backend/research/profit_campaign_v1/validation.py
research\launchers\run_profit_campaign_v1.bat
```

CI runs:

```powershell
python -m backend.research.profit_campaign_v1.selftest
```

Manual result validation:

```powershell
python -m backend.research.profit_campaign_v1.validate_result `
  data/research/profit_campaign_v1/20260728T173101Z
```

## Promotion Decision

Both campaigns remain `RESEARCH_ONLY_INSUFFICIENT_EVIDENCE`.

Promotion is blocked by:

- one gapped 24-hour archive window;
- one trading day and one calendar week;
- no independent multi-day block-bootstrap interval;
- no eight-week forward paper evidence;
- subsecond latency not resolvable;
- no timestamp-aligned current-ensemble comparison;
- non-positive untouched economics;
- dynamic exit failing its paired maximum-hold comparison.

The next valid experiment requires multi-week, sequenced L2 data recorded at a
cadence that can distinguish the declared latency cells. Until then, the
correct production action is no change.
