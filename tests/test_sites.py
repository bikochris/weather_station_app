import unittest
from unittest.mock import MagicMock, patch

from backend.main import export_sites, get_sites


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
