"""Permission-scoped comparisons and extremes over original saved observations."""
import csv
import io
import logging
import sqlite3
from threading import Lock
from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response
from .observation_analysis_store import open_index, index_source, stored_source, time_range

INDEX_LOCK = Lock()
LOGGER = logging.getLogger("uvicorn.error")
COMPARISONS = [
    dict(id="temperature",label="Air temperature",manual_element="101",automatic_element="1012",method="hourly"),
    dict(id="humidity",label="Relative humidity",manual_element="105",automatic_element="1022",method="hourly"),
    *[dict(id=f"soil-{depth}",label=f"Soil temperature at {depth} cm",manual_element=manual,automatic_element=automatic,method="hourly")
      for depth,manual,automatic in [(10,"176","2112"),(20,"177","2212"),(50,"179","2312"),(100,"180","2412")]],
    dict(id="maximum-temperature",label="Daily maximum temperature",manual_element="2",automatic_element="1012",method="max"),
    dict(id="minimum-temperature",label="Daily minimum temperature",manual_element="3",automatic_element="1012",method="min"),
    dict(id="rainfall",label="Meteorological-day rainfall",manual_element="5",automatic_element="1107",method="sum"),
]


def site_pairs(stations):
    groups = {}
    for row in stations:
        if row.get("latitude") is None or row.get("longitude") is None:
            continue
        groups.setdefault((row["latitude"],row["longitude"],row.get("altitude")), []).append(row)
    pairs = []
    for rows in groups.values():
        manual = [row for row in rows if row["station_category"] in {"Rainfall Station","Climatic Station","Principal Station"}]
        automatic = [row for row in rows if row["station_category"] in {"Automatic Rain Gauge","Automatic Weather station"}]
        for left in manual:
            for right in automatic:
                pairs.append(dict(id=left["station_code"]+":"+right["station_code"],
                    label=f'{left["station_name"]} / {right["station_name"]}',
                    manual_code=left["station_code"],automatic_code=right["station_code"],
                    manual_name=left["station_name"],automatic_name=right["station_name"],
                    manual_category=left["station_category"],automatic_category=right["station_category"]))
    return sorted(pairs, key=lambda pair: pair["label"].casefold())


def map_stations(stations):
    """Prefer a manual station at shared locations; keep automatic-only sites."""
    groups = {}
    rank = {"Principal Station": 0, "Climatic Station": 1, "Rainfall Station": 2,
            "Automatic Weather station": 3, "Automatic Rain Gauge": 4}
    for station in stations:
        key = (station.get("latitude"), station.get("longitude"), station.get("altitude"))
        if key[0] is None or key[1] is None:
            key = ("station", station["station_code"])
        groups.setdefault(key, []).append(station)
    chosen = []
    for group in groups.values():
        manual = [row for row in group if rank.get(row["station_category"], 5) <= 2]
        if manual:
            chosen.append(min(manual, key=lambda row: (rank[row["station_category"]], row["station_code"])))
        else:
            chosen.extend(group)
    return chosen


