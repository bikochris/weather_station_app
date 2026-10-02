import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from backend.main import audit_snapshot, get_record_history, record_entity_edit


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
                           {"status": "Suspended"}, {"username": "tester"})
        values = connection.cursor.return_value.execute.call_args.args[1]
        self.assertEqual(values[:2], ("stations", "5"))
        self.assertEqual(json.loads(values[2])["status"], "Operational")
        self.assertEqual(json.loads(values[3])["status"], "Suspended")
        self.assertEqual(values[4], "tester")

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


if __name__ == "__main__":
    unittest.main()
