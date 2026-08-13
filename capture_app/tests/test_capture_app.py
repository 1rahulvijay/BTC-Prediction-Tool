from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import pyarrow as pa
import pyarrow.parquet as pq

CAPTURE_APP_ROOT = Path(__file__).resolve().parents[1]
if str(CAPTURE_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(CAPTURE_APP_ROOT))

from recorder.settlements import (
    SETTLEMENT_SCHEMA, SettlementIndex, parse_slug, parse_timestamp_ms, resolve_outcome,
)
from recorder.archive import (
    _crc32c, upload_partition, verify_archive_receipts, verify_partition,
)
from recorder.futures import parse_positioning
from recorder.options import DERIBIT_SCHEMA, normalize_deribit, parse_instrument
from recorder.pyth import PYTH_SCHEMA, parse_pyth
from recorder.quality import quality_report
from recorder.storage import (
    PartitionWriter, archived_metadata, enforce_cap, mark_archived, partitions, write_status,
)
from recorder.streams import (
    PM_BOOK_SCHEMA, PM_EVENT_SCHEMA, PM_REFERENCE_SCHEMA, PM_TRADE_SCHEMA,
    _validated_snapshot, classify_futures_depth, classify_spot_depth,
    parse_pm_events, parse_pm_reference, parse_pm_references, pm_candidate_slugs,
)
from recorder.venues import (
    BYBIT_DERIVATIVE_SCHEMA, QUOTE_SCHEMA, VENUE_TRADE_SCHEMA, parse_bybit_metric,
    parse_bybit_quote, parse_bybit_trades, parse_coinbase_ticker,
)


