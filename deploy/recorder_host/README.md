# Standalone capture host

Run the market-data recorders on a free always-on VM, so that in 2–3 months there is enough
paired data to answer the questions `research_lanes/TEST_INVENTORY.md` currently cannot.

**Read-only market data only. No credentials, no orders, no trading.** If a recorder ever needs
a key, it does not belong on this host.

---

## The problem this is designed around

The repo lost **35 days** of Polymarket capture (2026-07-04 → 08-09) and did not notice until an
analysis went looking. The official-settlement window fell entirely inside the hole, which
blocked `POLYMARKET_RESIDUAL_V1` until settlements were backfilled by hand.

Nothing was broken. Nothing was *watching*.

So the supervisor's real job is not running recorders — it is making a stopped recorder
impossible to miss. `--status` exits nonzero on any stale stream; wire that to anything that can
reach you.

## Quick start

```bash
git clone <repo> && cd BTC-Prediction-Tool
pip install -r backend/requirements.txt
python deploy/recorder_host/supervisor.py --run
```

Check health (this is what you alert on):

```bash
python deploy/recorder_host/supervisor.py --status   # exit 1 if anything is stale
```

As a systemd unit:

```ini
[Unit]
Description=BTC capture host
After=network-online.target

[Service]
WorkingDirectory=/home/ubuntu/BTC-Prediction-Tool
ExecStart=/usr/bin/python3 deploy/recorder_host/supervisor.py --run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then a cron entry that actually tells you:

```bash
*/15 * * * * cd /home/ubuntu/BTC-Prediction-Tool && python3 deploy/recorder_host/supervisor.py --status || <notify>
```

## Where to host it

| option | verdict |
|---|---|
| **Oracle Cloud Always Free** | **best** — 4 ARM cores, 24 GB RAM, 200 GB block storage, no 12-month clock |
| Fly.io / Render free tiers | sleep on idle — unusable for persistent WebSockets |
| GitHub Actions | 6-hour job cap — not a 24/7 host |
| AWS / GCP free tier | 12 months then billed |

Oracle does reclaim instances it judges idle, and people lose them. **Treat the VM as
disposable and the data as the asset**: sync to object storage on a schedule (Cloudflare R2 has
a free tier with no egress charge), so losing the box costs hours, not months.

## Storage, from this repo's actual accumulated data

| store | current size | note |
|---|---:|---|
| `deribit_options.duckdb` | 644 MB | 788,706 chain snapshots — the largest |
| `analytics.duckdb` | 429 MB | |
| `model_metrics.duckdb` | 95 MB | 2.3M `ptb_log` rows |
| `open_position_actions.duckdb` | 83 MB | |
| others | ~50 MB | |
| **total** | **~1.3 GB** | |

Three months of continuous capture at these rates lands in the low tens of GB, comfortably
inside Oracle's 200 GB — **except for sequenced Binance L2**, which is the one to size
deliberately. Full depth updates can reach GB/day uncompressed. Decide upfront whether you need
every update or a throttled snapshot; that single choice separates ~10 GB from ~500 GB per
quarter.

## One writer per store

Each recorder owns its own DuckDB. That isolation already exists and is worth keeping — the
live files are held open by their writers, which is why a naive copy fails mid-write. Do not
run analysis against a live store; snapshot or export first.

## What each stream unblocks

From `research_lanes/TEST_INVENTORY.md` — 24 lanes are blocked, and 23 of them are missing
capture rather than modelling:

| recorder | unblocks |
|---|---|
| `binance_l2_sequenced` | **eight lanes** — order-flow surprise, book elasticity, replenishment, vacuum distance, cancellation toxicity, and fill inference for the maker study |
| `pm_quotes_settlements` | the atlas, the residual re-run, any second-era check |
| `pm_l2` | executable size for full-set arb; the maker lane |
| `btc_ticks` + `binance_l2_sequenced` | whether a resting quote would have filled — the measurement that decides `HEDGED_POLY_MM_V1` |
| `multi_venue` | cross-venue leadership, synchronised shock, funding dispersion |
| `funding_basis` | carry, with the **real** schedule read rather than an assumed 8-hour cycle |
| `deribit_options` | the only independent probability source available |

## What this host does not do

No trading, no order placement, no credentials, no model training. It captures and it reports
whether it is still capturing. Analysis happens elsewhere, against exports — never against a
live writer.

On the shadow-quote question: inferring fills from `btc_ticks` + sequenced L2 is a measurement
and belongs here. *Posting* real maker orders is live capital, needs funded credentials, and
does not.
