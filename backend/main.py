import mariadb

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import database connection from backend/database.py
from backend.database import get_connection


# ============================================================
# CREATE FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="Weather Station API",
    description="Weather Station Management System connected to MariaDB",
    version="1.0"
)


# ============================================================
# CORS CONFIGURATION
# Allows the HTML/JavaScript frontend to communicate
# with this FastAPI backend.
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# STATION DATA MODEL
# Defines the data expected when adding or updating a station.
# ============================================================

class Station(BaseModel):
    station_name: str
    latitude: float
    longitude: float
    status: str


# ============================================================
# HOME PAGE
# ============================================================

@app.get("/")
def home():

    return {
        "message": "Weather Station API is working"
    }


# ============================================================
# TEST DATABASE CONNECTION
# ============================================================

@app.get("/database-test")
def database_test():

    connection = None
    cursor = None

    try:

        connection = get_connection()

        cursor = connection.cursor()

        cursor.execute("SELECT DATABASE()")

        database = cursor.fetchone()

        return {
            "status": "Connected successfully",
            "database": database[0]
        }

    except mariadb.Error as error:

        raise HTTPException(
            status_code=500,
            detail=f"Database connection error: {error}"
        )

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()


# ============================================================
# GET ALL STATIONS
# ============================================================

@app.get("/stations")
def get_stations():

    connection = None
    cursor = None

    try:

        connection = get_connection()

        cursor = connection.cursor(
            dictionary=True
        )

        sql = """
            SELECT
                station_id,
                station_name,
                latitude,
                longitude,
                status
            FROM stations
            ORDER BY station_id
        """

        cursor.execute(sql)

        stations = cursor.fetchall()

        return stations

    except mariadb.Error as error:

        raise HTTPException(
            status_code=500,
            detail=f"Database error: {error}"
        )

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()


# ============================================================
# GET ONE STATION
# ============================================================

@app.get("/stations/{station_id}")
def get_station(station_id: int):

    connection = None
    cursor = None

    try:

        connection = get_connection()

        cursor = connection.cursor(
            dictionary=True
        )

        sql = """
            SELECT
                station_id,
                station_name,
                latitude,
                longitude,
                status
            FROM stations
            WHERE station_id = ?
        """

        cursor.execute(
            sql,
            (station_id,)
        )

        station = cursor.fetchone()

        if station is None:

            raise HTTPException(
                status_code=404,
                detail="Station not found"
            )

        return station

    except mariadb.Error as error:

        raise HTTPException(
            status_code=500,
            detail=f"Database error: {error}"
        )

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()


# ============================================================
# ADD A NEW STATION
# ============================================================

@app.post("/stations")
def add_station(station: Station):

    connection = None
    cursor = None

    try:

        connection = get_connection()

        cursor = connection.cursor()

        sql = """
            INSERT INTO stations
            (
                station_name,
                latitude,
                longitude,
                status
            )
            VALUES (?, ?, ?, ?)
        """

        cursor.execute(
            sql,
            (
                station.station_name,
                station.latitude,
                station.longitude,
                station.status
            )
        )

        connection.commit()

        new_station_id = cursor.lastrowid

        return {
            "message": "Station added successfully",
            "station_id": new_station_id
        }

    except mariadb.Error as error:

        if connection:
            connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Database error: {error}"
        )

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()


# ============================================================
# UPDATE A STATION
# ============================================================

@app.put("/stations/{station_id}")
def update_station(
    station_id: int,
    station: Station
):

    connection = None
    cursor = None

    try:

        connection = get_connection()

        cursor = connection.cursor()

        sql = """
            UPDATE stations
            SET
                station_name = ?,
                latitude = ?,
                longitude = ?,
                status = ?
            WHERE station_id = ?
        """

        cursor.execute(
            sql,
            (
                station.station_name,
                station.latitude,
                station.longitude,
                station.status,
                station_id
            )
        )

        connection.commit()

        if cursor.rowcount == 0:

            raise HTTPException(
                status_code=404,
                detail="Station not found"
            )

        return {
            "message": "Station updated successfully"
        }

    except mariadb.Error as error:

        if connection:
            connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Database error: {error}"
        )

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()


# ============================================================
# DELETE A STATION
# ============================================================

@app.delete("/stations/{station_id}")
def delete_station(station_id: int):

    connection = None
    cursor = None

    try:

        connection = get_connection()

        cursor = connection.cursor()

        sql = """
            DELETE FROM stations
            WHERE station_id = ?
        """

        cursor.execute(
            sql,
            (station_id,)
        )

        connection.commit()

        if cursor.rowcount == 0:

            raise HTTPException(
                status_code=404,
                detail="Station not found"
            )

        return {
            "message": "Station deleted successfully"
        }

    except mariadb.Error as error:

        if connection:
            connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Database error: {error}"
        )

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()