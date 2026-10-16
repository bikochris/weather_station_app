import json
import unittest
from datetime import date
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from backend import main
from backend.data_upload_engine import analyze_source, parse_source
from backend.instrument_availability import load_inventory


class InstrumentAvailabilityTests(unittest.TestCase):
    def config(self):
        return {"version": 1, "stations": [{"station_code": "A", "station_name": "Alpha"}],
                "parameters": [{"element_id": "2", "name": "Tmax", "cadence": "daily"},
                               {"element_id": "101", "name": "Temperature", "cadence": "hourly", "hours": [6, 12]}]}

    def parsed(self):
        return parse_source(b"station_id,element_id,element_name,date_time,value\nA,2,Tmax,01/07/2026 06:00,25\nA,101,Temperature,01/07/2026 06:00,20", "data.csv")

    def inventory(self, periods=None):
        return {"mapped_elements": ["2", "101"], "assignments": [
            {"station_code": "A", "element_ids": ["2", "101"], "periods": periods or [
                {"effective_date": "1900-01-01", "status": "Operational"},
                {"effective_date": "2026-07-11", "status": "Under Maintenance"},
                {"effective_date": "2026-07-21", "status": "Operational"}]}]}

    def test_dated_outage_only_excludes_affected_days(self):
        result = analyze_source(self.parsed(), self.config(), self.inventory())
        row = result["results"][0]
        self.assertEqual(row["daily_expected"], 21)
        self.assertEqual(row["hourly_expected"], 42)
        self.assertEqual(row["daily_available"], 1)
        self.assertEqual(row["hourly_available"], 1)
        outage = next(item for item in result["hourly_details"] if item["date"] == "2026-07-11")
        self.assertEqual(outage["missing_hours"], "")
        self.assertIn("06:00", outage["excluded_hours"])

    def test_another_operational_instrument_supplies_same_element(self):
        inventory = self.inventory()
        inventory["assignments"].append({"station_code": "A", "element_ids": ["2", "101"],
                                       "periods": [{"effective_date": "1900-01-01", "status": "Operational"}]})
        row = analyze_source(self.parsed(), self.config(), inventory)["results"][0]
        self.assertEqual(row["daily_expected"], 31)
        self.assertEqual(row["hourly_expected"], 62)

    def test_unassigned_mapped_elements_not_expected_and_unmapped_retained(self):
        row = analyze_source(self.parsed(), self.config(), {"mapped_elements": ["2"], "assignments": []})["results"][0]
        self.assertEqual(row["daily_expected"], 0)
        self.assertEqual(row["daily_available"], 0)
        self.assertEqual(row["hourly_expected"], 62)

    def test_inventory_overrides_legacy_daily_exclusions_but_keeps_hours(self):
        config = self.config()
        config["stations"][0].update(daily_excluded=["2"], hourly_overrides={"101": {"hours": [6]}})
        inventory = self.inventory([{"effective_date": "1900-01-01", "status": "Operational"}])
        row = analyze_source(self.parsed(), config, inventory)["results"][0]
        self.assertEqual(row["daily_expected"], 31)
        self.assertEqual(row["hourly_expected"], 31)

    def test_inventory_loader_is_scoped_and_loads_status_history(self):
        cursor = MagicMock()
        cursor.fetchall.side_effect = [
            [{"instrument_name": "Probe", "parameters_taken": "Catalog temperature", "element_ids": '["101"]'}],
            [{"station_instrument_id": 1, "station_code": "A", "element_ids": '["101"]', "status": "Operational"},
             {"station_instrument_id": 2, "station_code": "SECRET", "element_ids": '["101"]', "status": "Operational"}],
            [{"station_code": "A", "station_category": "Principal Station", "element_ids": '["101"]'},
             {"station_code": "SECRET", "station_category": "Principal Station", "element_ids": '["101"]'}],
            [{"station_instrument_id":1,"effective_date":"2026-07-11","status":"Inactive"}]]
        result = load_inventory(cursor, {"A"})
        self.assertEqual(len(result["assignments"]), 1)
        self.assertEqual(result["availability_mode"], "dated_status")
        self.assertEqual(result["assignments"][0]["status"], "Operational")
        self.assertEqual(result["assignments"][0]["periods"][0]["status"], "Inactive")
        self.assertEqual(result["element_names"], {"101": "Catalog temperature"})
        self.assertEqual(result["eligible_stations"], {"101": ["A"]})

    def test_proposals_follow_categories_not_installed_assignments(self):
        cursor = MagicMock()
        cursor.fetchall.side_effect = [
            [{"instrument_name":"Probe", "parameters_taken":"Temperature", "element_ids":'["101"]'}],
            [{"station_instrument_id":1,"station_code":"A","status":"Operational","element_ids":'["101"]'}],
            [{"station_code":"B","station_category":"Climatic Station","element_ids":'["101"]'},
             {"station_code":"B","station_category":"Climatic Station","element_ids":'["101"]'},
             {"station_code":"SECRET","station_category":"Climatic Station","element_ids":'["101"]'}], []]
        inventory = load_inventory(cursor, {"A", "B"})
        self.assertEqual(inventory["eligible_stations"]["101"], ["B"])
        self.assertEqual(inventory["element_station_categories"]["101"], ["Climatic Station"])
        self.assertEqual(inventory["assignments"][0]["station_code"], "A")
        config = self.config(); config["catalog_managed"] = True
        rows = analyze_source(self.parsed(), config, inventory)["results"]
        self.assertEqual(rows[0]["hourly_expected"], 0)

    def test_no_matching_category_never_means_all_stations(self):
        inventory = {"availability_mode":"current_status", "mapped_elements":["2","101"],
            "eligible_stations":{"2":[], "101":[]}, "assignments":[
                {"station_code":"A","element_ids":["2","101"],"status":"Operational"}]}
        config = self.config(); config["catalog_managed"] = True
        row = analyze_source(self.parsed(), config, inventory)["results"][0]
        self.assertEqual(row["daily_expected"], 0)
        self.assertEqual(row["hourly_expected"], 0)

    def test_station_choices_survive_reload_and_new_stations_are_proposed(self):
        from backend.instrument_availability import catalog_configuration
        config = self.config()
        config["parameters"][0]["excluded_station_codes"] = ["A"]
        inventory = {"element_names":{"2":"Maximum"},"eligible_stations":{"2":["A","B"]},"catalog_errors":[]}
        derived = catalog_configuration(config, inventory)
        self.assertEqual(derived["parameters"][0]["station_codes"], ["B"])
        self.assertEqual(derived["parameters"][0]["eligible_station_codes"], ["A","B"])
        expanded = catalog_configuration(derived, dict(inventory, eligible_stations={"2":["A","B","C"]}))
        self.assertEqual(expanded["parameters"][0]["station_codes"], ["B","C"])
        expanded["parameters"][0]["excluded_station_codes"] = []
        self.assertEqual(catalog_configuration(expanded, inventory)["parameters"][0]["station_codes"], ["A","B"])

    def test_unselect_all_excludes_both_daily_and_hourly_elements(self):
        from backend.instrument_availability import catalog_configuration
        config = self.config()
        for parameter in config["parameters"]:
            parameter["excluded_station_codes"] = ["A"]
        inventory = {"availability_mode":"current_status", "mapped_elements":["2","101"],
            "element_names":{"2":"Maximum","101":"Temperature"},
            "eligible_stations":{"2":["A"],"101":["A"]}, "catalog_errors":[],
            "assignments":[{"station_code":"A","element_ids":["2","101"],"status":"Operational"}]}
        derived = catalog_configuration(config, inventory)
        self.assertTrue(all(parameter["station_codes"] == [] for parameter in derived["parameters"]))
        row = analyze_source(self.parsed(), derived, inventory)["results"][0]
        self.assertEqual(row["daily_expected"], 0)
        self.assertEqual(row["hourly_expected"], 0)

    def test_catalog_owns_identity_and_stations_preserving_schedules(self):
        from backend.instrument_availability import catalog_configuration
        saved = self.config()
        saved["parameters"][1].update(name="VBA name", hours=[6,12], station_codes=["WRONG"])
        inv = {"element_names": {"101":"User temperature", "NEW":"User element"},
               "eligible_stations": {"101":["A"], "NEW":["B"]}, "catalog_errors": []}
        derived = catalog_configuration(saved, inv)
        self.assertEqual([p["element_id"] for p in derived["parameters"] if not p.get("reserved")], ["101", "NEW"])
        self.assertEqual(derived["parameters"][0]["name"], "User temperature")
        self.assertEqual(derived["parameters"][0]["hours"], [6,12])
        self.assertEqual(derived["parameters"][0]["station_codes"], ["A"])
        self.assertFalse(derived["parameters"][1]["enabled"])
        restored = catalog_configuration(derived, dict(inv, element_names={"2":"New maximum name"}, eligible_stations={"2":["A"]}))
        self.assertEqual(restored["parameters"][0]["cadence"], "daily")
        self.assertEqual(restored["parameters"][0]["name"], "New maximum name")

    def test_element_scores_have_catalog_names_and_correct_percentages(self):
        config = self.config(); config["parameters"][0]["name"] = "Catalog maximum"
        result = analyze_source(self.parsed(), config, self.inventory())
        score = next(row for row in result["element_results"] if row["element_id"] == "2")
        self.assertEqual(score["element_name"], "Catalog maximum")
        self.assertEqual(score["expected"], 21)
        self.assertEqual(score["available"], 1)
        self.assertEqual(score["score"], round(100/21, 2))

    def test_pipe_names_match_ids_and_mismatches_are_reported(self):
        cursor = MagicMock()
        cursor.fetchall.side_effect = [[
            {"instrument_name":"Combined probe", "element_ids":'["101","105"]', "parameters_taken":"Temperature|Humidity"},
            {"instrument_name":"Bad probe", "element_ids":'["2","3"]', "parameters_taken":"One name"}], [], [], []]
        inventory = load_inventory(cursor, {"A"})
        self.assertEqual(inventory["element_names"], {"101":"Temperature", "105":"Humidity"})
        self.assertIn("Bad probe", inventory["catalog_errors"][0])

    def test_current_status_applies_to_entire_month(self):
        inventory = {"availability_mode": "current_status", "mapped_elements": ["2", "101"],
            "assignments": [{"station_code": "A", "element_ids": ["2", "101"], "status": "Under Maintenance"}]}
        row = analyze_source(self.parsed(), self.config(), inventory)["results"][0]
        self.assertEqual(row["daily_expected"], 0)
        self.assertEqual(row["hourly_expected"], 0)
        inventory["assignments"][0]["status"] = "Operational"
        row = analyze_source(self.parsed(), self.config(), inventory)["results"][0]
        self.assertEqual(row["daily_expected"], 31)
        self.assertEqual(row["hourly_expected"], 62)

    def test_owner_confirmed_ids_are_preserved(self):
        from backend.instrument_availability import INITIAL_ELEMENT_IDS
        self.assertEqual(INITIAL_ELEMENT_IDS[64], ["532"])
        self.assertEqual(INITIAL_ELEMENT_IDS[61], ["177"])
        self.assertEqual(INITIAL_ELEMENT_IDS[62], ["179"])
        self.assertEqual(INITIAL_ELEMENT_IDS[59], ["104"])
        self.assertEqual(INITIAL_ELEMENT_IDS[67], ["184"])
        self.assertEqual(INITIAL_ELEMENT_IDS[63], ["180"])

    def test_requested_non_operational_statuses_exclude_entire_period(self):
        for status in ["Under Maintenance", "Unavailable", "Inactive"]:
            with self.subTest(status=status):
                inventory = {"availability_mode": "current_status", "mapped_elements": ["2", "101"],
                    "assignments": [{"station_code": "A", "element_ids": ["2", "101"], "status": status}]}
                result = analyze_source(self.parsed(), self.config(), inventory)
                row = result["results"][0]
                for key in ["daily_expected", "hourly_expected", "daily_available", "hourly_available", "daily_missing", "hourly_missing"]:
                    self.assertEqual(row[key], 0)
            self.assertTrue(any(status in entry.get("entered", "") for entry in result["hourly_details"]))

    def test_element_csv_round_trip_and_duplicate_merge(self):
        records, merged = main.parse_instrument_import("Instrument,Parameters,Category,Station categories,Elements\nProbe,Dry|Wet,Sensing,Principal Station,101|102\nProbe,Wet|Humidity,Sensing,Principal Station,102|105\n")
        self.assertEqual(merged, 1)
        self.assertEqual(records[0].element_ids, ["101", "102", "105"])
        self.assertEqual(records[0].parameters_taken, "Dry|Wet|Humidity")

    def test_default_assignments_are_admin_only(self):
        main.app.dependency_overrides[main.require_password_change_complete] = lambda: {"user_id": 2, "username": "observer", "department": "Observation Officer"}
        try:
            with patch("backend.main.get_connection") as connect:
                response = TestClient(main.app).post("/station-instruments/category-defaults")
            self.assertEqual(response.status_code, 403)
            connect.assert_not_called()
        finally:
            main.app.dependency_overrides.clear()

    def test_frozen_inventory_survives_later_status_changes(self):
        inventory = self.inventory()
        saved = json.loads(json.dumps(inventory))
        original = analyze_source(self.parsed(), self.config(), saved)["results"]
        inventory["assignments"][0]["periods"] = [{"effective_date": "1900-01-01", "status": "Inactive"}]
        self.assertEqual(analyze_source(self.parsed(), self.config(), saved)["results"], original)
        self.assertNotEqual(analyze_source(self.parsed(), self.config(), inventory)["results"], original)


if __name__ == "__main__":
    unittest.main()
