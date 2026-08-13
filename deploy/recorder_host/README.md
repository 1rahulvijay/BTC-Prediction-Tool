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

**Oracle ARM Always Free is effectively unobtainable.** The A1.Flex shapes return "Out of host
capacity" in most regions for weeks at a time, and the workaround people converge on is
upgrading to Pay As You Go — which is no longer free. Do not plan around it.

| option | free forever? | spec | verdict |
|---|---|---|---|
| **GCP `e2-micro` Always Free** | **yes** | 1 vCPU, 1 GB, 30 GB disk | **best remaining** — us-west1 / us-central1 / us-east1 only |
| **Oracle AMD Always Free** | yes | 2 x 1 GB micro | usually *available* when ARM is not, and you already have the account |
| Fly.io / Render / Railway | no | — | free tiers sleep or now require a card |
| AWS free tier | 12 months | — | then billed |
| **your own hardware** | yes | whatever you have | most robust; a Pi 4 or an old laptop is enough |
| Hetzner CX22 | ~EUR 4/mo | 2 vCPU, 4 GB | if free keeps failing, this ends the problem permanently |

### 1 GB will not run all seven — and that is a correctness issue, not a comfort one

Each Python + DuckDB recorder is roughly 80-150 MB resident. Seven is 700 MB-1 GB before the
OS, so a 1 GB box swaps or gets OOM-killed. **An OOM-killed recorder is exactly the silent hole
this supervisor exists to prevent** — you would reproduce the 35-day gap, just with a different
cause.

Run a subset chosen by what it unblocks:

```bash
python deploy/recorder_host/supervisor.py --run    --max-tier 1   # ~3 processes, fits 1 GB
python deploy/recorder_host/supervisor.py --status --max-tier 1   # alert on the same subset
```

| tier | recorders | needs | why this cut |
|---|---|---|---|
| **1** | `binance_l2_sequenced`, `pm_quotes_settlements`, `btc_ticks` | ~1 GB | sequenced L2 + ticks is the fill inference that decides `HEDGED_POLY_MM_V1`, the only lane whose upper bound has not failed. PM quotes+settlements is the pair whose gap blocked the residual lane. |
| 2 | adds `pm_l2`, `funding_basis` | ~2 GB | PM ladder depth for executable size; the real funding schedule rather than an assumed 8h cycle |
| 3 | adds `multi_venue`, `deribit_options` | ~4 GB + disk | venue breadth and the option chain. `deribit_options` alone is 644 MB of the current 1.3 GB — do not put it on a 30 GB disk without a retention policy |

If you can only run one tier, run tier 1 on GCP `e2-micro` and keep tier 3 on your own machine
where disk is cheap.

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
