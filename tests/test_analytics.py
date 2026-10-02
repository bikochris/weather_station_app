import unittest
from datetime import date
from unittest.mock import patch

from fastapi import HTTPException

from backend.main import (
    CombinedDataCount, get_data_counts, get_data_requests, save_combined_data_count,
    effective_instrument_status, fiscal_maintenance_coverage, fiscal_period,
    maintenance_schedule_status,
    instrument_due_alert, maintenance_coverage,
)


class CombinedDataCountTests(unittest.TestCase):
    def setUp(self):
        self.item = CombinedDataCount.model_validate({
            "record_month": "2026-09",
            "station_categories": ["Rainfall station", "Principal stations"],
            "record_count": 234,
        })

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_combined_count_is_saved_once(self, get_connection, _ensure):
        connection = get_connection.return_value
        connection.cursor.return_value.fetchall.return_value = []
        result = save_combined_data_count(self.item, {"user_id": 1, "username": "tester"})
        self.assertEqual(result, {"saved": 1, "total": 234})
        insert_call = next(call for call in connection.cursor.return_value.execute.call_args_list
                           if "INSERT INTO monthly_combined_data_counts" in call.args[0])
        values = insert_call.args[1]
        self.assertEqual(values[:3], ("Principal stations|Rainfall station", "2026-09-01", 234))
        connection.commit.assert_called_once()

    @patch("backend.main.get_connection")
    def test_duplicate_category_is_rejected_before_database_access(self, get_connection):
        self.item.station_categories.append("Rainfall station")
        with self.assertRaises(HTTPException) as error:
            save_combined_data_count(self.item, {"user_id": 1, "username": "tester"})
        self.assertEqual(error.exception.status_code, 400)
        get_connection.assert_not_called()

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_overlap_with_single_category_is_rejected(self, get_connection, _ensure):
        connection = get_connection.return_value
        connection.cursor.return_value.fetchall.side_effect = [[("Rainfall station",)], []]
        with self.assertRaises(HTTPException) as error:
            save_combined_data_count(self.item, {"user_id": 1, "username": "tester"})
        self.assertEqual(error.exception.status_code, 409)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_filtered_summary_combines_categories_by_month(self, get_connection, _ensure):
        connection = get_connection.return_value
        connection.cursor.return_value.fetchall.side_effect = [[], [
            {"data_count_id": 1, "category_key": "Principal stations|Rainfall station",
             "record_month": "2026-09", "record_count": 234},
        ]]
        result = get_data_counts(station_categories="Rainfall station", user={})
        self.assertEqual(result["summary"]["by_month"], {"2026-09": 234})
        self.assertEqual(result["summary"]["records"], 234)
        self.assertEqual(result["summary"]["categories"], 2)
        self.assertEqual(result["items"][0]["station_categories"], ["Principal stations", "Rainfall station"])

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_monthly_and_july_to_june_fiscal_totals(self, get_connection, _ensure):
        rows = [
            {"station_category": "Rainfall station", "record_month": month, "record_count": count}
            for month, count in [("2026-07", 10), ("2026-10", 20), ("2027-01", 30), ("2027-04", 40),
                                 ("2027-06", 50), ("2027-07", 60)]
        ]
        get_connection.return_value.cursor.return_value.fetchall.side_effect = [rows, []]
        summary = get_data_counts(user={})["summary"]
        self.assertEqual(summary["records"], 210)
        self.assertEqual(summary["by_month"]["2027-04"], 40)
        self.assertNotIn("by_calendar_year", summary)
        self.assertEqual(summary["by_fiscal_year"], {"2026/2027": 150, "2027/2028": 60})
        self.assertEqual(summary["by_fiscal_quarter"], {
            "2026/2027 Q1": 10, "2026/2027 Q2": 20,
            "2026/2027 Q3": 30, "2026/2027 Q4": 90, "2027/2028 Q1": 60,
        })


