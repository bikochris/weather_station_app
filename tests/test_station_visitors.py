import unittest
from datetime import date
from unittest.mock import patch

from pydantic import ValidationError

from backend.main import StationVisitor, list_station_visitors, save_visitor_record


class StationVisitorTests(unittest.TestCase):
    def setUp(self):
        self.item = StationVisitor(station_id=7, visit_date=date(2026, 9, 30),
            institution="Rwanda University", mission="Training", category_id=2,
            visitor_count=12)
        self.user = {"department": "Admin", "user_id": 1, "username": "admin"}

    def test_visit_date_and_institution_are_required(self):
        with self.assertRaises(ValidationError):
            StationVisitor(station_id=7, mission="Training", category_id=2,
                           visitor_count=12)

    @patch("backend.main.record_entity_edit")
    @patch("backend.main.audit_snapshot")
    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_save_uses_one_day_for_both_legacy_columns(self, get_connection, _ensure,
                                                        _snapshot, _audit):
        cursor = get_connection.return_value.cursor.return_value
        cursor.fetchone.side_effect = [(1,), (1,)]
        cursor.lastrowid = 21
        save_visitor_record(self.item, self.user)
        insert = next(call for call in cursor.execute.call_args_list
                      if "INSERT INTO station_visitors" in call.args[0])
        values = insert.args[1]
        self.assertEqual(values[1:3], (date(2026, 9, 30), date(2026, 9, 30)))
        self.assertEqual(values[3], "Rwanda University")

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_list_keeps_legacy_end_date_and_filters_on_visit_day(self, get_connection, _ensure):
        cursor = get_connection.return_value.cursor.return_value
        cursor.fetchall.return_value = [{"visitor_id": 2, "station_id": 7,
            "visitor_count": 12, "visit_date": date(2026, 9, 1),
            "legacy_period_end": date(2026, 9, 3), "institution": ""}]
        result = list_station_visitors(month_from="2026-09", institution="University",
                                       user=self.user)
        self.assertEqual(result["items"][0]["legacy_period_end"], date(2026, 9, 3))
        query, params = cursor.execute.call_args.args
        self.assertIn("v.period_start AS visit_date", query)
        self.assertIn("v.institution LIKE %s", query)
        self.assertIn("v.period_start >= %s", query)
        self.assertEqual(params, ("%University%", "2026-09-01"))


if __name__ == "__main__":
    unittest.main()
