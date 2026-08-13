# Standalone BTC/Polymarket Capture App

This program records public market data for later research, testing, feature construction and
model training. It is deliberately independent of the trading application: no model imports,
API secrets, order placement or live-capital path.

The core `start.bat` does not launch the legacy DuckDB recorder set by default. Run this capture
application independently. This application is an archival/training source; it does not publish
the core app's local low-latency `data/pm_live_quotes.json` compatibility bridge. Executable
Polymarket paper-entry gates therefore remain unavailable unless that bridge is deliberately
provided or the core is later wired to a direct equivalent.

## Recorded Dataset

Every row has a local receive timestamp. Exchange/source timestamps are retained separately.
Parquet is partitioned by stream, UTC date and UTC hour.

| Stream | Content | Primary use |
|---|---|---|
| `binance_depth_snapshot` | Spot REST L2 baseline per websocket session | Reconstruct spot book |
| `binance_depth` | Spot 100 ms L2 deltas, update IDs, session and gap state | Queue/order-flow research |
| `binance_trades` | Spot aggregate trades, trade-ID range and aggressor side | CVD, flow, executions |
| `futures_depth_snapshot` | USD-M perpetual REST L2 baseline per session | Reconstruct perp book |
| `futures_depth` | Perp 100 ms deltas including `pu`, transaction time and gaps | Queue, basis, liquidity |
| `futures_trades` | Perp aggregate trades and aggressor side | Futures CVD and toxicity |
| `futures_mark` | Mark, index, estimated settlement, funding and next funding time | Basis/funding state |
| `futures_open_interest` | Current open interest, polled | Positioning state |
| `futures_funding_history` | Official realized funding events, restart-deduplicated | Carry/P&L labels |
| `futures_positioning` | Global/top-trader long-short and taker buy/sell ratios | Crowding/flow state |
| `futures_liquidations` | Venue forced orders | Cascade/exhaustion research |
| `polymarket_market_meta` | Slug, anchor, horizon, token/outcome, tick/order rules, raw metadata | Joins and execution rules |
| `polymarket_book` | Full snapshots and every documented `price_change` delta | Exact CLOB reconstruction |
| `polymarket_trades` | `last_trade_price` events | Trade response and toxicity |
| `polymarket_market_events` | Best bid/ask, tick and lifecycle/unknown events with raw JSON | Market-state audit |
| `polymarket_reference` | Polymarket RTDS Binance and Chainlink BTC reference prices | Anchor/settlement analysis |
| `polymarket_settlement` | Final official UP/DOWN result plus raw Gamma payload | Labels and executable P&L |
| `bybit_quotes` | Bybit perpetual top-of-book with exchange/update/receive clocks | Cross-venue lead/lag and basis |
| `bybit_trades` | Bybit public trades with aggressor side | Cross-venue signed flow and propagation |
| `bybit_open_interest` | Bybit linear BTC open-interest history, source-time deduplicated | Exchange OI divergence |
| `bybit_funding_history` | Realized Bybit funding events, source-time deduplicated | Funding divergence and carry labels |
| `coinbase_ticker` | Coinbase BTC-USD bid/ask/last and exchange sequence | US spot premium and venue leadership |
| `deribit_options` | Full BTC option chain by strike/expiry every 60s | Implied volatility, skew, term and straddles |
| `pyth_reference` | Pyth BTC/USD price, confidence and publication time | Settlement-reference reconciliation |
| `collector_runtime` | Loop lag, process CPU/RSS and free disk every 5s | Prove whether the VM keeps up without timing distortion |

Unknown Polymarket market events are preserved as raw JSON rather than discarded. Market
metadata and settlement payloads are also preserved so future analysis can audit changed API
semantics.

## Reconstruction Guarantees

Binance delta streams are not usable alone. On every connection the recorder:

1. opens the websocket so updates are buffered;
2. downloads a REST depth snapshot;
3. stores the snapshot with a connection `session_id`;
4. discards stale deltas and applies the first overlapping update;
5. validates spot `U/u` or futures `pu` continuity;
6. records a true gap and reconnects for a new snapshot if continuity breaks.

Polymarket sends a complete book on subscription/reconnection. Snapshot and delta rows carry the
same session ID. The recorder sends Polymarket's required text-frame `PING` keepalive in addition
to websocket protocol handling.

## Commands

```bash
pip install -r capture_app/requirements.txt
python capture_app/run.py --selftest
python capture_app/run.py --record
python capture_app/run.py --status
python capture_app/run.py --quality
python capture_app/run.py --disk
python capture_app/run.py --archive-once
python capture_app/run.py --verify-archive
```

