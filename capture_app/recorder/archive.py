"""Verified, compacted off-machine archive for completed hour partitions."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

try:
    import google_crc32c
except ImportError:  # The recorder can run without GCS enabled before optional deps are installed.
    google_crc32c = None

from .storage import (
    archived_metadata, is_archived, mark_archived, partition_guard, partitions, status_dir,
    write_status,
)

ARCHIVE_FORMAT_VERSION = 2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _crc32c(path: Path) -> str:
    if google_crc32c is not None:
        checksum = google_crc32c.Checksum()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                checksum.update(chunk)
        digest = checksum.digest()
    else:
        # Castagnoli CRC32C fallback. The explicit dependency in requirements is much faster,
        # but archive-disabled installs must still be able to run recorder selftests.
        value = 0xFFFFFFFF
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                for byte in chunk:
                    value ^= byte
                    for _ in range(8):
                        value = (value >> 1) ^ (0x82F63B78 if value & 1 else 0)
        digest = (value ^ 0xFFFFFFFF).to_bytes(4, "big")
    return base64.b64encode(digest).decode("ascii")


def _schema_id(schema: pa.Schema) -> str:
    return hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()


def _source_inventory(files: list[Path]) -> list[dict]:
    result = []
    for path in files:
        parquet = pq.ParquetFile(path)
        result.append({
            "name": path.name,
            "bytes": path.stat().st_size,
            "rows": parquet.metadata.num_rows,
            "sha256": _sha256(path),
            "schema_sha256": _schema_id(parquet.schema_arrow),
        })
    return result


def _inventory_id(inventory: list[dict]) -> str:
    raw = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _group_sources(files: list[Path], target_bytes: int) -> list[list[Path]]:
    """Group immutable source files by schema and approximate compressed byte target."""
    by_schema: dict[str, list[Path]] = {}
    for path in files:
        schema = pq.ParquetFile(path).schema_arrow
        by_schema.setdefault(_schema_id(schema), []).append(path)
    groups = []
    for schema_id in sorted(by_schema):
        current, current_bytes = [], 0
        for path in sorted(by_schema[schema_id]):
            size = path.stat().st_size
            if current and current_bytes + size > target_bytes:
                groups.append(current)
                current, current_bytes = [], 0
            current.append(path)
            current_bytes += size
        if current:
            groups.append(current)
    return groups


def _compact(files: list[Path], output: Path) -> dict:
    first = pq.ParquetFile(files[0])
    schema = first.schema_arrow
    expected_rows = 0
    writer = pq.ParquetWriter(output, schema, compression="zstd", compression_level=6)
    try:
        for path in files:
            parquet = pq.ParquetFile(path)
            if not parquet.schema_arrow.equals(schema, check_metadata=True):
                raise ValueError(f"schema changed inside archive group: {path}")
            expected_rows += parquet.metadata.num_rows
            for batch in parquet.iter_batches(batch_size=65_536):
                writer.write_table(pa.Table.from_batches([batch], schema=schema))
    finally:
        writer.close()
    with output.open("r+b") as handle:
        os.fsync(handle.fileno())
    actual_rows = pq.ParquetFile(output).metadata.num_rows
    if actual_rows != expected_rows:
        raise IOError(f"archive row mismatch: expected={expected_rows} actual={actual_rows}")
    return {
        "bytes": output.stat().st_size,
        "rows": actual_rows,
        "sha256": _sha256(output),
        "schema_sha256": _schema_id(schema),
        "source_files": len(files),
    }


def _upload_verified(bucket, object_name: str, path: Path, metadata: dict) -> None:
    """Upload with generation preconditions and verify size plus our content digest."""
    crc32c = _crc32c(path)
    metadata = {**metadata, "crc32c": crc32c}
    blob = bucket.blob(object_name)
    exists = blob.exists()
    generation = 0
    if exists:
        blob.reload()
        remote_meta = blob.metadata or {}
        if (int(blob.size or -1) == path.stat().st_size
                and remote_meta.get("sha256") == metadata["sha256"]
                and blob.crc32c == crc32c):
            return
        raise IOError(
            f"immutable archive object conflicts with local content: "
            f"gs://{bucket.name}/{object_name}"
        )
    blob.metadata = {str(key): str(value) for key, value in metadata.items()}
    blob.upload_from_filename(
        str(path), if_generation_match=generation, checksum="crc32c",
    )
    blob.reload()
    remote_meta = blob.metadata or {}
    if int(blob.size or -1) != path.stat().st_size:
        raise IOError(f"archive size mismatch for gs://{bucket.name}/{object_name}")
    if remote_meta.get("sha256") != metadata["sha256"]:
        raise IOError(f"archive SHA-256 metadata mismatch for gs://{bucket.name}/{object_name}")
    if blob.crc32c != crc32c:
        raise IOError(f"archive CRC32C mismatch for gs://{bucket.name}/{object_name}")


def _write_receipt(root: Path, relative: str, metadata: dict) -> None:
    path = status_dir(root) / "archive_receipts" / Path(relative) / "receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
    with tmp.open("r+b") as handle:
        os.fsync(handle.fileno())
    tmp.replace(path)


def upload_partition(root: Path, part: Path, bucket, prefix: str = "btc-capture",
                     target_file_mb: int = 256) -> int:
    """Compact, upload and content-verify one immutable hour partition.

    The manifest is the read contract. Consumers must read only objects listed by the manifest,
    never wildcard every historical object under the prefix; an exceptional late event can
    publish a replacement generation while preserving the prior audit trail.
    """
    files = sorted(part.glob("*.parquet"))
    if not files:
        raise ValueError(f"partition has no parquet files: {part}")
    if not 32 <= int(target_file_mb) <= 512:
        raise ValueError("archive target file size must be between 32 and 512 MB")

    inventory = _source_inventory(files)
    inventory_sha = _inventory_id(inventory)
    relative = part.relative_to(root).as_posix()
    base = "/".join(value for value in (prefix.strip("/"), relative) if value)
    groups = _group_sources(files, int(target_file_mb) * 1024 * 1024)
    state = status_dir(root)
    state.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format_version": ARCHIVE_FORMAT_VERSION,
        "partition": relative,
        "inventory_sha256": inventory_sha,
        "source": inventory,
        "objects": [],
    }
    uploaded = 0
    with tempfile.TemporaryDirectory(prefix="archive-", dir=state) as tmp:
        staging = Path(tmp)
        for index, group in enumerate(groups):
            output = staging / f"part-{index:04d}.parquet"
            details = _compact(group, output)
            object_name = (
                f"{base}/data/v{ARCHIVE_FORMAT_VERSION}/{inventory_sha}/"
                f"{details['sha256']}/{output.name}"
            )
            metadata = {
                "sha256": details["sha256"],
                "rows": details["rows"],
                "schema_sha256": details["schema_sha256"],
                "inventory_sha256": inventory_sha,
                "archive_format_version": ARCHIVE_FORMAT_VERSION,
            }
            _upload_verified(bucket, object_name, output, metadata)
            manifest["objects"].append({"name": object_name, **details})
            uploaded += details["bytes"]
            output.unlink()

        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8",
        )
        manifest_sha = _sha256(manifest_path)
        manifest_object = (
            f"{base}/manifests/v{ARCHIVE_FORMAT_VERSION}/"
            f"{inventory_sha}-{manifest_sha}.json"
        )
        _upload_verified(bucket, manifest_object, manifest_path, {
            "sha256": manifest_sha,
            "inventory_sha256": inventory_sha,
            "archive_format_version": ARCHIVE_FORMAT_VERSION,
        })

    # Ensure no file arrived while compaction/upload was in progress. Such a late write removes
    # the local marker and must be archived in a fresh generation before deletion is possible.
    with partition_guard(part):
        if _inventory_id(_source_inventory(sorted(part.glob("*.parquet")))) != inventory_sha:
            raise RuntimeError(f"partition changed during archive: {part}")
        current_payload = {
            "format_version": ARCHIVE_FORMAT_VERSION,
            "partition": relative,
            "inventory_sha256": inventory_sha,
            "manifest_object": manifest_object,
            "manifest_sha256": manifest_sha,
            "objects": manifest["objects"],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", prefix="current-",
            dir=state, delete=False,
        ) as handle:
            json.dump(current_payload, handle, sort_keys=True, separators=(",", ":"))
            current_path = Path(handle.name)
        try:
            current_sha = _sha256(current_path)
            catalog_base = "/".join(
                value for value in (prefix.strip("/"), "_catalog", relative) if value
            )
            current_object = (
                f"{catalog_base}/v{ARCHIVE_FORMAT_VERSION}/"
                f"{inventory_sha}-{current_sha}.json"
            )
            _upload_verified(bucket, current_object, current_path, {
                "sha256": current_sha,
                "inventory_sha256": inventory_sha,
                "archive_format_version": ARCHIVE_FORMAT_VERSION,
            })
        finally:
            current_path.unlink(missing_ok=True)
        archive_metadata = {
            "verified": True,
            "partition": relative,
            "bucket": bucket.name,
            "prefix": prefix.strip("/"),
            "inventory_sha256": inventory_sha,
            "manifest_object": manifest_object,
            "manifest_sha256": manifest_sha,
            "current_object": current_object,
            "current_sha256": current_sha,
            "objects": manifest["objects"],
        }
        _write_receipt(root, relative, archive_metadata)
        mark_archived(part, archive_metadata)
    return uploaded


def _verify_metadata(marker: dict, bucket) -> tuple[bool, str | None]:
    if not marker.get("verified") or marker.get("bucket") != bucket.name:
        return False, "missing verified archive metadata"
    expected = list(marker.get("objects") or []) + [{
        "name": marker.get("manifest_object"),
        "sha256": marker.get("manifest_sha256"),
    }, {
        "name": marker.get("current_object"),
        "sha256": marker.get("current_sha256"),
    }]
    for item in expected:
        if not item.get("name") or not item.get("sha256"):
            return False, "incomplete archive marker"
        blob = bucket.blob(item["name"])
        if not blob.exists():
            return False, f"missing gs://{bucket.name}/{item['name']}"
        blob.reload()
        metadata = blob.metadata or {}
        if metadata.get("sha256") != item["sha256"]:
            return False, f"SHA-256 metadata mismatch: {item['name']}"
        if not blob.crc32c or metadata.get("crc32c") != blob.crc32c:
            return False, f"CRC32C metadata mismatch: {item['name']}"
        if item.get("bytes") is not None and int(blob.size or -1) != int(item["bytes"]):
            return False, f"size mismatch: {item['name']}"
    return True, None


def verify_partition(part: Path, bucket) -> tuple[bool, str | None]:
    return _verify_metadata(archived_metadata(part), bucket)


def verify_archive_receipts(root: Path, bucket) -> dict:
    report = {"checked": 0, "verified": 0, "failed": 0, "errors": []}
    seen = set()
    receipts = status_dir(root) / "archive_receipts"
    for path in sorted(receipts.glob("**/receipt.json")) if receipts.exists() else []:
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
            partition = str(metadata.get("partition") or path.parent)
            ok, error = _verify_metadata(metadata, bucket)
        except (OSError, TypeError, ValueError) as exc:
            partition, ok, error = str(path), False, f"invalid receipt: {exc}"
        seen.add(partition)
        report["checked"] += 1
        if ok:
            report["verified"] += 1
        else:
            report["failed"] += 1
            report["errors"].append({"partition": partition, "error": error})
    for part in partitions(root):
        if not is_archived(part):
            continue
        relative = part.relative_to(root).as_posix()
        if relative in seen:
            continue
        report["checked"] += 1
        ok, error = verify_partition(part, bucket)
        if ok:
            report["verified"] += 1
        else:
            report["failed"] += 1
            report["errors"].append({"partition": str(part), "error": error})
    return report


def verify_archives(root: Path, bucket_name: str) -> dict:
    from google.cloud import storage

    return verify_archive_receipts(root, storage.Client().bucket(bucket_name))


def archive_completed(root: Path, bucket_name: str, prefix: str,
                      older_than_hours: float, target_file_mb: int = 256) -> dict:
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    cutoff = time.time() - older_than_hours * 3600
    report = {"partitions_uploaded": 0, "bytes_uploaded": 0, "errors": 0}
    for part in partitions(root):
        if is_archived(part):
            continue
        # Partition time is encoded in UTC; mtime additionally prevents racing a late flush.
        newest = max((path.stat().st_mtime for path in part.glob("*.parquet")), default=0)
        if not newest or newest >= cutoff:
            continue
        try:
            report["bytes_uploaded"] += upload_partition(
                root, part, bucket, prefix, target_file_mb,
            )
            report["partitions_uploaded"] += 1
        except Exception as exc:  # noqa: BLE001
            report["errors"] += 1
            report["last_error"] = str(exc)[:300]
            break
    if not report["errors"]:
        report["last_error"] = None
    return report


async def archive_loop(root: Path, stop: asyncio.Event, *, bucket_name: str,
                       prefix: str = "btc-capture", older_than_hours: float = 6,
                       interval_s: int = 900, target_file_mb: int = 256) -> None:
    if not bucket_name:
        raise ValueError("archive bucket is required")
    if older_than_hours <= 0 or interval_s <= 0:
        raise ValueError("archive timing must be positive")
    totals = {"rows": 0, "bytes_uploaded": 0, "errors": 0}
    while not stop.is_set():
        try:
            result = await asyncio.to_thread(
                archive_completed, root, bucket_name, prefix, older_than_hours,
                target_file_mb,
            )
            totals["rows"] += int(result["partitions_uploaded"])
            totals["bytes_uploaded"] += int(result["bytes_uploaded"])
            totals["errors"] += int(result["errors"])
            totals["last_error"] = result.get("last_error")
            totals["last_success_utc"] = time.time()
        except Exception as exc:  # noqa: BLE001
            totals["errors"] += 1
            totals["last_error"] = str(exc)[:300]
        write_status(root, "archive_uploader", totals)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
