import unittest
from unittest.mock import patch

from fastapi import HTTPException
from mysql.connector import IntegrityError

from backend.main import (
    StationVolunteer, export_station_volunteers, get_record_history,
    list_station_volunteers, require_volunteer_data_access, save_station_volunteer,
)


ADMIN = {"department": "Admin", "user_id": 1, "username": "admin"}
OFFICER = {"department": "Observation Officer", "user_id": 2, "username": "officer"}


class StationVolunteerTests(unittest.TestCase):
    def setUp(self):
        self.item = StationVolunteer(station_id=3, volunteer_name="Aline",
            volunteer_identifier="1199", account_type="Bank", account_name="Aline",
            account_number="000123", mobile_phone="+250700000000")

    def test_officer_cannot_access_volunteer_records_or_history(self):
        with self.assertRaises(HTTPException) as error:
            require_volunteer_data_access(OFFICER)
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(HTTPException) as error:
            get_record_history("station_volunteers", "1", page=1, page_size=25, user=OFFICER)
        self.assertEqual(error.exception.status_code, 403)

    @patch("backend.main.station_volunteer_rows")
    def test_list_summary_uses_filtered_rows(self, rows):
        rows.return_value = [
            {"station_id": 3, "volunteer_name": "Aline"},
            {"station_id": 3, "volunteer_name": "Anne"},
            {"station_id": 4, "volunteer_name": "David"},
        ]
        result = list_station_volunteers(district="Huye", page=1, page_size=25, user=ADMIN)
        self.assertEqual(result["summary"], {"volunteers": 3, "stations": 2})
        self.assertEqual(rows.call_args.args[2], "Huye")

    @patch("backend.main.csv_download")
    @patch("backend.main.station_volunteer_rows")
    def test_export_passes_filters_and_preserves_account_number(self, rows, download):
        rows.return_value = [{"station_code": "01", "station_name": "A", "province": "South",
            "district": "Huye", "sector": "Ngoma", "volunteer_name": "Aline",
            "volunteer_identifier": "1199", "account_type": "Bank",
            "account_name": "Aline", "account_number": "000123", "mobile_phone": "+250700000000"}]
        export_station_volunteers(format="csv", district="Huye", sort_by="volunteer_name",
                                  sort_order="desc", user=ADMIN)
        self.assertEqual(rows.call_args.args[2], "Huye")
        self.assertEqual(rows.call_args.args[7:9], ("volunteer_name", "desc"))
        self.assertEqual(download.call_args.args[2][0][-2], "000123")

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_duplicate_id_at_same_station_is_conflict(self, get_connection, _ensure):
        cursor = get_connection.return_value.cursor.return_value
        cursor.fetchone.return_value = (1,)
        cursor.execute.side_effect = [None, IntegrityError("duplicate")]
        with self.assertRaises(HTTPException) as error:
            save_station_volunteer(self.item, ADMIN)
        self.assertEqual(error.exception.status_code, 409)
        get_connection.return_value.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
