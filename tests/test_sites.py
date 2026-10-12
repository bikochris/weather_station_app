import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from backend.main import export_sites, get_sites, site_distance_km


class SiteDistanceTests(unittest.TestCase):
    def test_distance_zero_known_arc_and_missing_coordinates(self):
        self.assertEqual(site_distance_km(-2, 30, -2, 30), 0)
        self.assertAlmostEqual(site_distance_km(0, 0, 0, 1), 111.195, places=2)
        self.assertAlmostEqual(site_distance_km(0, 179, 0, -179), 222.39, places=2)
        self.assertIsNone(site_distance_km(0, 0, None, 30))
        self.assertIsNone(site_distance_km(0, 0, 100, 30))

    def test_both_coordinates_required_and_ranges_checked(self):
        for latitude, longitude in [(1, None), (None, 30), (91, 0), (0, 181), (float("nan"), 0)]:
            with self.assertRaises(HTTPException) as error:
                get_sites(latitude=latitude, longitude=longitude)
            self.assertEqual(error.exception.status_code, 422)

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_nearest_sort_before_pagination_and_unknown_last(self, connect, _ensure):
        stations = []
        for index, latitude in enumerate([5, None, 2, 0, 1]):
            stations.append({"station_id": index + 1, "station_code": str(index),
                "station_name": f"Site {index}", "latitude": latitude, "longitude": 30,
                "altitude": 1500, "province": "Eastern", "district": "Bugesera", "sector": "Test",
                "station_category": "Principal Station", "status": "Operational"})
        connect.return_value.cursor.return_value.fetchall.return_value = stations
        user = {"department": "Observation Officer", "user_id": 7}
        arguments = dict(page=None, page_size=25, search=None, sort_by="distance_km",
                         sort_order="asc", user=user, latitude=0, longitude=30)
        sites = get_sites(**arguments)
        self.assertEqual([item["latitude"] for item in sites], [0, 1, 2, 5, None])
        query, parameters = connect.return_value.cursor.return_value.execute.call_args.args
        self.assertIn("user_station_assignments", query)
        self.assertEqual(parameters, (7,))
        arguments.update(page=1, page_size=2)
        self.assertEqual([item["latitude"] for item in get_sites(**arguments)["items"]], [0, 1])
        arguments.update(page=None, sort_order="desc")
        self.assertEqual([item["latitude"] for item in get_sites(**arguments)], [5, 2, 1, 0, None])


class SiteExportTests(unittest.TestCase):
    @patch("backend.main.csv_download")
    @patch("backend.main.get_sites")
    def test_csv_exports_all_filtered_sites_and_station_membership(self, get_sites, csv_download):
        get_sites.return_value = [{
            "site_code": "SITE-0001", "site_name": "Ruhuha", "latitude": -2.2,
            "longitude": 30.1, "altitude": 1400, "province": "Eastern",
            "district": "Bugesera", "sector": "Ruhuha", "station_count": 2,
            "status_summary": "Operational: 1, Suspended: 1",
            "stations": [
                {"station_code": "001", "station_name": "Ruhuha",
                 "station_category": "Principal Station", "status": "Operational"},
                {"station_code": "002", "station_name": "Ruhuha_AWS",
                 "station_category": "Automatic Weather station", "status": "Suspended"},
            ],
        }]
        user = {"username": "admin", "department": "Admin"}
        export_sites(format="csv", search="Ruhuha", sort_by="station_count",
                     sort_order="desc", district="Bugesera", status="Suspended", user=user)
        self.assertEqual(get_sites.call_args.kwargs["page"], None)
        self.assertEqual(get_sites.call_args.kwargs["district"], "Bugesera")
        self.assertEqual(get_sites.call_args.kwargs["search"], "Ruhuha")
        self.assertEqual(get_sites.call_args.kwargs["status"], "Suspended")
        self.assertIs(get_sites.call_args.kwargs["user"], user)
        filename, headers, rows = csv_download.call_args.args
        self.assertEqual(filename, "sites.csv")
        self.assertEqual(headers[-1], "Stations at site")
        self.assertEqual(rows[0][8], 2)
        self.assertEqual(rows[0][9], "Operational: 1, Suspended: 1")
        self.assertIn("002 - Ruhuha_AWS", rows[0][-1])

    @patch("backend.main.pdf_download")
    @patch("backend.main.get_sites", return_value=[])
    def test_pdf_exports_empty_filtered_result(self, get_sites, pdf_download):
        export_sites(format="pdf", search="missing", sort_by="site_name",
                     sort_order="asc", district=None, status=None,
                     user={"username": "admin", "department": "Admin"})
        filename, title, headers, rows, username = pdf_download.call_args.args
        self.assertEqual((filename, title, username), ("sites.pdf", "Site Registry", "admin"))
        self.assertEqual(len(headers), 10)
        self.assertEqual(rows, [])

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_status_filter_keeps_complete_site_and_counts(self, get_connection, _ensure):
        connection = MagicMock()
        get_connection.return_value = connection
        connection.cursor.return_value.fetchall.return_value = [
            {"station_id": 1, "station_code": "001", "station_name": "Ruhuha",
             "latitude": -2.2, "longitude": 30.1, "altitude": 1400,
             "province": "Eastern", "district": "Bugesera", "sector": "Ruhuha",
             "station_category": "Principal Station", "status": "Operational"},
            {"station_id": 2, "station_code": "002", "station_name": "Ruhuha_AWS",
             "latitude": -2.2, "longitude": 30.1, "altitude": 1400,
             "province": "Eastern", "district": "Bugesera", "sector": "Ruhuha",
             "station_category": "Automatic Weather station", "status": "Suspended"},
            {"station_id": 3, "station_code": "003", "station_name": "Other",
             "latitude": -2.0, "longitude": 30.2, "altitude": 1500,
             "province": "Eastern", "district": "Bugesera", "sector": "Other",
             "station_category": "Principal Station", "status": "Operational"},
        ]
        sites = get_sites(page=None, page_size=25, search=None, sort_by="site_name",
                          sort_order="asc", user={"department": "Admin"},
                          district=None, status="Suspended")
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0]["station_count"], 2)
        self.assertEqual(sites[0]["status_summary"], "Operational: 1, Suspended: 1")


if __name__ == "__main__":
    unittest.main()
