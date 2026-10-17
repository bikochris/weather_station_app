import io
import json
import unittest
import zipfile
import tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend.main import app, require_password_change_complete
from backend.data_upload_engine import (default_configuration, UploadConfiguration, parse_source,
    analyze_source, present_value, hour_ranges, report_pdf, report_details_zip, report_docx)


def source(lines):
    return parse_source(("station_id,element_id,element_name,date_time,value\n"+lines).encode(), "data.csv")


class UploadEngineTests(unittest.TestCase):
    def test_default_rule_parity(self):
        config = default_configuration()
        result = analyze_source(source("10101100,2,Tmax,01/07/2026 06:00,\n"), config)
        rows = {row["station_code"]:row for row in result["results"]}
        self.assertEqual(len(rows), 11)
        self.assertEqual(rows["10101100"]["daily_expected"], 310)
        self.assertEqual(rows["10101100"]["hourly_expected"], 31*412)
        self.assertEqual(rows["40302100"]["hourly_expected"], 31*344)
        self.assertEqual(rows["40303100"]["hourly_expected"], 31*460)
        self.assertEqual(rows["40303100"]["daily_expected"], 31*9)
        self.assertEqual(rows["30304100"]["daily_expected"], 310)
        self.assertEqual(rows["50410100"]["daily_expected"], 31*7)
        self.assertTrue(any(row["element_id"] == "184" and row["excluded_hours"] == "00:00-23:00" for row in result["hourly_details"] if row["station_code"] == "40302100"))

    def test_numeric_zero_duplicates_and_cloud_exemptions(self):
        parsed = source("10101100,2,Tmax,01/07/2026 06:10,0\n10101100,2,Tmax,01/07/2026 06:20,\n10101100,190,Cloud,01/07/2026 06:00,0\n")
        result = analyze_source(parsed, default_configuration())
        row = next(r for r in result["results"] if r["station_code"] == "10101100")
        self.assertEqual(parsed["duplicate_rows"], 1)
        self.assertEqual(row["daily_available"], 1)
        self.assertEqual(row["hourly_expected"], 31*412-2)
        self.assertEqual(row["hourly_available"], 1)
        self.assertEqual(present_value(0), 0)
        for value in [None,"", "missing", "#N/A", float("inf"), True]: self.assertIsNone(present_value(value))

    def test_last_present_parent_controls_exemption(self):
        parsed = source("10101100,190,Cloud,01/07/2026 06:00,0\n10101100,190,Cloud,01/07/2026 06:00,2\n")
        row = next(r for r in analyze_source(parsed, default_configuration())["results"] if r["station_code"] == "10101100")
        self.assertEqual(row["hourly_expected"], 31*412)

    def test_header_dates_leap_month_and_malformed_rows(self):
        parsed = source("10101100,2,Tmax,29/02/2024 06:00,5\n10101100,2,Tmax,30/02/2024 06:00,5\n")
        self.assertEqual(parsed["invalid_rows"], 1)
        row = analyze_source(parsed, default_configuration())["results"][0]
        self.assertEqual(row["daily_expected"], 290)
        with self.assertRaises(ValueError): parse_source(b"bad,headers\na,b", "data.csv")
        with self.assertRaises(ValueError): source("10101100,2,Tmax,30/02/2024 06:00,5\n")
        self.assertEqual(hour_ranges([0,1,2,6,12,13]), "00:00-02:00, 06:00, 12:00-13:00")

    def test_xlsx_excel_dates(self):
        from openpyxl import Workbook
        book = Workbook(); sheet = book.active
        sheet.append(["station_id","element_id","element_name","date_time","value"])
        sheet.append([10101100,2,"Tmax",datetime(2026,7,1,6),0])
        stream = io.BytesIO(); book.save(stream); book.close()
        parsed = parse_source(stream.getvalue(), "data.xlsx")
        self.assertEqual(parsed["periods"], ["2026-07"])
        self.assertEqual(parsed["row_count"], 1)

    def test_admin_rule_validation(self):
        config = default_configuration()
        config["stations"][0]["hourly_overrides"] = {"112":{"hours":[25]}}
        with self.assertRaises(ValidationError): UploadConfiguration.model_validate(config)
        config = default_configuration(); config["parameters"][0]["element_id"] = "3"
        with self.assertRaises(ValidationError): UploadConfiguration.model_validate(config)

    def test_reports_and_excluded_markings(self):
        config = default_configuration(); config["stations"] = [config["stations"][0]]
        config["parameters"] = [p for p in config["parameters"] if p["element_id"] in {"2","3","101"}]
        config["stations"][0]["daily_excluded"] = ["3"]
        result = analyze_source(source("10101100,2,Tmax,01/07/2026 06:00,20\n"), config)
        self.assertTrue(report_pdf(result).startswith(b"%PDF"))
        with zipfile.ZipFile(io.BytesIO(report_details_zip(result))) as archive:
            self.assertEqual(len(archive.namelist()), 4)
            self.assertIn("element_scores.csv", archive.namelist())
            self.assertIn("Tmin", archive.read("daily_missing.csv").decode("utf-8-sig"))
        with zipfile.ZipFile(io.BytesIO(report_docx(result))) as archive:
            xml = archive.read("word/document.xml").decode()
            self.assertIn("BE232D", xml)
            self.assertIn("Daily missing data", xml)


class UploadApiTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.storage_patch = patch("backend.observation_storage.STORAGE_ROOT", Path(self.storage.name))
        self.storage_patch.start()
        app.dependency_overrides[require_password_change_complete] = lambda: {"user_id":7,"username":"tester","department":"Admin"}
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.pop(require_password_change_complete, None)
        self.storage_patch.stop()
        self.storage.cleanup()

    @patch("backend.main.get_connection")
    def test_rejected_upload_logs_the_actual_validation_reason(self, connect):
        with self.assertLogs("uvicorn.error", level="WARNING") as logs:
            response = self.client.post("/performance-upload/analyze?filename=data.csv", content=b"wrong,headers\n1,2\n")
        self.assertEqual(response.status_code, 422)
        self.assertIn("Expected columns:", response.json()["detail"])
        self.assertIn("Observation upload rejected: HTTP 422", logs.output[0])
        self.assertIn("Expected columns:", logs.output[0])
        self.assertEqual(list(Path(self.storage.name).iterdir()), [])
        connect.assert_not_called()

    @patch("backend.main.get_connection")
    def test_admin_only_configuration(self, connect):
        app.dependency_overrides[require_password_change_complete] = lambda: {"user_id":7,"username":"tester","department":"Observation Officer"}
        response = self.client.put("/performance-upload/config", json=default_configuration())
        self.assertEqual(response.status_code, 403); connect.assert_not_called()

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_upload_stores_automatic_results_pdf_and_snapshot(self, connect, _ensure):
        cursor = connect.return_value.cursor.return_value
        cursor.fetchall.side_effect = [[{"station_id":1,"station_code":"10101100","station_name":"Gitega","district":"Gasabo"}],
            [{"instrument_name":"Thermometer", "parameters_taken":"Catalog maximum", "element_ids":'["2"]'}],
            [{"station_instrument_id":1,"station_code":"10101100","element_ids":'["2"]',"status":"Operational"}],
            [{"station_code":"10101100","station_category":"Principal Station","element_ids":'["2"]'}], []]
        config = default_configuration(); config["stations"] = [config["stations"][0]]
        cursor.fetchone.return_value = {"version":1,"configuration":json.dumps(config)}
        cursor.lastrowid = 9
        response = self.client.post("/performance-upload/analyze?filename=data.csv", content=b"station_id,element_id,element_name,date_time,value\n10101100,2,Tmax,01/07/2026 06:00,0\n")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["results_count"], 1)
        inserts = [call for call in cursor.execute.call_args_list if "INSERT INTO data_upload_runs" in call.args[0]]
        self.assertEqual(len(inserts), 1)
        self.assertTrue(inserts[0].args[1][5].startswith(b"%PDF"))
        self.assertIn("inventory_snapshot", json.loads(inserts[0].args[1][3]))
        frozen = json.loads(inserts[0].args[1][4])
        self.assertEqual(frozen["element_results"][0]["element_name"], "Catalog maximum")
        query, values = cursor.executemany.call_args.args
        self.assertEqual(query.count("%s"), len(values[0]))
        connect.return_value.commit.assert_called_once()

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_large_source_uses_disk_references_instead_of_mysql_blob(self, connect, _ensure):
        cursor = connect.return_value.cursor.return_value
        allowed = [{"station_id":1,"station_code":"10101100","station_name":"Gitega","district":"Gasabo"}]
        cursor.fetchall.side_effect = [allowed,
            [{"instrument_name":"Thermometer", "parameters_taken":"Catalog maximum", "element_ids":'["2"]'}],
            [{"station_instrument_id":1,"station_code":"10101100","element_ids":'["2"]',"status":"Operational"}],
            [{"station_code":"10101100","station_category":"Principal Station","element_ids":'["2"]'}], []]
        config = default_configuration(); config["stations"] = [config["stations"][0]]
        cursor.fetchone.return_value = {"version":1,"configuration":json.dumps(config)}
        cursor.lastrowid = 9
        small = b"station_id,element_id,element_name,date_time,value\n10101100,2,Tmax,01/07/2026 06:00,0\n"
        parsed = parse_source(small, "data.csv")
        with patch("backend.data_upload_api.parse_source", return_value=parsed), patch("backend.main.read_document_upload") as documents_reader:
            response = self.client.post("/performance-upload/analyze?filename=large.csv", content=small + b"\n" * (21 * 1024**2))
        self.assertEqual(response.status_code, 201, response.text)
        documents_reader.assert_not_called()
        insert = next(call for call in cursor.execute.call_args_list if "INSERT INTO data_upload_runs" in call.args[0])
        values = insert.args[1]
        self.assertEqual(values[1], b"")
        snapshot = json.loads(values[3])
        stored = json.loads(values[4])
        self.assertGreater(snapshot["source_file"]["size_bytes"], 20 * 1024**2)
        self.assertTrue((Path(self.storage.name) / snapshot["source_file"]["storage_key"]).exists())
        self.assertIn("details_file", stored)
        cursor.fetchall.side_effect = None; cursor.fetchall.return_value = allowed
        cursor.fetchone.return_value = dict(result_data=values[4],pdf_data=values[5])
        report = self.client.get("/performance-upload/runs/9/report?format=docx")
        self.assertEqual(report.status_code, 200, report.text[:100] if report.status_code != 200 else "")
        self.assertTrue(report.content.startswith(b"PK"))

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_upload_assesses_all_eligible_registry_stations_not_only_source_stations(self, connect, _ensure):
        cursor = connect.return_value.cursor.return_value
        registry = [dict(station_id=index,station_code=code,station_name=code,district="North",station_category="Principal Station",status=status)
            for index,(code,status) in enumerate([("A","Operational"),("B","Suspended"),("C","Under maintenance"),("D","Closed")],1)]
        cursor.fetchall.side_effect = [registry,
            [dict(instrument_name="Temperature",parameters_taken="Temperature",element_ids='["101"]')],
            [dict(station_instrument_id=row["station_id"],station_code=row["station_code"],status="Operational",element_ids='["101"]') for row in registry],
            [dict(station_code=row["station_code"],station_category=row["station_category"],element_ids='["101"]') for row in registry], []]
        config = dict(version=1,parameters=[dict(element_id="101",name="Temperature",cadence="hourly",hours=[6])],
            stations=[dict(station_code="A",station_name="A")])
        cursor.fetchone.return_value = dict(version=1,configuration=json.dumps(config))
        cursor.lastrowid = 1
        response = self.client.post("/performance-upload/analyze?filename=scope.csv",content=b"station_id,element_id,element_name,date_time,value\nA,101,Temperature,2026-07-01 06:00,1\n")
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json()["results_count"],3)
        insert = next(call for call in cursor.execute.call_args_list if "INSERT INTO data_upload_runs" in call.args[0])
        rows = {row["station_code"]:row for row in json.loads(insert.args[1][4])["results"]}
        self.assertEqual(set(rows),{"A","B","C"})
        self.assertEqual(rows["B"]["hourly_expected"],31)
        self.assertEqual(rows["B"]["hourly_available"],0)
        self.assertEqual(rows["C"]["station_status"],"Under maintenance")

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_filtered_element_results_recompute_station_and_sidebar_percentages(self, connect, _ensure):
        cursor = connect.return_value.cursor.return_value
        allowed = [dict(station_id=1,station_code="A",station_name="Alpha",district="North",station_category="Principal Station",status="Suspended")]
        record = dict(station_id=1,station_code="A",station_name="Alpha",report_month="2026-07-01",run_id=1,
            daily_expected=31,daily_available=31,daily_percent=100,hourly_expected=31,hourly_available=0,hourly_percent=0,overall_percent=50)
        elements = [dict(station_code="A",station_name="Alpha",report_month="2026-07",element_id=code,element_name=name,
            cadence=cadence,expected=31,available=available,missing=31-available,score=100 if available else 0)
            for code,name,cadence,available in [("2","Daily temperature","daily",31),("101","Temperature","hourly",0)]]
        cursor.fetchall.side_effect = [allowed,[record],[dict(run_id=1,result_data=json.dumps(dict(element_results=elements)))],[]]
        response = self.client.get("/performance-upload/results?station_category=Principal%20Station&station_status=Suspended&element_id=101")
        self.assertEqual(response.status_code,200,response.text)
        result = response.json()
        self.assertEqual(result["items"][0]["daily_expected"],0)
        self.assertEqual(result["items"][0]["overall_percent"],0)
        self.assertEqual(result["summary"]["average"],0)
        self.assertEqual([row["element_id"] for row in result["element_items"]],["101"])
        query = next(call for call in cursor.execute.call_args_list if "SELECT r.*" in call.args[0])
        self.assertIn("s.station_category",query.args[0])
        self.assertIn("Principal Station",query.args[1])
        self.assertIn("Suspended",query.args[1])

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_old_upload_element_filter_recovers_saved_source(self, connect, _ensure):
        source = b"station_id,element_id,element_name,date_time,value\nA,2,Tmax,2026-09-01 14:00,25\n"
        snapshot = dict(version=1, parameters=[dict(element_id="2", name="Tmax", cadence="daily")],
            stations=[dict(station_code="A", station_name="Alpha")])
        original = analyze_source(parse_source(source, "September.csv"), snapshot)
        original.pop("element_results")
        record = dict(original["results"][0], station_id=1, run_id=2)
        record["report_month"] += "-01"
        cursor = connect.return_value.cursor.return_value
        cursor.fetchall.side_effect = [
            [dict(station_id=1,station_code="A",station_name="Alpha",district="North",station_category="Principal Station",status="Operational")],
            [record], [dict(run_id=2,result_data=json.dumps(original),config_snapshot=json.dumps(snapshot))], []]
        cursor.fetchone.return_value = dict(filename="September.csv", source_data=source)
        response = self.client.get("/performance-upload/results?element_id=2&month_from=2026-08&month_to=2026-09")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["summary"]["average"], 3.33)
        self.assertEqual(response.json()["items"][0]["daily_available"], 1)

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_report_download_applies_category_element_and_month_filters(self, connect, _ensure):
        from backend.data_upload_scope import recompute_totals
        cursor = connect.return_value.cursor.return_value
        registry = [dict(station_id=index,station_code=code,station_name=code,district="North",station_category=category,status="Operational")
            for index,(code,category) in enumerate([("A","Principal Station"),("B","Rainfall Station")],1)]
        elements = [dict(station_code=code,station_name=code,report_month="2026-07",element_id=element,element_name=name,cadence=cadence,
            expected=31,available=31,missing=0,score=100) for code in ["A","B"] for element,name,cadence in [("2","Selected daily","daily"),("101","Hidden hourly","hourly")]]
        result = dict(results=recompute_totals([dict(station_code=code,station_name=code,report_month="2026-07") for code in ["A","B"]],elements),element_results=elements,
            daily_details=[],hourly_details=[],periods=["2026-07"],warnings=[])
        cursor.fetchall.return_value = registry
        cursor.fetchone.return_value = dict(result_data=json.dumps(result),pdf_data=b"original")
        response = self.client.get("/performance-upload/runs/1/report?format=csv&station_category=Principal%20Station&element_id=2&month_from=2026-07&month_to=2026-07")
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn("Selected daily",response.text)
        self.assertNotIn("Hidden hourly",response.text)
        self.assertNotIn("\r\nB,",response.text)
        missing = self.client.get("/performance-upload/runs/1/report?format=csv&element_id=unknown")
        self.assertEqual(missing.status_code,404)

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_upload_rejects_unassigned_source(self, connect, _ensure):
        app.dependency_overrides[require_password_change_complete] = lambda: {"user_id":7,"username":"tester","department":"Observation Officer"}
        connect.return_value.cursor.return_value.fetchall.return_value = [{"station_id":1,"station_code":"10101100","station_name":"Gitega","district":"Gasabo"}]
        response = self.client.post("/performance-upload/analyze?filename=data.csv", content=b"station_id,element_id,element_name,date_time,value\n40302100,2,Tmax,01/07/2026 06:00,0\n")
        self.assertEqual(response.status_code, 403)
        connect.return_value.commit.assert_not_called()

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_report_filters_other_stations(self, connect, _ensure):
        app.dependency_overrides[require_password_change_complete] = lambda: {"user_id":7,"username":"tester","department":"Observation Officer"}
        cursor = connect.return_value.cursor.return_value
        cursor.fetchall.return_value = [{"station_id":1,"station_code":"10101100","station_name":"Gitega","district":"Gasabo"}]
        result = {"results":[{"station_code":"10101100","station_name":"Allowed"},{"station_code":"40302100","station_name":"SECRET"}],
            "element_results":[{"station_code":"10101100","element_name":"Allowed element"},{"station_code":"40302100","element_name":"SECRET element"}],
            "daily_details":[],"hourly_details":[],"periods":["2026-07"],"warnings":[]}
        cursor.fetchone.return_value = {"result_data":json.dumps(result),"pdf_data":b""}
        response = self.client.get("/performance-upload/runs/1/report?format=csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Allowed", response.text)
        self.assertIn("Allowed element", response.text)
        self.assertNotIn("SECRET", response.text)

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_element_summary_uses_only_selected_latest_station_months(self, connect, _ensure):
        cursor = connect.return_value.cursor.return_value
        element = dict(station_code="10101100", station_name="Gitega", report_month="2026-07",
                       element_id="2",element_name="Catalog maximum",expected=50,available=25,missing=25,score=50)
        other = dict(element, station_code="40302100",element_name="SECRET",available=50,missing=0,score=100)
        record = dict(station_id=1,station_code="10101100",report_month="2026-07-01",run_id=7,
                      station_name="GITEGA",
                      daily_expected=50,daily_available=25,daily_percent=50,hourly_expected=0,hourly_available=0,hourly_percent=0,overall_percent=50)
        cursor.fetchall.side_effect = [
            [{"station_id":1,"station_code":"10101100","station_name":"Gitega","district":"Gasabo"}],
            [record], [{"run_id":7,"result_data":json.dumps({"element_results":[element,other]})}], []]
        response = self.client.get("/performance-upload/results")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["element_summary"][0]["score"], 50)
        self.assertEqual(len(response.json()["element_items"]), 1)
        self.assertEqual(response.json()["items"][0]["station_name"], "Gitega")
        self.assertEqual(response.json()["element_items"][0]["station_name"], "Gitega")
        self.assertNotIn("SECRET", response.text)

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_qc_counts_loaded_from_frozen_results_and_filtered_by_element(self, connect, _ensure):
        cursor = connect.return_value.cursor.return_value
        record = dict(station_id=1,station_code="10101100",report_month="2026-07-01",run_id=7,
                      daily_expected=100,daily_available=72,daily_percent=72,hourly_expected=0,hourly_available=0,hourly_percent=0,overall_percent=72)
        element = dict(station_code="10101100",station_name="Gitega",report_month="2026-07",cadence="daily",
            element_id="5",element_name="Rainfall",expected=100,available=72,missing=28,score=72,qc_none=2,qc_failed=5,qc_passed=65)
        saved = dict(record, report_month="2026-07",qc_none=2,qc_failed=5,qc_passed=65)
        for query in ["", "?element_id=5"]:
            cursor.fetchall.side_effect = [[dict(station_id=1,station_code="10101100",station_name="Gitega",district="Gasabo")],
                [dict(record)], [dict(run_id=7,result_data=json.dumps(dict(results=[saved],element_results=[element])))], []]
            response = self.client.get("/performance-upload/results" + query)
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            for row in [data["items"][0], data["element_summary"][0], data["summary"]]:
                self.assertEqual(row["qc_failed_percent"], 6.94)
                self.assertEqual(row["qc_none_percent"], 2.78)
                self.assertEqual(row["qc_passed_percent"], 90.28)
            self.assertEqual(data["summary"]["average"], 72)

    @patch("backend.data_upload_api.report_pdf", return_value=b"current PDF")
    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_cached_pdf_is_regenerated_when_qc_denominator_is_outdated(self, connect, _ensure, render):
        cursor = connect.return_value.cursor.return_value
        cursor.fetchall.return_value = [dict(station_id=1,station_code="A",station_name="Alpha",district="North")]
        record = dict(station_code="A",station_name="Alpha",report_month="2026-07",daily_expected=100,daily_available=20,
            hourly_expected=0,hourly_available=0,qc_none=2,qc_failed=3,qc_passed=15,qc_failed_percent=3)
        for basis in ["expected", "available"]:
            result = dict(results=[record],element_results=[],periods=["2026-07"],warnings=[],qc_percentage_basis=basis)
            cursor.fetchone.return_value = dict(result_data=json.dumps(result),pdf_data=b"cached PDF")
            render.reset_mock()
            response = self.client.get("/performance-upload/runs/1/report?format=pdf")
            self.assertEqual(response.status_code,200,response.text)
            if basis == "expected":
                self.assertEqual(response.content,b"current PDF")
                self.assertEqual(render.call_args.args[0]["results"][0]["qc_failed_percent"],15)
            else:
                render.assert_not_called()
                self.assertEqual(response.content,b"cached PDF")


if __name__ == "__main__": unittest.main()
