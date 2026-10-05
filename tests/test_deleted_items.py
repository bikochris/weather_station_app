import ast
import json
import io
import inspect
import unittest
import zlib
from unittest.mock import MagicMock
from zipfile import ZipFile

from fastapi import HTTPException
from mysql.connector import IntegrityError

from backend.deleted_items import (archive_deleted_item, download_deleted_item,
                                   get_deleted_item_history, restore_deleted_item)
from backend.main import app, deleted_items_query, require_it
import backend.main as main_module


class DeletedItemsTests(unittest.TestCase):
    def test_archive_captures_parent_and_related_rows(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.side_effect = [
            [{"maintenance_id": 7, "station_id": 4, "issue": "Battery"}],
            [{"maintenance_id": 7, "instrument_id": 2, "issue": "Low voltage"}],
        ]
        cursor.fetchone.return_value = {"district": "Gasabo"}
        archive_deleted_item(connection, "maintenance", 7, {"username": "admin"})
        statement, values = cursor.execute.call_args.args
        self.assertIn("INSERT INTO deleted_items", statement)
        self.assertEqual(values[:6], ("maintenance", "7", "Maintenance #7", "Gasabo", "admin", 2))
        blocks = json.loads(zlib.decompress(values[6]))
        self.assertEqual([block["table"] for block in blocks],
                         ["maintenance_records", "maintenance_record_instruments"])
        connection.commit.assert_not_called()

    def test_report_backup_keeps_all_linked_districts(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.side_effect = [
            [{"report_id": 8, "original_filename": "visit.pdf"}],
            [{"report_id": 8, "station_id": 3}, {"report_id": 8, "station_id": 4}],
            [{"district": "Gasabo"}, {"district": "Rulindo"}],
        ]
        archive_deleted_item(connection, "maintenance_reports", 8, {"username": "admin"})
        self.assertEqual(cursor.execute.call_args.args[1][3], "Gasabo,Rulindo")

    def test_restore_reinserts_rows_before_marking_restored(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        blocks = [{"table": "maintenance_records", "rows": [{"maintenance_id": 7, "station_id": 4}]},
                  {"table": "maintenance_record_instruments", "rows":
                   [{"maintenance_id": 7, "instrument_id": 2}]}]
        cursor.fetchone.return_value = {"entity_type": "maintenance", "entity_id": "7",
            "payload": zlib.compress(json.dumps(blocks).encode()), "restored_at": None}
        result = restore_deleted_item(connection, 3, {"username": "admin"})
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertIn("INSERT INTO `maintenance_records`", statements[1])
        self.assertIn("INSERT INTO `maintenance_record_instruments`", statements[2])
        self.assertIn("UPDATE deleted_items", statements[3])
        self.assertEqual(result["entity_id"], "7")
        connection.commit.assert_called_once()

    def test_restore_conflict_does_not_mark_backup_restored(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        blocks = [{"table": "stations", "rows": [{"station_id": 1, "station_code": "ST-1"}]}]
        cursor.fetchone.return_value = {"entity_type": "stations", "entity_id": "1",
            "payload": zlib.compress(json.dumps(blocks).encode()), "restored_at": None}
        cursor.execute.side_effect = [None, IntegrityError("duplicate")]
        with self.assertRaises(Exception) as error:
            restore_deleted_item(connection, 3, {"username": "admin"})
        self.assertEqual(error.exception.status_code, 409)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_backup_download_contains_csv_and_attachment(self):
        connection = MagicMock()
        blocks = [{"table": "maintenance_reports", "rows": [{
            "report_id": 2, "original_filename": "visit.pdf",
            "file_data": {"__binary__": "cGRm"},
        }]}]
        connection.cursor.return_value.fetchone.return_value = {
            "entity_type": "maintenance_reports", "entity_id": "2",
            "deleted_at": "2026-10-04 10:00:00",
            "payload": zlib.compress(json.dumps(blocks).encode()),
        }
        content = download_deleted_item(connection, 7)
        with ZipFile(io.BytesIO(content)) as archive:
            self.assertIn("tables/maintenance_reports.csv", archive.namelist())
            self.assertIn("visit.pdf", archive.read("tables/maintenance_reports.csv").decode("utf-8-sig"))
            self.assertEqual(archive.read("files/maintenance_reports_1_file_data.bin"), b"pdf")

    def test_deleted_history_is_available_and_included_in_download(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {
            "entity_type": "stations", "entity_id": "12",
            "deleted_at": "2026-10-04 10:00:00",
            "payload": zlib.compress(json.dumps([{"table": "stations", "rows": [
                {"station_id": 12, "station_name": "Kigali"}]}]).encode()),
        }
        event = {"before_data": json.dumps({"station_name": "Old"}),
                 "after_data": json.dumps({"station_name": "Kigali"}),
                 "changed_by_username": "admin", "edit_reason": "Correction",
                 "changed_at": "2026-10-03 10:00:00"}
        cursor.fetchall.return_value = [event]
        history = get_deleted_item_history(connection, 3)
        self.assertEqual(history[0]["after_data"]["station_name"], "Kigali")
        self.assertIn("changed_at <= %s", cursor.execute.call_args.args[0])
        content = download_deleted_item(connection, 3)
        with ZipFile(io.BytesIO(content)) as archive:
            data = archive.read("tables/edit_history.csv").decode("utf-8-sig")
            self.assertIn("Correction", data)
            self.assertIn("Kigali", data)

    def test_deleted_reporting_history_uses_reporting_audit_table(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {
            "entity_type": "reporting_status", "entity_id": "18",
            "deleted_at": "2026-10-04 10:00:00", "payload": b"",
        }
        cursor.fetchall.return_value = []
        self.assertEqual(get_deleted_item_history(connection, 3), [])
        self.assertIn("monthly_reporting_changes", cursor.execute.call_args.args[0])

    def test_district_filter_matches_multi_district_backups(self):
        where, params = deleted_items_query(district="Gasabo", status="deleted")
        self.assertIn("FIND_IN_SET(%s, district)", where)
        self.assertIn("restored_at IS NULL", where)
        self.assertEqual(params, ["Gasabo"])

    def test_restored_backup_cannot_be_restored_again(self):
        connection = MagicMock()
        connection.cursor.return_value.fetchone.return_value = {
            "entity_type": "stations", "entity_id": "1", "payload": b"",
            "restored_at": "2026-10-04 10:00:00",
        }
        with self.assertRaises(HTTPException) as error:
            restore_deleted_item(connection, 1, {"username": "admin"})
        self.assertEqual(error.exception.status_code, 409)
        connection.commit.assert_not_called()

    def test_non_admin_cannot_list_export_or_restore(self):
        with self.assertRaises(HTTPException) as error:
            require_it({"department": "Observation Officer"})
        self.assertEqual(error.exception.status_code, 403)
        for path, method in (("/deleted-items", "GET"),
                             ("/deleted-items/export", "GET"),
                             ("/deleted-items/{deleted_item_id}/restore", "POST"),
                             ("/deleted-items/{deleted_item_id}/history", "GET"),
                             ("/deleted-items/{deleted_item_id}/download", "GET")):
            route = next(route for route in app.routes if route.path == path
                         and method in route.methods)
            self.assertIn(require_it, [dependency.call for dependency in route.dependant.dependencies])

    def test_every_delete_route_archives_before_removal(self):
        tree = ast.parse(inspect.getsource(main_module))
        delete_routes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and any(isinstance(decorator, ast.Call) and
                                 isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "delete"
                                 for decorator in node.decorator_list)]
        self.assertTrue(delete_routes)
        for route in delete_routes:
            calls = [node for node in ast.walk(route) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == "archive_deleted_item"]
            self.assertTrue(calls, f"{route.name} does not back up the deleted record")


if __name__ == "__main__":
    unittest.main()
