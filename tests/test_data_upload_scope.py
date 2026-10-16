import unittest
import io
import zipfile
from pydantic import ValidationError
from backend.data_upload_scope import registry_configuration, report_profiles, filter_report, recompute_totals, recover_legacy_elements
from backend.data_upload_engine import analyze_source, parse_source, UploadConfiguration, report_pdf, report_docx


class ReportScopeTests(unittest.TestCase):
    def registry(self):
        return {code: dict(station_id=index,station_code=code,station_name=code,district="North",
            station_category="Principal Station" if code != "C" else "Rainfall Station",status=status)
            for index,(code,status) in enumerate([("A","Operational"),("B","Suspended"),("C","Under maintenance"),("D","Closed"),("E","Operational")],1)}

    def config(self):
        return dict(version=1,parameters=[dict(element_id="101",name="Temperature",cadence="hourly",
            schedule_mode="interval",interval_minutes=60,hours=[6])],
            stations=[dict(station_code="A",station_name="Old name"),dict(station_code="E",station_name="E",enabled=False)])

    def test_registry_includes_absent_stations_and_configurable_statuses(self):
        config = registry_configuration(self.config(),self.registry())
        self.assertEqual(len(config["stations"]),5)
        self.assertEqual(config["stations"][0]["station_name"],"A")
        self.assertEqual([s["station_code"] for s in report_profiles(config)],["A","B","C"])
        config["station_statuses"].append("Closed")
        self.assertEqual([s["station_code"] for s in report_profiles(config)],["A","B","C","D"])
        config["station_statuses"] = ["Closed"]
        self.assertEqual([s["station_code"] for s in report_profiles(config)],["D"])

    def test_missing_station_rows_still_have_full_expected_and_zero_available(self):
        config = registry_configuration(self.config(),self.registry())
        config["stations"] = report_profiles(config)
        parsed = parse_source(b"station_id,element_id,element_name,date_time,value\nA,101,Temperature,2026-07-01 06:00,1\n","data.csv")
        results = {row["station_code"]:row for row in analyze_source(parsed,config)["results"]}
        self.assertEqual(set(results),{"A","B","C"})
        self.assertEqual(results["B"]["hourly_expected"],31)
        self.assertEqual(results["B"]["hourly_available"],0)
        self.assertEqual(results["B"]["overall_percent"],0)
        config["stations"] = [row for row in config["stations"] if row["station_code"] == "B"]
        self.assertEqual(len(analyze_source(parsed,config)["results"]),1)

    def test_empty_or_invalid_status_selection_rejected(self):
        for statuses in [[],["Unknown"]]:
            with self.assertRaises(ValidationError): UploadConfiguration.model_validate(dict(self.config(),station_statuses=statuses))

    def result(self):
        elements = [dict(station_code=code,station_name=code,report_month="2026-07",element_id=id,
            element_name=name,cadence=cadence,expected=31,available=available,missing=31-available,
            score=100*available/31) for code in ["A","B","C"]
            for id,name,cadence,available in [("2","Daily temperature","daily",31),("101","Temperature","hourly",0)]]
        rows = recompute_totals([dict(station_code=code,station_name=code,report_month="2026-07") for code in ["A","B","C"]],elements)
        return dict(results=rows,element_results=elements,periods=["2026-07"],warnings=[],hourly_details=[
            dict(station_code=code,element_id="101",element_name="Temperature",date="2026-07-01",missing_hours="06:00",excluded_hours="") for code in ["A","B","C"]],
            daily_details=[dict(station_code="B",date="2026-07-01",missing=["Daily temperature","Unselected"],excluded=[],entered="No")])

    def test_category_status_element_and_month_filters_recompute_report(self):
        original = self.result()
        result = filter_report(original,self.registry(),station_category="Principal Station|Rainfall Station",
            station_status="Suspended",element_id="101",month_from="2026-07",month_to="2026-07")
        self.assertEqual([r["station_code"] for r in result["results"]],["B"])
        row = result["results"][0]
        self.assertEqual(row["daily_expected"],0)
        self.assertEqual(row["hourly_expected"],31)
        self.assertEqual(row["overall_percent"],0)
        self.assertEqual(len(result["element_results"]),1)
        self.assertEqual(len(result["hourly_details"]),1)
        self.assertEqual(result["daily_details"],[])
        self.assertEqual(len(original["results"]),3)
        self.assertEqual(len(original["daily_details"]),1)
        self.assertTrue(report_pdf(result).startswith(b"%PDF"))

    def test_daily_details_are_filtered_without_leaking_unselected_elements(self):
        result = filter_report(self.result(),self.registry(),element_id="2")
        self.assertEqual(result["daily_details"][0]["missing"],["Daily temperature"])
        self.assertEqual(result["hourly_details"],[])
        self.assertTrue(all(row["overall_percent"] == 100 for row in result["results"]))

    def test_element_two_percentage_uses_only_its_available_and_expected_counts(self):
        original = self.result()
        for element in original["element_results"]:
            if element["element_id"] == "2":
                element.update(report_month="2026-09", expected=30, available=12, missing=18)
        for row in original["results"]:
            row["report_month"] = "2026-09"
        result = filter_report(original, self.registry(), element_id="2",
            month_from="2026-09", month_to="2026-09")
        self.assertEqual(len(result["results"]), 3)
        for row in result["results"]:
            self.assertEqual(row["daily_expected"], 30)
            self.assertEqual(row["daily_available"], 12)
            self.assertEqual(row["hourly_expected"], 0)
            self.assertEqual(row["overall_percent"], 40)

    def test_legacy_element_scores_recovered_with_original_schedule(self):
        source = b"station_id,element_id,element_name,date_time,value\nA,2,Temperature,2026-09-01 14:00,20\n"
        snapshot = dict(version=1, parameters=[dict(element_id="2", name="Original temperature", cadence="daily")],
            stations=[dict(station_code="A", station_name="Alpha")])
        original = analyze_source(parse_source(source, "old.csv"), snapshot)
        original.pop("element_results")
        enriched = recover_legacy_elements(original, snapshot, source, "old.csv")
        self.assertNotIn("element_results", original)
        self.assertEqual(enriched["results"], original["results"])
        element = enriched["element_results"][0]
        self.assertEqual(element["expected"], 30)
        self.assertEqual(element["available"], 1)
        self.assertEqual(element["element_name"], "Original temperature")
        self.assertEqual(element["score"], 3.33)
        self.assertEqual(recover_legacy_elements(enriched, {}, None, ""), enriched)
        original["results"][0]["daily_available"] = 20
        with self.assertRaisesRegex(ValueError, "identical historical totals"):
            recover_legacy_elements(original, snapshot, source, "old.csv")
        with self.assertRaisesRegex(ValueError, "no saved source"):
            recover_legacy_elements(original, snapshot, None, "old.csv")

    def test_all_elements_and_unmatched_element_selections(self):
        result = self.result()
        self.assertEqual(len(filter_report(result,self.registry(),element_id="2|101")["element_results"]),6)
        self.assertEqual(len(filter_report(result,self.registry(),element_id="all")["results"]),3)
        self.assertEqual(filter_report(result,self.registry(),element_id="unknown")["results"],[])

    def test_large_word_report_keeps_all_station_scores_and_groups_details(self):
        result = self.result()
        result["hourly_details"] = [dict(station_code="A",element_id="101",element_name="Temperature",date="2026-07-01",missing_hours="",excluded_hours="06:00",entered="Under Maintenance")] * 20001
        with zipfile.ZipFile(io.BytesIO(report_docx(result))) as archive:
            xml = archive.read("word/document.xml").decode()
        self.assertIn("Summary report for all selected stations",xml)
        self.assertIn("Under Maintenance",xml)
        self.assertIn("<w:t>C</w:t>",xml)


if __name__ == "__main__": unittest.main()