Use `CAPTURE_DATA_DIR` to override the configured data location without editing the file:

```powershell
$env:CAPTURE_DATA_DIR = "D:\btc-capture\data"
python capture_app\run.py --record
```

`--status` checks only enabled streams. High-rate streams must have fresh data, quiet websocket
streams must have a fresh connected heartbeat, snapshots must contain rows, and pollers/archive
jobs fail on current errors. `DEGRADED` is intentionally unhealthy.

Current and upcoming Polymarket 5m/15m slugs are queried exactly every 15 seconds by default. The
websocket is restarted only when the token set changes. This avoids both a 120-second delay and the
global-event ordering failure that can select tomorrow's scheduled rounds instead of the rounds
trading now.

`--quality` reads only atomically completed Parquet files and checks row counts, timestamp
freshness, missing UTC hours, footer readability and schema drift per enabled dataset. It writes `state/quality.json`
and returns non-zero for a missing, corrupt or stale required stream. This is separate from
liveness: a connected socket with no durable rows is not a trainable dataset.

## Public Data Coverage

The recorder covers the public causal inputs required by the current Binance and Polymarket
research lanes: sequenced Binance spot/perpetual books, aggressor trades, liquidations, OI,
funding/positioning, synchronized Bybit and Coinbase observations, Polymarket two-token L2/trades,
official round labels, RTDS references, Pyth, and the Deribit strike/expiry surface.

It stores raw observations plus provenance rather than model features. CVD, VPIN, basis, premium,
skew, time-to-touch and queue simulations must be derived later with causal joins. This preserves
the source data when feature definitions change.

Pyth requires API authentication from 2026-08-18. The legacy public endpoint remains the default
for the transition. Obtain a Pyth key and deploy with these values kept in the shell environment,
not in `config.json`:

```bash
export PYTH_API_KEY='...'
export PYTH_ENDPOINT='https://pyth.dourolabs.app/hermes/v2/updates/price/latest'
```

If authentication is missing or rejected, `pyth_reference` stays visibly `ERROR`; it never falls
back to Binance and falsely labels an exchange price as Pyth.

## Futures REST Fallback

Some networks accept Binance futures depth but starve `aggTrade` and `markPrice` websocket
messages. The recorder detects this and preserves:

- aggregate trades through `/fapi/v1/aggTrades` with `source=rest`;
- mark/index/funding through `/fapi/v1/premiumIndex` with `source=rest`.

This prevents a total hole, but status reports `DEGRADED`: REST receive time is not equivalent to
websocket event arrival for latency or queue studies. On a host where websocket events arrive,
rows use `source=ws` and the fallback remains inactive.

## Durable Storage and Retention

Writes use a temporary parquet file, file `fsync`, then atomic rename. A failed write restores
the detached rows to memory. Timed flushes partition each row by its own UTC hour, including a
buffer that crosses an hour boundary.

The disk guard deletes only partitions carrying `.archived`. Recent clock-hours are protected
across all streams. Deletion failures are reported and bytes are counted as freed only after the
directory is confirmed absent.

For unattended retention, create a GCS bucket and set:

```json
"archive_gcs_bucket": "YOUR_BUCKET",
"archive_gcs_prefix": "btc-capture",
"archive_after_hours": 6,
"archive_interval_seconds": 900,
"archive_target_file_mb": 256
```

You can keep the bucket name out of the file by setting `CAPTURE_GCS_BUCKET` and optionally
`CAPTURE_GCS_PREFIX`. Environment values take precedence over JSON.

Each completed stream/hour is compacted by schema toward 256 MB objects. The target is a maximum,
not padding: a low-volume hour remains one small object. This reduces the one-minute flush-file
count without combining unrelated streams or clock hours. Every object is ZSTD Parquet and is
validated using upload CRC32C, remote CRC32C, byte size and SHA-256 metadata. A content-addressed
manifest records source files, row counts, schemas and object hashes. Immutable top-level
`_catalog/` records identify published generations for that stream/hour; consumers select the
newest record and then follow its exact manifest rather than wildcard-reading stale generations.

Only after the full object set, manifest and catalog record verify does the writer atomically add
a durable receipt outside the capped data tree and a structured local `.archived` marker. A
concurrent/late local write removes that marker under the same partition lock. The disk guard takes
that lock before deletion, so it cannot race a writer. Receipts remain available to
`--verify-archive` after the corresponding local Parquet directory has been reclaimed.
Run `--verify-archive` periodically; it returns non-zero for missing/tampered objects, legacy
unverified markers, or an empty verification set. If no bucket is configured, no automatic
off-machine retention exists and unarchived history is never silently deleted.

