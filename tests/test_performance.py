import unittest
from datetime import date
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from pydantic import ValidationError
from mysql.connector import IntegrityError
from backend.performance import PerformanceInput, PerformanceBatch, PerformanceBulkMonth, performance_summary
from backend.main import performance_records, save_station_performance, save_performance_batch, download_performance_report, update_performance_month
from backend.deleted_items import _read_rows


class PerformanceTests(unittest.TestCase):
    def test_shared_reports_grouped_without_duplicate_files(self):
        rows = [{"performance_id": station, "station_id": station, "station_code": str(station), "station_name": f"Station {station}",
                 "report_month": "2026-07-01", "score": 80, "report_id": 7, "original_filename": "July.pdf"} for station in [1, 2]]
        reports = performance_summary(rows)["reports"]
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["filename"], "July.pdf")
        self.assertEqual(reports[0]["months"], ["2026-07"])
        self.assertEqual(len(reports[0]["stations"]), 2)

    @patch("backend.main.record_entity_edit")
    @patch("backend.main.audit_snapshot", return_value={"report_month": "2026-07-01"})
    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_bulk_month_preserves_scores_reports_and_audits(self, connect, _ensure, _snapshot, audit):
        cursor = connect.return_value.cursor.return_value
        cursor.fetchall.side_effect = [[{"performance_id": 1, "station_id": 1, "report_month": date(2026, 10, 1), "created_by_user_id": 7},
                                       {"performance_id": 2, "station_id": 2, "report_month": date(2026, 10, 1), "created_by_user_id": 7}], []]
        result = update_performance_month(PerformanceBulkMonth(performance_ids=[1, 2], report_month="2026-07"),
                                         {"department": "Admin", "user_id": 7, "username": "tester", "edit_reason": "Correct month"})
        self.assertEqual(result, {"updated": 2, "unchanged": 0})
        updates = [call.args[0] for call in cursor.execute.call_args_list if call.args[0].startswith("UPDATE")]
        self.assertEqual(len(updates), 2)
        self.assertTrue(all("score=" not in query and "file_data=" not in query for query in updates))
        self.assertEqual(audit.call_count, 2)

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_bulk_month_conflict_rolls_back(self, connect, _ensure):
        cursor = connect.return_value.cursor.return_value
        cursor.fetchall.side_effect = [[{"performance_id": 1, "station_id": 1, "report_month": date(2026, 10, 1), "created_by_user_id": 7}], [{"performance_id": 2}]]
        with self.assertRaises(HTTPException) as error:
            update_performance_month(PerformanceBulkMonth(performance_ids=[1], report_month="2026-07"), {"department": "Admin", "user_id": 7})
        self.assertEqual(error.exception.status_code, 409)
        connect.return_value.commit.assert_not_called()
        self.assertFalse(any(call.args[0].startswith("UPDATE") for call in cursor.execute.call_args_list))

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_bulk_month_cannot_edit_another_creator(self, connect, _ensure):
        cursor = connect.return_value.cursor.return_value
        cursor.fetchall.return_value = [{"performance_id": 1, "station_id": 1, "report_month": date(2026, 10, 1), "created_by_user_id": 9}]
        with self.assertRaises(HTTPException) as error:
            update_performance_month(PerformanceBulkMonth(performance_ids=[1], report_month="2026-07"), {"department": "Division Manager", "user_id": 7})
        self.assertEqual(error.exception.status_code, 403)

    def test_batch_validation(self):
        self.assertEqual(len(PerformanceBatch(stations=[{"station_id": 1, "score": 0}, {"station_id": 2, "score": 100}]).stations), 2)
        for stations in [[], [{"station_id": 1, "score": 90}] * 2, [{"station_id": 1, "score": 101}]]:
            with self.assertRaises(ValidationError): PerformanceBatch(stations=stations)

    def test_shared_report_backed_up_as_standalone_file(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"performance_id": 1, "report_id": 7, "file_data": b""}]
        cursor.fetchone.return_value = {"file_data": b"shared report"}
        rows = _read_rows(cursor, "station_performance", "performance_id", 1)
        self.assertEqual(rows[0]["file_data"], b"shared report")
        self.assertIsNone(rows[0]["report_id"])
    def test_score_validation(self):
        self.assertEqual(PerformanceInput(station_id=1, report_month="2026-07-20", score=0).report_month, date(2026, 7, 1))
        self.assertEqual(PerformanceInput(station_id=1, report_month="2026-07-01", score=100).score, 100)
        for score in [-1, 101, float("nan"), float("inf")]:
            with self.assertRaises(ValidationError):
                PerformanceInput(station_id=1, report_month="2026-07-01", score=score)

    def test_averages_and_fiscal_boundaries(self):
        rows = [{"station_id": 1, "station_code": "A", "station_name": "Alpha", "score": score, "report_month": month}
                for score, month in [(0, "2026-06-01"), (80, "2026-07-01"), (100, "2026-09-01"), (60, "2026-10-01"), (70, "2027-01-01"), (90, "2027-04-01")]]
        result = performance_summary(rows)
        self.assertEqual(result["averages"]["quarters"]["2026/2027 Q1"], 90)
        self.assertEqual(result["averages"]["years"]["2026/2027"], 80)
        self.assertEqual(result["averages"]["years"]["2025/2026"], 0)
        self.assertEqual(result["station_count"], 1)
        self.assertIsNone(performance_summary([])["average"])

    def test_period_scores_keep_stations_separate(self):
        rows = [{"station_id": station, "station_code": str(station), "station_name": f"Station {station}", "score": score, "report_month": month}
                for station, score, month in [(1, 80, "2026-07-01"), (1, 100, "2026-08-01"),
                                              (2, 40, "2026-07-01"), (1, 60, "2026-10-01"), (2, 0, "2026-10-01")]]
        periods = performance_summary(rows)["station_period_averages"]
        self.assertEqual(periods["months"]["2026-07"], {"1": 80, "2": 40})
        self.assertEqual(periods["months"]["2026-08"], {"1": 100})
        self.assertEqual(periods["quarters"]["2026/2027 Q1"], {"1": 90, "2": 40})
        self.assertEqual(periods["quarters"]["2026/2027 Q2"], {"1": 60, "2": 0})
        self.assertEqual(periods["years"]["2026/2027"], {"1": 80, "2": 20})
        self.assertEqual(performance_summary([])["station_period_averages"], {"months": {}, "quarters": {}, "years": {}})

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_assigned_scope_and_filters(self, connect, _ensure):
        cursor = connect.return_value.cursor.return_value
        cursor.fetchall.return_value = []
        performance_records({"department": "Observation Officer", "user_id": 7}, district="Gasabo|Bugesera", station_id="1|2")
        query, params = cursor.execute.call_args.args
        self.assertIn("user_station_assignments", query)
        self.assertIn("s.district IN (%s, %s)", query)
        self.assertEqual(params, (7, "Gasabo", "Bugesera", "1", "2"))
        self.assertNotIn("file_data", query)

    @patch("backend.main.get_connection")
    def test_reversed_month_range(self, connect):
        with self.assertRaises(HTTPException):
            performance_records({"department": "Admin"}, month_from="2026-10", month_to="2026-09")
        connect.assert_not_called()

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_report_download_checks_assignment_before_bytes(self, connect, _ensure):
        cursor = connect.return_value.cursor.return_value
        cursor.fetchone.side_effect = [{"station_id": 1}, None]
        with self.assertRaises(HTTPException) as error:
            download_performance_report(1, {"department": "Observation Officer", "user_id": 7})
        self.assertEqual(error.exception.status_code, 403)
        self.assertFalse(any("file_data" in call.args[0] for call in cursor.execute.call_args_list))


class PerformanceSaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_one_file_and_separate_scores(self):
        with patch("backend.main.get_connection") as connect, patch("backend.main.ensure_application_tables"):
            cursor = connect.return_value.cursor.return_value
            cursor.fetchall.side_effect = [[{"station_id": 1}, {"station_id": 2}], []]
            cursor.lastrowid = 77
            result = await save_performance_batch(self.request(), '{"stations":[{"station_id":1,"score":85},{"station_id":2,"score":70}]}', "2026-10", "report.pdf", "Notes",
                {"department": "Admin", "user_id": 1, "username": "admin"})
            self.assertEqual(result["saved"], 2)
            query, rows = cursor.executemany.call_args.args
            self.assertEqual([row[3] for row in rows], [85, 70])
            self.assertEqual([row[1] for row in rows], [77, 77])
            self.assertTrue(all(row[8] == b"" for row in rows))
            self.assertEqual(query.count("%s"), len(rows[0]))
            connect.return_value.commit.assert_called_once()

    async def test_batch_duplicate_saves_nothing(self):
        with patch("backend.main.get_connection") as connect, patch("backend.main.ensure_application_tables"):
            cursor = connect.return_value.cursor.return_value
            cursor.fetchall.side_effect = [[{"station_id": 1}, {"station_id": 2}], [{"station_id": 2}]]
            with self.assertRaises(HTTPException) as error:
                await save_performance_batch(self.request(), '{"stations":[{"station_id":1,"score":85},{"station_id":2,"score":70}]}', "2026-10", "report.pdf", "",
                    {"department": "Admin", "user_id": 1, "username": "admin"})
            self.assertEqual(error.exception.status_code, 409)
            cursor.executemany.assert_not_called()
            connect.return_value.commit.assert_not_called()

    def request(self, method="POST"):
        request = MagicMock(); request.method = method
        async def stream(): yield b"report"
        request.stream = stream
        return request

    async def test_initial_report_required(self):
        with patch("backend.main.get_connection") as connect, patch("backend.main.ensure_application_tables"):
            connect.return_value.cursor.return_value.fetchone.return_value = {"station_id": 1}
            with self.assertRaises(HTTPException) as error:
                await save_station_performance(self.request(), 1, "2026-10", 90, notes="", filename=None, performance_id=None,
                    user={"department": "Admin", "user_id": 1, "username": "admin"})
            self.assertEqual(error.exception.status_code, 422)

    async def test_insert_and_duplicate(self):
        with patch("backend.main.get_connection") as connect, patch("backend.main.ensure_application_tables"):
            cursor = connect.return_value.cursor.return_value
            cursor.fetchone.return_value = {"station_id": 1}
            user = {"department": "Admin", "user_id": 1, "username": "admin"}
            await save_station_performance(self.request(), 1, "2026-10", 90, notes="", filename="report.pdf", performance_id=None, user=user)
            query, params = cursor.execute.call_args.args
            self.assertEqual(query.count("%s"), len(params))
            self.assertEqual(params[1], date(2026, 10, 1))
            cursor.execute.side_effect = [None, IntegrityError("duplicate")]
            with self.assertRaises(HTTPException) as error:
                await save_station_performance(self.request(), 1, "2026-10", 90, notes="", filename="report.pdf", performance_id=None, user=user)
            self.assertEqual(error.exception.status_code, 409)

    async def test_another_creator_cannot_edit(self):
        with patch("backend.main.get_connection") as connect, patch("backend.main.ensure_application_tables"):
            connect.return_value.cursor.return_value.fetchone.return_value = {"station_id": 1, "created_by_user_id": 2}
            with self.assertRaises(HTTPException) as error:
                await save_station_performance(self.request("PUT"), 1, "2026-10", 90, notes="", filename=None, performance_id=1,
                    user={"department": "Division Manager", "user_id": 1, "username": "manager"})
            self.assertEqual(error.exception.status_code, 403)


if __name__ == "__main__": unittest.main()
