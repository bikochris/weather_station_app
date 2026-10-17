"""Scientific matching, coverage, chart identity and bounded SQL regression tests."""
import inspect
import sqlite3
import unittest
from datetime import datetime, timedelta

from backend.observation_analysis_engine import comparisons, monitoring


class ObservationAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE observations(station TEXT, element TEXT, stamp TEXT, value REAL, "
                          "run_id INTEGER, PRIMARY KEY(station, element, stamp))")
        self.pairs = [{"id": "site", "label": "Site", "manual_code": "M", "automatic_code": "A"}]
        self.rules = [{"id": "temp", "label": "Temperature", "manual_element": "101",
                       "automatic_element": "1012", "method": "hourly"}]
        self.stations = [{"station_code": code, "station_name": code} for code in ("M", "A")]

    def put(self, station, element, stamp, value):
        self.conn.execute("INSERT INTO observations VALUES(?,?,?,?,1)", (station, element, stamp, value))

    def slots(self, element="1107", values=None, day="2026-01-01", omit=()):
        opening = datetime.fromisoformat(day + " 08:00")
        for index in range(144):
            if index not in omit:
                time = (opening + timedelta(minutes=10 * index)).strftime("%Y-%m-%d %H:%M")
                self.put("A", element, time, values[index] if values is not None else 1)

    def compare(self, **kwargs):
        options = dict(start="2026-01-01 00:00", end="2026-01-01 02:00")
        options.update(kwargs)
        return comparisons(self.conn, self.pairs, self.rules, **options)

    def daily_rule(self, manual, method):
        self.rules = [{"id": method, "label": method, "manual_element": str(manual),
                       "automatic_element": "1107" if str(manual) == "5" else "1012", "method": method}]

    def test_signature_rules_and_read_only(self):
        self.assertIn("rules", inspect.signature(comparisons).parameters)
        before = self.conn.total_changes
        self.compare()
        self.assertEqual(before, self.conn.total_changes)
        self.assertTrue(self.conn.in_transaction is False)

    def test_r_squared_uses_matched_values_and_is_independent_of_chart_period(self):
        for hour, manual, automatic in [(0, 1, 2), (1, 2, 4), (2, 3, 5)]:
            stamp = f"2026-01-01 {hour:02d}:00"
            self.put("M", "101", stamp, manual)
            self.put("A", "1012", stamp, automatic)
        self.put("M", "101", "2026-01-01 03:00", 999)
        for period in ("hour", "day"):
            result = self.compare(end="2026-01-01 04:00", period=period)
            self.assertAlmostEqual(result["per_parameter"][0]["r_squared"], 27 / 28)
            self.assertAlmostEqual(result["per_parameter"][0]["regression_slope"], 1.5)
            self.assertAlmostEqual(result["per_parameter"][0]["regression_intercept"], 2 / 3)
            self.assertAlmostEqual(result["summary"]["r_squared"], 27 / 28)

    def test_r_squared_undefined_for_missing_single_or_constant_pairs(self):
        self.assertIsNone(self.compare()["per_parameter"][0]["r_squared"])
        self.put("M", "101", "2026-01-01 00:00", 1)
        self.put("A", "1012", "2026-01-01 00:00", 2)
        self.assertIsNone(self.compare()["per_parameter"][0]["r_squared"])
        self.put("M", "101", "2026-01-01 01:00", 1)
        self.put("A", "1012", "2026-01-01 01:00", 4)
        self.assertIsNone(self.compare()["per_parameter"][0]["r_squared"])

    def test_r_squared_pools_multiple_stations_instead_of_averaging_scores(self):
        self.pairs.append(dict(id="other", manual_code="M2", automatic_code="A2"))
        for manual_code, automatic_code, observations in [
                ("M", "A", [(1, 2), (2, 4)]), ("M2", "A2", [(3, 5), (4, 4)])]:
            for hour, (manual, automatic) in enumerate(observations):
                stamp = f"2026-01-01 {hour:02d}:00"
                self.put(manual_code, "101", stamp, manual)
                self.put(automatic_code, "1012", stamp, automatic)
        self.assertAlmostEqual(self.compare()["per_parameter"][0]["r_squared"], 12.25 / 23.75)

    def test_hour_exact_not_last_or_nearest_and_zero(self):
        self.put("M", "101", "2026-01-01 00:00", 0)
        self.put("A", "1012", "2026-01-01 00:00", 2)
        self.put("A", "1012", "2026-01-01 00:50", 99)
        self.put("M", "101", "2026-01-01 01:00", 3)
        self.put("A", "1012", "2026-01-01 01:50", 3)
        result = self.compare()
        self.assertEqual(result["rows"][0]["difference"], 2)
        self.assertIsNone(result["rows"][1]["automatic"])
        self.assertIsNone(result["rows"][1]["inconsistent"])
        self.assertEqual(result["summary"], dict(paired=1, missing=1, inconsistent=0, bias=2, mae=2, rmse=2, r_squared=None))

    def test_pair_first_then_bucket_and_native_error_stats(self):
        for hour, mv, av in ((0, 10, 12), (1, 30, 28), (2, 900, None)):
            time = f"2026-01-01 {hour:02d}:00"
            self.put("M", "101", time, mv)
            if av is not None:
                self.put("A", "1012", time, av)
        result = self.compare(end="2026-01-01 03:00", period="day")
        row = result["rows"][0]
        self.assertAlmostEqual(row["manual"], 940 / 3)
        self.assertEqual(row["automatic"], 20)
        self.assertAlmostEqual(row["difference"], 20 - 940 / 3)
        self.assertEqual((row["manual_count"], row["automatic_count"], row["expected_automatic"]), (3, 2, 3))
        self.assertIsNone(row["inconsistent"])
        self.assertFalse(row["complete"])
        self.assertEqual(result["summary"]["mae"], 2)
        self.assertEqual(result["summary"]["rmse"], 2)

    def test_missing_automatic_element_explains_absent_curve(self):
        self.put("M", "101", "2026-01-01 00:00", 20)
        result = self.compare()
        self.assertEqual(result["series"][0]["manual"], 20)
        self.assertIsNone(result["series"][0]["automatic"])
        self.assertTrue(any("no numeric automatic element 1012" in warning and "curve cannot be drawn" in warning for warning in result["warnings"]))
        self.put("A", "1012", "2026-01-01 00:10", 21)
        result = self.compare()
        self.assertTrue(any("no exact :00 automatic element 1012" in warning for warning in result["warnings"]))

    def test_october_1_through_20_has_two_exact_hourly_curves(self):
        start = datetime(2026, 10, 1)
        for index in range(480):
            current = start + timedelta(hours=index)
            self.put("M", "101", current.strftime("%Y-%m-%d %H:%M"), 20 + index / 100)
            self.put("A", "1012", current.strftime("%Y-%m-%d %H:%M"), 21 + index / 100)
            for minute in (10, 20, 30, 40, 50):
                self.put("A", "1012", (current + timedelta(minutes=minute)).strftime("%Y-%m-%d %H:%M"), 999)
        result = self.compare(start="2026-10-01 00:00", end="2026-10-21 00:00", period="hour")
        self.assertEqual(len(result["series"]), 480)
        for index, point in enumerate(result["series"]):
            self.assertEqual(point["time"], (start + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M"))
            self.assertEqual(point["manual"], 20 + index / 100)
            self.assertEqual(point["automatic"], 21 + index / 100)
            self.assertEqual(point["aggregation"], "exact_hour")
            self.assertEqual((point["manual_element"], point["automatic_element"]), ("101", "1012"))

    def test_daily_boundary_08_excludes_next_08(self):
        self.daily_rule(5, "sum")
        self.put("M", "5", "2026-01-01 00:00", 144)
        self.slots()
        self.put("A", "1107", "2026-01-01 07:50", 1000)
        self.put("A", "1107", "2026-01-02 08:00", 1000)
        result = self.compare(start="2026-01-01 08:00", end="2026-01-02 08:00")
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual((row["manual"], row["automatic"], row["difference"]), (144, 144, 0))
        self.assertEqual(row["time"], "2026-01-01 00:00")
        self.assertEqual(row["automatic_count"], 144)
        self.assertEqual(row["expected_automatic"], 144)
        self.assertTrue(row["complete"])

    def test_partial_daily_aggregate_keeps_all_available_values_without_qc(self):
        self.daily_rule(5, "sum")
        self.put("M", "5", "2026-01-01 00:00", 143)
        self.slots(omit=(1,))
        self.put("A", "1107", "2026-01-01 08:11", 50)
        result = self.compare(end="2026-01-02 08:00")
        row = result["rows"][0]
        self.assertEqual(row["observed_automatic"], 193)
        self.assertEqual(row["automatic_count"], 144)
        self.assertEqual(row["automatic"], 193)
        self.assertEqual(row["difference"], 50)
        self.assertTrue(row["partial_day"])
        self.assertEqual(result["summary"]["paired"], 1)
        self.assertFalse(result["warnings"])

    def test_daily_labels_filtered_by_metday_opening(self):
        self.daily_rule(5, "sum")
        for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
            self.put("M", "5", day + " 00:00", 144)
            self.slots(day=day)
        result = self.compare(start="2026-01-01 08:00", end="2026-01-03 08:00")
        self.assertEqual([r["time"] for r in result["rows"]], ["2026-01-01 00:00", "2026-01-02 00:00"])
        self.assertTrue(all(r["complete"] for r in result["rows"]))
        result = self.compare(start="2026-01-01 09:00", end="2026-01-03 08:00")
        self.assertEqual([r["time"] for r in result["rows"]], ["2026-01-02 00:00"])
        monitored = monitoring(self.conn, [self.stations[0]], "2026-01-01 08:00", "2026-01-03 08:00")
        self.assertEqual([r["time"] for r in monitored["daily_rows"]],
                         ["2026-01-01 00:00", "2026-01-02 00:00"])

    def test_partial_daily_min_max_remain_visible(self):
        self.slots("1012", list(range(144)), omit=(0,))
        for manual, method, expected in (("2", "max", 143), ("3", "min", 1)):
            self.daily_rule(manual, method)
            self.put("M", manual, "2026-01-01 00:00", expected)
            result = self.compare(end="2026-01-02 08:00")
            row = result["rows"][0]
            self.assertEqual(row["observed_automatic"], expected)
            self.assertEqual(row["difference"], 0)
            self.assertEqual(row["automatic_count"], 143)
            self.assertTrue(row["partial_day"])

    def test_daily_min_max_use_entire_metday(self):
        self.slots("1012", list(range(144)))
        self.put("M", "2", "2026-01-01 00:00", 143)
        self.put("M", "3", "2026-01-01 00:00", 0)
        self.rules = [{"id": method, "label": method, "manual_element": element,
                       "automatic_element": "1012", "method": method}
                      for element, method in (("2", "max"), ("3", "min"))]
        result = self.compare(end="2026-01-02 08:00", period="month")
        self.assertEqual({r["parameter"]: r["automatic"] for r in result["rows"]}, {"max": 143, "min": 0})
        self.assertTrue(all(r["complete"] for r in result["rows"]))
        self.assertIsNone(result["summary"]["bias"])

    def test_daily_series_aggregates_but_rows_stay_daily(self):
        for manual, method, expected in (("5", "sum", 432), ("2", "max", 2), ("3", "min", 1)):
            with self.subTest(method=method):
                self.conn.execute("DELETE FROM observations")
                self.daily_rule(manual, method)
                element = self.rules[0]["automatic_element"]
                for day, value in (("2026-01-01", 1), ("2026-01-02", 2)):
                    self.slots(element, [value] * 144, day=day)
                    self.put("M", manual, day + " 00:00", value * 144 if manual == "5" else value)
                for period, minutes in (("week", 60), ("month", 60), ("custom", 4320)):
                    result = self.compare(end="2026-01-03 08:00", period=period, minutes=minutes)
                    self.assertEqual(len(result["rows"]), 2)
                    self.assertEqual(len(result["series"]), 1)
                    self.assertEqual(result["series"][0]["automatic"], expected)
                    self.assertEqual(result["series"][0]["aggregation"], method)

    def test_distinct_parameter_and_site_series_and_stats(self):
        self.rules.append({"id": "soil", "label": "Soil", "manual_element": "176",
                           "automatic_element": "2112", "method": "hourly"})
        self.pairs.append({"id": "second", "label": "Second", "manual_code": "X", "automatic_code": "Y"})
        for pair in self.pairs:
            for rule in self.rules:
                self.put(pair["manual_code"], rule["manual_element"], "2026-01-01 00:00", 10)
                self.put(pair["automatic_code"], rule["automatic_element"], "2026-01-01 00:00", 12)
        result = self.compare(end="2026-01-01 01:00")
        self.assertEqual(len(result["series"]), 4)
        self.assertEqual(len(result["per_parameter"]), 2)
        self.assertIsNone(result["summary"]["bias"])
        self.assertEqual(result["summary"]["paired"], 4)
        self.assertTrue(all(r["bias"] == 2 for r in result["per_parameter"]))

    def test_custom_and_calendar_buckets(self):
        for hour in range(4):
            for station, element in (("M", "101"), ("A", "1012")):
                self.put(station, element, f"2026-01-01 {hour:02d}:00", hour)
        result = self.compare(end="2026-01-01 04:00", period="custom", minutes=120)
        self.assertEqual([r["manual"] for r in result["series"]], [0.5, 2.5])
        for period, label in (("week", "2025-12-29 00:00"), ("month", "2026-01-01 00:00")):
            self.assertEqual(self.compare(period=period)["series"][0]["time"], label)

    def test_wind_rose_common_hour_north_wrap_calm_and_invalid(self):
        for hour, direction, speed in ((0, 359, 2), (1, 1, 2), (2, 360, 0.5), (3, 361, 2), (4, 90, -1)):
            for station, de, se in (("M", "112", "196"), ("A", "1042", "1092")):
                self.put(station, de, f"2026-01-01 {hour:02d}:00", direction)
                self.put(station, se, f"2026-01-01 {hour:02d}:00", speed)
        self.put("M", "112", "2026-01-01 05:00", 90)
        self.put("M", "196", "2026-01-01 05:00", 2)
        for station, element in (("A", "1042"), ("A", "1092")):
            self.put(station, element, "2026-01-01 05:50", 2)
        result = self.compare(end="2026-01-01 06:00")
        for rose in result["roses"].values():
            self.assertEqual(len(rose["bins"]), 16)
            self.assertEqual((rose["bins"][0], rose["calm"], rose["total"]), (3, 2, 5))
            self.assertEqual(sum(rose["bins"]) + rose["calm"], rose["total"])
        self.assertFalse(any("Invalid wind" in warning or "Negative" in warning for warning in result["warnings"]))

    def test_monitoring_partial_and_occurrence_metadata(self):
        self.put("M", "5", "2026-01-01 00:00", 10)
        self.slots(omit=(0,))
        self.put("A", "1092", "2026-01-01 09:10", 12)
        self.put("A", "1092", "2026-01-01 09:20", 12)
        self.put("A", "1092", "2026-01-01 09:00", 3)
        result = monitoring(self.conn, self.stations, "2026-01-01 00:00", "2026-01-02 08:00")
        self.assertEqual(result["summary"]["max_daily_rainfall"], 143)
        self.assertEqual(result["summary"]["observed_daily_max"], 143)
        aws = next(r for r in result["daily_max"] if r["station_code"] == "A")
        self.assertEqual((aws["value"], aws["time"], aws["element"], aws["automatic_count"]),
                         (143, "2026-01-01 00:00", "1107", 143))
        self.assertTrue(aws["partial_day"])
        self.assertEqual(result["wind_max"][1]["time"], "2026-01-01 09:00")
        self.assertEqual(result["summary"]["max_wind"], 3)
        self.assertEqual(result["wind_max"][1]["element"], "1092")
        self.assertEqual(len(result["daily_rows"]), 2)
        self.assertEqual({r["element"] for r in result["series"]}, {"5", "1107", "1092"})

    def test_monitoring_zero_invalid_missing_and_negative(self):
        for element, value in (("1107", -5), ("1092", -2), ("5", 0), ("196", 0)):
            self.put("M", element, "2026-01-01 00:00", value)
        self.put("A", "1107", "2026-01-01 00:00", None)
        self.put("A", "1092", "2026-01-01 00:00", float("inf"))
        result = monitoring(self.conn, self.stations, "2026-01-01 00:00", "2026-01-02 00:00")
        self.assertEqual([row["value"] for row in result["rain_rows"]], [-5])
        self.assertEqual(result["summary"]["max_daily_rainfall"], 0)
        self.assertEqual(result["summary"]["max_wind"], 0)
        self.assertIsNone(result["wind_max"][1]["value"])
        self.assertEqual(next(r for r in result["series"] if r["element"] == "5")["rainfall"], 0)
        self.assertTrue(result["warnings"])

    def test_day_range_uses_next_morning_and_retains_partial_temperature(self):
        self.daily_rule(2, "max")
        self.put("M", "2", "2026-10-01 00:00", 30)
        self.put("A", "1012", "2026-10-01 09:10", 32)
        self.put("A", "1012", "2026-10-02 07:50", 40)
        self.put("A", "1012", "2026-10-02 08:00", 99)
        result = self.compare(start="2026-10-01 00:00", end="2026-10-02 00:00")
        self.assertEqual(result["series"][0]["automatic"], 40)
        self.assertEqual(result["rows"][0]["automatic_count"], 2)
        self.assertEqual(result["rows"][0]["difference"], 10)
        self.assertFalse(result["warnings"])

    def test_monitoring_rain_chart_metday_boundary_and_actual_hour_custom(self):
        records = [("2026-10-02 07:50", 1), ("2026-10-02 08:00", 2),
                   ("2026-10-02 23:50", 4), ("2026-10-03 07:50", 8)]
        for time, value in records:
            self.put("A", "1107", time, value)
        options = dict(conn=self.conn, stations=[self.stations[1]],
                       start="2026-10-01 08:00", end="2026-10-03 08:00")
        result = monitoring(**options)
        self.assertEqual([(r["time"], r["rainfall"]) for r in result["series"]],
                         [("2026-10-01 00:00", 1), ("2026-10-02 00:00", 14)])
        self.assertEqual([(r["time"], r["value"]) for r in result["rain_rows"]], records)
        for period in ("hour", "custom"):
            result = monitoring(**options, period=period, minutes=60)
            self.assertEqual([(r["time"], r["rainfall"]) for r in result["series"]],
                             [("2026-10-02 07:00", 1), ("2026-10-02 08:00", 2),
                              ("2026-10-02 23:00", 4), ("2026-10-03 07:00", 8)])
        anchored = monitoring(**options, period="custom", minutes=100)
        self.assertEqual(anchored["series"][0]["time"], "2026-10-02 07:20")

    def test_monitoring_rain_week_month_follow_metday_labels(self):
        for time, value in (("2026-11-01 07:50", 1), ("2026-11-01 08:00", 2),
                            ("2026-11-02 07:50", 4), ("2026-11-02 08:00", 8)):
            self.put("A", "1107", time, value)
        for period, expected in (("month", [("2026-10-01 00:00", 1), ("2026-11-01 00:00", 14)]),
                                 ("week", [("2026-10-26 00:00", 7), ("2026-11-02 00:00", 8)])):
            with self.subTest(period=period):
                result = monitoring(self.conn, [self.stations[1]], "2026-10-31 08:00",
                                    "2026-11-03 08:00", period=period)
                self.assertEqual([(r["time"], r["rainfall"]) for r in result["series"]], expected)

    def test_monitoring_manual_daily_latest_for_charts_raw_records_retained(self):
        for time, value in (("2026-10-02 00:00", 3), ("2026-10-02 12:00", 7), ("2026-10-03 00:00", 2)):
            self.put("M", "5", time, value)
        for period in ("day", "week", "month", "hour", "custom"):
            with self.subTest(period=period):
                result = monitoring(self.conn, [self.stations[0]], "2026-10-02 08:00",
                                    "2026-10-04 08:00", period=period)
                self.assertEqual(sum(r["rainfall"] for r in result["series"]), 9)
                self.assertEqual([r["value"] for r in result["rain_rows"]], [3, 7, 2])
                self.assertEqual([r["value"] for r in result["daily_rows"]], [7, 2])
                self.assertEqual(result["daily_max"][0]["value"], 7)

    def test_station_category_does_not_reject_available_measurements(self):
        self.put("M", "101", "2026-01-01 00:00", 20)
        self.put("A", "1012", "2026-01-01 00:00", 21)
        for field, category in (("automatic_category", "Automatic Rain Gauge"),
                                ("manual_category", "Rainfall Station")):
            with self.subTest(category=category):
                self.pairs[0][field] = category
                result = self.compare()
                self.assertEqual(result["series"][0]["manual"], 20)
                self.assertEqual(result["series"][0]["automatic"], 21)
                self.assertEqual(result["summary"]["paired"], 1)
                del self.pairs[0][field]
        self.pairs[0]["automatic_category"] = "Automatic Rain Gauge"
        self.rules.append({"id": "rain", "label": "Rain", "manual_element": "5",
                           "automatic_element": "1107", "method": "sum"})
        result = self.compare(end="2026-01-02 08:00")
        self.assertEqual({r["parameter_id"] for r in result["rows"]}, {"rain", "temp"})
        self.pairs[0]["automatic_category"] = "Automatic Weather station"
        self.pairs[0]["manual_category"] = "Climatic Station"
        result = self.compare()
        self.assertTrue(any(r["parameter_id"] == "temp" for r in result["rows"]))

    def test_complete_zero_daily_max_is_valid(self):
        self.slots(values=[0] * 144)
        result = monitoring(self.conn, [self.stations[1]], "2026-01-01 08:00", "2026-01-02 08:00")
        self.assertEqual(result["summary"]["max_daily_rainfall"], 0)
        self.assertEqual(result["summary"]["rain_records"], 0)
        self.assertTrue(result["daily_max"][0]["complete"])

    def test_source_and_output_limits_are_separate(self):
        self.slots(values=[0] * 144)
        result = monitoring(self.conn, [self.stations[1]], "2026-01-01 08:00", "2026-01-02 08:00", max_rows=5)
        self.assertEqual(result["summary"]["max_daily_rainfall"], 0)
        with self.assertRaisesRegex(ValueError, "narrow the range"):
            monitoring(self.conn, self.stations, "2026-01-01 08:00", "2026-01-02 08:00", max_source_rows=10)
        with self.assertRaisesRegex(ValueError, "narrow the range"):
            self.compare(max_rows=1)
        result = self.compare(max_rows=1, period="day")
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["summary"]["missing"], 2)

    def test_half_open_bounds_and_sql_index(self):
        self.put("M", "101", "2026-01-01 00:00", 1)
        self.put("A", "1012", "2026-01-01 00:00", 1)
        self.put("A", "1012", "2026-01-01 01:00", 99)
        self.assertEqual(self.compare(end="2026-01-01 01:00")["summary"]["paired"], 1)
        plan = self.conn.execute("EXPLAIN QUERY PLAN SELECT stamp,value FROM observations "
                                 "WHERE station=? AND element=? AND stamp>=? AND stamp<? ORDER BY stamp LIMIT ?",
                                 ("M", "101", "2026-01-01", "2026-02-01", 100)).fetchall()
        self.assertIn("SEARCH", str(plan))
        self.assertIn("stamp>? AND stamp<?", str(plan))

    def test_31_days_many_stations_mostly_zero_exceeds_old_source_limit(self):
        opening = datetime(2026, 1, 1, 8)
        stamps = [(opening + timedelta(minutes=10 * index)).strftime("%Y-%m-%d %H:%M")
                  for index in range(31 * 144)]
        stations = [{"station_code": str(index), "station_name": str(index)} for index in range(12)]
        self.conn.executemany("INSERT INTO observations VALUES(?,?,?,?,1)",
                              ((station["station_code"], element, stamp, 0)
                               for station in stations for element in ("1107", "1092") for stamp in stamps))
        result = monitoring(self.conn, stations, "2026-01-01 08:00", "2026-02-01 08:00")
        self.assertEqual(result["summary"]["rain_records"], 0)
        self.assertEqual(result["summary"]["max_daily_rainfall"], 0)
        self.assertEqual(len(result["daily_rows"]), 12 * 31)
        self.assertTrue(all(r["complete"] for r in result["daily_rows"]))

    def test_validation(self):
        for kwargs in ({"period": "year"}, {"minutes": 0}, {"max_rows": 0}, {"max_source_rows": 0},
                       {"tolerance": -1}, {"calm_threshold": -1}, {"end": "2025-01-01"},
                       {"start": "2026-01-01T00:00+00:00"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.compare(**kwargs)


if __name__ == "__main__":
    unittest.main()