class DataRequestSummaryTests(unittest.TestCase):
    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.get_connection")
    def test_requests_sum_categories_once_per_month_and_fiscal_period(self, get_connection, _ensure):
        get_connection.return_value.cursor.return_value.fetchall.return_value = [
            {"request_month": "2026-09", "category": "Student", "served_requests": 2},
            {"request_month": "2026-09", "category": "Research", "served_requests": 3},
            {"request_month": "2026-10", "category": "Student", "served_requests": 7},
            {"request_month": "2027-01", "category": "Research", "served_requests": 11},
            {"request_month": "2027-07", "category": "Research", "served_requests": 13},
        ]
        result = get_data_requests(month_from=None, month_to=None, _user={"department": "Admin"})
        self.assertEqual(result["monthly_totals"], {
            "2026-09": 5, "2026-10": 7, "2027-01": 11, "2027-07": 13,
        })
        self.assertEqual(result["fiscal_quarter_totals"], {
            "2026/2027 Q1": 5, "2026/2027 Q2": 7,
            "2026/2027 Q3": 11, "2027/2028 Q1": 13,
        })
        self.assertEqual(result["fiscal_year_totals"], {"2026/2027": 23, "2027/2028": 13})


class InstrumentStatusTests(unittest.TestCase):
    def test_replacement_takes_priority_when_both_dates_due(self):
        self.assertEqual(
            effective_instrument_status("Operational", date(2026, 1, 1), date(2026, 1, 1), date(2026, 9, 1)),
            "Needs Replacement",
        )

    def test_manual_inactive_status_is_preserved(self):
        self.assertEqual(
            effective_instrument_status("Inactive", date(2026, 1, 1), None, date(2026, 9, 1)),
            "Inactive",
        )


class MaintenanceCoverageTests(unittest.TestCase):
    def test_overlapping_report_counts_as_maintained(self):
        report = {"period_start": date(2026, 8, 30), "period_end": date(2026, 9, 2)}
        result = maintenance_coverage([report], date(2026, 9, 1), date(2026, 9, 30), 90)
        self.assertEqual(result["coverage"], "Maintained")
        self.assertEqual(result["reports_in_period"], 1)
        self.assertFalse(result["pending"])

    def test_service_becomes_pending_at_frequency_deadline(self):
        report = {"period_start": date(2026, 1, 1), "period_end": date(2026, 1, 5)}
        result = maintenance_coverage([report], date(2026, 4, 1), date(2026, 4, 30), 90)
        self.assertEqual(result["coverage"], "Not maintained")
        self.assertTrue(result["pending"])
        self.assertEqual(result["next_due_date"], date(2026, 4, 5))

    def test_no_due_service_is_not_pending(self):
        report = {"period_start": date(2026, 3, 1), "period_end": date(2026, 3, 5)}
        result = maintenance_coverage([report], date(2026, 4, 1), date(2026, 4, 30), 90)
        self.assertFalse(result["pending"])


