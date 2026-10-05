import unittest
from unittest.mock import MagicMock, patch

from backend.main import add_filter_condition, filter_values, get_sites, matches_filter


class MultiFilterTests(unittest.TestCase):
    def test_splits_multiple_selected_values(self):
        self.assertEqual(filter_values("Bugesera|Burera"), ["Bugesera", "Burera"])
        self.assertEqual(filter_values(""), [])
        self.assertEqual(filter_values(None), [])

    def test_sql_condition_uses_placeholders_for_every_choice(self):
        conditions, parameters = [], []
        add_filter_condition(conditions, parameters, "stations.district", "Bugesera|Burera")
        self.assertEqual(conditions, ["stations.district IN (%s, %s)"])
        self.assertEqual(parameters, ["Bugesera", "Burera"])

    def test_python_filter_accepts_any_selected_value(self):
        self.assertTrue(matches_filter("Burera", "Bugesera|Burera"))
        self.assertFalse(matches_filter("Gasabo", "Bugesera|Burera"))
        self.assertTrue(matches_filter("Gasabo", ""))
        self.assertTrue(matches_filter(12, "11|12"))

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_sites_accept_multiple_districts_and_statuses(self, get_connection, _ensure):
        connection = MagicMock()
        get_connection.return_value = connection
        connection.cursor.return_value.fetchall.return_value = [
            {"station_id": 1, "station_code": "001", "station_name": "A",
             "latitude": -2.0, "longitude": 30.0, "altitude": 1400,
             "province": "Eastern", "district": "Bugesera", "sector": "A",
             "station_category": "Principal Station", "status": "Operational"},
            {"station_id": 2, "station_code": "002", "station_name": "B",
             "latitude": -1.0, "longitude": 29.0, "altitude": 1500,
             "province": "Northern", "district": "Burera", "sector": "B",
             "station_category": "Principal Station", "status": "Suspended"},
            {"station_id": 3, "station_code": "003", "station_name": "C",
             "latitude": -1.1, "longitude": 29.1, "altitude": 1600,
             "province": "Kigali", "district": "Gasabo", "sector": "C",
             "station_category": "Principal Station", "status": "Closed"},
        ]
        sites = get_sites(page=None, page_size=25, search=None, sort_by="site_name",
                          sort_order="asc", user={"department": "Admin"},
                          district="Bugesera|Burera", status="Operational|Suspended")
        self.assertEqual({site["site_name"] for site in sites}, {"A", "B"})


if __name__ == "__main__":
    unittest.main()
