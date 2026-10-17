"""Authenticated observation-upload automation and immutable report runs."""
import json
import logging
from pathlib import Path
from threading import BoundedSemaphore
from datetime import date
from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.concurrency import run_in_threadpool
from .instrument_availability import load_inventory, assign_category_defaults, catalog_configuration
from .data_upload_engine import (UploadConfiguration, parse_source, analyze_source, report_pdf,
    report_docx, report_details_zip, csv_bytes, SUMMARY_COLUMNS, ELEMENT_COLUMNS)
from .observation_qc import STATUSES, qc_fields, QC_COLUMNS
from .observation_storage import (MAX_UPLOAD_BYTES, MAX_EXCEL_BYTES, receive_observation_file,
    store_result_details, load_result_details)
from .data_upload_scope import (STATION_STATUSES, registry_configuration, report_profiles,
    choices, recompute_totals, filter_report, add_element_cadences, recover_legacy_elements)

UPLOAD_SLOT = BoundedSemaphore(1)
LOGGER = logging.getLogger("uvicorn.error")


def install_upload_routes(app):
    from . import main as core

    @app.post("/station-instruments/category-defaults")
    def add_category_defaults(user=Depends(core.require_it)):
        connection = core.get_connection()
        try:
            core.ensure_application_tables(connection)
            result = assign_category_defaults(core, connection, user)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def permitted_stations(cursor, user):
        query = "SELECT s.station_id,s.station_code,s.station_name,s.district,s.station_category,s.status FROM stations s"
        params = ()
        if user["department"] in core.ASSIGNED_STATION_ROLES:
            query += " JOIN user_station_assignments a ON a.station_id=s.station_id WHERE a.user_id=%s"
            params = (user["user_id"],)
        cursor.execute(query, params)
        return {row["station_code"]: row for row in cursor.fetchall()}

    def report_elements(cursor, run_id, result, snapshot):
        if "element_results" not in result:
            cursor.execute("SELECT filename,source_data FROM data_upload_runs WHERE run_id=%s", (run_id,))
            original = cursor.fetchone()
            try:
                result = recover_legacy_elements(result, snapshot, original["source_data"] if original else None,
                    original["filename"] if original else "")
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return add_element_cadences(result, snapshot)

    @app.get("/performance-upload/config")
    def get_configuration(user=Depends(core.require_password_change_complete)):
        connection = core.get_connection()
        try:
            core.ensure_application_tables(connection); cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT version,configuration FROM data_upload_config WHERE config_id=1")
            item = cursor.fetchone(); config = json.loads(item["configuration"])
            config["version"] = item["version"]
            allowed = permitted_stations(cursor, user)
            config = registry_configuration(config, allowed)
            config = catalog_configuration(config, load_inventory(cursor, allowed))
            config["observation_upload_max_bytes"] = MAX_UPLOAD_BYTES
            config["observation_qc_schema_version"] = 1
            config["observation_qc_statuses"] = ["none", "passed", "failed"]
            config["observation_excel_max_bytes"] = MAX_EXCEL_BYTES
            return config
        finally:
            if "cursor" in locals(): cursor.close()
            connection.close()

    @app.get("/performance-upload/filter-options")
    def filter_options(user=Depends(core.require_password_change_complete)):
        connection = core.get_connection()
        try:
            core.ensure_application_tables(connection); cursor = connection.cursor(dictionary=True)
            allowed = permitted_stations(cursor, user)
            inventory = load_inventory(cursor, allowed)
            return dict(report_filter_schema_version=1,station_categories=sorted({row["station_category"] for row in allowed.values() if row.get("station_category")}),
                station_statuses=STATION_STATUSES,
                elements=[dict(element_id=code,element_name=name) for code,name in inventory["element_names"].items()])
        finally:
            if "cursor" in locals(): cursor.close()
            connection.close()

    @app.put("/performance-upload/config")
    def save_configuration(item: UploadConfiguration, user=Depends(core.require_it)):
        connection = core.get_connection()
        try:
            core.ensure_application_tables(connection); cursor = connection.cursor(dictionary=True)
            allowed = permitted_stations(cursor, user)
            codes = {station.station_code for station in item.stations} | {code for parameter in item.parameters for code in parameter.station_codes}
            codes |= {code for parameter in item.parameters for code in parameter.excluded_station_codes}
            if not codes.issubset(allowed): raise HTTPException(status_code=422, detail="Choose station codes from the registered stations")
            cursor.execute("SELECT version,configuration FROM data_upload_config WHERE config_id=1 FOR UPDATE")
            old = cursor.fetchone()
            if old["version"] != item.version: raise HTTPException(status_code=409, detail="Settings changed in another session. Reload before saving.")
            new = item.model_dump(); new["version"] += 1
            before = json.loads(old["configuration"])
            if "schedule_defaults_version" in before:
                new["schedule_defaults_version"] = before["schedule_defaults_version"]
            if "manual_station_exclusions_version" in before:
                new["manual_station_exclusions_version"] = before["manual_station_exclusions_version"]
            new["reserved_cloud_version"] = 1
            if "downtime_policy_version" in before:
                new["downtime_policy_version"] = before["downtime_policy_version"]
            if "registry_scope_version" in before:
                new["registry_scope_version"] = before["registry_scope_version"]
            new["archived_parameter_rules"] = dict(before.get("archived_parameter_rules", {}))
            new["archived_parameter_rules"].update({rule["element_id"]: rule for rule in before["parameters"]})
            new = catalog_configuration(registry_configuration(new, allowed), load_inventory(cursor, allowed))
            if new["catalog_errors"]:
                raise HTTPException(status_code=422, detail="Fix instrument element names before saving configuration: " + " ".join(new["catalog_errors"]))
            if not new["parameters"]:
                raise HTTPException(status_code=422, detail="Define element IDs and matching parameter names in the instrument catalog first")
            cursor.execute("UPDATE data_upload_config SET version=%s,configuration=%s,updated_by_username=%s WHERE config_id=1", (new["version"], json.dumps(new), user["username"]))
            core.record_entity_edit(connection, "upload_configuration", 1, before, new, user)
            connection.commit()
            return new
        except Exception:
            connection.rollback(); raise
        finally:
            if "cursor" in locals(): cursor.close()
            connection.close()

    def process_upload(content, filename, user, source_metadata=None):
        try: parsed = parse_source(content, filename)
        except (ValueError, TypeError, OSError) as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
        try: connection = core.get_connection()
        except BaseException:
            if parsed.get("close"): parsed["close"]()
            raise
        details_path = None; committed = False
        try:
            core.ensure_application_tables(connection); cursor = connection.cursor(dictionary=True)
            allowed = permitted_stations(cursor, user)
            if user["department"] in core.ASSIGNED_STATION_ROLES and not parsed["station_codes"].issubset(allowed):
                raise HTTPException(status_code=403, detail="The upload contains stations not assigned to this account")
            cursor.execute("SELECT version,configuration FROM data_upload_config WHERE config_id=1")
            saved = cursor.fetchone(); config = json.loads(saved["configuration"])
            config = registry_configuration(config, allowed)
            inventory = load_inventory(cursor, allowed)
            if inventory["catalog_errors"]:
                raise HTTPException(status_code=422, detail="Fix instrument element names before uploading: " + " ".join(inventory["catalog_errors"]))
            config = catalog_configuration(config, inventory)
            config["stations"] = report_profiles(config)
            if not config["stations"]:
                raise HTTPException(status_code=422, detail="No enabled stations match the Admin report station-status settings")
            if not config["parameters"]:
                raise HTTPException(status_code=422, detail="No catalog elements are defined")
            if config["catalog_pending"]:
                raise HTTPException(status_code=422, detail="Admin must configure and save schedules for new catalog elements: " + ", ".join(config["catalog_pending"]))
            config["inventory_snapshot"] = inventory
            if source_metadata: config["source_file"] = source_metadata
            try: result = analyze_source(parsed, config, inventory=inventory)
            except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
            result.update(config_version=saved["version"], source_filename=filename, qc_schema_version=1, qc_percentage_basis="available")
            for row in result["results"]:
                station = allowed[row["station_code"]]
                row.update(station_category=station.get("station_category"), station_status=station.get("status", "Operational"), district=station.get("district"))
            pdf = report_pdf(result)
            stored_result = result
            source_bytes = content
            if isinstance(content, Path):
                if content.stat().st_size > 20 * 1024**2:
                    stored_result, details_path = store_result_details(result)
                    source_bytes = b""
                else:
                    source_bytes = content.read_bytes()
            cursor.execute("""INSERT INTO data_upload_runs
                (filename,source_data,config_version,config_snapshot,result_data,pdf_data,created_by_user_id,created_by_username)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (filename, source_bytes, saved["version"], json.dumps(config), json.dumps(stored_result), pdf, user["user_id"], user["username"]))
            run_id = cursor.lastrowid
            columns = ["run_id","station_id","station_code","station_name","district","report_month",*SUMMARY_COLUMNS[3:]]
            rows = []
            for item in result["results"]:
                station = allowed[item["station_code"]]
                rows.append((run_id, station["station_id"], item["station_code"], item["station_name"], station["district"], date.fromisoformat(item["report_month"]+"-01"), *[item[key] if item[key] is not None else 0 for key in SUMMARY_COLUMNS[3:]]))
            cursor.executemany("INSERT INTO data_upload_results ("+",".join(columns)+") VALUES ("+",".join(["%s"]*len(columns))+")", rows)
            connection.commit()
            committed = True
            if source_metadata is not None: source_metadata["committed"] = True
            return {"run_id": run_id, "results_count": len(rows), **{key: result[key] for key in ["periods","row_count","invalid_rows","duplicate_rows","warnings"]}, "published": 0, "conflicts": []}
        except Exception:
            connection.rollback(); raise
        finally:
            if "cursor" in locals(): cursor.close()
            connection.close()
            if parsed.get("close"): parsed["close"]()
            if details_path is not None and not committed: details_path.unlink(missing_ok=True)

    @app.post("/performance-upload/analyze", status_code=201)
    async def upload_observations(request: Request, filename: str = Query(min_length=1, max_length=255), user=Depends(core.require_password_change_complete)):
        clean, _group, _mime = core.document_file_details(filename)
        if not UPLOAD_SLOT.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="Another observation file is being processed. Try again when that upload finishes.")
        path = None; metadata = {}
        try:
            path, metadata = await receive_observation_file(request, clean)
            return await run_in_threadpool(process_upload, path, clean, user, metadata)
        except BaseException as exc:
            if isinstance(exc, HTTPException):
                detail = str(exc.detail).replace("\r", " ").replace("\n", " ")[:4000]
                LOGGER.warning("Observation upload rejected: HTTP %s; %s", exc.status_code, detail)
            if path is not None and not metadata.get("committed"): path.unlink(missing_ok=True)
            raise
        finally:
            UPLOAD_SLOT.release()

    @app.get("/performance-upload/results")
    def get_results(station_code: str | None = None, district: str | None = None,
        month_from: str | None = None, month_to: str | None = None,
        station_category: str | None = None, station_status: str | None = None, element_id: str | None = None,
        user=Depends(core.require_password_change_complete)):
        start = core.month_start(month_from) if month_from else None
        end = core.month_start(month_to) if month_to else None
        if start and end and start > end: raise HTTPException(status_code=422, detail="From month must not be after To month")
        connection = core.get_connection()
        try:
            core.ensure_application_tables(connection); cursor = connection.cursor(dictionary=True)
            allowed = permitted_stations(cursor, user)
            if not allowed: return {"items": [], "runs": [], "summary": {"average": 0, "stations": 0, "months": 0, "records": 0}}
            ids = [station["station_id"] for station in allowed.values()]
            params = list(ids)
            conditions = ["r.station_id IN ("+",".join(["%s"]*len(ids))+")",
                "NOT EXISTS (SELECT 1 FROM data_upload_results n WHERE n.station_id=r.station_id AND n.report_month=r.report_month AND n.run_id>r.run_id)"]
            core.add_filter_condition(conditions, params, "r.station_code", station_code)
            core.add_filter_condition(conditions, params, "s.district", district)
            core.add_filter_condition(conditions, params, "s.station_category", station_category)
            core.add_filter_condition(conditions, params, "s.status", station_status)
            if start: conditions.append("r.report_month>=%s"); params.append(start)
            if end: conditions.append("r.report_month<=%s"); params.append(end)
            cursor.execute("SELECT r.*,u.created_at FROM data_upload_results r JOIN data_upload_runs u ON u.run_id=r.run_id JOIN stations s ON s.station_id=r.station_id WHERE "+" AND ".join(conditions)+" ORDER BY r.report_month DESC,r.overall_percent DESC,r.station_name", tuple(params))
            items = cursor.fetchall()
            for item in items:
                item["station_name"] = allowed[item["station_code"]]["station_name"]
                item["report_month"] = str(item["report_month"])[:7]
                for key in ["daily_percent","hourly_percent","overall_percent"]: item[key] = float(item[key])
                if item["daily_expected"] + item["hourly_expected"] == 0: item["overall_percent"] = None
            expected = sum(item["daily_expected"]+item["hourly_expected"] for item in items)
            available = sum(item["daily_available"]+item["hourly_available"] for item in items)
            summary = {"average": round(100*available/expected, 2) if expected else None, "stations": len({i["station_id"] for i in items}), "months": len({i["report_month"] for i in items}), "records": len(items)}
            selected_keys = {(item["run_id"], item["station_code"], item["report_month"]) for item in items}
            run_ids = sorted({item["run_id"] for item in items})
            element_items = []
            selected_elements = choices(element_id)
            if run_ids:
                cursor.execute("SELECT run_id,result_data,config_snapshot FROM data_upload_runs WHERE run_id IN (" + ",".join(["%s"]*len(run_ids)) + ")", tuple(run_ids))
                for run in cursor.fetchall():
                    snapshot = json.loads(run["config_snapshot"]) if run.get("config_snapshot") else {}
                    data = report_elements(cursor, run["run_id"], json.loads(run["result_data"]), snapshot)
                    saved_rows = {(row["station_code"], row["report_month"]): row for row in data.get("results", [])}
                    for item in items:
                        if item["run_id"] == run["run_id"]:
                            item.update(qc_fields(item["daily_expected"] + item["hourly_expected"],
                                item["daily_available"] + item["hourly_available"], saved_rows.get((item["station_code"], item["report_month"]))))
                    for element in data.get("element_results", []):
                        if (run["run_id"], element["station_code"], element["report_month"]) in selected_keys and (not selected_elements or element["element_id"] in selected_elements):
                            element = dict(element, station_name=allowed[element["station_code"]]["station_name"])
                            element.update(qc_fields(element["expected"], element["available"], element))
                            element_items.append(element)
            if selected_elements:
                items = recompute_totals(items, element_items)
                items.sort(key=lambda item: item.get("station_name", ""))
                items.sort(key=lambda item: item["overall_percent"] if item["overall_percent"] is not None else -1, reverse=True)
                items.sort(key=lambda item: item["report_month"], reverse=True)
                expected = sum(item["daily_expected"] + item["hourly_expected"] for item in items)
                available = sum(item["daily_available"] + item["hourly_available"] for item in items)
                summary = dict(average=round(100*available/expected, 2) if expected else None,
                    stations=len({i["station_id"] for i in items}), months=len({i["report_month"] for i in items}), records=len(items))
            groups = {}
            for element in element_items:
                key = (element["element_id"], element["element_name"])
                group = groups.setdefault(key, dict(element_id=key[0], element_name=key[1], expected=0, available=0, missing=0))
                for metric in ["expected", "available", "missing"]:
                    group[metric] += element[metric]
                for status in STATUSES:
                    key = f"qc_{status}"
                    group[key] = group.get(key, 0) + element[key]
            for group in groups.values():
                group["score"] = round(100*group["available"]/group["expected"], 2) if group["expected"] else None
                group.update(qc_fields(group["expected"], group["available"], group))
            expected = sum(item["daily_expected"] + item["hourly_expected"] for item in items)
            available = sum(item["daily_available"] + item["hourly_available"] for item in items)
            summary.update(qc_fields(expected, available, {f"qc_{status}": sum(item.get(f"qc_{status}", 0) for item in items) for status in STATUSES}))
            cursor.execute("""SELECT DISTINCT u.run_id,u.filename,u.config_version,u.created_by_username,u.created_at,u.result_data
                FROM data_upload_runs u JOIN data_upload_results r ON r.run_id=u.run_id
                WHERE r.station_id IN ("""+",".join(["%s"]*len(ids))+") ORDER BY u.run_id DESC LIMIT 100", tuple(ids))
            runs = cursor.fetchall()
            for run in runs:
                data = json.loads(run.pop("result_data"))
                run.update({key:data[key] for key in ["row_count","invalid_rows","duplicate_rows","periods","warnings"]})
                if user["department"] in core.ASSIGNED_STATION_ROLES: run["warnings"] = ["Reports are restricted to your assigned stations."]
                run.update(published=0, conflicts=[])
            return {"items": items, "element_items": element_items, "element_summary": list(groups.values()), "summary": summary, "runs": runs}
        finally:
            if "cursor" in locals(): cursor.close()
            connection.close()

    @app.get("/performance-upload/runs/{run_id}/report")
    def download_report(run_id: int, format: str = Query(pattern="^(pdf|csv|docx|details)$"),
        station_code: str | None = None, district: str | None = None, station_category: str | None = None,
        station_status: str | None = None, element_id: str | None = None,
        month_from: str | None = None, month_to: str | None = None,
        user=Depends(core.require_password_change_complete)):
        start = core.month_start(month_from) if month_from else None
        end = core.month_start(month_to) if month_to else None
        if start and end and start > end: raise HTTPException(status_code=422, detail="From month must not be after To month")
        connection = core.get_connection()
        try:
            core.ensure_application_tables(connection); cursor = connection.cursor(dictionary=True)
            allowed = permitted_stations(cursor, user)
            cursor.execute("SELECT result_data,pdf_data,config_snapshot FROM data_upload_runs WHERE run_id=%s", (run_id,))
            run = cursor.fetchone()
            if run is None: raise HTTPException(status_code=404, detail="Upload report not found")
            summary = json.loads(run["result_data"])
            if not any(row["station_code"] in allowed for row in summary["results"]):
                raise HTTPException(status_code=403, detail="This report contains no stations accessible to this account")
            restricted = any(row["station_code"] not in allowed for row in summary["results"])
            snapshot = json.loads(run["config_snapshot"]) if run.get("config_snapshot") else {}
            result = report_elements(cursor, run_id, load_result_details(summary), snapshot)
            result = filter_report(result, allowed, station_code=station_code, district=district,
                station_category=station_category, station_status=station_status, element_id=element_id,
                month_from=str(start)[:7] if start else None, month_to=str(end)[:7] if end else None)
            selected = result["results"]
            if not selected: raise HTTPException(status_code=404, detail="No report results match the selected filters")
            if restricted: result["warnings"] = ["Report restricted to stations accessible to this account."]
            filtered = any([station_code, district, station_category, station_status, element_id, month_from, month_to])
            if format == "pdf": content, mime, ext = (report_pdf(result) if restricted or filtered or summary.get("qc_percentage_basis") != "available" else run["pdf_data"]), "application/pdf", "pdf"
            elif format == "csv":
                content = csv_bytes(selected, [*SUMMARY_COLUMNS, *QC_COLUMNS]) + b"\r\n" + csv_bytes(result["element_results"], [*ELEMENT_COLUMNS, *QC_COLUMNS]).decode("utf-8-sig").encode("utf-8")
                mime, ext = "text/csv; charset=utf-8", "csv"
            elif format == "details": content, mime, ext = report_details_zip(result), "application/zip", "zip"
            else:
                try: content = report_docx(result)
                except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
                mime, ext = "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
            return Response(content=content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="station-data-report-{run_id}.{ext}"', "X-Content-Type-Options": "nosniff"})
        finally:
            if "cursor" in locals(): cursor.close()
            connection.close()
