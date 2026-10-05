import csv
import io
import json
import math
import os
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from mysql.connector import Error as MySQLError, IntegrityError
from pydantic import BaseModel, Field, ValidationError
from typing import Literal

try:
    from .activity import ACTIVITY_SCHEMA, ActivityMiddleware, set_activity_actor, suspend_activity
    from .deleted_items import DELETED_ITEMS_SCHEMA, DELETED_SPECS, archive_deleted_item, download_deleted_item, get_deleted_item_history, restore_deleted_item
    from .database import get_connection
    from .security import (
        create_session_token,
        hash_password,
        hash_session_token,
        verify_password,
    )
except ImportError:
    from activity import ACTIVITY_SCHEMA, ActivityMiddleware, set_activity_actor, suspend_activity
    from deleted_items import DELETED_ITEMS_SCHEMA, DELETED_SPECS, archive_deleted_item, download_deleted_item, get_deleted_item_history, restore_deleted_item
    from database import get_connection
    from security import (
        create_session_token,
        hash_password,
        hash_session_token,
        verify_password,
    )


app = FastAPI(title="Station Operations", version="1.0.0")
app.add_middleware(ActivityMiddleware)
bearer_scheme = HTTPBearer(auto_error=False)
cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "*").split(",")
    if origin.strip()
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.mount(
    "/app",
    StaticFiles(
        directory=Path(__file__).resolve().parent.parent / "frontend",
        html=True,
    ),
    name="frontend",
)


DEFAULT_STATION_CATEGORIES = (
    "Automatic Rain Gauge",
    "Automatic Weather station",
    "Climatic Station",
    "Principal Station",
    "Rainfall Station",
    "Upper Air Station",
    "Weather radar",
)

LEGACY_STATION_CATEGORIES = {
    "Automatic Raingauge": "Automatic Rain Gauge",
    "Automatic Weather stations": "Automatic Weather station",
    "Climatic stations": "Climatic Station",
    "Principal stations": "Principal Station",
    "Rainfall station": "Rainfall Station",
    "Upper air station": "Upper Air Station",
}


def normalize_station_category(value):
    key = " ".join(value.split()).casefold()
    canonical = {name.casefold(): name for name in DEFAULT_STATION_CATEGORIES}
    canonical.update({old.casefold(): new for old, new in LEGACY_STATION_CATEGORIES.items()})
    canonical.update({
        "automatic rain gauges": "Automatic Rain Gauge",
        "automatic weather stations": "Automatic Weather station",
        "climatic station": "Climatic Station",
        "principal stations": "Principal Station",
        "rainfall stations": "Rainfall Station",
        "upper air stations": "Upper Air Station",
        "weather radars": "Weather radar",
    })
    return canonical.get(key, value.strip())


def migrate_station_categories(cursor):
    cursor.execute("SELECT 1 FROM app_migration_markers WHERE migration_key = 'station_categories_v2'")
    if cursor.fetchone():
        return

    for old, new in LEGACY_STATION_CATEGORIES.items():
        if old.casefold() == new.casefold():
            continue
        for table, key in (
            ("category_maintenance_targets", "fiscal_start_year"),
            ("monthly_category_data_counts", "record_month"),
        ):
            cursor.execute(
                f"SELECT 1 FROM `{table}` old JOIN `{table}` current "
                f"ON old.`{key}` = current.`{key}` AND BINARY current.station_category = BINARY %s "
                "WHERE BINARY old.station_category = BINARY %s LIMIT 1",
                (new, old),
            )
            if cursor.fetchone():
                raise ValueError(f"Cannot merge duplicate {table} records for {old} and {new}")

    cursor.execute("SELECT data_count_id, record_month, category_key FROM monthly_combined_data_counts")
    combined = cursor.fetchall()
    normalized_keys = {}
    for data_count_id, record_month, category_key in combined:
        normalized = "|".join(sorted({normalize_station_category(value) for value in category_key.split("|")}))
        key = (record_month, normalized)
        if key in normalized_keys:
            raise ValueError(f"Cannot merge duplicate combined data counts for {record_month}: {normalized}")
        normalized_keys[key] = data_count_id

    for old, new in LEGACY_STATION_CATEGORIES.items():
        cursor.execute("UPDATE stations SET station_category = %s WHERE BINARY station_category = BINARY %s", (new, old))
        if old.casefold() == new.casefold():
            cursor.execute(
                "UPDATE instrument_station_categories SET station_category = %s "
                "WHERE BINARY station_category = BINARY %s",
                (new, old),
            )
        else:
            cursor.execute(
                "INSERT IGNORE INTO instrument_station_categories (instrument_id, station_category) "
                "SELECT instrument_id, %s FROM instrument_station_categories "
                "WHERE BINARY station_category = BINARY %s",
                (new, old),
            )
            cursor.execute(
                "DELETE FROM instrument_station_categories WHERE BINARY station_category = BINARY %s",
                (old,),
            )
        for table in ("category_maintenance_targets", "monthly_category_data_counts"):
            cursor.execute(
                f"UPDATE `{table}` SET station_category = %s WHERE BINARY station_category = BINARY %s",
                (new, old),
            )

    for (record_month, normalized), data_count_id in normalized_keys.items():
        cursor.execute(
            "UPDATE monthly_combined_data_counts SET category_key = %s WHERE data_count_id = %s "
            "AND category_key <> %s",
            (normalized, data_count_id, normalized),
        )
    cursor.execute("INSERT INTO app_migration_markers (migration_key) VALUES ('station_categories_v2')")


def validate_station_categories(cursor, categories):
    names = list(dict.fromkeys(categories))
    if not names or any(not name or len(name) > 60 or "|" in name for name in names):
        raise HTTPException(status_code=400, detail="Invalid station category")
    cursor.execute(
        f"SELECT name FROM station_categories WHERE name IN ({', '.join(['%s'] * len(names))})",
        tuple(names),
    )
    available = {row["name"] if isinstance(row, dict) else row[0] for row in cursor.fetchall()}
    missing = [name for name in names if name not in available]
    if missing:
        raise HTTPException(status_code=400, detail="Unknown station category: " + ", ".join(missing))


def filter_values(value):
    if value is None or value == "":
        return []
    return [part.strip() for part in str(value).split("|") if part.strip()]


def add_filter_condition(conditions, parameters, column, value):
    values = filter_values(value)
    if values:
        conditions.append(f"{column} IN ({', '.join(['%s'] * len(values))})")
        parameters.extend(values)


def matches_filter(value, selected):
    values = filter_values(selected)
    return not values or str(value) in values

StationStatus = Literal[
    "Operational",
    "Under maintenance",
    "Suspended",
    "Closed",
]

UserRole = Literal[
    "Admin",
    "Observation Officer",
    "Observation Supervisor",
    "Observation Supervisor at HQ",
    "Data Quality Control Officer",
    "Observation Processing Officer",
    "Big Data Specialist",
    "Data Quality Control Specialist",
    "Division Manager",
    "Instrument Maintenance and Calibration Officer",
]

ASSIGNED_STATION_ROLES = {
    "Observation Officer",
    "Observation Supervisor",
}
DATA_QUALITY_ACTION_ROLES = {
    "Data Quality Control Officer",
    "Observation Processing Officer",
    "Observation Supervisor at HQ",
    *ASSIGNED_STATION_ROLES,
}
READ_ONLY_ALL_ROLES = {
    "Big Data Specialist",
    "Data Quality Control Specialist",
    "Division Manager",
}
MAINTENANCE_ROLE = "Instrument Maintenance and Calibration Officer"
DATA_OPERATIONS_ROLES = {
    "Data Quality Control Officer",
    "Observation Processing Officer",
}
QC_FILE_UPLOAD_ROLES = {
    *DATA_OPERATIONS_ROLES,
    "Observation Supervisor at HQ",
}
VOLUNTEER_DATA_ROLES = {
    *QC_FILE_UPLOAD_ROLES,
    *READ_ONLY_ALL_ROLES,
}
REPORTING_VIEW_ROLES = {
    *DATA_OPERATIONS_ROLES,
    *READ_ONLY_ALL_ROLES,
    "Observation Supervisor at HQ",
}
INSPECTION_HQ_ROLES = {"Observation Supervisor at HQ", "Admin"}
INSPECTION_MANAGEMENT_ROLES = {*READ_ONLY_ALL_ROLES, "Admin"}
INSPECTION_MAINTENANCE_ROLES = {MAINTENANCE_ROLE, "Admin"}


class Station(BaseModel):

    station_code: str = Field(min_length=1, max_length=50)
    station_name: str = Field(min_length=1, max_length=100)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude: float
    province: str = Field(min_length=1, max_length=100)
    district: str = Field(min_length=1, max_length=100)
    sector: str = Field(min_length=1, max_length=100)
    station_category: str = Field(min_length=1, max_length=60)
    status: StationStatus
    comment: str | None = Field(default=None, max_length=2000)
    action: str | None = Field(default=None, max_length=2000)


class MaintenanceInstrumentDetail(BaseModel):
    instrument_id: int = Field(gt=0)
    issue: str = Field(min_length=1, max_length=2000)
    action_done: str = Field(min_length=1, max_length=2000)
    recommendation: str | None = Field(default=None, max_length=2000)


class MaintenanceRecord(BaseModel):

    station_id: int = Field(gt=0)
    maintenance_date: date
    issue: str | None = Field(default=None, max_length=2000)
    activity_done: str | None = Field(default=None, max_length=2000)
    recommendations: str | None = Field(default=None, max_length=2000)
    technicians: str = Field(min_length=1, max_length=255)
    instrument_ids: list[int] = Field(default_factory=list)
    instrument_details: list[MaintenanceInstrumentDetail] = Field(default_factory=list)


def maintenance_instrument_payload(record):
    selected_ids = list(dict.fromkeys(record.instrument_ids))
    if record.instrument_details:
        details = record.instrument_details
        detail_ids = [item.instrument_id for item in details]
        if len(detail_ids) != len(set(detail_ids)):
            raise HTTPException(status_code=400, detail="Each instrument can appear only once")
        if selected_ids and set(selected_ids) != set(detail_ids):
            raise HTTPException(status_code=400, detail="Instrument details must match selected instruments")
        selected_ids = detail_ids
    else:
        if not record.issue or not record.issue.strip() or not record.activity_done or not record.activity_done.strip():
            raise HTTPException(status_code=400, detail="Enter an issue and action for each selected instrument")
        details = [MaintenanceInstrumentDetail(
            instrument_id=instrument_id, issue=record.issue,
            action_done=record.activity_done, recommendation=record.recommendations)
            for instrument_id in selected_ids]
    if not selected_ids:
        raise HTTPException(status_code=400, detail="Select at least one instrument")
    rows = []
    for item in details:
        issue, action = item.issue.strip(), item.action_done.strip()
        if not issue or not action:
            raise HTTPException(status_code=400, detail="Each instrument needs an issue and action")
        rows.append((item.instrument_id, issue, action,
                     item.recommendation.strip() if item.recommendation else None))
    def summary(index):
        return "; ".join(value[index] for value in rows if value[index])[:2000] or None
    return selected_ids, rows, summary(1), summary(2), summary(3)


class Instrument(BaseModel):

    instrument_name: str = Field(min_length=1, max_length=100)
    parameters_taken: str = Field(min_length=1, max_length=1000)
    category: str = Field(min_length=1, max_length=100)
    station_categories: list[str] = Field(min_length=1)
    description: str | None = Field(default=None, max_length=1000)
    is_active: bool = True


class SuspectedDataCreate(BaseModel):

    station_id: int = Field(gt=0)
    issue: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=2000)


class SuspectedDataUpdate(BaseModel):

    station_id: int = Field(gt=0)
    issue: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=2000)
    maintenance_date: date | None = None
    maintenance_issue: str | None = Field(default=None, max_length=2000)
    how_solved: str | None = Field(default=None, max_length=2000)
    maintenance_outcome: Literal[
        "Under Maintenance",
        "Solved",
        "Not Solved",
        "Not Maintained",
    ]
    way_forward: str | None = Field(default=None, max_length=2000)
    status: Literal["Open", "Under Review", "Resolved"]


class FinalDataReview(BaseModel):

    issue_solved: bool
    comment: str = Field(min_length=1, max_length=2000)


class VolunteerReportComment(BaseModel):

    report_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    comment: str = Field(min_length=1, max_length=2000)


class MonthlyReportingStatus(BaseModel):

    report_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    reported_stations: int = Field(ge=0)
    notes: str | None = Field(default=None, max_length=2000)


class MonthlyDataRequest(BaseModel):

    request_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    category: str = Field(min_length=1, max_length=100)
    served_requests: int = Field(ge=0)
    notes: str | None = Field(default=None, max_length=1000)


class DataRequestCategory(BaseModel):

    category: str = Field(min_length=1, max_length=100)
    served_requests: int = Field(ge=0)
    notes: str | None = Field(default=None, max_length=1000)


class MonthlyDataRequestBatch(BaseModel):

    request_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    categories: list[DataRequestCategory] = Field(min_length=1, max_length=50)


class StationInstrument(BaseModel):

    station_id: int = Field(gt=0)
    instrument_id: int = Field(gt=0)
    model: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=100)
    serial_number: str | None = Field(default=None, max_length=100)
    installation_date: date
    calibration_date: date | None = None
    replacement_date: date | None = None
    recommended_calibration_date: date | None = None
    recommended_replacement_date: date | None = None
    status: Literal[
        "Operational",
        "Needs Calibration",
        "Needs Replacement",
        "Under Maintenance",
        "Inactive",
    ]
    comment: str | None = Field(default=None, max_length=2000)
    data_logger_ports: str | None = Field(default=None, max_length=255)
    algorithm: str | None = Field(default=None, max_length=2000)
    wiring_colors: list[str | None] = Field(default_factory=list, max_length=10)


class MonthlyDataCount(BaseModel):
    station_category: str = Field(min_length=1, max_length=60)
    record_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    record_count: int = Field(ge=0)
    notes: str | None = Field(default=None, max_length=1000)


class CombinedDataCount(BaseModel):
    record_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    station_categories: list[str] = Field(min_length=2)
    record_count: int = Field(ge=0)
    notes: str | None = Field(default=None, max_length=1000)


class MaintenanceFrequency(BaseModel):
    station_category: str = Field(min_length=1, max_length=60)
    fiscal_start_year: int = Field(ge=2020, le=2100)
    cadence: Literal["quarterly", "yearly"]
    target_visits: int = Field(ge=1, le=100)


class VisitorCategory(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class StationCategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class StationVisitor(BaseModel):
    station_id: int = Field(gt=0)
    visit_date: date
    institution: str = Field(min_length=1, max_length=200)
    mission: str = Field(min_length=1, max_length=1000)
    category_id: int = Field(gt=0)
    visitor_count: int = Field(ge=1)


class StationVolunteer(BaseModel):
    station_id: int = Field(gt=0)
    volunteer_name: str = Field(min_length=1, max_length=150)
    volunteer_identifier: str = Field(min_length=1, max_length=50)
    account_type: str = Field(min_length=1, max_length=100)
    account_name: str = Field(min_length=1, max_length=150)
    account_number: str = Field(min_length=1, max_length=100)
    mobile_phone: str = Field(min_length=1, max_length=50)


def fiscal_period(start_year, quarter=None):
    if quarter not in (None, 1, 2, 3, 4):
        raise ValueError("Quarter must be Q1, Q2, Q3 or Q4")
    starts = [(start_year, 7), (start_year, 10), (start_year + 1, 1), (start_year + 1, 4)]
    ends = [(start_year, 9, 30), (start_year, 12, 31), (start_year + 1, 3, 31), (start_year + 1, 6, 30)]
    if quarter is None:
        return date(start_year, 7, 1), date(start_year + 1, 6, 30)
    year, month = starts[quarter - 1]
    return date(year, month, 1), date(*ends[quarter - 1])


def fiscal_totals_by_month(monthly_totals):
    by_fiscal_year, by_fiscal_quarter = {}, {}
    for month, count in monthly_totals.items():
        year, month_number = map(int, month.split("-"))
        fiscal_start = year if month_number >= 7 else year - 1
        fiscal_year = f"{fiscal_start}/{fiscal_start + 1}"
        quarter = ((month_number - 7) % 12) // 3 + 1
        by_fiscal_year[fiscal_year] = by_fiscal_year.get(fiscal_year, 0) + count
        quarter_key = f"{fiscal_year} Q{quarter}"
        by_fiscal_quarter[quarter_key] = by_fiscal_quarter.get(quarter_key, 0) + count
    return dict(sorted(by_fiscal_year.items())), dict(sorted(by_fiscal_quarter.items()))


def reporting_percentage(operational, expected):
    if operational is None or expected is None:
        return None
    return round(operational * 100 / expected, 1) if expected else 0


def instrument_due_alert(due_date, today):
    if not due_date:
        return {"level": "none", "label": "Not scheduled", "months_remaining": None}
    if due_date < today:
        return {"level": "overdue", "label": f"Overdue by {(today - due_date).days} days", "months_remaining": -1}
    months = (due_date.year - today.year) * 12 + due_date.month - today.month
    if months > 6 or (months == 6 and due_date.day > today.day):
        return {"level": "none", "label": "Scheduled", "months_remaining": months}
    if months == 0:
        label = f"Due in {(due_date - today).days} days" if due_date > today else "Due today"
    else:
        label = f"Due in {months} month{'s' if months != 1 else ''}"
    return {"level": "warning", "label": label, "months_remaining": months}


def fiscal_maintenance_coverage(history, fiscal_start_year, quarter, cadence, target, today):
    period_start, period_end = fiscal_period(fiscal_start_year, quarter)
    report_ids = {row["report_id"] for row in history
                  if period_start <= row["period_end"] <= period_end}
    last_date = max((row["period_end"] for row in history), default=None)
    if not target:
        return {"coverage": "Not configured", "reports_in_period": len(report_ids),
                "target_for_period": None, "last_maintenance_date": last_date}
    if cadence == "yearly":
        fy_start, fy_end = fiscal_period(fiscal_start_year)
        progress_end = period_end if quarter else fy_end
        completed = len({row["report_id"] for row in history
                         if fy_start <= row["period_end"] <= progress_end})
        status = "Maintained" if completed >= target else (
            "Not maintained" if quarter is None and today > fy_end else "Pending")
        return {"coverage": status, "reports_in_period": len(report_ids),
                "target_for_period": target, "last_maintenance_date": last_date}
    quarters = [quarter] if quarter else [1, 2, 3, 4]
    overdue = False
    all_met = True
    for number in quarters:
        start, end = fiscal_period(fiscal_start_year, number)
        visits = len({row["report_id"] for row in history if start <= row["period_end"] <= end})
        if visits < target:
            all_met = False
            if today > end:
                overdue = True
    status = "Maintained" if all_met else ("Not maintained" if overdue else "Pending")
    return {"coverage": status, "reports_in_period": len(report_ids),
            "target_for_period": target * len(quarters), "last_maintenance_date": last_date}


def maintenance_schedule_status(history, fiscal_start_year, quarter, cadence, target, today):
    period_start, period_end = fiscal_period(fiscal_start_year, quarter)
    completed_dates = sorted({row["maintenance_date"] for row in history
                              if row["maintenance_date"] <= today})
    period_records = [row for row in history
                      if period_start <= row["maintenance_date"] <= min(period_end, today)]
    quarter_progress = []
    for number in (1, 2, 3, 4):
        start, end = fiscal_period(fiscal_start_year, number)
        count = sum(start <= row["maintenance_date"] <= min(end, today) for row in history)
        if not target or cadence not in {"quarterly", "yearly"}:
            state = "unconfigured"
        elif cadence == "yearly":
            state = "good" if count else ("pending" if today <= end else "neutral")
        else:
            state = "good" if count >= target else ("missed" if today > end else "pending")
        quarter_progress.append({"quarter": number, "count": count if today >= start else None,
                                 "state": state, "target": target if cadence == "quarterly" else None})
    result = {
        "status": "Not configured", "status_detail": "Set a maintenance frequency for this category",
        "visits_in_period": len(period_records), "target_for_period": None,
        "visits_toward_target": None, "progress_percent": None,
        "last_maintenance_date": completed_dates[-1] if completed_dates else None,
        "maintenance_dates": [row["maintenance_date"] for row in period_records],
        "quarter_progress": quarter_progress,
    }
    if not target or cadence not in {"quarterly", "yearly"}:
        return result
    if cadence == "yearly":
        year_start, year_end = fiscal_period(fiscal_start_year)
        completed = sum(year_start <= row["maintenance_date"] <= min(year_end, today)
                        for row in history)
        result["target_for_period"] = target
        result["visits_toward_target"] = min(completed, target)
        result["progress_percent"] = round(100 * min(completed, target) / target)
        if completed >= target:
            result.update(status="Maintained", status_detail=f"Annual target met ({completed}/{target})")
        elif today > year_end:
            result.update(status="Not maintained", status_detail=f"Annual target missed ({completed}/{target})")
        else:
            result.update(status="Pending", status_detail=f"Annual target in progress ({completed}/{target})")
        return result

    quarters = [quarter] if quarter else [1, 2, 3, 4]
    result["target_for_period"] = target * len(quarters)
    fulfilled = sum(min(quarter_progress[number - 1]["count"] or 0, target)
                    for number in quarters)
    result["visits_toward_target"] = fulfilled
    result["progress_percent"] = round(100 * fulfilled / result["target_for_period"])
    missed, awaiting = [], []
    for number in quarters:
        start, end = fiscal_period(fiscal_start_year, number)
        completed = sum(start <= row["maintenance_date"] <= min(end, today) for row in history)
        if completed < target:
            (missed if today > end else awaiting).append(f"Q{number} ({completed}/{target})")
    if missed:
        result.update(status="Not maintained", status_detail="Quarterly deadline missed")
    elif awaiting:
        result.update(status="Pending", status_detail="Quarterly target still due")
    else:
        result.update(status="Maintained", status_detail="Quarterly target met")
    return result


def effective_instrument_status(status, calibration_due, replacement_due, today):
    if status != "Operational":
        return status
    if replacement_due and replacement_due <= today:
        return "Needs Replacement"
    if calibration_due and calibration_due <= today:
        return "Needs Calibration"
    return status


def maintenance_coverage(history, period_start, period_end, frequency_days):
    overlapping = [report for report in history if report["period_start"] <= period_end
                   and report["period_end"] >= period_start]
    prior = [report["period_end"] for report in history if report["period_end"] < period_start]
    last_prior = max(prior) if prior else None
    next_due = last_prior + timedelta(days=frequency_days) if last_prior else None
    return {
        "reports_in_period": len(overlapping),
        "last_maintenance_date": max((report["period_end"] for report in history), default=None),
        "next_due_date": next_due,
        "coverage": "Maintained" if overlapping else "Not maintained",
        "pending": not overlapping and (next_due is None or next_due <= period_end),
    }


class StationInspectionRecord(BaseModel):

    station_id: int = Field(gt=0)
    inspection_date: date
    finding: str = Field(min_length=1, max_length=5000)


class StationInspectionAction(BaseModel):

    action: Literal[
        "comment",
        "send_management",
        "send_maintenance",
        "close",
        "status",
    ]
    comment: str | None = Field(default=None, max_length=5000)
    status: Literal["Open", "Under Review", "Solved", "Not Solved"] | None = None
    reason: str | None = Field(default=None, max_length=5000)


class DiscussionCreate(BaseModel):

    title: str = Field(min_length=3, max_length=200)
    opening_message: str = Field(min_length=1, max_length=10000)
    participant_user_ids: list[int] = Field(min_length=1, max_length=100)


class DiscussionMessageCreate(BaseModel):

    message: str = Field(min_length=1, max_length=10000)


class LoginRequest(BaseModel):

    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)


class PasswordChange(BaseModel):

    new_password: str = Field(min_length=8, max_length=128)


class UserCreate(BaseModel):

    full_name: str = Field(min_length=2, max_length=100)
    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    email: str | None = Field(default=None, max_length=150)
    department: UserRole
    password: str = Field(min_length=8, max_length=128)
    station_ids: list[int] = Field(default_factory=list)


class UserUpdate(BaseModel):

    full_name: str = Field(min_length=2, max_length=100)
    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    email: str | None = Field(default=None, max_length=150)
    department: UserRole
    is_active: bool = True
    password: str | None = Field(default=None, min_length=8, max_length=128)
    station_ids: list[int] = Field(default_factory=list)


class BootstrapUser(BaseModel):

    full_name: str = Field(min_length=2, max_length=100)
    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    email: str | None = Field(default=None, max_length=150)
    password: str = Field(min_length=8, max_length=128)


def ensure_column(cursor, table_name, column_name, definition):
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = %s
            AND COLUMN_NAME = %s
        """,
        (table_name, column_name),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {definition}"
        )


def ensure_station_status_enum(cursor):
    desired_type = "enum('Operational','Under maintenance','Suspended','Closed')"
    cursor.execute(
        """SELECT COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'stations'
            AND COLUMN_NAME = 'status'"""
    )
    column_type, nullable = cursor.fetchone()
    if column_type == desired_type and nullable == "NO":
        return
    cursor.execute(
        """SELECT DISTINCT status FROM stations
        WHERE status IS NULL OR status NOT IN
            ('Operational', 'Under maintenance', 'Suspended', 'Closed')"""
    )
    invalid = [row[0] for row in cursor.fetchall()]
    if invalid:
        raise RuntimeError(
            f"Cannot constrain stations.status; classify legacy values first: {invalid}"
        )
    cursor.execute(
        """ALTER TABLE stations MODIFY COLUMN status
            ENUM('Operational', 'Under maintenance', 'Suspended', 'Closed') NOT NULL"""
    )


def ensure_foreign_key(cursor, table_name, constraint_name, definition):
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.TABLE_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA = DATABASE()
            AND TABLE_NAME = %s
            AND CONSTRAINT_NAME = %s
        """,
        (table_name, constraint_name),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            f"ALTER TABLE `{table_name}` "
            f"ADD CONSTRAINT `{constraint_name}` {definition}"
        )


def ensure_unique_index(cursor, table_name, index_name, column_name):
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = %s
            AND INDEX_NAME = %s
        """,
        (table_name, index_name),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            f"CREATE UNIQUE INDEX `{index_name}` "
            f"ON `{table_name}` (`{column_name}`)"
        )


def filter_sort_collection(
    items,
    search,
    search_fields,
    sort_by,
    sort_order,
    sort_fields,
    date_from=None,
    date_to=None,
    date_field=None,
):
    filtered = list(items)
    if search:
        term = search.strip().casefold()
        filtered = [
            item for item in filtered
            if any(term in str(item.get(field) or "").casefold() for field in search_fields)
        ]
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="Start date must not be after end date")
    if date_field and (date_from or date_to):
        def in_range(item):
            value = item.get(date_field)
            if value is None:
                return False
            item_date = value.date() if isinstance(value, datetime) else value
            return (not date_from or item_date >= date_from) and (
                not date_to or item_date <= date_to
            )
        filtered = [item for item in filtered if in_range(item)]
    column = sort_fields.get(sort_by) or next(iter(sort_fields.values()))
    present = [item for item in filtered if item.get(column) is not None]
    missing = [item for item in filtered if item.get(column) is None]
    present.sort(
        key=lambda item: item[column].casefold() if isinstance(item[column], str) else item[column],
        reverse=sort_order.lower() == "desc",
    )
    return present + missing


def paginate_collection(items, page, page_size, summary=None):
    if page is None:
        return items
    total = len(items)
    pages = max(1, math.ceil(total / page_size))
    page = min(page, pages)
    offset = (page - 1) * page_size
    return {
        "items": items[offset:offset + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "summary": summary or {},
    }


def csv_download(filename, headers, rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    content = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def pdf_download(filename, title, headers, rows, generated_by):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="PDF support is not installed") from exc
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph(title, styles["Title"]),
        Paragraph(
            f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by {generated_by}",
            styles["Normal"],
        ),
        Spacer(1, 12),
    ]
    table_data = [headers] + [
        ["" if value is None else str(value) for value in row] for row in rows
    ]
    table = Table(table_data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173f63")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#b7c4cf")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f6f8")]),
    ]))
    story.append(table)
    document.build(story)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def replace_instrument_station_categories(cursor, instrument_id, categories):
    cursor.execute(
        "DELETE FROM instrument_station_categories WHERE instrument_id = %s",
        (instrument_id,),
    )
    cursor.executemany(
        """
        INSERT INTO instrument_station_categories (instrument_id, station_category)
        VALUES (%s, %s)
        """,
        [(instrument_id, category) for category in dict.fromkeys(categories)],
    )


def ensure_instrument_matches_station_category(cursor, station_id, instrument_id):
    cursor.execute(
        "SELECT station_category FROM stations WHERE station_id = %s",
        (station_id,),
    )
    station = cursor.fetchone()
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")
    if not station[0]:
        raise HTTPException(
            status_code=409,
            detail="Classify the station before assigning instruments",
        )
    cursor.execute(
        "SELECT instrument_id, category FROM instruments WHERE instrument_id = %s",
        (instrument_id,),
    )
    instrument = cursor.fetchone()
    if instrument is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    cursor.execute(
        """
        SELECT instrument_id
        FROM instrument_station_categories
        WHERE instrument_id = %s AND station_category = %s
        """,
        (instrument_id, station[0]),
    )
    if cursor.fetchone() is None:
        raise HTTPException(
            status_code=409,
            detail="The instrument is not assigned to this station category",
        )
    return instrument[1]


def station_instrument_connection_values(item, category):
    ports = item.data_logger_ports.strip() if item.data_logger_ports else None
    algorithm = item.algorithm.strip() if item.algorithm else None
    colors = [(color.strip() if color else None) for color in item.wiring_colors]
    if any(color and len(color) > 50 for color in colors):
        raise HTTPException(status_code=400, detail="Each wire color must be 50 characters or fewer")
    if category.strip().casefold() != "sensing":
        if ports or algorithm or any(colors):
            raise HTTPException(status_code=400,
                detail="Data logger ports, algorithm, and wiring apply only to Sensing instruments")
        return None, None, None
    return ports, algorithm, json.dumps((colors + [None] * 10)[:10]) if any(colors) else None


def station_instrument_csv_headers():
    return ["Station ID", "Instrument", "Model", "Manufacturer", "Serial number",
        "Installation date", "Calibration date", "Replacement date", "Recommended calibration",
        "Recommended replacement", "Status", "Comment", "Data logger ports", "Algorithm",
        *[f"Wire {number}" for number in range(1, 11)]]


def parse_station_instrument_csv(content):
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV file has no header row")
    normalized = ["_".join((name or "").strip().lower().split()) for name in reader.fieldnames]
    if len(normalized) != len(set(normalized)):
        raise HTTPException(status_code=400, detail="CSV contains duplicate columns")
    required = {"station_id", "instrument", "installation_date", "status"}
    if not required.issubset(normalized):
        raise HTTPException(status_code=400, detail="CSV needs Station ID, Instrument, Installation date, and Status columns")
    rows = []
    for row_number, raw in enumerate(reader, start=2):
        if None in raw:
            raise HTTPException(status_code=400, detail=f"CSV row {row_number}: too many columns")
        row = {"_".join((key or "").strip().lower().split()): (value or "").strip()
               for key, value in raw.items()}
        if not any(row.values()):
            continue
        if not row.get("station_id") or not row.get("instrument"):
            raise HTTPException(status_code=400, detail=f"CSV row {row_number}: enter Station ID and Instrument")
        rows.append((row_number, row))
        if len(rows) > 2000:
            raise HTTPException(status_code=400, detail="Import at most 2,000 rows at a time")
    if not rows:
        raise HTTPException(status_code=400, detail="CSV file has no instrument rows")
    return rows


def insert_station_instrument(cursor, item, user, category):
    ports, algorithm, wiring = station_instrument_connection_values(item, category)
    cursor.execute("""INSERT INTO station_instruments (
        station_id, instrument_id, model, manufacturer, serial_number,
        installation_date, calibration_date, replacement_date,
        recommended_calibration_date, recommended_replacement_date,
        status, comment, data_logger_ports, algorithm, wiring_colors,
        created_by_user_id, recorded_by_username
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (item.station_id, item.instrument_id,
         item.model.strip() if item.model else None,
         item.manufacturer.strip() if item.manufacturer else None,
         item.serial_number.strip() if item.serial_number else None,
         item.installation_date, item.calibration_date, item.replacement_date,
         item.recommended_calibration_date, item.recommended_replacement_date,
         item.status, item.comment.strip() if item.comment else None,
         ports, algorithm, wiring, user["user_id"], user["username"]))
    return cursor.lastrowid


def ensure_department_options(cursor):
    cursor.execute(
        """
        SELECT COLUMN_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = 'users'
            AND COLUMN_NAME = 'department'
        """
    )
    row = cursor.fetchone()
    desired_roles = [
        "Admin",
        "Observation Officer",
        "Observation Supervisor",
        "Observation Supervisor at HQ",
        "Data Quality Control Officer",
        "Observation Processing Officer",
        "Big Data Specialist",
        "Data Quality Control Specialist",
        "Division Manager",
        MAINTENANCE_ROLE,
    ]
    if row and any(f"'{role}'" not in row[0] for role in desired_roles):
        cursor.execute(
            """
            ALTER TABLE users
            MODIFY COLUMN department
                ENUM(
                    'IT', 'Data', 'Quality Control', 'Maintenance',
                    'Admin', 'Data Quality Control', 'Observation Officer',
                    'Observation Supervisor', 'Observation Supervisor at HQ',
                    'Data Quality Control Officer',
                    'Observation Processing Officer', 'Big Data Specialist',
                    'Data Quality Control Specialist', 'Division Manager',
                    'Instrument Maintenance and Calibration Officer'
                ) NOT NULL
            """
        )
        cursor.execute("UPDATE users SET department = 'Admin' WHERE department = 'IT'")
        cursor.execute(
            "UPDATE users SET department = 'Data Quality Control Officer' "
            "WHERE department IN ('Data', 'Quality Control')"
        )
        cursor.execute(
            "UPDATE users SET department = 'Data Quality Control Officer' "
            "WHERE department = 'Data Quality Control'"
        )
        cursor.execute(
            "UPDATE users SET department = %s WHERE department = 'Maintenance'",
            (MAINTENANCE_ROLE,),
        )
        cursor.execute(
            """
            ALTER TABLE users
            MODIFY COLUMN department ENUM(
                'Admin', 'Observation Officer', 'Observation Supervisor',
                'Observation Supervisor at HQ',
                'Data Quality Control Officer', 'Observation Processing Officer',
                'Big Data Specialist', 'Data Quality Control Specialist',
                'Division Manager',
                'Instrument Maintenance and Calibration Officer'
            ) NOT NULL
            """
        )


