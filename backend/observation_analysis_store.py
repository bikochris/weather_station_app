"""Rebuildable, bounded-memory index of saved observation upload sources."""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from .data_upload_engine import parse_source
from .observation_storage import STORAGE_ROOT


def open_index(root=None):
    root = Path(root or STORAGE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "analysis-cache.sqlite", timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA cache_size=-32768")
    connection.execute("""CREATE TABLE IF NOT EXISTS observations (
        station TEXT, element TEXT, stamp TEXT, value REAL, run_id INTEGER,
        PRIMARY KEY(station,element,stamp)) WITHOUT ROWID""")
    connection.execute("CREATE INDEX IF NOT EXISTS analysis_time ON observations(stamp,station,element)")
    connection.execute("""CREATE TABLE IF NOT EXISTS indexed_runs (
        run_id INTEGER PRIMARY KEY, invalid_rows INTEGER NOT NULL)""")
    return connection


def index_source(connection, run_id, source, filename):
    if connection.execute("SELECT 1 FROM indexed_runs WHERE run_id=?", (run_id,)).fetchone():
        return
    parsed = parse_source(source, filename)
    try:
        lookup = parsed["minute_observations"]
        if hasattr(lookup, "store"):
            rows = lookup.store.connection.execute(
                "SELECT station,element,day,slot,value FROM observations WHERE kind=0 AND value IS NOT NULL")
        else:
            rows = ((station, element, day.isoformat(), slot, value)
                for (station, element, day, slot), value in lookup.items() if value is not None)
        batch = []
        connection.execute("BEGIN IMMEDIATE")
        with connection:
            if connection.execute("SELECT 1 FROM indexed_runs WHERE run_id=?", (run_id,)).fetchone():
                return
            for station, element, day, slot, value in rows:
                stamp = f"{day} {slot // 60:02d}:{slot % 60:02d}"
                batch.append((station, element, stamp, value, run_id))
                if len(batch) >= 10000:
                    _insert(connection, batch)
                    batch.clear()
            if batch:
                _insert(connection, batch)
            connection.execute("INSERT INTO indexed_runs VALUES (?,?)", (run_id, parsed["invalid_rows"]))
    finally:
        if parsed.get("close"):
            parsed["close"]()


def _insert(connection, rows):
    connection.executemany("""INSERT INTO observations VALUES (?,?,?,?,?)
        ON CONFLICT(station,element,stamp) DO UPDATE SET value=excluded.value,run_id=excluded.run_id
        WHERE excluded.run_id>observations.run_id""", rows)


def stored_source(run):
    import json
    snapshot = json.loads(run["config_snapshot"]) if run.get("config_snapshot") else {}
    key = snapshot.get("source_file", {}).get("storage_key")
    if key:
        if Path(key).name != key or "/" in key or "\\" in key:
            raise ValueError("Invalid observation source storage reference")
        source = STORAGE_ROOT / key
        if not source.is_file():
            raise ValueError("A saved observation source is missing. Restore its observation storage backup.")
        return source
    if not run.get("source_data"):
        raise ValueError("A saved observation source is missing. Restore its database backup.")
    return run["source_data"]


def time_range(start, end, connection, station_codes, days=False):
    """UI dates are local station times; the exclusive end avoids double counting."""
    if not station_codes:
        latest = None
    else:
        marks = ",".join("?" for _ in station_codes)
        latest = connection.execute(f"SELECT MAX(stamp) FROM observations WHERE station IN ({marks})", station_codes).fetchone()[0]
    def stamp(value):
        result = datetime.fromisoformat(value)
        if result.tzinfo is not None:
            raise ValueError("Use station local dates and times without a timezone suffix")
        return result.replace(second=0, microsecond=0)
    stop = stamp(end) if end else (stamp(latest) + timedelta(minutes=1) if latest else datetime.now().replace(second=0,microsecond=0))
    begin = stamp(start) if start else (stop - timedelta(days=31)).replace(hour=0,minute=0)
    if days:
        stop = (stamp(end or latest).replace(hour=0, minute=0) if end or latest else stop.replace(hour=0, minute=0)) + timedelta(days=1)
        begin = stamp(start).replace(hour=0,minute=0) if start else (stop - timedelta(days=31))
    if begin >= stop:
        raise ValueError("Start day must not be after end day" if days else "From must be before To (To is exclusive)")
    if (stop - begin).days > 3660:
        raise ValueError("Select at most ten years at a time")
    return begin.strftime("%Y-%m-%d %H:%M"), stop.strftime("%Y-%m-%d %H:%M")
