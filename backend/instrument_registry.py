"""Permanent public registry identity, preserving legacy foreign-key IDs."""
from fastapi import HTTPException


def ensure_registry(cursor):
    cursor.execute("SHOW COLUMNS FROM instruments LIKE 'registry_number'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE instruments ADD registry_number INT NULL")
    cursor.execute("SHOW INDEX FROM instruments")
    indexes = {}
    for row in cursor.fetchall():
        indexes.setdefault(row[2], []).append(row)
    if not any(len(rows) == 1 and rows[0][4] == "registry_number" and rows[0][1] == 0 for rows in indexes.values()):
        cursor.execute("ALTER TABLE instruments ADD UNIQUE KEY uq_instrument_registry (registry_number)")
    for name, rows in indexes.items():
        if name != "PRIMARY" and len(rows) == 1 and rows[0][4] == "instrument_name" and rows[0][1] == 0:
            cursor.execute("ALTER TABLE instruments DROP INDEX `" + name.replace("`", "``") + "`")
    cursor.execute("SELECT COUNT(*) FROM instruments WHERE registry_number IS NULL")
    if cursor.fetchone()[0] == 0:
        if not any(len(rows) == 1 and rows[0][4] == "instrument_id" and name != "PRIMARY" and rows[0][1] == 0 for name, rows in indexes.items()):
            cursor.execute("ALTER TABLE instruments ADD UNIQUE KEY uq_legacy_instrument_id (instrument_id)")
        primary = indexes.get("PRIMARY", [])
        if not primary or primary[0][4] != "registry_number":
            cursor.execute("ALTER TABLE instruments MODIFY registry_number INT NOT NULL, DROP PRIMARY KEY, ADD PRIMARY KEY (registry_number)")
    cursor.execute("SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME='instrument_registry_immutable'")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""CREATE TRIGGER instrument_registry_immutable BEFORE UPDATE ON instruments
            FOR EACH ROW BEGIN
            IF OLD.registry_number IS NOT NULL AND NOT (OLD.registry_number <=> NEW.registry_number) THEN
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Registry Number is permanent and cannot be changed';
            END IF;
            END""")


def check_registry_change(before, registry_number):
    current = (before or {}).get("registry_number")
    if current is not None and registry_number is not None and current != registry_number:
        raise HTTPException(status_code=409, detail="Registry Number is permanent and cannot be changed")


def import_key(registry_number, name):
    return f"registry:{registry_number}" if registry_number is not None else "name:" + " ".join(name.split()).casefold()
