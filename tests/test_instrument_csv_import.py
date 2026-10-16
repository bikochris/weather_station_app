import unittest
from fastapi import HTTPException

from backend.main import parse_instrument_import


class InstrumentCsvImportTests(unittest.TestCase):
    def test_latest_owner_table_is_valid_and_merges_hygrograph(self):
        from backend.catalog_update_20261015 import catalog_csv
        records, merged = parse_instrument_import(catalog_csv())
        by_name = {item.instrument_name: item for item in records}
        self.assertEqual(len(records), 41)
        self.assertEqual(merged, 1)
        self.assertEqual(by_name["Hygrograph"].element_ids, ["10", "184"])
        self.assertEqual(by_name["Hygrograph"].parameters_taken, "Humidity_Hygro|Relative humidity")
        self.assertEqual(by_name["Soil thermometer at 100 Cm"].element_ids, ["180"])
        self.assertEqual(by_name["Thermohygrography"].parameters_taken, "Air temperature|Relative humidity")
        self.assertEqual(by_name["Human Observation"].element_ids[5], "190")

    def test_extra_empty_pipe_and_category_alias_are_normalized(self):
        records, _ = parse_instrument_import("Instrument,Parameters,Category,Station categories,Elements\nProbe,Dry||Wet|Humidity,Sensing,Rainfall,101|102|105\n")
        self.assertEqual(records[0].parameters_taken, "Dry|Wet|Humidity")
        self.assertEqual(records[0].station_categories, ["Rainfall Station"])

    def test_mismatched_names_rejected_and_shared_ids_allowed(self):
        with self.assertRaises(HTTPException):
            parse_instrument_import("Instrument,Parameters,Category,Station categories,Elements\nProbe,Temperature,Sensing,Principal Station,101|102\n")
        records, _ = parse_instrument_import("Instrument,Parameters,Category,Station categories,Elements\nSoil,Soil temperature,Sensing,Principal Station,190\nHuman,Low Cloud Type,Observation,Principal Station,190\n")
        self.assertEqual(len(records), 2)

    def test_blank_duplicate_status_preserves_explicit_inactive(self):
        records, merged = parse_instrument_import("Instrument,Parameters,Category,Station categories,Elements,Status\nProbe,Temperature,Sensing,Principal Station,101,Inactive\nProbe,Temperature,Sensing,Climatic Station,101,\n")
        self.assertEqual(merged, 1)
        self.assertFalse(records[0].is_active)

    def test_template_merges_repeated_names_and_normalizes_category(self):
        csv_text = (
            "instrument_name,parameters_it_takes,category,station_categories,description\n"
            "Rain gauge sensor,Precipitation,Sensing,Automatic Weather station,\n"
            "Rain gauge sensor,Precipitation,Sensing,Automatic Weather stations|Automatic Raingauge,\n"
            "Digital thermometer,Max/Min temperature,Sensing,Principal stations|Climatic stations,\n"
            "Digital thermometer,Dry temperature,Sensing,Principal stations,\n"
        )
        items, merged = parse_instrument_import(csv_text)
        self.assertEqual((len(items), merged), (2, 2))
        self.assertEqual(items[0].station_categories, ["Automatic Weather station", "Automatic Rain Gauge"])
        self.assertEqual(items[1].parameters_taken, "Max/Min temperature; Dry temperature")

    def test_export_headers_and_inactive_status_round_trip(self):
        csv_text = (
            "Instrument,Parameters,Category,Station categories,Description,Status\n"
            'Thermometer,Temperature,Sensing,"Principal stations, Climatic stations",,Inactive\n'
        )
        items, merged = parse_instrument_import(csv_text)
        self.assertEqual(merged, 0)
        self.assertEqual(items[0].station_categories, ["Principal Station", "Climatic Station"])
        self.assertFalse(items[0].is_active)


if __name__ == "__main__":
    unittest.main()
