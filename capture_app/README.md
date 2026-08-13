# capture_app — standalone market-data recorder

Records Binance sequenced L2, Binance trades, and Polymarket books to hour-partitioned parquet.
Built to run on a **GCP `e2-micro` Always Free** box (1 vCPU, 1 GB RAM, 30 GB disk) and to rotate
old data out before the disk fills.

**It does one thing: record.** No trading, no credentials, no order placement, no model loading.
Verified to import nothing from `backend/`, `research_lanes/` or `deploy/` — it is a separate
program that happens to live in the same repository.

---

## Measured, not estimated

Rates from live captures this session. **All nine streams:**

| stream | rows/min | MB/day |
|---|---:|---:|
| `futures_depth` | 31,173 | **305** |
| `polymarket_book` | ~2,000 | 130 |
| `binance_depth` (spot) | 8,357 | 82 |
| `futures_trades` | ~300 | 11 |
| `binance_trades` (spot) | 210 | 8 |
| `futures_mark` | 60 | 3 |
| `futures_liquidations`, `futures_open_interest`, `polymarket_settlement` | low | <1 |
| **TOTAL** | | **~539 MB/day** |

| horizon | size | |
|---|---:|---|
| 30 days | 16.2 GB | fits |
| 60 days | 32.4 GB | **exceeds a 30 GB disk** |
| 90 days | 48.5 GB | |

**A 25 GB cap holds about 46 days locally.** So the 2–3 month target is reachable only with
rotation to object storage — which is what `--archive-older-than` plus the disk guard are for.
Upload weekly, mark archived, let the guard reclaim. The data is never lost; it just stops
living on the box.

Dropping spot depth saves 82 MB/day and buys ~9 more days. Not worth it: perp-spot basis needs
both legs, and basis is an input to several open lanes.

**Full-fidelity diff L2 is still the right call.** Throttling would halve the volume and
permanently foreclose fill inference, which is the one open question left. Rotation solves
capacity; throttling solves nothing and costs the answer.

## Run it

```bash
pip install -r capture_app/requirements.txt

python capture_app/run.py --record                  # capture
python capture_app/run.py --status                  # exit 1 if any stream is stale
python capture_app/run.py --disk                    # usage and partition list
python capture_app/run.py --archive-older-than 24   # mark partitions deletable
```

systemd:

```ini
[Unit]
Description=BTC capture
After=network-online.target

[Service]
WorkingDirectory=/opt/btc
ExecStart=/usr/bin/python3 capture_app/run.py --record
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Alerting — this is the part that matters:

```bash
*/15 * * * * cd /opt/btc && python3 capture_app/run.py --status || <notify>
```

## Settlements are recorded by the SAME process as the quotes

This is not an implementation detail — it is the reason the fetcher lives here rather than in a
separate job.

The project previously ran a quote recorder and a settlement fetcher at different times. Result:
**916 rounds of quotes across ten days, 6,725 officially settled rounds across a different twenty
days, and an anchor intersection of exactly zero.** Both halves existed. Neither was usable,
because a residual model needs the price *and* the outcome for the same round.

Two processes with independent lifetimes will eventually diverge. One process cannot.

### Terminal outcomes are write-once

A settlement is a fact. Once recorded it is never rewritten, and re-fetching the same round is a
no-op. Protection is structural, not conventional:

- a persisted index of `(slug, horizon)` already written, which survives restart
- append-only partitions
- if upstream later reports a **different** outcome for a round already recorded, the fetcher
  writes a row to `polymarket_settlement_conflict` and **keeps the original**. It never applies
  the change silently.

This repo has already had a defect where a prediction writer could flip `resolved` back to
`FALSE` on a settled row. The lesson taken here is that terminal state needs a mechanism, not a
convention.

### It refuses to guess

A market can be *closed* without being *finalised*. Only a clean 1/0 `outcomePrices` pair is
accepted as a settlement. Anything else — mid prices like `0.62/0.38`, wrong arity, unexpected
labels — is counted as unresolved and retried later. Recording a guess as an outcome would
poison the research set in a way no downstream statistic could detect.

Verified offline (Gamma is unreachable from the dev machine, so this is logic-tested, not
live-tested): 13 checks covering slug parsing, six refusal paths, and write-once persistence
across restart.

## Why sequenced diffs and not `depth20` snapshots

A snapshot shows a level went 50 → 30. It cannot say whether 20 was **cancelled** or **traded**.
Queue position only advances on the second. So snapshots structurally cannot answer *"would my
resting order have filled"* — the single open question left in this project after 21 research
lanes.

Diffs plus trades can. And you can always derive snapshots from diffs; never the reverse. The
cheap option permanently forecloses the question, which is why this records `@depth@100ms` with
`U`/`u` update ids rather than `depth20@100ms`.

**Sequence gaps are recorded, not hidden.** If `final_id(prev) + 1 != first_id(next)` the book is
no longer reconstructable and every downstream queue calculation is wrong. That row is flagged
`gap=True`. A reconstructed book with an unnoticed hole is worse than an admitted gap. The
60-second test recorded **0 gaps**.

## The rotation rule

**Only ARCHIVED partitions are ever deleted.** The disk guard will not touch a partition lacking
its `.archived` marker — if the cap is hit and nothing is safely removable it keeps recording,
sets `blocked: true`, and prints loudly. Deleting un-uploaded data to stay under a cap would
manufacture exactly the silent hole this design exists to prevent.

Workflow:

1. upload partitions older than N hours to R2/GCS/wherever
2. `--archive-older-than N` to mark them
3. the guard reclaims them when the cap is approached

`protect_recent_hours` (default 6) additionally shields the newest partitions even when marked,
since they may still be receiving writes.

## Layout

```
data/<stream>/date=YYYY-MM-DD/hour=HH/part-*.parquet
state/<stream>.json          liveness, row counts, gap counts
state/diskguard.json         usage, what was reclaimed, blocked flag
```

Hour partitions make a gap **visible as a missing directory**, rather than as an absence
discovered months later during analysis. That is precisely how this project lost 35 days of
Polymarket capture — the data was simply not there, and nothing said so at the time.

## What each stream unblocks

From `research_lanes/TEST_INVENTORY.md`, where 23 of 24 blocked lanes are missing capture rather
than modelling:

| stream | unblocks |
|---|---|
### Verified on live data

| check | result |
|---|---|
| spot depth sequencing | 0 gaps over 95s |
| futures depth sequencing | 0 gaps over 45s **after fixing the `pu` rule** |
| **book reconstruction, futures** | 1,043 bid / 1,006 ask levels, best 63,850.1 / 63,850.2, **not crossed** |
| **book reconstruction, spot** | 111 bid / 100 ask levels, spread 0.01, **not crossed** |
| partition rotation | 3 hour-partitions, archived-only deletion, `keep_hours` honoured |
| settlement logic | 13 checks — parsing, six refusal paths, write-once across restart |

Replaying the recorded diffs produces a coherent, uncrossed book. That is the prerequisite for
fill inference, and it is now demonstrated rather than assumed.

**Futures continuity uses `pu`, not the spot rule.** On spot, consecutive events satisfy
`U == prev_u + 1`. On USD-M futures they do not — the stream carries an explicit `pu` and the
contract is `pu == prev_u`. The first version applied the spot rule to futures and reported
**962 gaps in 95 seconds that did not exist**, which would have made every futures book look
unusable. Measured on a live message pair: spot rule `False`, `pu` rule `True`. Fixed, and gaps
went 962 → 0.

### Not verified from the dev machine

`futures_trades` (`@aggTrade`) and `futures_mark` (`@markPrice@1s`) **time out from this
network**, in isolation, while `futures_depth` on the same host works. The stream names match
Binance's documented format and the code path is shared with streams that do work, so this looks
like a local/regional block rather than a defect — but it is unproven. **Confirm on the GCP box**
that both show climbing row counts in `--status`; if they stay at zero while `futures_depth`
climbs, check `state/futures_*.json` for `last_error`.

Polymarket is in the same position: Gamma returns 403 here, so discovery and settlement parsing
are logic-tested only.

## What each stream unblocks

| `binance_depth` + `binance_trades` | fill inference — the measurement that decides `HEDGED_POLY_MM_V1`, the only lane whose upper bound has not failed. Also order-flow surprise, book elasticity, replenishment, vacuum distance, cancellation toxicity |
| `polymarket_book` + `polymarket_settlement` | the state-value atlas (underpowered at 10 days), the residual re-run, any second-era check, executable size for full-set arb. **Recorded together, which is the fix for the zero-intersection failure** |
| `futures_liquidations` | `LIQUIDATION_EXHAUSTION_V1` |
| `futures_open_interest` | `POSITIONING_STATE_MACHINE_V1`, `FUNDING_OI_CROWDING_V1` |
| `futures_mark` | `FUNDING_BASIS_CARRY_V1` (the actual rate and its **next funding time**, read not assumed), `MARK_INDEX_LAST_DISLOCATION_V1` |
| `futures_depth` + `binance_depth` | `MICROBASIS_REVERSION_V1` — a perp-spot spread needs both legs |

## Configuration

`config.json`. No credentials belong in it, ever.

| key | default | |
|---|---|---|
| `binance_symbol` | `BTCUSDT` | one symbol at full fidelity beats five throttled |
| `data_dir` | `capture_app/data` | |
| `cap_gb` | 25 | leaves headroom on a 30 GB disk |
| `protect_recent_hours` | 6 | never reclaim the live edge |
| `stale_seconds` | 300 | a stream quiet longer than this is DOWN, not slow |
| `streams.*` | all true | disable individually |

## Deliberate non-capabilities

No trading. No credentials. No orders. No model loading. No writes anywhere near the trading
app's stores. Analysis runs elsewhere, against copied partitions — never against a live writer.

On shadow quoting: inferring fills from depth + trades is a **measurement** and belongs here.
*Posting* real maker orders is live capital, needs funded credentials, and does not.
