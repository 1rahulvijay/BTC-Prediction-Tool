# GCP Cost And Storage Policy

Date: 2026-08-14
Status: implemented, cloud execution pending operator billing and deployment

## Decision

Use GCP for the standalone public-data recorder, but choose the bucket policy from the real access
pattern rather than applying a cold lifecycle to every object.

The default is now:

```text
CAPTURE_STORAGE_POLICY=download-monthly
```

This creates or updates a private regional Standard bucket and clears automatic storage-class
transitions. It is the correct default when recent data will be downloaded to the laptop for
research or training each month.

The optional long-retention mode is:

```text
CAPTURE_STORAGE_POLICY=archive
```

It configures Standard -> Coldline after 90 days -> Archive after 365 days. Use it only for data
that will not normally be read within a quarter/year.

## Why The Default Changed

Current Google Cloud pricing documentation states:

- Standard storage has no minimum storage duration and no retrieval fee;
- Nearline, Coldline and Archive have 30, 90 and 365-day minimum durations;
- retrieval is charged for Nearline, Coldline and Archive reads in addition to network and
  operation charges;
- deleting, replacing or moving a cold object before its minimum duration can incur an early
  deletion charge;
- eligible `us-east1`, `us-west1` and `us-central1` Cloud Storage usage includes 5 GB-month regional
  storage and 100 GB/month outbound transfer from North America to most destinations;
- the Compute Engine Free Tier includes one eligible non-preemptible `e2-micro` usage allowance and
  30 GB-month standard persistent disk in the supported US regions.

Authoritative references:

- https://cloud.google.com/storage/pricing
- https://docs.cloud.google.com/free/docs/free-cloud-features
- https://docs.cloud.google.com/storage/docs/lifecycle-configurations
- https://docs.cloud.google.com/storage/docs/data-validation

Pricing and free-tier terms can change. Check these pages and the billing calculator before each
deployment or major retention change.

## What Was Implemented

`capture_app/deploy_gcp.sh` now supports:

```text
CAPTURE_STORAGE_POLICY=download-monthly  # default
CAPTURE_STORAGE_POLICY=archive           # explicit cold lifecycle
```

For a new monthly-download bucket, the script creates Standard storage without a lifecycle. For an
existing bucket, it resets the default class to Standard and calls `--clear-lifecycle`. This stops
new automatic cold transitions.

Clearing a lifecycle does not rewrite objects that have already transitioned to Coldline or
Archive. Rewriting those objects can create retrieval, operation and early-deletion charges. Leave
existing cold objects in place until a deliberate cost review.

The new read-only inspection command is:

```bash
CAPTURE_GCS_BUCKET=YOUR_BUCKET bash capture_app/deploy_gcp.sh bucket-policy
```

The script retains uniform bucket-level access, public-access prevention, regional placement,
Standard as the upload class and the no-delete writer identity.

## Deployment Commands

Monthly-download policy:

```bash
export CAPTURE_GCS_BUCKET=YOUR_GLOBALLY_UNIQUE_BUCKET
export CAPTURE_STORAGE_POLICY=download-monthly
bash capture_app/deploy_gcp.sh bucket-create
bash capture_app/deploy_gcp.sh bucket-policy
bash capture_app/deploy_gcp.sh create
```

Long-term archive policy:

```bash
export CAPTURE_GCS_BUCKET=YOUR_GLOBALLY_UNIQUE_BUCKET
export CAPTURE_STORAGE_POLICY=archive
export CAPTURE_COLDLINE_AFTER_DAYS=90
export CAPTURE_ARCHIVE_AFTER_DAYS=365
bash capture_app/deploy_gcp.sh bucket-create
bash capture_app/deploy_gcp.sh bucket-policy
bash capture_app/deploy_gcp.sh create
```

`bucket-create` refuses to run until billing is enabled. The free tier is an allowance on a billed
project, not a way to create resources with billing disabled.

## Monthly Laptop Copy

The recorder uploads compacted Parquet objects plus immutable manifests and catalog generations.
The catalog/manifest is the data-selection contract. Training code must not wildcard every data
generation, because a late event can publish a replacement generation for the same hour.

A conservative monthly backup flow is:

1. Run `python capture_app/run.py --status` and `--quality` on the VM.
2. Run `python capture_app/run.py --archive-once` and `--verify-archive`.
3. Download with `gcloud storage cp` or `gcloud storage rsync`; Google Cloud CLI validates source
   and destination checksums for copies.
4. Preserve the remote `_catalog/` and manifest objects with the data objects.
5. Verify local Parquet footers, row counts and manifest SHA-256 values before using the copy.
6. Keep at least two independent copies before deleting the cloud copy.
7. Delete cloud data only as a separate, explicit operator action. The deployment script does not
   configure automatic deletion.

The current project has a verified remote archive reader, but not a catalog-aware one-command
monthly laptop restore tool. Until that tool is built and tested, do not treat a recursive download
alone as proof that a canonical training generation was reconstructed.

## Capacity Measurement

The pasted 200 GB/month and 3:1 compression examples are planning assumptions, not measurements
from this recorder. No local `capture_app/data` directory was present during this review, so an
actual compression ratio could not be calculated.

After deployment, run for at least 24 hours and record:

```bash
python capture_app/run.py --disk
python capture_app/run.py --status
python capture_app/run.py --quality
```

Then estimate:

```text
compressed_month_gb = 24_hour_parquet_gb * 30
average_new_storage_gb = compressed_month_gb / 2
```

Also inspect `collector_runtime` for sustained CPU, RSS, event-loop lag and free disk. An `e2-micro`
is a cost target, not a capacity guarantee. Move to a paid `e2-small` only if the quality report or
runtime evidence shows that the micro instance cannot preserve causal receive order.

## Cost Interpretation

Do not estimate the bill from storage alone. Include:

- average GB-month stored, not only month-end size;
- GCS operation counts;
- outbound transfer above the applicable free allowance;
- retrieval and early-deletion fees for any cold object;
- persistent disk and VM usage outside free-tier limits;
- taxes, currency conversion and region-specific SKUs.

If monthly compressed output remains under the outbound allowance, Standard plus monthly download
can be inexpensive. If compressed output is materially above it, downloading only the required
catalog generations or doing research close to the bucket can be cheaper than transferring the
entire lake every month.

## Safety And Evidence Boundary

Cheaper storage does not improve model accuracy or create profit. This architecture prevents data
loss and preserves causal evidence. A model can use the data only after schema, coverage, timing,
settlement and leakage checks pass, and a trading strategy still requires forward cost-adjusted
evidence before capital authority.