@suspend_activity
def ensure_application_tables(connection):

    cursor = connection.cursor()

    try:
        cursor.execute(ACTIVITY_SCHEMA)
        cursor.execute(DELETED_ITEMS_SCHEMA)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INT AUTO_INCREMENT PRIMARY KEY,
                full_name VARCHAR(100) NOT NULL,
                username VARCHAR(50) NOT NULL UNIQUE,
                email VARCHAR(150),
                department ENUM(
                    'Admin', 'Observation Officer', 'Observation Supervisor',
                    'Observation Supervisor at HQ',
                    'Data Quality Control Officer', 'Observation Processing Officer',
                    'Big Data Specialist', 'Data Quality Control Specialist',
                    'Division Manager',
                    'Instrument Maintenance and Calibration Officer'
                ) NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute("""CREATE TABLE IF NOT EXISTS record_edit_history (
            history_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            entity_type VARCHAR(50) NOT NULL,
            entity_id VARCHAR(100) NOT NULL,
            before_data JSON NOT NULL,
            after_data JSON NOT NULL,
            changed_by_username VARCHAR(50) NOT NULL,
            edit_reason VARCHAR(500) NULL,
            changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_record_edit_history (entity_type, entity_id, changed_at)
        )""")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                session_id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                token_hash CHAR(64) NOT NULL UNIQUE,
                expires_at DATETIME NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_session_user
                    FOREIGN KEY (user_id)
                    REFERENCES users(user_id)
                    ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                notification_id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                notification_type VARCHAR(50) NOT NULL,
                title VARCHAR(150) NOT NULL,
                message VARCHAR(500) NOT NULL,
                related_record_type VARCHAR(50),
                related_record_id INT,
                is_read BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_notification_user FOREIGN KEY (user_id)
                    REFERENCES users(user_id) ON DELETE CASCADE,
                INDEX idx_notification_user_read (user_id, is_read, created_at)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS volunteer_data_files (
                file_id INT AUTO_INCREMENT PRIMARY KEY,
                report_month DATE NOT NULL,
                file_kind ENUM('monthly_qc', 'filtered_data', 'filled_data') NOT NULL,
                original_filename VARCHAR(255) NOT NULL,
                content_type VARCHAR(120) NOT NULL,
                file_size INT NOT NULL,
                file_data LONGBLOB NOT NULL,
                uploaded_by_user_id INT,
                uploaded_by_username VARCHAR(50),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_volunteer_month_kind (report_month, file_kind),
                CONSTRAINT fk_volunteer_file_user
                    FOREIGN KEY (uploaded_by_user_id) REFERENCES users(user_id)
                    ON DELETE SET NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS volunteer_report_comments (
                comment_id INT AUTO_INCREMENT PRIMARY KEY,
                report_month DATE NOT NULL,
                comment TEXT NOT NULL,
                commented_by_user_id INT,
                commented_by_username VARCHAR(50),
                commented_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_volunteer_comment_user
                    FOREIGN KEY (commented_by_user_id) REFERENCES users(user_id)
                    ON DELETE SET NULL,
                INDEX idx_volunteer_comment_month (report_month, commented_at)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS monthly_reporting_status (
                reporting_status_id INT AUTO_INCREMENT PRIMARY KEY,
                report_month DATE NOT NULL UNIQUE,
                expected_stations INT NOT NULL,
                operational_stations INT NULL,
                under_maintenance_stations INT NULL,
                suspended_stations INT NULL,
                reported_stations INT NOT NULL,
                validated_reports INT NOT NULL,
                notes TEXT,
                recorded_by_user_id INT,
                recorded_by_username VARCHAR(50),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                CONSTRAINT fk_reporting_status_user
                    FOREIGN KEY (recorded_by_user_id) REFERENCES users(user_id)
                    ON DELETE SET NULL
            )
            """
        )
        cursor.execute("""CREATE TABLE IF NOT EXISTS monthly_reporting_changes (
            change_id INT AUTO_INCREMENT PRIMARY KEY,
            reporting_status_id INT NULL,
            report_month DATE NOT NULL,
            action ENUM('Created', 'Updated', 'Deleted', 'File uploaded') NOT NULL,
            before_data JSON NULL,
            after_data JSON NULL,
            changed_by_user_id INT NULL,
            changed_by_username VARCHAR(50) NOT NULL,
            edit_reason VARCHAR(500) NULL,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_reporting_change_time (changed_at, change_id),
            INDEX idx_reporting_change_month (report_month, change_id),
            FOREIGN KEY (changed_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
        )""")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS monthly_data_requests (
                data_request_id INT AUTO_INCREMENT PRIMARY KEY,
                request_month DATE NOT NULL,
                category VARCHAR(100) NOT NULL,
                served_requests INT NOT NULL,
                notes VARCHAR(1000),
                recorded_by_user_id INT,
                recorded_by_username VARCHAR(50),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_request_month_category (request_month, category),
                CONSTRAINT fk_data_request_user
                    FOREIGN KEY (recorded_by_user_id) REFERENCES users(user_id)
                    ON DELETE SET NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS monthly_non_reported_station_files (
                file_id INT AUTO_INCREMENT PRIMARY KEY,
                report_month DATE NOT NULL UNIQUE,
                original_filename VARCHAR(255) NOT NULL,
                content_type VARCHAR(120) NOT NULL,
                file_size INT NOT NULL,
                file_data LONGBLOB NOT NULL,
                uploaded_by_user_id INT,
                uploaded_by_username VARCHAR(50),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_non_reported_file_user
                    FOREIGN KEY (uploaded_by_user_id) REFERENCES users(user_id)
                    ON DELETE SET NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stations (
                station_id INT AUTO_INCREMENT PRIMARY KEY,
                station_code VARCHAR(50) NOT NULL UNIQUE,
                station_name VARCHAR(100) NOT NULL,
                latitude DECIMAL(9,6) NOT NULL,
                longitude DECIMAL(9,6) NOT NULL,
                altitude DECIMAL(10,2) NOT NULL,
                province VARCHAR(100) NOT NULL,
                district VARCHAR(100) NOT NULL,
                sector VARCHAR(100) NOT NULL,
                station_category VARCHAR(60) NOT NULL,
                status ENUM('Operational', 'Under maintenance', 'Suspended', 'Closed') NOT NULL,
                comment TEXT,
                action TEXT,
                created_by_user_id INT,
                recorded_by_username VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_station_creator
                    FOREIGN KEY (created_by_user_id)
                    REFERENCES users(user_id)
                    ON DELETE SET NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS maintenance_records (
                maintenance_id INT AUTO_INCREMENT PRIMARY KEY,
                station_id INT NOT NULL,
                maintenance_date DATE NOT NULL,
                issue TEXT NOT NULL,
                activity_done TEXT NOT NULL,
                recommendations TEXT,
                technicians VARCHAR(255) NOT NULL,
                created_by_user_id INT,
                recorded_by_username VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_maintenance_station
                    FOREIGN KEY (station_id)
                    REFERENCES stations(station_id)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT,
                CONSTRAINT fk_maintenance_creator
                    FOREIGN KEY (created_by_user_id)
                    REFERENCES users(user_id)
                    ON DELETE SET NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS maintenance_reports (
                report_id INT AUTO_INCREMENT PRIMARY KEY,
                period_start DATE NOT NULL,
                period_end DATE NOT NULL,
                notes VARCHAR(2000),
                original_filename VARCHAR(255) NOT NULL,
                content_type VARCHAR(100) NOT NULL,
                file_size INT NOT NULL,
                file_data LONGBLOB NOT NULL,
                uploaded_by_user_id INT,
                uploaded_by_username VARCHAR(50),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_maintenance_report_user
                    FOREIGN KEY (uploaded_by_user_id)
                    REFERENCES users(user_id)
                    ON DELETE SET NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS maintenance_report_stations (
                report_id INT NOT NULL,
                station_id INT NOT NULL,
                PRIMARY KEY (report_id, station_id),
                CONSTRAINT fk_maintenance_report_station_report
                    FOREIGN KEY (report_id)
                    REFERENCES maintenance_reports(report_id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_maintenance_report_station_station
                    FOREIGN KEY (station_id)
                    REFERENCES stations(station_id)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS pre_maintenance_reports (
                report_id INT AUTO_INCREMENT PRIMARY KEY,
                period_start DATE NOT NULL,
                period_end DATE NOT NULL,
                notes VARCHAR(2000),
                original_filename VARCHAR(255) NOT NULL,
                content_type VARCHAR(100) NOT NULL,
                file_size INT NOT NULL,
                file_data LONGBLOB NOT NULL,
                uploaded_by_user_id INT,
                uploaded_by_username VARCHAR(50),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_pre_maintenance_report_user
                    FOREIGN KEY (uploaded_by_user_id)
                    REFERENCES users(user_id)
                    ON DELETE SET NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS pre_maintenance_report_stations (
                report_id INT NOT NULL,
                station_id INT NOT NULL,
                PRIMARY KEY (report_id, station_id),
                CONSTRAINT fk_pre_maintenance_report_station_report
                    FOREIGN KEY (report_id)
                    REFERENCES pre_maintenance_reports(report_id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_pre_maintenance_report_station_station
                    FOREIGN KEY (station_id)
                    REFERENCES stations(station_id)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS station_maintenance_frequencies (
                station_id INT PRIMARY KEY,
                frequency_days INT NOT NULL DEFAULT 90,
                updated_by_user_id INT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (station_id) REFERENCES stations(station_id) ON DELETE CASCADE,
                FOREIGN KEY (updated_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS monthly_data_counts (
                data_count_id INT AUTO_INCREMENT PRIMARY KEY,
                station_id INT NOT NULL,
                record_month DATE NOT NULL,
                record_count INT NOT NULL,
                notes VARCHAR(1000) NULL,
                recorded_by_user_id INT NULL,
                recorded_by_username VARCHAR(50) NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_monthly_data_station_month (station_id, record_month),
                FOREIGN KEY (station_id) REFERENCES stations(station_id) ON DELETE RESTRICT,
                FOREIGN KEY (recorded_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
            )
            """
        )
        cursor.execute("""CREATE TABLE IF NOT EXISTS monthly_category_data_counts (
            data_count_id INT AUTO_INCREMENT PRIMARY KEY,
            station_category VARCHAR(60) NOT NULL,
            record_month DATE NOT NULL,
            record_count INT NOT NULL,
            notes VARCHAR(1000),
            recorded_by_user_id INT NULL,
            recorded_by_username VARCHAR(50),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_category_month (station_category, record_month),
            FOREIGN KEY (recorded_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
        )""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS monthly_combined_data_counts (
            data_count_id INT AUTO_INCREMENT PRIMARY KEY,
            category_key VARCHAR(500) NOT NULL,
            record_month DATE NOT NULL,
            record_count INT NOT NULL,
            notes VARCHAR(1000),
            recorded_by_user_id INT NULL,
            recorded_by_username VARCHAR(50),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_combined_month (record_month, category_key),
            FOREIGN KEY (recorded_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
        )""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS app_migration_markers (
            migration_key VARCHAR(100) PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        cursor.execute("SELECT 1 FROM app_migration_markers WHERE migration_key = 'category_data_counts_v1'")
        if not cursor.fetchone():
            cursor.execute("""INSERT INTO monthly_category_data_counts
                (station_category, record_month, record_count, notes)
                SELECT s.station_category, m.record_month, SUM(m.record_count),
                    'Aggregated from legacy station counts'
                FROM monthly_data_counts m JOIN stations s ON s.station_id = m.station_id
                GROUP BY s.station_category, m.record_month
                ON DUPLICATE KEY UPDATE record_count = monthly_category_data_counts.record_count""")
            cursor.execute("INSERT INTO app_migration_markers (migration_key) VALUES ('category_data_counts_v1')")
        cursor.execute("""CREATE TABLE IF NOT EXISTS category_maintenance_targets (
            station_category VARCHAR(60) NOT NULL,
            fiscal_start_year SMALLINT NOT NULL,
            cadence ENUM('quarterly', 'yearly') NOT NULL,
            target_visits INT NOT NULL,
            updated_by_user_id INT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (station_category, fiscal_start_year),
            FOREIGN KEY (updated_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
        )""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS station_categories (
            category_id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(60) NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS instrument_alert_deliveries (
            station_instrument_id INT NOT NULL,
            user_id INT NOT NULL,
            alert_kind VARCHAR(20) NOT NULL,
            alert_month DATE NOT NULL,
            PRIMARY KEY (station_instrument_id, user_id, alert_kind, alert_month),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS visitor_categories (
            category_id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS station_visitors (
            visitor_id INT AUTO_INCREMENT PRIMARY KEY,
            station_id INT NOT NULL,
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            institution VARCHAR(200) NOT NULL DEFAULT '',
            mission VARCHAR(1000) NOT NULL,
            category_id INT NOT NULL,
            visitor_count INT NOT NULL,
            recorded_by_user_id INT NULL,
            recorded_by_username VARCHAR(50),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (station_id) REFERENCES stations(station_id) ON DELETE RESTRICT,
            FOREIGN KEY (category_id) REFERENCES visitor_categories(category_id) ON DELETE RESTRICT,
            FOREIGN KEY (recorded_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
            INDEX idx_station_visitors_period (period_start, station_id)
        )""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS station_volunteers (
            volunteer_id INT AUTO_INCREMENT PRIMARY KEY,
            station_id INT NOT NULL,
            volunteer_name VARCHAR(150) NOT NULL,
            volunteer_identifier VARCHAR(50) NOT NULL,
            account_type VARCHAR(100) NOT NULL,
            account_name VARCHAR(150) NOT NULL,
            account_number VARCHAR(100) NOT NULL,
            mobile_phone VARCHAR(50) NOT NULL,
            recorded_by_user_id INT NULL,
            recorded_by_username VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT fk_station_volunteer_station FOREIGN KEY (station_id)
                REFERENCES stations(station_id) ON DELETE RESTRICT,
            CONSTRAINT fk_station_volunteer_user FOREIGN KEY (recorded_by_user_id)
                REFERENCES users(user_id) ON DELETE SET NULL,
            UNIQUE KEY uq_station_volunteer_identifier (station_id, volunteer_identifier),
            INDEX idx_station_volunteer_name (volunteer_name)
        )""")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS station_inspections (
                inspection_id INT AUTO_INCREMENT PRIMARY KEY,
                station_id INT NOT NULL,
                inspection_date DATE NOT NULL,
                finding TEXT NOT NULL,
                workflow_stage VARCHAR(30) NOT NULL DEFAULT 'HQ Review',
                status VARCHAR(30) NOT NULL DEFAULT 'Open',
                not_solved_reason TEXT,
                created_by_user_id INT,
                created_by_username VARCHAR(50),
                updated_by_user_id INT,
                updated_by_username VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                CONSTRAINT fk_station_inspection_station
                    FOREIGN KEY (station_id) REFERENCES stations(station_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT fk_station_inspection_creator
                    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id)
                    ON DELETE SET NULL,
                CONSTRAINT fk_station_inspection_updater
                    FOREIGN KEY (updated_by_user_id) REFERENCES users(user_id)
                    ON DELETE SET NULL,
                INDEX idx_station_inspection_stage_status
                    (workflow_stage, status, inspection_date)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS station_inspection_comments (
                comment_id INT AUTO_INCREMENT PRIMARY KEY,
                inspection_id INT NOT NULL,
                action_type VARCHAR(40) NOT NULL DEFAULT 'Comment',
                comment TEXT,
                status_after VARCHAR(30),
                reason TEXT,
                commented_by_user_id INT,
                commented_by_username VARCHAR(50),
                commented_by_department VARCHAR(100),
                commented_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_station_inspection_comment_inspection
                    FOREIGN KEY (inspection_id) REFERENCES station_inspections(inspection_id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_station_inspection_comment_user
                    FOREIGN KEY (commented_by_user_id) REFERENCES users(user_id)
                    ON DELETE SET NULL,
                INDEX idx_station_inspection_comment_record
                    (inspection_id, commented_at)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS station_inspection_reports (
                report_id INT AUTO_INCREMENT PRIMARY KEY,
                inspection_id INT NULL,
                period_start DATE,
                period_end DATE,
                notes VARCHAR(2000),
                original_filename VARCHAR(255) NOT NULL,
                content_type VARCHAR(100) NOT NULL,
                file_size INT NOT NULL,
                file_data LONGBLOB NOT NULL,
                uploaded_by_user_id INT,
                uploaded_by_username VARCHAR(50),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_station_inspection_report_inspection
                    FOREIGN KEY (inspection_id) REFERENCES station_inspections(inspection_id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_station_inspection_report_user
                    FOREIGN KEY (uploaded_by_user_id) REFERENCES users(user_id)
                    ON DELETE SET NULL,
                INDEX idx_station_inspection_report_record
                    (inspection_id, uploaded_at)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS station_inspection_report_stations (
                report_id INT NOT NULL,
                station_id INT NOT NULL,
                PRIMARY KEY (report_id, station_id),
                CONSTRAINT fk_inspection_report_station_report
                    FOREIGN KEY (report_id) REFERENCES station_inspection_reports(report_id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_inspection_report_station_station
                    FOREIGN KEY (station_id) REFERENCES stations(station_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT
            )
            """
        )
        cursor.execute(
            """
            INSERT IGNORE INTO station_inspection_report_stations (report_id, station_id)
            SELECT reports.report_id, inspections.station_id
            FROM station_inspection_reports AS reports
            INNER JOIN station_inspections AS inspections
                ON inspections.inspection_id = reports.inspection_id
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS station_inspection_photos (
                inspection_id INT PRIMARY KEY,
                original_filename VARCHAR(255) NOT NULL,
                content_type VARCHAR(100) NOT NULL,
                file_size INT NOT NULL,
                file_data LONGBLOB NOT NULL,
                uploaded_by_user_id INT,
                uploaded_by_username VARCHAR(50),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP,
                CONSTRAINT fk_inspection_photo_inspection
                    FOREIGN KEY (inspection_id) REFERENCES station_inspections(inspection_id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_inspection_photo_user
                    FOREIGN KEY (uploaded_by_user_id) REFERENCES users(user_id)
                    ON DELETE SET NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS discussions (
                discussion_id INT AUTO_INCREMENT PRIMARY KEY,
                title VARCHAR(200) NOT NULL,
                status ENUM('Open', 'Closed') NOT NULL DEFAULT 'Open',
                created_by_user_id INT,
                created_by_username VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_by_user_id INT,
                closed_by_username VARCHAR(50),
                closed_at DATETIME,
                CONSTRAINT fk_discussion_creator
                    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id)
                    ON DELETE SET NULL,
                CONSTRAINT fk_discussion_closer
                    FOREIGN KEY (closed_by_user_id) REFERENCES users(user_id)
                    ON DELETE SET NULL,
                INDEX idx_discussion_status_created (status, created_at)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS discussion_participants (
                discussion_id INT NOT NULL,
                user_id INT NOT NULL,
                invited_by_user_id INT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (discussion_id, user_id),
                CONSTRAINT fk_discussion_participant_discussion
                    FOREIGN KEY (discussion_id) REFERENCES discussions(discussion_id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_discussion_participant_user
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_discussion_participant_inviter
                    FOREIGN KEY (invited_by_user_id) REFERENCES users(user_id)
                    ON DELETE SET NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS discussion_messages (
                message_id INT AUTO_INCREMENT PRIMARY KEY,
                discussion_id INT NOT NULL,
                message TEXT NOT NULL,
                posted_by_user_id INT,
                posted_by_username VARCHAR(50),
                posted_by_full_name VARCHAR(100),
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_discussion_message_discussion
                    FOREIGN KEY (discussion_id) REFERENCES discussions(discussion_id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_discussion_message_user
                    FOREIGN KEY (posted_by_user_id) REFERENCES users(user_id)
                    ON DELETE SET NULL,
                INDEX idx_discussion_message_thread (discussion_id, posted_at)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_station_assignments (
                user_id INT NOT NULL,
                station_id INT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, station_id),
                CONSTRAINT fk_assignment_user
                    FOREIGN KEY (user_id)
                    REFERENCES users(user_id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_assignment_station
                    FOREIGN KEY (station_id)
                    REFERENCES stations(station_id)
                    ON UPDATE CASCADE
                    ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS instruments (
                instrument_id INT AUTO_INCREMENT PRIMARY KEY,
                instrument_name VARCHAR(100) NOT NULL UNIQUE,
                parameters_taken VARCHAR(1000) NOT NULL,
                category VARCHAR(100),
                description VARCHAR(1000),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_by_user_id INT,
                recorded_by_username VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_instrument_creator
                    FOREIGN KEY (created_by_user_id)
                    REFERENCES users(user_id)
                    ON DELETE SET NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS instrument_station_categories (
                instrument_id INT NOT NULL,
                station_category VARCHAR(60) NOT NULL,
                PRIMARY KEY (instrument_id, station_category),
                CONSTRAINT fk_instrument_station_category
                    FOREIGN KEY (instrument_id)
                    REFERENCES instruments(instrument_id)
                    ON UPDATE CASCADE
                    ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS maintenance_record_instruments (
                maintenance_id INT NOT NULL,
                instrument_id INT NOT NULL,
                issue TEXT,
                action_done TEXT,
                recommendation TEXT,
                PRIMARY KEY (maintenance_id, instrument_id),
                CONSTRAINT fk_record_instrument_maintenance
                    FOREIGN KEY (maintenance_id)
                    REFERENCES maintenance_records(maintenance_id)
                    ON UPDATE CASCADE
                    ON DELETE CASCADE,
                CONSTRAINT fk_record_instrument_instrument
                    FOREIGN KEY (instrument_id)
                    REFERENCES instruments(instrument_id)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT
            )
            """
        )
        for field in ("issue", "action_done", "recommendation"):
            ensure_column(cursor, "maintenance_record_instruments", field, "TEXT NULL")
        cursor.execute("""UPDATE maintenance_record_instruments AS link
            JOIN maintenance_records AS record ON record.maintenance_id = link.maintenance_id
            SET link.issue = COALESCE(link.issue, record.issue),
                link.action_done = COALESCE(link.action_done, record.activity_done),
                link.recommendation = COALESCE(link.recommendation, record.recommendations)
            WHERE link.issue IS NULL OR link.action_done IS NULL""")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS suspected_data_records (
                suspected_data_id INT AUTO_INCREMENT PRIMARY KEY,
                station_id INT NOT NULL,
                issue VARCHAR(255) NOT NULL,
                description TEXT NOT NULL,
                reported_by_user_id INT,
                reported_by_username VARCHAR(50),
                maintenance_date DATE,
                maintenance_issue TEXT,
                how_solved TEXT,
                maintenance_outcome VARCHAR(30),
                way_forward TEXT,
                resolved_by_user_id INT,
                resolved_by_username VARCHAR(50),
                resolved_at DATETIME,
                status VARCHAR(30) NOT NULL DEFAULT 'Open',
                final_is_solved BOOLEAN,
                final_comment TEXT,
                final_reviewed_by_user_id INT,
                final_reviewed_by_username VARCHAR(50),
                final_reviewed_at DATETIME,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                CONSTRAINT fk_suspected_data_station
                    FOREIGN KEY (station_id)
                    REFERENCES stations(station_id)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT,
                CONSTRAINT fk_suspected_data_reporter
                    FOREIGN KEY (reported_by_user_id)
                    REFERENCES users(user_id)
                    ON DELETE SET NULL,
                CONSTRAINT fk_suspected_data_resolver
                    FOREIGN KEY (resolved_by_user_id)
                    REFERENCES users(user_id)
                    ON DELETE SET NULL,
                CONSTRAINT fk_suspected_data_final_reviewer
                    FOREIGN KEY (final_reviewed_by_user_id)
                    REFERENCES users(user_id)
                    ON DELETE SET NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS station_instruments (
                station_instrument_id INT AUTO_INCREMENT PRIMARY KEY,
                station_id INT NOT NULL,
                instrument_id INT NOT NULL,
                model VARCHAR(100),
                manufacturer VARCHAR(100),
                serial_number VARCHAR(100),
                installation_date DATE NOT NULL,
                calibration_replacement_date DATE NULL,
                calibration_date DATE NULL,
                replacement_date DATE NULL,
                recommended_calibration_date DATE NULL,
                recommended_replacement_date DATE NULL,
                status VARCHAR(30) NOT NULL,
                comment TEXT,
                data_logger_ports VARCHAR(255) NULL,
                algorithm TEXT NULL,
                wiring_colors JSON NULL,
                created_by_user_id INT,
                recorded_by_username VARCHAR(50),
                updated_by_user_id INT,
                updated_by_username VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                CONSTRAINT fk_station_instrument_station
                    FOREIGN KEY (station_id)
                    REFERENCES stations(station_id)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT,
                CONSTRAINT fk_station_instrument_catalog
                    FOREIGN KEY (instrument_id)
                    REFERENCES instruments(instrument_id)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT,
                CONSTRAINT fk_station_instrument_creator
                    FOREIGN KEY (created_by_user_id)
                    REFERENCES users(user_id)
                    ON DELETE SET NULL,
                CONSTRAINT fk_station_instrument_updater
                    FOREIGN KEY (updated_by_user_id)
                    REFERENCES users(user_id)
                    ON DELETE SET NULL
            )
            """
        )
        ensure_column(cursor, "record_edit_history", "edit_reason", "VARCHAR(500) NULL")
        ensure_column(cursor, "monthly_reporting_changes", "edit_reason", "VARCHAR(500) NULL")
        ensure_column(
            cursor,
            "users",
            "must_change_password",
            "BOOLEAN NOT NULL DEFAULT FALSE",
        )
        ensure_department_options(cursor)
        for table_name in ("stations", "maintenance_records", "instruments"):
            ensure_column(cursor, table_name, "created_by_user_id", "INT NULL")
            ensure_column(cursor, table_name, "recorded_by_username", "VARCHAR(50) NULL")
        ensure_column(
            cursor,
            "stations",
            "created_at",
            "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
        )
        ensure_column(cursor, "stations", "station_code", "VARCHAR(50) NULL")
        ensure_column(cursor, "stations", "altitude", "DECIMAL(10,2) NULL")
        ensure_column(cursor, "stations", "province", "VARCHAR(100) NULL")
        ensure_column(cursor, "stations", "district", "VARCHAR(100) NULL")
        ensure_column(cursor, "stations", "sector", "VARCHAR(100) NULL")
        ensure_column(cursor, "stations", "station_category", "VARCHAR(60) NULL")
        cursor.execute(
            "UPDATE stations SET status = 'Under maintenance' WHERE status = 'Maintenance'"
        )
        cursor.execute(
            """SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'stations'
                AND COLUMN_NAME = 'suspended'"""
        )
        if cursor.fetchone()[0]:
            cursor.execute(
                "UPDATE stations SET status = 'Suspended' WHERE suspended = TRUE"
            )
            cursor.execute("ALTER TABLE stations DROP COLUMN suspended")
        ensure_station_status_enum(cursor)
        ensure_column(cursor, "monthly_reporting_status", "operational_stations", "INT NULL")
        ensure_column(cursor, "monthly_reporting_status", "under_maintenance_stations", "INT NULL")
        ensure_column(cursor, "monthly_reporting_status", "suspended_stations", "INT NULL")
        ensure_column(cursor, "stations", "comment", "TEXT NULL")
        ensure_column(cursor, "stations", "action", "TEXT NULL")
        ensure_column(cursor, "station_visitors", "institution", "VARCHAR(200) NOT NULL DEFAULT ''")
        ensure_column(cursor, "instruments", "parameters_taken", "VARCHAR(1000) NULL")
        ensure_column(cursor, "station_instruments", "calibration_date", "DATE NULL")
        ensure_column(cursor, "station_instruments", "replacement_date", "DATE NULL")
        ensure_column(cursor, "station_instruments", "recommended_calibration_date", "DATE NULL")
        ensure_column(cursor, "station_instruments", "recommended_replacement_date", "DATE NULL")
        ensure_column(cursor, "station_instruments", "comment", "TEXT NULL")
        ensure_column(cursor, "station_instruments", "data_logger_ports", "VARCHAR(255) NULL")
        ensure_column(cursor, "station_instruments", "algorithm", "TEXT NULL")
        ensure_column(cursor, "station_instruments", "wiring_colors", "JSON NULL")
        ensure_column(cursor, "station_inspection_reports", "period_start", "DATE NULL")
        ensure_column(cursor, "station_inspection_reports", "period_end", "DATE NULL")
        cursor.execute(
            "ALTER TABLE station_inspection_reports MODIFY COLUMN inspection_id INT NULL"
        )
        cursor.execute(
            "ALTER TABLE station_instruments "
            "MODIFY COLUMN calibration_replacement_date DATE NULL"
        )
        cursor.execute(
            """
            UPDATE station_instruments
            SET calibration_date = calibration_replacement_date
            WHERE calibration_date IS NULL
                AND calibration_replacement_date IS NOT NULL
            """
        )
        ensure_column(cursor, "suspected_data_records", "resolved_at", "DATETIME NULL")
        ensure_column(cursor, "suspected_data_records", "maintenance_outcome", "VARCHAR(30) NULL")
        ensure_column(cursor, "suspected_data_records", "way_forward", "TEXT NULL")
        ensure_column(cursor, "suspected_data_records", "final_is_solved", "BOOLEAN NULL")
        ensure_column(cursor, "suspected_data_records", "final_comment", "TEXT NULL")
        ensure_column(
            cursor,
            "suspected_data_records",
            "final_reviewed_by_user_id",
            "INT NULL",
        )
        ensure_column(
            cursor,
            "suspected_data_records",
            "final_reviewed_by_username",
            "VARCHAR(50) NULL",
        )
        ensure_column(
            cursor,
            "suspected_data_records",
            "final_reviewed_at",
            "DATETIME NULL",
        )
        cursor.execute(
            """
            UPDATE suspected_data_records
            SET maintenance_outcome = CASE maintenance_outcome
                WHEN 'Not solved' THEN 'Not Solved'
                WHEN 'Not maintained' THEN 'Not Maintained'
                ELSE maintenance_outcome
            END
            WHERE maintenance_outcome IN ('Not solved', 'Not maintained')
            """
        )
        cursor.execute(
            """
            UPDATE stations
            SET station_code = CAST(station_id AS CHAR)
            WHERE station_code IS NULL OR station_code = ''
            """
        )
        ensure_unique_index(cursor, "stations", "uq_station_code", "station_code")
        ensure_foreign_key(
            cursor,
            "stations",
            "fk_station_creator",
            "FOREIGN KEY (`created_by_user_id`) REFERENCES `users` (`user_id`) "
            "ON DELETE SET NULL",
        )
        ensure_foreign_key(
            cursor,
            "maintenance_records",
            "fk_maintenance_creator",
            "FOREIGN KEY (`created_by_user_id`) REFERENCES `users` (`user_id`) "
            "ON DELETE SET NULL",
        )
        ensure_foreign_key(
            cursor,
            "instruments",
            "fk_instrument_creator",
            "FOREIGN KEY (`created_by_user_id`) REFERENCES `users` (`user_id`) "
            "ON DELETE SET NULL",
        )
        ensure_foreign_key(
            cursor,
            "suspected_data_records",
            "fk_suspected_data_final_reviewer",
            "FOREIGN KEY (`final_reviewed_by_user_id`) REFERENCES `users` (`user_id`) "
            "ON DELETE SET NULL",
        )
        migrate_station_categories(cursor)
        cursor.execute("SELECT 1 FROM app_migration_markers WHERE migration_key = 'station_category_catalog_v1'")
        if not cursor.fetchone():
            cursor.executemany(
                "INSERT IGNORE INTO station_categories (name) VALUES (%s)",
                [(name,) for name in DEFAULT_STATION_CATEGORIES],
            )
            for table in ("stations", "instrument_station_categories", "category_maintenance_targets", "monthly_category_data_counts"):
                cursor.execute(
                    f"INSERT IGNORE INTO station_categories (name) SELECT DISTINCT station_category "
                    f"FROM `{table}` WHERE station_category IS NOT NULL AND station_category <> ''"
                )
            cursor.execute("SELECT category_key FROM monthly_combined_data_counts")
            extra = {name for (key,) in cursor.fetchall() for name in key.split("|") if name}
            if extra:
                cursor.executemany("INSERT IGNORE INTO station_categories (name) VALUES (%s)", [(name,) for name in extra])
            cursor.execute("INSERT INTO app_migration_markers (migration_key) VALUES ('station_category_catalog_v1')")
        connection.commit()
    finally:
        cursor.close()


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                users.user_id,
                users.full_name,
                users.username,
                users.email,
                users.department,
                users.must_change_password
            FROM user_sessions
            INNER JOIN users ON users.user_id = user_sessions.user_id
            WHERE user_sessions.token_hash = %s
                AND user_sessions.expires_at > NOW()
                AND users.is_active = TRUE
            """,
            (hash_session_token(credentials.credentials),),
        )
        user = cursor.fetchone()

        if user is None:
            raise HTTPException(status_code=401, detail="Session expired or invalid")

        user["edit_reason"] = unquote(request.headers.get("x-edit-reason", "")).strip()
        set_activity_actor(user)
        return user

    except HTTPException:
        raise
    except MySQLError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {exc}",
        ) from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


def require_password_change_complete(user=Depends(get_current_user)):
    if user["must_change_password"]:
        raise HTTPException(status_code=403, detail="Password change required")
    return user


def require_it(user=Depends(require_password_change_complete)):
    if user["department"] != "Admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


@app.get("/station-categories")
def list_station_categories(_user=Depends(require_password_change_complete)):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT category_id, name FROM station_categories ORDER BY name")
        return cursor.fetchall()
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.post("/station-categories", status_code=201)
def add_station_category(item: StationCategoryInput, user=Depends(require_it)):
    name = item.name.strip()
    if not name or any(separator in name for separator in "|,;"):
        raise HTTPException(status_code=400, detail="Category name cannot be empty or contain |, comma, or semicolon")
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor()
        cursor.execute("INSERT INTO station_categories (name) VALUES (%s)", (name,))
        category_id = cursor.lastrowid
        record_entity_edit(connection, "station_categories", category_id, {}, {"category_id": category_id, "name": name}, user)
        connection.commit()
        return {"category_id": category_id, "name": name}
    except IntegrityError as exc:
        connection.rollback()
        raise HTTPException(status_code=409, detail="Station category already exists") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.put("/station-categories/{category_id}")
def update_station_category(category_id: int, item: StationCategoryInput, user=Depends(require_it)):
    name = item.name.strip()
    if not name or any(separator in name for separator in "|,;"):
        raise HTTPException(status_code=400, detail="Category name cannot be empty or contain |, comma, or semicolon")
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor()
        cursor.execute("SELECT name FROM station_categories WHERE category_id = %s FOR UPDATE", (category_id,))
        found = cursor.fetchone()
        if not found:
            raise HTTPException(status_code=404, detail="Station category not found")
        old = found[0]
        if name == old:
            return {"category_id": category_id, "name": name}
        cursor.execute("SELECT category_id FROM station_categories WHERE name = %s AND category_id <> %s", (name, category_id))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail="Station category already exists")
        for table, key in (("category_maintenance_targets", "fiscal_start_year"),
                           ("monthly_category_data_counts", "record_month")):
            if old.casefold() == name.casefold():
                continue
            cursor.execute(
                f"SELECT 1 FROM `{table}` old JOIN `{table}` current ON old.`{key}` = current.`{key}` "
                "AND current.station_category = %s WHERE old.station_category = %s LIMIT 1",
                (name, old),
            )
            if cursor.fetchone():
                raise HTTPException(status_code=409, detail=f"The new category name conflicts with existing {table} records")
        cursor.execute("SELECT data_count_id, record_month, category_key FROM monthly_combined_data_counts")
        combined = cursor.fetchall()
        rewritten = {}
        for data_count_id, month, key in combined:
            updated = "|".join(sorted({name if part == old else part for part in key.split("|")}))
            if (month, updated) in rewritten:
                raise HTTPException(status_code=409, detail="The new name conflicts with an existing combined data count")
            rewritten[(month, updated)] = (data_count_id, key)
        for table in ("stations", "instrument_station_categories", "category_maintenance_targets", "monthly_category_data_counts"):
            cursor.execute(f"UPDATE `{table}` SET station_category = %s WHERE station_category = %s", (name, old))
        for (_month, updated), (data_count_id, previous) in rewritten.items():
            if updated != previous:
                cursor.execute("UPDATE monthly_combined_data_counts SET category_key = %s WHERE data_count_id = %s", (updated, data_count_id))
        cursor.execute("UPDATE station_categories SET name = %s WHERE category_id = %s", (name, category_id))
        cursor.execute("SELECT history_id, entity_id FROM record_edit_history WHERE entity_type = 'maintenance_frequencies'")
        for history_id, entity_id in cursor.fetchall():
            if entity_id.endswith("|" + old):
                cursor.execute("UPDATE record_edit_history SET entity_id = %s WHERE history_id = %s",
                               (entity_id[:-(len(old))] + name, history_id))
        record_entity_edit(connection, "station_categories", category_id,
                           {"category_id": category_id, "name": old}, {"category_id": category_id, "name": name}, user)
        connection.commit()
        return {"category_id": category_id, "name": name}
    except IntegrityError as exc:
        connection.rollback()
        raise HTTPException(status_code=409, detail="Station category conflicts with an existing record") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


def require_data(user=Depends(require_password_change_complete)):
    if user["department"] != "Data Quality Control Officer":
        raise HTTPException(status_code=403, detail="Data Quality Control Officer access required")
    return user


def require_data_or_it(user=Depends(require_password_change_complete)):
    if user["department"] != "Admin" and user["department"] not in DATA_QUALITY_ACTION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Data quality access required",
        )
    return user


def require_maintenance(user=Depends(require_password_change_complete)):
    if user["department"] != MAINTENANCE_ROLE:
        raise HTTPException(status_code=403, detail="Maintenance access required")
    return user


def require_maintenance_or_it(user=Depends(require_password_change_complete)):
    if user["department"] not in (MAINTENANCE_ROLE, "Admin"):
        raise HTTPException(
            status_code=403,
            detail="Maintenance or Administrator access required",
        )
    return user


def require_suspected_data_access(user=Depends(require_password_change_complete)):
    return user


def require_instrument_catalog_access(user=Depends(require_password_change_complete)):
    if (
        user["department"] not in ("Admin", MAINTENANCE_ROLE)
        and user["department"] not in READ_ONLY_ALL_ROLES
    ):
        raise HTTPException(status_code=403, detail="Instrument catalog access is not allowed")
    return user


def require_user_directory_access(user=Depends(require_password_change_complete)):
    if user["department"] != "Admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


def require_volunteer_data_access(user=Depends(require_password_change_complete)):
    if user["department"] != "Admin" and user["department"] not in VOLUNTEER_DATA_ROLES:
        raise HTTPException(status_code=403, detail="Volunteer data access is not allowed")
    return user


def require_data_operations_writer(user=Depends(require_password_change_complete)):
    if user["department"] != "Admin" and user["department"] not in DATA_OPERATIONS_ROLES:
        raise HTTPException(status_code=403, detail="Data operations write access is required")
    return user


def require_reporting_view(user=Depends(require_password_change_complete)):
    if user["department"] != "Admin" and user["department"] not in REPORTING_VIEW_ROLES:
        raise HTTPException(status_code=403, detail="Monthly reporting access is not allowed")
    return user


AUDITED_RECORDS = {
    "station_categories": ("station_categories", "category_id"),
    "stations": ("stations", "station_id"),
    "users": ("users", "user_id"),
    "instruments": ("instruments", "instrument_id"),
    "maintenance": ("maintenance_records", "maintenance_id"),
    "station_instruments": ("station_instruments", "station_instrument_id"),
    "suspected_data": ("suspected_data_records", "suspected_data_id"),
    "station_inspections": ("station_inspections", "inspection_id"),
    "data_requests": ("monthly_data_requests", "data_request_id"),
    "category_data_counts": ("monthly_category_data_counts", "data_count_id"),
    "combined_data_counts": ("monthly_combined_data_counts", "data_count_id"),
    "station_visitors": ("station_visitors", "visitor_id"),
    "station_volunteers": ("station_volunteers", "volunteer_id"),
    "visitor_categories": ("visitor_categories", "category_id"),
    "maintenance_frequencies": ("category_maintenance_targets", "station_category"),
    "volunteer_data": (None, None),
}


def deleted_items_query(entity_type=None, district=None, search=None, status="deleted",
                        date_from=None, date_to=None):
    conditions, params = [], []
    if entity_type:
        types = filter_values(entity_type)
        if any(value not in DELETED_SPECS and value != "volunteer_data" for value in types):
            raise HTTPException(status_code=400, detail="Unknown item type")
        add_filter_condition(conditions, params, "entity_type", entity_type)
    if district:
        values = filter_values(district)
        conditions.append("(" + " OR ".join(["FIND_IN_SET(%s, district) > 0"] * len(values)) + ")")
        params.extend(values)
    if search:
        conditions.append("(title LIKE %s OR entity_id LIKE %s OR deleted_by_username LIKE %s)")
        params.extend([f"%{search}%"] * 3)
    if status == "deleted":
        conditions.append("restored_at IS NULL")
    elif status == "restored":
        conditions.append("restored_at IS NOT NULL")
    if date_from:
        conditions.append("deleted_at >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("deleted_at < %s")
        params.append(date_to + timedelta(days=1))
    return (" WHERE " + " AND ".join(conditions) if conditions else ""), params


@app.get("/deleted-items/filters")
def deleted_item_filters(_admin=Depends(require_it)):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor()
        cursor.execute("SELECT DISTINCT district FROM deleted_items WHERE district IS NOT NULL ORDER BY district")
        districts = sorted({name for row in cursor.fetchall() for name in row[0].split(",") if name})
        return {"types": sorted([*DELETED_SPECS, "volunteer_data"]), "districts": districts}
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.get("/deleted-items")
def list_deleted_items(
    entity_type: str | None = None, district: str | None = None,
    search: str | None = Query(default=None, max_length=100),
    status: str = "deleted",
    date_from: date | None = None, date_to: date | None = None,
    sort_by: Literal["deleted_at", "entity_type", "title", "district", "deleted_by_username", "row_count"] = "deleted_at",
    sort_order: Literal["asc", "desc"] = "desc",
    page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
    _admin=Depends(require_it),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="Start date must be before end date")
    where, params = deleted_items_query(entity_type, district, search, status, date_from, date_to)
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) AS total FROM deleted_items" + where, tuple(params))
        total = cursor.fetchone()["total"]
        cursor.execute("""SELECT deleted_item_id, entity_type, entity_id, title, district,
            deleted_by_username, deleted_at, restored_by_username, restored_at, row_count
            FROM deleted_items""" + where +
            f" ORDER BY {sort_by} {sort_order.upper()}, deleted_item_id DESC LIMIT %s OFFSET %s",
            (*params, page_size, (page - 1) * page_size))
        return {"items": cursor.fetchall(), "total": total, "page": page, "page_size": page_size}
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.get("/deleted-items/export")
def export_deleted_items(
    entity_type: str | None = None, district: str | None = None,
    search: str | None = Query(default=None, max_length=100),
    status: str = "deleted",
    date_from: date | None = None, date_to: date | None = None,
    sort_by: Literal["deleted_at", "entity_type", "title", "district", "deleted_by_username", "row_count"] = "deleted_at",
    sort_order: Literal["asc", "desc"] = "desc",
    format: Literal["csv", "pdf"] = "csv",
    admin=Depends(require_it),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="Start date must be before end date")
    where, params = deleted_items_query(entity_type, district, search, status, date_from, date_to)
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""SELECT deleted_item_id, entity_type, entity_id, title, district,
            deleted_by_username, deleted_at, restored_by_username, restored_at, row_count
            FROM deleted_items""" + where +
            f" ORDER BY {sort_by} {sort_order.upper()}, deleted_item_id DESC", tuple(params))
        items = cursor.fetchall()
        headers = ["Backup ID", "Type", "Original ID", "Item", "District", "Deleted by",
                   "Deleted at", "Records backed up", "Restored by", "Restored at"]
        rows = [[item[key] for key in ("deleted_item_id", "entity_type", "entity_id", "title",
                 "district", "deleted_by_username", "deleted_at", "row_count",
                 "restored_by_username", "restored_at")] for item in items]
        if format == "pdf":
            return pdf_download("deleted-items.pdf", "Deleted Items", headers, rows, admin["username"])
        return csv_download("deleted-items.csv", headers, rows)
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.post("/deleted-items/{deleted_item_id}/restore")
def restore_deleted_item_endpoint(deleted_item_id: int, admin=Depends(require_it)):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        return restore_deleted_item(connection, deleted_item_id, admin)
    finally:
        if connection.is_connected(): connection.close()


@app.get("/deleted-items/{deleted_item_id}/history")
def deleted_item_history_endpoint(deleted_item_id: int, _admin=Depends(require_it)):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        return {"items": get_deleted_item_history(connection, deleted_item_id)}
    finally:
        if connection.is_connected(): connection.close()


@app.get("/deleted-items/{deleted_item_id}/download")
def download_deleted_item_endpoint(deleted_item_id: int, _admin=Depends(require_it)):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        content = download_deleted_item(connection, deleted_item_id)
        return Response(content=content, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="deleted-item-{deleted_item_id}.zip"'
        })
    finally:
        if connection.is_connected(): connection.close()


class HistoryExportColumn(BaseModel):
    label: str = Field(max_length=100)
    source: str | None = Field(default=None, max_length=80)
    fallback: str | None = Field(default=None, max_length=500)


class RecordHistoryExport(BaseModel):
    entity_type: str
    entity_ids: list[str] = Field(min_length=1, max_length=5000)
    columns: list[HistoryExportColumn] | None = Field(default=None, max_length=50)


def plain_history_value(value):
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)):
                value = parsed
        except (ValueError, TypeError):
            return value
    if isinstance(value, dict):
        return "; ".join(f"{key.replace('_', ' ')}: {plain_history_value(part)}"
                         for key, part in value.items()) or "-"
    if isinstance(value, list):
        return "; ".join(plain_history_value(part) for part in value) or "-"
    return str(value)


def history_export_columns(entries, requested):
    if requested is not None:
        return requested
    keys = list(dict.fromkeys(key for entry in entries
        for snapshot in (entry["before_data"], entry["after_data"]) if snapshot
        for key in snapshot if not any(
            part in key.lower() for part in ("password", "secret", "token", "session"))))
    return [HistoryExportColumn(label=key.replace("_", " ").title(), source=key)
            for key in keys]


def history_export_lookups(cursor, entries):
    snapshots = [snapshot for entry in entries
                 for snapshot in (entry["before_data"], entry["after_data"]) if snapshot]
    station_ids = {snapshot.get("station_id") for snapshot in snapshots if snapshot.get("station_id")}
    instrument_ids = {snapshot.get("instrument_id") for snapshot in snapshots if snapshot.get("instrument_id")}
    lookups = {"stations": {}, "instruments": {}}
    if station_ids:
        cursor.execute("""SELECT station_id, station_code, station_name, province, district,
            sector, station_category FROM stations WHERE station_id IN (""" +
            ", ".join(["%s"] * len(station_ids)) + ")", tuple(station_ids))
        lookups["stations"] = {row["station_id"]: row for row in cursor.fetchall()}
    if instrument_ids:
        cursor.execute("""SELECT instrument_id, instrument_name, parameters_taken
            FROM instruments WHERE instrument_id IN (""" +
            ", ".join(["%s"] * len(instrument_ids)) + ")", tuple(instrument_ids))
        lookups["instruments"] = {row["instrument_id"]: row for row in cursor.fetchall()}
    return lookups


def history_export_cell(snapshot, column, allowed_keys, lookups=None):
    source = column.source
    if not source:
        return ""
    lookups = lookups or {"stations": {}, "instruments": {}}
    station = lookups["stations"].get(snapshot.get("station_id"), {})
    instrument = lookups["instruments"].get(snapshot.get("instrument_id"), {})
    if source == "station_id" and snapshot.get("station_id"):
        label = column.label.casefold()
        if label == "station id": return station.get("station_code") or str(snapshot["station_id"])
        if label == "station name": return station.get("station_name") or str(snapshot["station_id"])
        if station: return f'{station["station_code"]} - {station["station_name"]}'
    if source == "instrument_id" and snapshot.get("instrument_id") and column.label.casefold() == "instrument":
        return instrument.get("instrument_name") or str(snapshot["instrument_id"])
    if source in {"station_category", "province", "district", "sector"} and source not in snapshot and station:
        return station.get(source) or "-"
    if source == "parameters_taken" and source not in snapshot and instrument:
        return instrument.get(source) or "-"
    if source == "files" and isinstance(snapshot.get("files"), list):
        kind = {"monthly qc report": "monthly_qc", "filtered data": "filtered_data",
                "filled data": "filled_data"}.get(column.label.casefold())
        file = next((item for item in snapshot["files"] if item.get("file_kind") == kind), None)
        return file.get("original_filename", "-") if file else "-"
    if source not in allowed_keys:
        return column.fallback or "-"
    if source not in snapshot:
        return column.fallback or "-"
    value = snapshot[source]
    if source == "is_active":
        return "Active" if value else "Inactive"
    if source == "wiring_colors" and value:
        colors = json.loads(value) if isinstance(value, str) else value
        return "; ".join(f"{index}: {color}" for index, color in enumerate(colors, 1) if color) or "-"
    return plain_history_value(value)


def authorize_record_history(entity_type, user):
    if entity_type not in AUDITED_RECORDS:
        raise HTTPException(status_code=404, detail="Record history is unavailable")
    role = user["department"]
    if entity_type in {"users", "visitor_categories", "maintenance_frequencies", "station_categories"} and role != "Admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    if entity_type == "instruments" and role not in {"Admin", MAINTENANCE_ROLE, *READ_ONLY_ALL_ROLES}:
        raise HTTPException(status_code=403, detail="Instrument catalog access is not allowed")
    if entity_type in {"data_requests", "category_data_counts", "combined_data_counts"} and role not in {"Admin", *REPORTING_VIEW_ROLES}:
        raise HTTPException(status_code=403, detail="Data operations access is not allowed")
    if entity_type in {"volunteer_data", "station_volunteers"} and role not in {"Admin", *VOLUNTEER_DATA_ROLES}:
        raise HTTPException(status_code=403, detail="Volunteer data access is not allowed")


def normalize_history_entity_id(entity_type, entity_id):
    if entity_type == "volunteer_data":
        try:
            return str(month_start(entity_id))[:7]
        except (ValueError, TypeError, HTTPException) as exc:
            raise HTTPException(status_code=400, detail="Invalid report month") from exc
    if entity_type == "maintenance_frequencies":
        if "|" not in entity_id:
            raise HTTPException(status_code=400, detail="Invalid frequency ID")
        return entity_id
    try:
        return str(int(entity_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid record ID") from exc


def audit_snapshot(connection, entity_type, entity_id):
    table, key = AUDITED_RECORDS[entity_type]
    cursor = connection.cursor(dictionary=True)
    try:
        if entity_type == "volunteer_data":
            target_month = entity_id if isinstance(entity_id, date) else month_start(str(entity_id))
            cursor.execute("""SELECT file_id, file_kind, original_filename, content_type,
                file_size, uploaded_by_username, uploaded_at FROM volunteer_data_files
                WHERE report_month = %s ORDER BY file_kind""", (target_month,))
            files = cursor.fetchall()
            cursor.execute("""SELECT comment_id, comment, commented_by_username, commented_at
                FROM volunteer_report_comments WHERE report_month = %s ORDER BY comment_id""",
                (target_month,))
            comments = cursor.fetchall()
            return json.loads(json.dumps({"report_month": str(target_month),
                                          "files": files, "comments": comments}, default=str))
        if entity_type == "maintenance_frequencies":
            fiscal_year, category = str(entity_id).split("|", 1)
            cursor.execute("""SELECT * FROM category_maintenance_targets
                WHERE fiscal_start_year = %s AND station_category = %s""",
                (int(fiscal_year), category))
        else:
            cursor.execute(f"SELECT * FROM `{table}` WHERE `{key}` = %s", (entity_id,))
        snapshot = cursor.fetchone()
        if snapshot is None:
            return None
        if entity_type == "users":
            allowed = {"user_id", "full_name", "username", "email", "department",
                       "must_change_password", "is_active", "created_at"}
            snapshot = {key: value for key, value in snapshot.items() if key in allowed}
            cursor.execute("SELECT station_id FROM user_station_assignments WHERE user_id = %s ORDER BY station_id", (entity_id,))
            snapshot["assigned_station_ids"] = [row["station_id"] for row in cursor.fetchall()]
        elif entity_type == "instruments":
            cursor.execute("SELECT station_category FROM instrument_station_categories WHERE instrument_id = %s ORDER BY station_category", (entity_id,))
            snapshot["station_categories"] = [row["station_category"] for row in cursor.fetchall()]
        elif entity_type == "maintenance":
            cursor.execute("""SELECT instrument_id, issue, action_done, recommendation
                FROM maintenance_record_instruments WHERE maintenance_id = %s ORDER BY instrument_id""",
                (entity_id,))
            snapshot["instrument_details"] = cursor.fetchall()
        elif entity_type == "station_inspections":
            cursor.execute("""SELECT comment_id, action_type, comment, status_after,
                reason, commented_by_username, commented_at
                FROM station_inspection_comments WHERE inspection_id = %s
                ORDER BY comment_id""", (entity_id,))
            snapshot["actions"] = cursor.fetchall()
        return json.loads(json.dumps(snapshot, default=str))
    finally:
        cursor.close()


def edit_reason_required(entity_type, before, after):
    if not before:
        return False
    if entity_type == "volunteer_data":
        old_files = {item["file_kind"]: item for item in before.get("files", [])}
        return any(item["file_kind"] in old_files and old_files[item["file_kind"]] != item
                   for item in (after or {}).get("files", []))
    return True


def record_entity_edit(connection, entity_type, entity_id, before, after, user):
    reason = user.get("edit_reason", "").strip()
    if edit_reason_required(entity_type, before, after) and not reason:
        raise HTTPException(status_code=422, detail="Explain the reason for this edit")
    if len(reason) > 500:
        raise HTTPException(status_code=422, detail="Edit reason must be at most 500 characters")
    cursor = connection.cursor()
    try:
        cursor.execute("""INSERT INTO record_edit_history
            (entity_type, entity_id, before_data, after_data, changed_by_username, edit_reason)
            VALUES (%s, %s, %s, %s, %s, %s)""",
            (entity_type, str(entity_id), json.dumps(before, default=str),
             json.dumps(after, default=str), user["username"], reason or None))
    finally:
        cursor.close()


@app.get("/record-history/{entity_type}/{entity_id}")
def get_record_history(entity_type: str, entity_id: str,
                       page: int = Query(default=1, ge=1),
                       page_size: int = Query(default=25, ge=1, le=100),
                       user=Depends(require_password_change_complete)):
    authorize_record_history(entity_type, user)
    entity_id = normalize_history_entity_id(entity_type, entity_id)
    role = user["department"]
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""SELECT history_id, before_data, after_data,
            changed_by_username, edit_reason, changed_at FROM record_edit_history
            WHERE entity_type = %s AND entity_id = %s
            ORDER BY changed_at DESC, history_id DESC""", (entity_type, str(entity_id)))
        items = cursor.fetchall()
        for item in items:
            for field in ("before_data", "after_data"):
                if isinstance(item[field], (str, bytes)):
                    item[field] = json.loads(item[field])
        if role in ASSIGNED_STATION_ROLES:
            cursor.execute("SELECT station_id FROM user_station_assignments WHERE user_id = %s", (user["user_id"],))
            allowed = {row["station_id"] for row in cursor.fetchall()}
            current = audit_snapshot(connection, entity_type, entity_id)
            current_station = int(entity_id) if entity_type == "stations" else (current or {}).get("station_id")
            if current_station not in allowed:
                raise HTTPException(status_code=403, detail="Station is not assigned to this account")
            items = [item for item in items if all(
                (snapshot.get("station_id", int(entity_id) if entity_type == "stations" else None) in allowed)
                for snapshot in (item["before_data"], item["after_data"]) if snapshot)]
        total = len(items)
        return {"items": items[(page - 1) * page_size:page * page_size],
                "total": total, "page": page, "page_size": page_size}
    finally:
        if "cursor" in locals(): cursor.close()
        if connection.is_connected(): connection.close()


@app.post("/record-history/export")
def export_record_history(request: RecordHistoryExport,
                          user=Depends(require_password_change_complete)):
    entity_type = request.entity_type
    authorize_record_history(entity_type, user)
    entity_ids = list(dict.fromkeys(
        normalize_history_entity_id(entity_type, str(value)) for value in request.entity_ids
    ))
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        placeholders = ", ".join(["%s"] * len(entity_ids))
        cursor.execute(f"""SELECT entity_id, before_data, after_data,
            changed_by_username, changed_at FROM record_edit_history
            WHERE entity_type = %s AND entity_id IN ({placeholders})
            ORDER BY changed_at DESC, history_id DESC""", (entity_type, *entity_ids))
        entries = cursor.fetchall()
        for entry in entries:
            for field in ("before_data", "after_data"):
                if isinstance(entry[field], (str, bytes)):
                    entry[field] = json.loads(entry[field])
        if user["department"] in ASSIGNED_STATION_ROLES:
            cursor.execute("SELECT station_id FROM user_station_assignments WHERE user_id = %s", (user["user_id"],))
            allowed = {row["station_id"] for row in cursor.fetchall()}
            current_allowed = set()
            for entity_id in entity_ids:
                current = audit_snapshot(connection, entity_type, entity_id)
                station_id = int(entity_id) if entity_type == "stations" else (current or {}).get("station_id")
                if station_id in allowed:
                    current_allowed.add(entity_id)
            entries = [entry for entry in entries if entry["entity_id"] in current_allowed
                       and all(snapshot.get("station_id", int(entry["entity_id"]) if entity_type == "stations" else None) in allowed
                               for snapshot in (entry["before_data"], entry["after_data"]) if snapshot)]
        columns = history_export_columns(entries, request.columns)
        lookups = history_export_lookups(cursor, entries)
        allowed_keys = {key for entry in entries
            for snapshot in (entry["before_data"], entry["after_data"]) if snapshot
            for key in snapshot if not any(
                part in key.lower() for part in ("password", "secret", "token", "session"))}
        headers = [column.label for column in columns]
        rows = []
        for entry in entries:
            after = entry["after_data"] or {}
            rows.append([history_export_cell(after, column, allowed_keys, lookups) for column in columns])
        return csv_download(f"{entity_type}-edit-history.csv", headers, rows)
    finally:
        if "cursor" in locals(): cursor.close()
        if connection.is_connected(): connection.close()


def replace_user_station_assignments(cursor, user_id, station_ids):
    station_ids = list(dict.fromkeys(station_ids))
    cursor.execute(
        "DELETE FROM user_station_assignments WHERE user_id = %s",
        (user_id,),
    )
    if not station_ids:
        return
    placeholders = ", ".join(["%s"] * len(station_ids))
    cursor.execute(
        f"SELECT station_id FROM stations WHERE station_id IN ({placeholders})",
        tuple(station_ids),
    )
    if {row[0] for row in cursor.fetchall()} != set(station_ids):
        raise HTTPException(status_code=400, detail="One or more stations do not exist")
    cursor.executemany(
        "INSERT INTO user_station_assignments (user_id, station_id) VALUES (%s, %s)",
        [(user_id, station_id) for station_id in station_ids],
    )


def ensure_user_can_access_station(cursor, user, station_id):
    if user["department"] not in ASSIGNED_STATION_ROLES:
        return
    cursor.execute(
        """
        SELECT station_id
        FROM user_station_assignments
        WHERE user_id = %s AND station_id = %s
        """,
        (user["user_id"], station_id),
    )
    if cursor.fetchone() is None:
        raise HTTPException(
            status_code=403,
            detail="This station is not assigned to your account",
        )


def create_role_notifications(
    cursor, departments, notification_type, title, message,
    related_record_type, related_record_id, station_id=None,
):
    placeholders = ", ".join(["%s"] * len(departments))
    query = f"""
        SELECT DISTINCT users.user_id
        FROM users
        LEFT JOIN user_station_assignments
            ON user_station_assignments.user_id = users.user_id
        WHERE users.is_active = TRUE
            AND users.department IN ({placeholders})
    """
    parameters = list(departments)
    if station_id is not None:
        query += """
            AND (users.department NOT IN ('Observation Officer', 'Observation Supervisor')
                OR user_station_assignments.station_id = %s)
        """
        parameters.append(station_id)
    cursor.execute(query, tuple(parameters))
    recipients = [row[0] for row in cursor.fetchall()]
    if recipients:
        cursor.executemany(
            """
            INSERT INTO notifications (
                user_id, notification_type, title, message,
                related_record_type, related_record_id
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [(user_id, notification_type, title, message, related_record_type, related_record_id) for user_id in recipients],
        )


@app.put("/maintenance/{maintenance_id}")
def update_maintenance_record(
    maintenance_id: int,
    record: MaintenanceRecord,
    _user=Depends(require_maintenance_or_it),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        before = audit_snapshot(connection, "maintenance", maintenance_id)

        cursor.execute(
            "SELECT maintenance_id FROM maintenance_records WHERE maintenance_id = %s",
            (maintenance_id,),
        )
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Maintenance record not found")

        cursor.execute(
            "SELECT station_id, station_code, station_name FROM stations WHERE station_id = %s",
            (record.station_id,),
        )
        station = cursor.fetchone()
        if station is None:
            raise HTTPException(status_code=404, detail="Station not found")

        instrument_ids, details, issue, activity_done, recommendations = maintenance_instrument_payload(record)
        placeholders = ", ".join(["%s"] * len(instrument_ids))
        cursor.execute(
            f"""
            SELECT instruments.instrument_id
            FROM instruments
            INNER JOIN instrument_station_categories
                ON instrument_station_categories.instrument_id = instruments.instrument_id
            INNER JOIN stations
                ON stations.station_id = %s
                AND stations.station_category =
                    instrument_station_categories.station_category
            WHERE instruments.instrument_id IN ({placeholders})
                AND instruments.is_active = TRUE
            """,
            (record.station_id, *instrument_ids),
        )
        if {row[0] for row in cursor.fetchall()} != set(instrument_ids):
            raise HTTPException(
                status_code=400,
                detail=(
                    "One or more selected instruments are unavailable for this "
                    "station category"
                ),
            )

        cursor.execute(
            """
            UPDATE maintenance_records
            SET station_id = %s,
                maintenance_date = %s,
                issue = %s,
                activity_done = %s,
                recommendations = %s,
                technicians = %s
            WHERE maintenance_id = %s
            """,
            (
                record.station_id,
                record.maintenance_date,
                issue,
                activity_done,
                recommendations,
                record.technicians.strip(),
                maintenance_id,
            ),
        )
        cursor.execute(
            "DELETE FROM maintenance_record_instruments WHERE maintenance_id = %s",
            (maintenance_id,),
        )
        cursor.executemany(
            """
            INSERT INTO maintenance_record_instruments
                (maintenance_id, instrument_id, issue, action_done, recommendation)
            VALUES (%s, %s, %s, %s, %s)
            """,
            [(maintenance_id, *detail) for detail in details],
        )
        record_entity_edit(connection, "maintenance", maintenance_id, before,
                           audit_snapshot(connection, "maintenance", maintenance_id), _user)
        connection.commit()
        return {"message": "Maintenance record updated"}
    except HTTPException:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.delete("/maintenance/{maintenance_id}", status_code=204)
def delete_maintenance_record(
    maintenance_id: int,
    _user=Depends(require_maintenance_or_it),
):
    try:
        connection = get_connection()
        archive_deleted_item(connection, "maintenance", maintenance_id, _user)
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM maintenance_records WHERE maintenance_id = %s",
            (maintenance_id,),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Maintenance record not found")
        connection.commit()
    except HTTPException:
        raise
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


def insert_user(connection, user, department, must_change_password):
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO users (
                full_name,
                username,
                email,
                department,
                password_hash,
                must_change_password
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                user.full_name.strip(),
                user.username.strip().lower(),
                user.email.strip().lower() if user.email else None,
                department,
                hash_password(user.password),
                must_change_password,
            ),
        )
        return cursor.lastrowid
    finally:
        cursor.close()


@app.get("/auth/status")
def get_auth_status():
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        return {"needs_setup": cursor.fetchone()[0] == 0}
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.post("/auth/bootstrap", status_code=201)
def bootstrap_it_user(user: BootstrapUser):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")

        if cursor.fetchone()[0] != 0:
            raise HTTPException(status_code=409, detail="Initial setup is already complete")

        cursor.close()
        del cursor
        user_id = insert_user(connection, user, "Admin", False)
        connection.commit()
        return {"message": "Administrator created", "user_id": user_id}
    except HTTPException:
        raise
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Username already exists") from exc
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.post("/auth/login")
def login(login_request: LoginRequest):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                user_id,
                full_name,
                username,
                email,
                department,
                password_hash,
                must_change_password
            FROM users
            WHERE username = %s AND is_active = TRUE
            """,
            (login_request.username.strip().lower(),),
        )
        user = cursor.fetchone()

        if user is None or not verify_password(
            login_request.password,
            user["password_hash"],
        ):
            raise HTTPException(status_code=401, detail="Invalid username or password")

        set_activity_actor(user)
        token = create_session_token()
        cursor.execute(
            """
            INSERT INTO user_sessions (user_id, token_hash, expires_at)
            VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL 12 HOUR))
            """,
            (user["user_id"], hash_session_token(token)),
        )
        connection.commit()
        user.pop("password_hash")

        return {"access_token": token, "token_type": "bearer", "user": user}
    except HTTPException:
        raise
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.get("/auth/me")
def get_my_profile(user=Depends(get_current_user)):
    return user


@app.put("/auth/password")
def change_password(
    password_change: PasswordChange,
    user=Depends(get_current_user),
):
    try:
        connection = get_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT password_hash FROM users WHERE user_id = %s",
            (user["user_id"],),
        )
        existing = cursor.fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="User not found")
        if verify_password(password_change.new_password, existing["password_hash"]):
            raise HTTPException(
                status_code=400,
                detail="New password must be different from the temporary password",
            )
        cursor.execute(
            """
            UPDATE users
            SET password_hash = %s, must_change_password = FALSE
            WHERE user_id = %s
            """,
            (hash_password(password_change.new_password), user["user_id"]),
        )
        connection.commit()
        return {"message": "Password changed"}
    except HTTPException:
        raise
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.post("/auth/logout", status_code=204)
def logout(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    _user=Depends(get_current_user),
):
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM user_sessions WHERE token_hash = %s",
            (hash_session_token(credentials.credentials),),
        )
        connection.commit()
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


def issue_instrument_alerts(cursor, user):
    today = date.today()
    month_start = today.replace(day=1)
    cursor.execute("""SELECT si.station_instrument_id, si.recommended_calibration_date,
        si.recommended_replacement_date, s.station_name, i.instrument_name
        FROM station_instruments si JOIN stations s ON s.station_id = si.station_id
        JOIN instruments i ON i.instrument_id = si.instrument_id
        WHERE si.recommended_calibration_date IS NOT NULL OR si.recommended_replacement_date IS NOT NULL""")
    for item in cursor.fetchall():
        for kind, due in (("calibration", item["recommended_calibration_date"]),
                          ("replacement", item["recommended_replacement_date"])):
            alert = instrument_due_alert(due, today)
            if alert["level"] == "none":
                continue
            cursor.execute("""INSERT IGNORE INTO instrument_alert_deliveries
                (station_instrument_id, user_id, alert_kind, alert_month)
                VALUES (%s, %s, %s, %s)""",
                (item["station_instrument_id"], user["user_id"], kind, month_start))
            if cursor.rowcount:
                cursor.execute("""INSERT INTO notifications
                    (user_id, notification_type, title, message, related_record_type, related_record_id)
                    VALUES (%s, %s, %s, %s, %s, %s)""",
                    (user["user_id"], "instrument_due", f"Instrument {kind} {alert['label'].lower()}",
                     f"{item['instrument_name']} at {item['station_name']}: {kind} {alert['label'].lower()} ({due}).",
                     "station_instrument", item["station_instrument_id"]))


@app.get("/notifications")
def get_notifications(
    unread_only: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    page: int = Query(default=1, ge=1),
    user=Depends(require_password_change_complete),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        if user["department"] == MAINTENANCE_ROLE:
            issue_instrument_alerts(cursor, user)
        connection.commit()
        query = """
            SELECT notification_id, notification_type, title, message,
                related_record_type, related_record_id, is_read, created_at
            FROM notifications WHERE user_id = %s
        """
        parameters = [user["user_id"]]
        if unread_only:
            query += " AND is_read = FALSE"
        query += " ORDER BY created_at DESC, notification_id DESC LIMIT %s OFFSET %s"
        parameters.extend([limit, (page - 1) * limit])
        cursor.execute(query, tuple(parameters))
        items = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) AS total FROM notifications WHERE user_id = %s AND is_read = FALSE", (user["user_id"],))
        unread_count = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM notifications WHERE user_id = %s" +
                       (" AND is_read = FALSE" if unread_only else ""), (user["user_id"],))
        total = cursor.fetchone()["total"]
        return {"items": items, "unread_count": unread_count, "total": total, "page": page,
                "total_pages": max(1, math.ceil(total / limit))}
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/data-counts")
def get_data_counts(
    month_from: str | None = None, month_to: str | None = None,
    station_category: str | None = None, station_categories: str | None = None,
    user=Depends(require_password_change_complete),
):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        query = """SELECT data_count_id, station_category,
            DATE_FORMAT(record_month, '%Y-%m') AS record_month,
            record_count, notes, recorded_by_username, updated_at
            FROM monthly_category_data_counts"""
        conditions, params = [], []
        selected_categories = [value.strip() for value in station_categories.split("|") if value.strip()] if station_categories else []
        if selected_categories:
            validate_station_categories(cursor, selected_categories)
            conditions.append(f"station_category IN ({', '.join(['%s'] * len(selected_categories))})")
            params.extend(selected_categories)
        elif station_category:
            conditions.append("station_category = %s")
            params.append(station_category)
        if month_from:
            conditions.append("record_month >= %s")
            params.append(month_from + "-01")
        if month_to:
            conditions.append("record_month <= %s")
            params.append(month_to + "-01")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY record_month DESC, station_category"
        cursor.execute(query, tuple(params))
        items = cursor.fetchall()
        for row in items:
            row["station_categories"] = [row["station_category"]]
            row["entry_type"] = "single"
        combined_query = """SELECT data_count_id, category_key,
            DATE_FORMAT(record_month, '%Y-%m') AS record_month,
            record_count, notes, recorded_by_username, updated_at
            FROM monthly_combined_data_counts"""
        combined_conditions, combined_params = [], []
        if month_from:
            combined_conditions.append("record_month >= %s")
            combined_params.append(month_from + "-01")
        if month_to:
            combined_conditions.append("record_month <= %s")
            combined_params.append(month_to + "-01")
        if combined_conditions:
            combined_query += " WHERE " + " AND ".join(combined_conditions)
        cursor.execute(combined_query, tuple(combined_params))
        for row in cursor.fetchall():
            categories = row.pop("category_key").split("|")
            if selected_categories and not set(categories).intersection(selected_categories):
                continue
            if not selected_categories and station_category and station_category not in categories:
                continue
            row["station_categories"] = categories
            row["station_category"] = " + ".join(categories)
            row["entry_type"] = "combined"
            items.append(row)
        items.sort(key=lambda row: (row["record_month"], row["station_category"]), reverse=True)
        by_month = {}
        for row in items:
            by_month[row["record_month"]] = by_month.get(row["record_month"], 0) + row["record_count"]
        by_fiscal_year, by_fiscal_quarter = fiscal_totals_by_month(by_month)
        return {"items": items, "summary": {
            "entries": len(items), "records": sum(row["record_count"] for row in items),
            "categories": len({category for row in items for category in row["station_categories"]}),
            "by_category": {category: sum(row["record_count"] for row in items if row["station_category"] == category)
                            for category in sorted({row["station_category"] for row in items})},
            "by_month": dict(sorted(by_month.items())),
            "by_fiscal_year": dict(sorted(by_fiscal_year.items())),
            "by_fiscal_quarter": dict(sorted(by_fiscal_quarter.items())),
        }}
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.post("/data-counts", status_code=201)
def save_data_count(item: MonthlyDataCount, user=Depends(require_data_operations_writer)):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor()
        validate_station_categories(cursor, [item.station_category])
        cursor.execute("SELECT data_count_id FROM monthly_category_data_counts WHERE station_category = %s AND record_month = %s",
                       (item.station_category, item.record_month + "-01"))
        existing = cursor.fetchone()
        before = audit_snapshot(connection, "category_data_counts", existing[0]) if existing else {}
        cursor.execute("SELECT category_key FROM monthly_combined_data_counts WHERE record_month = %s",
                       (item.record_month + "-01",))
        if any(item.station_category in key.split("|") for (key,) in cursor.fetchall()):
            raise HTTPException(status_code=409, detail="This category is already included in a combined count for this month")
        cursor.execute("""INSERT INTO monthly_category_data_counts
            (station_category, record_month, record_count, notes, recorded_by_user_id, recorded_by_username)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE record_count = VALUES(record_count), notes = VALUES(notes),
                recorded_by_user_id = VALUES(recorded_by_user_id),
                recorded_by_username = VALUES(recorded_by_username)""",
            (item.station_category, item.record_month + "-01", item.record_count,
             item.notes, user["user_id"], user["username"]))
        cursor.execute("SELECT data_count_id FROM monthly_category_data_counts WHERE station_category = %s AND record_month = %s",
                       (item.station_category, item.record_month + "-01"))
        data_count_id = cursor.fetchone()[0]
        record_entity_edit(connection, "category_data_counts", data_count_id, before,
                           audit_snapshot(connection, "category_data_counts", data_count_id), user)
        connection.commit()
        return {"message": "Monthly data count saved"}
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.post("/data-counts/combined", status_code=201)
def save_combined_data_count(item: CombinedDataCount, user=Depends(require_data_operations_writer)):
    categories = sorted(item.station_categories)
    if len(categories) != len(set(categories)):
        raise HTTPException(status_code=400, detail="Choose each station category only once")
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor()
        validate_station_categories(cursor, categories)
        month = item.record_month + "-01"
        cursor.execute("SELECT station_category FROM monthly_category_data_counts WHERE record_month = %s", (month,))
        existing = {category for (category,) in cursor.fetchall()}
        cursor.execute("SELECT category_key FROM monthly_combined_data_counts WHERE record_month = %s", (month,))
        existing.update(category for (key,) in cursor.fetchall() for category in key.split("|"))
        overlap = existing.intersection(categories)
        if overlap:
            raise HTTPException(status_code=409, detail=f"Already counted this month: {', '.join(sorted(overlap))}. Edit or delete the existing entry first.")
        cursor.execute("""INSERT INTO monthly_combined_data_counts
            (category_key, record_month, record_count, notes, recorded_by_user_id, recorded_by_username)
            VALUES (%s, %s, %s, %s, %s, %s)""",
            ("|".join(categories), month, item.record_count, item.notes, user["user_id"], user["username"]))
        data_count_id = cursor.lastrowid
        record_entity_edit(connection, "combined_data_counts", data_count_id, {},
                           audit_snapshot(connection, "combined_data_counts", data_count_id), user)
        connection.commit()
        return {"saved": 1, "total": item.record_count}
    except Exception:
        connection.rollback()
        raise
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.put("/data-counts/combined/{data_count_id}")
def update_combined_data_count(data_count_id: int, item: CombinedDataCount,
                               user=Depends(require_data_operations_writer)):
    categories = sorted(item.station_categories)
    if len(categories) != len(set(categories)):
        raise HTTPException(status_code=400, detail="Choose each station category only once")
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor()
        validate_station_categories(cursor, categories)
        before = audit_snapshot(connection, "combined_data_counts", data_count_id)
        month = item.record_month + "-01"
        cursor.execute("SELECT data_count_id FROM monthly_combined_data_counts WHERE data_count_id = %s", (data_count_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Data count not found")
        cursor.execute("SELECT station_category FROM monthly_category_data_counts WHERE record_month = %s", (month,))
        existing = {category for (category,) in cursor.fetchall()}
        cursor.execute("SELECT category_key FROM monthly_combined_data_counts WHERE record_month = %s AND data_count_id <> %s",
                       (month, data_count_id))
        existing.update(category for (key,) in cursor.fetchall() for category in key.split("|"))
        overlap = existing.intersection(categories)
        if overlap:
            raise HTTPException(status_code=409, detail=f"Already counted this month: {', '.join(sorted(overlap))}")
        cursor.execute("""UPDATE monthly_combined_data_counts SET category_key = %s, record_month = %s,
            record_count = %s, notes = %s, recorded_by_user_id = %s, recorded_by_username = %s
            WHERE data_count_id = %s""",
            ("|".join(categories), month, item.record_count, item.notes,
             user["user_id"], user["username"], data_count_id))
        record_entity_edit(connection, "combined_data_counts", data_count_id, before,
                           audit_snapshot(connection, "combined_data_counts", data_count_id), user)
        connection.commit()
        return {"saved": 1, "total": item.record_count}
    except Exception:
        connection.rollback()
        raise
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.delete("/data-counts/combined/{data_count_id}", status_code=204)
def delete_combined_data_count(data_count_id: int, user=Depends(require_data_operations_writer)):
    connection = get_connection()
    try:
        archive_deleted_item(connection, "combined_data_counts", data_count_id, user)
        cursor = connection.cursor()
        cursor.execute("DELETE FROM monthly_combined_data_counts WHERE data_count_id = %s", (data_count_id,))
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="Data count not found")
        connection.commit()
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.delete("/data-counts/{data_count_id}", status_code=204)
def delete_data_count(data_count_id: int, user=Depends(require_data_operations_writer)):
    connection = get_connection()
    try:
        archive_deleted_item(connection, "category_data_counts", data_count_id, user)
        cursor = connection.cursor()
        cursor.execute("DELETE FROM monthly_category_data_counts WHERE data_count_id = %s", (data_count_id,))
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="Data count not found")
        connection.commit()
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.get("/instrument-status-summary")
def get_instrument_status_summary(
    station_id: str | None = None, district: str | None = None,
    instrument_id: str | None = None, search: str | None = None,
    user=Depends(require_password_change_complete),
):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        query = """SELECT si.station_instrument_id, si.station_id, s.station_code,
            s.station_name, s.district, i.instrument_id, i.instrument_name,
            si.status, si.installation_date, si.calibration_date, si.replacement_date,
            si.recommended_calibration_date, si.recommended_replacement_date
            FROM station_instruments si JOIN stations s ON s.station_id = si.station_id
            JOIN instruments i ON i.instrument_id = si.instrument_id"""
        conditions, params = [], []
        if user["department"] in ASSIGNED_STATION_ROLES:
            conditions.append("EXISTS (SELECT 1 FROM user_station_assignments a WHERE a.station_id = si.station_id AND a.user_id = %s)")
            params.append(user["user_id"])
        add_filter_condition(conditions, params, "si.station_id", station_id)
        add_filter_condition(conditions, params, "s.district", district)
        add_filter_condition(conditions, params, "si.instrument_id", instrument_id)
        if search:
            conditions.append("(s.station_code LIKE %s OR s.station_name LIKE %s OR s.district LIKE %s OR i.instrument_name LIKE %s)")
            params.extend([f"%{search}%"] * 4)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY s.station_name, i.instrument_name"
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        today = date.today()
        counts = {name: 0 for name in ("Operational", "Needs Calibration", "Needs Replacement", "Under Maintenance", "Inactive")}
        for row in rows:
            status = effective_instrument_status(
                row["status"], row["recommended_calibration_date"],
                row["recommended_replacement_date"], today
            )
            row["effective_status"] = status
            row["calibration_alert"] = instrument_due_alert(row["recommended_calibration_date"], today)
            row["replacement_alert"] = instrument_due_alert(row["recommended_replacement_date"], today)
            counts[status] = counts.get(status, 0) + 1
        return {"items": rows, "summary": {"total": len(rows), "stations": len({row["station_id"] for row in rows}), "statuses": counts,
            "calibration_alerts": sum(row["calibration_alert"]["level"] != "none" for row in rows),
            "replacement_alerts": sum(row["replacement_alert"]["level"] != "none" for row in rows)}}
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.put("/maintenance-frequencies")
def set_maintenance_frequencies(item: MaintenanceFrequency, user=Depends(require_it)):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor()
        validate_station_categories(cursor, [item.station_category])
        audit_id = f"{item.fiscal_start_year}|{item.station_category}"
        before = audit_snapshot(connection, "maintenance_frequencies", audit_id) or {}
        cursor.execute("""INSERT INTO category_maintenance_targets
            (station_category, fiscal_start_year, cadence, target_visits, updated_by_user_id)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE cadence = VALUES(cadence), target_visits = VALUES(target_visits),
                updated_by_user_id = VALUES(updated_by_user_id)""",
            (item.station_category, item.fiscal_start_year, item.cadence,
             item.target_visits, user["user_id"]))
        record_entity_edit(connection, "maintenance_frequencies", audit_id, before,
                           audit_snapshot(connection, "maintenance_frequencies", audit_id), user)
        connection.commit()
        return {"message": "Category maintenance target saved"}
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.get("/maintenance-frequencies")
def list_maintenance_frequencies(fiscal_start_year: int = Query(ge=2020, le=2100),
                                 user=Depends(require_password_change_complete)):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""SELECT station_category, fiscal_start_year, cadence, target_visits, updated_at
            FROM category_maintenance_targets WHERE fiscal_start_year = %s ORDER BY station_category""",
            (fiscal_start_year,))
        return cursor.fetchall()
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.delete("/maintenance-frequencies/{station_category}", status_code=204)
def delete_maintenance_frequency(station_category: str, fiscal_start_year: int,
                                 user=Depends(require_it)):
    connection = get_connection()
    try:
        archive_deleted_item(connection, "maintenance_frequencies", station_category, user,
                             fiscal_start_year=fiscal_start_year)
        cursor = connection.cursor()
        cursor.execute("DELETE FROM category_maintenance_targets WHERE station_category = %s AND fiscal_start_year = %s",
                       (station_category, fiscal_start_year))
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="Target not found")
        connection.commit()
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.get("/maintenance-summary")
def get_maintenance_summary(
    fiscal_start_year: int = Query(ge=2020, le=2100), quarter: int | None = Query(default=None, ge=1, le=4),
    station_id: str | None = None, district: str | None = None,
    station_category: str | None = None, status: str | None = None,
    user=Depends(require_password_change_complete),
):
    date_from, date_to = fiscal_period(fiscal_start_year, quarter)
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        query = """SELECT s.station_id, s.station_code, s.station_name, s.district,
            s.station_category, t.cadence, t.target_visits
            FROM stations s LEFT JOIN category_maintenance_targets t
            ON t.station_category = s.station_category AND t.fiscal_start_year = %s"""
        conditions, params = [], [fiscal_start_year]
        if user["department"] in ASSIGNED_STATION_ROLES:
            conditions.append("EXISTS (SELECT 1 FROM user_station_assignments a WHERE a.station_id = s.station_id AND a.user_id = %s)")
            params.append(user["user_id"])
        add_filter_condition(conditions, params, "s.station_id", station_id)
        add_filter_condition(conditions, params, "s.district", district)
        add_filter_condition(conditions, params, "s.station_category", station_category)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY s.district, s.station_name"
        cursor.execute(query, tuple(params))
        stations = cursor.fetchall()
        cursor.execute("""SELECT maintenance_id, station_id, maintenance_date
            FROM maintenance_records WHERE maintenance_date <= %s
            ORDER BY maintenance_date, maintenance_id""", (date.today(),))
        history_by_station = {}
        for record in cursor.fetchall():
            history_by_station.setdefault(record["station_id"], []).append(record)
        counts = {"maintained": 0, "not_maintained": 0, "pending": 0, "unconfigured": 0}
        visible = []
        for station in stations:
            station.update(maintenance_schedule_status(
                history_by_station.get(station["station_id"], []), fiscal_start_year,
                quarter, station["cadence"],
                station["target_visits"], date.today()))
            if not matches_filter(station["status"], status):
                continue
            key = "unconfigured" if station["status"] == "Not configured" else station["status"].lower().replace(" ", "_")
            counts[key] += 1
            visible.append(station)
        return {"items": visible, "summary": {"total": len(visible), **counts},
                "period": {"from": date_from, "to": date_to, "fiscal_year": f"{fiscal_start_year}/{fiscal_start_year+1}", "quarter": quarter}}
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.get("/maintenance-summary/export")
def export_maintenance_summary(
    format: Literal["csv", "pdf"], fiscal_start_year: int = Query(ge=2020, le=2100),
    quarter: int | None = Query(default=None, ge=1, le=4),
    station_id: str | None = None, district: str | None = None,
    station_category: str | None = None, status: str | None = None,
    user=Depends(require_password_change_complete),
):
    result = get_maintenance_summary(fiscal_start_year, quarter, station_id, district,
                                     station_category, status, user)
    headers = ["District", "Station ID", "Station", "Category", "Schedule status",
               "Schedule progress (%)", "Completed toward target",
               "Q1", "Q2", "Q3", "Q4", "Schedule detail", "Maintenance entries", "Target", "Frequency",
               "Latest maintenance date", "Maintenance dates in selected period"]
    rows = [[item["district"], item["station_code"], item["station_name"],
             item["station_category"], item["status"], item["progress_percent"],
             item["visits_toward_target"],
             *[f'{quarter["count"] if quarter["count"] is not None else ""} ({quarter["state"]})'
               for quarter in item["quarter_progress"]], item["status_detail"],
             item["visits_in_period"], item["target_for_period"], item["cadence"],
             item["last_maintenance_date"], ", ".join(map(str, item["maintenance_dates"]))]
            for item in result["items"]]
    filename = f"maintenance-summary-{fiscal_start_year}-{fiscal_start_year + 1}"
    if quarter:
        filename += f"-Q{quarter}"
    if format == "csv":
        return csv_download(f"{filename}.csv", headers, rows)
    return maintenance_summary_pdf_download(f"{filename}.pdf", result, user["username"])


def maintenance_summary_pdf_download(filename, result, username):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        from xml.sax.saxutils import escape
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="PDF support is not installed") from exc
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=22,
                                 rightMargin=22, topMargin=22, bottomMargin=22)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("MaintenanceCell", parent=styles["Normal"], fontSize=6.5, leading=8)
    header = ParagraphStyle("MaintenanceHeader", parent=body, textColor=colors.white,
                            fontName="Helvetica-Bold")
    period = result["period"]
    heading = f"Maintenance summary | FY {period['fiscal_year']}"
    if period["quarter"]:
        heading += f" Q{period['quarter']}"
    story = [Paragraph(heading, styles["Title"]),
             Paragraph(f"Period: {period['from']} to {period['to']} | Generated by {escape(username)} on {date.today()}", styles["Normal"]),
             Spacer(1, 10)]
    names = ["District", "Station", "Category", "Status", "Q1", "Q2", "Q3", "Q4",
             "Visits / target", "Frequency", "Latest date", "Dates in period"]
    table_data = [[Paragraph(name, header) for name in names]]
    for item in result["items"]:
        progress = (f'{item["status"]} | {item["progress_percent"]}% '
                    f'({item["visits_toward_target"]}/{item["target_for_period"]})'
                    if item["progress_percent"] is not None else item["status"])
        values = [item["district"], item["station_name"], item["station_category"],
                  progress,
                  *[quarter["count"] if quarter["count"] is not None else ""
                    for quarter in item["quarter_progress"]],
                  f"{item['visits_in_period']} / {item['target_for_period'] or '-'}",
                  item["cadence"] or "-", item["last_maintenance_date"] or "-",
                  ", ".join(map(str, item["maintenance_dates"])) or "-"]
        table_data.append([Paragraph(escape(str(value)), body) for value in values])
    table = Table(table_data, colWidths=[54, 82, 86, 84, 27, 27, 27, 27, 56, 57, 65, 143], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173f63")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c4d1d7")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f7f8")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    quarter_colors = {"good": "#c3efd1", "missed": "#ffc9c4", "pending": "#fff2a7",
                      "neutral": "#eef2f3", "unconfigured": "#e9eff2"}
    for row_number, item in enumerate(result["items"], start=1):
        for index, quarter in enumerate(item["quarter_progress"], start=4):
            table.setStyle(TableStyle([("BACKGROUND", (index, row_number), (index, row_number),
                                        colors.HexColor(quarter_colors[quarter["state"]]))]))
    story.append(table)
    document.build(story)
    output.seek(0)
    return StreamingResponse(output, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/visitor-categories")
def list_visitor_categories(user=Depends(require_password_change_complete)):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT category_id, name FROM visitor_categories ORDER BY name")
        return cursor.fetchall()
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.post("/visitor-categories", status_code=201)
def add_visitor_category(item: VisitorCategory, user=Depends(require_it)):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor()
        cursor.execute("INSERT INTO visitor_categories (name) VALUES (%s)", (item.name.strip(),))
        connection.commit()
        return {"category_id": cursor.lastrowid}
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.put("/visitor-categories/{category_id}")
def update_visitor_category(category_id: int, item: VisitorCategory, user=Depends(require_it)):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        before = audit_snapshot(connection, "visitor_categories", category_id)
        cursor = connection.cursor()
        cursor.execute("UPDATE visitor_categories SET name = %s WHERE category_id = %s", (item.name.strip(), category_id))
        if not cursor.rowcount: raise HTTPException(status_code=404, detail="Category not found")
        record_entity_edit(connection, "visitor_categories", category_id, before,
                           audit_snapshot(connection, "visitor_categories", category_id), user)
        connection.commit()
        return {"message": "Category updated"}
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.delete("/visitor-categories/{category_id}", status_code=204)
def delete_visitor_category(category_id: int, user=Depends(require_it)):
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT 1 FROM station_visitors WHERE category_id = %s LIMIT 1", (category_id,))
        if cursor.fetchone(): raise HTTPException(status_code=409, detail="Category is used by visitor records")
        archive_deleted_item(connection, "visitor_categories", category_id, user)
        cursor.execute("DELETE FROM visitor_categories WHERE category_id = %s", (category_id,))
        if not cursor.rowcount: raise HTTPException(status_code=404, detail="Category not found")
        connection.commit()
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.get("/station-visitors")
def list_station_visitors(month_from: str | None = None, month_to: str | None = None,
                          station_id: str | None = None, district: str | None = None,
                          category_id: str | None = None, institution: str | None = None,
                          user=Depends(require_password_change_complete)):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        query = """SELECT v.visitor_id, v.station_id, s.station_code, s.station_name, s.district,
            v.period_start AS visit_date, v.period_end AS legacy_period_end,
            v.institution, v.mission, v.category_id, c.name AS category,
            v.visitor_count, v.recorded_by_user_id, v.recorded_by_username, v.updated_at
            FROM station_visitors v JOIN stations s ON s.station_id = v.station_id
            JOIN visitor_categories c ON c.category_id = v.category_id"""
        conditions, params = [], []
        if user["department"] in ASSIGNED_STATION_ROLES:
            conditions.append("EXISTS (SELECT 1 FROM user_station_assignments a WHERE a.station_id = v.station_id AND a.user_id = %s)")
            params.append(user["user_id"])
        add_filter_condition(conditions, params, "v.station_id", station_id)
        add_filter_condition(conditions, params, "s.district", district)
        add_filter_condition(conditions, params, "v.category_id", category_id)
        if institution: conditions.append("v.institution LIKE %s"); params.append(f"%{institution}%")
        if month_from: conditions.append("v.period_start >= %s"); params.append(month_from + "-01")
        if month_to:
            year, month = map(int, month_to.split("-"))
            end = date(year + (month == 12), month % 12 + 1, 1)
            conditions.append("v.period_start < %s"); params.append(end)
        if conditions: query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY v.period_start DESC, v.visitor_id DESC"
        cursor.execute(query, tuple(params))
        items = cursor.fetchall()
        return {"items": items, "summary": {"visitors": sum(row["visitor_count"] for row in items),
                "visits": len(items), "stations": len({row["station_id"] for row in items})}}
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


def save_visitor_record(item, user, visitor_id=None):
    institution = item.institution.strip()
    if not institution:
        raise HTTPException(status_code=400, detail="Enter the institution or school")
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor()
        ensure_user_can_access_station(cursor, user, item.station_id)
        cursor.execute("SELECT 1 FROM stations WHERE station_id = %s", (item.station_id,))
        if not cursor.fetchone(): raise HTTPException(status_code=404, detail="Station not found")
        cursor.execute("SELECT 1 FROM visitor_categories WHERE category_id = %s", (item.category_id,))
        if not cursor.fetchone(): raise HTTPException(status_code=404, detail="Category not found")
        if visitor_id:
            before = audit_snapshot(connection, "station_visitors", visitor_id)
            cursor.execute("SELECT recorded_by_user_id FROM station_visitors WHERE visitor_id = %s", (visitor_id,))
            owner = cursor.fetchone()
            if not owner: raise HTTPException(status_code=404, detail="Visitor record not found")
            if user["department"] != "Admin" and owner[0] != user["user_id"]:
                raise HTTPException(status_code=403, detail="Only the recorder can edit this entry")
            cursor.execute("""UPDATE station_visitors SET station_id=%s, period_start=%s, period_end=%s,
                institution=%s, mission=%s, category_id=%s, visitor_count=%s WHERE visitor_id=%s""",
                (item.station_id, item.visit_date, item.visit_date, institution,
                 item.mission.strip(), item.category_id, item.visitor_count, visitor_id))
        else:
            cursor.execute("""INSERT INTO station_visitors (station_id, period_start, period_end,
                institution, mission, category_id, visitor_count, recorded_by_user_id, recorded_by_username)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (item.station_id, item.visit_date, item.visit_date, institution,
                 item.mission.strip(), item.category_id, item.visitor_count, user["user_id"], user["username"]))
            visitor_id = cursor.lastrowid
            before = {}
        record_entity_edit(connection, "station_visitors", visitor_id, before,
                           audit_snapshot(connection, "station_visitors", visitor_id), user)
        connection.commit()
        return {"message": "Visitor record saved"}
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.post("/station-visitors", status_code=201)
def add_station_visitor(item: StationVisitor, user=Depends(require_password_change_complete)):
    if user["department"] in READ_ONLY_ALL_ROLES:
        raise HTTPException(status_code=403, detail="Visitor entry access is not allowed")
    return save_visitor_record(item, user)


