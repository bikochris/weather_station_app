"""Latest owner-supplied registry; registry numbers permanently identify instruments."""
import json
from .catalog_update_20261015 import ROWS


BASE = {row[0]: row for row in ROWS}
SPECS = [
    (1,"Regulator","Regulator","AWS_Voltage","Automatic Rain Gauge",""),
    (2,"Battery","Battery","AWS_Voltage","Automatic Rain Gauge|Automatic Weather station","1081"),
    (3,"Datalogger","Datalogger","AWS_Data Processing Unit","Automatic Rain Gauge|Automatic Weather station",""),
    (4,"Rain gauge sensor","Rain gauge sensor","AWS_Precipitation","Automatic Rain Gauge|Automatic Weather station","1107"),
    (5,"Solar Panel","Solar Panel","AWS_Solar Charger","Automatic Rain Gauge|Automatic Weather station","2181"),
    (6,"Barometric pressure sensor","Barometric pressure sensor","AWS_Atmospheric pressure","Automatic Weather station","1132"),
    (7,"Ceilometer","Ceilometer","AWS_Rainfall size","Automatic Weather station",""),
    (8,"Direct radiometer and sunshine duration sensor","Direct radiometer and sunshine duration sensor","AWS_Direct radiation|Sunshine duration","Automatic Weather station",""),
    (9,"Lightning detector sensor","Lightning detector sensor","AWS_Storm","Automatic Weather station","1272"),
    (10,"Pyranometer","Pyranometer","AWS_Global solar radiation","Automatic Weather station","1032"),
    (11,"Pyrheliometer sensor","Pyrheliometer sensor","AWS_Direct radiation","Automatic Weather station","2132"),
    (12,"Soil moisture and temperature sensor at 10 Cm","Soil moisture and temperature sensor at 10 Cm","AWS_Soil moisture at 10 Cm|AWS_Temperature at 10 Cm","Automatic Weather station","2122|2112"),
    (13,"Soil moisture and temperature sensor at 20 Cm","Soil moisture and temperature sensor at 20 Cm","AWS_Soil moisture at 20 Cm|AWS_Temperature at 20 Cm","Automatic Weather station","2222|2212"),
    (14,"Soil moisture and temperature sensor at 50 Cm","Soil moisture and temperature sensor at 50 Cm","AWS_Soil moisture at 50 Cm|AWS_Temperature at 50 Cm","Automatic Weather station","2322|2312"),
    (15,"Soil moisture and temperature sensor at 100 Cm","Soil moisture and temperature sensor at 100 Cm","AWS_Soil moisture at 100 Cm|AWS_Temperature at 100 Cm","Automatic Weather station","2422|2412"),
    (16,"Soil thermometer","Soil thermometer","AWS_Soil temperature","Automatic Weather station","2112"),
    (17,"Tower","Tower","AWS_Instrument Holder","Automatic Weather station",""),
    (18,"Visibility and present weather sensor","Visibility and present weather sensor","AWS_Visibility","Automatic Weather station","1432"),
    (19,"Wind speed sensor","Anemometer","AWS_Average Wind Speed","Automatic Weather station","1092"),
    (20,"Wind direction sensor","Wind Vane","AWS_Average Wind Direction","Automatic Weather station","1042"),
    (21,None,"Derived","AWS_Sunshine Duration|Evapotranspiration|AWS_Dew Point","Automatic Weather station","1441|1151|2512"),
    (51,"Measuring Cylinder","Measuring Cylinder",None,None,None),
    (52,"Stevenson Screen","Stevenson Screen",None,None,None),
    (53,"Digital thermometer","Digital thermometer",None,None,None),
    (54,"Manual rain gauge","Manual rain gauge",None,None,None),
    (55,"Digital hygrometer","Digital hygrometer",None,None,None),
    (56,"Evaporation pan/Hook gauge","Evaporation pan/Hook gauge",None,None,None),
    (57,"Evaporation Picth","Evaporation Picth",None,None,None),
    (58,"Grass thermometer","Grass thermometer",None,None,None),
    (59,"Hygrograph","Hygrograph","Humidity_Hygro","Principal Station","10"),
    (60,"Pluviograph/Rain gauge recorder","Pluviograph/Rain gauge recorder",None,None,None),
    (61,"Soil thermometer at 10 Cm","Soil thermometer at 10 Cm",None,None,None),
    (62,"Soil thermometer at 20 Cm","Soil thermometer at 20 Cm",None,None,None),
    (63,"Soil thermometer at 50 Cm","Soil thermometer at 50 Cm",None,None,None),
    (64,"Soil thermometer at 100 Cm","Soil thermometer at 1000 Cm","Soil temperature at 100 Cm","Principal Station","190"),
    (65,"Sunshine recorder","Sunshine recorder",None,None,None),
    (66,"Thermograph","Thermograph",None,None,None),
    (67,"Thermohygrography","Thermohygrography",None,None,None),
    (68,"Hygrograph","Hygrograph","Relative humidity","Principal Station","184"),
    (69,"Wind totalizer","Wind totalizer",None,None,None),
    (70,"Human Observation","Human Observation",None,None,None),
    (71,"Vapor Pressure Paper","Vapor Pressure Paper",None,None,None),
    (72,"Barometer","Barometer",None,None,None),
]


