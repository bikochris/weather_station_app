import os
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from mysql.connector import Error as MySQLError, IntegrityError
from pydantic import BaseModel, Field
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


class Station(BaseModel):

    station_name: str = Field(min_length=1, max_length=100)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    status: str


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
    category: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    is_active: bool = True


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
    department: Literal["IT", "Data"]
    password: str = Field(min_length=8, max_length=128)


class UserUpdate(BaseModel):

    full_name: str = Field(min_length=2, max_length=100)
    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    email: str | None = Field(default=None, max_length=150)
    department: Literal["IT", "Data"]
    is_active: bool = True
    password: str | None = Field(default=None, min_length=8, max_length=128)


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
                department ENUM('IT', 'Data') NOT NULL,
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
            CREATE TABLE IF NOT EXISTS stations (
                station_id INT AUTO_INCREMENT PRIMARY KEY,
                station_name VARCHAR(100) NOT NULL,
                latitude DECIMAL(9,6),
                longitude DECIMAL(9,6),
                status VARCHAR(30),
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
            CREATE TABLE IF NOT EXISTS instruments (
                instrument_id INT AUTO_INCREMENT PRIMARY KEY,
                instrument_name VARCHAR(100) NOT NULL UNIQUE,
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
        ensure_column(
            cursor,
            "users",
            "must_change_password",
            "BOOLEAN NOT NULL DEFAULT FALSE",
        )
        for table_name in ("stations", "maintenance_records", "instruments"):
            ensure_column(cursor, table_name, "created_by_user_id", "INT NULL")
            ensure_column(cursor, table_name, "recorded_by_username", "VARCHAR(50) NULL")
        ensure_column(
            cursor,
            "stations",
            "created_at",
            "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
        )
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
        cursor.executemany(
            """
            INSERT IGNORE INTO instruments (
                instrument_name,
                category,
                description
            )
            VALUES (%s, %s, %s)
            """,
            [
                ("Datalogger", "Data acquisition", "Records and stores station observations."),
                ("Battery", "Power", "Provides backup power to station equipment."),
                ("Solar Panel", "Power", "Charges the station battery."),
                ("Rain Gauge", "Precipitation", "Measures rainfall amount."),
                ("Temperature Sensor", "Temperature", "Measures air temperature."),
                ("Humidity Sensor", "Humidity", "Measures relative humidity."),
                ("Wind Sensor", "Wind", "Measures wind speed and direction."),
                ("Barometer", "Pressure", "Measures atmospheric pressure."),
                ("Radiation Shield", "Housing", "Protects sensors from direct radiation."),
                ("Modem", "Communication", "Transmits observations to the data center.")
            ]
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
    if user["department"] != "IT":
        raise HTTPException(status_code=403, detail="IT access required")
    return user


@app.put("/maintenance/{maintenance_id}")
def update_maintenance_record(
    maintenance_id: int,
    record: MaintenanceRecord,
    _admin=Depends(require_it),
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
            "SELECT station_id FROM stations WHERE station_id = %s",
            (record.station_id,),
        )
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Station not found")

        instrument_ids = list(dict.fromkeys(record.instrument_ids))
        placeholders = ", ".join(["%s"] * len(instrument_ids))
        cursor.execute(
            f"""
            SELECT instrument_id
            FROM instruments
            WHERE instrument_id IN ({placeholders}) AND is_active = TRUE
            """,
            tuple(instrument_ids),
        )
        if {row[0] for row in cursor.fetchall()} != set(instrument_ids):
            raise HTTPException(
                status_code=400,
                detail="One or more selected instruments are unavailable",
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
    _admin=Depends(require_it),
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
        connection.commit()
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
        user_id = insert_user(connection, user, "IT", False)
        return {"message": "IT administrator created", "user_id": user_id}
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


@app.get("/users")
def get_users(_admin=Depends(require_it)):
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
        return cursor.fetchall()
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.post("/users", status_code=201)
def add_user(user: UserCreate, _admin=Depends(require_it)):
    try:
        connection = get_connection()
        user_id = insert_user(connection, user, user.department, True)
        return {"message": "User created", "user_id": user_id}
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Username already exists") from exc
    except MySQLError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        if "connection" in locals() and connection.is_connected():
            connection.close()


@app.put("/users/{user_id}")
def update_user(
    user_id: int,
    user: UserUpdate,
    admin=Depends(require_it),
):
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
            existing["department"] == "IT"
            and bool(existing["is_active"])
            and (user.department != "IT" or not user.is_active)
        )
        if removing_it_access:
            cursor.execute(
                "SELECT COUNT(*) AS total FROM users "
                "WHERE department = 'IT' AND is_active = TRUE"
            )
            if cursor.fetchone()["total"] <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="The last active IT administrator cannot be disabled or reassigned",
                )

        if user_id == admin["user_id"] and (
            user.department != "IT" or not user.is_active
        ):
            raise HTTPException(
                status_code=409,
                detail="You cannot remove your own IT access",
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

        if existing["department"] == "IT" and bool(existing["is_active"]):
            cursor.execute(
                "SELECT COUNT(*) AS total FROM users "
                "WHERE department = 'IT' AND is_active = TRUE"
            )
            if cursor.fetchone()["total"] <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="The last active IT administrator cannot be deleted",
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
def get_stations(_user=Depends(require_password_change_complete)):

    try:
        connection = get_connection()

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                station_id,
                station_name,
                CAST(latitude AS DOUBLE) AS latitude,
                CAST(longitude AS DOUBLE) AS longitude,
                status,
                COALESCE(stations.recorded_by_username, creator.username) AS recorded_by
            FROM stations
            LEFT JOIN users AS creator
                ON creator.user_id = stations.created_by_user_id
            ORDER BY station_id
            """
        )

        stations = cursor.fetchall()

        return stations

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


@app.post("/stations")
def add_station(station: Station, user=Depends(require_password_change_complete)):

    try:
        connection = get_connection()

        cursor = connection.cursor()

        sql = """
            INSERT INTO stations
            (
                station_name,
                latitude,
                longitude,
                status,
                created_by_user_id,
                recorded_by_username
            )
            VALUES (%s, %s, %s, %s, %s, %s)
        """

        cursor.execute(
            sql,
            (
                station.station_name,
                station.latitude,
                station.longitude,
                station.status,
                user["user_id"],
                user["username"],
            )
        )

        connection.commit()

        return {
            "message": "Station added successfully"
        }

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


@app.put("/stations/{station_id}")
def update_station(
    station_id: int,
    station: Station,
    _admin=Depends(require_it),
):
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE stations
            SET station_name = %s, latitude = %s, longitude = %s, status = %s
            WHERE station_id = %s
            """,
            (
                station.station_name.strip(),
                station.latitude,
                station.longitude,
                station.status,
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
            detail="This station has maintenance history and cannot be deleted",
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
    _user=Depends(require_password_change_complete),
):

    try:
        connection = get_connection()
        ensure_application_tables(connection)

        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT
                instruments.instrument_id,
                instruments.instrument_name,
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

        return cursor.fetchall()

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


@app.post("/instruments", status_code=201)
def add_instrument(instrument: Instrument, user=Depends(require_password_change_complete)):

    try:
        connection = get_connection()
        ensure_application_tables(connection)

        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO instruments (
                instrument_name,
                category,
                description,
                is_active,
                created_by_user_id,
                recorded_by_username
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                instrument.instrument_name.strip(),
                instrument.category.strip() if instrument.category else None,
                instrument.description.strip() if instrument.description else None,
                instrument.is_active,
                user["user_id"],
                user["username"],
            )
        )
        connection.commit()

        return {
            "message": "Instrument added successfully",
            "instrument_id": cursor.lastrowid
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


@app.put("/instruments/{instrument_id}")
def update_instrument(
    instrument_id: int,
    instrument: Instrument,
    _admin=Depends(require_it),
):
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE instruments
            SET instrument_name = %s,
                category = %s,
                description = %s,
                is_active = %s
            WHERE instrument_id = %s
            """,
            (
                instrument.instrument_name.strip(),
                instrument.category.strip() if instrument.category else None,
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
            detail="This instrument is used in maintenance history and cannot be deleted",
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
    _user=Depends(require_password_change_complete),
):

    try:
        connection = get_connection()
        ensure_application_tables(connection)

        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT
                maintenance_records.maintenance_id,
                maintenance_records.station_id,
                stations.station_name,
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
        parameters = ()

        if station_id is not None:
            query += " WHERE maintenance_records.station_id = %s"
            parameters = (station_id,)

        query += (
            " ORDER BY maintenance_records.maintenance_date DESC, "
            "maintenance_records.maintenance_id DESC, "
            "instruments.instrument_name"
        )
        cursor.execute(query, parameters)

        records = {}

        for row in cursor.fetchall():
            maintenance_id = row["maintenance_id"]

            if maintenance_id not in records:
                records[maintenance_id] = {
                    "maintenance_id": maintenance_id,
                    "station_id": row["station_id"],
                    "station_name": row["station_name"],
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

        return list(records.values())

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


@app.post("/maintenance", status_code=201)
def add_maintenance_record(
    record: MaintenanceRecord,
    user=Depends(require_password_change_complete),
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
            SELECT instrument_id
            FROM instruments
            WHERE instrument_id IN ({placeholders})
                AND is_active = TRUE
            """,
            tuple(instrument_ids)
        )

        available_instrument_ids = {
            row[0] for row in cursor.fetchall()
        }

        if available_instrument_ids != set(instrument_ids):
            raise HTTPException(
                status_code=400,
                detail="One or more selected instruments are unavailable"
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
