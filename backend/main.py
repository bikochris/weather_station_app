import csv
import io
import math
import os
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from mysql.connector import Error as MySQLError, IntegrityError
from pydantic import BaseModel, Field, ValidationError
from typing import Literal

try:
    from .database import get_connection
    from .security import (
        create_session_token,
        hash_password,
        hash_session_token,
        verify_password,
    )
except ImportError:
    from database import get_connection
    from security import (
        create_session_token,
        hash_password,
        hash_session_token,
        verify_password,
    )


app = FastAPI()
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


StationCategory = Literal[
    "Upper air station",
    "Weather radar",
    "Automatic Weather stations",
    "Automatic Raingauge",
    "Principal stations",
    "Climatic stations",
    "Rainfall station",
]

UserRole = Literal[
    "Admin",
    "Maintenance",
    "Data Quality Control",
    "Observation Officer",
]


class Station(BaseModel):

    station_code: str = Field(min_length=1, max_length=50)
    station_name: str = Field(min_length=1, max_length=100)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude: float
    province: str = Field(min_length=1, max_length=100)
    district: str = Field(min_length=1, max_length=100)
    sector: str = Field(min_length=1, max_length=100)
    station_category: StationCategory
    status: str = Field(min_length=1, max_length=30)
    suspended: bool = False
    comment: str | None = Field(default=None, max_length=2000)
    action: str | None = Field(default=None, max_length=2000)


class MaintenanceRecord(BaseModel):

    station_id: int = Field(gt=0)
    maintenance_date: date
    issue: str = Field(min_length=1, max_length=2000)
    activity_done: str = Field(min_length=1, max_length=2000)
    recommendations: str | None = Field(default=None, max_length=2000)
    technicians: str = Field(min_length=1, max_length=255)
    instrument_ids: list[int] = Field(min_length=1)


class Instrument(BaseModel):

    instrument_name: str = Field(min_length=1, max_length=100)
    parameters_taken: str = Field(min_length=1, max_length=1000)
    category: str = Field(min_length=1, max_length=100)
    station_categories: list[StationCategory] = Field(min_length=1)
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


class StationInstrument(BaseModel):

    station_id: int = Field(gt=0)
    instrument_id: int = Field(gt=0)
    model: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=100)
    serial_number: str | None = Field(default=None, max_length=100)
    installation_date: date
    calibration_date: date | None = None
    replacement_date: date | None = None
    status: Literal[
        "Operational",
        "Needs Calibration",
        "Needs Replacement",
        "Under Maintenance",
        "Inactive",
    ]
    comment: str | None = Field(default=None, max_length=2000)


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
    filtered.sort(
        key=lambda item: (item.get(column) is None, item.get(column)),
        reverse=sort_order.lower() == "desc",
    )
    return filtered


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
        "SELECT instrument_id FROM instruments WHERE instrument_id = %s",
        (instrument_id,),
    )
    if cursor.fetchone() is None:
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
    if row and (
        "Admin" not in row[0]
        or "Data Quality Control" not in row[0]
        or "Observation Officer" not in row[0]
        or "'IT'" in row[0]
        or "'Data'" in row[0]
        or "'Quality Control'" in row[0]
    ):
        cursor.execute(
            """
            ALTER TABLE users
            MODIFY COLUMN department
                ENUM(
                    'IT', 'Data', 'Quality Control', 'Maintenance',
                    'Admin', 'Data Quality Control', 'Observation Officer'
                ) NOT NULL
            """
        )
        cursor.execute("UPDATE users SET department = 'Admin' WHERE department = 'IT'")
        cursor.execute(
            "UPDATE users SET department = 'Data Quality Control' "
            "WHERE department IN ('Data', 'Quality Control')"
        )
        cursor.execute(
            """
            ALTER TABLE users
            MODIFY COLUMN department ENUM(
                'Admin', 'Maintenance', 'Data Quality Control',
                'Observation Officer'
            ) NOT NULL
            """
        )


