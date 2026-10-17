import io
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from backend.data_upload_engine import parse_source, analyze_source, report_details_zip, report_pdf, report_docx
from backend.data_upload_scope import recompute_totals
from backend.observation_qc import qc_fields

HEADER = "station_id,element_id,element_name,date_time,value,qc_status\n"


class ObservationQCTests(unittest.TestCase):
    def test_qc_denominator_is_available_and_zero_available_is_undefined(self):
        fields = qc_fields(100, 20, dict(qc_none=2, qc_failed=3, qc_passed=15))
        self.assertEqual([fields[f"qc_{s}_percent"] for s in ("none", "failed", "passed")], [10, 15, 75])
        self.assertEqual(qc_fields(100, 20)["qc_none_percent"], 100)
        for expected in [0, 100]:
            self.assertTrue(all(qc_fields(expected, 0)[f"qc_{s}_percent"] is None for s in ("none", "failed", "passed")))

    def config(self, cadence="hourly", **kwargs):
        return dict(version=1, stations=[dict(station_code="A", station_name="Alpha")],
            parameters=[dict(element_id="5", name="Rainfall", cadence=cadence, hours=[0], **kwargs)])

    def test_qc_normalization_duplicates_blank_values_and_disk_parity(self):
        source = HEADER + (
            "A,5,Rainfall,2026-07-01 00:00,0,Passed\n"
            "A,5,Rainfall,2026-07-01 00:00,,failed\n"
            "A,5,Rainfall,2026-07-02 00:00,5,FAILED\n"
            "A,5,Rainfall,2026-07-03 00:00,2,none\n"
            "A,5,Rainfall,2026-07-04 00:00,,Passed\n"
            "A,5,Rainfall,2026-07-05 00:00,1,failed\n"
            "A,5,Rainfall,2026-07-05 00:00,3, passed \n")
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "input.csv"; path.write_text(source, encoding="utf-8")
            memory, disk = parse_source(source.encode(), "input.csv"), parse_source(path, "input.csv")
            try:
                for parsed in [memory, disk]:
                    key = ("A", "5", date(2026, 7, 1), 0)
                    self.assertEqual(parsed["minute_observations"].get(key), 0)
                    self.assertEqual(parsed["qc_minute_observations"].get(key), "passed")
                    result = analyze_source(parsed, self.config())
                    row = result["results"][0]
                    self.assertEqual(row["hourly_expected"], 31)
                    self.assertEqual(row["hourly_available"], 4)
                    self.assertEqual([row[f"qc_{s}"] for s in ("none", "failed", "passed")], [1, 1, 2])
                    self.assertEqual(row["qc_failed_percent"], 25)
                    self.assertEqual(row["qc_passed_percent"], 50)
                    element = result["element_results"][0]
                    self.assertEqual(element["qc_failed"], 1)
                    self.assertEqual(element["qc_none"] + element["qc_failed"] + element["qc_passed"], element["available"])
                self.assertEqual(analyze_source(memory, self.config()), analyze_source(disk, self.config()))
            finally:
                disk["close"]()

    def test_daily_qc_uses_winning_daily_value_and_exact_schedule(self):
        source = HEADER + "A,5,Rain,2026-07-01 08:00,0,Passed\nA,5,Rain,2026-07-01 10:00,3,Failed\n"
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "input.csv"; path.write_text(source)
            for content in [source.encode(), path]:
                parsed = parse_source(content, "input.csv")
                try:
                    legacy = analyze_source(parsed, self.config("daily"))["results"][0]
                    self.assertEqual(legacy["qc_failed"], 1)
                    exact = analyze_source(parsed, self.config("daily", schedule_mode="times", times=["08:00"]))["results"][0]
                    self.assertEqual(exact["qc_passed"], 1)
                    self.assertEqual(exact["qc_failed"], 0)
                finally:
                    if parsed.get("close"): parsed["close"]()

    def test_tabs_optional_labels_and_unknown_status_errors(self):
        examples = [HEADER.replace(",", "\t") + "A\t5\tRain\t2026-07-01 00:00\t0\tPassed\n",
                    HEADER + "A,5,Rain,2026-07-01 00:00,0\n",
                    HEADER.replace(",qc_status", "") + "A,5,Rain,2026-07-01 00:00,0\n"]
        with tempfile.TemporaryDirectory() as root:
            for index, source in enumerate(examples):
                path = Path(root) / "input.csv"; path.write_text(source)
                for content in [source.encode(), path]:
                    parsed = parse_source(content, "input.csv")
                    try:
                        result = analyze_source(parsed, self.config())["results"][0]
                        self.assertEqual(result["qc_passed" if index == 0 else "qc_none"], 1)
                    finally:
                        if parsed.get("close"): parsed["close"]()
            source = HEADER + "A,5,Rain,2026-07-01 00:00,0,typo\n"
            path.write_text(source)
            for content in [source.encode(), path]:
                with self.assertRaisesRegex(ValueError, "Row 2.*qc_status"):
                    parse_source(content, "input.csv")
            self.assertFalse(any(p.is_dir() for p in Path(root).iterdir()))

    def test_filtered_totals_qc_and_downloads(self):
        parsed = parse_source((HEADER + "A,5,Rain,2026-07-01 00:00,0,Failed\n").encode(), "input.csv")
        result = analyze_source(parsed, self.config())
        row = recompute_totals(result["results"], result["element_results"])[0]
        self.assertEqual(row["qc_failed"], 1)
        self.assertEqual(row["qc_failed_percent"], 100)
        self.assertEqual(row["overall_percent"], 3.23)
        with zipfile.ZipFile(io.BytesIO(report_details_zip(result))) as archive:
            self.assertIn("qc_passed_percent", archive.read("station_summary.csv").decode("utf-8-sig"))
            self.assertIn("qc_failed", archive.read("element_scores.csv").decode("utf-8-sig"))
        self.assertTrue(report_pdf(result).startswith(b"%PDF"))
        with zipfile.ZipFile(io.BytesIO(report_docx(result))) as archive:
            self.assertIn("QC status percentages", archive.read("word/document.xml").decode())


if __name__ == "__main__":
    unittest.main()
