"""Transactional activity records and notifications for operational changes."""

import re
from contextvars import ContextVar
from functools import wraps


request_activity = ContextVar("request_activity", default=None)
activity_suspended = ContextVar("activity_suspended", default=False)

ACTIVITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS operation_activity (
    activity_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    actor_user_id INT NULL,
    actor_name VARCHAR(100) NOT NULL,
    action VARCHAR(20) NOT NULL,
    resource VARCHAR(100) NOT NULL,
    record_id VARCHAR(100) NULL,
    request_path VARCHAR(255) NOT NULL,
    affected_records INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_activity_created (created_at, activity_id)
)
"""

# Only operational tables are tracked; credentials and file contents are never logged.
RESOURCES = {
    "users": ("user_id", "User account", "users", "users.html"),
    "user_sessions": ("session_id", "Account session", "account", None),
    "stations": ("station_id", "Station", "stations", "stations.html"),
    "instruments": ("instrument_id", "Instrument", "instruments", "instruments.html"),
    "maintenance_records": ("maintenance_id", "Maintenance", "maintenance", "maintenance.html"),
    "maintenance_reports": ("report_id", "Maintenance report", "maintenance_report", "maintenance.html#maintenanceReports"),
    "pre_maintenance_reports": ("report_id", "Pre-maintenance report", "pre_maintenance_report", "pre-maintenance-reports.html"),
    "suspected_data_records": ("suspected_data_id", "Quality control", "suspected_data", "suspected-data.html"),
    "station_instruments": ("station_instrument_id", "Station instrument", "station_instrument", "station-instruments.html"),
    "station_inspections": ("inspection_id", "Station inspection", "station_inspection", "station-inspections.html"),
    "station_inspection_comments": ("comment_id", "Inspection response", "station_inspection", "station-inspections.html"),
    "station_inspection_reports": ("report_id", "Inspection report", "station_inspection_report", "station-inspections.html"),
    "station_inspection_photos": ("inspection_id", "Inspection photograph", "station_inspection", "station-inspections.html"),
    "discussions": ("discussion_id", "Discussion", "discussion", "discussions.html"),
    "discussion_messages": ("message_id", "Discussion response", "discussion", "discussions.html"),
    "station_visitors": ("visitor_id", "Station visit", "station_visitors", "station-visitors.html"),
    "station_volunteers": ("volunteer_id", "Station volunteer", "station_volunteers", "station-volunteers.html"),
    "visitor_categories": ("category_id", "Visitor category", "station_visitors", "station-visitors.html"),
    "monthly_data_counts": ("data_count_id", "Data count", "data_counts", "data-counts.html"),
    "monthly_category_data_counts": ("data_count_id", "Category data count", "data_counts", "data-counts.html"),
    "monthly_combined_data_counts": ("data_count_id", "Data count", "data_counts", "data-counts.html"),
    "station_maintenance_frequencies": ("station_id", "Maintenance schedule", "maintenance_summary", "maintenance-summary.html"),
    "category_maintenance_targets": ("station_category", "Maintenance target", "maintenance_summary", "maintenance-summary.html"),
    "volunteer_data_files": ("file_id", "Volunteer report", "volunteer_data", "volunteer-data.html"),
    "volunteer_report_comments": ("comment_id", "Volunteer report comment", "volunteer_data", "volunteer-data.html"),
    "monthly_reporting_status": ("reporting_status_id", "Reporting status", "reporting_status", "reporting-status.html"),
    "monthly_non_reported_station_files": ("file_id", "Reporting attachment", "reporting_status", "reporting-status.html"),
    "monthly_data_requests": ("data_request_id", "Data request", "data_requests", "data-requests.html"),
    "notifications": ("notification_id", "Notification", "notification", None),
    "user_station_assignments": ("user_id", "Station assignment", "users", "users.html"),
    "discussion_participants": ("discussion_id", "Discussion participant", "discussion", "discussions.html"),
    "maintenance_record_instruments": ("maintenance_id", "Maintenance instrument", "maintenance", "maintenance.html"),
    "instrument_station_categories": ("instrument_id", "Instrument category", "instruments", "instruments.html"),
    "maintenance_report_stations": ("report_id", "Maintenance report coverage", "maintenance_report", "maintenance.html"),
    "pre_maintenance_report_stations": ("report_id", "Pre-maintenance report coverage", "pre_maintenance_report", "pre-maintenance-reports.html"),
    "station_inspection_report_stations": ("report_id", "Inspection report coverage", "station_inspection_report", "station-inspections.html"),
}


class ActivityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await self.app(scope, receive, send)
        token = request_activity.set({"path": scope["path"], "user": None})
        try:
            await self.app(scope, receive, send)
        finally:
            request_activity.reset(token)


def set_activity_actor(user):
    context = request_activity.get()
    if context is not None:
        context["user"] = {key: user.get(key) for key in ("user_id", "full_name", "username", "department")}


def suspend_activity(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        token = activity_suspended.set(True)
        try:
            return function(*args, **kwargs)
        finally:
            activity_suspended.reset(token)
    return wrapped


class ActivityCursor:
    def __init__(self, cursor, connection):
        self._cursor = cursor
        self._connection = connection

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def execute(self, operation, params=None, **kwargs):
        change = self._connection.prepare_change(operation, params)
        result = self._cursor.execute(operation, params, **kwargs)
        self._connection.capture_change(change, self._cursor)
        return result

    def executemany(self, operation, seq_params):
        context = request_activity.get()
        match = re.match(r"\s*(?:INSERT(?:\s+IGNORE)?\s+INTO|UPDATE|DELETE\s+FROM)\s+`?(\w+)`?", operation, re.I)
        if activity_suspended.get() or not context or not context.get("user") or not match or match[1].lower() not in RESOURCES:
            return self._cursor.executemany(operation, seq_params)
        for params in seq_params:
            self.execute(operation, params)


class ActivityConnection:
    def __init__(self, connection):
        self._connection = connection
        self._changes = []
        self._columns = {}

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def cursor(self, *args, **kwargs):
        return ActivityCursor(self._connection.cursor(*args, **kwargs), self)

    def metadata(self, table, where="", params=()):
        cursor = self._connection.cursor(dictionary=True)
        try:
            if table not in self._columns:
                cursor.execute(f"SHOW COLUMNS FROM `{table}`")
                self._columns[table] = [row["Field"] for row in cursor.fetchall() if
                    row["Field"].endswith("_id") or row["Field"] == "station_category"]
            columns = self._columns[table]
            if not columns:
                return []
            cursor.execute(f"SELECT {', '.join('`' + name + '`' for name in columns)} FROM `{table}` {where}", params)
            return cursor.fetchall()
        finally:
            cursor.close()

    def prepare_change(self, operation, params):
        context = request_activity.get()
        if activity_suspended.get() or not context or not context.get("user"):
            return None
        match = re.match(r"\s*(INSERT(?:\s+IGNORE)?\s+INTO|UPDATE|DELETE\s+FROM)\s+`?(\w+)`?", operation, re.I)
        if not match or match[2].lower() not in RESOURCES:
            return None
        table = match[2].lower()
        if table == "notifications" and not context["path"].startswith("/notifications/"):
            return None
        verb = match[1].split()[0].upper()
        before = []
        where = re.search(r"\bWHERE\b(.*)", operation, re.I | re.S)
        if verb != "INSERT" and where:
            count = where[0].count("%s")
            before = self.metadata(table, where[0], tuple(params or ())[-count:] if count else ())
        if verb == "INSERT":
            columns = re.search(r"\w+`?\s*\(([^)]+)\)\s*VALUES", operation, re.I | re.S)
            if columns:
                before = [{key: value for key, value in zip(
                    (name.strip().strip('`') for name in columns[1].split(',')), params or ())
                    if key.endswith('_id') and isinstance(value, int)}]
        # Preserve station coverage before report deletion cascades remove the links.
        links = {"maintenance_reports": "maintenance_report_stations",
                 "pre_maintenance_reports": "pre_maintenance_report_stations",
                 "station_inspection_reports": "station_inspection_report_stations"}
        if verb == "DELETE" and table in links:
            before += [linked for row in list(before) for linked in
                       self.metadata(links[table], "WHERE report_id = %s", (row["report_id"],))]
        return {"table": table, "verb": verb, "before": before,
                "upsert": "ON DUPLICATE KEY UPDATE" in operation.upper()}

    def capture_change(self, change, cursor):
        if change and cursor.rowcount > 0:
            change["count"] = cursor.rowcount
            change["insert_id"] = cursor.lastrowid if change["verb"] == "INSERT" else None
            self._changes.append(change)

    def rollback(self):
        self._changes.clear()
        return self._connection.rollback()

    def commit(self):
        context = request_activity.get()
        try:
            if self._changes and context and context.get("user"):
                self.write_activity(context)
            self._connection.commit()
            self._changes.clear()
        except Exception:
            self.rollback()
            raise

    def write_activity(self, context):
        user = context["user"]
        cursor = self._connection.cursor(dictionary=True)
        try:
            for change in self._changes:
                table = change["table"]
                primary_key, label, record_type, target = RESOURCES[table]
                rows = change["before"]
                record_id = change["insert_id"] or (rows[0].get(primary_key) if len(rows) == 1 else None)
                if change["insert_id"]:
                    rows += self.metadata(table, f"WHERE `{primary_key}` = %s", (record_id,))
                elif record_id and change["verb"] == "UPDATE":
                    rows += self.metadata(table, f"WHERE `{primary_key}` = %s", (record_id,))
                for parent_key, parent_table in (("inspection_id", "station_inspections"),
                                                  ("maintenance_id", "maintenance_records")):
                    if table != parent_table:
                        parent_ids = {row[parent_key] for row in rows if row.get(parent_key)}
                        for parent_id in parent_ids:
                            rows += self.metadata(parent_table, f"WHERE `{parent_key}` = %s", (parent_id,))
                action = {"INSERT": "Created", "UPDATE": "Updated", "DELETE": "Deleted"}[change["verb"]]
                if change.get("upsert"):
                    action = "Saved"
                if context["path"].startswith("/auth/"):
                    action = {"/auth/login": "Signed in", "/auth/logout": "Signed out", "/auth/password": "Password changed"}.get(context["path"], action)
                actor_name = user.get("full_name") or user.get("username") or "Administrator"
                cursor.execute("""INSERT INTO operation_activity
                    (actor_user_id, actor_name, action, resource, record_id, request_path, affected_records)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (user["user_id"], actor_name, action, label, str(record_id) if record_id else None,
                     context["path"], change["count"]))
                if record_type in {"notification", "account"}:
                    continue
                recipients = {value for row in rows for key, value in row.items()
                              if (key.endswith("_user_id") or key == "user_id") and value}
                station_ids = {row["station_id"] for row in rows if row.get("station_id")}
                discussion_ids = {row["discussion_id"] for row in rows if row.get("discussion_id")}
                for discussion_id in discussion_ids:
                    cursor.execute("SELECT user_id FROM discussion_participants WHERE discussion_id = %s", (discussion_id,))
                    recipients.update(row["user_id"] for row in cursor.fetchall())
                link_table = {"maintenance_reports": "maintenance_report_stations",
                              "pre_maintenance_reports": "pre_maintenance_report_stations",
                              "station_inspection_reports": "station_inspection_report_stations"}.get(table)
                if link_table and record_id and change["verb"] != "DELETE":
                    cursor.execute(f"SELECT station_id FROM {link_table} WHERE report_id = %s", (record_id,))
                    station_ids.update(row["station_id"] for row in cursor.fetchall())
                if station_ids:
                    placeholders = ", ".join(["%s"] * len(station_ids))
                    cursor.execute(f"SELECT user_id FROM user_station_assignments WHERE station_id IN ({placeholders})", tuple(station_ids))
                    recipients.update(row["user_id"] for row in cursor.fetchall())
                roles = {"Admin"}
                if table in {"maintenance_records", "maintenance_reports", "pre_maintenance_reports", "station_instruments", "suspected_data_records", "station_inspections"}:
                    roles.add("Instrument Maintenance and Calibration Officer")
                if table in {"suspected_data_records", "station_inspections", "station_inspection_comments", "station_inspection_reports", "station_inspection_photos", "monthly_reporting_status", "monthly_data_counts", "monthly_category_data_counts", "monthly_combined_data_counts", "volunteer_data_files", "volunteer_report_comments"}:
                    roles.update({"Observation Supervisor at HQ", "Data Quality Control Officer", "Observation Processing Officer"})
                cursor.execute(f"SELECT user_id FROM users WHERE is_active = TRUE AND department IN ({', '.join(['%s'] * len(roles))})", tuple(roles))
                recipients.update(row["user_id"] for row in cursor.fetchall())
                recipients.discard(user["user_id"])
                if recipients:
                    cursor.execute(f"SELECT user_id FROM users WHERE is_active = TRUE AND user_id IN ({', '.join(['%s'] * len(recipients))})", tuple(recipients))
                    recipients = {row["user_id"] for row in cursor.fetchall()}
                title = f"{label}: {action.lower()}"
                message = f"{actor_name} {action.lower()} {label.lower()}" + (f" #{record_id}." if record_id else f" ({change['count']} records).")
                notification_record_id = record_id
                if record_type == "discussion":
                    notification_record_id = next(iter(discussion_ids), record_id)
                if record_type == "station_inspection":
                    notification_record_id = next((row["inspection_id"] for row in rows if row.get("inspection_id")), record_id)
                if recipients:
                    cursor.executemany("""INSERT INTO notifications
                        (user_id, notification_type, title, message, related_record_type, related_record_id)
                        VALUES (%s, 'operation_activity', %s, %s, %s, %s)""",
                        [(recipient, title, message, record_type,
                          notification_record_id if isinstance(notification_record_id, int) else None)
                         for recipient in sorted(recipients)])
        finally:
            cursor.close()
