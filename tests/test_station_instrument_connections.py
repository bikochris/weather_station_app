import csv
import io
import json
import unittest

from fastapi import HTTPException

from backend.main import (
    StationInstrument,
    parse_station_instrument_csv,
    station_instrument_connection_values,
    station_instrument_csv_headers,
)


class StationInstrumentConnectionTests(unittest.TestCase):
    def make_item(self, **changes):
        values = {
            "station_id": 1,
            "instrument_id": 2,
            "installation_date": "2026-10-01",
            "status": "Operational",
        }
        values.update(changes)
        return StationInstrument.model_validate(values)

    def test_sensing_wires_preserve_terminal_numbers(self):
        item = self.make_item(
            data_logger_ports=" AI1, AI2 ", algorithm=" Voltage * 2 ",
            wiring_colors=[None, " Red ", None, "Black"],
        )
        ports, algorithm, wiring = station_instrument_connection_values(item, "Sensing")
        self.assertEqual((ports, algorithm), ("AI1, AI2", "Voltage * 2"))
        colors = json.loads(wiring)
        self.assertEqual(len(colors), 10)
        self.assertEqual(colors[1], "Red")
        self.assertEqual(colors[3], "Black")
        self.assertIsNone(colors[0])

    def test_non_sensing_rejects_connection_data(self):
        with self.assertRaises(HTTPException) as caught:
            station_instrument_connection_values(
                self.make_item(wiring_colors=["Blue"]), "Power System"
            )
        self.assertEqual(caught.exception.status_code, 400)

    def test_non_sensing_empty_fields_are_allowed(self):
        self.assertEqual(
            station_instrument_connection_values(self.make_item(), "Power System"),
            (None, None, None),
        )

    def test_export_headers_parse_back_for_import(self):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(station_instrument_csv_headers())
        writer.writerow(["ST-1", "Rain gauge sensor", "", "", "", "2026-10-01",
                         "", "", "", "", "Operational", "", "AI1", "Formula",
                         "Red", *([""] * 9)])
        rows = parse_station_instrument_csv(output.getvalue())
        self.assertEqual(rows[0][1]["station_id"], "ST-1")
        self.assertEqual(rows[0][1]["wire_1"], "Red")
        self.assertEqual(rows[0][1]["data_logger_ports"], "AI1")

    def test_csv_requires_key_columns(self):
        with self.assertRaises(HTTPException):
            parse_station_instrument_csv("Instrument,Status\nGauge,Operational\n")


if __name__ == "__main__":
    unittest.main()
