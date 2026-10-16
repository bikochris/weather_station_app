import asyncio
import os
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch
from datetime import date

from fastapi import HTTPException
from starlette.requests import ClientDisconnect
from backend import observation_storage as storage
from backend.data_upload_engine import parse_source, analyze_source


HEADER = b"station_id,element_id,element_name,date_time,value\n"


class StreamRequest:
    def __init__(self, chunks, declared=None):
        self.chunks = chunks
        self.headers = {} if declared is None else {"content-length": str(declared)}
    async def stream(self):
        for chunk in self.chunks: yield chunk


class LargeObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(storage, "STORAGE_ROOT", self.root)
        self.patch.start()
    def tearDown(self):
        self.patch.stop(); self.temp.cleanup()

    def test_stream_limit_and_partial_file_cleanup(self):
        with patch.object(storage, "MAX_UPLOAD_BYTES", 100):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(storage.receive_observation_file(StreamRequest([b"x" * 60, b"x" * 60]), "data.csv"))
        self.assertEqual(error.exception.status_code, 413)
        self.assertEqual(list(self.root.iterdir()), [])
        with self.assertRaises(HTTPException) as error:
            asyncio.run(storage.receive_observation_file(StreamRequest([b"x"], 100), "data.csv"))
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_over_one_gb_header_is_not_rejected_by_20mb_limit(self):
        with self.assertRaises(HTTPException) as error:
            asyncio.run(storage.receive_observation_file(StreamRequest([b"x"], 1024**3 + 1), "data.csv"))
        self.assertEqual(error.exception.status_code, 400)  # Incomplete, not over limit.

    def test_excel_keeps_separate_limit(self):
        with self.assertRaises(HTTPException) as error:
            asyncio.run(storage.receive_observation_file(StreamRequest([], 21 * 1024**2), "data.xlsx"))
        self.assertEqual(error.exception.status_code, 413)

    def test_disconnect_removes_partial_file_and_releases_upload_slot(self):
        from backend.main import app
        from backend.data_upload_api import UPLOAD_SLOT
        class InterruptedRequest(StreamRequest):
            async def stream(self):
                yield HEADER
                raise ClientDisconnect()
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/performance-upload/analyze")
        with patch("backend.main.get_connection") as connect:
            with self.assertRaises(HTTPException) as error:
                asyncio.run(endpoint(InterruptedRequest([]),filename="data.csv",user=dict(username="tester",department="Admin",user_id=1)))
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("interrupted", error.exception.detail)
        self.assertEqual(list(self.root.iterdir()), [])
        connect.assert_not_called()
        acquired = UPLOAD_SLOT.acquire(blocking=False)
        self.assertTrue(acquired)
        if acquired: UPLOAD_SLOT.release()

    def test_disk_parser_matches_legacy_and_minute_duplicate_rules(self):
        raw = HEADER + b"A,101,Td,01/07/2026 06:00,0\nA,101,Td,01/07/2026 06:10,2\nA,101,Td,01/07/2026 06:10,\nA,101,Td,01/07/2026 06:20,3\n"
        path, metadata = asyncio.run(storage.receive_observation_file(StreamRequest([raw[:40],raw[40:]],len(raw)), "data.csv"))
        disk = parse_source(path, "data.csv")
        memory = parse_source(raw, "data.csv")
        try:
            key = ("A", "101", date(2026,7,1))
            self.assertEqual(disk["observations"].get((*key,6)), 3)
            self.assertEqual(disk["minute_observations"].get((*key,370)), 2)
            self.assertIn(key, disk["daily"])
            for field in ["row_count", "duplicate_rows", "minute_duplicate_rows", "periods"]:
                self.assertEqual(disk[field], memory[field])
            self.assertEqual(metadata["size_bytes"], len(raw))
        finally: disk["close"]()
        self.assertFalse(any(p.is_dir() for p in self.root.iterdir()))

    def test_more_than_500000_rows(self):
        path = self.root / "rows.csv"
        row = b"A,101,Td,2026-07-01 06:00,1\n"
        with path.open("wb") as output:
            output.write(HEADER)
            for _ in range(100): output.write(row * 5000)
            output.write(row)
        parsed = parse_source(path, "rows.csv")
        try:
            self.assertEqual(parsed["row_count"], 500001)
            self.assertEqual(parsed["minute_duplicate_rows"], 500000)
        finally: parsed["close"]()

    def test_station_name_and_reordered_columns_work_in_both_parsers(self):
        examples = [
            b"station_id,station_name,element_id,element_name,date_time,value\n40404100AWS,Cyeru_AWS,892,AWS Precip Cum,2026-10-01 00:00:00,0\n",
            b"value,element_name,date_time,station_name,element_id,station_id\n0,AWS Precip Cum,2026-10-01 00:00:00,Cyeru_AWS,892,40404100AWS\n"]
        for raw in examples:
            path, _ = asyncio.run(storage.receive_observation_file(StreamRequest([raw]), "data.csv"))
            disk = parse_source(path, "data.csv")
            memory = parse_source(raw, "data.csv")
            try:
                for parsed in [disk, memory]:
                    self.assertEqual(parsed["row_count"], 1)
                    self.assertEqual(parsed["invalid_rows"], 0)
                    self.assertEqual(parsed["observations"].get(("40404100AWS","892",date(2026,10,1),0)), 0)
            finally: disk["close"]()

    def test_ambiguous_duplicate_required_headers_are_rejected(self):
        path = self.root / "duplicate.csv"
        path.write_bytes(b"station_id,element_id,element_name,date_time,value,value\nA,101,Td,2026-07-01,1,2\n")
        with self.assertRaisesRegex(ValueError, "once each"):
            parse_source(path, "duplicate.csv")

    def test_external_details_round_trip_and_invalid_reference(self):
        result = dict(results=[], element_results=[], daily_details=[{"entered":"Inactive"}],hourly_details=[])
        summary, path = storage.store_result_details(result)
        self.assertNotIn("daily_details", summary)
        self.assertEqual(storage.load_result_details(summary), result)
        path.unlink()
        with self.assertRaises(HTTPException): storage.load_result_details(summary)
        with self.assertRaises(HTTPException): storage.load_result_details({"details_file":"../secret.json.gz"})

    @unittest.skipUnless(os.getenv("RUN_GB_OBSERVATION_TEST") == "1", "Explicit >1 GiB disk/stream integration test")
    def test_real_csv_over_one_gb_with_bounded_python_memory(self):
        row = b"A,101," + b"T" * 100000 + b",2026-07-01 06:00,1\n"
        repetitions = 11000
        size = len(HEADER) + len(row) * repetitions
        self.assertGreater(size, 1024**3)
        def chunks():
            yield HEADER
            for _ in range(repetitions): yield row
        tracemalloc.start()
        try:
            path, metadata = asyncio.run(storage.receive_observation_file(StreamRequest(chunks(),size), "large.csv"))
            parsed = parse_source(path, "large.csv")
            try:
                config = dict(version=1,stations=[dict(station_code="A",station_name="Alpha")],parameters=[
                    dict(element_id="101",name="Temperature",cadence="hourly",schedule_mode="interval",interval_minutes=10,hours=[6])])
                result = analyze_source(parsed,config)
                self.assertEqual(result["results"][0]["hourly_available"],1)
                self.assertEqual(parsed["row_count"],repetitions)
                self.assertEqual(metadata["size_bytes"],size)
            finally: parsed["close"]()
            peak = tracemalloc.get_traced_memory()[1]
            self.assertLess(peak, 128 * 1024**2)
            print(f"Verified {size:,} byte CSV; peak traced Python memory {peak / 1024**2:.1f} MiB")
        finally: tracemalloc.stop()


if __name__ == "__main__": unittest.main()
