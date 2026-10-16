"""Freeze station-instrument availability for reproducible upload assessments."""
import json
from .catalog_schedule_20261015 import registry_records

RESERVED_CLOUD_IDS = "116 117 118 120 121 122 124 125 126 128 129 130".split()
CLOUD_PARAMETER_IDS = set(RESERVED_CLOUD_IDS) | {"190", "193", "195", "115", "191", "192", "194", "170", "201", "171"}

# Initial mappings follow the latest owner-approved table, never overwrite edits.
INITIAL_ELEMENT_IDS = {}
for _row in registry_records():
    if _row["element_ids"]:
        _values = INITIAL_ELEMENT_IDS.setdefault(_row["registry_number"], [])
        for _code in _row["element_ids"]:
            if _code not in _values:
                _values.append(_code)


def assign_category_defaults(core, connection, user, initialize_elements=False):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT instrument_id,registry_number,instrument_name,element_ids FROM instruments")
        instruments = cursor.fetchall()
        for instrument in instruments:
            elements = INITIAL_ELEMENT_IDS.get(instrument["registry_number"])
            if initialize_elements and elements and not json.loads(instrument.get("element_ids") or "[]"):
                before = core.audit_snapshot(connection, "instruments", instrument["instrument_id"])
                cursor.execute("UPDATE instruments SET element_ids=%s WHERE instrument_id=%s",
                               (json.dumps(elements), instrument["instrument_id"]))
                core.record_entity_edit(connection, "instruments", instrument["instrument_id"], before,
                    core.audit_snapshot(connection, "instruments", instrument["instrument_id"]), user)
        cursor.execute("""SELECT DISTINCT s.station_id,i.instrument_id FROM stations s
            JOIN instrument_station_categories cat ON cat.station_category=s.station_category
            JOIN instruments i ON i.instrument_id=cat.instrument_id AND i.is_active=1
            WHERE NOT EXISTS (SELECT 1 FROM station_instruments si
                WHERE si.station_id=s.station_id AND si.instrument_id=i.instrument_id)
            ORDER BY s.station_id,i.instrument_id""")
        pairs = cursor.fetchall()
        # Lock a catalog row to serialize concurrent runs, then recheck each pair.
        cursor.execute("SELECT instrument_id FROM instruments ORDER BY instrument_id FOR UPDATE")
        cursor.fetchall()
        added = 0
        for pair in pairs:
            cursor.execute("SELECT station_instrument_id FROM station_instruments WHERE station_id=%s AND instrument_id=%s FOR UPDATE",
                           (pair["station_id"], pair["instrument_id"]))
            if cursor.fetchone():
                continue
            cursor.execute("""INSERT INTO station_instruments
                (station_id,instrument_id,status,comment,created_by_user_id,recorded_by_username)
                VALUES (%s,%s,'Operational',%s,%s,%s)""", (pair["station_id"], pair["instrument_id"],
                    "Category default: assumed operational baseline; installation date not supplied.",
                    user.get("user_id"), user["username"]))
            record_id = cursor.lastrowid
            cursor.execute("""INSERT INTO station_instrument_availability
                (station_instrument_id,effective_date,status,recorded_by_username)
                VALUES (%s,'1900-01-01','Operational',%s)""", (record_id, user["username"]))
            added += 1
        return {"added": added, "message": "Missing category assignments added as Operational; existing assignments and availability preserved."}
    finally:
        cursor.close()


def load_inventory(cursor, station_codes):
    cursor.execute("SELECT instrument_name,parameters_taken,element_ids FROM instruments")
    mapped = set()
    elements = {}; errors = []; aliases = {}
    for row in cursor.fetchall():
        ids = json.loads(row.get("element_ids") or "[]")
        mapped.update(ids)
        if not ids:
            continue
        names = [name.strip() for name in (row.get("parameters_taken") or "").split("|")]
        if len(ids) != len(names) or any(not name for name in names):
            errors.append(f'{row.get("instrument_name", "Instrument")}: enter {len(ids)} parameter names separated by | to match its element IDs.')
            continue
        for code, name in zip(ids, names):
            values = aliases.setdefault(code, [])
            if name.casefold() not in {value.casefold() for value in values}:
                values.append(name)
            elements[code] = " / ".join(values)
    cursor.execute("""SELECT si.station_instrument_id,s.station_code,si.status,
        i.element_ids,si.installation_date FROM station_instruments si
        JOIN stations s ON s.station_id=si.station_id
        JOIN instruments i ON i.instrument_id=si.instrument_id""")
    assignments = []
    for row in cursor.fetchall():
        if row["station_code"] not in station_codes:
            continue
        assignments.append(dict(station_instrument_id=row["station_instrument_id"],
            station_code=row["station_code"], element_ids=json.loads(row.get("element_ids") or "[]"),
            status=row["status"], installation_date=str(row["installation_date"]) if row.get("installation_date") else None))
    cursor.execute("""SELECT DISTINCT s.station_code,cat.station_category,i.element_ids
        FROM instruments i
        JOIN instrument_station_categories cat ON cat.instrument_id=i.instrument_id
        JOIN stations s ON s.station_category=cat.station_category
        ORDER BY s.station_code""")
    eligible_sets = {code: set() for code in elements}
    categories = {code: set() for code in elements}
    for row in cursor.fetchall():
        if row["station_code"] not in station_codes:
            continue
        for code in json.loads(row.get("element_ids") or "[]"):
            if code in eligible_sets:
                eligible_sets[code].add(row["station_code"])
                categories[code].add(row["station_category"])
    eligible = {code: sorted(values) for code, values in eligible_sets.items()}
    cursor.execute("""SELECT a.station_instrument_id,a.effective_date,a.status
        FROM station_instrument_availability a JOIN station_instruments si
        ON si.station_instrument_id=a.station_instrument_id
        JOIN stations s ON s.station_id=si.station_id ORDER BY a.effective_date""")
    by_id = {row["station_instrument_id"]: row for row in assignments}
    for row in cursor.fetchall():
        assignment = by_id.get(row["station_instrument_id"])
        if assignment is not None:
            assignment.setdefault("periods", []).append(dict(effective_date=str(row["effective_date"]), status=row["status"]))
    for assignment in assignments:
        assignment.setdefault("periods", [dict(effective_date=assignment["installation_date"] or "1900-01-01", status=assignment["status"])])
    return dict(availability_mode="dated_status", mapped_elements=sorted(mapped), assignments=assignments,
                element_names=elements, element_name_aliases=aliases, eligible_stations=eligible,
                element_station_categories={code: sorted(values) for code, values in categories.items()},
                catalog_errors=list(dict.fromkeys(errors)))


