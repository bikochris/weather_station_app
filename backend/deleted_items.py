"""Transactional backups for records removed through the application."""

import base64
import csv
import io
import json
import zlib
from datetime import date, datetime
from decimal import Decimal
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import HTTPException
from mysql.connector import IntegrityError


DELETED_SPECS = {
    "stations": ("stations", "station_id"),
    "users": ("users", "user_id"),
    "instruments": ("instruments", "instrument_id"),
    "maintenance": ("maintenance_records", "maintenance_id"),
    "station_instruments": ("station_instruments", "station_instrument_id"),
    "suspected_data": ("suspected_data_records", "suspected_data_id"),
    "station_inspections": ("station_inspections", "inspection_id"),
    "station_inspection_reports": ("station_inspection_reports", "report_id"),
    "maintenance_reports": ("maintenance_reports", "report_id"),
    "pre_maintenance_reports": ("pre_maintenance_reports", "report_id"),
    "data_requests": ("monthly_data_requests", "data_request_id"),
    "category_data_counts": ("monthly_category_data_counts", "data_count_id"),
    "combined_data_counts": ("monthly_combined_data_counts", "data_count_id"),
    "station_visitors": ("station_visitors", "visitor_id"),
    "station_volunteers": ("station_volunteers", "volunteer_id"),
    "visitor_categories": ("visitor_categories", "category_id"),
    "maintenance_frequencies": ("category_maintenance_targets", "station_category"),
    "reporting_status": ("monthly_reporting_status", "reporting_status_id"),
}


DELETED_ITEMS_SCHEMA = """CREATE TABLE IF NOT EXISTS deleted_items (
    deleted_item_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    entity_type VARCHAR(60) NOT NULL,
    entity_id VARCHAR(100) NOT NULL,
    title VARCHAR(255) NOT NULL,
    district VARCHAR(1000) NULL,
    deleted_by_username VARCHAR(50) NOT NULL,
    deleted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    restored_by_username VARCHAR(50) NULL,
    restored_at TIMESTAMP NULL,
    row_count INT NOT NULL,
    payload LONGBLOB NOT NULL,
    INDEX idx_deleted_items_time (deleted_at, deleted_item_id),
    INDEX idx_deleted_items_type (entity_type, restored_at)
)"""


def _json_value(value):
    if isinstance(value, (bytes, bytearray)):
        return {"__binary__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    raise TypeError(f"Cannot archive {type(value).__name__}")


def _restore_value(value):
    if isinstance(value, dict) and set(value) == {"__binary__"}:
        return base64.b64decode(value["__binary__"], validate=True)
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


def _read_rows(cursor, table, key, value):
    cursor.execute(f"SELECT * FROM `{table}` WHERE `{key}` = %s", (value,))
    return cursor.fetchall()


def _add_rows(blocks, cursor, table, key, value):
    rows = _read_rows(cursor, table, key, value)
    if rows:
        blocks.append({"table": table, "rows": rows})
    return rows


def _record_title(row, entity_type, entity_id):
    for key in ("station_name", "instrument_name", "full_name", "volunteer_name",
                "original_filename", "name", "station_code", "category", "report_month"):
        if row.get(key):
            return str(row[key])[:255]
    return f"{entity_type.replace('_', ' ').title()} #{entity_id}"[:255]


def _history_for_item(cursor, item):
    entity_type = item["entity_type"]
    entity_id = item["entity_id"]
    if entity_type == "reporting_status":
        cursor.execute("""SELECT before_data, after_data, changed_by_username,
            edit_reason, changed_at, action FROM monthly_reporting_changes
            WHERE reporting_status_id = %s AND changed_at <= %s
            ORDER BY changed_at DESC, change_id DESC""",
            (entity_id, item["deleted_at"]))
    else:
        if entity_type == "maintenance_frequencies":
            blocks = json.loads(zlib.decompress(item["payload"]).decode("utf-8"))
            year = blocks[0]["rows"][0]["fiscal_start_year"]
            entity_id = f"{year}|{entity_id}"
        cursor.execute("""SELECT before_data, after_data, changed_by_username,
            edit_reason, changed_at FROM record_edit_history
            WHERE entity_type = %s AND entity_id = %s AND changed_at <= %s
            ORDER BY changed_at DESC, history_id DESC""",
            (entity_type, entity_id, item["deleted_at"]))
    events = cursor.fetchall()
    for event in events:
        for key in ("before_data", "after_data"):
            value = event[key]
            if isinstance(value, (str, bytes)):
                event[key] = json.loads(value)
            if isinstance(event[key], dict):
                for secret in ("password_hash", "token_hash"):
                    if secret in event[key]:
                        event[key][secret] = "[redacted]"
    return events


def get_deleted_item_history(connection, deleted_item_id):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("""SELECT entity_type, entity_id, deleted_at, payload
            FROM deleted_items WHERE deleted_item_id = %s""", (deleted_item_id,))
        item = cursor.fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="Deleted item not found")
        return _history_for_item(cursor, item)
    finally:
        cursor.close()


