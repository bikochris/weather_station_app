import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from backend.main import (HistoryExportColumn, RecordHistoryExport, audit_snapshot, export_record_history,
                          export_reporting_history, get_record_history, record_entity_edit)


class RecordHistoryTests(unittest.TestCase):
    @patch("backend.main.get_connection")
    @patch("backend.main.ensure_application_tables")
    def test_history_returns_saved_differences(self, _ensure, get_connection):
        cursor = get_connection.return_value.cursor.return_value
        cursor.fetchall.return_value = [{
            "history_id": 1,
            "before_data": '{"status": "Operational"}',
            "after_data": '{"status": "Suspended"}',
            "changed_by_username": "tester",
        }]
        result = get_record_history("stations", "5", page=1, page_size=25,
                                    user={"department": "Admin", "user_id": 1})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["after_data"]["status"], "Suspended")

    def test_user_history_is_admin_only(self):
        with self.assertRaises(HTTPException) as error:
            get_record_history("users", "5", page=1, page_size=25,
                               user={"department": "Observation Officer", "user_id": 2})
        self.assertEqual(error.exception.status_code, 403)

    @patch("backend.main.audit_snapshot")
    @patch("backend.main.get_connection")
    @patch("backend.main.ensure_application_tables")
    def test_assigned_role_cannot_read_other_station_history(self, _ensure, get_connection, snapshot):
        cursor = get_connection.return_value.cursor.return_value
        cursor.fetchall.side_effect = [[], [{"station_id": 10}]]
        snapshot.return_value = {"station_id": 20}
        with self.assertRaises(HTTPException) as error:
            get_record_history("maintenance", "8", page=1, page_size=25,
                               user={"department": "Observation Officer", "user_id": 2})
        self.assertEqual(error.exception.status_code, 403)

    def test_user_snapshot_excludes_password_hash(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {
            "user_id": 5, "username": "tester", "password_hash": "secret-hash"
        }
        cursor.fetchall.return_value = [{"station_id": 3}]
        snapshot = audit_snapshot(connection, "users", 5)
        self.assertNotIn("password_hash", snapshot)
        self.assertEqual(snapshot["assigned_station_ids"], [3])

    def test_audit_payload_has_actor_and_before_after(self):
        connection = MagicMock()
        record_entity_edit(connection, "stations", 5, {"status": "Operational"},
                           {"status": "Suspended"}, {"username": "tester", "edit_reason": "Sensor offline"})
        values = connection.cursor.return_value.execute.call_args.args[1]
        self.assertEqual(values[:2], ("stations", "5"))
        self.assertEqual(json.loads(values[2])["status"], "Operational")
        self.assertEqual(json.loads(values[3])["status"], "Suspended")
        self.assertEqual(values[4], "tester")
        self.assertEqual(values[5], "Sensor offline")

    def test_audit_rejects_edit_without_reason(self):
        connection = MagicMock()
        with self.assertRaises(HTTPException) as error:
            record_entity_edit(connection, "stations", 5, {"status": "Operational"},
                               {"status": "Suspended"}, {"username": "tester"})
        self.assertEqual(error.exception.status_code, 422)
        connection.cursor.assert_not_called()

    def test_first_record_does_not_require_reason(self):
        connection = MagicMock()
        record_entity_edit(connection, "stations", 5, {},
                           {"status": "Operational"}, {"username": "tester"})
        values = connection.cursor.return_value.execute.call_args.args[1]
        self.assertIsNone(values[5])

    def test_first_volunteer_file_does_not_require_reason(self):
        connection = MagicMock()
        before = {"files": [], "comments": []}
        after = {"files": [{"file_kind": "monthly_qc", "original_filename": "first.pdf"}],
                 "comments": []}
        record_entity_edit(connection, "volunteer_data", "2026-09", before, after,
                           {"username": "tester"})
        self.assertIsNone(connection.cursor.return_value.execute.call_args.args[1][5])

    def test_replacing_volunteer_file_requires_reason(self):
        connection = MagicMock()
        before = {"files": [{"file_kind": "monthly_qc", "original_filename": "first.pdf"}]}
        after = {"files": [{"file_kind": "monthly_qc", "original_filename": "revised.pdf"}]}
        with self.assertRaises(HTTPException) as error:
            record_entity_edit(connection, "volunteer_data", "2026-09", before, after,
                               {"username": "tester"})
        self.assertEqual(error.exception.status_code, 422)

    def test_volunteer_snapshot_contains_metadata_not_file_bytes(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.side_effect = [
            [{"file_id": 2, "original_filename": "report.pdf", "file_size": 100}],
            [{"comment_id": 4, "comment": "Reviewed"}],
        ]
        snapshot = audit_snapshot(connection, "volunteer_data", "2026-09")
        self.assertEqual(snapshot["files"][0]["original_filename"], "report.pdf")
        self.assertNotIn("file_data", json.dumps(snapshot))
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(all("file_data" not in statement for statement in statements))

    def test_volunteer_history_rejects_unrelated_role(self):
        with self.assertRaises(HTTPException) as error:
            get_record_history("volunteer_data", "2026-09", page=1, page_size=25,
                               user={"department": "Observation Officer", "user_id": 2})
        self.assertEqual(error.exception.status_code, 403)

    @patch("backend.main.csv_download")
    @patch("backend.main.get_connection")
    @patch("backend.main.ensure_application_tables")
    def test_export_includes_full_saved_row_and_changed_values(self, _ensure, get_connection, csv_download):
        cursor = get_connection.return_value.cursor.return_value
        cursor.fetchall.side_effect = [[{
            "entity_id": "5", "before_data": '{"station_id": 9, "status": "Operational"}',
            "after_data": '{"station_id": 9, "status": "Suspended"}',
            "changed_by_username": "tester", "changed_at": "2026-10-03",
        }], [{"station_id": 9, "station_code": "ST-9", "station_name": "Central",
              "province": "Kigali", "district": "Gasabo", "sector": "Sector",
              "station_category": "Principal Station"}]]
        columns = [HistoryExportColumn(label="Station ID", source="station_id"),
                   HistoryExportColumn(label="Station status", source="status"),
                   HistoryExportColumn(label="Actions")]
        export_record_history(RecordHistoryExport(entity_type="stations", entity_ids=["5"], columns=columns),
                              user={"department": "Admin", "user_id": 1})
        headers, rows = csv_download.call_args.args[1:]
        self.assertEqual(headers, ["Station ID", "Station status", "Actions"])
        self.assertEqual(rows, [["ST-9", "Suspended", ""]])

    def test_export_respects_history_permissions(self):
        with self.assertRaises(HTTPException) as error:
            export_record_history(RecordHistoryExport(entity_type="users", entity_ids=["5"]),
                                  user={"department": "Observation Officer", "user_id": 2})
        self.assertEqual(error.exception.status_code, 403)

    @patch("backend.main.csv_download")
    @patch("backend.main.audit_snapshot")
    @patch("backend.main.get_connection")
    @patch("backend.main.ensure_application_tables")
    def test_export_excludes_unassigned_station_history(self, _ensure, get_connection, snapshot, csv_download):
        cursor = get_connection.return_value.cursor.return_value
        cursor.fetchall.side_effect = [
            [{"entity_id": "8", "before_data": '{"station_id": 20}',
              "after_data": '{"station_id": 20}', "changed_by_username": "tester",
              "changed_at": "2026-10-03"}],
            [{"station_id": 10}],
        ]
        snapshot.return_value = {"station_id": 20}
        export_record_history(RecordHistoryExport(entity_type="maintenance", entity_ids=["8"]),
                              user={"department": "Observation Officer", "user_id": 2})
        self.assertEqual(csv_download.call_args.args[2], [])

    @patch("backend.main.csv_download")
    @patch("backend.main.get_connection")
    @patch("backend.main.ensure_application_tables")
    def test_reporting_export_contains_full_snapshot(self, _ensure, get_connection, csv_download):
        cursor = get_connection.return_value.cursor.return_value
        cursor.fetchall.return_value = [{"report_month": "2026-09", "action": "Updated",
            "before_data": '{"reported_stations": 20, "notes": "Original"}',
            "after_data": '{"reported_stations": 21, "notes": "Original"}',
            "changed_by_username": "tester", "changed_at": "2026-10-03"}]
        export_reporting_history(report_month="2026-09", _user={"department": "Admin"})
        headers, rows = csv_download.call_args.args[1:]
        self.assertEqual(headers, ["Month", "Operational", "Under maintenance", "Suspended",
            "Expected", "Reported", "Pending", "Reporting %", "Non-reported list",
            "Recorded by", "Notes", "Actions"])
        self.assertEqual(rows[0][headers.index("Notes")], "Original")
        self.assertEqual(rows[0][headers.index("Reported")], 21)


if __name__ == "__main__":
    unittest.main()