def catalog_configuration(saved, inventory):
    """Catalog owns identity/eligibility; saved configuration owns schedules."""
    config = json.loads(json.dumps(saved))
    previous = dict(config.get("archived_parameter_rules", {}))
    previous.update({item["element_id"]: item for item in config["parameters"]})
    config["archived_parameter_rules"] = {code: {key: value for key, value in rule.items()
        if key in {"element_id", "name", "cadence", "hours", "enabled", "zero_parent", "excluded_station_codes", "schedule_mode", "interval_minutes", "times"}} for code, rule in previous.items()}
    parameters = []
    names = inventory["element_names"]
    for code, name in names.items():
        rule = dict(previous.get(code, dict(element_id=code, cadence="daily", hours=[], enabled=False,
                                            zero_parent=None)))
        eligible = inventory["eligible_stations"][code]
        excluded = list(dict.fromkeys(rule.get("excluded_station_codes", [])))
        rule.update(name=name, excluded_station_codes=excluded,
                    station_codes=[station for station in eligible if station not in excluded])
        rule["assessment_station_codes"] = list(rule["station_codes"])
        rule["eligible_station_codes"] = list(eligible)
        rule["station_categories"] = inventory.get("element_station_categories", {}).get(code, [])
        if inventory.get("availability_mode") == "dated_status":
            operational = {a["station_code"] for a in inventory["assignments"]
                           if code in a["element_ids"] and a["status"] == "Operational"}
            rule["inactive_station_codes"] = [station for station in eligible if station not in operational]
            rule["station_codes"] = [station for station in rule["station_codes"] if station in operational]
            rule["station_statuses"] = {station: ("Operational" if station in operational else
                "; ".join(sorted({a["status"] for a in inventory["assignments"] if a["station_code"] == station and code in a["element_ids"]})) or "Not assigned") for station in eligible}
        rule["is_cloud_parameter"] = code in CLOUD_PARAMETER_IDS or "cloud" in name.casefold()
        if not rule["is_cloud_parameter"]:
            rule["zero_parent"] = None
        if rule.get("zero_parent") not in set(names) | set(RESERVED_CLOUD_IDS):
            rule["zero_parent"] = None
        parameters.append(rule)
    for code in RESERVED_CLOUD_IDS:
        if code in names:
            continue
        # These are placeholders, not new instruments or invented observations.
        rule = dict(previous.get(code, {}))
        if config.get("reserved_cloud_version") != 1:
            rule.update(schedule_mode="none", cadence="hourly", hours=[], times=[], enabled=False)
        rule.update(element_id=code, name=rule.get("name") or f"Element {code}",
                    station_codes=[], assessment_station_codes=[], eligible_station_codes=[], station_categories=[],
                    reserved=True, is_cloud_parameter=True, zero_parent=rule.get("zero_parent"))
        parameters.append(rule)
    config["reserved_cloud_version"] = 1
    config["parameters"] = parameters
    for station in config["stations"]:
        station["daily_excluded"] = [code for code in station.get("daily_excluded", []) if code in names]
        station["hourly_overrides"] = {code: value for code, value in station.get("hourly_overrides", {}).items() if code in names and previous.get(code, {}).get("cadence") == "hourly"}
    config.update(catalog_managed=True, catalog_schema_version=6, catalog_errors=inventory["catalog_errors"],
                  catalog_pending=[code for code in names if code not in previous])
    return config


def save_downtime_upgrade(core, connection):
    """Persist the explicit reporting-policy change with an auditable rule version."""
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT version,configuration FROM data_upload_config WHERE config_id=1 FOR UPDATE")
        saved = cursor.fetchone()
        before = json.loads(saved["configuration"])
        if before.get("downtime_policy_version") == 1:
            return {"message": "Already applied"}
        cursor.execute("SELECT station_code FROM stations")
        allowed = {row["station_code"] for row in cursor.fetchall()}
        config = catalog_configuration(before, load_inventory(cursor, allowed))
        config.update(version=saved["version"] + 1, downtime_policy_version=1)
        from .data_upload_engine import UploadConfiguration
        UploadConfiguration.model_validate(config)
        user = dict(username="System", user_id=None, edit_reason=
            "Owner requested dated instrument downtime, automatic operational selections and unscheduled cloud placeholders.")
        cursor.execute("UPDATE data_upload_config SET version=%s,configuration=%s,updated_by_username=%s WHERE config_id=1",
            (config["version"], json.dumps(config), user["username"]))
        core.record_entity_edit(connection, "upload_configuration", 1, before, config, user)
        connection.commit()
        return dict(rule_version=config["version"], reserved_cloud_ids=RESERVED_CLOUD_IDS)
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