@app.put("/station-visitors/{visitor_id}")
def edit_station_visitor(visitor_id: int, item: StationVisitor, user=Depends(require_password_change_complete)):
    if user["department"] in READ_ONLY_ALL_ROLES:
        raise HTTPException(status_code=403, detail="Visitor entry access is not allowed")
    return save_visitor_record(item, user, visitor_id)


@app.delete("/station-visitors/{visitor_id}", status_code=204)
def delete_station_visitor(visitor_id: int, user=Depends(require_password_change_complete)):
    if user["department"] in READ_ONLY_ALL_ROLES:
        raise HTTPException(status_code=403, detail="Visitor entry access is not allowed")
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT station_id, recorded_by_user_id FROM station_visitors WHERE visitor_id = %s", (visitor_id,))
        row = cursor.fetchone()
        if not row: raise HTTPException(status_code=404, detail="Visitor record not found")
        ensure_user_can_access_station(cursor, user, row[0])
        if user["department"] != "Admin" and row[1] != user["user_id"]:
            raise HTTPException(status_code=403, detail="Only the recorder can delete this entry")
        archive_deleted_item(connection, "station_visitors", visitor_id, user)
        cursor.execute("DELETE FROM station_visitors WHERE visitor_id = %s", (visitor_id,))
        connection.commit()
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


