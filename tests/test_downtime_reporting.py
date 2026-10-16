import io
import unittest
import zipfile
from backend.data_upload_engine import analyze_source, parse_source, report_docx, report_details_zip, report_pdf, downtime_summary
from backend.instrument_availability import catalog_configuration, RESERVED_CLOUD_IDS


class DowntimeReportingTests(unittest.TestCase):
    def config(self):
        return dict(version=3, parameters=[dict(element_id="101", name="Temperature", cadence="hourly",
            schedule_mode="interval", interval_minutes=10, hours=list(range(24)), enabled=True)],
            stations=[dict(station_code="A", station_name="Alpha")])

    def inventory(self, current="Operational", periods=None):
        return dict(availability_mode="dated_status", mapped_elements=["101"],
            element_names={"101":"Temperature"}, eligible_stations={"101":["A"]}, catalog_errors=[],
            assignments=[dict(station_code="A", element_ids=["101"], status=current, periods=periods or [
                dict(effective_date="1900-01-01", status="Operational"),
                dict(effective_date="2026-07-11", status="Under Maintenance"),
                dict(effective_date="2026-07-16", status="Inactive"),
                dict(effective_date="2026-07-21", status="Operational")])])

    def assess(self, inventory):
        config = catalog_configuration(self.config(), inventory)
        parsed = parse_source(b"station_id,element_id,element_name,date_time,value\nA,101,Temperature,01/07/2026 00:00,1\nA,101,Temperature,11/07/2026 00:00,1\nA,101,Temperature,21/07/2026 00:10,1\n", "data.csv")
        return analyze_source(parsed, config, inventory)

    def test_current_operational_preserves_past_downtime_and_entered_status(self):
        result = self.assess(self.inventory())
        self.assertEqual(result["results"][0]["hourly_expected"], 21 * 144)
        self.assertEqual(result["results"][0]["hourly_available"], 2)
        details = {row["date"]:row for row in result["hourly_details"]}
        self.assertEqual(details["2026-07-11"]["entered"], "Under Maintenance")
        self.assertEqual(details["2026-07-16"]["entered"], "Inactive")
        self.assertEqual(details["2026-07-16"]["missing_hours"], "")
        summary = downtime_summary(result)
        self.assertIn(["A", "Temperature", "2026-07-11 to 2026-07-15", "Under Maintenance"], summary)
        self.assertTrue(report_pdf(result).startswith(b"%PDF"))
        with zipfile.ZipFile(io.BytesIO(report_docx(result))) as archive:
            xml = archive.read("word/document.xml").decode()
            self.assertIn("Entered", xml)
            self.assertIn("Under Maintenance", xml)
            self.assertIn("Inactive", xml)
        with zipfile.ZipFile(io.BytesIO(report_details_zip(result))) as archive:
            self.assertIn("entered", archive.read("hourly_missing.csv").decode())
            self.assertIn("Under Maintenance", archive.read("hourly_missing.csv").decode())

    def test_current_inactive_does_not_remove_earlier_operational_days(self):
        inventory = self.inventory("Inactive", [dict(effective_date="1900-01-01",status="Operational"),
            dict(effective_date="2026-07-11",status="Inactive")])
        config = catalog_configuration(self.config(), inventory)
        param = config["parameters"][0]
        self.assertEqual(param["station_codes"], [])
        self.assertEqual(param["assessment_station_codes"], ["A"])
        self.assertEqual(self.assess(inventory)["results"][0]["hourly_expected"], 10 * 144)

    def test_automatic_checks_and_manual_exclusions_are_independent(self):
        down = catalog_configuration(self.config(), self.inventory("Inactive"))
        self.assertEqual(down["parameters"][0]["station_codes"], [])
        self.assertEqual(down["parameters"][0]["excluded_station_codes"], [])
        up = catalog_configuration(down, self.inventory())
        self.assertEqual(up["parameters"][0]["station_codes"], ["A"])
        up["parameters"][0]["excluded_station_codes"] = ["A"]
        self.assertEqual(catalog_configuration(up, self.inventory())["parameters"][0]["station_codes"], [])

    def test_placeholders_have_no_observation_hours_or_expectations(self):
        config = catalog_configuration(self.config(), self.inventory())
        placeholders = [p for p in config["parameters"] if p.get("reserved")]
        self.assertEqual({p["element_id"] for p in placeholders}, set(RESERVED_CLOUD_IDS))
        for p in placeholders:
            self.assertEqual(p["hours"], [])
            self.assertEqual(p["times"], [])
            self.assertEqual(p["schedule_mode"], "none")
            self.assertFalse(p["enabled"])
        self.assertFalse(config["parameters"][0]["is_cloud_parameter"])
        self.assertTrue(all(p["is_cloud_parameter"] for p in placeholders))

    def test_daily_entered_column_identifies_downtime(self):
        inventory = self.inventory()
        config = self.config()
        config["parameters"][0].update(cadence="daily",schedule_mode="times",times=["08:00"])
        config = catalog_configuration(config, inventory)
        parsed = parse_source(b"station_id,element_id,element_name,date_time,value\nA,101,Temp,01/07/2026 08:00,1\n", "data.csv")
        result = analyze_source(parsed, config, inventory)
        self.assertEqual(result["results"][0]["daily_expected"], 21)
        row = next(r for r in result["daily_details"] if r["date"] == "2026-07-11")
        self.assertEqual(row["entered"], "Temperature: Under Maintenance")


if __name__ == "__main__": unittest.main()
