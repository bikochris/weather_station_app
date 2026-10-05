import unittest
from unittest.mock import patch

from backend.main import import_stations


EXPORT_CSV = (
    "Station ID,Station,Latitude,Longitude,Altitude,Province,District,Sector,"
    "Category,Station status,Comment,Action,Registered at,Recorded by\n"
    "ST-1,Test Station,-1.9,30.1,1500,East,Bugesera,Ruhuha,"
    "Automatic Weather stations,Operational,Working well,,2026-09-25,tester\n"
)


class FakeRequest:
    async def body(self):
        return EXPORT_CSV.encode("utf-8")


class FakeCursor:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def fetchall(self):
        return [(7, "ST-1", "Automatic Weather stations")]

    def fetchone(self):
        return (0,)

    def close(self):
        pass

    def executemany(self, sql, params):
        self.statements.append((sql, params))


class FakeConnection:
    def __init__(self):
        self.query = FakeCursor()
        self.committed = False

    def cursor(self):
        return self.query

    def commit(self):
        self.committed = True

    def close(self):
        pass

    def is_connected(self):
        return True


class StationCsvImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_exported_headers_preview_existing_station(self):
        connection = FakeConnection()
        with patch("backend.main.get_connection", return_value=connection), patch(
            "backend.main.ensure_application_tables"
        ), patch(
            "backend.main.validate_station_categories"
        ):
            result = await import_stations(FakeRequest(), mode="preview", user={"user_id": 1, "username": "tester"})
        self.assertEqual(result, {"total": 1, "new": 0, "existing": 1})
        self.assertFalse(connection.committed)

    async def test_upsert_updates_existing_station_without_replacing_identity(self):
        connection = FakeConnection()
        with patch("backend.main.get_connection", return_value=connection), patch(
            "backend.main.ensure_application_tables"
        ), patch(
            "backend.main.validate_station_categories"
        ), patch("backend.main.audit_snapshot", return_value={"station_id": 7}), patch(
            "backend.main.record_entity_edit"
        ):
            result = await import_stations(FakeRequest(), mode="upsert", user={"user_id": 1, "username": "tester"})
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["imported"], 0)
        self.assertTrue(connection.committed)
        updates = [(sql, params) for sql, params in connection.query.statements if "UPDATE stations SET" in sql]
        self.assertEqual(len(updates), 1)
        self.assertIn("Automatic Weather station", updates[0][1][0])
        self.assertEqual(updates[0][1][0][-1], 7)


if __name__ == "__main__":
    unittest.main()