def station_volunteer_rows(user, province=None, district=None, sector=None,
                           station_id=None, account_type=None, search=None,
                           sort_by="station_name", sort_order="asc"):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        query = """SELECT v.volunteer_id, v.station_id, s.station_code, s.station_name,
            s.province, s.district, s.sector, v.volunteer_name, v.volunteer_identifier,
            v.account_type, v.account_name, v.account_number, v.mobile_phone,
            v.recorded_by_user_id, v.recorded_by_username, v.created_at, v.updated_at
            FROM station_volunteers v JOIN stations s ON s.station_id = v.station_id"""
        conditions, params = [], []
        for column, value in (("s.province", province), ("s.district", district),
                              ("s.sector", sector), ("v.station_id", station_id)):
            add_filter_condition(conditions, params, column, value)
        if account_type:
            conditions.append("v.account_type LIKE %s")
            params.append(f"%{account_type}%")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        return filter_sort_collection(rows, search,
            ["station_code", "station_name", "province", "district", "sector",
             "volunteer_name", "volunteer_identifier", "account_type", "account_name",
             "account_number", "mobile_phone"], sort_by, sort_order,
            {name: name for name in ("station_code", "station_name", "province", "district",
             "sector", "volunteer_name", "volunteer_identifier", "account_type",
             "account_name", "account_number", "mobile_phone", "updated_at")})
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.get("/station-volunteers")
def list_station_volunteers(
    province: str | None = None, district: str | None = None,
    sector: str | None = None, station_id: str | None = None,
    account_type: str | None = None, search: str | None = Query(default=None, max_length=100),
    sort_by: str = "station_name", sort_order: Literal["asc", "desc"] = "asc",
    page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=10, le=100),
    user=Depends(require_volunteer_data_access),
):
    rows = station_volunteer_rows(user, province, district, sector, station_id,
                                  account_type, search, sort_by, sort_order)
    return paginate_collection(rows, page, page_size,
        {"volunteers": len(rows), "stations": len({row["station_id"] for row in rows})})


@app.get("/station-volunteers/export")
def export_station_volunteers(
    format: Literal["csv", "pdf"], province: str | None = None,
    district: str | None = None, sector: str | None = None,
    station_id: str | None = None, account_type: str | None = None,
    search: str | None = Query(default=None, max_length=100),
    sort_by: str = "station_name", sort_order: Literal["asc", "desc"] = "asc",
    user=Depends(require_volunteer_data_access),
):
    rows = station_volunteer_rows(user, province, district, sector, station_id,
                                  account_type, search, sort_by, sort_order)
    headers = ["Station ID", "Station name", "Province", "District", "Sector",
               "Volunteer name", "ID", "Account type", "Account name", "Account number", "Mobile phone"]
    values = [[row[key] for key in ("station_code", "station_name", "province", "district",
        "sector", "volunteer_name", "volunteer_identifier", "account_type",
        "account_name", "account_number", "mobile_phone")] for row in rows]
    if format == "csv":
        return csv_download("station-volunteers.csv", headers, values)
    return pdf_download("station-volunteers.pdf", "Volunteers at stations", headers,
                        values, user["username"])


def save_station_volunteer(item, user, volunteer_id=None):
    if user["department"] in READ_ONLY_ALL_ROLES:
        raise HTTPException(status_code=403, detail="Volunteer entry access is not allowed")
    fields = [item.volunteer_name.strip(), item.volunteer_identifier.strip(),
              item.account_type.strip(), item.account_name.strip(),
              item.account_number.strip(), item.mobile_phone.strip()]
    if not all(fields):
        raise HTTPException(status_code=400, detail="Complete every volunteer field")
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor()
        cursor.execute("SELECT 1 FROM stations WHERE station_id = %s", (item.station_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Station not found")
        if volunteer_id is not None:
            cursor.execute("SELECT recorded_by_user_id FROM station_volunteers WHERE volunteer_id = %s", (volunteer_id,))
            owner = cursor.fetchone()
            if not owner:
                raise HTTPException(status_code=404, detail="Volunteer record not found")
            if user["department"] != "Admin" and owner[0] != user["user_id"]:
                raise HTTPException(status_code=403, detail="Only the recorder can edit this entry")
            before = audit_snapshot(connection, "station_volunteers", volunteer_id)
            statement = """UPDATE station_volunteers SET station_id=%s, volunteer_name=%s,
                volunteer_identifier=%s, account_type=%s, account_name=%s,
                account_number=%s, mobile_phone=%s WHERE volunteer_id=%s"""
            params = (item.station_id, *fields, volunteer_id)
        else:
            before = {}
            statement = """INSERT INTO station_volunteers (station_id, volunteer_name,
                volunteer_identifier, account_type, account_name, account_number,
                mobile_phone, recorded_by_user_id, recorded_by_username)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
            params = (item.station_id, *fields, user["user_id"], user["username"])
        try:
            cursor.execute(statement, params)
        except IntegrityError as exc:
            raise HTTPException(status_code=409,
                detail="This volunteer ID is already registered at the selected station") from exc
        if volunteer_id is None:
            volunteer_id = cursor.lastrowid
        record_entity_edit(connection, "station_volunteers", volunteer_id, before,
                           audit_snapshot(connection, "station_volunteers", volunteer_id), user)
        connection.commit()
        return {"volunteer_id": volunteer_id, "message": "Volunteer record saved"}
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.post("/station-volunteers", status_code=201)
def add_station_volunteer(item: StationVolunteer, user=Depends(require_volunteer_data_access)):
    return save_station_volunteer(item, user)


@app.put("/station-volunteers/{volunteer_id}")
def edit_station_volunteer(volunteer_id: int, item: StationVolunteer,
                           user=Depends(require_volunteer_data_access)):
    return save_station_volunteer(item, user, volunteer_id)


@app.delete("/station-volunteers/{volunteer_id}", status_code=204)
def delete_station_volunteer(volunteer_id: int, user=Depends(require_volunteer_data_access)):
    if user["department"] in READ_ONLY_ALL_ROLES:
        raise HTTPException(status_code=403, detail="Volunteer entry access is not allowed")
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor()
        cursor.execute("SELECT recorded_by_user_id FROM station_volunteers WHERE volunteer_id = %s", (volunteer_id,))
        owner = cursor.fetchone()
        if not owner:
            raise HTTPException(status_code=404, detail="Volunteer record not found")
        if user["department"] != "Admin" and owner[0] != user["user_id"]:
            raise HTTPException(status_code=403, detail="Only the recorder can delete this entry")
        archive_deleted_item(connection, "station_volunteers", volunteer_id, user)
        cursor.execute("DELETE FROM station_volunteers WHERE volunteer_id = %s", (volunteer_id,))
        connection.commit()
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.get("/activity")
def get_operation_activity(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    user=Depends(require_it),
):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute("SELECT COUNT(*) AS total FROM operation_activity")
            total = cursor.fetchone()["total"]
            cursor.execute("""SELECT activity_id, actor_name, action, resource, record_id,
                request_path, affected_records, created_at FROM operation_activity
                ORDER BY created_at DESC, activity_id DESC LIMIT %s OFFSET %s""",
                (page_size, (page - 1) * page_size))
            return {"items": cursor.fetchall(), "page": page, "total": total,
                    "total_pages": max(1, math.ceil(total / page_size))}
        finally:
            cursor.close()
    finally:
        connection.close()


@app.put("/notifications/read-all")
def mark_all_notifications_read(user=Depends(require_password_change_complete)):
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("UPDATE notifications SET is_read = TRUE WHERE user_id = %s AND is_read = FALSE", (user["user_id"],))
        connection.commit()
        return {"message": "All notifications marked as read"}
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.put("/notifications/{notification_id:int}/read")
def mark_notification_read(notification_id: int, user=Depends(require_password_change_complete)):
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("UPDATE notifications SET is_read = TRUE WHERE notification_id = %s AND user_id = %s", (notification_id, user["user_id"]))
        if cursor.rowcount == 0:
            cursor.execute("SELECT notification_id FROM notifications WHERE notification_id = %s AND user_id = %s", (notification_id, user["user_id"]))
            if cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail="Notification not found")
        connection.commit()
        return {"message": "Notification marked as read"}
    except HTTPException:
        raise
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/users")
def get_users(
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=25, ge=10, le=100),
    search: str | None = Query(default=None, max_length=100),
    sort_by: str = "full_name",
    sort_order: Literal["asc", "desc"] = "asc",
    _user=Depends(require_user_directory_access),
):
    try:
        connection = get_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT user_id, full_name, username, email, department,
                must_change_password, is_active, created_at
            FROM users
            ORDER BY full_name, username
            """
        )
        users = cursor.fetchall()
        cursor.execute(
            """
            SELECT user_station_assignments.user_id,
                user_station_assignments.station_id,
                stations.station_code,
                stations.station_name
            FROM user_station_assignments
            INNER JOIN stations
                ON stations.station_id = user_station_assignments.station_id
            ORDER BY station_id
            """
        )
        assignments = {}
        for assignment in cursor.fetchall():
            assignments.setdefault(assignment["user_id"], []).append(assignment)
        for item in users:
            assigned = assignments.get(item["user_id"], [])
            item["station_ids"] = [entry["station_id"] for entry in assigned]
            item["assigned_stations"] = [
                f'{entry["station_code"]} - {entry["station_name"]}' for entry in assigned
            ]
            item["assigned_station_search"] = " ".join(item["assigned_stations"])
        users = filter_sort_collection(
            users, search,
            ["full_name", "username", "email", "department", "assigned_station_search"],
            sort_by, sort_order,
            {"full_name": "full_name", "username": "username", "email": "email", "department": "department", "assigned_stations": "assigned_station_search", "status": "is_active"},
        )
        return paginate_collection(users, page, page_size)
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.get("/users/export")
def export_users(
    format: Literal["csv", "pdf"], search: str | None = None,
    sort_by: str = "full_name", sort_order: Literal["asc", "desc"] = "asc",
    user=Depends(require_user_directory_access),
):
    items = get_users(None, 100, search, sort_by, sort_order, user)
    headers = ["Name", "Username", "Email", "Role", "Assigned stations", "Status"]
    rows = [[item["full_name"], item["username"], item["email"], item["department"], ", ".join(item["assigned_stations"]), "Active" if item["is_active"] else "Inactive"] for item in items]
    return csv_download("users.csv", headers, rows) if format == "csv" else pdf_download("users.pdf", "User Directory", headers, rows, user["username"])


@app.post("/users", status_code=201)
def add_user(user: UserCreate, _admin=Depends(require_it)):
    if user.department in ASSIGNED_STATION_ROLES and not user.station_ids:
        raise HTTPException(
            status_code=400,
            detail="Assign at least one station to this observation role",
        )
    try:
        connection = get_connection()
        user_id = insert_user(connection, user, user.department, True)
        cursor = connection.cursor()
        replace_user_station_assignments(
            cursor,
            user_id,
            user.station_ids if user.department in ASSIGNED_STATION_ROLES else [],
        )
        connection.commit()
        return {"message": "User created", "user_id": user_id}
    except HTTPException:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Username already exists") from exc
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.put("/users/{user_id}")
def update_user(
    user_id: int,
    user: UserUpdate,
    admin=Depends(require_it),
):
    if user.department in ASSIGNED_STATION_ROLES and not user.station_ids:
        raise HTTPException(
            status_code=400,
            detail="Assign at least one station to this observation role",
        )
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        before = audit_snapshot(connection, "users", user_id)
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT user_id, department, is_active FROM users WHERE user_id = %s",
            (user_id,),
        )
        existing = cursor.fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="User not found")

        removing_it_access = (
            existing["department"] == "Admin"
            and bool(existing["is_active"])
            and (user.department != "Admin" or not user.is_active)
        )
        if removing_it_access:
            cursor.execute(
                "SELECT COUNT(*) AS total FROM users "
                "WHERE department = 'Admin' AND is_active = TRUE"
            )
            if cursor.fetchone()["total"] <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="The last active administrator cannot be disabled or reassigned",
                )

        if user_id == admin["user_id"] and (
            user.department != "Admin" or not user.is_active
        ):
            raise HTTPException(
                status_code=409,
                detail="You cannot remove your own administrator access",
            )

        values = [
            user.full_name.strip(),
            user.username.strip().lower(),
            user.email.strip().lower() if user.email else None,
            user.department,
            user.is_active,
        ]
        password_sql = ""
        if user.password:
            password_sql = ", password_hash = %s, must_change_password = TRUE"
            values.append(hash_password(user.password))
        values.append(user_id)

        cursor.execute(
            f"""
            UPDATE users
            SET full_name = %s,
                username = %s,
                email = %s,
                department = %s,
                is_active = %s
                {password_sql}
            WHERE user_id = %s
            """,
            tuple(values),
        )
        replace_user_station_assignments(
            cursor,
            user_id,
            user.station_ids if user.department in ASSIGNED_STATION_ROLES else [],
        )
        after = audit_snapshot(connection, "users", user_id)
        if user.password:
            after["password_updated"] = True
        record_entity_edit(connection, "users", user_id, before, after, admin)
        connection.commit()
        return {"message": "User updated"}
    except HTTPException:
        raise
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Username already exists") from exc
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, admin=Depends(require_it)):
    if user_id == admin["user_id"]:
        raise HTTPException(status_code=409, detail="You cannot delete your own account")

    try:
        connection = get_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT department, is_active FROM users WHERE user_id = %s",
            (user_id,),
        )
        existing = cursor.fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="User not found")

        if existing["department"] == "Admin" and bool(existing["is_active"]):
            cursor.execute(
                "SELECT COUNT(*) AS total FROM users "
                "WHERE department = 'Admin' AND is_active = TRUE"
            )
            if cursor.fetchone()["total"] <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="The last active administrator cannot be deleted",
                )

        archive_deleted_item(connection, "users", user_id, admin)
        cursor.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
        connection.commit()
    except HTTPException:
        raise
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.get("/")
def home():
    return RedirectResponse(url="/app/login.html")


@app.get("/health")
def health():
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        return {"status": "ok"}
    except MySQLError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    finally:
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.get("/stations")
def get_stations(
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=25, ge=10, le=100),
    search: str | None = Query(default=None, max_length=100),
    sort_by: str = "station_name",
    sort_order: Literal["asc", "desc"] = "asc",
    date_from: date | None = None,
    date_to: date | None = None,
    station_category: str | None = None,
    user=Depends(require_password_change_complete),
    district: str | None = None,
    status: str | None = None,
):

    try:
        connection = get_connection()
        ensure_application_tables(connection)

        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT
                stations.station_id,
                station_code,
                station_name,
                CAST(latitude AS DOUBLE) AS latitude,
                CAST(longitude AS DOUBLE) AS longitude,
                CAST(altitude AS DOUBLE) AS altitude,
                province,
                district,
                sector,
                station_category,
                status,
                comment,
                action,
                stations.created_at,
                COALESCE(stations.recorded_by_username, creator.username) AS recorded_by
            FROM stations
            LEFT JOIN users AS creator
                ON creator.user_id = stations.created_by_user_id
        """
        conditions = []
        parameters = []
        if user["department"] in ASSIGNED_STATION_ROLES:
            query += """
                INNER JOIN user_station_assignments
                    ON user_station_assignments.station_id = stations.station_id
                    AND user_station_assignments.user_id = %s
            """
            parameters.append(user["user_id"])
        add_filter_condition(conditions, parameters, "stations.station_category", station_category)
        add_filter_condition(conditions, parameters, "stations.status", status)
        add_filter_condition(conditions, parameters, "stations.district", district)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY stations.station_id"
        cursor.execute(query, tuple(parameters))

        stations = cursor.fetchall()
        stations = filter_sort_collection(
            stations,
            search,
            [
                "station_code", "station_name", "province", "district",
                "sector", "station_category", "status", "comment", "action",
            ],
            sort_by,
            sort_order,
            {
                "station_code": "station_code",
                "station_name": "station_name",
                "latitude": "latitude", "longitude": "longitude", "altitude": "altitude",
                "province": "province", "district": "district", "sector": "sector",
                "station_category": "station_category",
                "status": "status",
                "comment": "comment", "action": "action", "recorded_by": "recorded_by",
                "created_at": "created_at",
            },
            date_from,
            date_to,
            "created_at",
        )
        categories = {}
        for station in stations:
            category = station["station_category"] or "Not classified"
            categories[category] = categories.get(category, 0) + 1
        operational = sum(
            station["status"] == "Operational"
            for station in stations
        )
        summary = {
            "total": len(stations),
            "operational": operational,
            "attention": len(stations) - operational,
            "under_maintenance": sum(station["status"] == "Under maintenance" for station in stations),
            "closed": sum(station["status"] == "Closed" for station in stations),
            "suspended": sum(station["status"] == "Suspended" for station in stations),
            "suspended_aws": sum(
                station["status"] == "Suspended"
                and station["station_category"] == "Automatic Weather station"
                for station in stations
            ),
            "suspended_arg": sum(
                station["status"] == "Suspended"
                and station["station_category"] == "Automatic Rain Gauge"
                for station in stations
            ),
            "categories": categories,
        }
        return paginate_collection(stations, page, page_size, summary)

    except MySQLError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {exc}"
        ) from exc

    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.get("/stations/export")
def export_stations(
    format: Literal["csv", "pdf"],
    search: str | None = None,
    sort_by: str = "station_name",
    sort_order: Literal["asc", "desc"] = "asc",
    date_from: date | None = None,
    date_to: date | None = None,
    station_category: str | None = None,
    user=Depends(require_password_change_complete),
    district: str | None = None,
    status: str | None = None,
):
    items = get_stations(
        page=None,
        page_size=100,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        date_from=date_from,
        date_to=date_to,
        station_category=station_category,
        user=user,
        district=district,
        status=status,
    )
    headers = [
        "Station ID", "Station", "Latitude", "Longitude", "Altitude",
        "Province", "District", "Sector", "Category", "Station status",
        "Comment", "Action", "Registered at", "Recorded by",
    ]
    rows = [
        [
            item["station_code"], item["station_name"], item["latitude"],
            item["longitude"], item["altitude"], item["province"],
            item["district"], item["sector"], item["station_category"],
            item["status"], item["comment"], item["action"],
            item["created_at"], item["recorded_by"],
        ]
        for item in items
    ]
    if format == "csv":
        return csv_download("stations.csv", headers, rows)
    return pdf_download("stations.pdf", "Station Registry", headers, rows, user["username"])


@app.get("/sites")
def get_sites(
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=25, ge=10, le=100),
    search: str | None = Query(default=None, max_length=100),
    sort_by: str = "site_name",
    sort_order: Literal["asc", "desc"] = "asc",
    user=Depends(require_password_change_complete),
    district: str | None = None,
    status: str | None = None,
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT stations.station_id, station_code, station_name,
                CAST(latitude AS DOUBLE) AS latitude,
                CAST(longitude AS DOUBLE) AS longitude,
                CAST(altitude AS DOUBLE) AS altitude,
                province, district, sector, station_category, status
            FROM stations
        """
        parameters = []
        if user["department"] in ASSIGNED_STATION_ROLES:
            query += """
                INNER JOIN user_station_assignments
                    ON user_station_assignments.station_id = stations.station_id
                    AND user_station_assignments.user_id = %s
            """
            parameters.append(user["user_id"])
        query += " ORDER BY latitude, longitude, altitude, station_name"
        cursor.execute(query, tuple(parameters))
        grouped = {}
        for station in cursor.fetchall():
            if not matches_filter(station["district"], district):
                continue
            coordinate_key = (
                station["latitude"], station["longitude"], station["altitude"]
            )
            grouped.setdefault(coordinate_key, []).append(station)
        sites = []
        for number, (coordinate_key, stations) in enumerate(grouped.items(), start=1):
            representative = min(
                stations,
                key=lambda item: (len(item["station_name"]), item["station_name"].casefold()),
            )
            site_name = representative["station_name"]
            for suffix in ("_AWS", "_ARG", "_WR", "_UAS"):
                if site_name.upper().endswith(suffix):
                    site_name = site_name[:-len(suffix)]
                    break
            station_labels = [
                f'{item["station_code"]} - {item["station_name"]}' for item in stations
            ]
            status_counts = {
                name: sum(item["status"] == name for item in stations)
                for name in ("Operational", "Under maintenance", "Suspended", "Closed")
            }
            sites.append({
                "site_id": number,
                "site_code": f"SITE-{number:04d}",
                "site_name": site_name.replace("_", " "),
                "latitude": coordinate_key[0],
                "longitude": coordinate_key[1],
                "altitude": coordinate_key[2],
                "province": representative["province"],
                "district": representative["district"],
                "sector": representative["sector"],
                "station_count": len(stations),
                "stations": stations,
                "status_counts": status_counts,
                "status_summary": ", ".join(
                    f"{name}: {count}" for name, count in status_counts.items() if count
                ),
                "station_search": " ".join(station_labels),
                "categories": sorted({item["station_category"] for item in stations}),
            })
        if status:
            statuses = filter_values(status)
            sites = [site for site in sites if any(site["status_counts"].get(value, 0) for value in statuses)]
        sites = filter_sort_collection(
            sites, search,
            [
                "site_code", "site_name", "province", "district", "sector",
                "station_search", "categories",
            ],
            sort_by, sort_order,
            {
                "site_code": "site_code", "site_name": "site_name",
                "latitude": "latitude", "longitude": "longitude",
                "station_count": "station_count", "altitude": "altitude",
                "province": "province", "district": "district", "sector": "sector",
                "stations": "station_search", "status_summary": "status_summary",
            },
        )
        summary = {
            "total_sites": len(sites),
            "shared_sites": sum(item["station_count"] > 1 for item in sites),
            "single_station_sites": sum(item["station_count"] == 1 for item in sites),
            "stations": sum(item["station_count"] for item in sites),
        }
        return paginate_collection(sites, page, page_size, summary)
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/sites/export")
def export_sites(
    format: Literal["csv", "pdf"],
    search: str | None = Query(default=None, max_length=100),
    sort_by: str = "site_name",
    sort_order: Literal["asc", "desc"] = "asc",
    district: str | None = None,
    status: str | None = None,
    user=Depends(require_password_change_complete),
):
    sites = get_sites(
        page=None, search=search, sort_by=sort_by,
        sort_order=sort_order, district=district, status=status, user=user,
    )
    headers = ["Site ID", "Site", "Latitude", "Longitude", "Altitude (m)",
               "Province", "District", "Sector", "Station count", "Station status"]
    rows = [[site[key] for key in ("site_code", "site_name", "latitude", "longitude",
             "altitude", "province", "district", "sector", "station_count", "status_summary")]
            for site in sites]
    if format == "csv":
        headers.append("Stations at site")
        for row, site in zip(rows, sites):
            row.append("; ".join(
                f'{station["station_code"]} - {station["station_name"]} '
                f'({station["station_category"]}, {station["status"]})'
                for station in site["stations"]
            ))
        return csv_download("sites.csv", headers, rows)
    return pdf_download("sites.pdf", "Site Registry", headers, rows, user["username"])