def registry_records():
    records = []
    for number, source, name, parameters, categories, elements in SPECS:
        base = BASE.get(source, [name,"","Calculated","Automatic Weather station","","","Active"])
        records.append(dict(registry_number=number,instrument_name=name,
            parameters_taken=base[1] if parameters is None else parameters,
            category=base[2],station_categories=(base[3] if categories is None else categories).split("|"),
            element_ids=[code for code in (base[4] if elements is None else elements).split("|") if code],
            description=None,is_active=True,source_name=source))
    return records


def apply_registry(core, connection, user):
    from .instrument_availability import assign_category_defaults
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT instrument_id,instrument_name,registry_number FROM instruments ORDER BY instrument_id FOR UPDATE")
        old = cursor.fetchall(); used = set(); updated = added = 0
        for data in registry_records():
            source = data.pop("source_name")
            item = core.Instrument(**data)
            matches = [row for row in old if row["registry_number"] == item.registry_number]
            if not matches:
                matches = [row for row in old if row["instrument_id"] not in used and row["registry_number"] is None
                           and row["instrument_name"] == source]
            if len(matches) > 1:
                raise ValueError(f"Ambiguous registry migration for {item.registry_number}")
            before = core.audit_snapshot(connection,"instruments",matches[0]["instrument_id"]) if matches else None
            if matches:
                record_id = matches[0]["instrument_id"]; used.add(record_id)
                cursor.execute("""UPDATE instruments SET registry_number=%s,instrument_name=%s,
                    parameters_taken=%s,category=%s,element_ids=%s,is_active=%s WHERE instrument_id=%s""",
                    (item.registry_number,item.instrument_name,item.parameters_taken,item.category,json.dumps(item.element_ids),item.is_active,record_id))
            else:
                cursor.execute("""INSERT INTO instruments (registry_number,instrument_name,parameters_taken,
                    category,element_ids,is_active,created_by_user_id,recorded_by_username)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (item.registry_number,item.instrument_name,
                        item.parameters_taken,item.category,json.dumps(item.element_ids),item.is_active,user.get("user_id"),user["username"]))
                record_id = cursor.lastrowid; added += 1
            core.validate_station_categories(cursor,item.station_categories)
            core.replace_instrument_station_categories(cursor,record_id,item.station_categories)
            after = core.audit_snapshot(connection,"instruments",record_id)
            if before and before != after:
                core.record_entity_edit(connection,"instruments",record_id,before,after,user); updated += 1
        if any(row["instrument_id"] not in used for row in old):
            raise ValueError("Unexpected existing instruments outside the supplied registry; update rolled back to protect linked history")
        defaults = assign_category_defaults(core,connection,user)
        return dict(updated=updated,added=added,new_assignments=defaults["added"],registry_count=len(SPECS))
    finally:
        cursor.close()