def install_analysis_routes(app):
    from . import main as core
    from .observation_analysis_engine import comparisons, monitoring

    def registry(cursor, user):
        sql = "SELECT s.station_code,s.station_name,s.station_category,s.latitude,s.longitude,s.altitude FROM stations s"
        params = ()
        if user["department"] in core.ASSIGNED_STATION_ROLES:
            sql += " JOIN user_station_assignments a ON a.station_id=s.station_id WHERE a.user_id=%s"
            params = (user["user_id"],)
        cursor.execute(sql, params)
        return cursor.fetchall()

    def prepare(user, sync=True):
        mysql = core.get_connection()
        index = None
        try:
            cursor = mysql.cursor(dictionary=True)
            stations = registry(cursor, user)
            cursor.execute("SELECT run_id,filename,created_at FROM data_upload_runs ORDER BY run_id")
            runs = cursor.fetchall()
            index = open_index()
            warnings = []
            if sync:
                if not INDEX_LOCK.acquire(blocking=False):
                    raise HTTPException(status_code=429, detail="Observation analysis is updating. Please retry shortly.")
                try:
                    known = {row[0] for row in index.execute("SELECT run_id FROM indexed_runs")}
                    for run in runs:
                        if run["run_id"] in known:
                            continue
                        cursor.execute("SELECT filename,source_data,config_snapshot FROM data_upload_runs WHERE run_id=%s", (run["run_id"],))
                        original = cursor.fetchone()
                        try:
                            index_source(index, run["run_id"], stored_source(original), run["filename"])
                        except (ValueError, OSError) as exc:
                            LOGGER.warning("Observation analysis skipped source run %s: %s", run["run_id"], exc)
                            label = "A saved upload" if user["department"] in core.ASSIGNED_STATION_ROLES else f'Upload {run["run_id"]} ({run["filename"]})'
                            warnings.append(label + " could not be indexed; analysis excludes its observations. Restore its original source backup.")
                finally:
                    INDEX_LOCK.release()
            if user["department"] in core.ASSIGNED_STATION_ROLES:
                runs = []
            return index, stations, runs, warnings
        except BaseException:
            if index is not None: index.close()
            raise
        finally:
            if "cursor" in locals(): cursor.close()
            mysql.close()

    def selected(value, items, key):
        wanted = set(value.split("|")) if value else set()
        choices = {str(item[key]) for item in items}
        if wanted - choices:
            raise HTTPException(status_code=422, detail="Choose only available stations, sites or comparisons")
        return [item for item in items if not wanted or str(item[key]) in wanted]

    def output(data, format, name):
        if format != "csv": return data
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        sections = dict(data)
        sections["summary"] = [data["summary"]]
        for side, rose in data.get("roses", {}).items():
            sections[f"{side}_wind_rose"] = [dict(direction_degrees=sector*22.5,count=count,
                total=rose["total"],calm=rose["calm"]) for sector,count in enumerate(rose["bins"])]
        for section in ["summary","per_parameter","rows","rain_rows","daily_max","daily_rows","wind_max","manual_wind_rose","automatic_wind_rose"]:
            rows = sections.get(section)
            if not rows: continue
            writer.writerow([section])
            columns = [key for key in rows[0] if key not in {"inconsistent", "complete", "partial_day", "trusted_value", "observed_automatic", "observed_daily_max"}]
            writer.writerow(columns)
            for row in rows:
                values = [row.get(key) for key in columns]
                writer.writerow(["'"+value if isinstance(value,str) and value.startswith(("=","+","-","@")) else value for value in values])
            writer.writerow([])
        if data.get("warnings"):
            writer.writerow(["Warnings"])
            for warning in data["warnings"]:
                writer.writerow([warning])
        return Response(content="\ufeff"+stream.getvalue(),media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition":f'attachment; filename="{name}.csv"',"X-Content-Type-Options":"nosniff"})

    @app.get("/observation-analysis/options")
    def options(user=Depends(core.require_password_change_complete)):
        index, stations, runs, _ = prepare(user)
        try:
            pairs = site_pairs(stations)
            available = {}
            for station in stations:
                code = station["station_code"]
                available[code] = {rule[key] for rule in COMPARISONS
                    for key in ("manual_element", "automatic_element")
                    if index.execute("SELECT 1 FROM observations WHERE station=? AND element=? LIMIT 1",
                                     (code, rule[key])).fetchone()}
            for pair in pairs:
                pair["comparison_ids"] = [rule["id"] for rule in COMPARISONS
                    if rule["manual_element"] in available[pair["manual_code"]]
                    and rule["automatic_element"] in available[pair["automatic_code"]]]
            return dict(stations=stations,pairs=pairs,comparisons=COMPARISONS,runs=runs)
        finally:
            index.close()

    @app.get("/observation-analysis/comparison")
    def comparison(pair_id: str | None=None, comparison_id: str | None=None, start: str | None=None,
        end: str | None=None, period: str=Query(default="hour",pattern="^(hour|day|week|month)$"), days: bool=False,
        format: str=Query(default="json",pattern="^(json|csv)$"),user=Depends(core.require_password_change_complete)):
        index = None
        try:
            index, stations, _, warnings = prepare(user)
            pairs = selected(pair_id, site_pairs(stations), "id")
            rules = selected(comparison_id, COMPARISONS, "id")
            codes = sorted({pair[key] for pair in pairs for key in ["manual_code","automatic_code"]})
            start, end = time_range(start,end,index,codes,days=days)
            data = comparisons(index,pairs,rules,start,end,period=period)
            data["warnings"].extend(warnings)
            data.update(start=start,end=end)
            if days:
                from datetime import datetime, timedelta
                data.update(start_day=start[:10],end_day=(datetime.fromisoformat(end)-timedelta(days=1)).date().isoformat())
            return output(data,format,"manual-automatic-comparison")
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503,detail="Observation index is busy or unavailable. Please retry shortly.") from exc
        finally:
            if index is not None: index.close()

    @app.get("/observation-analysis/monitoring")
    def rain_wind(station_code: str | None=None,start: str | None=None,end: str | None=None,
        period: str=Query(default="day",pattern="^(hour|day|week|month)$"), days: bool=False,
        format: str=Query(default="json",pattern="^(json|csv)$"),user=Depends(core.require_password_change_complete)):
        index = None
        try:
            index, stations, _, warnings = prepare(user)
            stations = selected(station_code,stations,"station_code")
            start,end = time_range(start,end,index,[row["station_code"] for row in stations],days=days)
            data = monitoring(index,stations,start,end,period=period)
            data["warnings"].extend(warnings)
            data.update(start=start,end=end)
            if days:
                from datetime import datetime, timedelta
                data.update(start_day=start[:10],end_day=(datetime.fromisoformat(end)-timedelta(days=1)).date().isoformat())
            return output(data,format,"rainfall-wind-monitoring")
        except (ValueError,OSError) as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503,detail="Observation index is busy or unavailable. Please retry shortly.") from exc
        finally:
            if index is not None: index.close()

    @app.get("/observation-analysis/maps")
    def geographic_maps(start: str | None=None, end: str | None=None, days: bool=True,
                        user=Depends(core.require_password_change_complete)):
        from datetime import datetime, timedelta
        from .observation_analysis_engine import geographic_monitoring
        index = None
        try:
            index, stations, _, warnings = prepare(user)
            stations = map_stations(stations)
            start, end = time_range(start, end, index, [row["station_code"] for row in stations], days=days)
            data = geographic_monitoring(index, stations, start, end)
            data.update(stations=stations, start=start, end=end, warnings=data["warnings"] + warnings)
            if days:
                data.update(start_day=start[:10], end_day=(datetime.fromisoformat(end)-timedelta(days=1)).date().isoformat())
            return data
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail="Observation index is busy or unavailable. Please retry shortly.") from exc
        finally:
            if index is not None: index.close()