class FiscalMaintenanceTests(unittest.TestCase):
    def test_fiscal_quarters_cross_calendar_year(self):
        self.assertEqual(fiscal_period(2026, 1), (date(2026, 7, 1), date(2026, 9, 30)))
        self.assertEqual(fiscal_period(2026, 3), (date(2027, 1, 1), date(2027, 3, 31)))
        self.assertEqual(fiscal_period(2026), (date(2026, 7, 1), date(2027, 6, 30)))

    def test_two_visits_required_in_each_quarter(self):
        reports = [{"report_id": n, "period_end": date(2026, 7, n)} for n in (1, 2)]
        result = fiscal_maintenance_coverage(reports, 2026, 1, "quarterly", 2, date(2026, 10, 1))
        self.assertEqual(result["coverage"], "Maintained")
        whole_year = fiscal_maintenance_coverage(reports, 2026, None, "quarterly", 2, date(2027, 7, 1))
        self.assertEqual(whole_year["coverage"], "Not maintained")

    def test_annual_target_waits_until_fiscal_year_end(self):
        result = fiscal_maintenance_coverage([], 2026, 1, "yearly", 3, date(2026, 10, 1))
        self.assertEqual(result["coverage"], "Pending")
        result = fiscal_maintenance_coverage([], 2026, None, "yearly", 3, date(2027, 7, 1))
        self.assertEqual(result["coverage"], "Not maintained")

    def test_due_alerts(self):
        today = date(2026, 9, 29)
        self.assertEqual(instrument_due_alert(date(2026, 8, 29), today)["level"], "overdue")
        self.assertEqual(instrument_due_alert(date(2027, 3, 29), today)["level"], "warning")
        self.assertEqual(instrument_due_alert(date(2027, 4, 29), today)["level"], "none")

    def test_summary_uses_actual_maintenance_dates(self):
        history = [{"maintenance_date": date(2026, 7, 10)},
                   {"maintenance_date": date(2026, 9, 20)}]
        result = maintenance_schedule_status(history, 2026, 1, "quarterly", 2,
                                             date(2026, 9, 30))
        self.assertEqual(result["status"], "Maintained")
        self.assertEqual(result["last_maintenance_date"], date(2026, 9, 20))
        self.assertEqual(result["maintenance_dates"], [date(2026, 7, 10), date(2026, 9, 20)])

    def test_missed_quarter_remains_red_when_later_quarter_is_met(self):
        history = [{"maintenance_date": date(2026, 10, 10)}]
        result = maintenance_schedule_status(history, 2026, None, "quarterly", 1,
                                             date(2026, 11, 1))
        self.assertEqual(result["status"], "Not maintained")
        self.assertEqual(result["quarter_progress"][0]["state"], "missed")

    def test_yearly_target_stays_pending_until_fiscal_deadline(self):
        result = maintenance_schedule_status([], 2026, None, "yearly", 2,
                                             date(2027, 3, 1))
        self.assertEqual(result["status"], "Pending")
        result = maintenance_schedule_status([], 2026, None, "yearly", 2,
                                             date(2027, 7, 1))
        self.assertEqual(result["status"], "Not maintained")

    def test_unconfigured_schedule_is_distinct_from_missed(self):
        result = maintenance_schedule_status([], 2026, 1, None, None,
                                             date(2026, 10, 1))
        self.assertEqual(result["status"], "Not configured")

    def test_quarter_cells_show_completed_missed_and_future(self):
        history = [{"maintenance_date": date(2026, 8, 1)},
                   {"maintenance_date": date(2027, 2, 1)}]
        result = maintenance_schedule_status(history, 2026, None, "quarterly", 1,
                                             date(2027, 4, 1))
        self.assertEqual([q["count"] for q in result["quarter_progress"]], [1, 0, 1, 0])
        self.assertEqual([q["state"] for q in result["quarter_progress"]],
                         ["good", "missed", "good", "pending"])

    def test_future_quarter_has_blank_count(self):
        result = maintenance_schedule_status([], 2026, 1, "quarterly", 1,
                                             date(2026, 9, 30))
        self.assertEqual(result["quarter_progress"][0]["count"], 0)
        self.assertIsNone(result["quarter_progress"][1]["count"])
        self.assertEqual(result["quarter_progress"][1]["state"], "pending")

    def test_yearly_schedule_does_not_mark_empty_quarter_missed(self):
        result = maintenance_schedule_status([], 2026, None, "yearly", 2,
                                             date(2027, 4, 1))
        self.assertEqual(result["status"], "Pending")
        self.assertEqual(result["quarter_progress"][0]["state"], "neutral")

    def test_quarterly_progress_does_not_transfer_extra_visits(self):
        history = [{"maintenance_date": date(2026, 7, day)} for day in (1, 2, 3)]
        result = maintenance_schedule_status(history, 2026, None, "quarterly", 1,
                                             date(2027, 7, 1))
        self.assertEqual(result["visits_in_period"], 3)
        self.assertEqual(result["visits_toward_target"], 1)
        self.assertEqual(result["target_for_period"], 4)
        self.assertEqual(result["progress_percent"], 25)

    def test_selected_quarter_progress_uses_only_that_quarter(self):
        history = [{"maintenance_date": date(2026, 7, 1)},
                   {"maintenance_date": date(2026, 10, 1)}]
        result = maintenance_schedule_status(history, 2026, 2, "quarterly", 2,
                                             date(2026, 12, 31))
        self.assertEqual(result["progress_percent"], 50)
        self.assertEqual(result["visits_toward_target"], 1)

    def test_yearly_progress_is_capped_at_target(self):
        history = [{"maintenance_date": date(2026, 7, day)} for day in (1, 2, 3)]
        result = maintenance_schedule_status(history, 2026, None, "yearly", 2,
                                             date(2026, 9, 30))
        self.assertEqual(result["progress_percent"], 100)
        self.assertEqual(result["visits_toward_target"], 2)

    def test_unconfigured_progress_has_no_percentage(self):
        result = maintenance_schedule_status([], 2026, None, None, None,
                                             date(2026, 9, 30))
        self.assertIsNone(result["progress_percent"])

if __name__ == "__main__":
    unittest.main()
