import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from backend.main import (CombinedDataCount, Instrument, Station, StationCategoryInput,
                          update_station_category, validate_station_categories)


class StationCategoryTests(unittest.TestCase):
    def test_custom_category_is_accepted_by_models(self):
        station = Station.model_validate({
            "station_code": "S-1", "station_name": "Test", "latitude": 0,
            "longitude": 0, "altitude": 0, "province": "East", "district": "Bugesera",
            "sector": "Ruhuha", "station_category": "Agrometeorological Station",
            "status": "Operational",
        })
        instrument = Instrument.model_validate({
            "instrument_name": "Sensor", "parameters_taken": "Temperature",
            "category": "Sensing", "station_categories": ["Agrometeorological Station"],
        })
        count = CombinedDataCount.model_validate({
            "record_month": "2026-10",
            "station_categories": ["Agrometeorological Station", "Rainfall Station"],
            "record_count": 10,
        })
        self.assertEqual(station.station_category, instrument.station_categories[0])
        self.assertEqual(count.station_categories[0], station.station_category)

    def test_unknown_category_is_rejected(self):
        cursor = Mock()
        cursor.fetchall.return_value = [("Rainfall Station",)]
        with self.assertRaises(HTTPException) as error:
            validate_station_categories(cursor, ["Rainfall Station", "Unknown category"])
        self.assertEqual(error.exception.status_code, 400)

    def test_dictionary_cursor_is_supported(self):
        cursor = Mock()
        cursor.fetchall.return_value = [{"name": "Agrometeorological Station"}]
        validate_station_categories(cursor, ["Agrometeorological Station"])

    @patch("backend.main.record_entity_edit")
    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_rename_updates_linked_records_and_frequency_history(self, get_connection, _ensure, audit):
        connection = get_connection.return_value
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [("Old category",), None, None, None]
        cursor.fetchall.side_effect = [
            [(1, "2026-10-01", "Old category|Rainfall Station")],
            [(10, "2026|Old category")],
        ]
        result = update_station_category(
            9, StationCategoryInput(name="New category"),
            {"username": "admin", "edit_reason": "Correct category name"},
        )
        self.assertEqual(result["name"], "New category")
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        for table in ("stations", "instrument_station_categories", "category_maintenance_targets",
                      "monthly_category_data_counts", "monthly_combined_data_counts", "record_edit_history"):
            self.assertTrue(any(f"UPDATE {table}" in sql or f"UPDATE `{table}`" in sql for sql in statements), table)
        audit.assert_called_once()
        connection.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