`--archive-older-than HOURS --confirm-uploaded` is a manual marker and must be used only after an
independently verified upload. Without the explicit confirmation flag it refuses to act.

## Settlement Integrity

Only a clean final `1/0` outcome pair is accepted. Malformed slugs, missing anchors, unexpected
labels and unresolved prices are rejected. The parquet row is flushed before its write-once index
is updated. On every startup the index is rebuilt from durable parquet, so a corrupt or stale JSON
index cannot hide a missing outcome. Conflicting durable outcomes stop startup.

## Verification Performed on 2026-08-13

Deterministic suite: 16/16 passed.

- cross-hour partitioning;
- failed-write buffer recovery;
- global recent-hour disk protection;
- status-state merge;
- verified archive marking;
- compaction row reconciliation, manifest/current-pointer verification and tamper rejection;
- spot snapshot overlap/stale/gap rules;
- futures snapshot overlap/`pu`/gap rules;
- invalid snapshot refusal;
- Polymarket snapshot/delta/trade/event normalization;
- Polymarket RTDS reference normalization;
- strict settlement parsing;
- settlement-index reconstruction from parquet.

An isolated live smoke captured and read back valid parquet for spot depth/snapshot/trades,
futures depth/snapshot/OI, Polymarket book/metadata/events, and both Polymarket reference sources.
There were zero spot or futures depth sequence gaps. On the development network, futures trades
and mark data were captured through the REST fallback and correctly reported `DEGRADED`.

No Polymarket trade or newly closed settlement occurred during the short smoke interval. Those
schemas and parsers are tested, but production row growth must be confirmed during the first
long-running capture.

## Deployment

```bash
CAPTURE_GCS_BUCKET=YOUR-GLOBALLY-UNIQUE-BUCKET \
  bash capture_app/deploy_gcp.sh bucket-create

CAPTURE_GCS_BUCKET=YOUR-GLOBALLY-UNIQUE-BUCKET \
  bash capture_app/deploy_gcp.sh create

bash capture_app/deploy_gcp.sh status
bash capture_app/deploy_gcp.sh logs
bash capture_app/deploy_gcp.sh destroy
```

`bucket-create` creates or hardens a private regional Standard bucket with uniform bucket-level
access, public-access prevention, and default lifecycle transitions Standard -> Coldline at 90
days -> Archive at 365 days. Change those ages only through
`CAPTURE_COLDLINE_AFTER_DAYS`/`CAPTURE_ARCHIVE_AFTER_DAYS`, with the latter strictly greater. These
defaults keep actively scanned research data out of retrieval-charged archival storage. GCS
supports these lifecycle transitions, but minimum-duration, retrieval and operation charges still
apply; review the current [storage class](https://cloud.google.com/storage/docs/storage-classes),
[lifecycle](https://cloud.google.com/storage/docs/lifecycle), and
[pricing](https://cloud.google.com/storage/pricing) documentation.

The script uploads only `capture_app/`, runs `--selftest`, and configures systemd to run the tests
before every service start. When a bucket is supplied, it verifies that bucket before VM creation,
creates/uses a dedicated `btc-capture-writer` service account, grants immutable object create/read
access (not delete), attaches it to the VM, and injects the
bucket/prefix into systemd. It
guards the chosen zone, machine type, disk size and an existing active e2-micro. These checks do
not guarantee a zero cloud bill: account billing, quotas, network egress, bucket location and
current Google Cloud free-tier terms remain the operator's responsibility.

As checked on 2026-08-13, project `btc-capture` is selected but reports `billingEnabled: false`.
Google Cloud resource creation/upload cannot be treated as operational until billing is explicitly
enabled by the account owner and a real `--archive-once` plus `--verify-archive` smoke passes.

The launcher defaults to the free-tier `e2-micro`. That is a cost experiment, not a capacity
promise. Keep it only if at least 24 hours of `collector_runtime` show no sustained pressure and
the quality report has no missing hours. If it cannot keep up, redeploy with
`CAPTURE_MACHINE_TYPE=e2-small`; preserving causal receive order is more important than saving the
small VM difference. The script prints a billed-resource warning for any non-micro selection.

## Known Boundaries

This captures the listed public forward data from the moment it starts. It does not backfill L2,
know your own queue position or missed passive fills, or record private order acknowledgements.
Public L2 can simulate queue/fillability; only an authenticated order audit can prove actual fills.
Macro, on-chain and social/news feeds are not collected because no current preregistered gate
requires them; indiscriminate feeds add cost and multiple-testing risk without fixing execution.

Recording comprehensive inputs does not create a profitable signal. It creates the causal,
auditable evidence needed to test one without inventing fills or labels.