def ensure_application_tables(connection):

    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INT AUTO_INCREMENT PRIMARY KEY,
                full_name VARCHAR(100) NOT NULL,
                username VARCHAR(50) NOT NULL UNIQUE,
                email VARCHAR(150),
                department ENUM(
                    'Admin', 'Maintenance', 'Data Quality Control',
                    'Observation Officer'
                ) NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
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
                status VARCHAR(30) NOT NULL,
                suspended BOOLEAN NOT NULL DEFAULT FALSE,
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
                status VARCHAR(30) NOT NULL,
                comment TEXT,
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
        ensure_column(cursor, "stations", "suspended", "BOOLEAN NOT NULL DEFAULT FALSE")
        ensure_column(cursor, "stations", "comment", "TEXT NULL")
        ensure_column(cursor, "stations", "action", "TEXT NULL")
        ensure_column(cursor, "instruments", "parameters_taken", "VARCHAR(1000) NULL")
        ensure_column(cursor, "station_instruments", "calibration_date", "DATE NULL")
        ensure_column(cursor, "station_instruments", "replacement_date", "DATE NULL")
        ensure_column(cursor, "station_instruments", "comment", "TEXT NULL")
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
        connection.commit()
    finally:
        cursor.close()


def get_current_user(
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


def require_data(user=Depends(require_password_change_complete)):
    if user["department"] != "Data Quality Control":
        raise HTTPException(status_code=403, detail="Data Quality Control access required")
    return user


def require_data_or_it(user=Depends(require_password_change_complete)):
    if user["department"] not in (
        "Data Quality Control",
        "Observation Officer",
        "Admin",
    ):
        raise HTTPException(
            status_code=403,
            detail="Data quality access required",
        )
    return user


def require_maintenance(user=Depends(require_password_change_complete)):
    if user["department"] != "Maintenance":
        raise HTTPException(status_code=403, detail="Maintenance access required")
    return user


def require_maintenance_or_it(user=Depends(require_password_change_complete)):
    if user["department"] not in ("Maintenance", "Admin"):
        raise HTTPException(
            status_code=403,
            detail="Maintenance or Administrator access required",
        )
    return user


def require_suspected_data_access(user=Depends(require_password_change_complete)):
    return user


def require_instrument_catalog_access(user=Depends(require_password_change_complete)):
    if user["department"] not in ("Admin", "Maintenance"):
        raise HTTPException(status_code=403, detail="Instrument catalog access is not allowed")
    return user


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
    if user["department"] != "Observation Officer":
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
            AND (users.department <> 'Observation Officer'
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

        instrument_ids = list(dict.fromkeys(record.instrument_ids))
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
                record.issue.strip(),
                record.activity_done.strip(),
                record.recommendations.strip() if record.recommendations else None,
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
            INSERT INTO maintenance_record_instruments (maintenance_id, instrument_id)
            VALUES (%s, %s)
            """,
            [(maintenance_id, instrument_id) for instrument_id in instrument_ids],
        )
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


@app.get("/notifications")
def get_notifications(
    unread_only: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    user=Depends(require_password_change_complete),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT notification_id, notification_type, title, message,
                related_record_type, related_record_id, is_read, created_at
            FROM notifications WHERE user_id = %s
        """
        parameters = [user["user_id"]]
        if unread_only:
            query += " AND is_read = FALSE"
        query += " ORDER BY created_at DESC, notification_id DESC LIMIT %s"
        parameters.append(limit)
        cursor.execute(query, tuple(parameters))
        items = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) AS total FROM notifications WHERE user_id = %s AND is_read = FALSE", (user["user_id"],))
        return {"items": items, "unread_count": cursor.fetchone()["total"]}
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals() and connection.is_connected(): connection.close()


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
    _admin=Depends(require_it),
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
            {"full_name": "full_name", "username": "username", "department": "department", "status": "is_active"},
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
    admin=Depends(require_it),
):
    items = get_users(None, 100, search, sort_by, sort_order, admin)
    headers = ["Name", "Username", "Email", "Role", "Assigned stations", "Status"]
    rows = [[item["full_name"], item["username"], item["email"], item["department"], ", ".join(item["assigned_stations"]), "Active" if item["is_active"] else "Inactive"] for item in items]
    return csv_download("users.csv", headers, rows) if format == "csv" else pdf_download("users.pdf", "User Directory", headers, rows, admin["username"])


