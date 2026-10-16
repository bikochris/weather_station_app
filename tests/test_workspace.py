import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from mysql.connector import IntegrityError
from pydantic import ValidationError

from backend.main import app, require_password_change_complete, add_document_category, audit_snapshot, save_agenda_item, save_idea, save_workspace_document, workspace_records
from backend.workspace import (AgendaItem, DocumentMetadata, DocumentCategoryInput, IdeaItem, agenda_timing, document_file_details,
                               read_document_upload, require_workspace_owner)


class WorkspaceValidationTests(unittest.TestCase):
    def test_document_category_validation(self):
        self.assertEqual(DocumentMetadata(title="File", document_date="2026-10-13").category, "General")
        self.assertEqual(DocumentMetadata(title="File", document_date="2026-10-13", category="  Policy  ").category, "Policy")
        for category in [" ", "Policy|Reports", "x" * 101]:
            with self.assertRaises(ValidationError):
                DocumentMetadata(title="File", document_date="2026-10-13", category=category)
            with self.assertRaises(ValidationError):
                DocumentCategoryInput(name=category)

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_document_category_defined_without_upload(self, connect, _ensure):
        result = add_document_category(DocumentCategoryInput(name="  Policies  "), {"user_id": 1})
        self.assertEqual(result["name"], "Policies")
        self.assertEqual(connect.return_value.cursor.return_value.execute.call_args.args[1], ("Policies",))
        connect.return_value.commit.assert_called_once()

    @patch("backend.main.get_connection")
    def test_document_category_admin_only(self, connect):
        app.dependency_overrides[require_password_change_complete] = lambda: {"user_id": 7, "department": "Observation Officer"}
        try:
            response = TestClient(app).post("/documents/categories", json={"name": "Policies"})
            self.assertEqual(response.status_code, 403)
            connect.assert_not_called()
        finally:
            app.dependency_overrides.pop(require_password_change_complete, None)

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_document_category_duplicate(self, connect, _ensure):
        connect.return_value.cursor.return_value.execute.side_effect = IntegrityError("Duplicate")
        with self.assertRaises(HTTPException) as error:
            add_document_category(DocumentCategoryInput(name="General"), {"user_id": 1})
        self.assertEqual(error.exception.status_code, 409)
        connect.return_value.rollback.assert_called_once()

    def test_idea_validation(self):
        idea = IdeaItem(title="  Improve stations  ", idea_date="2026-10-12")
        self.assertEqual(idea.title, "Improve stations")
        self.assertEqual(idea.status, "Proposed")
        self.assertEqual(idea.category, "General")
        self.assertEqual(IdeaItem(title="Suggestion", idea_date="2026-10-12", category="  Equipment  ").category, "Equipment")
        for category in ["  ", "Equipment|Data", "x" * 101]:
            with self.assertRaises(ValidationError):
                IdeaItem(title="Suggestion", idea_date="2026-10-12", category=category)
        for fields in [{"title": "  "}, {"status": "Unknown"}]:
            with self.assertRaises(ValidationError):
                IdeaItem(**{"title": "Suggestion", "idea_date": "2026-10-12", **fields})

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_other_users_idea_cannot_be_updated(self, connect, _ensure):
        cursor = connect.return_value.cursor.return_value
        cursor.fetchone.return_value = {"created_by_user_id": 5}
        with self.assertRaises(HTTPException) as error:
            save_idea(IdeaItem(title="Suggestion", idea_date="2026-10-12"),
                      {"user_id": 7, "department": "Observation Officer", "username": "tester"}, 1)
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(cursor.execute.call_count, 1)
        connect.return_value.rollback.assert_called_once()

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_idea_multiselect_filters_and_summary(self, connect, _ensure):
        cursor = connect.return_value.cursor.return_value
        cursor.fetchall.return_value = []
        _, summary = workspace_records("ideas", {"user_id": 7, "department": "Admin"},
                                       status="Proposed|Accepted", priority="High|Normal", category="Equipment|Data")
        query, params = cursor.execute.call_args.args
        self.assertIn("status IN (%s, %s)", query)
        self.assertIn("category IN (%s, %s)", query)
        self.assertEqual(params, ("Proposed", "Accepted", "High", "Normal", "Equipment", "Data"))
        self.assertEqual(summary, {"total": 0, "proposed": 0, "under_review": 0, "implemented": 0})

    def test_supported_uploads_and_windows_filename(self):
        for filename, group in [("report.PDF", "PDF"), ("data.xlsx", "Spreadsheet"),
                                ("archive.zip", "Archive"), ("station.png", "Image")]:
            clean, actual_group, _mime = document_file_details("C:\\folder\\" + filename)
            self.assertEqual((clean, actual_group), (filename, group))

    def test_unsupported_executable_and_header_control_characters(self):
        for filename in ["run.exe", "report\n.pdf"]:
            with self.assertRaises(HTTPException):
                document_file_details(filename)

    def test_owner_and_admin_can_change_entry(self):
        require_workspace_owner({"department": "Admin", "user_id": 99}, {"created_by_user_id": 1})
        require_workspace_owner({"department": "Observation Officer", "user_id": 1}, {"created_by_user_id": 1})
        with self.assertRaises(HTTPException) as error:
            require_workspace_owner({"department": "Observation Officer", "user_id": 2}, {"created_by_user_id": 1})
        self.assertEqual(error.exception.status_code, 403)

    def test_agenda_due_boundaries_and_completed_not_overdue(self):
        today = date(2026, 10, 12)
        item = {"status": "Open", "due_date": date(2026, 10, 11)}
        self.assertEqual(agenda_timing(item, today), "Overdue")
        item["due_date"] = today
        self.assertEqual(agenda_timing(item, today), "Due soon")
        item["due_date"] = date(2026, 10, 19)
        self.assertEqual(agenda_timing(item, today), "Due soon")
        item["due_date"] = date(2026, 10, 20)
        self.assertEqual(agenda_timing(item, today), "On track")
        item.update(status="Completed", due_date=date(2026, 10, 1))
        self.assertEqual(agenda_timing(item, today), "Completed")

    def test_progress_status_consistency(self):
        self.assertEqual(AgendaItem(title="Review", due_date="2026-10-12", status="Completed").progress, 100)
        for fields in [{"status": "Open", "progress": 20}, {"status": "In progress", "progress": 100}]:
            with self.assertRaises(ValidationError):
                AgendaItem(title="Review", due_date="2026-10-12", **fields)
        with self.assertRaises(ValidationError):
            AgendaItem(title="  ", due_date="2026-10-12")

    @patch("backend.main.get_connection")
    def test_document_snapshot_does_not_fetch_file_bytes(self, connect):
        cursor = connect.return_value.cursor.return_value
        cursor.fetchone.return_value = {"document_id": 1, "title": "Reference", "version": 1}
        snapshot = audit_snapshot(connect.return_value, "documents", 1)
        self.assertNotIn("file_data", snapshot)
        self.assertNotIn("file_data", cursor.execute.call_args.args[0])

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_other_users_agenda_cannot_be_updated(self, connect, _ensure):
        cursor = connect.return_value.cursor.return_value
        cursor.fetchone.return_value = {"created_by_user_id": 5}
        with self.assertRaises(HTTPException) as error:
            save_agenda_item(AgendaItem(title="Review", due_date="2026-10-12"),
                             {"user_id": 7, "department": "Observation Officer", "username": "tester"}, 1)
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(cursor.execute.call_count, 1)
        connect.return_value.rollback.assert_called_once()

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_filtered_library_queries_metadata_only(self, connect, _ensure):
        cursor = connect.return_value.cursor.return_value
        cursor.fetchall.return_value = []
        items, summary = workspace_records("documents", {"user_id": 7, "department": "Admin"},
                                           file_type="PDF|Image", mine=True, category="Policy|Reports")
        query, params = cursor.execute.call_args.args
        self.assertNotIn("file_data", query)
        self.assertIn("file_type IN (%s, %s)", query)
        self.assertIn("category IN (%s, %s)", query)
        self.assertEqual(params, (7, "PDF", "Image", "Policy", "Reports"))
        self.assertEqual((items, summary["total"]), ([], 0))


class WorkspaceUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_category_saved_and_audited(self):
        with patch("backend.main.get_connection") as connect, patch("backend.main.ensure_application_tables"), \
                patch("backend.main.audit_snapshot", side_effect=[{"category": "General"}, {"category": "Policy"}]), \
                patch("backend.main.record_entity_edit") as audit:
            cursor = connect.return_value.cursor.return_value
            cursor.fetchone.side_effect = [{"created_by_user_id": 7}, {"name": "Policy"}]
            await save_workspace_document(self.request([]),
                DocumentMetadata(title="File", document_date="2026-10-13", category="Policy"), None,
                {"user_id": 7, "department": "Admin", "username": "tester"}, 1)
            query, values = cursor.execute.call_args.args
            self.assertIn("category = %s", query)
            self.assertEqual(values[1], "Policy")
            self.assertEqual(audit.call_args.args[3:5], ({"category": "General"}, {"category": "Policy"}))
            connect.return_value.commit.assert_called_once()

    async def test_new_document_category_insert(self):
        with patch("backend.main.get_connection") as connect, patch("backend.main.ensure_application_tables"):
            cursor = connect.return_value.cursor.return_value
            cursor.fetchone.return_value = {"name": "Reports"}
            await save_workspace_document(self.request([b"file"]),
                DocumentMetadata(title="File", document_date="2026-10-13", category="Reports"), "file.txt",
                {"user_id": 7, "department": "Admin", "username": "tester"})
            query, values = cursor.execute.call_args.args
            self.assertIn("(title, category, document_date", query)
            self.assertEqual(values[1], "Reports")
            self.assertEqual(query.count("%s"), len(values))

    async def test_undefined_document_category_rejected(self):
        with patch("backend.main.get_connection") as connect, patch("backend.main.ensure_application_tables"):
            connect.return_value.cursor.return_value.fetchone.return_value = None
            with self.assertRaises(HTTPException) as error:
                await save_workspace_document(self.request([b"file"]),
                    DocumentMetadata(title="File", document_date="2026-10-13", category="Unknown"), "file.txt",
                    {"user_id": 7, "department": "Admin", "username": "tester"})
            self.assertEqual(error.exception.status_code, 422)
            connect.return_value.commit.assert_not_called()

    def request(self, chunks):
        async def stream():
            for chunk in chunks:
                yield chunk
        request = MagicMock()
        request.stream = stream
        return request

    async def test_stream_enforces_limit_before_accepting_file(self):
        with patch("backend.workspace.MAX_DOCUMENT_BYTES", 4):
            self.assertEqual(await read_document_upload(self.request([b"12", b"34"])), b"1234")
            with self.assertRaises(HTTPException) as error:
                await read_document_upload(self.request([b"12", b"345"]))
            self.assertEqual(error.exception.status_code, 413)

    async def test_empty_file_rejected(self):
        with self.assertRaises(HTTPException) as error:
            await read_document_upload(self.request([b""]))
        self.assertEqual(error.exception.status_code, 400)

    async def test_other_users_document_cannot_be_replaced(self):
        with patch("backend.main.get_connection") as connect, patch("backend.main.ensure_application_tables"):
            cursor = connect.return_value.cursor.return_value
            cursor.fetchone.return_value = {"created_by_user_id": 5}
            with self.assertRaises(HTTPException) as error:
                await save_workspace_document(self.request([b"abc"]),
                    DocumentMetadata(title="File", document_date="2026-10-12"), "file.txt",
                    {"user_id": 7, "department": "Observation Officer", "username": "tester"}, 1)
            self.assertEqual(error.exception.status_code, 403)
            self.assertEqual(cursor.execute.call_count, 1)
            connect.return_value.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
