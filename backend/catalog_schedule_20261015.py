"""Explicit, one-time owner-approved registry renumbering and schedule migration.

Run with python -m backend.catalog_schedule_20261015 after taking the app offline.
Normal startup never renumbers registry keys or overwrites admin schedules.
"""
import json
from .catalog_registry_20261015 import registry_records as previous_records
from .data_upload_engine import apply_observation_defaults, UploadConfiguration


def registry_records():
    records = []
    for row in previous_records():
        if row["registry_number"] == 59:
            continue
        if row["registry_number"] >= 60:
            row["registry_number"] -= 1
        if row["registry_number"] == 63:
            row["element_ids"] = ["180"]
        records.append(row)
    return records


def migrate(core, connection):
    from .instrument_registry import ensure_registry
    from .instrument_availability import catalog_configuration, load_inventory
    from .deleted_items import archive_deleted_item
    user = dict(username="System", user_id=None, edit_reason=
        "Owner-approved removal of element 10 Hygrograph, one-time renumbering, soil element 180 and observation schedule defaults.")
    cursor = connection.cursor(dictionary=True)
    guard_removed = False
    try:
        cursor.execute("SELECT version,configuration FROM data_upload_config WHERE config_id=1")
        saved = cursor.fetchone()
        before_config = json.loads(saved["configuration"])
        if before_config.get("schedule_defaults_version") == 1:
            return {"message": "Already applied; subsequent admin edits preserved"}
        cursor.execute("SELECT instrument_id,registry_number,element_ids FROM instruments ORDER BY registry_number")
        old = cursor.fetchall()
        if {row["registry_number"] for row in old} != set(range(1, 22)) | set(range(51, 73)):
            raise ValueError("Registry differs from the expected previous version; no changes applied")
        removed = next(row for row in old if row["registry_number"] == 59)
        if json.loads(removed["element_ids"]) != ["10"]:
            raise ValueError("Registry 59 is not the expected old Hygrograph; no changes applied")
        cursor.execute("SELECT COUNT(*) AS n FROM maintenance_record_instruments WHERE instrument_id=%s", (removed["instrument_id"],))
        if cursor.fetchone()["n"]:
            raise ValueError("Old Hygrograph has maintenance references; archive them safely before removing the catalog row")
        connection.commit()
        # DDL commits implicitly in MySQL. Restore the guard even if the DML rolls back.
        cursor.execute("DROP TRIGGER instrument_registry_immutable")
        guard_removed = True
        connection.start_transaction()
        cursor.execute("SELECT instrument_id FROM instruments ORDER BY instrument_id FOR UPDATE")
        cursor.fetchall()
        archive_deleted_item(connection, "instruments", removed["instrument_id"], user)
        cursor.execute("""DELETE a FROM station_instrument_availability a JOIN station_instruments si
            ON si.station_instrument_id=a.station_instrument_id WHERE si.instrument_id=%s""", (removed["instrument_id"],))
        cursor.execute("DELETE FROM station_instruments WHERE instrument_id=%s", (removed["instrument_id"],))
        cursor.execute("DELETE FROM instrument_station_categories WHERE instrument_id=%s", (removed["instrument_id"],))
        cursor.execute("DELETE FROM instruments WHERE instrument_id=%s", (removed["instrument_id"],))
        for row in old:
            if row["registry_number"] < 60:
                continue
            before = core.audit_snapshot(connection, "instruments", row["instrument_id"])
            cursor.execute("UPDATE instruments SET registry_number=%s WHERE instrument_id=%s",
                (row["registry_number"] - 1, row["instrument_id"]))
            if row["registry_number"] == 64:
                cursor.execute("UPDATE instruments SET element_ids=%s WHERE instrument_id=%s",
                    (json.dumps(["180"]), row["instrument_id"]))
            core.record_entity_edit(connection, "instruments", row["instrument_id"], before,
                core.audit_snapshot(connection, "instruments", row["instrument_id"]), user)
        config = apply_observation_defaults(before_config)
        cursor.execute("SELECT station_code FROM stations")
        allowed = {row["station_code"] for row in cursor.fetchall()}
        config = catalog_configuration(config, load_inventory(cursor, allowed))
        UploadConfiguration.model_validate(config)
        if config["catalog_errors"] or config["catalog_pending"]:
            raise ValueError("Catalog schedules are incomplete: " + str(config["catalog_errors"] + config["catalog_pending"]))
        config["version"] = saved["version"] + 1
        cursor.execute("UPDATE data_upload_config SET version=%s,configuration=%s,updated_by_username=%s WHERE config_id=1",
            (config["version"], json.dumps(config), user["username"]))
        core.record_entity_edit(connection, "upload_configuration", 1, before_config, config, user)
        connection.commit()
        return dict(instruments=42, rule_version=config["version"], removed_registry=59, soil_element="180")
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        if guard_removed:
            guard = connection.cursor()
            try:
                ensure_registry(guard)
                connection.commit()
            finally:
                guard.close()


if __name__ == "__main__":
    from . import main as core
    connection = core.get_connection()
    try:
        print(migrate(core, connection))
    finally:
        connection.close()
