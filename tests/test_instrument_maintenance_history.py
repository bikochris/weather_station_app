import json
import unittest
from unittest.mock import patch, MagicMock

from fastapi import HTTPException

from backend.main import export_maintenance, get_instrument_maintenance_history


class InstrumentMaintenanceHistoryTests(unittest.TestCase):
    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_history_contains_only_this_instruments_edits(self, get_connection, _ensure):
        cursor = get_connection.return_value.cursor.return_value
        cursor.fetchone.return_value = {"station_id": 7}
        cursor.fetchall.side_effect = [
            [{"maintenance_id": 11, "maintenance_date": "2026-09-30", "issue": "Fault"}],
            [{"before_data": json.dumps({"station_id": 7, "instrument_details": [
                {"instrument_id": 3, "issue": "Fault", "action_done": "Check"},
                {"instrument_id": 4, "issue": "Other", "action_done": "Clean"}]}),
              "after_data": json.dumps({"station_id": 7, "instrument_details": [
                {"instrument_id": 3, "issue": "Fault", "action_done": "Replaced"},
                {"instrument_id": 4, "issue": "Other", "action_done": "Clean"}]}),
              "changed_by_username": "tech", "changed_at": "2026-09-30 12:00:00"}],
        ]
        with patch("backend.main.ensure_user_can_access_station"):
            result = get_instrument_maintenance_history(7, 3,
                user={"department": "Admin", "user_id": 1})
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["items"][0]["changes"]), 1)
        self.assertEqual(result["items"][0]["changes"][0]["after"]["action_done"], "Replaced")

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_assigned_officer_cannot_read_other_station(self, get_connection, _ensure):
        cursor = get_connection.return_value.cursor.return_value
        cursor.fetchone.return_value = {"station_id": 7}
        with patch("backend.main.ensure_user_can_access_station",
                   side_effect=HTTPException(status_code=403, detail="Not assigned")):
            with self.assertRaises(HTTPException) as error:
                get_instrument_maintenance_history(7, 3,
                    user={"department": "Observation Officer", "user_id": 9})
        self.assertEqual(error.exception.status_code, 403)

    @patch("backend.main.csv_download")
    @patch("backend.main.get_maintenance_records")
    def test_export_has_one_row_per_instrument(self, get_records, csv_download):
        get_records.return_value = [{
            "maintenance_id": 11, "station_code": "ST-1", "station_name": "Station",
            "maintenance_date": "2026-09-30", "technicians": "Team", "recorded_by": "tech",
            "instruments": [
                {"instrument_name": "Battery", "issue": "Low", "action_done": "Replaced", "recommendation": None},
                {"instrument_name": "Gauge", "issue": "Blocked", "action_done": "Cleaned", "recommendation": "Recheck"},
            ],
        }]
        export_maintenance("csv", None, None, "maintenance_date", "desc", None,
                           None, {"department": "Admin", "username": "tester"}, None)
        rows = csv_download.call_args.args[2]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][3:6], ["Battery", "Low", "Replaced"])
        self.assertEqual(rows[1][3:6], ["Gauge", "Blocked", "Cleaned"])


if __name__ == "__main__":
    unittest.main()
