import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from backend.data_upload_engine import parse_source
from backend.observation_analysis_store import open_index, index_source, time_range, stored_source


class AnalysisStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.index = open_index(self.directory.name)

    def tearDown(self):
        self.index.close()
        self.directory.cleanup()

    def source(self, values):
        return ("station_id,element_id,element_name,date_time,value\n" + values).encode()

    def test_new_numeric_upload_updates_only_matching_keys(self):
        index_source(self.index, 1, self.source("A,1012,Air,2026-10-01 08:00,20\nA,1012,Air,2026-10-01 08:10,21\n"), "first.csv")
        index_source(self.index, 3, self.source("A,1012,Air,2026-10-01 08:00,25\nA,1012,Air,2026-10-01 08:10,\n"), "new.csv")
        index_source(self.index, 2, self.source("A,1012,Air,2026-10-01 08:00,24\n"), "older.csv")
        rows = self.index.execute("SELECT stamp,value,run_id FROM observations ORDER BY stamp").fetchall()
        self.assertEqual(rows, [("2026-10-01 08:00",25,3),("2026-10-01 08:10",21,1)])
        index_source(self.index, 3, b"invalid", "new.csv")
        self.assertEqual(self.index.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 2)

    def test_exact_minute_and_zero_preserved(self):
        index_source(self.index, 1, self.source("A,1107,Rain,2026-10-01 08:00,0\nA,1107,Rain,2026-10-01 08:10,3\n"), "rain.csv")
        self.assertEqual(self.index.execute("SELECT value FROM observations ORDER BY stamp").fetchall(),[(0,),(3,)])

    def test_concurrent_workers_index_same_run_once(self):
        barrier = Barrier(2)
        source = self.source("A,1107,Rain,2026-10-01 08:00,3\n")
        def parse_together(*args):
            result = parse_source(*args)
            barrier.wait(timeout=10)
            return result
        def worker():
            connection = open_index(self.directory.name)
            try:
                index_source(connection, 1, source, "rain.csv")
            finally:
                connection.close()
        with patch("backend.observation_analysis_store.parse_source", side_effect=parse_together):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(worker) for _ in range(2)]
                for future in futures:
                    future.result(timeout=15)
        self.assertEqual(self.index.execute("SELECT COUNT(*) FROM indexed_runs").fetchone()[0],1)
        self.assertEqual(self.index.execute("SELECT COUNT(*) FROM observations").fetchone()[0],1)

    def test_time_range_scoped_and_validated(self):
        index_source(self.index, 1, self.source("A,5,Rain,2026-10-01 08:00,3\nB,5,Rain,2026-12-01 08:00,3\n"), "rain.csv")
        start,end = time_range(None,None,self.index,["A"])
        self.assertEqual(end,"2026-10-01 08:01")
        with self.assertRaises(ValueError): time_range("2026-10-01","2026-09-01",self.index,["A"])
        with self.assertRaises(ValueError): time_range("2026-10-01T08:00Z",None,self.index,["A"])

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError): stored_source(dict(config_snapshot='{"source_file":{"storage_key":"../secret"}}'))
