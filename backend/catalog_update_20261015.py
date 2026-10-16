"""Owner-approved catalog replacement, retaining instrument IDs and assignments."""
import csv
import io
import json


HEADERS = ["Instrument", "Parameters", "Category", "Station categories", "Elements", "Description", "Status"]
ROWS = [
    ["Barometric pressure sensor", "Atmospheric pressure", "Sensing", "Automatic Weather station", "1132", "", "Active"],
    ["Battery", "Voltage", "Power System", "Automatic Rain Gauge", "1081", "", ""],
    ["Ceilometer", "Rainfall size", "sensor", "Automatic Weather station", "", "", "Active"],
    ["Datalogger", "Data Processing Unit", "Data Processing", "Automatic Rain Gauge", "", "", ""],
    ["Digital hygrometer", "Dru Bulb Temperature|Wet Bulb Temperature|Dewpoint Temperature|Relative Humidity", "Sensing", "Principal Station", "101|102|103|105", "", "Active"],
    ["Digital thermometer", "Daily Maximum Temperature|Daily Minimum temperature", "Sensing", "Climatic Station|Principal Station", "2|3", "", ""],
    ["Direct radiometer and sunshine duration sensor", "Direct radiation and sunshine duration", "Sensing", "Automatic Weather station", "", "", "Active"],
    ["Evaporation pan/Hook gauge", "Evaporation Pan 06:00-18:00|Evaporation Pan18:000-06:00|Daily Evapo Pan", "Sensing", "Principal Station", "575|576|18", "", "Active"],
    ["Evaporation Picth", "Evaporation Piche 06:00-18:00|Evaporation Piche 18:000-06:00|Daily Evapo Piche", "Sensing", "Principal Station", "573|574|477", "", "Active"],
    ["Grass thermometer", "Grass temperature", "Sensing", "Principal Station", "85", "", "Active"],
    ["Hygrograph", "Humidity_Hygro", "Sensing", "Principal Station", "10", "", "Active"],
    ["Lightning detector sensor", "Storm", "Sensing", "Automatic Weather station", "", "", "Active"],
    ["Manual rain gauge", "Precipitation", "Sensing", "Climatic Station|Principal Station|Rainfall Station", "5", "", ""],
    ["Measuring Cylinder", "Measure Rainfall", "Supporting Equipment", "Climatic Station", "", "", ""],
    ["Pluviograph/Rain gauge recorder", "Precipitation", "Sensing", "Principal Station", "104", "", "Active"],
    ["Pyranometer", "Global solar radiation", "Sensing", "Automatic Weather station", "", "", "Active"],
    ["Pyrheliometer sensor", "Direct radiation", "Sensing", "Automatic Weather station", "", "", "Active"],
    ["Rain gauge sensor", "Precipitation", "Sensing", "Automatic Rain Gauge", "", "", ""],
    ["Regulator", "Voltage 2", "Power System", "Automatic Rain Gauge", "", "", ""],
    ["Soil moisture and temperature sensor at 10 Cm", "Soil moisture and temperature at 10 Cm", "Sensing", "Automatic Weather station", "", "", "Active"],
    ["Soil moisture and temperature sensor at 100 Cm", "Soil moisture and temperature at 100 Cm", "Sensing", "Automatic Weather station", "", "", "Active"],
    ["Soil moisture and temperature sensor at 20 Cm", "Soil moisture and temperature at 20 Cm", "Sensing", "Automatic Weather station", "", "", "Active"],
    ["Soil moisture and temperature sensor at 50 Cm", "Soil moisture and temperature at 50 Cm", "Sensing", "Automatic Weather station", "", "", "Active"],
    ["Soil thermometer", "Soil temperature", "Sensing", "Automatic Weather station", "", "", "Active"],
    ["Soil thermometer at 10 Cm", "Soil temperature at 10 Cm", "Sensing", "Principal Station", "176", "", "Active"],
    ["Soil thermometer at 20 Cm", "Soil temperature at 20 Cm", "Sensing", "Principal Station", "177", "", "Active"],
    ["Soil thermometer at 50 Cm", "Soil temperature at 50 Cm", "Sensing", "Principal Station", "179", "", "Active"],
    ["Soil thermometer at 100 Cm", "Soil temperature at 100 Cm", "Sensing", "Principal Station", "180", "", "Active"],
    ["Solar Panel", "Solar Charger", "Power System", "Automatic Rain Gauge", "", "", ""],
    ["Stevenson Screen", "Solar Shield", "Supporting Equipment", "Climatic Station", "", "", ""],
    ["Sunshine recorder", "Sunshine duration in hours", "Sensing", "Principal Station", "532", "", "Active"],
    ["Thermograph", "Air temperature", "Sensing", "Principal Station", "183", "", "Active"],
    ["Thermohygrography", "Air temperature|Relative humidity", "Sensing", "Principal Station", "183|184", "", "Active"],
    ["Hygrograph", "Relative humidity", "Sensing", "Principal Station", "184", "", ""],
    ["Tower", "Instrument Holder", "Supporting Equipment", "Automatic Weather station", "", "", "Active"],
    ["Visibility and present weather sensor", "Visibility|present weather", "Sensing", "Automatic Weather station", "", "", "Active"],
    ["Wind direction sensor", "Wind direction", "Sensing", "Automatic Weather station", "", "", "Active"],
    ["Wind speed sensor", "Wind speed", "Sensing", "Automatic Weather station", "", "", "Active"],
    ["Wind totalizer", "wind speed and direction", "Sensing", "Principal Station", "", "", "Active"],
    ["Human Observation", "Wind Direction|Wind Speed|Visibility|Present Weather|Past Weather|Low Cloud Type|Middle Cloud Type|High Cloud Type|Total Cloud Cover|Low Cloud Amount|Low Cloud Height|Middle Cloud Amount|Middle Cloud Height|High Cloud Amount|High Cloud Height", "Observation", "Principal Station", "112|196|110|167|851|190|193|195|115|191|192|194|170|201|171", "", "Active"],
    ["Vapor Pressure Paper", "Vapor Pressure", "Reading", "Principal Station", "166", "", "Active"],
    ["Barometer", "Station Pressure|Geopotential Height", "Sensing", "Principal Station", "106|185", "", "Active"],
]


