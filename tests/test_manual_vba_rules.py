from copy import deepcopy
import unittest

from backend.data_upload_engine import default_configuration, apply_observation_defaults, apply_vba_manual_rules, apply_manual_station_exclusions, EXCLUDED, DAILY, HOURLY, analyze_source, parse_source
from backend.instrument_availability import catalog_configuration


class ManualVBARulesTests(unittest.TestCase):
    def fixture(self):
        config = apply_observation_defaults(default_configuration())
        registry = {station["station_code"]: dict(station_name="Registry " + station["station_code"])
                    for station in config["stations"]}
        config["parameters"].append(dict(element_id="1107",name="AWS rainfall",cadence="hourly",schedule_mode="interval",
                                        interval_minutes=10,hours=list(range(24)),enabled=True))
        config["parameters"].append(dict(element_id="116",name="Reserved cloud",cadence="hourly",schedule_mode="none",hours=[],enabled=False))
        for rule in config["parameters"]:
            if rule["element_id"] in {"106", "185"}: rule["station_codes"] = []
        return config, registry

    def test_restore_manual_hours_preserves_aws_reserved_identity_and_original(self):
        config, registry = self.fixture(); before = deepcopy(config)
        result = apply_vba_manual_rules(config, registry)
        rules = {rule["element_id"]:rule for rule in result["parameters"]}
        self.assertEqual(config, before)
        self.assertEqual(rules["1107"], next(p for p in before["parameters"] if p["element_id"] == "1107"))
        self.assertEqual(rules["116"], next(p for p in before["parameters"] if p["element_id"] == "116"))
        self.assertEqual(rules["5"]["schedule_mode"], "legacy")
        self.assertEqual(rules["196"]["hours"], list(range(6,19)))
        self.assertEqual(rules["176"]["hours"], [6,12,18])
        self.assertEqual(rules["101"]["hours"], list(range(24)))
        self.assertEqual(rules["191"]["zero_parent"], "190")
        self.assertIn("40503100",rules["18"]["excluded_station_codes"])
        self.assertNotIn("40303100",rules["106"]["excluded_station_codes"])
        self.assertIn("10101100",rules["106"]["excluded_station_codes"])
        self.assertEqual(result["stations"][0]["station_name"],registry[result["stations"][0]["station_code"]]["station_name"])

    def test_counts_match_vba_without_catalog_or_downtime(self):
        config, registry = self.fixture()
        original = default_configuration()
        restored = apply_vba_manual_rules(config,registry)
        restored["parameters"] = [p for p in restored["parameters"] if p["element_id"] not in {"1107", "116"}]
        parsed = parse_source(b"station_id,element_id,element_name,date_time,value,qc_status\n10101100,5,Rain,01/07/2026 00:10,0,Passed\n", "data.csv")
        expected = analyze_source(parsed, original)["results"]
        actual = analyze_source(parsed, restored)["results"]
        for old, new in zip(expected,actual):
            for key in ["daily_expected", "daily_available", "hourly_expected", "hourly_available", "qc_passed"]:
                self.assertEqual(new[key],old[key],(new["station_code"],key))

    def test_catalog_retains_vba_pressure_restrictions_and_daily_exclusions(self):
        config, registry = self.fixture()
        restored = apply_vba_manual_rules(config,registry)
        names = {p["element_id"]: p["name"] for p in restored["parameters"] if p["element_id"] != "116"}
        inventory = dict(element_names=names,eligible_stations={code:list(registry) for code in names},catalog_errors=[])
        result = catalog_configuration(restored,inventory)
        rules = {rule["element_id"]: rule for rule in result["parameters"]}
        self.assertEqual(set(rules["106"]["station_codes"]), {"30304100","40303100"})
        self.assertNotIn("40503100", rules["575"]["station_codes"])
        self.assertNotIn("40303100", rules["85"]["station_codes"])
        self.assertIn("40503100", rules["176"]["station_codes"])

    def test_requested_unticks_use_exact_schedules_and_preserve_busogo(self):
        config, registry = self.fixture()
        before = deepcopy(config)
        result = apply_manual_station_exclusions(config, registry)
        self.assertEqual(config, before)
        rules = {p["element_id"]: p for p in result["parameters"]}
        for code in ("1107", "116"):
            self.assertEqual(rules[code], next(p for p in before["parameters"] if p["element_id"] == code))
        self.assertEqual(rules["176"]["times"], ["06:00", "12:00", "18:00"])
        self.assertEqual(rules["5"]["times"], ["08:00"])
        self.assertEqual(rules["575"]["times"], ["18:00"])
        self.assertEqual(rules["576"]["times"], ["06:00"])
        for code in set(DAILY) | set(HOURLY):
            self.assertIn(rules[code]["schedule_mode"], ("interval", "times"))
        names = {p["element_id"]: p["name"] for p in result["parameters"] if p["element_id"] != "116"}
        inventory = dict(element_names=names, eligible_stations={code:list(registry) for code in names}, catalog_errors=[])
        catalog = catalog_configuration(result, inventory)
        catalog_rules = {p["element_id"]: p for p in catalog["parameters"]}
        for station, codes in EXCLUDED.items():
            for code in codes:
                self.assertIn(station, catalog_rules[code]["excluded_station_codes"])
                self.assertNotIn(station, catalog_rules[code]["station_codes"])
                self.assertNotIn(station, catalog_rules[code]["assessment_station_codes"])
        busogo = next(s for s in result["stations"] if s["station_code"] == "40302100")
        for code in ("102", "103", "105", "166"):
            self.assertEqual(busogo["hourly_overrides"][code]["hours"], list(range(6, 19)))
        self.assertFalse(busogo["hourly_overrides"]["184"]["count_in_summary"])
        self.assertEqual(busogo["hourly_overrides"]["184"]["hours"], [])
        self.assertNotIn("20602100", rules["177"]["excluded_station_codes"])


if __name__ == "__main__": unittest.main()
