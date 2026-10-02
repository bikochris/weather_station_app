"""Copy the configured database to a new schema without removing the source."""

import argparse
import os
import re
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv


def migrate(target):
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
    source = os.environ["DB_NAME"]
    if not all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) for name in (source, target)):
        raise ValueError("Database names must contain only letters, digits, and underscores")
    if source == target:
        raise ValueError("The application already uses the requested database")
    connection = mysql.connector.connect(host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3308")), user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""), autocommit=False)
    cursor = connection.cursor()
    locked = False
    try:
        cursor.execute("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s", (target,))
        if cursor.fetchone():
            raise ValueError("The target database already exists; choose a new name")
        for catalog in ("TRIGGERS", "ROUTINES", "EVENTS"):
            column = {"TRIGGERS": "TRIGGER_SCHEMA", "ROUTINES": "ROUTINE_SCHEMA", "EVENTS": "EVENT_SCHEMA"}[catalog]
            cursor.execute(f"SELECT COUNT(*) FROM INFORMATION_SCHEMA.{catalog} WHERE {column} = %s", (source,))
            if cursor.fetchone()[0]:
                raise ValueError("This database includes stored objects; use a complete database backup for migration")
        cursor.execute("SELECT TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = %s", (source,))
        tables = cursor.fetchall()
        if not tables or any(kind != "BASE TABLE" for _, kind in tables):
            raise ValueError("The source must contain tables only")
        cursor.execute("SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s", (source,))
        charset, collation = cursor.fetchone()
        cursor.execute(f"CREATE DATABASE `{target}` CHARACTER SET {charset} COLLATE {collation}")
        cursor.execute(f"USE `{target}`")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table, _ in tables:
            cursor.execute(f"SHOW CREATE TABLE `{source}`.`{table}`")
            definition = cursor.fetchone()[1].replace(f"`{source}`.", f"`{target}`.")
            cursor.execute(definition)
        locks = [f"`{source}`.`{table}` READ, `{target}`.`{table}` WRITE" for table, _ in tables]
        cursor.execute("LOCK TABLES " + ", ".join(locks))
        locked = True
        total = 0
        for table, _ in tables:
            cursor.execute(f"INSERT INTO `{target}`.`{table}` SELECT * FROM `{source}`.`{table}`")
            cursor.execute(f"SELECT COUNT(*) FROM `{source}`.`{table}`")
            source_count = cursor.fetchone()[0]
            cursor.execute(f"SELECT COUNT(*) FROM `{target}`.`{table}`")
            if cursor.fetchone()[0] != source_count:
                raise RuntimeError(f"Verification failed for {table}")
            total += source_count
        connection.commit()
        cursor.execute("UNLOCK TABLES")
        locked = False
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        print(f"Verified {len(tables)} tables and {total} rows in {target}. Source {source} retained.")
        print(f"Set DB_NAME={target} in .env and restart the application to switch databases.")
    except Exception:
        connection.rollback()
        raise
    finally:
        if locked:
            cursor.execute("UNLOCK TABLES")
        cursor.close()
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    migrate(parser.parse_args().target)
