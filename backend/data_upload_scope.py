"""Registry-based report coverage and consistent result/download filtering."""
from copy import deepcopy

STATION_STATUSES = ["Operational", "Under maintenance", "Suspended", "Closed"]
DEFAULT_STATION_STATUSES = STATION_STATUSES[:3]


def registry_configuration(saved, stations):
    config = deepcopy(saved)
    profiles = {row["station_code"]: row for row in config["stations"]}
    config["stations"] = []
    config.setdefault("station_statuses", list(DEFAULT_STATION_STATUSES))
    config["station_scope"] = "registry"
    for code, row in sorted(stations.items()):
        profile = profiles.get(code, dict(station_code=code, enabled=True, daily_excluded=[], hourly_overrides={}))
        profile.update(station_name=row["station_name"], station_category=row.get("station_category"),
                       station_status=row.get("status", "Operational"))
        config["stations"].append(profile)
    return config


def report_profiles(config):
    return [row for row in config["stations"] if row.get("enabled", True)
            and row.get("station_status", "Operational") in config.get("station_statuses", DEFAULT_STATION_STATUSES)]


def choices(value):
    return {part for part in (value or "").split("|") if part and part != "all"}


def recompute_totals(rows, elements):
    groups = {}
    for element in elements:
        key = (element["station_code"], element["report_month"])
        totals = groups.setdefault(key, dict(daily_expected=0, daily_available=0, hourly_expected=0, hourly_available=0))
        cadence = element.get("cadence", "hourly")
        for source, target in [("expected", "expected"), ("available", "available")]:
            totals[f"{cadence}_{target}"] += element[source]
    selected = []
    for row in rows:
        totals = groups.get((row["station_code"], row["report_month"]))
        if totals is None: continue
        item = dict(row, **totals)
        for cadence in ["daily", "hourly"]:
            expected, available = item[f"{cadence}_expected"], item[f"{cadence}_available"]
            item[f"{cadence}_missing"] = expected - available
            item[f"{cadence}_percent"] = round(100 * available / expected, 2) if expected else 0
        expected = item["daily_expected"] + item["hourly_expected"]
        item["overall_percent"] = round(100 * (item["daily_available"] + item["hourly_available"]) / expected, 2) if expected else None
        selected.append(item)
    return selected


def filter_report(result, registry, *, station_code=None, district=None, station_category=None,
                  station_status=None, element_id=None, month_from=None, month_to=None):
    selected_ids = choices(element_id)
    filters = [(choices(station_code), "station_code"), (choices(district), "district"),
               (choices(station_category), "station_category"), (choices(station_status), "status")]
    rows = []
    for row in result["results"]:
        station = registry.get(row["station_code"])
        if station is None: continue
        values = dict(station, station_code=row["station_code"])
        if any(selected and values.get(key) not in selected for selected, key in filters): continue
        period = row.get("report_month", "")
        if month_from and period < month_from: continue
        if month_to and period > month_to: continue
        rows.append(row)
    keys = {(row["station_code"], row.get("report_month")) for row in rows}
    elements = [row for row in result.get("element_results", [])
                if (row["station_code"], row.get("report_month")) in keys
                and (not selected_ids or row.get("element_id") in selected_ids)]
    if selected_ids:
        rows = recompute_totals(rows, elements)
        keys = {(row["station_code"], row["report_month"]) for row in rows}
    result = dict(result, results=rows, element_results=elements)
    names = {row["element_name"] for row in elements}
    for section in ["hourly_details", "daily_details"]:
        details = []
        for row in result.get(section, []):
            if (row["station_code"], row["date"][:7]) not in keys: continue
            if selected_ids and section == "hourly_details" and row["element_id"] not in selected_ids: continue
            if selected_ids and section == "daily_details":
                row = deepcopy(row)
                row["missing"] = [name for name in row["missing"] if name in names]
                row["excluded"] = [text for text in row["excluded"] if any(text == name or text.startswith(name + " (") for name in names)]
                row["downtime"] = [entry for entry in row.get("downtime", []) if entry["element_name"] in names]
                row["entered"] = "; ".join(entry["element_name"] + ": " + entry["status"] for entry in row["downtime"]) or "; ".join(row["excluded"]) or "No"
                if not row["missing"] and not row["excluded"]: continue
            details.append(row)
        result[section] = details
    result["periods"] = sorted({row["report_month"] for row in rows if row.get("report_month")})
    return result


def add_element_cadences(result, snapshot):
    rules = {rule["element_id"]:rule["cadence"] for rule in (snapshot or {}).get("parameters", [])}
    for row in result.get("element_results", []):
        row.setdefault("cadence", rules.get(row.get("element_id"), "hourly"))
    return result


def recover_legacy_elements(result, snapshot, source, filename):
    """Enrich old reports without replacing their original totals or rules."""
    if "element_results" in result:
        return result
    from .data_upload_engine import parse_source, analyze_source
    if not source or not snapshot:
        raise ValueError("This older report has no saved source or rules. Upload its original observations again to obtain element scores.")
    parsed = parse_source(source, filename)
    try:
        recovered = analyze_source(parsed, snapshot, inventory=snapshot.get("inventory_snapshot"))
    finally:
        if parsed.get("close"):
            parsed["close"]()
    originals = {(row["station_code"], row["report_month"]): row for row in result["results"]}
    calculated = {(row["station_code"], row["report_month"]): row for row in recovered["results"]}
    metrics = ["daily_expected", "daily_available", "hourly_expected", "hourly_available"]
    if originals.keys() != calculated.keys() or any(
        row[key] != calculated[identity][key] for identity, row in originals.items() for key in metrics
    ):
        raise ValueError("The older report cannot be reconstructed with identical historical totals. Upload its original observations again to obtain element scores.")
    return dict(result, element_results=recovered["element_results"])


def save_registry_scope_upgrade(core, connection):
    import json
    from .instrument_availability import catalog_configuration, load_inventory
    from .data_upload_engine import UploadConfiguration
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT version,configuration FROM data_upload_config WHERE config_id=1 FOR UPDATE")
        saved = cursor.fetchone(); before = json.loads(saved["configuration"])
        if before.get("registry_scope_version") == 1: return {"message":"Already applied"}
        cursor.execute("SELECT station_id,station_code,station_name,district,station_category,status FROM stations")
        registry = {row["station_code"]:row for row in cursor.fetchall()}
        config = catalog_configuration(registry_configuration(before, registry), load_inventory(cursor, registry))
        config.update(version=saved["version"] + 1, registry_scope_version=1)
        UploadConfiguration.model_validate(config)
        user = dict(username="System",user_id=None,edit_reason="Owner requested report coverage of all eligible registered stations, including those absent from uploaded observations, with configurable station statuses.")
        cursor.execute("UPDATE data_upload_config SET version=%s,configuration=%s,updated_by_username=%s WHERE config_id=1",
            (config["version"],json.dumps(config),user["username"]))
        core.record_entity_edit(connection,"upload_configuration",1,before,config,user)
        connection.commit()
        return dict(rule_version=config["version"],registered_stations=len(registry),included_stations=len(report_profiles(config)),station_statuses=config["station_statuses"])
    except Exception:
        connection.rollback(); raise
    finally: cursor.close()