@app.post("/users", status_code=201)
def add_user(user: UserCreate, _admin=Depends(require_it)):
    if user.department == "Observation Officer" and not user.station_ids:
        raise HTTPException(
            status_code=400,
            detail="Assign at least one station to the Observation Officer",
        )
    try:
        connection = get_connection()
        user_id = insert_user(connection, user, user.department, True)
        cursor = connection.cursor()
        replace_user_station_assignments(
            cursor,
            user_id,
            user.station_ids if user.department == "Observation Officer" else [],
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
    if user.department == "Observation Officer" and not user.station_ids:
        raise HTTPException(
            status_code=400,
            detail="Assign at least one station to the Observation Officer",
        )
    try:
        connection = get_connection()
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
            user.station_ids if user.department == "Observation Officer" else [],
        )
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
    station_category: StationCategory | None = None,
    suspended: bool | None = None,
    user=Depends(require_password_change_complete),
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
                suspended,
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
        if user["department"] == "Observation Officer":
            query += """
                INNER JOIN user_station_assignments
                    ON user_station_assignments.station_id = stations.station_id
                    AND user_station_assignments.user_id = %s
            """
            parameters.append(user["user_id"])
        if station_category is not None:
            conditions.append("stations.station_category = %s")
            parameters.append(station_category)
        if suspended is not None:
            conditions.append("stations.suspended = %s")
            parameters.append(suspended)
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
                "station_category": "station_category",
                "status": "status",
                "suspended": "suspended",
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
            "suspended": sum(bool(station["suspended"]) for station in stations),
            "suspended_aws": sum(
                bool(station["suspended"])
                and station["station_category"] == "Automatic Weather stations"
                for station in stations
            ),
            "suspended_arg": sum(
                bool(station["suspended"])
                and station["station_category"] == "Automatic Raingauge"
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
    station_category: StationCategory | None = None,
    suspended: bool | None = None,
    user=Depends(require_password_change_complete),
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
        suspended=suspended,
        user=user,
    )
    headers = [
        "Station ID", "Station", "Latitude", "Longitude", "Altitude",
        "Province", "District", "Sector", "Category", "Operational status",
        "Suspended", "Comment", "Action", "Registered at", "Recorded by",
    ]
    rows = [
        [
            item["station_code"], item["station_name"], item["latitude"],
            item["longitude"], item["altitude"], item["province"],
            item["district"], item["sector"], item["station_category"],
            item["status"], 1 if item["suspended"] else 0, item["comment"], item["action"],
            item["created_at"], item["recorded_by"],
        ]
        for item in items
    ]
    if format == "csv":
        return csv_download("stations.csv", headers, rows)
    return pdf_download("stations.pdf", "Station Registry", headers, rows, user["username"])


