import unittest

from backend.main import parse_instrument_import


class InstrumentCsvImportTests(unittest.TestCase):
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
