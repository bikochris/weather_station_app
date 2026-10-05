import unittest

from pydantic import ValidationError

from backend.main import Station


def station(status):
    return Station(
        station_code="TEST-1",
        station_name="Test station",
        latitude=-1.9,
        longitude=30.1,
        altitude=1400,
        province="Kigali",
        district="Gasabo",
        sector="Test",
        station_category="Automatic Weather station",
        status=status,
    )


class StationStatusTests(unittest.TestCase):
    def test_status_is_the_only_station_state_field(self):
        self.assertEqual(station("Suspended").status, "Suspended")
        self.assertNotIn("suspended", station("Suspended").model_dump())

    def test_all_four_statuses_are_allowed(self):
        for status in ("Operational", "Under maintenance", "Suspended", "Closed"):
            self.assertEqual(station(status).status, status)

    def test_only_four_statuses_allowed(self):
        with self.assertRaises(ValidationError):
            station("Non-operational")


if __name__ == "__main__":
    unittest.main()
