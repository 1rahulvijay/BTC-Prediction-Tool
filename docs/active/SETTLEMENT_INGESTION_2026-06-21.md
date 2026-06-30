# Polymarket Official Settlement Ingestion - 2026-06-21

## Status

**Implemented and validated.** The persisted backlog contained 364 expired BTC 5m/15m markets.
The recovery run resolved **364/364**, leaving zero pending settlements.

| Metric | Result |
|---|---:|
| Persisted round metadata | 364 |
| Official settlements | 364 |
| CLOB-sourced outcomes | 364 |
| Proxy/fallback outcomes | 0 |
| UP outcomes | 189 |
| DOWN outcomes | 175 |
| Quote snapshots | 4 |
| Distinct quoted rounds | 4 |

The last two rows are the limiting factor. An outcome without a quote cannot prove a tradeable edge.

## What Changed

- Closed markets are resolved from `https://clob.polymarket.com/markets/{condition_id}` using the
  explicit winning token. Gamma `closed=true&slug=...` is an official fallback.
- Binance-at-resolution is no longer accepted as a settlement label. It sampled after expiry and could
  disagree with Polymarket's Chainlink settlement rule.
- Unresolved rounds are loaded from `pm_round_meta`, so process restarts no longer lose settlement work.
- `pm_settlement_attempts` provides retry pacing without starving newer or older pending rounds.
- Pyth is the recorder's primary BTC reference; Binance is a labelled fallback (`price_source`).
- `start.bat` and `start_instant.bat` now launch `start_recorder.bat` automatically. Set
  `BTC_SKIP_PM_RECORDER=1` only for deliberate offline work.
- `--settle-once` repairs a backlog without starting the continuous recorder.

## Analyzer Corrections

`analyze_pm_recorder.py` now:

1. uses the P(Hold) and UP/DOWN ask captured in the same recorder tick;
2. handles numeric `current_side` correctly (the old code always selected the DOWN ask);
3. accepts only official CLOB/Gamma outcomes;
4. counts at most one entry per market (first threshold crossing), preventing repeated snapshots from
   inflating trade count and ROI;
5. falls back to periodic Parquet exports while the recorder owns the DuckDB writer lock.

Current edge result: four joined quote rounds, zero signals clearing even a 1-cent buffer. This is
**insufficient evidence**, not proof that the strategy works or fails. The predeclared gate remains a
positive after-cost result at >=3 cents over at least 500 joined rounds.

## Commands

```powershell
# Continuous collection (normally auto-started with the app)
.\start_recorder.bat

# Recover expired persisted rounds, then exit
python backend\polymarket\live_btc_updown_recorder.py --settle-once

# Counts / quick scorecard
python backend\polymarket\live_btc_updown_recorder.py --report

# Profit-edge proof
python backend\polymarket\analyze_pm_recorder.py
```

## Validation

- Python compile: pass.
- Recorder isolated self-test: pass.
- Analyzer ROI/buffer/one-entry-per-round self-test: pass.
- Real recovery: `attempted=364 resolved=364 remaining=0`.
- Source audit: `polymarket_clob=364`; no proxy settlements.

## Other Current Decisions

- Overall P(Hold) post-retrain is stable: n=79,019, ECE 0.0093.
- 1m P(Hold) remains overconfident: n=11,971, ECE 0.0533; high tiers are 2.6-5.0 points optimistic.
- The regime gate remains shadow-only. Latest recent-250 RANGE/LOW_VOL accuracy is 46.2% with
  Wilson lower bound 35.7%, so it fails promotion despite a marginal full-history result.
