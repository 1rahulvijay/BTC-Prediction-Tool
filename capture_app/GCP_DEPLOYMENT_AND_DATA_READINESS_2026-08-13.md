# GCP Capture Deployment and Data Readiness

Date: 2026-08-13

## Objective

Run the read-only capture service continuously at the lowest cost that preserves event ordering,
durable history and enough raw evidence for Binance-derivatives and Polymarket research. This
collector has no order credentials and cannot trade.

## Current Blockers

1. GCP project `btc-capture` has `billingEnabled: false`.
2. The only visible billing account is closed. Reopen it or create/link an active account.
3. Pyth Hermes authentication becomes mandatory on 2026-08-18. Obtain a Pyth API key before that
   date; do not replace Pyth with Binance under the same source name.

Official references:

- GCP free-tier limits: https://cloud.google.com/free/docs/free-cloud-features
- Cloud Storage pricing: https://cloud.google.com/storage/pricing
- Reopen billing: https://cloud.google.com/billing/docs/how-to/close-or-reopen-billing-account
- Pyth Hermes access: https://docs.pyth.network/price-feeds/core/api-instances-and-providers/hermes

## Cheapest Defensible Architecture

```text
Public exchange/oracle APIs
        |
        v
one Compute Engine collector
  e2-micro first, capacity-gated
  30 GB pd-standard
        |
        v
hour-partitioned Parquet + ZSTD
        |
        v
private regional GCS bucket
  Standard 0-90d
  Coldline 90-365d
  Archive 365d+
```

The e2-micro is an experiment, not an approval. Full 100 ms depth plus Polymarket L2 can exceed a
shared-core budget. `collector_runtime` records loop lag, CPU, RSS and disk every five seconds. If
the dedicated 24-hour soak reports sustained pressure or missing hours, resize to `e2-small`; do
not discard timing fidelity to remain free-tier.

## Data Contract

Captured raw public evidence:

- Binance spot/perpetual sequenced snapshots, deltas and aggressor trades;
- Binance mark/index/funding, OI, positioning, realized funding and liquidations;
- Bybit perpetual top-of-book, trades, OI and realized funding;
- Coinbase BTC-USD bid/ask/last and 24-hour market fields;
- Polymarket both-token L2, market events, trades, exact metadata and final settlements;
- Polymarket RTDS Binance/Chainlink references plus independent Pyth price/confidence;
- Deribit full BTC option surface by expiry and strike;
- collector resource and continuity telemetry.

Every causal cross-venue row preserves exchange publication time, collector UTC receive time,
collector monotonic time and connection/session identity. Missing is never encoded as zero or
neutral.

Not obtainable from a public read-only recorder:

- your real maker queue position;
- order acknowledgements, rejects, partial fills and cancels;
- private fees/rebates tied to your account;
- historical L2 before collection began.

Those require a separate authenticated order-audit stream when paper/live execution begins. Public
L2 supports realistic simulation, not proof that your own order filled.

## Deployment Commands After Billing Is Enabled

Use a globally unique bucket name. The project number makes this candidate likely to be unique:

```powershell
$env:CAPTURE_GCS_BUCKET = "btc-capture-573767148691"
$env:PYTH_API_KEY = "YOUR_PYTH_KEY"
$env:PYTH_ENDPOINT = "https://pyth.dourolabs.app/hermes/v2/updates/price/latest"

& "C:\Program Files\Git\bin\bash.exe" capture_app/deploy_gcp.sh bucket-create
& "C:\Program Files\Git\bin\bash.exe" capture_app/deploy_gcp.sh create
& "C:\Program Files\Git\bin\bash.exe" capture_app/deploy_gcp.sh status
& "C:\Program Files\Git\bin\bash.exe" capture_app/deploy_gcp.sh logs
```

The script refuses resource creation while billing is disabled. It creates a private bucket,
public-access prevention, lifecycle rules, a no-delete GCS writer identity, a systemd service,
15-minute status checks and hourly parquet-quality checks. Pyth credentials are copied to a
root-only environment file and are never committed or stored in instance metadata.

## Mandatory 24-Hour Acceptance Gate

Do not call the deployment evidence-grade until all are true:

1. `python capture_app/run.py --status` exits zero.
2. `python capture_app/run.py --quality` exits zero.
3. No required stream has a missing UTC hour.
4. Binance spot/perpetual reconstruction reports zero unresolved sequence gaps.
5. `collector_runtime` has no sustained `resource_pressure`; inspect lag distribution, not only
   the latest row.
6. At least one completed 5m and 15m Polymarket market has metadata, both-token quotes and official
   settlement joined by exact identifiers.
7. A real `--archive-once` and `--verify-archive` pass against GCS.
8. Stop/start the service once and confirm buffered rows were flushed and capture resumed in a new
   session without corrupt files.

If capacity fails, resize without deleting the disk:

```powershell
gcloud.cmd compute instances stop btc-capture --zone us-central1-a
gcloud.cmd compute instances set-machine-type btc-capture --zone us-central1-a --machine-type e2-small
gcloud.cmd compute instances start btc-capture --zone us-central1-a
```

## Training and Profit Rules

- Train from an immutable GCS catalog generation copied to a research workspace, never from files
  currently being written.
- Build causal features after capture; retain raw rows permanently.
- Split by time with purging/embargo and keep a recent untouched test period.
- Include actual bid/ask, fee, slippage, latency and fill assumptions in every economic label.
- Continue forward shadow validation after any full-data refit.
- Do not authorize capital because data collection is comprehensive. Authorize only a strategy
  whose forward, cost-adjusted, day-clustered lower bound is positive under real settlement and
  execution evidence.

No recorder or model can guarantee monthly income. This deployment removes data shortage as a
known avoidable failure mode; it does not manufacture predictive edge or profit.