@app.post("/stations")
def add_station(station: Station, user=Depends(require_it)):

    try:
        connection = get_connection()
        ensure_application_tables(connection)

        cursor = connection.cursor()
        validate_station_categories(cursor, [station.station_category])

        sql = """
            INSERT INTO stations
            (
                station_code,
                station_name,
                latitude,
                longitude,
                altitude,
                province,
                district,
                sector,
                station_category,
                status,
                comment,
                action,
                created_by_user_id,
                recorded_by_username
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        cursor.execute(
            sql,
            (
                station.station_code.strip(),
                station.station_name.strip(),
                station.latitude,
                station.longitude,
                station.altitude,
                station.province.strip(),
                station.district.strip(),
                station.sector.strip(),
                station.station_category,
                station.status.strip(),
                station.comment.strip() if station.comment else None,
                station.action.strip() if station.action else None,
                user["user_id"],
                user["username"],
            )
        )

        connection.commit()

        return {
            "message": "Station added successfully"
        }

    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="A station with this Station ID already exists",
        ) from exc
    except MySQLError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {exc}"
        ) from exc

    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.post("/stations/import", status_code=201)
async def import_stations(
    request: Request,
    mode: Literal["create", "upsert", "preview"] = "create",
    user=Depends(require_it),
):
    try:
        content = (await request.body()).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV file must use UTF-8 encoding") from exc

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV file has no header row")

    headers = {(name or "").strip().lower().replace(" ", "_") for name in reader.fieldnames}
    required = {
        "station_id": {"station_id", "station_code"},
        "station_name": {"station", "station_name"},
        "category": {"category", "station_category"},
        "status": {"station_status", "operational_status", "status"},
        "latitude": {"latitude"}, "longitude": {"longitude"},
        "altitude": {"altitude"}, "province": {"province"},
        "district": {"district"}, "sector": {"sector"},
    }
    missing = [label for label, aliases in required.items() if not headers.intersection(aliases)]
    if missing:
        raise HTTPException(status_code=400, detail="CSV is missing columns: " + ", ".join(missing))

    stations = []
    station_codes = set()
    for row_number, source_row in enumerate(reader, start=2):
        if None in source_row:
            raise HTTPException(
                status_code=400,
                detail=f"CSV row {row_number}: too many columns",
            )
        row = {
            (key or "").strip().lower().replace(" ", "_"): (value or "").strip()
            for key, value in source_row.items()
        }
        imported_status = row.get("station_status") or row.get("operational_status") or row.get("status") or ""
        if imported_status == "Maintenance":
            imported_status = "Under maintenance"
        if (row.get("suspended") or "").lower() in {"1", "true", "yes"}:
            imported_status = "Suspended"
        try:
            station = Station(
                station_code=row.get("station_id") or row.get("station_code") or "",
                station_name=row.get("station_name") or row.get("station") or "",
                latitude=row.get("latitude") or "",
                longitude=row.get("longitude") or "",
                altitude=row.get("altitude") or "",
                province=row.get("province") or "",
                district=row.get("district") or "",
                sector=row.get("sector") or "",
                station_category=normalize_station_category(row.get("station_category") or row.get("category") or ""),
                status=imported_status,
                comment=row.get("comment") or None,
                action=row.get("action") or row.get("actions") or None,
            )
        except ValidationError as exc:
            first_error = exc.errors()[0]
            field = ".".join(map(str, first_error.get("loc", ())))
            message = f"{field}: {first_error['msg']}" if field else first_error["msg"]
            raise HTTPException(
                status_code=400,
                detail=f"CSV row {row_number}: {message}",
            ) from exc

        normalized_code = station.station_code.strip().lower()
        if normalized_code in station_codes:
            raise HTTPException(
                status_code=400,
                detail=f'CSV row {row_number}: duplicate Station ID "{station.station_code}"',
            )
        station_codes.add(normalized_code)
        stations.append(station)

    if not stations:
        raise HTTPException(status_code=400, detail="CSV file contains no station records")

    connection = None
    cursor = None
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        validate_station_categories(cursor, [station.station_category for station in stations])
        placeholders = ", ".join(["%s"] * len(stations))
        cursor.execute(
            f"SELECT station_id, station_code, station_category FROM stations WHERE LOWER(station_code) IN ({placeholders})",
            tuple(station_codes),
        )
        existing = {row[1].lower(): (row[0], row[1], row[2]) for row in cursor.fetchall()}
        to_update = [station for station in stations if station.station_code.strip().lower() in existing]
        to_insert = [station for station in stations if station.station_code.strip().lower() not in existing]
        if mode == "preview":
            return {"total": len(stations), "new": len(to_insert), "existing": len(to_update)}
        if mode == "create" and to_update:
            raise HTTPException(
                status_code=409,
                detail="Station ID already exists: " + ", ".join(
                    station.station_code for station in to_update[:10]
                ) + ("..." if len(to_update) > 10 else ""),
            )

        for station in to_update:
            station_id, _, old_category = existing[station.station_code.strip().lower()]
            if old_category == station.station_category:
                continue
            cursor.execute(
                """SELECT COUNT(*) FROM station_instruments si
                LEFT JOIN instrument_station_categories isc
                    ON isc.instrument_id = si.instrument_id AND isc.station_category = %s
                WHERE si.station_id = %s AND isc.instrument_id IS NULL""",
                (station.station_category, station_id),
            )
            if cursor.fetchone()[0]:
                raise HTTPException(status_code=409, detail=(
                    f"Station {station.station_code}: installed instruments are not assigned "
                    "to the new station category"
                ))

        if to_update:
            before_updates = {
                existing[station.station_code.strip().lower()][0]:
                    audit_snapshot(connection, "stations", existing[station.station_code.strip().lower()][0])
                for station in to_update
            }
            cursor.executemany(
            """UPDATE stations SET station_name = %s, latitude = %s, longitude = %s,
                altitude = %s, province = %s, district = %s, sector = %s,
                station_category = %s, status = %s, comment = %s, action = %s
                WHERE station_id = %s""",
            [(
                station.station_name.strip(), station.latitude, station.longitude,
                station.altitude, station.province.strip(), station.district.strip(),
                station.sector.strip(), station.station_category, station.status,
                station.comment.strip() if station.comment else None,
                station.action.strip() if station.action else None,
                existing[station.station_code.strip().lower()][0],
            ) for station in to_update],
            )
            for station_id, before in before_updates.items():
                record_entity_edit(connection, "stations", station_id, before,
                                   audit_snapshot(connection, "stations", station_id), user)

        if to_insert:
            cursor.executemany(
            """
            INSERT INTO stations (
                station_code,
                station_name,
                latitude,
                longitude,
                altitude,
                province,
                district,
                sector,
                station_category,
                status,
                comment,
                action,
                created_by_user_id,
                recorded_by_username
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    station.station_code.strip(),
                    station.station_name.strip(),
                    station.latitude,
                    station.longitude,
                    station.altitude,
                    station.province.strip(),
                    station.district.strip(),
                    station.sector.strip(),
                    station.station_category,
                    station.status.strip(),
                    station.comment.strip() if station.comment else None,
                    station.action.strip() if station.action else None,
                    user["user_id"],
                    user["username"],
                )
                for station in to_insert
            ],
            )
        connection.commit()
        return {"message": "Stations imported successfully", "imported": len(to_insert),
                "updated": len(to_update)}
    except HTTPException:
        if connection is not None and connection.is_connected():
            connection.rollback()
        raise
    except IntegrityError as exc:
        if connection is not None and connection.is_connected():
            connection.rollback()
        raise HTTPException(
            status_code=409,
            detail="The CSV contains a Station ID that already exists",
        ) from exc
    except MySQLError as exc:
        if connection is not None and connection.is_connected():
            connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


@app.put("/stations/{station_id}")
def update_station(
    station_id: int,
    station: Station,
    _admin=Depends(require_it),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        validate_station_categories(cursor, [station.station_category])
        before = audit_snapshot(connection, "stations", station_id)
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM station_instruments
            LEFT JOIN instrument_station_categories
                ON instrument_station_categories.instrument_id =
                    station_instruments.instrument_id
                AND instrument_station_categories.station_category = %s
            WHERE station_instruments.station_id = %s
                AND instrument_station_categories.instrument_id IS NULL
            """,
            (station.station_category, station_id),
        )
        if cursor.fetchone()[0]:
            raise HTTPException(
                status_code=409,
                detail=(
                    "One or more installed instruments are not assigned to the "
                    "new station category"
                ),
            )
        cursor.execute(
            """
            UPDATE stations
            SET station_code = %s,
                station_name = %s,
                latitude = %s,
                longitude = %s,
                altitude = %s,
                province = %s,
                district = %s,
                sector = %s,
                station_category = %s,
                status = %s,
                comment = %s,
                action = %s
            WHERE station_id = %s
            """,
            (
                station.station_code.strip(),
                station.station_name.strip(),
                station.latitude,
                station.longitude,
                station.altitude,
                station.province.strip(),
                station.district.strip(),
                station.sector.strip(),
                station.station_category,
                station.status.strip(),
                station.comment.strip() if station.comment else None,
                station.action.strip() if station.action else None,
                station_id,
            ),
        )
        if cursor.rowcount == 0:
            cursor.execute(
                "SELECT station_id FROM stations WHERE station_id = %s",
                (station_id,),
            )
            if cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail="Station not found")
        record_entity_edit(connection, "stations", station_id, before,
                           audit_snapshot(connection, "stations", station_id), _admin)
        connection.commit()
        return {"message": "Station updated"}
    except HTTPException:
        raise
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="A station with this Station ID already exists",
        ) from exc
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.delete("/stations/{station_id}", status_code=204)
def delete_station(station_id: int, _admin=Depends(require_it)):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        archive_deleted_item(connection, "stations", station_id, _admin)
        cursor = connection.cursor()
        cursor.execute("DELETE FROM stations WHERE station_id = %s", (station_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Station not found")
        connection.commit()
    except HTTPException:
        raise
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "This station has maintenance, suspected data, or instrument history "
                "and cannot be deleted"
            ),
        ) from exc
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.get("/instruments")
def get_instruments(
    active_only: bool = False,
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=25, ge=10, le=100),
    search: str | None = Query(default=None, max_length=100),
    sort_by: str = "instrument_name",
    sort_order: Literal["asc", "desc"] = "asc",
    _user=Depends(require_instrument_catalog_access),
):

    try:
        connection = get_connection()
        ensure_application_tables(connection)

        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT
                instruments.instrument_id,
                instruments.instrument_name,
                instruments.parameters_taken,
                instruments.category,
                instruments.description,
                instruments.is_active,
                COALESCE(instruments.recorded_by_username, creator.username) AS recorded_by
            FROM instruments
            LEFT JOIN users AS creator
                ON creator.user_id = instruments.created_by_user_id
        """

        if active_only:
            query += " WHERE instruments.is_active = TRUE"

        query += " ORDER BY instruments.instrument_name"
        cursor.execute(query)
        instruments = cursor.fetchall()
        if instruments:
            cursor.execute(
                """
                SELECT instrument_id, station_category
                FROM instrument_station_categories
                ORDER BY station_category
                """
            )
            category_map = {}
            for row in cursor.fetchall():
                category_map.setdefault(row["instrument_id"], []).append(
                    row["station_category"]
                )
            for instrument in instruments:
                instrument["station_categories"] = category_map.get(
                    instrument["instrument_id"],
                    [],
                )
                instrument["station_category_sort"] = ", ".join(instrument["station_categories"])
        instruments = filter_sort_collection(
            instruments, search,
            ["instrument_name", "parameters_taken", "category", "description"],
            sort_by, sort_order,
            {"instrument_id": "instrument_id", "instrument_name": "instrument_name", "parameters_taken": "parameters_taken", "category": "category", "station_categories": "station_category_sort", "description": "description", "status": "is_active", "recorded_by": "recorded_by"},
        )
        return paginate_collection(instruments, page, page_size)

    except MySQLError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {exc}"
        ) from exc

    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.get("/instruments/export")
def export_instruments(
    format: Literal["csv", "pdf"], active_only: bool = False,
    search: str | None = None, sort_by: str = "instrument_name",
    sort_order: Literal["asc", "desc"] = "asc",
    user=Depends(require_instrument_catalog_access),
):
    items = get_instruments(active_only, None, 100, search, sort_by, sort_order, user)
    headers = ["Instrument", "Parameters", "Category", "Station categories", "Description", "Status"]
    rows = [[item["instrument_name"], item["parameters_taken"], item["category"], ", ".join(item["station_categories"]), item["description"], "Active" if item["is_active"] else "Inactive"] for item in items]
    return csv_download("instruments.csv", headers, rows) if format == "csv" else pdf_download("instruments.pdf", "Instrument Catalog", headers, rows, user["username"])


@app.post("/instruments", status_code=201)
def add_instrument(instrument: Instrument, user=Depends(require_it)):

    try:
        connection = get_connection()
        ensure_application_tables(connection)

        cursor = connection.cursor()
        validate_station_categories(cursor, instrument.station_categories)
        cursor.execute(
            """
            INSERT INTO instruments (
                instrument_name,
                parameters_taken,
                category,
                description,
                is_active,
                created_by_user_id,
                recorded_by_username
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                instrument.instrument_name.strip(),
                instrument.parameters_taken.strip(),
                instrument.category.strip(),
                instrument.description.strip() if instrument.description else None,
                instrument.is_active,
                user["user_id"],
                user["username"],
            )
        )
        instrument_id = cursor.lastrowid
        replace_instrument_station_categories(
            cursor,
            instrument_id,
            instrument.station_categories,
        )
        connection.commit()

        return {
            "message": "Instrument added successfully",
            "instrument_id": instrument_id
        }

    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="An instrument with this name already exists"
        ) from exc
    except MySQLError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {exc}"
        ) from exc

    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


def parse_instrument_import(content):
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV file has no header row")
    headers = {(name or "").strip().lower().replace(" ", "_") for name in reader.fieldnames}
    required = {
        "instrument": {"instrument", "instrument_name"},
        "parameters": {"parameters", "parameters_it_takes", "parameters_taken"},
        "category": {"category"},
        "station categories": {"station_categories", "station_category"},
    }
    missing = [label for label, aliases in required.items() if not headers.intersection(aliases)]
    if missing:
        raise HTTPException(status_code=400, detail="CSV is missing columns: " + ", ".join(missing))

    by_name = {}
    merged_rows = 0
    for row_number, source_row in enumerate(reader, start=2):
        if None in source_row:
            raise HTTPException(status_code=400, detail=f"CSV row {row_number}: too many columns")
        row = {(key or "").strip().lower().replace(" ", "_"): (value or "").strip()
               for key, value in source_row.items()}
        categories = []
        raw_categories = row.get("station_categories") or row.get("station_category") or ""
        for raw in raw_categories.replace(";", "|").replace(",", "|").split("|"):
            if raw.strip():
                categories.append(normalize_station_category(raw))
        raw_status = (row.get("status") or row.get("is_active") or "Active").casefold()
        if raw_status not in {"active", "inactive", "true", "false", "yes", "no", "1", "0"}:
            raise HTTPException(status_code=400, detail=f"CSV row {row_number}: invalid status")
        try:
            instrument = Instrument(
                instrument_name=row.get("instrument_name") or row.get("instrument") or "",
                parameters_taken=row.get("parameters_it_takes") or row.get("parameters_taken") or row.get("parameters") or "",
                category=row.get("category") or "",
                station_categories=list(dict.fromkeys(categories)),
                description=row.get("description") or None,
                is_active=raw_status in {"active", "true", "yes", "1"},
            )
        except ValidationError as exc:
            first_error = exc.errors()[0]
            field = ".".join(map(str, first_error.get("loc", ())))
            raise HTTPException(status_code=400, detail=f"CSV row {row_number}: {field}: {first_error['msg']}") from exc
        key = instrument.instrument_name.strip().casefold()
        previous = by_name.get(key)
        if previous:
            if previous.category.casefold() != instrument.category.casefold() or previous.is_active != instrument.is_active:
                raise HTTPException(status_code=400, detail=(
                    f"CSV row {row_number}: conflicting category or status for {instrument.instrument_name}"
                ))
            parameters = list(dict.fromkeys([previous.parameters_taken, instrument.parameters_taken]))
            descriptions = list(dict.fromkeys(filter(None, [previous.description, instrument.description])))
            try:
                instrument = Instrument(
                    instrument_name=previous.instrument_name,
                    parameters_taken="; ".join(parameters),
                    category=previous.category,
                    station_categories=list(dict.fromkeys(previous.station_categories + instrument.station_categories)),
                    description="; ".join(descriptions) or None,
                    is_active=previous.is_active,
                )
            except ValidationError as exc:
                raise HTTPException(status_code=400, detail=f"CSV row {row_number}: merged instrument exceeds field limits") from exc
            merged_rows += 1
        by_name[key] = instrument
    if not by_name:
        raise HTTPException(status_code=400, detail="CSV file has no instrument rows")
    return list(by_name.values()), merged_rows


@app.post("/instruments/import", status_code=201)
async def import_instruments(
    request: Request,
    mode: Literal["create", "upsert", "preview"] = "create",
    user=Depends(require_it),
):
    try:
        content = (await request.body()).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV file must use UTF-8 encoding") from exc

    instruments, merged_rows = parse_instrument_import(content)
    instrument_names = {instrument.instrument_name.strip().casefold() for instrument in instruments}
    import_headers = {(name or "").strip().lower().replace(" ", "_")
                      for name in next(csv.reader(io.StringIO(content)), [])}
    status_supplied = bool(import_headers.intersection({"status", "is_active"}))

    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        validate_station_categories(cursor, [category for instrument in instruments for category in instrument.station_categories])
        placeholders = ", ".join(["%s"] * len(instrument_names))
        cursor.execute(
            f"""
            SELECT instrument_id, instrument_name
            FROM instruments
            WHERE LOWER(instrument_name) IN ({placeholders})
            """,
            tuple(instrument_names),
        )
        existing = {row[1].casefold(): (row[0], row[1]) for row in cursor.fetchall()}
        to_update = [instrument for instrument in instruments if instrument.instrument_name.strip().casefold() in existing]
        to_insert = [instrument for instrument in instruments if instrument.instrument_name.strip().casefold() not in existing]
        for instrument in to_update:
            instrument_id = existing[instrument.instrument_name.strip().casefold()][0]
            cursor.execute("""SELECT DISTINCT s.station_category FROM station_instruments si
                JOIN stations s ON s.station_id = si.station_id
                WHERE si.instrument_id = %s""", (instrument_id,))
            installed_categories = {row[0] for row in cursor.fetchall()}
            if installed_categories - set(instrument.station_categories):
                raise HTTPException(status_code=409, detail=(
                    f"{instrument.instrument_name} is installed at stations outside the imported categories"
                ))
        if mode == "preview":
            return {"total": len(instruments), "new": len(to_insert),
                    "existing": len(to_update), "merged_rows": merged_rows}
        if mode == "create" and to_update:
            raise HTTPException(
                status_code=409,
                detail=(
                    "These instruments already exist: "
                    + ", ".join(sorted(instrument.instrument_name for instrument in to_update[:10]))
                ),
            )
        for instrument in to_update:
            instrument_id = existing[instrument.instrument_name.strip().casefold()][0]
            before = audit_snapshot(connection, "instruments", instrument_id)
            cursor.execute("""UPDATE instruments SET parameters_taken=%s, category=%s,
                description=COALESCE(%s, description),
                is_active=CASE WHEN %s THEN %s ELSE is_active END WHERE instrument_id=%s""",
                (instrument.parameters_taken.strip(), instrument.category.strip(),
                 instrument.description.strip() if instrument.description else None,
                 status_supplied, instrument.is_active, instrument_id))
            replace_instrument_station_categories(cursor, instrument_id, instrument.station_categories)
            record_entity_edit(connection, "instruments", instrument_id, before,
                               audit_snapshot(connection, "instruments", instrument_id), user)
        for instrument in to_insert:
            cursor.execute(
                """
                INSERT INTO instruments (
                    instrument_name,
                    parameters_taken,
                    category,
                    description,
                    is_active,
                    created_by_user_id,
                    recorded_by_username
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    instrument.instrument_name.strip(),
                    instrument.parameters_taken.strip(),
                    instrument.category.strip(),
                    instrument.description.strip() if instrument.description else None,
                    instrument.is_active,
                    user["user_id"],
                    user["username"],
                ),
            )
            replace_instrument_station_categories(
                cursor,
                cursor.lastrowid,
                instrument.station_categories,
            )
        connection.commit()
        return {
            "message": "Instruments imported successfully",
            "imported": len(to_insert),
            "updated": len(to_update),
            "merged_rows": merged_rows,
        }
    except HTTPException:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.put("/instruments/{instrument_id}")
def update_instrument(
    instrument_id: int,
    instrument: Instrument,
    _admin=Depends(require_it),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        validate_station_categories(cursor, instrument.station_categories)
        before = audit_snapshot(connection, "instruments", instrument_id)
        cursor.execute(
            """
            SELECT DISTINCT stations.station_category
            FROM station_instruments
            INNER JOIN stations
                ON stations.station_id = station_instruments.station_id
            WHERE station_instruments.instrument_id = %s
                AND stations.station_category IS NOT NULL
            """,
            (instrument_id,),
        )
        installed_categories = {row[0] for row in cursor.fetchall()}
        missing_categories = installed_categories - set(instrument.station_categories)
        if missing_categories:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This instrument is installed at stations in these categories: "
                    + ", ".join(sorted(missing_categories))
                ),
            )
        cursor.execute(
            """
            UPDATE instruments
            SET instrument_name = %s,
                parameters_taken = %s,
                category = %s,
                description = %s,
                is_active = %s
            WHERE instrument_id = %s
            """,
            (
                instrument.instrument_name.strip(),
                instrument.parameters_taken.strip(),
                instrument.category.strip(),
                instrument.description.strip() if instrument.description else None,
                instrument.is_active,
                instrument_id,
            ),
        )
        if cursor.rowcount == 0:
            cursor.execute(
                "SELECT instrument_id FROM instruments WHERE instrument_id = %s",
                (instrument_id,),
            )
            if cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail="Instrument not found")
        replace_instrument_station_categories(
            cursor,
            instrument_id,
            instrument.station_categories,
        )
        record_entity_edit(connection, "instruments", instrument_id, before,
                           audit_snapshot(connection, "instruments", instrument_id), _admin)
        connection.commit()
        return {"message": "Instrument updated"}
    except HTTPException:
        raise
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="An instrument with this name already exists",
        ) from exc
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.delete("/instruments/{instrument_id}", status_code=204)
def delete_instrument(instrument_id: int, _admin=Depends(require_it)):
    try:
        connection = get_connection()
        archive_deleted_item(connection, "instruments", instrument_id, _admin)
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM instruments WHERE instrument_id = %s",
            (instrument_id,),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Instrument not found")
        connection.commit()
    except HTTPException:
        raise
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "This instrument is used in maintenance history or a station inventory "
                "and cannot be deleted"
            ),
        ) from exc
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


def inspection_stage_departments(stage):
    if stage == "Management":
        return set(INSPECTION_MANAGEMENT_ROLES)
    if stage == "Maintenance":
        return set(INSPECTION_MAINTENANCE_ROLES)
    return set(INSPECTION_HQ_ROLES)


def can_edit_station_inspection(user, inspection):
    if user["department"] == "Admin":
        return True
    if user["user_id"] == inspection["created_by_user_id"]:
        return True
    if inspection["workflow_stage"] == "Maintenance":
        return user["department"] in INSPECTION_MAINTENANCE_ROLES
    if inspection["workflow_stage"] == "Management":
        return user["department"] in INSPECTION_MANAGEMENT_ROLES
    return False


def get_inspection_for_user(cursor, inspection_id, user):
    cursor.execute(
        """
        SELECT station_inspections.*, stations.station_code, stations.station_name
        FROM station_inspections
        INNER JOIN stations ON stations.station_id = station_inspections.station_id
        WHERE station_inspections.inspection_id = %s
        """,
        (inspection_id,),
    )
    inspection = cursor.fetchone()
    if inspection is None:
        raise HTTPException(status_code=404, detail="Station inspection not found")
    ensure_user_can_access_station(cursor, user, inspection["station_id"])
    return inspection


def create_inspection_notifications(
    cursor, inspection, actor_user_id, departments, title, message,
):
    recipients = set()
    if inspection.get("created_by_user_id"):
        recipients.add(inspection["created_by_user_id"])
    if departments:
        placeholders = ", ".join(["%s"] * len(departments))
        cursor.execute(
            f"""
            SELECT user_id FROM users
            WHERE is_active = TRUE AND department IN ({placeholders})
            """,
            tuple(departments),
        )
        recipients.update(
            row["user_id"] if isinstance(row, dict) else row[0]
            for row in cursor.fetchall()
        )
    recipients.discard(actor_user_id)
    if recipients:
        cursor.executemany(
            """
            INSERT INTO notifications (
                user_id, notification_type, title, message,
                related_record_type, related_record_id
            ) VALUES (%s, 'station_inspection', %s, %s, 'station_inspection', %s)
            """,
            [
                (user_id, title, message, inspection["inspection_id"])
                for user_id in recipients
            ],
        )


def ensure_user_can_access_inspection_report(cursor, user, report_id):
    if user["department"] not in ASSIGNED_STATION_ROLES:
        return
    cursor.execute(
        """
        SELECT 1
        FROM station_inspection_report_stations AS links
        INNER JOIN user_station_assignments AS assignments
            ON assignments.station_id = links.station_id
        WHERE links.report_id = %s AND assignments.user_id = %s
        LIMIT 1
        """,
        (report_id, user["user_id"]),
    )
    if cursor.fetchone() is None:
        raise HTTPException(status_code=403, detail="This report is outside your assigned stations")


