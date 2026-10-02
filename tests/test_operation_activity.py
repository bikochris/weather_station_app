import unittest
from unittest.mock import MagicMock, patch

from backend.activity import ActivityConnection, request_activity, set_activity_actor, suspend_activity


class OperationActivityTests(unittest.TestCase):
    def setUp(self):
        self.token = request_activity.set({"path": "/stations/3", "user": {
            "user_id": 1, "full_name": "Operator", "username": "operator", "department": "Admin"}})
        self.raw = MagicMock()
        self.connection = ActivityConnection(self.raw)

    def tearDown(self):
        request_activity.reset(self.token)

    def test_audit_written_before_commit(self):
        self.connection._changes = [{"table": "stations"}]
        calls = []
        with patch.object(self.connection, "write_activity", side_effect=lambda _: calls.append("audit")):
            self.raw.commit.side_effect = lambda: calls.append("commit")
            self.connection.commit()
        self.assertEqual(calls, ["audit", "commit"])
        self.assertEqual(self.connection._changes, [])

    def test_notification_failure_rolls_back_change(self):
        self.connection._changes = [{"table": "stations"}]
        with patch.object(self.connection, "write_activity", side_effect=RuntimeError("Notification failed")):
            with self.assertRaises(RuntimeError):
                self.connection.commit()
        self.raw.commit.assert_not_called()
        self.raw.rollback.assert_called_once()
        self.assertEqual(self.connection._changes, [])

    def test_failed_or_unchanged_write_is_not_recorded(self):
        cursor = MagicMock(rowcount=0)
        self.connection.capture_change({"table": "stations", "verb": "UPDATE"}, cursor)
        self.assertEqual(self.connection._changes, [])

    def test_schema_changes_do_not_generate_activity(self):
        @suspend_activity
        def schema_update():
            return self.connection.prepare_change("UPDATE stations SET status = %s WHERE station_id = %s", ("Closed", 3))
        self.assertIsNone(schema_update())

    def test_metadata_excludes_passwords_and_file_contents(self):
        cursor = self.raw.cursor.return_value
        cursor.fetchall.side_effect = [[{"Field": "user_id"}, {"Field": "password_hash"}, {"Field": "file_data"}], [{"user_id": 3}]]
        self.connection.metadata("users", "WHERE user_id = %s", (3,))
        statement = cursor.execute.call_args.args[0]
        self.assertNotIn("password_hash", statement)
        self.assertNotIn("file_data", statement)

    def test_recipient_deduplication_excludes_actor_and_inactive_users(self):
        cursor = self.raw.cursor.return_value
        cursor.fetchall.side_effect = [
            [{"user_id": 2}, {"user_id": 1}],
            [{"user_id": 3}, {"user_id": 2}],
            [{"user_id": 2}, {"user_id": 3}],
        ]
        self.connection._changes = [{"table": "stations", "verb": "UPDATE", "count": 1,
            "insert_id": None, "before": [{"station_id": 3, "created_by_user_id": 2}]}]
        with patch.object(self.connection, "metadata", return_value=[]):
            self.connection.write_activity(request_activity.get())
        recipients = [row[0] for row in cursor.executemany.call_args.args[1]]
        self.assertEqual(recipients, [2, 3])

    def test_report_deletion_preserves_station_recipients(self):
        with patch.object(self.connection, "metadata", side_effect=[
            [{"report_id": 7, "uploaded_by_user_id": 2}], [{"report_id": 7, "station_id": 9}]]):
            change = self.connection.prepare_change("DELETE FROM pre_maintenance_reports WHERE report_id = %s", (7,))
        self.assertEqual(change["before"][1]["station_id"], 9)

    def test_actor_context_does_not_store_credentials(self):
        set_activity_actor({"user_id": 1, "username": "operator", "password_hash": "secret", "access_token": "secret"})
        self.assertNotIn("password_hash", request_activity.get()["user"])
        self.assertNotIn("access_token", request_activity.get()["user"])


if __name__ == "__main__":
    unittest.main()