def catalog_csv():
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(HEADERS); writer.writerows(ROWS)
    return stream.getvalue()


def apply_update(core, connection, user):
    """Caller commits or rolls back the complete catalog update and defaults."""
    from .instrument_availability import assign_category_defaults
    instruments, merged = core.parse_instrument_import(catalog_csv())
    explicit_status = {row[0].casefold() for row in ROWS if row[6]}
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT instrument_id,instrument_name FROM instruments ORDER BY instrument_id FOR UPDATE")
        existing = {row["instrument_name"].casefold(): row["instrument_id"] for row in cursor.fetchall()}
        renamed = "Thermohygrography".casefold()
        legacy = "Thermohygrometer".casefold()
        if renamed in existing and legacy in existing:
            raise ValueError("Both Thermohygrometer and Thermohygrography exist; resolve their identity before updating")
        updated = added = 0
        for item in instruments:
            core.validate_station_categories(cursor, item.station_categories)
            key = item.instrument_name.casefold()
            record_id = existing.get(key) or (existing.get(legacy) if key == renamed else None)
            if record_id:
                before = core.audit_snapshot(connection, "instruments", record_id)
                cursor.execute("""UPDATE instruments SET instrument_name=%s,parameters_taken=%s,
                    category=%s,element_ids=%s,is_active=CASE WHEN %s THEN %s ELSE is_active END
                    WHERE instrument_id=%s""", (item.instrument_name,item.parameters_taken,item.category,
                        json.dumps(item.element_ids), key in explicit_status,item.is_active,record_id))
            else:
                before = None
                cursor.execute("""INSERT INTO instruments (instrument_name,parameters_taken,category,
                    element_ids,is_active,created_by_user_id,recorded_by_username)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""", (item.instrument_name,item.parameters_taken,
                        item.category,json.dumps(item.element_ids),item.is_active,user.get("user_id"),user["username"]))
                record_id = cursor.lastrowid
                added += 1
            core.replace_instrument_station_categories(cursor, record_id, item.station_categories)
            after = core.audit_snapshot(connection, "instruments", record_id)
            if before and before != after:
                core.record_entity_edit(connection, "instruments", record_id, before, after, user)
                updated += 1
        defaults = assign_category_defaults(core, connection, user)
        return dict(updated=updated,added=added,merged_csv_rows=merged,new_assignments=defaults["added"])
    finally:
        cursor.close()