@app.get("/station-inspections")
def get_station_inspections(
    inspection_id: int | None = Query(default=None, gt=0),
    station_id: str | None = None,
    stage: str | None = Query(default=None, max_length=300),
    status: str | None = Query(default=None, max_length=300),
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=25, ge=10, le=100),
    search: str | None = Query(default=None, max_length=100),
    sort_by: str = "inspection_date",
    sort_order: Literal["asc", "desc"] = "desc",
    date_from: date | None = None,
    date_to: date | None = None,
    user=Depends(require_password_change_complete),
    district: str | None = None,
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT station_inspections.inspection_id,
                station_inspections.station_id,
                stations.station_code, stations.station_name,
                stations.district, stations.station_category,
                station_inspections.inspection_date,
                station_inspections.finding,
                station_inspections.workflow_stage,
                station_inspections.status,
                station_inspections.not_solved_reason,
                station_inspections.created_by_user_id,
                station_inspections.created_by_username,
                station_inspections.updated_by_username,
                station_inspections.created_at,
                station_inspections.updated_at
            FROM station_inspections
            INNER JOIN stations ON stations.station_id = station_inspections.station_id
        """
        parameters = []
        if user["department"] in ASSIGNED_STATION_ROLES:
            query += """
                INNER JOIN user_station_assignments
                    ON user_station_assignments.station_id = station_inspections.station_id
                    AND user_station_assignments.user_id = %s
            """
            parameters.append(user["user_id"])
        query += " ORDER BY station_inspections.inspection_date DESC, station_inspections.inspection_id DESC"
        cursor.execute(query, tuple(parameters))
        items = cursor.fetchall()
        if inspection_id:
            items = [item for item in items if item["inspection_id"] == inspection_id]
        items = [item for item in items if matches_filter(item["station_id"], station_id)
                 and matches_filter(item["district"], district)
                 and matches_filter(item["workflow_stage"], stage)
                 and matches_filter(item["status"], status)]
        items = filter_sort_collection(
            items, search,
            [
                "station_code", "station_name", "district", "station_category",
                "finding", "workflow_stage", "status", "created_by_username",
            ],
            sort_by, sort_order,
            {
                "inspection_date": "inspection_date", "station_name": "station_name",
                "finding": "finding", "workflow_stage": "workflow_stage", "status": "status",
                "status_reason": "not_solved_reason", "created_by_username": "created_by_username",
                "updated_at": "updated_at",
            },
            date_from, date_to, "inspection_date",
        )
        summary = {
            "total": len(items),
            "open": sum(item["status"] == "Open" for item in items),
            "under_review": sum(item["status"] == "Under Review" for item in items),
            "solved": sum(item["status"] == "Solved" for item in items),
            "not_solved": sum(item["status"] == "Not Solved" for item in items),
            "closed": sum(item["workflow_stage"] == "Closed" for item in items),
        }
        result = paginate_collection(items, page, page_size, summary)
        page_items = result["items"] if isinstance(result, dict) else result
        inspection_ids = [item["inspection_id"] for item in page_items]
        station_ids = [item["station_id"] for item in page_items]
        comments_by_inspection = {record_id: [] for record_id in inspection_ids}
        reports_by_inspection = {record_id: [] for record_id in inspection_ids}
        photos_by_inspection = {}
        if inspection_ids:
            placeholders = ", ".join(["%s"] * len(inspection_ids))
            cursor.execute(
                f"""
                SELECT comment_id, inspection_id, action_type, comment,
                    status_after, reason, commented_by_username,
                    commented_by_user_id, commented_by_department, commented_at
                FROM station_inspection_comments
                WHERE inspection_id IN ({placeholders})
                ORDER BY commented_at, comment_id
                """,
                tuple(inspection_ids),
            )
            for comment in cursor.fetchall():
                comments_by_inspection[comment["inspection_id"]].append(comment)
            station_placeholders = ", ".join(["%s"] * len(station_ids))
            cursor.execute(
                f"""
                SELECT DISTINCT reports.report_id, reports.inspection_id,
                    reports.period_start, reports.period_end, reports.notes,
                    reports.original_filename, reports.content_type,
                    reports.file_size, reports.uploaded_by_user_id,
                    reports.uploaded_by_username, reports.uploaded_at
                FROM station_inspection_reports AS reports
                INNER JOIN station_inspection_report_stations AS links
                    ON links.report_id = reports.report_id
                WHERE reports.inspection_id IN ({placeholders})
                    OR links.station_id IN ({station_placeholders})
                ORDER BY reports.uploaded_at DESC, reports.report_id DESC
                """,
                tuple(inspection_ids + station_ids),
            )
            reports = cursor.fetchall()
            report_ids = [report["report_id"] for report in reports]
            report_station_ids = {report_id: [] for report_id in report_ids}
            report_stations = {report_id: [] for report_id in report_ids}
            if report_ids:
                report_placeholders = ", ".join(["%s"] * len(report_ids))
                cursor.execute(
                    f"""
                    SELECT links.report_id, links.station_id,
                        stations.station_code, stations.station_name
                    FROM station_inspection_report_stations AS links
                    INNER JOIN stations ON stations.station_id = links.station_id
                    WHERE links.report_id IN ({report_placeholders})
                    ORDER BY stations.station_name
                    """,
                    tuple(report_ids),
                )
                for link in cursor.fetchall():
                    report_station_ids[link["report_id"]].append(link["station_id"])
                    report_stations[link["report_id"]].append({
                        "station_id": link["station_id"],
                        "station_code": link["station_code"],
                        "station_name": link["station_name"],
                    })
            for report in reports:
                report["stations"] = report_stations[report["report_id"]]
                covered_ids = report_station_ids[report["report_id"]]
                for item in page_items:
                    if (report["inspection_id"] == item["inspection_id"]
                            or item["station_id"] in covered_ids):
                        reports_by_inspection[item["inspection_id"]].append(report)
            cursor.execute(
                f"""
                SELECT inspection_id, original_filename, content_type, file_size,
                    uploaded_by_username, uploaded_at
                FROM station_inspection_photos
                WHERE inspection_id IN ({placeholders})
                """,
                tuple(inspection_ids),
            )
            photos_by_inspection = {
                photo["inspection_id"]: photo for photo in cursor.fetchall()
            }
        for item in page_items:
            item["comments"] = comments_by_inspection[item["inspection_id"]]
            item["reports"] = reports_by_inspection[item["inspection_id"]]
            item["photo"] = photos_by_inspection.get(item["inspection_id"])
            item["can_edit"] = can_edit_station_inspection(user, item)
            item["can_delete"] = user["user_id"] == item["created_by_user_id"]
        return result
    except HTTPException:
        raise
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.post("/station-inspections", status_code=201)
def create_station_inspection(
    record: StationInspectionRecord,
    user=Depends(require_password_change_complete),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT station_id, station_code, station_name FROM stations WHERE station_id = %s",
            (record.station_id,),
        )
        station = cursor.fetchone()
        if station is None:
            raise HTTPException(status_code=404, detail="Station not found")
        ensure_user_can_access_station(cursor, user, record.station_id)
        cursor.execute(
            """
            INSERT INTO station_inspections (
                station_id, inspection_date, finding,
                created_by_user_id, created_by_username,
                updated_by_user_id, updated_by_username
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.station_id, record.inspection_date, record.finding.strip(),
                user["user_id"], user["username"], user["user_id"], user["username"],
            ),
        )
        inspection_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO station_inspection_comments (
                inspection_id, action_type, comment, status_after,
                commented_by_user_id, commented_by_username, commented_by_department
            ) VALUES (%s, 'Inspection reported', %s, 'Open', %s, %s, %s)
            """,
            (
                inspection_id, record.finding.strip(), user["user_id"],
                user["username"], user["department"],
            ),
        )
        inspection = {
            "inspection_id": inspection_id,
            "created_by_user_id": user["user_id"],
        }
        create_inspection_notifications(
            cursor, inspection, user["user_id"], INSPECTION_HQ_ROLES,
            "Station inspection requires HQ review",
            f'{station["station_name"]} inspection was submitted by {user["username"]}.',
        )
        connection.commit()
        return {"message": "Station inspection submitted for HQ review", "inspection_id": inspection_id}
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.put("/station-inspections/{inspection_id}")
def update_station_inspection(
    inspection_id: int,
    record: StationInspectionRecord,
    user=Depends(require_password_change_complete),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        inspection = get_inspection_for_user(cursor, inspection_id, user)
        before = audit_snapshot(connection, "station_inspections", inspection_id)
        if not can_edit_station_inspection(user, inspection):
            raise HTTPException(status_code=403, detail="Only the inspector or assigned team can edit this inspection")
        cursor.execute("SELECT station_id FROM stations WHERE station_id = %s", (record.station_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Station not found")
        ensure_user_can_access_station(cursor, user, record.station_id)
        cursor.execute(
            """
            UPDATE station_inspections
            SET station_id = %s, inspection_date = %s, finding = %s,
                updated_by_user_id = %s, updated_by_username = %s
            WHERE inspection_id = %s
            """,
            (
                record.station_id, record.inspection_date, record.finding.strip(),
                user["user_id"], user["username"], inspection_id,
            ),
        )
        action_label = (
            "Inspection updated"
            if user["user_id"] == inspection["created_by_user_id"]
            else f'{inspection["workflow_stage"]}: Inspection edited'
        )
        cursor.execute(
            """
            INSERT INTO station_inspection_comments (
                inspection_id, action_type, comment, status_after,
                commented_by_user_id, commented_by_username, commented_by_department
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                inspection_id, action_label, record.finding.strip(), inspection["status"],
                user["user_id"], user["username"], user["department"],
            ),
        )
        create_inspection_notifications(
            cursor, inspection, user["user_id"],
            inspection_stage_departments(inspection["workflow_stage"]) | INSPECTION_HQ_ROLES,
            "Station inspection updated",
            f'{inspection["station_name"]} inspection was updated by {user["username"]}.',
        )
        record_entity_edit(connection, "station_inspections", inspection_id, before,
                           audit_snapshot(connection, "station_inspections", inspection_id), user)
        connection.commit()
        return {"message": "Station inspection updated"}
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.post("/station-inspections/{inspection_id}/actions")
def act_on_station_inspection(
    inspection_id: int,
    action: StationInspectionAction,
    user=Depends(require_password_change_complete),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        inspection = get_inspection_for_user(cursor, inspection_id, user)
        before = audit_snapshot(connection, "station_inspections", inspection_id)
        role = user["department"]
        current_stage = inspection["workflow_stage"]
        cursor.execute(
            "SELECT 1 FROM station_inspection_photos WHERE inspection_id = %s",
            (inspection_id,),
        )
        if cursor.fetchone() is None:
            raise HTTPException(status_code=409, detail="Upload the required station photo before recording actions")
        if current_stage == "Closed":
            raise HTTPException(status_code=409, detail="This inspection is already closed")
        if action.action in {"send_management", "send_maintenance"}:
            if role not in INSPECTION_HQ_ROLES:
                raise HTTPException(status_code=403, detail="HQ review access required")
            if current_stage != "HQ Review":
                raise HTTPException(status_code=409, detail="The inspection has already left HQ review")
        elif action.action == "close":
            if role not in INSPECTION_HQ_ROLES:
                raise HTTPException(status_code=403, detail="HQ review access required")
        elif action.action == "status":
            if current_stage not in {"Maintenance", "Management"}:
                raise HTTPException(status_code=409, detail="HQ must route the inspection before a team can update its status")
            if role not in inspection_stage_departments(current_stage):
                raise HTTPException(status_code=403, detail="This status belongs to the receiving team")
        elif role not in inspection_stage_departments(current_stage):
            if action.action != "comment" or user["user_id"] != inspection["created_by_user_id"]:
                raise HTTPException(status_code=403, detail="This action belongs to the receiving team")

        comment = action.comment.strip() if action.comment else None
        reason = action.reason.strip() if action.reason else None
        if action.action == "comment" and not comment:
            raise HTTPException(status_code=400, detail="Enter a comment")
        if action.action in {"send_management", "send_maintenance", "close"} and not comment:
            raise HTTPException(status_code=400, detail="Enter a decision comment")
        if action.action == "status" and action.status is None:
            raise HTTPException(status_code=400, detail="Select a status")
        if action.action == "status" and action.status == "Not Solved" and not reason:
            raise HTTPException(status_code=400, detail="Give the reason the issue is not solved")
        if action.action == "close" and current_stage in {"Maintenance", "Management"}:
            receiving_roles = (
                {MAINTENANCE_ROLE}
                if current_stage == "Maintenance"
                else set(INSPECTION_MANAGEMENT_ROLES) - {"Admin"}
            )
            placeholders = ", ".join(["%s"] * len(receiving_roles))
            cursor.execute(
                f"""
                SELECT 1 FROM station_inspection_comments
                WHERE inspection_id = %s
                    AND (
                        commented_by_department IN ({placeholders})
                        OR action_type LIKE %s
                    )
                LIMIT 1
                """,
                (inspection_id, *receiving_roles, f"{current_stage}:%"),
            )
            if cursor.fetchone() is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Wait for the {current_stage.lower()} team response before closing",
                )

        new_stage = current_stage
        new_status = inspection["status"]
        action_label = "Comment"
        if action.action == "send_management":
            new_stage, new_status, action_label = "Management", "Under Review", "Sent to Management"
        elif action.action == "send_maintenance":
            new_stage, new_status, action_label = "Maintenance", "Under Review", "Sent to Maintenance"
        elif action.action == "close":
            new_stage, new_status, action_label = "Closed", "Solved", "Closed"
        elif action.action == "status":
            new_status = action.status
            action_label = f"{current_stage}: {action.status}"
            if current_stage == "Maintenance" and action.status == "Not Solved":
                new_stage = "Management"
                new_status = "Under Review"
                action_label = "Maintenance: Not Solved to Management"
            elif action.status != "Not Solved":
                reason = None

        cursor.execute(
            """
            UPDATE station_inspections
            SET workflow_stage = %s, status = %s, not_solved_reason = %s,
                updated_by_user_id = %s, updated_by_username = %s
            WHERE inspection_id = %s
            """,
            (
                new_stage, new_status, reason, user["user_id"],
                user["username"], inspection_id,
            ),
        )
        cursor.execute(
            """
            INSERT INTO station_inspection_comments (
                inspection_id, action_type, comment, status_after, reason,
                commented_by_user_id, commented_by_username, commented_by_department
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                inspection_id, action_label, comment,
                action.status if action.action == "status" else new_status,
                reason,
                user["user_id"], user["username"], user["department"],
            ),
        )
        inspection["workflow_stage"] = new_stage
        departments = inspection_stage_departments(new_stage) | INSPECTION_HQ_ROLES
        create_inspection_notifications(
            cursor, inspection, user["user_id"], departments,
            f"Station inspection: {action_label}",
            f'{inspection["station_name"]}: {action_label} by {user["username"]}.',
        )
        record_entity_edit(connection, "station_inspections", inspection_id, before,
                           audit_snapshot(connection, "station_inspections", inspection_id), user)
        connection.commit()
        return {"message": f"Inspection action recorded: {action_label}"}
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.delete("/station-inspections/{inspection_id}", status_code=204)
def delete_station_inspection(
    inspection_id: int,
    user=Depends(require_password_change_complete),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        inspection = get_inspection_for_user(cursor, inspection_id, user)
        if user["user_id"] != inspection["created_by_user_id"]:
            raise HTTPException(status_code=403, detail="Only the inspector can delete this inspection")
        archive_deleted_item(connection, "station_inspections", inspection_id, user)
        create_inspection_notifications(
            cursor, inspection, user["user_id"], INSPECTION_HQ_ROLES,
            "Station inspection deleted",
            f'{inspection["station_name"]} inspection was deleted by {user["username"]}.',
        )
        cursor.execute("DELETE FROM station_inspections WHERE inspection_id = %s", (inspection_id,))
        connection.commit()
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.post("/station-inspections/{inspection_id}/photo", status_code=201)
async def upload_station_inspection_photo(
    inspection_id: int,
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    user=Depends(require_password_change_complete),
):
    clean_filename = Path(filename).name
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    if content_type not in allowed_types or Path(clean_filename).suffix.lower() not in {
        ".jpg", ".jpeg", ".png", ".webp",
    }:
        raise HTTPException(status_code=415, detail="Upload a JPG, PNG, or WebP station photo")
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="The station photo is empty")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The maximum station photo size is 10 MB")
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        inspection = get_inspection_for_user(cursor, inspection_id, user)
        if not can_edit_station_inspection(user, inspection):
            raise HTTPException(status_code=403, detail="Only the inspector or assigned team can replace the station photo")
        cursor.execute(
            """
            INSERT INTO station_inspection_photos (
                inspection_id, original_filename, content_type, file_size,
                file_data, uploaded_by_user_id, uploaded_by_username
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                original_filename = VALUES(original_filename),
                content_type = VALUES(content_type),
                file_size = VALUES(file_size),
                file_data = VALUES(file_data),
                uploaded_by_user_id = VALUES(uploaded_by_user_id),
                uploaded_by_username = VALUES(uploaded_by_username),
                uploaded_at = CURRENT_TIMESTAMP
            """,
            (
                inspection_id, clean_filename, content_type, len(content), content,
                user["user_id"], user["username"],
            ),
        )
        cursor.execute(
            """
            INSERT INTO station_inspection_comments (
                inspection_id, action_type, comment, status_after,
                commented_by_user_id, commented_by_username, commented_by_department
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                inspection_id,
                (
                    "Station photo uploaded"
                    if user["user_id"] == inspection["created_by_user_id"]
                    else f'{inspection["workflow_stage"]}: Photo updated'
                ),
                clean_filename, inspection["status"], user["user_id"],
                user["username"], user["department"],
            ),
        )
        create_inspection_notifications(
            cursor, inspection, user["user_id"],
            inspection_stage_departments(inspection["workflow_stage"]) | INSPECTION_HQ_ROLES,
            "Station inspection photo added",
            f'{inspection["station_name"]} inspection photo was uploaded by {user["username"]}.',
        )
        connection.commit()
        return {"message": "Station photo uploaded"}
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/station-inspections/{inspection_id}/photo")
def get_station_inspection_photo(
    inspection_id: int,
    user=Depends(require_password_change_complete),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        get_inspection_for_user(cursor, inspection_id, user)
        cursor.execute(
            "SELECT * FROM station_inspection_photos WHERE inspection_id = %s",
            (inspection_id,),
        )
        photo = cursor.fetchone()
        if photo is None:
            raise HTTPException(status_code=404, detail="Station photo not found")
        filename = photo["original_filename"].replace('"', "")
        return Response(
            content=photo["file_data"], media_type=photo["content_type"],
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.post("/station-inspections/{inspection_id}/reports", status_code=201)
async def upload_station_inspection_report(
    inspection_id: int,
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    notes: str | None = Query(default=None, max_length=2000),
    station_ids: str | None = Query(default=None, max_length=4000),
    period_start: date | None = None,
    period_end: date | None = None,
    user=Depends(require_password_change_complete),
):
    clean_filename = Path(filename).name
    if Path(clean_filename).suffix.lower() not in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt"}:
        raise HTTPException(status_code=415, detail="Upload a PDF, Word, Excel, CSV, or text report")
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded report is empty")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The maximum report size is 20 MB")
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        inspection = get_inspection_for_user(cursor, inspection_id, user)
        report_start = period_start or inspection["inspection_date"]
        report_end = period_end or report_start
        if report_end < report_start:
            raise HTTPException(status_code=400, detail="Inspection report end date cannot be before its start date")
        covered_station_ids = {inspection["station_id"]}
        if station_ids:
            try:
                covered_station_ids.update(
                    int(value) for value in station_ids.split(",") if value.strip()
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid report station selection") from exc
        if len(covered_station_ids) > 300:
            raise HTTPException(status_code=400, detail="A report can cover at most 300 stations")
        placeholders = ", ".join(["%s"] * len(covered_station_ids))
        cursor.execute(
            f"SELECT station_id FROM stations WHERE station_id IN ({placeholders})",
            tuple(covered_station_ids),
        )
        available_ids = {row["station_id"] for row in cursor.fetchall()}
        if available_ids != covered_station_ids:
            raise HTTPException(status_code=404, detail="One or more selected stations do not exist")
        for station_id in covered_station_ids:
            ensure_user_can_access_station(cursor, user, station_id)
        cursor.execute(
            """
            INSERT INTO station_inspection_reports (
                inspection_id, period_start, period_end, notes,
                original_filename, content_type, file_size, file_data,
                uploaded_by_user_id, uploaded_by_username
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                inspection_id, report_start, report_end,
                notes.strip() if notes else None, clean_filename,
                request.headers.get("content-type", "application/octet-stream").split(";", 1)[0],
                len(content), content, user["user_id"], user["username"],
            ),
        )
        report_id = cursor.lastrowid
        cursor.executemany(
            """
            INSERT INTO station_inspection_report_stations (report_id, station_id)
            VALUES (%s, %s)
            """,
            [(report_id, station_id) for station_id in sorted(covered_station_ids)],
        )
        cursor.execute(
            """
            INSERT INTO station_inspection_comments (
                inspection_id, action_type, comment, status_after,
                commented_by_user_id, commented_by_username, commented_by_department
            ) VALUES (%s, 'Report uploaded', %s, %s, %s, %s, %s)
            """,
            (
                inspection_id,
                f'{clean_filename} ({len(covered_station_ids)} station(s))',
                inspection["status"],
                user["user_id"], user["username"], user["department"],
            ),
        )
        create_inspection_notifications(
            cursor, inspection, user["user_id"],
            inspection_stage_departments(inspection["workflow_stage"]) | INSPECTION_HQ_ROLES,
            "Inspection report uploaded",
            f'{clean_filename} was linked to {len(covered_station_ids)} station(s) by {user["username"]}.',
        )
        connection.commit()
        return {"message": "Inspection report uploaded"}
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/station-inspection-reports")
def get_station_inspection_reports(
    user=Depends(require_password_change_complete), district: str | None = None,
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT report_id, inspection_id, period_start, period_end, notes,
                original_filename, content_type, file_size,
                uploaded_by_user_id, uploaded_by_username, uploaded_at
            FROM station_inspection_reports AS reports
        """
        parameters = []
        conditions = []
        if user["department"] in ASSIGNED_STATION_ROLES:
            conditions.append("""
                EXISTS (
                    SELECT 1
                    FROM station_inspection_report_stations AS links
                    INNER JOIN user_station_assignments AS assignments
                        ON assignments.station_id = links.station_id
                    WHERE links.report_id = reports.report_id
                        AND assignments.user_id = %s
                )
            """)
            parameters.append(user["user_id"])
        if district:
            values = filter_values(district)
            conditions.append(f"""EXISTS (
                SELECT 1 FROM station_inspection_report_stations AS links
                JOIN stations AS s ON s.station_id = links.station_id
                WHERE links.report_id = reports.report_id AND s.district IN ({', '.join(['%s'] * len(values))})
            )""")
            parameters.extend(values)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY period_end DESC, period_start DESC, report_id DESC"
        cursor.execute(query, tuple(parameters))
        reports = cursor.fetchall()
        report_ids = [report["report_id"] for report in reports]
        stations_by_report = {report_id: [] for report_id in report_ids}
        if report_ids:
            placeholders = ", ".join(["%s"] * len(report_ids))
            station_query = f"""
                SELECT links.report_id, stations.station_id,
                    stations.station_code, stations.station_name
                FROM station_inspection_report_stations AS links
                INNER JOIN stations ON stations.station_id = links.station_id
                """
            station_params = []
            if user["department"] in ASSIGNED_STATION_ROLES:
                station_query += """INNER JOIN user_station_assignments AS assignments
                    ON assignments.station_id = stations.station_id AND assignments.user_id = %s """
                station_params.append(user["user_id"])
            station_query += f"WHERE links.report_id IN ({placeholders}) ORDER BY stations.station_name"
            station_params.extend(report_ids)
            cursor.execute(station_query, tuple(station_params))
            for station in cursor.fetchall():
                stations_by_report[station["report_id"]].append({
                    "station_id": station["station_id"],
                    "station_code": station["station_code"],
                    "station_name": station["station_name"],
                })
        for report in reports:
            report["stations"] = stations_by_report[report["report_id"]]
            report["can_delete"] = (
                user["department"] == "Admin"
                or user["user_id"] == report["uploaded_by_user_id"]
            )
        return reports
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.post("/station-inspection-reports", status_code=201)
async def upload_standalone_station_inspection_report(
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    station_ids: str = Query(min_length=1, max_length=4000),
    period_start: date = Query(),
    period_end: date = Query(),
    notes: str | None = Query(default=None, max_length=2000),
    user=Depends(require_password_change_complete),
):
    clean_filename = Path(filename).name
    if Path(clean_filename).suffix.lower() not in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt"}:
        raise HTTPException(status_code=415, detail="Upload a PDF, Word, Excel, CSV, or text report")
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded report is empty")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The maximum report size is 20 MB")
    if period_end < period_start:
        raise HTTPException(status_code=400, detail="Inspection report end date cannot be before its start date")
    try:
        covered_station_ids = {
            int(value) for value in station_ids.split(",") if value.strip()
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid report station selection") from exc
    if not covered_station_ids:
        raise HTTPException(status_code=400, detail="Select at least one station")
    if len(covered_station_ids) > 300:
        raise HTTPException(status_code=400, detail="A report can cover at most 300 stations")
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        placeholders = ", ".join(["%s"] * len(covered_station_ids))
        cursor.execute(
            f"SELECT station_id FROM stations WHERE station_id IN ({placeholders})",
            tuple(covered_station_ids),
        )
        available_ids = {row["station_id"] for row in cursor.fetchall()}
        if available_ids != covered_station_ids:
            raise HTTPException(status_code=404, detail="One or more selected stations do not exist")
        for station_id in covered_station_ids:
            ensure_user_can_access_station(cursor, user, station_id)
        cursor.execute(
            """
            INSERT INTO station_inspection_reports (
                inspection_id, period_start, period_end, notes,
                original_filename, content_type, file_size, file_data,
                uploaded_by_user_id, uploaded_by_username
            ) VALUES (NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                period_start, period_end, notes.strip() if notes else None,
                clean_filename,
                request.headers.get("content-type", "application/octet-stream").split(";", 1)[0],
                len(content), content, user["user_id"], user["username"],
            ),
        )
        report_id = cursor.lastrowid
        cursor.executemany(
            """
            INSERT INTO station_inspection_report_stations (report_id, station_id)
            VALUES (%s, %s)
            """,
            [(report_id, station_id) for station_id in sorted(covered_station_ids)],
        )
        hq_roles = tuple(INSPECTION_HQ_ROLES)
        hq_placeholders = ", ".join(["%s"] * len(hq_roles))
        cursor.execute(
            f"""
            SELECT DISTINCT users.user_id
            FROM users
            LEFT JOIN user_station_assignments AS assignments
                ON assignments.user_id = users.user_id
            WHERE users.is_active = TRUE
                AND (
                    users.department IN ({hq_placeholders})
                    OR assignments.station_id IN ({placeholders})
                )
            """,
            (*hq_roles, *covered_station_ids),
        )
        recipients = {
            row["user_id"] for row in cursor.fetchall()
            if row["user_id"] != user["user_id"]
        }
        if recipients:
            cursor.executemany(
                """
                INSERT INTO notifications (
                    user_id, notification_type, title, message,
                    related_record_type, related_record_id
                ) VALUES (%s, 'station_inspection_report', %s, %s,
                    'station_inspection_report', %s)
                """,
                [(
                    recipient, "Inspection report uploaded",
                    f'{user["username"]} uploaded {clean_filename} for {len(covered_station_ids)} station(s).',
                    report_id,
                ) for recipient in recipients],
            )
        connection.commit()
        return {"message": "Inspection report uploaded", "report_id": report_id}
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/station-inspection-reports/{report_id}/file")
def download_station_inspection_report(
    report_id: int,
    user=Depends(require_password_change_complete),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT station_inspection_reports.*
            FROM station_inspection_reports
            WHERE station_inspection_reports.report_id = %s
            """,
            (report_id,),
        )
        report = cursor.fetchone()
        if report is None:
            raise HTTPException(status_code=404, detail="Inspection report not found")
        ensure_user_can_access_inspection_report(cursor, user, report_id)
        filename = report["original_filename"].replace('"', "")
        return Response(
            content=report["file_data"], media_type=report["content_type"],
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.delete("/station-inspection-reports/{report_id}", status_code=204)
def delete_station_inspection_report(
    report_id: int,
    user=Depends(require_password_change_complete),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT report_id, inspection_id, original_filename,
                uploaded_by_user_id
            FROM station_inspection_reports
            WHERE report_id = %s
            """,
            (report_id,),
        )
        report = cursor.fetchone()
        if report is None:
            raise HTTPException(status_code=404, detail="Inspection report not found")
        ensure_user_can_access_inspection_report(cursor, user, report_id)
        if (user["department"] != "Admin"
                and user["user_id"] != report["uploaded_by_user_id"]):
            raise HTTPException(status_code=403, detail="Only the uploader can delete this report")
        inspection = None
        if report["inspection_id"]:
            cursor.execute(
                """
                SELECT station_inspections.*, stations.station_name
                FROM station_inspections
                INNER JOIN stations ON stations.station_id = station_inspections.station_id
                WHERE station_inspections.inspection_id = %s
                """,
                (report["inspection_id"],),
            )
            inspection = cursor.fetchone()
        archive_deleted_item(connection, "station_inspection_reports", report_id, user)
        cursor.execute("DELETE FROM station_inspection_reports WHERE report_id = %s", (report_id,))
        if inspection:
            cursor.execute(
                """
                INSERT INTO station_inspection_comments (
                    inspection_id, action_type, comment, status_after,
                    commented_by_user_id, commented_by_username, commented_by_department
                ) VALUES (%s, 'Report deleted', %s, %s, %s, %s, %s)
                """,
                (
                    inspection["inspection_id"], report["original_filename"], inspection["status"],
                    user["user_id"], user["username"], user["department"],
                ),
            )
            create_inspection_notifications(
                cursor, inspection, user["user_id"],
                inspection_stage_departments(inspection["workflow_stage"]) | INSPECTION_HQ_ROLES,
                "Inspection report deleted",
                f'{report["original_filename"]} was removed from {inspection["station_name"]}.',
            )
        connection.commit()
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


def get_discussion_for_user(cursor, discussion_id, user):
    cursor.execute(
        "SELECT * FROM discussions WHERE discussion_id = %s",
        (discussion_id,),
    )
    discussion = cursor.fetchone()
    if discussion is None:
        raise HTTPException(status_code=404, detail="Discussion not found")
    if user["department"] != "Admin":
        cursor.execute(
            """
            SELECT 1 FROM discussion_participants
            WHERE discussion_id = %s AND user_id = %s
            """,
            (discussion_id, user["user_id"]),
        )
        if cursor.fetchone() is None:
            raise HTTPException(status_code=403, detail="You are not an attendee of this discussion")
    return discussion


def notify_discussion_participants(cursor, discussion_id, actor_user_id, title, message):
    cursor.execute(
        "SELECT user_id FROM discussion_participants WHERE discussion_id = %s",
        (discussion_id,),
    )
    recipients = {
        row["user_id"] if isinstance(row, dict) else row[0]
        for row in cursor.fetchall()
    }
    recipients.discard(actor_user_id)
    if recipients:
        cursor.executemany(
            """
            INSERT INTO notifications (
                user_id, notification_type, title, message,
                related_record_type, related_record_id
            ) VALUES (%s, 'discussion', %s, %s, 'discussion', %s)
            """,
            [(user_id, title, message, discussion_id) for user_id in recipients],
        )


@app.get("/discussion-users")
def get_discussion_users(user=Depends(require_password_change_complete)):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT user_id, full_name, username, department
            FROM users
            WHERE is_active = TRUE AND user_id <> %s
            ORDER BY full_name, username
            """,
            (user["user_id"],),
        )
        return cursor.fetchall()
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/discussions")
def get_discussions(
    discussion_id: int | None = Query(default=None, gt=0),
    status: str | None = None,
    search: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=10, le=100),
    user=Depends(require_password_change_complete),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        parameters = []
        query = """
            SELECT DISTINCT discussions.*
            FROM discussions
            LEFT JOIN discussion_participants
                ON discussion_participants.discussion_id = discussions.discussion_id
            WHERE 1 = 1
        """
        if user["department"] != "Admin":
            query += " AND discussion_participants.user_id = %s"
            parameters.append(user["user_id"])
        if discussion_id:
            query += " AND discussions.discussion_id = %s"
            parameters.append(discussion_id)
        if status:
            values = filter_values(status)
            query += f" AND discussions.status IN ({', '.join(['%s'] * len(values))})"
            parameters.extend(values)
        if search:
            query += " AND (discussions.title LIKE %s OR discussions.created_by_username LIKE %s)"
            term = f"%{search.strip()}%"
            parameters.extend([term, term])
        query += " ORDER BY discussions.created_at DESC, discussions.discussion_id DESC"
        cursor.execute(query, tuple(parameters))
        items = cursor.fetchall()
        summary = {
            "total": len(items),
            "open": sum(item["status"] == "Open" for item in items),
            "closed": sum(item["status"] == "Closed" for item in items),
        }
        result = paginate_collection(items, page, page_size, summary)
        page_items = result["items"]
        discussion_ids = [item["discussion_id"] for item in page_items]
        participants = {record_id: [] for record_id in discussion_ids}
        messages = {record_id: [] for record_id in discussion_ids}
        if discussion_ids:
            placeholders = ", ".join(["%s"] * len(discussion_ids))
            cursor.execute(
                f"""
                SELECT participants.discussion_id, users.user_id, users.full_name,
                    users.username, users.department
                FROM discussion_participants AS participants
                INNER JOIN users ON users.user_id = participants.user_id
                WHERE participants.discussion_id IN ({placeholders})
                ORDER BY users.full_name
                """,
                tuple(discussion_ids),
            )
            for participant in cursor.fetchall():
                participants[participant["discussion_id"]].append(participant)
            cursor.execute(
                f"""
                SELECT message_id, discussion_id, message, posted_by_user_id,
                    posted_by_username, posted_by_full_name, posted_at
                FROM discussion_messages
                WHERE discussion_id IN ({placeholders})
                ORDER BY posted_at, message_id
                """,
                tuple(discussion_ids),
            )
            for message in cursor.fetchall():
                messages[message["discussion_id"]].append(message)
        for item in page_items:
            item["participants"] = participants[item["discussion_id"]]
            item["messages"] = messages[item["discussion_id"]]
            item["can_close"] = (
                item["status"] == "Open"
                and (user["department"] == "Admin" or item["created_by_user_id"] == user["user_id"])
            )
        return result
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.post("/discussions", status_code=201)
def create_discussion(
    record: DiscussionCreate,
    user=Depends(require_password_change_complete),
):
    participant_ids = set(record.participant_user_ids)
    participant_ids.discard(user["user_id"])
    if not participant_ids:
        raise HTTPException(status_code=400, detail="Select at least one other attendee")
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        placeholders = ", ".join(["%s"] * len(participant_ids))
        cursor.execute(
            f"SELECT user_id FROM users WHERE is_active = TRUE AND user_id IN ({placeholders})",
            tuple(participant_ids),
        )
        active_ids = {row["user_id"] for row in cursor.fetchall()}
        if active_ids != participant_ids:
            raise HTTPException(status_code=400, detail="One or more selected attendees are unavailable")
        cursor.execute(
            """
            INSERT INTO discussions (title, created_by_user_id, created_by_username)
            VALUES (%s, %s, %s)
            """,
            (record.title.strip(), user["user_id"], user["username"]),
        )
        discussion_id = cursor.lastrowid
        all_participants = participant_ids | {user["user_id"]}
        cursor.executemany(
            """
            INSERT INTO discussion_participants (discussion_id, user_id, invited_by_user_id)
            VALUES (%s, %s, %s)
            """,
            [(discussion_id, participant_id, user["user_id"]) for participant_id in all_participants],
        )
        cursor.execute(
            """
            INSERT INTO discussion_messages (
                discussion_id, message, posted_by_user_id,
                posted_by_username, posted_by_full_name
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (
                discussion_id, record.opening_message.strip(), user["user_id"],
                user["username"], user["full_name"],
            ),
        )
        notify_discussion_participants(
            cursor, discussion_id, user["user_id"], "Discussion invitation",
            f'{user["full_name"]} invited you to "{record.title.strip()}".',
        )
        connection.commit()
        return {"message": "Discussion opened and attendees notified", "discussion_id": discussion_id}
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.post("/discussions/{discussion_id}/messages", status_code=201)
def post_discussion_message(
    discussion_id: int,
    record: DiscussionMessageCreate,
    user=Depends(require_password_change_complete),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        discussion = get_discussion_for_user(cursor, discussion_id, user)
        if discussion["status"] == "Closed":
            raise HTTPException(status_code=409, detail="This discussion is closed")
        cursor.execute(
            """
            INSERT INTO discussion_messages (
                discussion_id, message, posted_by_user_id,
                posted_by_username, posted_by_full_name
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (
                discussion_id, record.message.strip(), user["user_id"],
                user["username"], user["full_name"],
            ),
        )
        notify_discussion_participants(
            cursor, discussion_id, user["user_id"], "New discussion response",
            f'{user["full_name"]} responded in "{discussion["title"]}".',
        )
        connection.commit()
        return {"message": "Response posted"}
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.post("/discussions/{discussion_id}/close")
def close_discussion(
    discussion_id: int,
    user=Depends(require_password_change_complete),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        discussion = get_discussion_for_user(cursor, discussion_id, user)
        if user["department"] != "Admin" and discussion["created_by_user_id"] != user["user_id"]:
            raise HTTPException(status_code=403, detail="Only the discussion creator can close it")
        if discussion["status"] == "Closed":
            raise HTTPException(status_code=409, detail="This discussion is already closed")
        cursor.execute(
            """
            UPDATE discussions
            SET status = 'Closed', closed_by_user_id = %s,
                closed_by_username = %s, closed_at = NOW()
            WHERE discussion_id = %s
            """,
            (user["user_id"], user["username"], discussion_id),
        )
        notify_discussion_participants(
            cursor, discussion_id, user["user_id"], "Discussion closed",
            f'{user["full_name"]} closed "{discussion["title"]}".',
        )
        connection.commit()
        return {"message": "Discussion closed and attendees notified"}
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/maintenance-reports")
def get_maintenance_reports(
    user=Depends(require_password_change_complete), district: str | None = None,
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT report_id, period_start, period_end, notes, original_filename,
                content_type, file_size, uploaded_by_username, uploaded_at
            FROM maintenance_reports
        """
        parameters = []
        conditions = []
        if user["department"] in ASSIGNED_STATION_ROLES:
            conditions.append("""
                EXISTS (
                    SELECT 1 FROM maintenance_report_stations
                    INNER JOIN user_station_assignments
                        ON user_station_assignments.station_id = maintenance_report_stations.station_id
                    WHERE maintenance_report_stations.report_id = maintenance_reports.report_id
                        AND user_station_assignments.user_id = %s
                )
            """)
            parameters.append(user["user_id"])
        if district:
            values = filter_values(district)
            conditions.append(f"""EXISTS (
                SELECT 1 FROM maintenance_report_stations mrs
                JOIN stations s ON s.station_id = mrs.station_id
                WHERE mrs.report_id = maintenance_reports.report_id AND s.district IN ({', '.join(['%s'] * len(values))})
            )""")
            parameters.extend(values)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY period_end DESC, period_start DESC, report_id DESC"
        cursor.execute(query, tuple(parameters))
        items = cursor.fetchall()
        report_ids = [item["report_id"] for item in items]
        stations_by_report = {report_id: [] for report_id in report_ids}
        if report_ids:
            placeholders = ", ".join(["%s"] * len(report_ids))
            station_query = f"""
                SELECT maintenance_report_stations.report_id, stations.station_id,
                    stations.station_code, stations.station_name
                FROM maintenance_report_stations
                INNER JOIN stations
                    ON stations.station_id = maintenance_report_stations.station_id
            """
            station_parameters = list(report_ids)
            if user["department"] in ASSIGNED_STATION_ROLES:
                station_query += """
                    INNER JOIN user_station_assignments
                        ON user_station_assignments.station_id = stations.station_id
                        AND user_station_assignments.user_id = %s
                """
                station_parameters = [user["user_id"], *station_parameters]
            station_query += f" WHERE maintenance_report_stations.report_id IN ({placeholders}) ORDER BY stations.station_name"
            cursor.execute(station_query, tuple(station_parameters))
            for station in cursor.fetchall():
                stations_by_report[station["report_id"]].append({
                    "station_id": station["station_id"],
                    "station_code": station["station_code"],
                    "station_name": station["station_name"],
                })
        for item in items:
            item["stations"] = stations_by_report[item["report_id"]]
        return {
            "items": items,
            "can_manage": user["department"] in (MAINTENANCE_ROLE, "Admin"),
        }
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.post("/maintenance-reports", status_code=201)
async def upload_maintenance_report(
    request: Request,
    period_start: date,
    period_end: date,
    station_ids: str = Query(min_length=1),
    filename: str = Query(min_length=1, max_length=255),
    notes: str | None = Query(default=None, max_length=2000),
    user=Depends(require_maintenance_or_it),
):
    if period_end < period_start:
        raise HTTPException(status_code=400, detail="Maintenance end date cannot be before the start date")
    try:
        selected_station_ids = list(dict.fromkeys(int(value) for value in station_ids.split(",") if value.strip()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="One or more station selections are invalid") from exc
    if not selected_station_ids:
        raise HTTPException(status_code=400, detail="Select at least one station")
    clean_filename = Path(filename).name
    if Path(clean_filename).suffix.lower() not in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt"}:
        raise HTTPException(status_code=415, detail="Upload a PDF, Word, Excel, CSV, or text report")
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded report is empty")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The maximum report size is 20 MB")
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        placeholders = ", ".join(["%s"] * len(selected_station_ids))
        cursor.execute(
            f"SELECT station_id FROM stations WHERE station_id IN ({placeholders})",
            tuple(selected_station_ids),
        )
        if {row[0] for row in cursor.fetchall()} != set(selected_station_ids):
            raise HTTPException(status_code=400, detail="One or more selected stations do not exist")
        cursor.execute(
            """
            INSERT INTO maintenance_reports (
                period_start, period_end, notes, original_filename, content_type,
                file_size, file_data, uploaded_by_user_id, uploaded_by_username
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                period_start, period_end, notes.strip() if notes else None,
                clean_filename,
                request.headers.get("content-type", "application/octet-stream").split(";", 1)[0],
                len(content), content, user["user_id"], user["username"],
            ),
        )
        report_id = cursor.lastrowid
        cursor.executemany(
            "INSERT INTO maintenance_report_stations (report_id, station_id) VALUES (%s, %s)",
            [(report_id, station_id) for station_id in selected_station_ids],
        )
        connection.commit()
        return {"message": "Maintenance report uploaded", "report_id": report_id}
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/maintenance-reports/{report_id}/file")
def download_maintenance_report(report_id: int, user=Depends(require_password_change_complete)):
    try:
        connection = get_connection()
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT original_filename, content_type, file_data
            FROM maintenance_reports WHERE report_id = %s
        """
        parameters = [report_id]
        if user["department"] in ASSIGNED_STATION_ROLES:
            query += """
                AND EXISTS (
                    SELECT 1 FROM maintenance_report_stations
                    INNER JOIN user_station_assignments
                        ON user_station_assignments.station_id = maintenance_report_stations.station_id
                    WHERE maintenance_report_stations.report_id = maintenance_reports.report_id
                        AND user_station_assignments.user_id = %s
                )
            """
            parameters.append(user["user_id"])
        cursor.execute(query, tuple(parameters))
        item = cursor.fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="Maintenance report not found")
        filename = item["original_filename"].replace('"', "")
        return Response(
            content=item["file_data"], media_type=item["content_type"],
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.delete("/maintenance-reports/{report_id}", status_code=204)
def delete_maintenance_report(report_id: int, _user=Depends(require_maintenance_or_it)):
    try:
        connection = get_connection()
        archive_deleted_item(connection, "maintenance_reports", report_id, _user)
        cursor = connection.cursor()
        cursor.execute("DELETE FROM maintenance_reports WHERE report_id = %s", (report_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Maintenance report not found")
        connection.commit()
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()



@app.get("/pre-maintenance-reports")
def get_pre_maintenance_reports(
    user=Depends(require_password_change_complete), district: str | None = None,
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT report_id, period_start, period_end, notes, original_filename,
                content_type, file_size, uploaded_by_username, uploaded_at
            FROM pre_maintenance_reports
        """
        parameters = []
        conditions = []
        if user["department"] in ASSIGNED_STATION_ROLES:
            conditions.append("""
                EXISTS (
                    SELECT 1 FROM pre_maintenance_report_stations
                    INNER JOIN user_station_assignments
                        ON user_station_assignments.station_id = pre_maintenance_report_stations.station_id
                    WHERE pre_maintenance_report_stations.report_id = pre_maintenance_reports.report_id
                        AND user_station_assignments.user_id = %s
                )
            """)
            parameters.append(user["user_id"])
        if district:
            values = filter_values(district)
            conditions.append(f"""EXISTS (
                SELECT 1 FROM pre_maintenance_report_stations mrs
                JOIN stations s ON s.station_id = mrs.station_id
                WHERE mrs.report_id = pre_maintenance_reports.report_id AND s.district IN ({', '.join(['%s'] * len(values))})
            )""")
            parameters.extend(values)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY period_end DESC, period_start DESC, report_id DESC"
        cursor.execute(query, tuple(parameters))
        items = cursor.fetchall()
        report_ids = [item["report_id"] for item in items]
        stations_by_report = {report_id: [] for report_id in report_ids}
        if report_ids:
            placeholders = ", ".join(["%s"] * len(report_ids))
            station_query = f"""
                SELECT pre_maintenance_report_stations.report_id, stations.station_id,
                    stations.station_code, stations.station_name
                FROM pre_maintenance_report_stations
                INNER JOIN stations
                    ON stations.station_id = pre_maintenance_report_stations.station_id
            """
            station_parameters = list(report_ids)
            if user["department"] in ASSIGNED_STATION_ROLES:
                station_query += """
                    INNER JOIN user_station_assignments
                        ON user_station_assignments.station_id = stations.station_id
                        AND user_station_assignments.user_id = %s
                """
                station_parameters = [user["user_id"], *station_parameters]
            station_query += f" WHERE pre_maintenance_report_stations.report_id IN ({placeholders}) ORDER BY stations.station_name"
            cursor.execute(station_query, tuple(station_parameters))
            for station in cursor.fetchall():
                stations_by_report[station["report_id"]].append({
                    "station_id": station["station_id"],
                    "station_code": station["station_code"],
                    "station_name": station["station_name"],
                })
        for item in items:
            item["stations"] = stations_by_report[item["report_id"]]
        return {
            "items": items,
            "can_manage": user["department"] in (MAINTENANCE_ROLE, "Admin"),
        }
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.post("/pre-maintenance-reports", status_code=201)
async def upload_pre_maintenance_report(
    request: Request,
    period_start: date,
    period_end: date,
    station_ids: str = Query(min_length=1),
    filename: str = Query(min_length=1, max_length=255),
    notes: str | None = Query(default=None, max_length=2000),
    user=Depends(require_maintenance_or_it),
):
    if period_end < period_start:
        raise HTTPException(status_code=400, detail="Pre-maintenance end date cannot be before the start date")
    try:
        selected_station_ids = list(dict.fromkeys(int(value) for value in station_ids.split(",") if value.strip()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="One or more station selections are invalid") from exc
    if not selected_station_ids:
        raise HTTPException(status_code=400, detail="Select at least one station")
    clean_filename = Path(filename).name
    if Path(clean_filename).suffix.lower() not in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt"}:
        raise HTTPException(status_code=415, detail="Upload a PDF, Word, Excel, CSV, or text report")
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded report is empty")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The maximum report size is 20 MB")
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        placeholders = ", ".join(["%s"] * len(selected_station_ids))
        cursor.execute(
            f"SELECT station_id FROM stations WHERE station_id IN ({placeholders})",
            tuple(selected_station_ids),
        )
        if {row[0] for row in cursor.fetchall()} != set(selected_station_ids):
            raise HTTPException(status_code=400, detail="One or more selected stations do not exist")
        cursor.execute(
            """
            INSERT INTO pre_maintenance_reports (
                period_start, period_end, notes, original_filename, content_type,
                file_size, file_data, uploaded_by_user_id, uploaded_by_username
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                period_start, period_end, notes.strip() if notes else None,
                clean_filename,
                request.headers.get("content-type", "application/octet-stream").split(";", 1)[0],
                len(content), content, user["user_id"], user["username"],
            ),
        )
        report_id = cursor.lastrowid
        cursor.executemany(
            "INSERT INTO pre_maintenance_report_stations (report_id, station_id) VALUES (%s, %s)",
            [(report_id, station_id) for station_id in selected_station_ids],
        )
        connection.commit()
        return {"message": "Pre-maintenance report uploaded", "report_id": report_id}
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/pre-maintenance-reports/{report_id}/file")
def download_pre_maintenance_report(report_id: int, user=Depends(require_password_change_complete)):
    try:
        connection = get_connection()
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT original_filename, content_type, file_data
            FROM pre_maintenance_reports WHERE report_id = %s
        """
        parameters = [report_id]
        if user["department"] in ASSIGNED_STATION_ROLES:
            query += """
                AND EXISTS (
                    SELECT 1 FROM pre_maintenance_report_stations
                    INNER JOIN user_station_assignments
                        ON user_station_assignments.station_id = pre_maintenance_report_stations.station_id
                    WHERE pre_maintenance_report_stations.report_id = pre_maintenance_reports.report_id
                        AND user_station_assignments.user_id = %s
                )
            """
            parameters.append(user["user_id"])
        cursor.execute(query, tuple(parameters))
        item = cursor.fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="Pre-maintenance report not found")
        filename = item["original_filename"].replace('"', "")
        return Response(
            content=item["file_data"], media_type=item["content_type"],
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.delete("/pre-maintenance-reports/{report_id}", status_code=204)
def delete_pre_maintenance_report(report_id: int, _user=Depends(require_maintenance_or_it)):
    try:
        connection = get_connection()
        archive_deleted_item(connection, "pre_maintenance_reports", report_id, _user)
        cursor = connection.cursor()
        cursor.execute("DELETE FROM pre_maintenance_reports WHERE report_id = %s", (report_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Pre-maintenance report not found")
        connection.commit()
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/maintenance")
def get_maintenance_records(
    station_id: str | None = None,
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=25, ge=10, le=100),
    search: str | None = Query(default=None, max_length=100),
    sort_by: str = "maintenance_date",
    sort_order: Literal["asc", "desc"] = "desc",
    date_from: date | None = None,
    date_to: date | None = None,
    user=Depends(require_password_change_complete),
    district: str | None = None,
):

    try:
        connection = get_connection()
        ensure_application_tables(connection)

        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT
                maintenance_records.maintenance_id,
                maintenance_records.station_id,
                stations.station_code,
                stations.station_name,
                stations.station_category,
                stations.district,
                maintenance_records.maintenance_date,
                maintenance_records.issue,
                maintenance_records.activity_done,
                maintenance_records.recommendations,
                maintenance_records.technicians,
                COALESCE(
                    maintenance_records.recorded_by_username,
                    creator.username
                ) AS recorded_by,
                instruments.instrument_id,
                instruments.instrument_name,
                maintenance_record_instruments.issue AS instrument_issue,
                maintenance_record_instruments.action_done AS instrument_action_done,
                maintenance_record_instruments.recommendation AS instrument_recommendation
            FROM maintenance_records
            INNER JOIN stations
                ON stations.station_id = maintenance_records.station_id
            LEFT JOIN users AS creator
                ON creator.user_id = maintenance_records.created_by_user_id
            LEFT JOIN maintenance_record_instruments
                ON maintenance_record_instruments.maintenance_id =
                    maintenance_records.maintenance_id
            LEFT JOIN instruments
                ON instruments.instrument_id =
                    maintenance_record_instruments.instrument_id
        """
        conditions = []
        parameters = []

        add_filter_condition(conditions, parameters, "maintenance_records.station_id", station_id)
        add_filter_condition(conditions, parameters, "stations.district", district)
        if user["department"] in ASSIGNED_STATION_ROLES:
            conditions.append(
                "EXISTS (SELECT 1 FROM user_station_assignments "
                "WHERE user_station_assignments.user_id = %s "
                "AND user_station_assignments.station_id = "
                "maintenance_records.station_id)"
            )
            parameters.append(user["user_id"])
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += (
            " ORDER BY maintenance_records.maintenance_date DESC, "
            "maintenance_records.maintenance_id DESC, "
            "instruments.instrument_name"
        )
        cursor.execute(query, tuple(parameters))

        records = {}

        for row in cursor.fetchall():
            maintenance_id = row["maintenance_id"]

            if maintenance_id not in records:
                records[maintenance_id] = {
                    "maintenance_id": maintenance_id,
                    "station_id": row["station_id"],
                    "station_code": row["station_code"],
                    "station_name": row["station_name"],
                    "station_category": row["station_category"],
                    "district": row["district"],
                    "maintenance_date": row["maintenance_date"],
                    "issue": row["issue"],
                    "activity_done": row["activity_done"],
                    "recommendations": row["recommendations"],
                    "technicians": row["technicians"],
                    "recorded_by": row["recorded_by"],
                    "instruments": []
                }

            if row["instrument_id"] is not None:
                records[maintenance_id]["instruments"].append({
                    "instrument_id": row["instrument_id"],
                    "instrument_name": row["instrument_name"],
                    "issue": row["instrument_issue"],
                    "action_done": row["instrument_action_done"],
                    "recommendation": row["instrument_recommendation"],
                })

        items = list(records.values())
        for item in items:
            item["instrument_search"] = " ".join(
                " ".join(str(instrument.get(key) or "") for key in
                         ("instrument_name", "issue", "action_done", "recommendation"))
                for instrument in item["instruments"]
            )
        items = filter_sort_collection(
            items, search,
            ["station_code", "station_name", "issue", "activity_done", "recommendations", "technicians", "instrument_search"],
            sort_by, sort_order,
            {"maintenance_id": "maintenance_id", "station": "station_name", "maintenance_date": "maintenance_date", "instruments": "instrument_search", "issue": "issue", "activity_done": "activity_done", "recommendations": "recommendations", "technicians": "technicians", "recorded_by": "recorded_by"},
            date_from, date_to, "maintenance_date",
        )
        instrument_ids = {
            instrument["instrument_id"]
            for item in items for instrument in item["instruments"]
        }
        categories = {}
        for item in items:
            category = item["station_category"] or "Not classified"
            categories[category] = categories.get(category, 0) + 1
        summary = {
            "total": len(items),
            "stations": len({item["station_id"] for item in items}),
            "instruments": len(instrument_ids),
            "latest": max((item["maintenance_date"] for item in items), default=None),
            "categories": categories,
        }
        return paginate_collection(items, page, page_size, summary)

    except MySQLError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {exc}"
        ) from exc

    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.get("/maintenance/instrument-history/{station_id}/{instrument_id}")
def get_instrument_maintenance_history(
    station_id: int, instrument_id: int,
    user=Depends(require_password_change_complete),
):
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT station_id FROM stations WHERE station_id = %s", (station_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Station not found")
        ensure_user_can_access_station(cursor, user, station_id)
        cursor.execute("""SELECT record.maintenance_id, record.maintenance_date,
                record.technicians, COALESCE(record.recorded_by_username, creator.username) AS recorded_by,
                link.issue, link.action_done, link.recommendation
            FROM maintenance_record_instruments AS link
            JOIN maintenance_records AS record ON record.maintenance_id = link.maintenance_id
            LEFT JOIN users AS creator ON creator.user_id = record.created_by_user_id
            WHERE record.station_id = %s AND link.instrument_id = %s
            ORDER BY record.maintenance_date DESC, record.maintenance_id DESC""",
            (station_id, instrument_id))
        items = cursor.fetchall()
        for item in items:
            cursor.execute("""SELECT before_data, after_data, changed_by_username, edit_reason, changed_at
                FROM record_edit_history WHERE entity_type = 'maintenance' AND entity_id = %s
                ORDER BY changed_at DESC, history_id DESC""", (str(item["maintenance_id"]),))
            changes = []
            for event in cursor.fetchall():
                before = json.loads(event["before_data"]) if isinstance(event["before_data"], (str, bytes)) else (event["before_data"] or {})
                after = json.loads(event["after_data"]) if isinstance(event["after_data"], (str, bytes)) else (event["after_data"] or {})
                if user["department"] in ASSIGNED_STATION_ROLES and any(
                    snapshot.get("station_id") != station_id for snapshot in (before, after) if snapshot
                ):
                    continue
                old_detail = next((entry for entry in before.get("instrument_details", [])
                                   if entry["instrument_id"] == instrument_id), None)
                new_detail = next((entry for entry in after.get("instrument_details", [])
                                   if entry["instrument_id"] == instrument_id), None)
                if old_detail is None and new_detail is None:
                    continue
                shared = {key: {"before": before.get(key), "after": after.get(key)}
                          for key in ("maintenance_date", "technicians")
                          if before.get(key) != after.get(key)}
                if old_detail != new_detail or shared:
                    changes.append({"changed_at": event["changed_at"],
                                    "changed_by_username": event["changed_by_username"],
                                    "edit_reason": event.get("edit_reason"),
                                    "before": old_detail, "after": new_detail,
                                    "shared_changes": shared,
                                    "before_full": before, "after_full": after})
            item["changes"] = changes
        return {"items": items, "total": len(items)}
    finally:
        if "cursor" in locals(): cursor.close()
        if connection.is_connected(): connection.close()


@app.get("/maintenance/export")
def export_maintenance(
    format: Literal["csv", "pdf"], station_id: str | None = None,
    search: str | None = None, sort_by: str = "maintenance_date",
    sort_order: Literal["asc", "desc"] = "desc", date_from: date | None = None,
    date_to: date | None = None, user=Depends(require_password_change_complete),
    district: str | None = None,
):
    items = get_maintenance_records(station_id, None, 100, search, sort_by, sort_order, date_from, date_to, user, district)
    headers = ["Visit ID", "Station", "Date", "Instrument", "Issue", "Action done", "Recommendation", "Technicians", "Recorded by"]
    rows = [[item["maintenance_id"], f'{item["station_code"]} - {item["station_name"]}',
             item["maintenance_date"], instrument["instrument_name"], instrument["issue"],
             instrument["action_done"], instrument["recommendation"], item["technicians"],
             item["recorded_by"]]
            for item in items for instrument in (item["instruments"] or [{
                "instrument_name": "Not specified", "issue": item["issue"],
                "action_done": item["activity_done"], "recommendation": item["recommendations"]}])]
    return csv_download("maintenance.csv", headers, rows) if format == "csv" else pdf_download("maintenance.pdf", "Maintenance Records", headers, rows, user["username"])


@app.post("/maintenance", status_code=201)
def add_maintenance_record(
    record: MaintenanceRecord,
    user=Depends(require_maintenance_or_it),
):

    try:
        connection = get_connection()
        ensure_application_tables(connection)

        cursor = connection.cursor()
        cursor.execute(
            "SELECT station_id FROM stations WHERE station_id = %s",
            (record.station_id,)
        )

        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Station not found")

        instrument_ids, details, issue, activity_done, recommendations = maintenance_instrument_payload(record)
        placeholders = ", ".join(["%s"] * len(instrument_ids))
        cursor.execute(
            f"""
            SELECT instruments.instrument_id
            FROM instruments
            INNER JOIN instrument_station_categories
                ON instrument_station_categories.instrument_id = instruments.instrument_id
            INNER JOIN stations
                ON stations.station_id = %s
                AND stations.station_category =
                    instrument_station_categories.station_category
            WHERE instruments.instrument_id IN ({placeholders})
                AND instruments.is_active = TRUE
            """,
            (record.station_id, *instrument_ids)
        )

        available_instrument_ids = {
            row[0] for row in cursor.fetchall()
        }

        if available_instrument_ids != set(instrument_ids):
            raise HTTPException(
                status_code=400,
                detail=(
                    "One or more selected instruments are unavailable for this "
                    "station category"
                )
            )

        cursor.execute(
            """
            INSERT INTO maintenance_records (
                station_id,
                maintenance_date,
                issue,
                activity_done,
                recommendations,
                technicians,
                created_by_user_id,
                recorded_by_username
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.station_id,
                record.maintenance_date,
                issue,
                activity_done,
                recommendations,
                record.technicians.strip(),
                user["user_id"],
                user["username"],
            )
        )
        maintenance_id = cursor.lastrowid
        cursor.executemany(
            """
            INSERT INTO maintenance_record_instruments (
                maintenance_id,
                instrument_id,
                issue,
                action_done,
                recommendation
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            [
                (maintenance_id, *detail)
                for detail in details
            ]
        )
        connection.commit()

        return {
            "message": "Maintenance record added successfully",
            "maintenance_id": maintenance_id
        }

    except HTTPException:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {exc}"
        ) from exc

    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.get("/suspected-data")
def get_suspected_data_records(
    station_id: str | None = None,
    status: str | None = None,
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=25, ge=10, le=100),
    search: str | None = Query(default=None, max_length=100),
    sort_by: str = "reported_at",
    sort_order: Literal["asc", "desc"] = "desc",
    date_from: date | None = None,
    date_to: date | None = None,
    user=Depends(require_suspected_data_access),
    district: str | None = None,
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT
                suspected_data_records.suspected_data_id,
                suspected_data_records.station_id,
                stations.station_code,
                stations.station_name,
                stations.district,
                suspected_data_records.issue,
                suspected_data_records.description,
                COALESCE(
                    suspected_data_records.reported_by_username,
                    reporter.username
                ) AS reported_by,
                suspected_data_records.maintenance_date,
                suspected_data_records.maintenance_issue,
                suspected_data_records.how_solved,
                suspected_data_records.maintenance_outcome,
                suspected_data_records.way_forward,
                COALESCE(
                    suspected_data_records.resolved_by_username,
                    resolver.username
                ) AS resolved_by,
                suspected_data_records.resolved_at,
                suspected_data_records.status,
                suspected_data_records.final_is_solved,
                suspected_data_records.final_comment,
                COALESCE(
                    suspected_data_records.final_reviewed_by_username,
                    final_reviewer.username
                ) AS final_reviewed_by,
                suspected_data_records.final_reviewed_at,
                suspected_data_records.created_at AS reported_at,
                suspected_data_records.updated_at
            FROM suspected_data_records
            INNER JOIN stations
                ON stations.station_id = suspected_data_records.station_id
            LEFT JOIN users AS reporter
                ON reporter.user_id = suspected_data_records.reported_by_user_id
            LEFT JOIN users AS resolver
                ON resolver.user_id = suspected_data_records.resolved_by_user_id
            LEFT JOIN users AS final_reviewer
                ON final_reviewer.user_id =
                    suspected_data_records.final_reviewed_by_user_id
        """
        conditions = []
        parameters = []
        add_filter_condition(conditions, parameters, "suspected_data_records.station_id", station_id)
        add_filter_condition(conditions, parameters, "stations.district", district)
        add_filter_condition(conditions, parameters, "suspected_data_records.status", status)
        if user["department"] in ASSIGNED_STATION_ROLES:
            conditions.append(
                "EXISTS (SELECT 1 FROM user_station_assignments "
                "WHERE user_station_assignments.user_id = %s "
                "AND user_station_assignments.station_id = "
                "suspected_data_records.station_id)"
            )
            parameters.append(user["user_id"])
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += (
            " ORDER BY suspected_data_records.created_at DESC, "
            "suspected_data_records.suspected_data_id DESC"
        )
        cursor.execute(query, tuple(parameters))
        items = filter_sort_collection(
            cursor.fetchall(), search,
            ["station_code", "station_name", "issue", "description", "status", "reported_by", "resolved_by", "maintenance_outcome", "way_forward", "final_comment"],
            sort_by, sort_order,
            {"reported_at": "reported_at", "station": "station_name", "measurement": "issue", "description": "description", "reported_by": "reported_by", "maintenance_date": "maintenance_date", "maintenance_findings": "maintenance_issue", "how_solved": "how_solved", "maintenance_outcome": "maintenance_outcome", "way_forward": "way_forward", "resolved_by": "resolved_by", "status": "status", "resolved_at": "resolved_at", "final_is_solved": "final_is_solved", "final_comment": "final_comment", "final_reviewed_by": "final_reviewed_by", "final_reviewed_at": "final_reviewed_at"},
            date_from, date_to, "reported_at",
        )
        statuses = {}
        measurements = {}
        for item in items:
            statuses[item["status"]] = statuses.get(item["status"], 0) + 1
            measurements[item["issue"]] = measurements.get(item["issue"], 0) + 1
        resolved = sum(item["status"] == "Resolved" for item in items)
        summary = {
            "reported": len(items), "open": len(items) - resolved,
            "resolved": resolved,
            "reviewed": sum(bool(item["final_reviewed_at"]) for item in items),
            "statuses": statuses, "measurements": measurements,
        }
        return paginate_collection(items, page, page_size, summary)
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.get("/suspected-data/export")
def export_suspected_data(
    format: Literal["csv", "pdf"], station_id: str | None = None,
    status: str | None = None,
    search: str | None = None, sort_by: str = "reported_at",
    sort_order: Literal["asc", "desc"] = "desc", date_from: date | None = None,
    date_to: date | None = None, user=Depends(require_suspected_data_access),
    district: str | None = None,
):
    items = get_suspected_data_records(station_id, status, None, 100, search, sort_by, sort_order, date_from, date_to, user, district)
    headers = ["Station", "Measurement", "Description", "Reported by", "Reported at", "Maintenance outcome", "Way forward", "Status", "Resolved by", "Final comment", "Final reviewed by"]
    rows = [[f'{item["station_code"]} - {item["station_name"]}', item["issue"], item["description"], item["reported_by"], item["reported_at"], item["maintenance_outcome"], item["way_forward"], item["status"], item["resolved_by"], item["final_comment"], item["final_reviewed_by"]] for item in items]
    return csv_download("qc-records.csv", headers, rows) if format == "csv" else pdf_download("qc-records.pdf", "Quality Control Records", headers, rows, user["username"])


@app.post("/suspected-data", status_code=201)
def add_suspected_data_record(
    record: SuspectedDataCreate,
    user=Depends(require_data_or_it),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        cursor.execute(
            "SELECT station_id, station_code, station_name "
            "FROM stations WHERE station_id = %s",
            (record.station_id,),
        )
        station = cursor.fetchone()
        if station is None:
            raise HTTPException(status_code=404, detail="Station not found")
        ensure_user_can_access_station(cursor, user, record.station_id)
        cursor.execute(
            """
            INSERT INTO suspected_data_records (
                station_id,
                issue,
                description,
                reported_by_user_id,
                reported_by_username,
                status
            )
            VALUES (%s, %s, %s, %s, %s, 'Open')
            """,
            (
                record.station_id,
                record.issue.strip(),
                record.description.strip(),
                user["user_id"],
                user["username"],
            ),
        )
        suspected_data_id = cursor.lastrowid
        create_role_notifications(
            cursor, [MAINTENANCE_ROLE], "suspected_data_reported",
            "New QC report",
            f"{record.issue.strip()} was reported at {station[1]} - {station[2]}.",
            "suspected_data", suspected_data_id,
        )
        connection.commit()
        return {
            "message": "QC record added successfully",
            "suspected_data_id": suspected_data_id,
        }
    except HTTPException:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.put("/suspected-data/{suspected_data_id}")
def update_suspected_data_record(
    suspected_data_id: int,
    record: SuspectedDataUpdate,
    user=Depends(require_maintenance_or_it),
):
    if record.maintenance_date is None:
        raise HTTPException(
            status_code=400,
            detail="Maintenance date is required",
        )
    if record.maintenance_outcome == "Solved" and not record.how_solved:
        raise HTTPException(status_code=400, detail="Solved issues require the solution taken")
    if record.maintenance_outcome in ("Not Solved", "Not Maintained") and not record.way_forward:
        raise HTTPException(
            status_code=400,
            detail="Not Solved or Not Maintained issues require a way forward",
        )
    workflow_status = (
        "Resolved" if record.maintenance_outcome == "Solved" else "Under Review"
    )
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        before = audit_snapshot(connection, "suspected_data", suspected_data_id)
        cursor.execute(
            """
            SELECT status, station_id
            FROM suspected_data_records
            WHERE suspected_data_id = %s
            """,
            (suspected_data_id,),
        )
        existing_record = cursor.fetchone()
        if existing_record is None:
            raise HTTPException(status_code=404, detail="Suspected data record not found")
        cursor.execute(
            "SELECT station_id FROM stations WHERE station_id = %s",
            (record.station_id,),
        )
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Station not found")
        cursor.execute(
            """
            UPDATE suspected_data_records
            SET station_id = %s,
                issue = %s,
                description = %s,
                maintenance_date = %s,
                maintenance_issue = %s,
                how_solved = %s,
                maintenance_outcome = %s,
                way_forward = %s,
                resolved_by_user_id = %s,
                resolved_by_username = %s,
                resolved_at = CASE WHEN %s = 'Resolved' THEN NOW() ELSE NULL END,
                status = %s,
                final_is_solved = NULL,
                final_comment = NULL,
                final_reviewed_by_user_id = NULL,
                final_reviewed_by_username = NULL,
                final_reviewed_at = NULL
            WHERE suspected_data_id = %s
            """,
            (
                record.station_id,
                record.issue.strip(),
                record.description.strip(),
                record.maintenance_date,
                record.maintenance_issue.strip() if record.maintenance_issue else None,
                record.how_solved.strip() if record.how_solved else None,
                record.maintenance_outcome,
                record.way_forward.strip() if record.way_forward else None,
                user["user_id"],
                user["username"],
                workflow_status,
                workflow_status,
                suspected_data_id,
            ),
        )
        if workflow_status == "Resolved" and existing_record[0] != "Resolved":
            cursor.execute(
                "SELECT station_code, station_name FROM stations WHERE station_id = %s",
                (record.station_id,),
            )
            station = cursor.fetchone()
            create_role_notifications(
                cursor, [
                    "Data Quality Control Officer",
                    "Observation Processing Officer",
                    "Observation Officer",
                    "Observation Supervisor",
                    "Observation Supervisor at HQ",
                ],
                "suspected_data_resolved", "Final review required",
                f"{record.issue.strip()} at {station[0]} - {station[1]} is ready for final review.",
                "suspected_data", suspected_data_id, station_id=record.station_id,
            )
        record_entity_edit(connection, "suspected_data", suspected_data_id, before,
                           audit_snapshot(connection, "suspected_data", suspected_data_id), user)
        connection.commit()
        return {"message": "Suspected data record updated"}
    except HTTPException:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.put("/suspected-data/{suspected_data_id}/final-review")
def review_suspected_data_final(
    suspected_data_id: int,
    review: FinalDataReview,
    user=Depends(require_data_or_it),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        before = audit_snapshot(connection, "suspected_data", suspected_data_id)
        cursor.execute(
            """
            SELECT status, station_id
            FROM suspected_data_records
            WHERE suspected_data_id = %s
            """,
            (suspected_data_id,),
        )
        record = cursor.fetchone()
        if record is None:
            raise HTTPException(status_code=404, detail="Suspected data record not found")
        if record["status"] != "Resolved":
            raise HTTPException(
                status_code=409,
                detail="Data can complete final review only after resolution",
            )
        ensure_user_can_access_station(cursor, user, record["station_id"])
        cursor.execute(
            """
            UPDATE suspected_data_records
            SET final_is_solved = %s,
                final_comment = %s,
                final_reviewed_by_user_id = %s,
                final_reviewed_by_username = %s,
                final_reviewed_at = NOW()
            WHERE suspected_data_id = %s
            """,
            (
                review.issue_solved,
                review.comment.strip(),
                user["user_id"],
                user["username"],
                suspected_data_id,
            ),
        )
        record_entity_edit(connection, "suspected_data", suspected_data_id, before,
                           audit_snapshot(connection, "suspected_data", suspected_data_id), user)
        connection.commit()
        return {"message": "Final Data review saved"}
    except HTTPException:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.delete("/suspected-data/{suspected_data_id}", status_code=204)
def delete_suspected_data_record(
    suspected_data_id: int,
    _admin=Depends(require_it),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        archive_deleted_item(connection, "suspected_data", suspected_data_id, _admin)
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM suspected_data_records WHERE suspected_data_id = %s",
            (suspected_data_id,),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Suspected data record not found")
        connection.commit()
    except HTTPException:
        raise
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.get("/station-instruments")
def get_station_instruments(
    station_id: str | None = None,
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=25, ge=10, le=100),
    search: str | None = Query(default=None, max_length=100),
    sort_by: str = "station",
    sort_order: Literal["asc", "desc"] = "asc",
    date_from: date | None = None,
    date_to: date | None = None,
    user=Depends(require_password_change_complete),
    district: str | None = None,
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT
                station_instruments.station_instrument_id,
                station_instruments.station_id,
                stations.station_code,
                stations.station_name,
                stations.station_category,
                stations.district,
                station_instruments.instrument_id,
                instruments.instrument_name,
                instruments.parameters_taken,
                station_instruments.model,
                station_instruments.manufacturer,
                station_instruments.serial_number,
                station_instruments.installation_date,
                station_instruments.calibration_date,
                station_instruments.replacement_date,
                station_instruments.recommended_calibration_date,
                station_instruments.recommended_replacement_date,
                station_instruments.status,
                station_instruments.comment,
                station_instruments.data_logger_ports,
                station_instruments.algorithm,
                station_instruments.wiring_colors,
                COALESCE(
                    station_instruments.recorded_by_username,
                    creator.username
                ) AS recorded_by,
                station_instruments.created_at,
                COALESCE(
                    station_instruments.updated_by_username,
                    updater.username
                ) AS updated_by,
                station_instruments.updated_at
            FROM station_instruments
            INNER JOIN stations
                ON stations.station_id = station_instruments.station_id
            INNER JOIN instruments
                ON instruments.instrument_id = station_instruments.instrument_id
            LEFT JOIN users AS creator
                ON creator.user_id = station_instruments.created_by_user_id
            LEFT JOIN users AS updater
                ON updater.user_id = station_instruments.updated_by_user_id
        """
        conditions = []
        parameters = []
        add_filter_condition(conditions, parameters, "station_instruments.station_id", station_id)
        add_filter_condition(conditions, parameters, "stations.district", district)
        if user["department"] in ASSIGNED_STATION_ROLES:
            conditions.append(
                "EXISTS (SELECT 1 FROM user_station_assignments "
                "WHERE user_station_assignments.user_id = %s "
                "AND user_station_assignments.station_id = "
                "station_instruments.station_id)"
            )
            parameters.append(user["user_id"])
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += (
            " ORDER BY stations.station_name, instruments.instrument_name, "
            "station_instruments.station_instrument_id"
        )
        cursor.execute(query, tuple(parameters))
        items = filter_sort_collection(
            cursor.fetchall(), search,
            ["station_code", "station_name", "station_category", "district", "instrument_name", "parameters_taken", "model", "manufacturer", "serial_number", "status", "comment", "data_logger_ports", "algorithm"],
            sort_by, sort_order,
            {"station": "station_name", "station_category": "station_category", "instrument": "instrument_name", "parameters": "parameters_taken", "model": "model", "manufacturer": "manufacturer", "serial_number": "serial_number", "status": "status", "comment": "comment", "data_logger_ports": "data_logger_ports", "algorithm": "algorithm", "recorded_by": "recorded_by", "recorded_at": "created_at", "updated_by": "updated_by", "updated_at": "updated_at", "installation_date": "installation_date", "calibration_date": "calibration_date", "replacement_date": "replacement_date", "recommended_calibration_date": "recommended_calibration_date", "recommended_replacement_date": "recommended_replacement_date"},
            date_from, date_to, "installation_date",
        )
        statuses = {}
        categories = {}
        for item in items:
            item["wiring_colors"] = json.loads(item["wiring_colors"]) if item["wiring_colors"] else []
            statuses[item["status"]] = statuses.get(item["status"], 0) + 1
            category = item["station_category"] or "Not classified"
            categories[category] = categories.get(category, 0) + 1
        operational = statuses.get("Operational", 0)
        summary = {
            "total": len(items), "stations": len({item["station_id"] for item in items}),
            "operational": operational, "attention": len(items) - operational,
            "statuses": statuses, "categories": categories,
        }
        return paginate_collection(items, page, page_size, summary)
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.get("/station-instruments/export")
def export_station_instruments(
    format: Literal["csv", "pdf"], station_id: str | None = None,
    search: str | None = None, sort_by: str = "station",
    sort_order: Literal["asc", "desc"] = "asc", date_from: date | None = None,
    date_to: date | None = None, user=Depends(require_password_change_complete),
    district: str | None = None,
):
    items = get_station_instruments(station_id, None, 100, search, sort_by, sort_order, date_from, date_to, user, district)
    csv_headers = station_instrument_csv_headers()
    csv_rows = [[item["station_code"], item["instrument_name"], item["model"],
        item["manufacturer"], item["serial_number"], item["installation_date"],
        item["calibration_date"], item["replacement_date"],
        item["recommended_calibration_date"], item["recommended_replacement_date"],
        item["status"], item["comment"], item["data_logger_ports"], item["algorithm"],
        *[(item["wiring_colors"] + [None] * 10)[index] for index in range(10)]]
        for item in items]
    if format == "csv":
        return csv_download("station-instruments.csv", csv_headers, csv_rows)
    pdf_headers = ["Station", "Instrument", "Model", "Serial", "Installed", "Status",
                   "Logger ports", "Algorithm", "Wiring"]
    pdf_rows = [[f'{item["station_code"]} - {item["station_name"]}', item["instrument_name"],
        item["model"], item["serial_number"], item["installation_date"], item["status"],
        item["data_logger_ports"], item["algorithm"],
        "; ".join(f"{number}: {color}" for number, color in enumerate(item["wiring_colors"], 1) if color)]
        for item in items]
    return pdf_download("station-instruments.pdf", "Station Instruments", pdf_headers, pdf_rows, user["username"])


@app.get("/station-instruments/template")
def station_instrument_template(user=Depends(require_maintenance_or_it)):
    return csv_download("station-instrument-template.csv", station_instrument_csv_headers(), [])


@app.post("/station-instruments/import")
async def import_station_instruments(
    request: Request,
    mode: Literal["preview", "create"] = "preview",
    user=Depends(require_maintenance_or_it),
):
    body = await request.body()
    if len(body) > 5_000_000:
        raise HTTPException(status_code=413, detail="CSV file exceeds 5 MB")
    try:
        rows = parse_station_instrument_csv(body.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV file must use UTF-8 encoding") from exc

    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        cursor.execute("SELECT station_id, station_code FROM stations")
        stations = {str(code).strip().casefold(): station_id for station_id, code in cursor.fetchall()}
        cursor.execute("SELECT instrument_id, instrument_name FROM instruments WHERE is_active = 1")
        instruments = {name.strip().casefold(): instrument_id for instrument_id, name in cursor.fetchall()}
        cursor.execute("""SELECT station_id, instrument_id, serial_number, installation_date
            FROM station_instruments""")
        existing = {(station_id, instrument_id, (serial or "").strip().casefold(), str(installed))
                    for station_id, instrument_id, serial, installed in cursor.fetchall()}
        prepared = []
        skipped = 0
        seen = set()
        for row_number, row in rows:
            station_id = stations.get(row["station_id"].casefold())
            instrument_id = instruments.get(row["instrument"].casefold())
            if not station_id or not instrument_id:
                missing = "Station ID" if not station_id else "Instrument"
                raise HTTPException(status_code=400, detail=f"CSV row {row_number}: unknown {missing}")
            try:
                item = StationInstrument.model_validate({
                    "station_id": station_id, "instrument_id": instrument_id,
                    "model": row.get("model") or None,
                    "manufacturer": row.get("manufacturer") or None,
                    "serial_number": row.get("serial_number") or None,
                    "installation_date": row.get("installation_date"),
                    "calibration_date": row.get("calibration_date") or None,
                    "replacement_date": row.get("replacement_date") or None,
                    "recommended_calibration_date": row.get("recommended_calibration") or None,
                    "recommended_replacement_date": row.get("recommended_replacement") or None,
                    "status": row.get("status"), "comment": row.get("comment") or None,
                    "data_logger_ports": row.get("data_logger_ports") or None,
                    "algorithm": row.get("algorithm") or None,
                    "wiring_colors": [row.get(f"wire_{number}") or None for number in range(1, 11)],
                })
            except ValidationError as exc:
                fields = ", ".join(dict.fromkeys(str(error["loc"][0]) for error in exc.errors()))
                raise HTTPException(status_code=400, detail=f"CSV row {row_number}: invalid {fields}") from exc
            try:
                category = ensure_instrument_matches_station_category(cursor, station_id, instrument_id)
                station_instrument_connection_values(item, category)
            except HTTPException as exc:
                raise HTTPException(status_code=400, detail=f"CSV row {row_number}: {exc.detail}") from exc
            key = (station_id, instrument_id, (item.serial_number or "").strip().casefold(),
                   str(item.installation_date))
            if key in existing or key in seen:
                skipped += 1
                continue
            seen.add(key)
            prepared.append((item, category))
        if mode == "create":
            for item, category in prepared:
                insert_station_instrument(cursor, item, user, category)
            connection.commit()
        return {"total": len(rows), "new": len(prepared), "skipped": skipped,
                "created": len(prepared) if mode == "create" else 0}
    except HTTPException:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.post("/station-instruments", status_code=201)
def add_station_instrument(
    item: StationInstrument,
    user=Depends(require_maintenance_or_it),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        category = ensure_instrument_matches_station_category(
            cursor,
            item.station_id,
            item.instrument_id,
        )
        station_instrument_id = insert_station_instrument(cursor, item, user, category)
        connection.commit()
        return {
            "message": "Station instrument added successfully",
            "station_instrument_id": station_instrument_id,
        }
    except HTTPException:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.put("/station-instruments/{station_instrument_id}")
def update_station_instrument(
    station_instrument_id: int,
    item: StationInstrument,
    user=Depends(require_maintenance_or_it),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        before = audit_snapshot(connection, "station_instruments", station_instrument_id)
        cursor.execute(
            "SELECT station_instrument_id FROM station_instruments "
            "WHERE station_instrument_id = %s",
            (station_instrument_id,),
        )
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Station instrument not found")
        category = ensure_instrument_matches_station_category(
            cursor,
            item.station_id,
            item.instrument_id,
        )
        ports, algorithm, wiring = station_instrument_connection_values(item, category)
        cursor.execute(
            """
            UPDATE station_instruments
            SET station_id = %s,
                instrument_id = %s,
                model = %s,
                manufacturer = %s,
                serial_number = %s,
                installation_date = %s,
                calibration_date = %s,
                replacement_date = %s,
                recommended_calibration_date = %s,
                recommended_replacement_date = %s,
                status = %s,
                comment = %s,
                data_logger_ports = %s,
                algorithm = %s,
                wiring_colors = %s,
                updated_by_user_id = %s,
                updated_by_username = %s
            WHERE station_instrument_id = %s
            """,
            (
                item.station_id,
                item.instrument_id,
                item.model.strip() if item.model else None,
                item.manufacturer.strip() if item.manufacturer else None,
                item.serial_number.strip() if item.serial_number else None,
                item.installation_date,
                item.calibration_date,
                item.replacement_date,
                item.recommended_calibration_date,
                item.recommended_replacement_date,
                item.status,
                item.comment.strip() if item.comment else None,
                ports,
                algorithm,
                wiring,
                user["user_id"],
                user["username"],
                station_instrument_id,
            ),
        )
        record_entity_edit(connection, "station_instruments", station_instrument_id, before,
                           audit_snapshot(connection, "station_instruments", station_instrument_id), user)
        connection.commit()
        return {"message": "Station instrument updated"}
    except HTTPException:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected():
            connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.delete("/station-instruments/{station_instrument_id}", status_code=204)
def delete_station_instrument(
    station_instrument_id: int,
    _user=Depends(require_maintenance_or_it),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        archive_deleted_item(connection, "station_instruments", station_instrument_id, _user)
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM station_instruments WHERE station_instrument_id = %s",
            (station_instrument_id,),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Station instrument not found")
        connection.commit()
    except HTTPException:
        raise
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


def month_start(value):
    try:
        return datetime.strptime(value, "%Y-%m").date().replace(day=1)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Month must use YYYY-MM format") from exc


@app.get("/volunteer-data")
def get_volunteer_data(user=Depends(require_volunteer_data_access)):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT file_id, DATE_FORMAT(report_month, '%Y-%m') AS report_month,
                file_kind, original_filename, content_type, file_size,
                uploaded_by_username, uploaded_at
            FROM volunteer_data_files
            ORDER BY report_month DESC, file_kind
            """
        )
        months = {}
        for item in cursor.fetchall():
            month = months.setdefault(item["report_month"], {
                "report_month": item["report_month"], "files": {}, "comments": []
            })
            month["files"][item["file_kind"]] = item
        cursor.execute(
            """
            SELECT comment_id, DATE_FORMAT(report_month, '%Y-%m') AS report_month,
                comment, commented_by_username, commented_at
            FROM volunteer_report_comments
            ORDER BY report_month DESC, commented_at DESC
            """
        )
        for item in cursor.fetchall():
            month = months.setdefault(item["report_month"], {
                "report_month": item["report_month"], "files": {}, "comments": []
            })
            month["comments"].append(item)
        return {
            "items": sorted(months.values(), key=lambda item: item["report_month"], reverse=True),
            "permissions": {
                "upload_qc": user["department"] == "Admin" or user["department"] in QC_FILE_UPLOAD_ROLES,
                "upload_filtered": user["department"] == "Admin" or user["department"] in QC_FILE_UPLOAD_ROLES,
                "upload_filled": user["department"] == "Admin",
                "comment": user["department"] == "Admin",
                "edit": user["department"] == "Admin" or user["department"] in QC_FILE_UPLOAD_ROLES,
                "delete": user["department"] == "Admin" or user["department"] in QC_FILE_UPLOAD_ROLES,
            },
        }
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.post("/volunteer-data/files")
async def upload_volunteer_data_file(
    request: Request,
    report_month: str,
    file_kind: Literal["monthly_qc", "filtered_data", "filled_data"],
    filename: str = Query(min_length=1, max_length=255),
    user=Depends(require_volunteer_data_access),
):
    if file_kind in ("monthly_qc", "filtered_data"):
        if user["department"] != "Admin" and user["department"] not in QC_FILE_UPLOAD_ROLES:
            raise HTTPException(
                status_code=403,
                detail="Only authorized data-quality and HQ supervision roles can upload this file",
            )
    elif user["department"] != "Admin":
        raise HTTPException(status_code=403, detail="Only an Administrator can upload filled data")
    clean_filename = Path(filename).name
    extension = Path(clean_filename).suffix.lower()
    if extension not in {".csv", ".xlsx", ".xls", ".pdf"}:
        raise HTTPException(status_code=415, detail="Upload a CSV, Excel, or PDF file")
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The maximum file size is 15 MB")
    content_type = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        target_month = month_start(report_month)
        before = audit_snapshot(connection, "volunteer_data", target_month)
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO volunteer_data_files (
                report_month, file_kind, original_filename, content_type,
                file_size, file_data, uploaded_by_user_id, uploaded_by_username
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                original_filename = VALUES(original_filename),
                content_type = VALUES(content_type), file_size = VALUES(file_size),
                file_data = VALUES(file_data), uploaded_by_user_id = VALUES(uploaded_by_user_id),
                uploaded_by_username = VALUES(uploaded_by_username), uploaded_at = CURRENT_TIMESTAMP
            """,
            (target_month, file_kind, clean_filename, content_type,
             len(content), content, user["user_id"], user["username"]),
        )
        record_entity_edit(connection, "volunteer_data", str(target_month)[:7], before,
                           audit_snapshot(connection, "volunteer_data", target_month), user)
        connection.commit()
        return {"message": "Monthly file saved"}
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/volunteer-data/files/{file_id}")
def download_volunteer_data_file(file_id: int, _user=Depends(require_volunteer_data_access)):
    try:
        connection = get_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT original_filename, content_type, file_data FROM volunteer_data_files WHERE file_id = %s",
            (file_id,),
        )
        item = cursor.fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="File not found")
        filename = item["original_filename"].replace('"', "")
        return Response(
            content=item["file_data"], media_type=item["content_type"],
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.post("/volunteer-data/comments", status_code=201)
def add_volunteer_report_comment(
    item: VolunteerReportComment,
    user=Depends(require_it),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        target_month = month_start(item.report_month)
        before = audit_snapshot(connection, "volunteer_data", target_month)
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO volunteer_report_comments (
                report_month, comment, commented_by_user_id, commented_by_username
            ) VALUES (%s, %s, %s, %s)
            """,
            (target_month, item.comment.strip(), user["user_id"], user["username"]),
        )
        record_entity_edit(connection, "volunteer_data", str(target_month)[:7], before,
                           audit_snapshot(connection, "volunteer_data", target_month), user)
        connection.commit()
        return {"message": "Supervisor comment saved"}
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.delete("/volunteer-data/{report_month}", status_code=204)
def delete_volunteer_month(
    report_month: str,
    user=Depends(require_volunteer_data_access),
):
    target_month = month_start(report_month)
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        if user["department"] == "Admin":
            archive_deleted_item(connection, "volunteer_data", str(target_month), user)
            cursor.execute("DELETE FROM volunteer_report_comments WHERE report_month = %s", (target_month,))
            cursor.execute("DELETE FROM volunteer_data_files WHERE report_month = %s", (target_month,))
        elif user["department"] in QC_FILE_UPLOAD_ROLES:
            archive_deleted_item(connection, "volunteer_data", str(target_month), user,
                                 volunteer_file_kinds={"monthly_qc", "filtered_data"})
            cursor.execute(
                "DELETE FROM volunteer_data_files WHERE report_month = %s AND file_kind IN ('monthly_qc', 'filtered_data')",
                (target_month,),
            )
        else:
            raise HTTPException(status_code=403, detail="Delete access is not allowed")
        connection.commit()
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/reporting-status")
def get_reporting_status(_user=Depends(require_reporting_view)):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""SELECT
            COUNT(*) AS total_stations,
            COALESCE(SUM(status = 'Operational'), 0) AS operational_stations,
            COALESCE(SUM(status = 'Under maintenance'), 0) AS under_maintenance_stations,
            COALESCE(SUM(status = 'Suspended'), 0) AS suspended_stations,
            COALESCE(SUM(status = 'Closed'), 0) AS closed_stations
            FROM stations""")
        network = cursor.fetchone()
        operational_stations = network["operational_stations"]
        under_maintenance_stations = network["under_maintenance_stations"]
        suspended_stations = network["suspended_stations"]
        cursor.execute(
            """
            SELECT reporting_status_id, DATE_FORMAT(report_month, '%Y-%m') AS report_month,
                expected_stations, operational_stations, under_maintenance_stations,
                suspended_stations,
                reported_stations, notes,
                recorded_by_username, updated_at
            FROM monthly_reporting_status ORDER BY report_month DESC
            """
        )
        items = cursor.fetchall()
        cursor.execute(
            """
            SELECT file_id, DATE_FORMAT(report_month, '%Y-%m') AS report_month,
                original_filename, content_type, file_size,
                uploaded_by_username, uploaded_at
            FROM monthly_non_reported_station_files
            """
        )
        files = {item["report_month"]: item for item in cursor.fetchall()}
        for item in items:
            if any(item[key] is None for key in
                   ("operational_stations", "under_maintenance_stations", "suspended_stations")):
                item["expected_stations"] = None
                item["pending_stations"] = None
                item["reporting_percent"] = None
            else:
                expected = (item["operational_stations"] + item["under_maintenance_stations"]
                            + item["suspended_stations"])
                item["expected_stations"] = expected
                item["pending_stations"] = max(expected - item["reported_stations"], 0)
                item["reporting_percent"] = reporting_percentage(item["operational_stations"], expected)
            item["non_reported_file"] = files.get(item["report_month"])
        return {
            "items": items,
            "total_stations": network["total_stations"],
            "operational_stations": operational_stations,
            "under_maintenance_stations": under_maintenance_stations,
            "suspended_stations": suspended_stations,
            "closed_stations": network["closed_stations"],
            "expected_stations": operational_stations + under_maintenance_stations + suspended_stations,
            "current_reporting_percent": reporting_percentage(
                operational_stations, operational_stations + under_maintenance_stations + suspended_stations),
            "can_manage": _user["department"] == "Admin" or _user["department"] in DATA_OPERATIONS_ROLES,
        }
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


def reporting_snapshot(cursor, report_month):
    cursor.execute("""SELECT reporting_status_id, DATE_FORMAT(report_month, '%Y-%m') AS report_month,
        expected_stations, operational_stations, under_maintenance_stations,
        suspended_stations, reported_stations, notes, recorded_by_username
        FROM monthly_reporting_status WHERE report_month = %s""", (report_month,))
    return cursor.fetchone()


def record_reporting_change(cursor, report_month, action, before, after, user, reporting_status_id=None):
    reason = user.get("edit_reason", "").strip()
    if action in {"Updated", "File uploaded"} and before and not reason:
        raise HTTPException(status_code=422, detail="Explain the reason for this edit")
    if len(reason) > 500:
        raise HTTPException(status_code=422, detail="Edit reason must be at most 500 characters")
    cursor.execute("""INSERT INTO monthly_reporting_changes
        (reporting_status_id, report_month, action, before_data, after_data,
         changed_by_user_id, changed_by_username, edit_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (reporting_status_id, report_month, action,
         json.dumps(before, default=str) if before is not None else None,
         json.dumps(after, default=str) if after is not None else None,
         user["user_id"], user["username"], reason or None))


@app.post("/reporting-status")
def save_reporting_status(item: MonthlyReportingStatus, user=Depends(require_data_operations_writer)):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""SELECT
            COALESCE(SUM(status = 'Operational'), 0) AS operational_stations,
            COALESCE(SUM(status = 'Under maintenance'), 0) AS under_maintenance_stations,
            COALESCE(SUM(status = 'Suspended'), 0) AS suspended_stations
            FROM stations""")
        network = cursor.fetchone()
        operational_stations = network["operational_stations"]
        under_maintenance_stations = network["under_maintenance_stations"]
        suspended_stations = network["suspended_stations"]
        expected_stations = operational_stations + under_maintenance_stations + suspended_stations
        if item.reported_stations > expected_stations:
            raise HTTPException(
                status_code=400,
                detail=f"Reported stations cannot exceed the {expected_stations} expected stations",
            )
        target_month = month_start(item.report_month)
        before = reporting_snapshot(cursor, target_month)
        cursor.execute(
            """
            INSERT INTO monthly_reporting_status (
                report_month, expected_stations, operational_stations,
                under_maintenance_stations, suspended_stations,
                reported_stations, validated_reports,
                notes, recorded_by_user_id, recorded_by_username
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE expected_stations = VALUES(expected_stations),
                operational_stations = VALUES(operational_stations),
                under_maintenance_stations = VALUES(under_maintenance_stations),
                suspended_stations = VALUES(suspended_stations),
                reported_stations = VALUES(reported_stations),
                validated_reports = VALUES(validated_reports), notes = VALUES(notes),
                recorded_by_user_id = VALUES(recorded_by_user_id),
                recorded_by_username = VALUES(recorded_by_username), updated_at = CURRENT_TIMESTAMP
            """,
            (target_month, expected_stations,
             operational_stations, under_maintenance_stations, suspended_stations,
             item.reported_stations,
             0, item.notes.strip() if item.notes else None,
             user["user_id"], user["username"]),
        )
        after = reporting_snapshot(cursor, target_month)
        record_reporting_change(cursor, target_month, "Updated" if before else "Created",
                                before, after, user, after["reporting_status_id"])
        connection.commit()
        return {"message": "Monthly reporting status saved"}
    except Exception:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/reporting-status/history")
def get_reporting_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    report_month: str | None = Query(default=None, max_length=7),
    sort_by: Literal["changed_at", "report_month", "action", "changed_by_username"] = "changed_at",
    sort_order: Literal["asc", "desc"] = "desc",
    _user=Depends(require_reporting_view),
):
    target_month = month_start(report_month) if report_month else None
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        where = " WHERE report_month = %s" if target_month else ""
        params = (target_month,) if target_month else ()
        cursor.execute("SELECT COUNT(*) AS total FROM monthly_reporting_changes" + where, params)
        total = cursor.fetchone()["total"]
        order_column = {
            "changed_at": "changed_at", "report_month": "report_month",
            "action": "action", "changed_by_username": "changed_by_username",
        }[sort_by]
        cursor.execute("""SELECT change_id, reporting_status_id,
            DATE_FORMAT(report_month, '%Y-%m') AS report_month, action,
            before_data, after_data, changed_by_username, edit_reason, changed_at
            FROM monthly_reporting_changes""" + where +
            f" ORDER BY {order_column} {sort_order.upper()}, change_id DESC LIMIT %s OFFSET %s",
            (*params, page_size, (page - 1) * page_size))
        items = cursor.fetchall()
        for item in items:
            for key in ("before_data", "after_data"):
                if isinstance(item[key], (str, bytes)):
                    item[key] = json.loads(item[key])
        return {"items": items, "total": total, "page": page, "page_size": page_size}
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.get("/reporting-status/history/export")
def export_reporting_history(report_month: str | None = Query(default=None, max_length=7),
                             _user=Depends(require_reporting_view)):
    target_month = month_start(report_month) if report_month else None
    connection = get_connection()
    try:
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        where = " WHERE report_month = %s" if target_month else ""
        cursor.execute("""SELECT DATE_FORMAT(report_month, '%Y-%m') AS report_month,
            action, before_data, after_data, changed_by_username, changed_at
            FROM monthly_reporting_changes""" + where +
            " ORDER BY changed_at DESC, change_id DESC", (target_month,) if target_month else ())
        entries = cursor.fetchall()
        for entry in entries:
            for field in ("before_data", "after_data"):
                if isinstance(entry[field], (str, bytes)):
                    entry[field] = json.loads(entry[field])
        headers = ["Month", "Operational", "Under maintenance", "Suspended", "Expected",
                   "Reported", "Pending", "Reporting %", "Non-reported list",
                   "Recorded by", "Notes"]
        if _user["department"] in {"Admin", *DATA_OPERATIONS_ROLES}:
            headers.append("Actions")
        rows = []
        for entry in entries:
            after = entry["after_data"] or {}
            expected = after.get("expected_stations")
            reported = after.get("reported_stations")
            operational = after.get("operational_stations")
            pending = max(0, expected - reported) if expected is not None and reported is not None else "-"
            percentage = (f"{operational / expected * 100:.1f}%"
                          if expected and operational is not None else "-")
            row = [entry["report_month"], after.get("operational_stations", "-"),
                   after.get("under_maintenance_stations", "-"),
                   after.get("suspended_stations", "-"), expected if expected is not None else "-",
                   reported if reported is not None else "-", pending, percentage,
                   after.get("filename", "-"), after.get("recorded_by_username", "-"),
                   plain_history_value(after.get("notes"))]
            if len(headers) == 12:
                row.append("")
            rows.append(row)
        return csv_download("reporting-edit-history.csv", headers, rows)
    finally:
        if "cursor" in locals(): cursor.close()
        connection.close()


@app.post("/reporting-status/non-reported-file")
async def upload_non_reported_station_file(
    request: Request,
    report_month: str,
    filename: str = Query(min_length=1, max_length=255),
    user=Depends(require_data_operations_writer),
):
    clean_filename = Path(filename).name
    if Path(clean_filename).suffix.lower() not in {".csv", ".xlsx", ".xls", ".pdf"}:
        raise HTTPException(status_code=415, detail="Upload a CSV, Excel, or PDF file")
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The maximum file size is 15 MB")
    content_type = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        target_month = month_start(report_month)
        cursor.execute("SELECT reporting_status_id FROM monthly_reporting_status WHERE report_month = %s", (target_month,))
        record = cursor.fetchone()
        if record is None:
            raise HTTPException(status_code=409, detail="Save the monthly reporting status before uploading its station list")
        cursor.execute("""SELECT original_filename, file_size, uploaded_by_username
            FROM monthly_non_reported_station_files WHERE report_month = %s""", (target_month,))
        previous_file = cursor.fetchone()
        cursor.execute(
            """
            INSERT INTO monthly_non_reported_station_files (
                report_month, original_filename, content_type, file_size, file_data,
                uploaded_by_user_id, uploaded_by_username
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE original_filename = VALUES(original_filename),
                content_type = VALUES(content_type), file_size = VALUES(file_size),
                file_data = VALUES(file_data), uploaded_by_user_id = VALUES(uploaded_by_user_id),
                uploaded_by_username = VALUES(uploaded_by_username), uploaded_at = CURRENT_TIMESTAMP
            """,
            (target_month, clean_filename, content_type, len(content), content,
             user["user_id"], user["username"]),
        )
        before = ({"filename": previous_file[0], "file_size": previous_file[1],
                   "uploaded_by_username": previous_file[2]} if previous_file else None)
        after = {"filename": clean_filename, "file_size": len(content),
                 "uploaded_by_username": user["username"]}
        record_reporting_change(cursor, target_month, "File uploaded", before, after,
                                user, record[0])
        connection.commit()
        return {"message": "Non-reported station list saved"}
    except Exception:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/reporting-status/non-reported-file/{file_id}")
def download_non_reported_station_file(file_id: int, _user=Depends(require_reporting_view)):
    try:
        connection = get_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT original_filename, content_type, file_data FROM monthly_non_reported_station_files WHERE file_id = %s",
            (file_id,),
        )
        item = cursor.fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="File not found")
        filename = item["original_filename"].replace('"', "")
        return Response(content=item["file_data"], media_type=item["content_type"],
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.delete("/reporting-status/{reporting_status_id}", status_code=204)
def delete_reporting_status(
    reporting_status_id: int,
    user=Depends(require_data_operations_writer),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT report_month FROM monthly_reporting_status WHERE reporting_status_id = %s", (reporting_status_id,))
        record = cursor.fetchone()
        if record is None:
            raise HTTPException(status_code=404, detail="Reporting status not found")
        target_month = record["report_month"]
        before = reporting_snapshot(cursor, target_month)
        cursor.execute("""SELECT original_filename, file_size, uploaded_by_username
            FROM monthly_non_reported_station_files WHERE report_month = %s""", (target_month,))
        file_record = cursor.fetchone()
        if file_record:
            before["non_reported_file"] = {"filename": file_record["original_filename"],
                                           "file_size": file_record["file_size"],
                                           "uploaded_by_username": file_record["uploaded_by_username"]}
        archive_deleted_item(connection, "reporting_status", reporting_status_id, user)
        cursor.execute("DELETE FROM monthly_non_reported_station_files WHERE report_month = %s", (target_month,))
        cursor.execute("DELETE FROM monthly_reporting_status WHERE reporting_status_id = %s", (reporting_status_id,))
        record_reporting_change(cursor, target_month, "Deleted", before, None, user, reporting_status_id)
        connection.commit()
    except Exception:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/data-requests")
def get_data_requests(
    month_from: str | None = Query(default=None, max_length=7),
    month_to: str | None = Query(default=None, max_length=7),
    _user=Depends(require_reporting_view),
):
    start_month = month_start(month_from) if month_from else None
    end_month = month_start(month_to) if month_to else None
    if start_month and end_month and start_month > end_month:
        raise HTTPException(status_code=400, detail="From month cannot be after To month")
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT data_request_id, DATE_FORMAT(request_month, '%Y-%m') AS request_month,
                category, served_requests, notes, recorded_by_username, updated_at
            FROM monthly_data_requests
        """
        conditions = []
        parameters = []
        if start_month:
            conditions.append("request_month >= %s")
            parameters.append(start_month)
        if end_month:
            conditions.append("request_month <= %s")
            parameters.append(end_month)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY request_month DESC, category"
        cursor.execute(query, tuple(parameters))
        items = cursor.fetchall()
        totals = {}
        categories = {}
        for item in items:
            totals[item["request_month"]] = totals.get(item["request_month"], 0) + item["served_requests"]
            categories[item["category"]] = categories.get(item["category"], 0) + item["served_requests"]
        fiscal_year_totals, fiscal_quarter_totals = fiscal_totals_by_month(totals)
        return {
            "items": items,
            "monthly_totals": totals,
            "fiscal_quarter_totals": fiscal_quarter_totals,
            "fiscal_year_totals": fiscal_year_totals,
            "category_totals": categories,
            "can_manage": _user["department"] == "Admin" or _user["department"] in DATA_OPERATIONS_ROLES,
        }
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.post("/data-requests")
def save_data_request(item: MonthlyDataRequest, user=Depends(require_data_operations_writer)):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        cursor.execute("SELECT data_request_id FROM monthly_data_requests WHERE request_month = %s AND category = %s",
                       (month_start(item.request_month), item.category.strip()))
        existing = cursor.fetchone()
        before = audit_snapshot(connection, "data_requests", existing[0]) if existing else {}
        cursor.execute(
            """
            INSERT INTO monthly_data_requests (
                request_month, category, served_requests, notes,
                recorded_by_user_id, recorded_by_username
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE served_requests = VALUES(served_requests),
                notes = VALUES(notes), recorded_by_user_id = VALUES(recorded_by_user_id),
                recorded_by_username = VALUES(recorded_by_username), updated_at = CURRENT_TIMESTAMP
            """,
            (month_start(item.request_month), item.category.strip(), item.served_requests,
             item.notes.strip() if item.notes else None, user["user_id"], user["username"]),
        )
        cursor.execute("SELECT data_request_id FROM monthly_data_requests WHERE request_month = %s AND category = %s",
                       (month_start(item.request_month), item.category.strip()))
        data_request_id = cursor.fetchone()[0]
        record_entity_edit(connection, "data_requests", data_request_id, before,
                           audit_snapshot(connection, "data_requests", data_request_id), user)
        connection.commit()
        return {"message": "Monthly data request count saved"}
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.post("/data-requests/batch")
def save_data_request_batch(
    item: MonthlyDataRequestBatch,
    user=Depends(require_data_operations_writer),
):
    categories = {}
    for entry in item.categories:
        name = entry.category.strip()
        key = name.casefold()
        if key in categories:
            raise HTTPException(status_code=400, detail=f"Category '{name}' is repeated")
        categories[key] = (name, entry)
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        target_month = month_start(item.request_month)
        before_by_category = {}
        for name, _entry in categories.values():
            cursor.execute("SELECT data_request_id FROM monthly_data_requests WHERE request_month = %s AND category = %s",
                           (target_month, name))
            existing = cursor.fetchone()
            before_by_category[name] = audit_snapshot(connection, "data_requests", existing[0]) if existing else {}
        cursor.executemany(
            """
            INSERT INTO monthly_data_requests (
                request_month, category, served_requests, notes,
                recorded_by_user_id, recorded_by_username
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE served_requests = VALUES(served_requests),
                notes = VALUES(notes), recorded_by_user_id = VALUES(recorded_by_user_id),
                recorded_by_username = VALUES(recorded_by_username), updated_at = CURRENT_TIMESTAMP
            """,
            [
                (
                    target_month, name, entry.served_requests,
                    entry.notes.strip() if entry.notes else None,
                    user["user_id"], user["username"],
                )
                for name, entry in categories.values()
            ],
        )
        for name, _entry in categories.values():
            cursor.execute("SELECT data_request_id FROM monthly_data_requests WHERE request_month = %s AND category = %s",
                           (target_month, name))
            data_request_id = cursor.fetchone()[0]
            record_entity_edit(connection, "data_requests", data_request_id, before_by_category[name],
                               audit_snapshot(connection, "data_requests", data_request_id), user)
        connection.commit()
        return {"message": "Monthly request categories saved", "saved": len(categories)}
    except MySQLError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.put("/data-requests/{data_request_id}")
def update_data_request(
    data_request_id: int,
    item: MonthlyDataRequest,
    user=Depends(require_data_operations_writer),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        before = audit_snapshot(connection, "data_requests", data_request_id)
        cursor.execute(
            """
            UPDATE monthly_data_requests
            SET request_month = %s, category = %s, served_requests = %s, notes = %s,
                recorded_by_user_id = %s, recorded_by_username = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE data_request_id = %s
            """,
            (
                month_start(item.request_month), item.category.strip(), item.served_requests,
                item.notes.strip() if item.notes else None,
                user["user_id"], user["username"], data_request_id,
            ),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Data request record not found")
        record_entity_edit(connection, "data_requests", data_request_id, before,
                           audit_snapshot(connection, "data_requests", data_request_id), user)
        connection.commit()
        return {"message": "Data request record updated"}
    except IntegrityError as exc:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise HTTPException(
            status_code=409,
            detail="That requester category is already recorded for the selected month",
        ) from exc
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.delete("/data-requests/{data_request_id}", status_code=204)
def delete_data_request(
    data_request_id: int,
    _user=Depends(require_data_operations_writer),
):
    try:
        connection = get_connection()
        archive_deleted_item(connection, "data_requests", data_request_id, _user)
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM monthly_data_requests WHERE data_request_id = %s",
            (data_request_id,),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Data request record not found")
        connection.commit()
    except HTTPException:
        if "connection" in locals() and connection.is_connected(): connection.rollback()
        raise
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/kpi")
def get_kpi(month: str | None = None, _user=Depends(require_it)):
    selected_month = month_start(month) if month else date.today().replace(day=1)
    next_month = (selected_month.replace(day=28) + timedelta(days=4)).replace(day=1)
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT user_id, full_name, username, department FROM users WHERE is_active = TRUE ORDER BY full_name")
        users = cursor.fetchall()
        activity_sources = {
            "maintenance": ("maintenance_records", "created_by_user_id", "created_at"),
            "qc_reported": ("suspected_data_records", "reported_by_user_id", "created_at"),
            "qc_resolved": ("suspected_data_records", "resolved_by_user_id", "resolved_at"),
            "qc_reviewed": ("suspected_data_records", "final_reviewed_by_user_id", "final_reviewed_at"),
            "files_uploaded": ("volunteer_data_files", "uploaded_by_user_id", "uploaded_at"),
            "comments": ("volunteer_report_comments", "commented_by_user_id", "commented_at"),
            "reporting_entries": ("monthly_reporting_status", "recorded_by_user_id", "updated_at"),
            "request_entries": ("monthly_data_requests", "recorded_by_user_id", "updated_at"),
        }
        counts = {user["user_id"]: {} for user in users}
        for key, (table, user_column, date_column) in activity_sources.items():
            cursor.execute(
                f"SELECT {user_column} AS user_id, COUNT(*) AS total FROM {table} "
                f"WHERE {date_column} >= %s AND {date_column} < %s AND {user_column} IS NOT NULL GROUP BY {user_column}",
                (selected_month, next_month),
            )
            for row in cursor.fetchall(): counts.setdefault(row["user_id"], {})[key] = row["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM suspected_data_records WHERE status <> 'Resolved'")
        pending_resolution = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM suspected_data_records WHERE status = 'Resolved' AND final_reviewed_at IS NULL")
        pending_review = cursor.fetchone()["total"]
        items = []
        for user in users:
            metrics = counts.get(user["user_id"], {})
            total = sum(metrics.values())
            pending = pending_resolution if user["department"] in ("Admin", MAINTENANCE_ROLE) else (
                pending_review if user["department"] in DATA_QUALITY_ACTION_ROLES else 0
            )
            items.append({**user, **{key: metrics.get(key, 0) for key in activity_sources}, "activity_total": total, "pending": pending})
        return {"month": selected_month.strftime("%Y-%m"), "items": items,
                "summary": {"active_users": len(users), "activity_total": sum(i["activity_total"] for i in items),
                            "pending_resolution": pending_resolution, "pending_review": pending_review}}
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


@app.get("/data-search")
def search_database(
    q: str = Query(min_length=2, max_length=200),
    context_station_id: int | None = Query(default=None, ge=1),
    user=Depends(require_it),
):
    term = q.strip()
    like = f"%{term}%"
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        station_scope = ""
        record_scope = ""
        scope_parameters = []
        if user["department"] in ASSIGNED_STATION_ROLES:
            station_scope = " AND EXISTS (SELECT 1 FROM user_station_assignments usa WHERE usa.station_id = stations.station_id AND usa.user_id = %s)"
            record_scope = " AND station_id IN (SELECT station_id FROM user_station_assignments WHERE user_id = %s)"
            scope_parameters.append(user["user_id"])
        cursor.execute(
            """
            SELECT station_id, station_code, station_name,
                CAST(latitude AS DOUBLE) AS latitude,
                CAST(longitude AS DOUBLE) AS longitude,
                CAST(altitude AS DOUBLE) AS altitude,
                province, district, sector, station_category, status,
                comment, action
            FROM stations WHERE 1=1
            """ + station_scope,
            tuple(scope_parameters),
        )
        accessible_stations = cursor.fetchall()
        normalized_question = term.casefold().replace("_", " ")
        station_candidate = None
        station_suffixes = (" aws", " arg", " wr", " uas")
        for station in sorted(accessible_stations, key=lambda item: len(item["station_name"]), reverse=True):
            aliases = {
                station["station_name"].casefold().replace("_", " "),
                station["station_code"].casefold().replace("_", " "),
            }
            for alias in tuple(aliases):
                for suffix in station_suffixes:
                    if alias.endswith(suffix):
                        aliases.add(alias[:-len(suffix)].strip())
            if any(alias and alias in normalized_question for alias in aliases):
                station_candidate = station
                break
        if station_candidate is None and context_station_id is not None:
            station_candidate = next(
                (item for item in accessible_stations if item["station_id"] == context_station_id),
                None,
            )
        cursor.execute("SELECT COUNT(*) AS total, SUM(status = 'Suspended') AS suspended FROM stations WHERE 1=1" + station_scope, tuple(scope_parameters))
        station_summary = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) AS total FROM maintenance_records WHERE 1=1" + record_scope, tuple(scope_parameters))
        maintenance_total = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total, SUM(status = 'Resolved') AS resolved FROM suspected_data_records WHERE 1=1" + record_scope, tuple(scope_parameters))
        qc_summary = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) AS total FROM instruments")
        instrument_total = cursor.fetchone()["total"]
        cursor.execute("SELECT COALESCE(SUM(served_requests), 0) AS total FROM monthly_data_requests")
        request_total = cursor.fetchone()["total"]
        cursor.execute(
            """
            SELECT DATE_FORMAT(report_month, '%Y-%m') AS report_month,
                operational_stations, under_maintenance_stations, suspended_stations,
                reported_stations
            FROM monthly_reporting_status ORDER BY report_month DESC LIMIT 1
            """
        )
        latest_reporting = cursor.fetchone()
        lower = term.casefold()
        asks_for_count = any(phrase in lower for phrase in ("how many", "number of", "count"))
        asks_about_site = "site" in lower
        asks_for_latest = any(word in lower for word in ("latest", "last", "recent", "newest"))
        asks_for_suspension_cause = (
            "suspend" in lower
            and any(word in lower for word in ("why", "cause", "reason", "issue", "problem"))
        )
        latest_maintenance = None
        if "maintenance" in lower and (asks_for_latest or station_candidate):
            maintenance_query = """
                SELECT m.maintenance_id, m.maintenance_date, m.issue,
                    m.activity_done, m.recommendations, m.technicians,
                    m.created_at, s.station_id, s.station_code, s.station_name
                FROM maintenance_records m
                INNER JOIN stations s ON s.station_id = m.station_id
                WHERE 1=1
            """
            maintenance_parameters = []
            if station_candidate:
                maintenance_query += " AND m.station_id = %s"
                maintenance_parameters.append(station_candidate["station_id"])
            maintenance_query += (
                " ORDER BY m.maintenance_date DESC, m.created_at DESC, "
                "m.maintenance_id DESC LIMIT 1"
            )
            cursor.execute(maintenance_query, tuple(maintenance_parameters))
            latest_maintenance = cursor.fetchone()

        if station_candidate and asks_about_site and asks_for_count:
            site_stations = [
                station for station in accessible_stations
                if station["latitude"] == station_candidate["latitude"]
                and station["longitude"] == station_candidate["longitude"]
                and station["altitude"] == station_candidate["altitude"]
            ]
            station_names = ", ".join(
                sorted(station["station_name"] for station in site_stations)
            )
            site_name = station_candidate["station_name"].replace("_", " ")
            for suffix in (" AWS", " ARG", " WR", " UAS"):
                if site_name.upper().endswith(suffix):
                    site_name = site_name[:-len(suffix)]
                    break
            answer = (
                f"The {site_name} site has "
                f"{len(site_stations)} station{'s' if len(site_stations) != 1 else ''}: "
                f"{station_names}. They share latitude {station_candidate['latitude']:g}, "
                f"longitude {station_candidate['longitude']:g}, and elevation "
                f"{station_candidate['altitude']:g} metres."
            )
        elif station_candidate and asks_for_suspension_cause:
            if station_candidate["status"] == "Suspended":
                cause = station_candidate["comment"] or "No suspension cause has been recorded"
                next_action = station_candidate["action"]
                answer = f"{station_candidate['station_name']} is suspended. The recorded cause is: {cause}."
                if next_action:
                    answer += f" The planned action is: {next_action}."
            else:
                answer = (
                    f"{station_candidate['station_name']} is not currently suspended. "
                    f"Its recorded status is {station_candidate['status']}."
                )
        elif "maintenance" in lower and (asks_for_latest or station_candidate):
            if latest_maintenance:
                maintenance_date = latest_maintenance["maintenance_date"].strftime("%d %B %Y")
                answer = (
                    f"The latest maintenance update"
                    f"{' for ' + station_candidate['station_name'] if station_candidate else ''} "
                    f"was recorded for {latest_maintenance['station_name']} on {maintenance_date}. "
                    f"Issue: {latest_maintenance['issue']}. "
                    f"Activity completed: {latest_maintenance['activity_done']}."
                )
                if latest_maintenance["recommendations"]:
                    answer += f" Recommendation: {latest_maintenance['recommendations']}."
                if latest_maintenance["technicians"]:
                    answer += f" Technician(s): {latest_maintenance['technicians']}."
            elif station_candidate:
                answer = f"No maintenance record has been entered for {station_candidate['station_name']}."
            else:
                answer = "No maintenance record has been entered yet."
        elif station_candidate and ("altitude" in lower or "elevation" in lower or "height" in lower):
            answer = (
                f"{station_candidate['station_name']} ({station_candidate['station_code']}) "
                f"has an elevation of {station_candidate['altitude']:g} metres above sea level."
            )
        elif station_candidate and ("latitude" in lower or "longitude" in lower or "coordinate" in lower or "location" in lower):
            answer = (
                f"{station_candidate['station_name']} is at latitude {station_candidate['latitude']:g}, "
                f"longitude {station_candidate['longitude']:g}, in {station_candidate['sector']}, "
                f"{station_candidate['district']}, {station_candidate['province']}."
            )
        elif station_candidate and ("district" in lower or "province" in lower or "sector" in lower or "where" in lower):
            answer = (
                f"{station_candidate['station_name']} is in {station_candidate['sector']} Sector, "
                f"{station_candidate['district']} District, {station_candidate['province']} Province."
            )
        elif station_candidate and ("category" in lower or "type" in lower):
            answer = f"{station_candidate['station_name']} is classified as {station_candidate['station_category']}."
        elif station_candidate and ("status" in lower or "operational" in lower or "suspended" in lower):
            answer = f"{station_candidate['station_name']} has status {station_candidate['status']}."
        elif "suspend" in lower:
            answer = f"There are {int(station_summary['suspended'] or 0)} suspended stations."
        elif "maintenance" in lower:
            answer = f"The database contains {maintenance_total} maintenance records."
        elif "qc" in lower or "suspect" in lower or "quality" in lower:
            answer = f"There are {qc_summary['total']} QC records; {int(qc_summary['resolved'] or 0)} are resolved."
        elif "instrument" in lower or "equipment" in lower:
            answer = f"The instrument catalog contains {instrument_total} instruments."
        elif "request" in lower:
            answer = f"A total of {request_total} served data requests has been recorded."
        elif "report" in lower and latest_reporting:
            counts = [latest_reporting[key] for key in
                      ("operational_stations", "under_maintenance_stations", "suspended_stations")]
            expected = sum(counts) if all(count is not None for count in counts) else None
            percent = reporting_percentage(latest_reporting["operational_stations"], expected)
            answer = (f"For {latest_reporting['report_month']}, {latest_reporting['reported_stations']} stations reported. "
                      + (f"Reporting percentage was {percent}% ({latest_reporting['operational_stations']} operational / {expected} expected)."
                         if percent is not None else "The operational/expected percentage is unavailable for this older record."))
        elif "station" in lower:
            answer = f"The network contains {station_summary['total']} stations."
        else:
            answer = (
                "I could not identify one precise question. You can ask about a station's site, "
                "elevation, coordinates, location, category, operational or suspension status, "
                "suspension cause, or latest maintenance update."
            )
        cursor.execute(
            "SELECT station_id AS id, 'Station' AS type, station_name AS title, CONCAT(station_code, ' | ', station_category, ' | ', status) AS detail FROM stations WHERE (station_name LIKE %s OR station_code LIKE %s OR district LIKE %s OR station_category LIKE %s)" + station_scope + " LIMIT 12",
            (like, like, like, like, *scope_parameters),
        )
        results = cursor.fetchall()
        if station_candidate and not any(
            item["type"] == "Station" and item["id"] == station_candidate["station_id"]
            for item in results
        ):
            results.insert(0, {
                "id": station_candidate["station_id"],
                "type": "Station",
                "title": station_candidate["station_name"],
                "detail": (
                    f"{station_candidate['station_code']} | {station_candidate['station_category']} | "
                    f"Altitude {station_candidate['altitude']:g} m | {station_candidate['status']}"
                ),
            })
        if latest_maintenance:
            results.insert(0, {
                "id": latest_maintenance["maintenance_id"],
                "type": "Maintenance",
                "title": f"{latest_maintenance['station_name']} - {latest_maintenance['maintenance_date']}",
                "detail": (
                    f"{latest_maintenance['issue']} | {latest_maintenance['activity_done']}"
                ),
            })
        cursor.execute("SELECT instrument_id AS id, 'Instrument' AS type, instrument_name AS title, CONCAT(category, ' | ', parameters_taken) AS detail FROM instruments WHERE instrument_name LIKE %s OR category LIKE %s OR parameters_taken LIKE %s LIMIT 12", (like, like, like))
        results.extend(cursor.fetchall())
        cursor.execute("SELECT maintenance_id AS id, 'Maintenance' AS type, CONCAT('Maintenance #', maintenance_id) AS title, CONCAT(issue, ' | ', activity_done) AS detail FROM maintenance_records WHERE (issue LIKE %s OR activity_done LIKE %s OR recommendations LIKE %s)" + record_scope + " LIMIT 12", (like, like, like, *scope_parameters))
        results.extend(cursor.fetchall())
        cursor.execute("SELECT suspected_data_id AS id, 'QC' AS type, issue AS title, CONCAT(description, ' | ', status) AS detail FROM suspected_data_records WHERE (issue LIKE %s OR description LIKE %s OR final_comment LIKE %s)" + record_scope + " LIMIT 12", (like, like, like, *scope_parameters))
        results.extend(cursor.fetchall())
        cursor.execute("SELECT data_request_id AS id, 'Data request' AS type, category AS title, CONCAT(DATE_FORMAT(request_month, '%Y-%m'), ' | ', served_requests, ' served') AS detail FROM monthly_data_requests WHERE category LIKE %s OR notes LIKE %s LIMIT 12", (like, like))
        results.extend(cursor.fetchall())
        return {
            "question": term,
            "answer": answer,
            "results": results[:40],
            "context_station_id": station_candidate["station_id"] if station_candidate else None,
            "context_station_name": station_candidate["station_name"] if station_candidate else None,
        }
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()