@app.post("/stations")
def add_station(station: Station, user=Depends(require_it)):

    try:
        connection = get_connection()
        ensure_application_tables(connection)

        cursor = connection.cursor()

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
                suspended,
                comment,
                action,
                created_by_user_id,
                recorded_by_username
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                station.suspended,
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
    user=Depends(require_it),
):
    try:
        content = (await request.body()).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV file must use UTF-8 encoding") from exc

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV file has no header row")

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
        try:
            station = Station(
                station_code=row.get("station_id") or row.get("station_code") or "",
                station_name=row.get("station_name") or "",
                latitude=row.get("latitude") or "",
                longitude=row.get("longitude") or "",
                altitude=row.get("altitude") or "",
                province=row.get("province") or "",
                district=row.get("district") or "",
                sector=row.get("sector") or "",
                station_category=row.get("station_category") or "",
                status=row.get("operational_status") or row.get("status") or "",
                suspended=row.get("suspended") or "0",
                comment=row.get("comment") or None,
                action=row.get("action") or row.get("actions") or None,
            )
        except ValidationError as exc:
            message = exc.errors()[0]["msg"]
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
        placeholders = ", ".join(["%s"] * len(stations))
        cursor.execute(
            f"SELECT station_code FROM stations WHERE LOWER(station_code) IN ({placeholders})",
            tuple(station_codes),
        )
        existing_codes = [row[0] for row in cursor.fetchall()]
        if existing_codes:
            raise HTTPException(
                status_code=409,
                detail="Station ID already exists: " + ", ".join(existing_codes),
            )

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
                suspended,
                comment,
                action,
                created_by_user_id,
                recorded_by_username
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    station.suspended,
                    station.comment.strip() if station.comment else None,
                    station.action.strip() if station.action else None,
                    user["user_id"],
                    user["username"],
                )
                for station in stations
            ],
        )
        connection.commit()
        return {"message": "Stations imported successfully", "imported": len(stations)}
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
                suspended = %s,
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
                station.suspended,
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
        instruments = filter_sort_collection(
            instruments, search,
            ["instrument_name", "parameters_taken", "category", "description"],
            sort_by, sort_order,
            {"instrument_name": "instrument_name", "category": "category", "status": "is_active"},
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


@app.post("/instruments/import", status_code=201)
async def import_instruments(
    request: Request,
    user=Depends(require_it),
):
    try:
        content = (await request.body()).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV file must use UTF-8 encoding") from exc

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV file has no header row")

    instruments = []
    instrument_names = set()
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
        try:
            instrument = Instrument(
                instrument_name=row.get("instrument_name") or "",
                parameters_taken=(
                    row.get("parameters_it_takes")
                    or row.get("parameters_taken")
                    or ""
                ),
                category=row.get("category") or "",
                station_categories=[
                    value.strip()
                    for value in (
                        row.get("station_categories")
                        or row.get("station_category")
                        or ""
                    ).replace(";", "|").split("|")
                    if value.strip()
                ],
                description=row.get("description") or None,
                is_active=True,
            )
        except ValidationError as exc:
            message = exc.errors()[0]["msg"]
            raise HTTPException(
                status_code=400,
                detail=f"CSV row {row_number}: {message}",
            ) from exc

        normalized_name = instrument.instrument_name.strip().lower()
        if normalized_name in instrument_names:
            raise HTTPException(
                status_code=400,
                detail=(
                    f'CSV row {row_number}: duplicate instrument name '
                    f'"{instrument.instrument_name}"'
                ),
            )
        instrument_names.add(normalized_name)
        instruments.append(instrument)

    if not instruments:
        raise HTTPException(status_code=400, detail="CSV file has no instrument rows")

    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        placeholders = ", ".join(["%s"] * len(instrument_names))
        cursor.execute(
            f"""
            SELECT instrument_name
            FROM instruments
            WHERE LOWER(instrument_name) IN ({placeholders})
            """,
            tuple(instrument_names),
        )
        existing_names = [row[0] for row in cursor.fetchall()]
        if existing_names:
            raise HTTPException(
                status_code=409,
                detail=(
                    "These instruments already exist: "
                    + ", ".join(sorted(existing_names))
                ),
            )
        for instrument in instruments:
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
                VALUES (%s, %s, %s, %s, TRUE, %s, %s)
                """,
                (
                    instrument.instrument_name.strip(),
                    instrument.parameters_taken.strip(),
                    instrument.category.strip(),
                    instrument.description.strip() if instrument.description else None,
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
            "imported": len(instruments),
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


@app.get("/maintenance")
def get_maintenance_records(
    station_id: int | None = Query(default=None, gt=0),
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=25, ge=10, le=100),
    search: str | None = Query(default=None, max_length=100),
    sort_by: str = "maintenance_date",
    sort_order: Literal["asc", "desc"] = "desc",
    date_from: date | None = None,
    date_to: date | None = None,
    user=Depends(require_password_change_complete),
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
                maintenance_date,
                issue,
                activity_done,
                recommendations,
                technicians,
                COALESCE(
                    maintenance_records.recorded_by_username,
                    creator.username
                ) AS recorded_by,
                instruments.instrument_id,
                instruments.instrument_name
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

        if station_id is not None:
            conditions.append("maintenance_records.station_id = %s")
            parameters.append(station_id)
        if user["department"] == "Observation Officer":
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
                    "instrument_name": row["instrument_name"]
                })

        items = list(records.values())
        for item in items:
            item["instrument_search"] = " ".join(
                instrument["instrument_name"] for instrument in item["instruments"]
            )
        items = filter_sort_collection(
            items, search,
            ["station_code", "station_name", "issue", "activity_done", "recommendations", "technicians", "instrument_search"],
            sort_by, sort_order,
            {"station": "station_name", "maintenance_date": "maintenance_date", "issue": "issue", "technicians": "technicians"},
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


@app.get("/maintenance/export")
def export_maintenance(
    format: Literal["csv", "pdf"], station_id: int | None = Query(default=None, gt=0),
    search: str | None = None, sort_by: str = "maintenance_date",
    sort_order: Literal["asc", "desc"] = "desc", date_from: date | None = None,
    date_to: date | None = None, user=Depends(require_password_change_complete),
):
    items = get_maintenance_records(station_id, None, 100, search, sort_by, sort_order, date_from, date_to, user)
    headers = ["Station", "Date", "Instruments", "Issue", "Activity done", "Recommendations", "Technicians", "Recorded by"]
    rows = [[f'{item["station_code"]} - {item["station_name"]}', item["maintenance_date"], ", ".join(i["instrument_name"] for i in item["instruments"]), item["issue"], item["activity_done"], item["recommendations"], item["technicians"], item["recorded_by"]] for item in items]
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

        instrument_ids = list(dict.fromkeys(record.instrument_ids))
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
                record.issue.strip(),
                record.activity_done.strip(),
                record.recommendations.strip()
                    if record.recommendations else None,
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
                instrument_id
            )
            VALUES (%s, %s)
            """,
            [
                (maintenance_id, instrument_id)
                for instrument_id in instrument_ids
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
    station_id: int | None = Query(default=None, gt=0),
    status: Literal["Open", "Under Review", "Resolved"] | None = None,
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=25, ge=10, le=100),
    search: str | None = Query(default=None, max_length=100),
    sort_by: str = "reported_at",
    sort_order: Literal["asc", "desc"] = "desc",
    date_from: date | None = None,
    date_to: date | None = None,
    user=Depends(require_suspected_data_access),
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
        if station_id is not None:
            conditions.append("suspected_data_records.station_id = %s")
            parameters.append(station_id)
        if status is not None:
            conditions.append("suspected_data_records.status = %s")
            parameters.append(status)
        if user["department"] == "Observation Officer":
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
            {"reported_at": "reported_at", "station": "station_name", "measurement": "issue", "status": "status", "resolved_at": "resolved_at"},
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
    format: Literal["csv", "pdf"], station_id: int | None = Query(default=None, gt=0),
    status: Literal["Open", "Under Review", "Resolved"] | None = None,
    search: str | None = None, sort_by: str = "reported_at",
    sort_order: Literal["asc", "desc"] = "desc", date_from: date | None = None,
    date_to: date | None = None, user=Depends(require_suspected_data_access),
):
    items = get_suspected_data_records(station_id, status, None, 100, search, sort_by, sort_order, date_from, date_to, user)
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
            cursor, ["Maintenance"], "suspected_data_reported",
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
                cursor, ["Data Quality Control", "Observation Officer"],
                "suspected_data_resolved", "Final review required",
                f"{record.issue.strip()} at {station[0]} - {station[1]} is ready for final review.",
                "suspected_data", suspected_data_id, station_id=record.station_id,
            )
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
    station_id: int | None = Query(default=None, gt=0),
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=25, ge=10, le=100),
    search: str | None = Query(default=None, max_length=100),
    sort_by: str = "station",
    sort_order: Literal["asc", "desc"] = "asc",
    date_from: date | None = None,
    date_to: date | None = None,
    user=Depends(require_password_change_complete),
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
                station_instruments.instrument_id,
                instruments.instrument_name,
                instruments.parameters_taken,
                station_instruments.model,
                station_instruments.manufacturer,
                station_instruments.serial_number,
                station_instruments.installation_date,
                station_instruments.calibration_date,
                station_instruments.replacement_date,
                station_instruments.status,
                station_instruments.comment,
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
        if station_id is not None:
            conditions.append("station_instruments.station_id = %s")
            parameters.append(station_id)
        if user["department"] == "Observation Officer":
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
            ["station_code", "station_name", "station_category", "instrument_name", "parameters_taken", "model", "manufacturer", "serial_number", "status", "comment"],
            sort_by, sort_order,
            {"station": "station_name", "instrument": "instrument_name", "status": "status", "installation_date": "installation_date", "calibration_date": "calibration_date", "replacement_date": "replacement_date"},
            date_from, date_to, "installation_date",
        )
        statuses = {}
        categories = {}
        for item in items:
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
    format: Literal["csv", "pdf"], station_id: int | None = Query(default=None, gt=0),
    search: str | None = None, sort_by: str = "station",
    sort_order: Literal["asc", "desc"] = "asc", date_from: date | None = None,
    date_to: date | None = None, user=Depends(require_password_change_complete),
):
    items = get_station_instruments(station_id, None, 100, search, sort_by, sort_order, date_from, date_to, user)
    headers = ["Station", "Category", "Instrument", "Model", "Manufacturer", "Serial number", "Installation date", "Calibration date", "Replacement date", "Status", "Comment"]
    rows = [[f'{item["station_code"]} - {item["station_name"]}', item["station_category"], item["instrument_name"], item["model"], item["manufacturer"], item["serial_number"], item["installation_date"], item["calibration_date"], item["replacement_date"], item["status"], item["comment"]] for item in items]
    return csv_download("station-instruments.csv", headers, rows) if format == "csv" else pdf_download("station-instruments.pdf", "Station Instruments", headers, rows, user["username"])


@app.post("/station-instruments", status_code=201)
def add_station_instrument(
    item: StationInstrument,
    user=Depends(require_maintenance_or_it),
):
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        ensure_instrument_matches_station_category(
            cursor,
            item.station_id,
            item.instrument_id,
        )
        cursor.execute(
            """
            INSERT INTO station_instruments (
                station_id,
                instrument_id,
                model,
                manufacturer,
                serial_number,
                installation_date,
                calibration_date,
                replacement_date,
                status,
                comment,
                created_by_user_id,
                recorded_by_username
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                item.status,
                item.comment.strip() if item.comment else None,
                user["user_id"],
                user["username"],
            ),
        )
        station_instrument_id = cursor.lastrowid
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
        cursor.execute(
            "SELECT station_instrument_id FROM station_instruments "
            "WHERE station_instrument_id = %s",
            (station_instrument_id,),
        )
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Station instrument not found")
        ensure_instrument_matches_station_category(
            cursor,
            item.station_id,
            item.instrument_id,
        )
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
                status = %s,
                comment = %s,
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
                item.status,
                item.comment.strip() if item.comment else None,
                user["user_id"],
                user["username"],
                station_instrument_id,
            ),
        )
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
