import csv
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from threading import Lock
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import main as core
from backend import observation_analysis_api as api
from backend import observation_analysis_store as store


HEADER = "station_id,element_id,element_name,date_time,value\n"


class ObservationAnalysisApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.user = dict(user_id=9, department="Observation Officer", must_change_password=False)
        self.assigned_codes = {"M", "A", "OTHER"}
        self.stations = [
            dict(station_code=code, station_name=code, station_category=category,
                 latitude=latitude, longitude=1, altitude=100)
            for code, category, latitude in [
                ("M", "Principal Station", 1),
                ("A", "Automatic Weather station", 1),
                ("OTHER", "Rainfall Station", 2),
                ("PRIVATE-M", "Principal Station", 3),
                ("PRIVATE-A", "Automatic Weather station", 3),
            ]
        ]
        self.runs = [dict(run_id=1, filename="initial.csv", created_at="2026-01-01")]
        self.originals = {}
        self.index_rows(1,
            "M,101,Temperature,2026-01-01 08:00,20\n"
            "A,1012,Temperature,2026-01-01 08:00,21\n"
            "OTHER,5,Rain,2026-10-01 08:00,2\n"
            "PRIVATE-M,101,Temperature,2026-03-01 08:00,40\n"
            "PRIVATE-A,1012,Temperature,2026-03-01 08:00,45\n")

        self.mysql = MagicMock()
        self.cursor = self.mysql.cursor.return_value
        self.cursor.execute.side_effect = self.execute
        self.cursor.fetchall.side_effect = lambda: self.query_rows
        self.cursor.fetchone.side_effect = lambda: self.query_row
        self.query_rows, self.query_row = [], None
        self.connect = self.start_patch(patch.object(core, "get_connection", return_value=self.mysql))
        self.open_index = self.start_patch(patch.object(api, "open_index",
            side_effect=lambda: store.open_index(self.root)))
        self.start_patch(patch.object(store, "STORAGE_ROOT", self.root))
        self.start_patch(patch.object(api, "INDEX_LOCK", Lock()))

        app = FastAPI()
        api.install_analysis_routes(app)
        app.dependency_overrides[core.require_password_change_complete] = (
            lambda: core.require_password_change_complete(self.user))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def start_patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def index_rows(self, run_id, rows):
        connection = store.open_index(self.root)
        try:
            store.index_source(connection, run_id, (HEADER + rows).encode(), "data.csv")
        finally:
            connection.close()

    def execute(self, sql, params=()):
        if "FROM stations" in sql:
            self.query_rows = self.stations
            if "user_station_assignments" in sql:
                self.query_rows = [row for row in self.stations
                                   if row["station_code"] in self.assigned_codes]
        elif "WHERE run_id=%s" in sql:
            self.query_row = self.originals[params[0]]
        elif "FROM data_upload_runs" in sql:
            self.query_rows = self.runs
        else:
            raise AssertionError("Unexpected database query: " + sql)

    def comparison(self, **params):
        query = dict(pair_id="M:A", comparison_id="temperature")
        query.update(params)
        return self.client.get("/observation-analysis/comparison", params=query)

    def monitoring(self, **params):
        query = dict(station_code="A", start="2026-01-01", end="2026-01-02")
        query.update(params)
        return self.client.get("/observation-analysis/monitoring", params=query)

    def add_missing_source(self):
        self.runs.append(dict(run_id=2, filename="private-unrelated.csv", created_at="2026-02-01"))
        self.originals[2] = dict(filename="private-unrelated.csv", source_data=b"",
            config_snapshot=json.dumps(dict(source_file=dict(storage_key="missing-private.csv"))))

    def csv_rows(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))

    def test_assigned_options_scope_stations_pairs_and_hide_runs(self):
        response = self.client.get("/observation-analysis/options")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual({row["station_code"] for row in data["stations"]}, self.assigned_codes)
        self.assertEqual([pair["id"] for pair in data["pairs"]], ["M:A"])
        self.assertEqual(data["pairs"][0]["comparison_ids"], ["temperature"])
        self.assertEqual(data["pairs"][0]["manual_name"], "M")
        self.assertEqual(data["runs"], [])
        registry_call = self.cursor.execute.call_args_list[0]
        self.assertIn("WHERE a.user_id=%s", registry_call.args[0])
        self.assertEqual(registry_call.args[1], (9,))
        self.mysql.close.assert_called_once()

    def test_options_require_measurements_on_both_sides_for_element_choices(self):
        self.index_rows(2, "M,105,Humidity,2026-01-01 08:00,50\n")
        data = self.client.get("/observation-analysis/options").json()
        self.assertEqual(data["pairs"][0]["comparison_ids"], ["temperature"])
        self.index_rows(3, "A,1022,Humidity,2026-01-01 08:00,51\n")
        data = self.client.get("/observation-analysis/options").json()
        self.assertEqual(data["pairs"][0]["comparison_ids"], ["temperature", "humidity"])

    def test_unrestricted_options_include_all_stations_and_runs(self):
        self.user["department"] = "Admin"
        response = self.client.get("/observation-analysis/options")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(len(data["stations"]), 5)
        self.assertEqual({pair["id"] for pair in data["pairs"]}, {"M:A", "PRIVATE-M:PRIVATE-A"})
        self.assertEqual(data["runs"], self.runs)

    def test_unassigned_pair_rejected_even_when_it_exists_in_index(self):
        response = self.comparison(pair_id="M:A|PRIVATE-M:PRIVATE-A")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertNotIn("45", response.text)

    def test_unassigned_station_rejected_even_when_it_exists_in_index(self):
        response = self.monitoring(station_code="A|PRIVATE-A")
        self.assertEqual(response.status_code, 422, response.text)

    def test_no_assignments_return_no_observation_data(self):
        self.assigned_codes.clear()
        comparison = self.client.get("/observation-analysis/comparison")
        monitoring = self.client.get("/observation-analysis/monitoring")
        self.assertEqual(comparison.status_code, 200, comparison.text)
        self.assertEqual(monitoring.status_code, 200, monitoring.text)
        self.assertEqual(comparison.json()["rows"], [])
        for section in ("rain_rows", "daily_max", "daily_rows", "wind_max", "series"):
            self.assertEqual(monitoring.json()[section], [])

    def test_password_change_required_before_database_access(self):
        self.user["must_change_password"] = True
        for path in ("options", "comparison", "monitoring"):
            with self.subTest(path=path):
                response = self.client.get("/observation-analysis/" + path)
                self.assertEqual(response.status_code, 403, response.text)
        self.connect.assert_not_called()

    def test_selected_pair_drives_default_range(self):
        response = self.comparison()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["end"], "2026-01-01 08:01")
        self.assertEqual(data["start"], "2025-12-01 00:00")
        self.assertEqual(data["summary"]["paired"], 1)
        paired = [row for row in data["rows"] if row["difference"] is not None]
        self.assertEqual([(row["manual"], row["automatic"]) for row in paired], [(20, 21)])

    def test_missing_unrelated_source_warns_and_later_upload_still_indexes(self):
        self.add_missing_source()
        self.runs.append(dict(run_id=3, filename="later.csv", created_at="2026-02-02"))
        self.originals[3] = dict(filename="later.csv", config_snapshot="{}",
            source_data=(HEADER + "A,1012,Temperature,2026-01-01 08:00,23\n").encode())
        with self.assertLogs(api.LOGGER, level="WARNING") as logs:
            response = self.comparison()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        paired = [row for row in data["rows"] if row["difference"] is not None]
        self.assertEqual([(row["manual"], row["automatic"]) for row in paired], [(20, 23)])
        self.assertTrue(any("could not be indexed" in warning for warning in data["warnings"]))
        self.assertNotIn("private-unrelated.csv", response.text)
        self.assertNotIn("missing-private.csv", response.text)
        self.assertTrue(any("source run 2" in message for message in logs.output))
        connection = store.open_index(self.root)
        try:
            self.assertEqual(connection.execute("SELECT run_id FROM indexed_runs ORDER BY run_id").fetchall(),
                             [(1,), (3,)])
        finally:
            connection.close()

    def test_missing_source_warning_is_visible_in_comparison_csv(self):
        self.add_missing_source()
        with self.assertLogs(api.LOGGER, level="WARNING"):
            response = self.comparison(format="csv")
        rows = self.csv_rows(response)
        self.assertIn(["Warnings"], rows)
        self.assertTrue(any(len(row) == 1 and "could not be indexed" in row[0] for row in rows))
        self.assertNotIn("private-unrelated.csv", response.text)
        self.assertIn(["per_parameter"], rows)
        self.assertIn(["manual_wind_rose"], rows)
        self.assertIn(["automatic_wind_rose"], rows)

    def test_monitoring_csv_uses_available_daily_values_without_qc(self):
        self.index_rows(2, "A,1107,Rain,2026-01-01 08:00,10\n")
        result = self.monitoring()
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        self.assertTrue(data["daily_rows"])
        self.assertFalse(data["daily_rows"][0]["complete"])
        self.assertEqual(data["warnings"], [])
        self.assertEqual(data["summary"]["max_daily_rainfall"], 10)
        response = self.monitoring(format="csv")
        rows = self.csv_rows(response)
        self.assertIn(["daily_rows"], rows)
        self.assertNotIn(["Warnings"], rows)
        for warning in data["warnings"]:
            self.assertIn([warning], rows)
        offset = rows.index(["daily_rows"])
        daily_row = dict(zip(rows[offset + 1], rows[offset + 2]))
        self.assertEqual(daily_row["station_code"], "A")
        self.assertNotIn("complete", daily_row)
        self.assertEqual(daily_row["automatic_count"], "1")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_inclusive_day_filters_include_last_day_hours(self):
        self.index_rows(2, "M,101,Temperature,2026-01-02 23:00,22\nA,1012,Temperature,2026-01-02 23:00,23\n")
        response = self.comparison(start="2026-01-01", end="2026-01-02", days="true")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["start_day"], "2026-01-01")
        self.assertEqual(data["end_day"], "2026-01-02")
        self.assertEqual(data["end"], "2026-01-03 00:00")
        self.assertEqual(data["series"][-1]["manual"], 22)
        self.assertEqual(data["series"][-1]["automatic"], 23)

    def test_regression_line_and_r_squared_are_returned(self):
        self.index_rows(2, "M,101,Temperature,2026-01-01 09:00,22\nA,1012,Temperature,2026-01-01 09:00,25\n")
        data = self.comparison(start="2026-01-01", end="2026-01-01", days=True).json()
        self.assertAlmostEqual(data["summary"]["r_squared"], 1)
        self.assertAlmostEqual(data["per_parameter"][0]["regression_slope"], 2)
        self.assertAlmostEqual(data["per_parameter"][0]["regression_intercept"], -19)

    def test_maps_use_all_authorized_stations_with_manual_preference_and_met_day(self):
        self.stations.append(dict(station_code="B", station_name="B", station_category="Automatic Weather station",
                                  latitude=4, longitude=1, altitude=100))
        self.assigned_codes.add("B")
        self.index_rows(2, "M,5,Rain,2026-01-01 08:00,3\n"
            "A,1107,Rain,2026-01-01 08:00,999\n"
            "M,196,Wind,2026-01-01 08:00,4\nM,196,Wind,2026-01-01 08:10,999\n"
            "B,1107,Rain,2026-01-01 07:50,999\nB,1107,Rain,2026-01-01 08:00,7\n"
            "B,1107,Rain,2026-01-02 07:50,8\nB,1107,Rain,2026-01-02 08:00,999\n")
        response = self.client.get("/observation-analysis/maps", params=dict(start="2026-01-01", end="2026-01-01", station_code="A"))
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual({row["station_code"] for row in data["stations"]}, {"M", "OTHER", "B"})
        rains = {row["station_code"]: row["value"] for row in data["daily_rows"]}
        self.assertEqual(rains["M"], 3)
        self.assertEqual(rains["B"], 15)
        self.assertIsNone(rains["OTHER"])
        self.assertEqual(data["series"][0]["wind"], 4)
        self.assertEqual(data["start_day"], "2026-01-01")
        self.assertEqual(data["end_day"], "2026-01-01")

    def test_sqlite_failure_returns_retryable_503(self):
        self.open_index.side_effect = sqlite3.OperationalError("database is locked")
        for request in (self.comparison, self.monitoring):
            with self.subTest(endpoint=request.__name__):
                response = request()
                self.assertEqual(response.status_code, 503, response.text)
                self.assertIn("retry", response.json()["detail"].lower())
        self.assertEqual(self.mysql.close.call_count, 2)


if __name__ == "__main__":
    unittest.main()
