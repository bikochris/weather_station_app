import unittest
from pydantic import ValidationError
from backend.data_upload_engine import (ParameterRule, parse_source, analyze_source,
    apply_observation_defaults, default_configuration, scheduled_minutes)
from backend.catalog_schedule_20261015 import registry_records
from backend.instrument_availability import catalog_configuration


class ObservationScheduleTests(unittest.TestCase):
    def assess(self, rules, lines, overrides=None):
        parsed = parse_source(("station_id,element_id,element_name,date_time,value\n" + lines).encode(), "test.csv")
        config = dict(version=1, parameters=rules, stations=[dict(station_code="S", station_name="Station",
            hourly_overrides=overrides or {})])
        return analyze_source(parsed, config)

    def rule(self, code="A", **kwargs):
        return dict(element_id=code, name=code, cadence="hourly", hours=list(range(24)),
            schedule_mode="interval", interval_minutes=10, **kwargs)

    def test_ten_minute_slots_and_duplicates(self):
        result = self.assess([self.rule()], "S,A,A,01/07/2026 00:00,1\nS,A,A,01/07/2026 00:10,0\nS,A,A,01/07/2026 00:10,\nS,A,A,01/07/2026 00:11,5\n")
        row = result["results"][0]
        self.assertEqual(row["hourly_expected"], 31 * 144)
        self.assertEqual(row["hourly_available"], 2)
        self.assertEqual(result["duplicate_rows"], 1)
        self.assertIn("00:20", result["hourly_details"][0]["missing_hours"])

    def test_custom_intervals_and_validation(self):
        for interval in [1, 5, 10, 60]:
            rule = ParameterRule(**(self.rule() | dict(interval_minutes=interval)))
            self.assertEqual(len(scheduled_minutes(rule)), 1440 // interval)
        for bad in [0, 1441, 1.5]:
            with self.assertRaises(ValidationError): ParameterRule(**(self.rule() | dict(interval_minutes=bad)))
        for times in [[], ["24:00"], ["06:00", "06:00"]]:
            with self.assertRaises(ValidationError): ParameterRule(**(self.rule() | dict(schedule_mode="times", times=times)))

    def test_daily_and_three_daily_times_are_exact(self):
        rule = self.rule() | dict(cadence="daily", schedule_mode="times", times=["08:00"])
        result = self.assess([rule], "S,A,A,01/07/2026 07:50,1\nS,A,A,02/07/2026 08:00,0\n")
        self.assertEqual(result["results"][0]["daily_available"], 1)
        rule |= dict(cadence="hourly", times=["06:00", "12:00", "18:00"])
        result = self.assess([rule], "S,A,A,01/07/2026 06:10,1\nS,A,A,01/07/2026 12:00,1\n")
        self.assertEqual(result["results"][0]["hourly_expected"], 31 * 3)
        self.assertEqual(result["results"][0]["hourly_available"], 1)

    def test_cloud_parent_uses_same_minute(self):
        result = self.assess([self.rule("P"), self.rule("C", zero_parent="P")],
            "S,P,P,01/07/2026 00:00,0\nS,P,P,01/07/2026 00:10,1\n")
        child = next(r for r in result["element_results"] if r["element_id"] == "C")
        self.assertEqual(child["expected"], 31 * 144 - 1)

    def test_station_hour_exceptions_apply_to_intervals(self):
        result = self.assess([self.rule()], "S,A,A,01/07/2026 06:10,1\nS,A,A,01/07/2026 07:10,1\n",
            {"A": dict(hours=[6], reported_excluded_hours=[7])})
        self.assertEqual(result["results"][0]["hourly_expected"], 31 * 6)
        self.assertEqual(result["results"][0]["hourly_available"], 1)

    def test_hourly_minute_zero_and_edited_fixed_time(self):
        rule = self.rule() | dict(interval_minutes=60)
        result = self.assess([rule], "S,A,A,01/07/2026 06:10,1\nS,A,A,01/07/2026 07:00,1\n")
        self.assertEqual(result["results"][0]["hourly_available"], 1)
        fixed = ParameterRule(**(self.rule() | dict(schedule_mode="times", times=["07:30", "12:00"], hours=[6, 12, 18])))
        self.assertEqual(scheduled_minutes(fixed), [450, 720])
        self.assertEqual(scheduled_minutes(fixed, [12]), [720])

    def test_unavailable_instrument_is_not_expected_at_any_minute(self):
        rule = self.rule() | dict(station_codes=["S"])
        parsed = parse_source(b"station_id,element_id,element_name,date_time,value\nS,A,A,01/07/2026 00:00,1\n", "test.csv")
        for status in ["Unavailable", "Under Maintenance", "Inactive"]:
            inventory = dict(availability_mode="current_status", mapped_elements=["A"],
                eligible_stations={"A":["S"]}, assignments=[dict(station_code="S",element_ids=["A"],status=status)])
            result = analyze_source(parsed, dict(version=1,catalog_managed=True,parameters=[rule],
                stations=[dict(station_code="S",station_name="Station")]), inventory)
            self.assertEqual(result["results"][0]["hourly_expected"], 0)
            self.assertIsNone(result["element_results"][0]["score"])

    def test_defaults_keep_daylight_station_rules_and_archived_ids(self):
        original = default_configuration()
        config = apply_observation_defaults(original)
        rules = config["archived_parameter_rules"]
        self.assertEqual(rules["112"]["hours"], list(range(6, 19)))
        self.assertEqual(rules["1081"]["interval_minutes"], 10)
        self.assertEqual(rules["180"]["times"], ["06:00", "12:00", "18:00"])
        self.assertEqual(rules["5"]["times"], ["08:00"])
        self.assertEqual(config["stations"], original["stations"])
        self.assertEqual(original["parameters"][0]["schedule_mode"], "legacy")
        inventory = dict(element_names={"1081":"Voltage"}, eligible_stations={"1081":[]}, catalog_errors=[])
        derived = catalog_configuration(config, inventory)
        self.assertEqual(derived["catalog_pending"], [])
        self.assertEqual(derived["parameters"][0]["interval_minutes"], 10)
        reserved = next(p for p in derived["parameters"] if p["element_id"] == "116")
        self.assertEqual(reserved["schedule_mode"], "none")
        self.assertFalse(reserved["enabled"])
        self.assertEqual(derived["archived_parameter_rules"]["116"]["interval_minutes"], 60)

    def test_latest_registry_removes_humidity_10_and_shifts_once(self):
        rows = {r["registry_number"]:r for r in registry_records()}
        self.assertEqual(len(rows), 42)
        self.assertEqual(rows[59]["element_ids"], ["104"])
        self.assertEqual(rows[63]["element_ids"], ["180"])
        self.assertEqual(rows[67]["element_ids"], ["184"])
        self.assertEqual(rows[71]["element_ids"], ["106", "185"])
        self.assertNotIn("10", {code for row in rows.values() for code in row["element_ids"]})


if __name__ == "__main__": unittest.main()