class StorageTests(unittest.TestCase):
    def test_crc32c_matches_standard_vector(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vector.bin"
            path.write_bytes(b"123456789")
            self.assertEqual(_crc32c(path), "4waSgw==")

    def test_flush_partitions_rows_by_their_own_hour(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            schema = pa.schema([("ts_ms", pa.int64()), ("value", pa.int64())])
            writer = PartitionWriter(root, "sample", schema)
            writer.add({"ts_ms": 1_700_000_000_000, "value": 1})
            writer.add({"ts_ms": 1_700_003_600_000, "value": 2})
            self.assertEqual(writer.flush(), 2)
            files = list(root.glob("sample/date=*/hour=*/*.parquet"))
            self.assertEqual(len(files), 2)
            self.assertEqual(sorted(pq.read_table(path).column("value")[0].as_py()
                                    for path in files), [1, 2])

    def test_failed_write_restores_buffer(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = PartitionWriter(
                Path(tmp) / "data", "sample",
                pa.schema([("ts_ms", pa.int64()), ("value", pa.int64())]),
            )
            writer.add({"ts_ms": 1_700_000_000_000, "value": 1})
            with mock.patch("recorder.storage.pq.write_table", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    writer.flush()
            self.assertEqual(len(writer._buf), 1)
            self.assertEqual(writer.rows_written, 0)

    def test_threshold_flush_does_not_block_stream_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = pq.write_table

            def slow_write(*args, **kwargs):
                time.sleep(0.15)
                return original(*args, **kwargs)

            writer = PartitionWriter(
                Path(tmp) / "data", "sample",
                pa.schema([("ts_ms", pa.int64()), ("value", pa.int64())]), max_rows=1,
            )
            with mock.patch("recorder.storage.pq.write_table", side_effect=slow_write):
                started = time.perf_counter()
                writer.add({"ts_ms": 1_700_000_000_000, "value": 1})
                self.assertLess(time.perf_counter() - started, 0.10)
                writer.flush()
            self.assertEqual(writer.rows_written, 1)

    def test_cap_protects_recent_clock_hours_across_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            old = root / "a" / "date=2020-01-01" / "hour=00"
            recent = root / "z" / "date=2099-01-01" / "hour=00"
            for part in (old, recent):
                part.mkdir(parents=True)
                (part / "x.parquet").write_bytes(b"x" * 128)
                mark_archived(part)
            report = enforce_cap(root, cap_gb=0.0000001, keep_hours=6)
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())
            self.assertIn(str(old), report["removed"])

    def test_status_updates_merge_instead_of_erasing_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            write_status(root, "x", {"rows": 12})
            write_status(root, "x", {"connected": True})
            state = json.loads((root.parent / "state" / "x.json").read_text())
            self.assertEqual(state["rows"], 12)
            self.assertTrue(state["connected"])

    def test_archive_marks_partition_only_after_size_verification(self):
        class Blob:
            def __init__(self):
                self.size = None
                self.generation = 1
                self.metadata = {}
                self.crc32c = None

            def exists(self):
                return self.size is not None

            def upload_from_filename(self, filename, if_generation_match, checksum):
                self.size = Path(filename).stat().st_size
                self.generation += 1
                self.crc32c = self.metadata["crc32c"]

            def reload(self):
                return None

        class Bucket:
            name = "test"

            def __init__(self):
                self.objects = {}

            def blob(self, name):
                return self.objects.setdefault(name, Blob())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            part = root / "stream" / "date=2026-08-13" / "hour=10"
            part.mkdir(parents=True)
            schema = pa.schema([("ts_ms", pa.int64()), ("value", pa.int64())])
            pq.write_table(pa.Table.from_pydict({"ts_ms": [1, 2], "value": [3, 4]},
                                                schema=schema), part / "a.parquet")
            pq.write_table(pa.Table.from_pydict({"ts_ms": [5], "value": [6]},
                                                schema=schema), part / "b.parquet")
            bucket = Bucket()
            self.assertGreater(upload_partition(root, part, bucket, target_file_mb=32), 0)
            self.assertTrue((part / ".archived").exists())
            marker = archived_metadata(part)
            self.assertTrue(marker["verified"])
            self.assertEqual(sum(item["rows"] for item in marker["objects"]), 3)
            self.assertTrue(verify_partition(part, bucket)[0])
            receipt = (root.parent / "state" / "archive_receipts" / "stream"
                       / "date=2026-08-13" / "hour=10" / "receipt.json")
            self.assertTrue(receipt.exists())
            self.assertEqual(json.loads(receipt.read_text())["inventory_sha256"],
                             marker["inventory_sha256"])
            object_count = len(bucket.objects)
            self.assertGreater(upload_partition(root, part, bucket, target_file_mb=32), 0)
            self.assertEqual(len(bucket.objects), object_count)

            # A later write must invalidate deletion eligibility immediately.
            writer = PartitionWriter(root, "stream", schema)
            writer.add({"ts_ms": 1_786_616_100_000, "value": 7})
            writer.flush()
            self.assertFalse((part / ".archived").exists())
            upload_partition(root, part, bucket, target_file_mb=32)
            shutil.rmtree(part)
            report = verify_archive_receipts(root, bucket)
            self.assertEqual((report["checked"], report["verified"], report["failed"]),
                             (1, 1, 0))

    def test_archive_verification_rejects_remote_metadata_tampering(self):
        class Blob:
            def __init__(self):
                self.size = None
                self.generation = 1
                self.metadata = {}
                self.crc32c = None

            def exists(self):
                return self.size is not None

            def upload_from_filename(self, filename, if_generation_match, checksum):
                self.size = Path(filename).stat().st_size
                self.generation += 1
                self.crc32c = self.metadata["crc32c"]

            def reload(self):
                return None

        class Bucket:
            name = "test"

            def __init__(self):
                self.objects = {}

            def blob(self, name):
                return self.objects.setdefault(name, Blob())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            part = root / "stream" / "date=2026-08-13" / "hour=10"
            part.mkdir(parents=True)
            pq.write_table(pa.table({"ts_ms": [1], "value": [2]}), part / "a.parquet")
            bucket = Bucket()
            upload_partition(root, part, bucket, target_file_mb=32)
            object_name = archived_metadata(part)["objects"][0]["name"]
            bucket.objects[object_name].metadata["sha256"] = "tampered"
            ok, error = verify_partition(part, bucket)
            self.assertFalse(ok)
            self.assertIn("SHA-256", error)


class SequenceTests(unittest.TestCase):
    def test_spot_snapshot_overlap_stale_and_gap(self):
        self.assertEqual(classify_spot_depth(100, 95, 101), ("APPLIED", False))
        self.assertEqual(classify_spot_depth(100, 90, 100), ("STALE", False))
        self.assertEqual(classify_spot_depth(100, 102, 103), ("GAP", True))

    def test_futures_snapshot_overlap_then_pu_chain(self):
        self.assertEqual(classify_futures_depth(100, 100, 98, 102, 97),
                         ("APPLIED", False))
        self.assertEqual(classify_futures_depth(100, 100, 99, 100, 98),
                         ("APPLIED", False))
        self.assertEqual(classify_futures_depth(102, 100, 103, 104, 102),
                         ("APPLIED", False))
        self.assertEqual(classify_futures_depth(104, 100, 105, 106, 103),
                         ("GAP", True))
        self.assertEqual(classify_futures_depth(104, 100, 100, 104, 99),
                         ("STALE", False))

    def test_snapshot_validation_refuses_incomplete_or_nonfinite_books(self):
        with self.assertRaises(ValueError):
            _validated_snapshot({"lastUpdateId": 1, "bids": [], "asks": [["2", "1"]]})
        with self.assertRaises(ValueError):
            _validated_snapshot({"lastUpdateId": 1, "bids": [["nan", "1"]],
                                 "asks": [["2", "1"]]})

    def test_positioning_combines_only_timestamped_official_rows(self):
        row = parse_positioning(
            [{"timestamp": 1000, "longShortRatio": "1.1"}],
            [{"timestamp": 1001, "longShortRatio": "1.2"}],
            [{"timestamp": 1002, "longShortRatio": "1.3"}],
            [{"timestamp": 1003, "buySellRatio": "1.4", "buyVol": "5",
              "sellVol": "4"}],
            "BTCUSDT", "5m", 2000,
        )
        self.assertEqual(row["data_ms"], 1000)
        self.assertEqual(row["top_position_long_short_ratio"], 1.3)
        self.assertEqual(row["taker_buy_sell_ratio"], 1.4)


class IndependentFeedTests(unittest.TestCase):
    def test_cross_venue_parsers_preserve_exchange_and_receive_clocks(self):
        quote = parse_bybit_quote({
            "topic": "orderbook.1.BTCUSDT", "ts": 1_700_000_000_001,
            "data": {"s": "BTCUSDT", "u": 9, "seq": 11, "cts": 1_700_000_000_000,
                     "b": [["60000", "2"]], "a": [["60001", "3"]]},
        }, 1_700_000_000_002_000_000, 123, "s1")
        self.assertEqual(quote["event_ms"], 1_700_000_000_000)
        self.assertEqual(quote["recv_ns"], 1_700_000_000_002_000_000)
        pa.Table.from_pylist([quote], schema=QUOTE_SCHEMA)

        trades = parse_bybit_trades({
            "topic": "publicTrade.BTCUSDT", "data": [
                {"T": 1_700_000_000_000, "s": "BTCUSDT", "i": "t1",
                 "p": "60000", "v": "0.1", "S": "Buy", "L": "PlusTick"},
            ],
        }, 1_700_000_000_002_000_000, 124, "s2")
        self.assertEqual(trades[0]["side"], "buy")
        pa.Table.from_pylist(trades, schema=VENUE_TRADE_SCHEMA)

        coinbase = parse_coinbase_ticker({
            "type": "ticker", "product_id": "BTC-USD", "sequence": 5,
            "time": "2023-11-14T22:13:20.000Z", "price": "60000",
            "best_bid": "59999", "best_ask": "60001", "last_size": "0.2",
        }, 1_700_000_000_002_000_000, 125, "s3")
        self.assertEqual(coinbase["event_ms"], 1_700_000_000_000)
        pa.Table.from_pylist([coinbase], schema=QUOTE_SCHEMA)

        oi = parse_bybit_metric({"retCode": 0, "result": {"list": [{
            "symbol": "BTCUSDT", "openInterest": "12345.6", "timestamp": "1700000000000",
        }]}}, "open_interest", 100, 200)
        funding = parse_bybit_metric({"retCode": 0, "result": {"list": [{
            "symbol": "BTCUSDT", "fundingRate": "0.0001",
            "fundingRateTimestamp": "1700000000000",
        }]}}, "funding_rate", 100, 200)
        self.assertEqual((oi["unit"], funding["unit"]), ("BTC", "ratio"))
        pa.Table.from_pylist([oi, funding], schema=BYBIT_DERIVATIVE_SCHEMA)

    def test_deribit_and_pyth_normalization_is_trainable(self):
        self.assertEqual(parse_instrument("BTC-31JUL26-70000-C")[1:], (70_000.0, "C"))
        deribit = normalize_deribit({"result": [{
            "instrument_name": "BTC-31JUL26-70000-C", "creation_timestamp": 1000,
            "underlying_index": "SYN.BTC-31JUL26", "underlying_price": 69000,
            "bid_price": 0.01, "ask_price": 0.02, "mark_price": 0.015,
            "mark_iv": 45, "open_interest": 12, "volume": 2,
            "base_currency": "BTC", "quote_currency": "BTC",
        }]}, 1_000_000_000, 2_000_000_000)
        self.assertEqual(len(deribit), 1)
        self.assertEqual(len(deribit[0]["response_sha256"]), 64)
        pa.Table.from_pylist(deribit, schema=DERIBIT_SCHEMA)

        pyth = parse_pyth({"parsed": [{
            "id": "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
            "price": {"price": "600001234", "conf": "1234", "expo": -4,
                      "publish_time": 1700000000},
            "ema_price": {"price": "599991234", "conf": "1500", "expo": -4,
                          "publish_time": 1699999999},
        }]}, 1_000_000_000, 2_000_000_000)
        self.assertAlmostEqual(pyth["price"], 60000.1234)
        self.assertAlmostEqual(pyth["confidence"], 0.1234)
        pa.Table.from_pylist([pyth], schema=PYTH_SCHEMA)

    def test_quality_report_detects_missing_and_corrupt_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            writer = PartitionWriter(root, "good", pa.schema([
                ("ts_ms", pa.int64()), ("value", pa.int64()),
            ]))
            writer.add({"ts_ms": int(__import__("time").time() * 1000), "value": 1})
            writer.flush()
            report = quality_report(root, ["good", "missing"], 300)
            self.assertFalse(report["ok"])
            self.assertEqual(report["streams"]["good"]["rows"], 1)
            self.assertIn("missing: no parquet files", report["errors"])

    def test_quality_report_detects_missing_continuous_hour(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            writer = PartitionWriter(root, "continuous", pa.schema([
                ("ts_ms", pa.int64()), ("value", pa.int64()),
            ]))
            writer.add({"ts_ms": 1_700_000_000_000, "value": 1})
            writer.add({"ts_ms": 1_700_007_200_000, "value": 2})
            writer.flush()
            report = quality_report(root, ["continuous"], 10**9)
            self.assertFalse(report["ok"])
            self.assertEqual(len(report["streams"]["continuous"]["missing_hours"]), 1)


class PolymarketTests(unittest.TestCase):
    def setUp(self):
        self.meta = {"up": {"condition_id": "c1", "outcome": "UP"},
                     "down": {"condition_id": "c1", "outcome": "DOWN"}}

    def test_complete_market_event_normalization(self):
        messages = [
            {"event_type": "book", "asset_id": "up", "market": "c1", "timestamp": "1000",
             "hash": "h", "bids": [{"price": "0.4", "size": "3"},
                                      {"price": "0.5", "size": "2"}],
             "asks": [{"price": "0.7", "size": "1"},
                                      {"price": "0.6", "size": "4"}]},
            {"event_type": "price_change", "market": "c1", "timestamp": "1001",
             "price_changes": [{"asset_id": "up", "side": "BUY", "price": "0.51",
                                "size": "5", "best_bid": "0.51", "best_ask": "0.60"}]},
            {"event_type": "last_trade_price", "market": "c1", "asset_id": "up",
             "timestamp": "1002", "side": "BUY", "price": "0.6", "size": "2"},
            {"event_type": "tick_size_change", "market": "c1", "asset_id": "up",
             "timestamp": "1003", "old_tick_size": "0.01", "new_tick_size": "0.001"},
        ]
        parsed = parse_pm_events(json.dumps(messages), self.meta, 2_000_000, "session")
        self.assertEqual([row["price"] for row in parsed["book"][:2]], [0.5, 0.4])
        self.assertEqual([row["price"] for row in parsed["book"][2:4]], [0.6, 0.7])
        self.assertEqual(parsed["book"][4]["event_type"], "price_change")
        self.assertEqual(parsed["trades"][0]["session_id"], "session")
        self.assertEqual(parsed["events"][0]["new_tick_size"], 0.001)
        pa.Table.from_pylist(parsed["book"], schema=PM_BOOK_SCHEMA)
        pa.Table.from_pylist(parsed["trades"], schema=PM_TRADE_SCHEMA)
        pa.Table.from_pylist(parsed["events"], schema=PM_EVENT_SCHEMA)

    def test_discovery_candidates_cover_current_and_upcoming_5m_15m_rounds(self):
        slugs = pm_candidate_slugs(1_786_609_201)
        self.assertEqual(len(slugs), 10)
        self.assertIn("btc-updown-5m-1786609200", slugs)
        self.assertIn("btc-updown-15m-1786608900", slugs)
        self.assertIn("btc-updown-5m-1786609800", slugs)

    def test_reference_parser_preserves_source_time_and_raw_payload(self):
        raw = json.dumps({"topic": "crypto_prices_chainlink", "timestamp": 1234,
                          "payload": {"symbol": "btc/usd", "value": "62000.5",
                                      "timestamp": 1235}})
        row = parse_pm_reference(raw, 2_000_000, "s1")
        self.assertEqual(row["source"], "chainlink")
        self.assertEqual(row["event_ms"], 1_235_000)
        self.assertEqual(row["session_id"], "s1")
        pa.Table.from_pylist([row], schema=PM_REFERENCE_SCHEMA)

        batch = json.dumps({"topic": "crypto_prices", "timestamp": 1240,
                            "payload": {"symbol": "btc/usd", "data": [
                                {"timestamp": 1238, "value": 62001},
                                {"timestamp": 1239, "value": 62002},
                            ]}})
        batch_rows = parse_pm_references(batch, 2_000_000, "s2")
        self.assertEqual([row["source"] for row in batch_rows], ["binance", "binance"])
        self.assertEqual([row["event_ms"] for row in batch_rows], [1_238_000, 1_239_000])
        pa.Table.from_pylist(batch_rows, schema=PM_REFERENCE_SCHEMA)


class SettlementTests(unittest.TestCase):
    def test_parsers_are_strict_and_handle_iso_time(self):
        self.assertEqual(parse_slug("btc-updown-15m-1781626200"), (15, 1781626200))
        self.assertEqual(parse_timestamp_ms("2026-08-13T07:30:00Z"), 1_786_606_200_000)
        self.assertEqual(resolve_outcome({"outcomes": '["Up","Down"]',
                                         "outcomePrices": '["1","0"]'})[:2], ("UP", 1))
        self.assertIsNone(resolve_outcome({"outcomes": '["Up","Down"]',
                                           "outcomePrices": '["0.6","0.4"]'})[0])

    def test_index_is_rebuilt_from_durable_parquet_not_stale_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            writer = PartitionWriter(root, "polymarket_settlement", SETTLEMENT_SCHEMA)
            writer.add({"ts_ms": 1_700_000_000_000, "slug": "btc-updown-5m-1700000000",
                        "condition_id": "c", "horizon": 5, "anchor_ts": 1_700_000_000,
                        "outcome": "UP", "up_win": 1, "resolution_source": "test",
                        "closed_ms": 1_700_000_300_000, "raw_outcome_prices": '["1","0"]',
                        "payload_json": "{}"})
            writer.flush()
            state = root.parent / "state"
            state.mkdir()
            (state / "settled_index.json").write_text('{"missing|5":0}')
            index = SettlementIndex(state, root)
            self.assertTrue(index.has("btc-updown-5m-1700000000", 5))
            self.assertFalse(index.has("missing", 5))
            self.assertEqual(len(partitions(root)), 1)


if __name__ == "__main__":
    unittest.main()
