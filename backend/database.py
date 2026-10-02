import os
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

try:
    from .activity import ActivityConnection
except ImportError:
    from activity import ActivityConnection


load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)


def get_connection():
    return ActivityConnection(mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "Chris32"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "Weather_Stations_App"),
        port=int(os.getenv("DB_PORT", "3308")),
    ))
