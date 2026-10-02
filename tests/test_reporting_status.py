import unittest
import asyncio
import json
from datetime import date
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from backend.main import (
    MonthlyReportingStatus, get_reporting_history, get_reporting_status,
    reporting_percentage, save_reporting_status, delete_reporting_status,
    upload_non_reported_station_file,
)


class ReportingStatusTests(unittest.TestCase):
    def test_percentage_uses_operational_over_expected(self):
        self.assertEqual(reporting_percentage(8, 13), 61.5)
        self.assertIsNone(reporting_percentage(None, 10))
        self.assertEqual(reporting_percentage(0, 0), 0)

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_save_includes_operational_maintenance_and_suspended_in_expected(self, get_connection, _ensure):
        connection = get_connection.return_value
        connection.cursor.return_value.fetchone.side_effect = [
            {"operational_stations": 8, "under_maintenance_stations": 2, "suspended_stations": 3},
            None,
            {"reporting_status_id": 4, "report_month": "2026-09", "expected_stations": 13,
             "reported_stations": 9, "notes": None},
        ]
        item = MonthlyReportingStatus(report_month="2026-09", reported_stations=9)
        save_reporting_status(item, {"user_id": 1, "username": "tester"})
        calls = connection.cursor.return_value.execute.call_args_list
        values = next(call.args[1] for call in calls if "INSERT INTO monthly_reporting_status" in call.args[0])
        self.assertEqual(values[1:6], (13, 8, 2, 3, 9))
        audit = next(call.args[1] for call in calls if "INSERT INTO monthly_reporting_changes" in call.args[0])
        self.assertEqual(audit[2], "Created")
        self.assertEqual(json.loads(audit[4])["reported_stations"], 9)
        connection.commit.assert_called_once()

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_reported_cannot_exceed_expected(self, get_connection, _ensure):
        get_connection.return_value.cursor.return_value.fetchone.return_value = {
            "operational_stations": 8, "under_maintenance_stations": 2, "suspended_stations": 3,
        }
        item = MonthlyReportingStatus(report_month="2026-09", reported_stations=14)
        with self.assertRaises(HTTPException) as error:
            save_reporting_status(item, {"user_id": 1, "username": "tester"})
        self.assertEqual(error.exception.status_code, 400)

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_read_keeps_legacy_percentage_unavailable(self, get_connection, _ensure):
        cursor = get_connection.return_value.cursor.return_value
        cursor.fetchone.return_value = {"total_stations": 15, "operational_stations": 8,
                                        "under_maintenance_stations": 2, "suspended_stations": 3,
                                        "closed_stations": 2}
        cursor.fetchall.side_effect = [[
            {"report_month": "2026-09", "expected_stations": 8, "operational_stations": None,
             "under_maintenance_stations": None, "suspended_stations": None, "reported_stations": 7},
            {"report_month": "2026-10", "expected_stations": 10, "operational_stations": 8,
             "under_maintenance_stations": 2, "suspended_stations": None, "reported_stations": 9},
            {"report_month": "2026-11", "expected_stations": 13, "operational_stations": 8,
             "under_maintenance_stations": 2, "suspended_stations": 3, "reported_stations": 9},
        ], []]
        result = get_reporting_status({"department": "Admin"})
        self.assertEqual(result["total_stations"], 15)
        self.assertEqual(result["closed_stations"], 2)
        self.assertEqual(result["expected_stations"], 13)
        self.assertEqual(result["current_reporting_percent"], 61.5)
        self.assertIsNone(result["items"][0]["reporting_percent"])
        self.assertIsNone(result["items"][0]["expected_stations"])
        self.assertIsNone(result["items"][1]["reporting_percent"])
        self.assertEqual(result["items"][2]["reporting_percent"], 61.5)
        self.assertEqual(result["items"][2]["pending_stations"], 4)

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_current_panel_does_not_depend_on_monthly_records(self, get_connection, _ensure):
        cursor = get_connection.return_value.cursor.return_value
        cursor.fetchone.return_value = {"total_stations": 4, "operational_stations": 2,
                                        "under_maintenance_stations": 1, "suspended_stations": 1,
                                        "closed_stations": 0}
        cursor.fetchall.side_effect = [[], []]
        result = get_reporting_status({"department": "Admin"})
        self.assertEqual(result["items"], [])
        self.assertEqual(result["expected_stations"], 4)
        self.assertEqual(result["current_reporting_percent"], 50.0)

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_edit_records_previous_and_new_values(self, get_connection, _ensure):
        connection = get_connection.return_value
        connection.cursor.return_value.fetchone.side_effect = [
            {"operational_stations": 8, "under_maintenance_stations": 2, "suspended_stations": 3},
            {"reporting_status_id": 4, "report_month": "2026-09", "reported_stations": 6},
            {"reporting_status_id": 4, "report_month": "2026-09", "reported_stations": 9},
        ]
        save_reporting_status(MonthlyReportingStatus(report_month="2026-09", reported_stations=9),
                              {"user_id": 1, "username": "tester"})
        calls = connection.cursor.return_value.execute.call_args_list
        audit = next(call.args[1] for call in calls if "INSERT INTO monthly_reporting_changes" in call.args[0])
        self.assertEqual(audit[2], "Updated")
        self.assertEqual(json.loads(audit[3])["reported_stations"], 6)
        self.assertEqual(json.loads(audit[4])["reported_stations"], 9)

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_history_returns_structured_changes(self, get_connection, _ensure):
        cursor = get_connection.return_value.cursor.return_value
        cursor.fetchone.return_value = {"total": 1}
        cursor.fetchall.return_value = [{"change_id": 1, "before_data": '{"reported_stations": 6}',
                                         "after_data": '{"reported_stations": 9}'}]
        result = get_reporting_history(page=1, page_size=25, report_month=None,
                                       sort_by="action", sort_order="asc", _user={})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["before_data"]["reported_stations"], 6)
        self.assertIn("ORDER BY action ASC", cursor.execute.call_args_list[-1].args[0])

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_delete_keeps_an_audit_snapshot(self, get_connection, _ensure):
        connection = get_connection.return_value
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [
            {"report_month": date(2026, 9, 1)},
            {"reporting_status_id": 4, "report_month": "2026-09", "reported_stations": 9},
            None,
        ]
        delete_reporting_status(4, {"user_id": 1, "username": "tester"})
        audit = next(call.args[1] for call in cursor.execute.call_args_list
                     if "INSERT INTO monthly_reporting_changes" in call.args[0])
        self.assertEqual(audit[2], "Deleted")
        self.assertEqual(json.loads(audit[3])["reported_stations"], 9)
        connection.commit.assert_called_once()

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_file_replacement_is_recorded(self, get_connection, _ensure):
        connection = get_connection.return_value
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [(4,), ("old.csv", 100, "first-user")]
        request = AsyncMock()
        request.body.return_value = b"station_id\n1\n"
        request.headers = {"content-type": "text/csv"}
        asyncio.run(upload_non_reported_station_file(
            request, report_month="2026-09", filename="new.csv",
            user={"user_id": 1, "username": "tester"},
        ))
        audit = next(call.args[1] for call in cursor.execute.call_args_list
                     if "INSERT INTO monthly_reporting_changes" in call.args[0])
        self.assertEqual(audit[2], "File uploaded")
        self.assertEqual(json.loads(audit[3])["filename"], "old.csv")
        self.assertEqual(json.loads(audit[4])["filename"], "new.csv")
        connection.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
