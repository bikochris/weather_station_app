import unittest
from datetime import date

from fastapi import HTTPException

from backend.main import MaintenanceRecord, maintenance_instrument_payload


class MaintenanceInstrumentTests(unittest.TestCase):
    def test_distinct_details_are_kept_per_instrument(self):
        record = MaintenanceRecord(
            station_id=1, maintenance_date=date(2026, 9, 30), technicians="Team",
            instrument_ids=[3, 4], instrument_details=[
                {"instrument_id": 3, "issue": "Faulty battery", "action_done": "Replaced",
                 "recommendation": "Check voltage"},
                {"instrument_id": 4, "issue": "Loose cable", "action_done": "Reconnected",
                 "recommendation": None},
            ],
        )
        ids, details, issues, actions, recommendations = maintenance_instrument_payload(record)
        self.assertEqual(ids, [3, 4])
        self.assertEqual(details[0], (3, "Faulty battery", "Replaced", "Check voltage"))
        self.assertEqual(details[1], (4, "Loose cable", "Reconnected", None))
        self.assertIn("Loose cable", issues)
        self.assertIn("Replaced", actions)
        self.assertEqual(recommendations, "Check voltage")

    def test_legacy_shared_fields_remain_usable(self):
        record = MaintenanceRecord(
            station_id=1, maintenance_date=date(2026, 9, 30), technicians="Team",
            instrument_ids=[3, 4], issue="Routine check", activity_done="Cleaned",
        )
        _, details, *_ = maintenance_instrument_payload(record)
        self.assertEqual([item[1] for item in details], ["Routine check", "Routine check"])

    def test_selected_ids_must_match_details(self):
        record = MaintenanceRecord(
            station_id=1, maintenance_date=date(2026, 9, 30), technicians="Team",
            instrument_ids=[3, 4], instrument_details=[
                {"instrument_id": 3, "issue": "Fault", "action_done": "Fixed"},
            ],
        )
        with self.assertRaises(HTTPException) as error:
            maintenance_instrument_payload(record)
        self.assertEqual(error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
