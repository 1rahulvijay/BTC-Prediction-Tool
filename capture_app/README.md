# capture_app — standalone market-data recorder

Records Binance sequenced L2, Binance trades, and Polymarket books to hour-partitioned parquet.
Built to run on a **GCP `e2-micro` Always Free** box (1 vCPU, 1 GB RAM, 30 GB disk) and to rotate
old data out before the disk fills.

**It does one thing: record.** No trading, no credentials, no order placement, no model loading.
Verified to import nothing from `backend/`, `research_lanes/` or `deploy/` — it is a separate
program that happens to live in the same repository.

---

## Measured, not estimated

A 60-second live capture against Binance:

| stream | rows/min | bytes/row | |
|---|---:|---:|---|
| `binance_depth` | 8,357 | 6.8 | zstd parquet |
| `binance_trades` | 210 | 25.3 | |

| projection | |
|---|---:|
| **90 MB / day** | depth + trades, BTCUSDT |
| **2.7 GB / 30 days** | |
| **8.1 GB / 90 days** | |
| **~278 days** | retained locally on 30 GB with 5 GB headroom |

**Full-fidelity diff L2 fits comfortably in the free tier.** The 500 GB/quarter figure people
quote is for all symbols and uncompressed; one symbol with zstd is two orders of magnitude
smaller. There is no reason to throttle.

Polymarket books add on top and vary with how many rounds are live; it is small relative to
depth.

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
| `binance_depth` + `binance_trades` | fill inference — the measurement that decides `HEDGED_POLY_MM_V1`, the only lane whose upper bound has not failed. Also order-flow surprise, book elasticity, replenishment, vacuum distance, cancellation toxicity |
| `polymarket_book` + `polymarket_settlement` | the state-value atlas (underpowered at 10 days), the residual re-run, any second-era check, executable size for full-set arb. **Recorded together, which is the fix for the zero-intersection failure** |

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