def _history_csv(events):
    keys = list(dict.fromkeys(key for event in events for snapshot in
        (event.get("before_data") or {}, event.get("after_data") or {}) for key in snapshot))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Changed at", "Changed by", "Action", "Reason", *keys])
    for event in events:
        before = event.get("before_data") or {}
        after = event.get("after_data") or {}
        values = []
        for key in keys:
            value = after.get(key, before.get(key))
            if key in {"password_hash", "token_hash"}:
                value = "[redacted]"
            elif isinstance(value, (dict, list)):
                value = str(value)
            values.append(value if value is not None else "")
        writer.writerow([event["changed_at"], event["changed_by_username"],
                         event.get("action", "Edited"), event.get("edit_reason") or "", *values])
    return output.getvalue().encode("utf-8-sig")


def archive_deleted_item(connection, entity_type, entity_id, user, *, fiscal_start_year=None,
                         volunteer_file_kinds=None):
    """Insert a backup on the same connection as the pending DELETE."""
    cursor = connection.cursor(dictionary=True)
    try:
        blocks = []
        if entity_type == "volunteer_data":
            month = str(entity_id)[:7] + "-01"
            files = _read_rows(cursor, "volunteer_data_files", "report_month", month)
            if volunteer_file_kinds is not None:
                files = [row for row in files if row["file_kind"] in volunteer_file_kinds]
            comments = (_read_rows(cursor, "volunteer_report_comments", "report_month", month)
                        if volunteer_file_kinds is None else [])
            if files:
                blocks.append({"table": "volunteer_data_files", "rows": files})
            if comments:
                blocks.append({"table": "volunteer_report_comments", "rows": comments})
            if not blocks:
                raise HTTPException(status_code=404, detail="No volunteer data to delete")
            row = {"report_month": month}
        else:
            if entity_type not in DELETED_SPECS:
                raise ValueError("Unsupported deleted item type")
            table, key = DELETED_SPECS[entity_type]
            if entity_type == "maintenance_frequencies":
                cursor.execute("SELECT * FROM category_maintenance_targets WHERE station_category = %s AND fiscal_start_year = %s",
                               (entity_id, fiscal_start_year))
                parent = cursor.fetchall()
            else:
                parent = _read_rows(cursor, table, key, entity_id)
            if not parent:
                raise HTTPException(status_code=404, detail="Record not found")
            blocks.append({"table": table, "rows": parent})
            row = parent[0]
            if entity_type == "maintenance":
                _add_rows(blocks, cursor, "maintenance_record_instruments", "maintenance_id", entity_id)
            elif entity_type == "instruments":
                _add_rows(blocks, cursor, "instrument_station_categories", "instrument_id", entity_id)
            elif entity_type == "users":
                _add_rows(blocks, cursor, "user_station_assignments", "user_id", entity_id)
            elif entity_type == "stations":
                _add_rows(blocks, cursor, "user_station_assignments", "station_id", entity_id)
                _add_rows(blocks, cursor, "station_maintenance_frequencies", "station_id", entity_id)
            elif entity_type in {"maintenance_reports", "pre_maintenance_reports", "station_inspection_reports"}:
                child = {"maintenance_reports": "maintenance_report_stations",
                         "pre_maintenance_reports": "pre_maintenance_report_stations",
                         "station_inspection_reports": "station_inspection_report_stations"}[entity_type]
                _add_rows(blocks, cursor, child, "report_id", entity_id)
            elif entity_type == "station_inspections":
                _add_rows(blocks, cursor, "station_inspection_comments", "inspection_id", entity_id)
                _add_rows(blocks, cursor, "station_inspection_photos", "inspection_id", entity_id)
                reports = _add_rows(blocks, cursor, "station_inspection_reports", "inspection_id", entity_id)
                for report in reports:
                    _add_rows(blocks, cursor, "station_inspection_report_stations", "report_id", report["report_id"])
            elif entity_type == "reporting_status":
                _add_rows(blocks, cursor, "monthly_non_reported_station_files", "report_month", row["report_month"])

        station_id = row.get("station_id")
        district = row.get("district")
        if station_id and not district:
            cursor.execute("SELECT district FROM stations WHERE station_id = %s", (station_id,))
            station = cursor.fetchone()
            district = station["district"] if station else None
        if entity_type in {"maintenance_reports", "pre_maintenance_reports", "station_inspection_reports"}:
            linked_ids = {entry["station_id"] for block in blocks for entry in block["rows"]
                          if block["table"].endswith("_report_stations")}
            if linked_ids:
                cursor.execute("SELECT DISTINCT district FROM stations WHERE station_id IN (" +
                               ", ".join(["%s"] * len(linked_ids)) + ") ORDER BY district", tuple(linked_ids))
                district = ",".join(entry["district"] for entry in cursor.fetchall() if entry["district"])
        title = _record_title(row, entity_type, entity_id)
        payload = zlib.compress(json.dumps(blocks, default=_json_value).encode("utf-8"))
        cursor.execute("""INSERT INTO deleted_items
            (entity_type, entity_id, title, district, deleted_by_username, row_count, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (entity_type, str(entity_id), title, district, user["username"],
             sum(len(block["rows"]) for block in blocks), payload))
    finally:
        cursor.close()


def restore_deleted_item(connection, deleted_item_id, user):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT entity_type, entity_id, payload, restored_at FROM deleted_items WHERE deleted_item_id = %s FOR UPDATE",
                       (deleted_item_id,))
        item = cursor.fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="Deleted item not found")
        if item["restored_at"] is not None:
            raise HTTPException(status_code=409, detail="This item has already been restored")
        blocks = json.loads(zlib.decompress(item["payload"]).decode("utf-8"))
        allowed = {table for table, _ in DELETED_SPECS.values()} | {
            "volunteer_data_files", "volunteer_report_comments", "maintenance_record_instruments",
            "instrument_station_categories", "user_station_assignments", "station_maintenance_frequencies",
            "maintenance_report_stations", "pre_maintenance_report_stations",
            "station_inspection_report_stations", "station_inspection_comments", "station_inspection_photos",
            "monthly_non_reported_station_files",
        }
        for block in blocks:
            table = block["table"]
            if table not in allowed:
                raise HTTPException(status_code=409, detail="Unsupported backup table")
            for row in block["rows"]:
                columns = list(row)
                values = [_restore_value(row[column]) for column in columns]
                cursor.execute(f"INSERT INTO `{table}` (" + ", ".join(f"`{column}`" for column in columns) +
                               ") VALUES (" + ", ".join(["%s"] * len(columns)) + ")", tuple(values))
        cursor.execute("UPDATE deleted_items SET restored_at = CURRENT_TIMESTAMP, restored_by_username = %s WHERE deleted_item_id = %s",
                       (user["username"], deleted_item_id))
        connection.commit()
        return {"message": "Item restored", "entity_type": item["entity_type"], "entity_id": item["entity_id"]}
    except IntegrityError as exc:
        connection.rollback()
        raise HTTPException(status_code=409, detail="Restore conflicts with existing data or a missing related record") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def download_deleted_item(connection, deleted_item_id):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT entity_type, entity_id, deleted_at, payload FROM deleted_items WHERE deleted_item_id = %s",
                       (deleted_item_id,))
        item = cursor.fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="Deleted item not found")
        blocks = json.loads(zlib.decompress(item["payload"]).decode("utf-8"))
        tables = {}
        for block in blocks:
            tables.setdefault(block["table"], []).extend(block["rows"])
        output = io.BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            history = _history_for_item(cursor, item)
            if history:
                archive.writestr("tables/edit_history.csv", _history_csv(history))
            for table, rows in tables.items():
                columns = list(dict.fromkeys(column for row in rows for column in row))
                csv_output = io.StringIO()
                writer = csv.writer(csv_output)
                writer.writerow(columns)
                for index, row in enumerate(rows, 1):
                    values = []
                    for column in columns:
                        value = row.get(column)
                        if column in {"password_hash", "token_hash"}:
                            values.append("[redacted]")
                        elif isinstance(value, dict) and set(value) == {"__binary__"}:
                            filename = f"files/{table}_{index}_{column}.bin"
                            archive.writestr(filename, base64.b64decode(value["__binary__"], validate=True))
                            values.append(filename)
                        elif value is None:
                            values.append("")
                        elif isinstance(value, (dict, list)):
                            values.append(str(value))
                        else:
                            values.append(str(value))
                    writer.writerow(values)
                archive.writestr(f"tables/{table}.csv", csv_output.getvalue().encode("utf-8-sig"))
        return output.getvalue()
    finally:
        cursor.close()
