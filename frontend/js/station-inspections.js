let inspectionRecords = [];
let selectedInspection = null;
let inspectionStations = [];
let inspectionPhotoUrl = null;
let selectedInspectionReportStationIds = new Set();
let activeInspectionReportDistrict = "";
const inspectionCollection = createCollectionState("inspection_date", "desc");
const HQ_INSPECTION_ROLES = new Set(["Admin", "Observation Supervisor at HQ"]);
const MANAGEMENT_INSPECTION_ROLES = new Set([
    "Admin", "Big Data Specialist", "Data Quality Control Specialist", "Division Manager"
]);
const MAINTENANCE_INSPECTION_ROLES = new Set([
    "Admin", "Instrument Maintenance and Calibration Officer"
]);


function inspectionStatusElement(value) {
    const status = document.createElement("span");
    status.className = `status ${String(value).toLowerCase().replaceAll(" ", "-")}`;
    status.textContent = value;
    return status;
}


function renderInspectionSummary(summary) {
    setMetric("inspectionTotalMetric", summary.total || 0);
    setMetric("inspectionOpenMetric", summary.open || 0);
    setMetric("inspectionReviewMetric", summary.under_review || 0);
    setMetric("inspectionSolvedMetric", summary.solved || 0);
    setMetric("inspectionNotSolvedMetric", summary.not_solved || 0);
    setMetric("inspectionClosedMetric", summary.closed || 0);
}


async function loadInspectionStations() {
    const response = await apiFetch("/stations");
    if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load stations"));
    const stations = await response.json();
    inspectionStations = stations;
    const select = document.getElementById("inspectionStation");
    select.replaceChildren(new Option("Select a station", ""));
    stations.forEach((station) => {
        select.appendChild(new Option(
            `${station.station_code} - ${station.station_name} (${station.district})`,
            station.station_id
        ));
    });
    renderInspectionReportDistricts();
    renderInspectionReportStationOptions();
}


function renderInspectionReportDistricts() {
    const container = document.getElementById("inspectionReportDistricts");
    if (!container) return;
    const districts = [...new Set(inspectionStations.map((station) => station.district).filter(Boolean))]
        .sort((left, right) => left.localeCompare(right));
    if (!districts.includes(activeInspectionReportDistrict)) activeInspectionReportDistrict = districts[0] || "";
    container.replaceChildren();
    districts.forEach((district) => {
        const districtStations = inspectionStations.filter((station) => station.district === district);
        const selected = districtStations.filter((station) => selectedInspectionReportStationIds.has(station.station_id)).length;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "report-district-tab";
        button.classList.toggle("active", district === activeInspectionReportDistrict);
        const name = document.createElement("span");
        name.textContent = district;
        const count = document.createElement("small");
        count.textContent = selected ? `${selected}/${districtStations.length}` : districtStations.length;
        button.append(name, count);
        button.addEventListener("click", () => {
            activeInspectionReportDistrict = district;
            document.getElementById("inspectionReportStationSearch").value = "";
            renderInspectionReportDistricts();
            renderInspectionReportStationOptions();
        });
        container.appendChild(button);
    });
}


function renderInspectionReportStationOptions() {
    const container = document.getElementById("inspectionReportStationOptions");
    if (!container) return;
    const search = document.getElementById("inspectionReportStationSearch").value.trim().toLowerCase();
    container.replaceChildren();
    const matching = inspectionStations.filter((station) =>
        station.district === activeInspectionReportDistrict
        && (!search || `${station.station_code} ${station.station_name}`.toLowerCase().includes(search))
    );
    matching.forEach((station) => {
        const label = document.createElement("label");
        label.className = "report-station-option";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = selectedInspectionReportStationIds.has(station.station_id);
        checkbox.addEventListener("change", () => {
            if (checkbox.checked) selectedInspectionReportStationIds.add(station.station_id);
            else selectedInspectionReportStationIds.delete(station.station_id);
            setMetric("inspectionSelectedReportStationCount", selectedInspectionReportStationIds.size);
            renderInspectionReportDistricts();
        });
        const text = document.createElement("span");
        text.textContent = station.station_name;
        label.append(checkbox, text);
        container.appendChild(label);
    });
    if (!matching.length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = "No stations match this district search.";
        container.appendChild(empty);
    }
    setMetric("inspectionSelectedReportStationCount", selectedInspectionReportStationIds.size);
}


function resetInspectionReportForm() {
    document.getElementById("inspectionReportForm").reset();
    const reportDate = new Date().toISOString().slice(0, 10);
    document.getElementById("inspectionReportPeriodStart").value = reportDate;
    document.getElementById("inspectionReportPeriodEnd").value = reportDate;
    selectedInspectionReportStationIds = new Set();
    document.getElementById("inspectionReportStationSearch").value = "";
    renderInspectionReportDistricts();
    renderInspectionReportStationOptions();
}


async function loadInspectionReports() {
    const response = await apiFetch("/station-inspection-reports");
    if (!response.ok) {
        showTableMessage(
            document.getElementById("inspectionReportList"), 7,
            await getErrorMessage(response, "Unable to load inspection reports")
        );
        return;
    }
    renderInspectionReports(await response.json());
}


function resetInspectionForm() {
    document.getElementById("inspectionForm").reset();
    document.getElementById("inspectionEditId").value = "";
    document.getElementById("inspectionDate").value = new Date().toISOString().slice(0, 10);
    document.getElementById("inspectionPhoto").required = true;
    document.getElementById("inspectionSubmit").textContent = "Submit to Officer of Supervisor at HQ";
    document.getElementById("cancelInspectionEdit").hidden = true;
}


function editInspection(record) {
    document.getElementById("inspectionEditId").value = record.inspection_id;
    document.getElementById("inspectionStation").value = record.station_id;
    document.getElementById("inspectionDate").value = record.inspection_date;
    document.getElementById("inspectionFinding").value = record.finding;
    document.getElementById("inspectionPhoto").required = false;
    document.getElementById("inspectionSubmit").textContent = "Save inspection changes";
    document.getElementById("cancelInspectionEdit").hidden = false;
    document.getElementById("inspectionForm").scrollIntoView({behavior: "smooth"});
}


async function deleteInspection(record) {
    if (!window.confirm(`Delete the inspection for ${record.station_name}?`)) return;
    const response = await apiFetch(`/station-inspections/${record.inspection_id}`, {method: "DELETE"});
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to delete inspection"));
        return;
    }
    if (selectedInspection?.inspection_id === record.inspection_id) closeInspectionWorkspace();
    await loadInspections();
    await loadInspectionReports();
}


function inspectionEntryPanel(entry, record) {
    if (["Inspection reported", "Inspection updated", "Station photo uploaded"].includes(entry.action_type)) return "Inspector";
    if (entry.action_type?.startsWith("Maintenance:")) return "Maintenance";
    if (entry.action_type?.startsWith("Management:")) return "Management";
    if (entry.commented_by_department === MAINTENANCE_ROLE) return "Maintenance";
    if (MANAGEMENT_INSPECTION_ROLES.has(entry.commented_by_department)
            && entry.commented_by_department !== "Admin") return "Management";
    if (HQ_INSPECTION_ROLES.has(entry.commented_by_department)) return "HQ Review";
    if (entry.commented_by_user_id === record.created_by_user_id) return "Inspector";
    return "Inspector";
}


function renderPanelTimeline(timeline, entries) {
    timeline.replaceChildren();
    if (!entries.length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = "No action recorded in this panel.";
        timeline.appendChild(empty);
        return;
    }
    [...entries].reverse().forEach((entry) => {
        const item = document.createElement("article");
        const heading = document.createElement("div");
        const action = document.createElement("strong");
        action.textContent = entry.action_type;
        const time = document.createElement("time");
        time.textContent = new Date(entry.commented_at).toLocaleString();
        heading.append(action, time);
        const author = document.createElement("small");
        author.textContent = `${entry.commented_by_username || "Former user"} · ${entry.commented_by_department || "Role unavailable"}`;
        item.append(heading, author);
        if (entry.comment) {
            const comment = document.createElement("p");
            comment.textContent = entry.comment;
            item.appendChild(comment);
        }
        if (entry.reason) {
            const reason = document.createElement("p");
            reason.className = "inspection-reason";
            reason.textContent = `Reason: ${entry.reason}`;
            item.appendChild(reason);
        }
        timeline.appendChild(item);
    });
}


function updatePanelFields(form) {
    const isStatus = form.querySelector(".panel-action").value === "status";
    const statusField = form.querySelector(".panel-status-field");
    const reasonField = form.querySelector(".panel-reason-field");
    if (statusField) statusField.hidden = !isStatus;
    const notSolved = isStatus && form.querySelector(".panel-status")?.value === "Not Solved";
    if (reasonField) reasonField.hidden = !notSolved;
    const reason = form.querySelector(".panel-reason");
    if (reason) reason.required = Boolean(notSolved);
}


function canUseInspectionPanel(panel, record) {
    if (record.workflow_stage === "Closed") return false;
    if (panel === "Inspector") return record.created_by_user_id === currentUser.user_id;
    if (panel === "HQ Review") return HQ_INSPECTION_ROLES.has(currentUser.department);
    if (panel === "Maintenance") {
        return record.workflow_stage === "Maintenance"
            && (currentUser.department === "Admin" || MAINTENANCE_INSPECTION_ROLES.has(currentUser.department));
    }
    if (panel === "Management") {
        return record.workflow_stage === "Management"
            && (currentUser.department === "Admin" || MANAGEMENT_INSPECTION_ROLES.has(currentUser.department));
    }
    return false;
}


function renderInspectionSequence(record) {
    document.querySelectorAll("[data-stage-panel]").forEach((panelElement) => {
        const panel = panelElement.dataset.stagePanel;
        const entries = record.comments.filter((entry) => inspectionEntryPanel(entry, record) === panel);
        const isTeamPanel = panel === "Maintenance" || panel === "Management";
        const isRelevantTeam = record.workflow_stage === panel || entries.length > 0;
        panelElement.hidden = isTeamPanel && !isRelevantTeam;
        renderPanelTimeline(panelElement.querySelector("[data-panel-timeline]"), entries);
        const form = panelElement.querySelector("[data-panel-form]");
        const canUse = canUseInspectionPanel(panel, record);
        form.hidden = !canUse;
        form.reset();
        if (panel === "HQ Review" && record.workflow_stage === "HQ Review") {
            const action = form.querySelector(".panel-action");
            action.replaceChildren(
                new Option("Close inspection", "close"),
                new Option("Send to Maintenance", "send_maintenance"),
                new Option("Send to Management", "send_management")
            );
        } else if (panel === "HQ Review" && record.workflow_stage !== "Closed") {
            const action = form.querySelector(".panel-action");
            action.replaceChildren(
                new Option("Close after team response", "close")
            );
        }
        updatePanelFields(form);
        panelElement.classList.toggle("is-active", record.workflow_stage === panel || (panel === "Inspector" && record.workflow_stage === "HQ Review"));
        const state = panelElement.querySelector("[data-stage-state]");
        if (panel === "Inspector") state.textContent = "Submitted";
        else if (record.workflow_stage === panel) state.textContent = "Action required";
        else if (record.workflow_stage === "Closed") state.textContent = entries.length ? "Completed" : "Not used";
        else if (panel === "HQ Review" && canUse) state.textContent = "Closure available";
        else if (entries.length) state.textContent = "Completed";
        else state.textContent = "Waiting";
    });
}


function latestInspectionAction(record, panel) {
    const entries = record.comments.filter((entry) => inspectionEntryPanel(entry, record) === panel);
    if (!entries.length) return "-";
    const entry = entries[entries.length - 1];
    return entry.action_type || "-";
}


async function downloadInspectionReport(report) {
    const response = await apiFetch(`/station-inspection-reports/${report.report_id}/file`);
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to download report"));
        return;
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = report.original_filename;
    link.click();
    URL.revokeObjectURL(link.href);
}


async function deleteInspectionReport(report) {
    const stationCount = report.stations?.length || 1;
    if (!window.confirm(`Delete ${report.original_filename} from all ${stationCount} linked station(s)?`)) return;
    const response = await apiFetch(`/station-inspection-reports/${report.report_id}`, {method: "DELETE"});
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to delete report"));
        return;
    }
    await loadInspectionReports();
    if (selectedInspection) await reloadSelectedInspection();
}


async function uploadInspectionFile(file, notes, stationIds, periodStart, periodEnd) {
    const query = new URLSearchParams({filename: file.name});
    if (notes) query.set("notes", notes);
    if (stationIds.length) query.set("station_ids", stationIds.join(","));
    query.set("period_start", periodStart);
    query.set("period_end", periodEnd);
    return apiFetch(`/station-inspection-reports?${query}`, {
        method: "POST",
        headers: {"Content-Type": file.type || "application/octet-stream"},
        body: file
    });
}


function renderInspectionReports(reports) {
    const list = document.getElementById("inspectionReportList");
    list.replaceChildren();
    if (!reports.length) {
        showTableMessage(list, 7, "No inspection report has been uploaded.");
        return;
    }
    reports.forEach((report) => {
        const row = document.createElement("tr");
        appendCell(
            row,
            report.period_start === report.period_end
                ? (report.period_start || "-")
                : `${report.period_start || "-"} to ${report.period_end || "-"}`
        );
        const fileCell = appendCell(row, "");
        const downloadName = document.createElement("button");
        downloadName.type = "button";
        downloadName.className = "text-button";
        downloadName.textContent = report.original_filename;
        downloadName.addEventListener("click", () => downloadInspectionReport(report));
        fileCell.appendChild(downloadName);
        appendCell(row, (report.stations || []).map((station) => station.station_name).join(", ") || "-");
        appendCell(row, report.notes || "-");
        appendCell(row, report.uploaded_by_username || "Former user");
        appendCell(row, new Date(report.uploaded_at).toLocaleString());
        const actions = appendCell(row, "");
        actions.className = "action-group";
        const download = document.createElement("button");
        download.type = "button";
        download.className = "secondary-button compact-button";
        download.textContent = "Download";
        download.addEventListener("click", () => downloadInspectionReport(report));
        actions.appendChild(download);
        if (report.can_delete) {
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "danger-button compact-button";
            remove.textContent = "Delete";
            remove.addEventListener("click", () => deleteInspectionReport(report));
            actions.appendChild(remove);
        }
        list.appendChild(row);
    });
}


async function uploadInspectionPhoto(inspectionId, file) {
    const query = new URLSearchParams({filename: file.name});
    return apiFetch(`/station-inspections/${inspectionId}/photo?${query}`, {
        method: "POST",
        headers: {"Content-Type": file.type || "application/octet-stream"},
        body: file
    });
}


async function renderInspectionPhoto(record) {
    const preview = document.getElementById("inspectionPhotoPreview");
    const meta = document.getElementById("inspectionPhotoMeta");
    if (inspectionPhotoUrl) URL.revokeObjectURL(inspectionPhotoUrl);
    inspectionPhotoUrl = null;
    preview.removeAttribute("src");
    if (!record.photo) {
        preview.hidden = true;
        meta.textContent = "Required photo has not been uploaded.";
        return;
    }
    const response = await apiFetch(`/station-inspections/${record.inspection_id}/photo`);
    if (!response.ok) {
        preview.hidden = true;
        meta.textContent = "Photo is currently unavailable.";
        return;
    }
    inspectionPhotoUrl = URL.createObjectURL(await response.blob());
    preview.src = inspectionPhotoUrl;
    preview.hidden = false;
    meta.textContent = `${record.photo.original_filename} · ${record.photo.uploaded_by_username || "Former user"} · ${new Date(record.photo.uploaded_at).toLocaleString()}`;
}


function openInspectionWorkspace(record) {
    selectedInspection = record;
    document.getElementById("inspectionWorkspace").hidden = false;
    document.getElementById("inspectionWorkspaceTitle").textContent = `${record.station_code} · ${record.station_name}`;
    document.getElementById("inspectionCurrentStage").textContent = record.workflow_stage;
    document.getElementById("inspectionCurrentStatus").textContent = record.status;
    document.getElementById("inspectionInspector").textContent = record.created_by_username || "Former user";
    document.getElementById("inspectionUpdatedAt").textContent = new Date(record.updated_at).toLocaleString();
    renderInspectionSequence(record);
    renderInspectionPhoto(record);
    document.getElementById("inspectionWorkspace").scrollIntoView({behavior: "smooth", block: "start"});
}


function closeInspectionWorkspace() {
    selectedInspection = null;
    document.getElementById("inspectionWorkspace").hidden = true;
    if (inspectionPhotoUrl) URL.revokeObjectURL(inspectionPhotoUrl);
    inspectionPhotoUrl = null;
}


async function reloadSelectedInspection() {
    if (!selectedInspection) return;
    const id = selectedInspection.inspection_id;
    const response = await apiFetch(`/station-inspections?inspection_id=${id}&page=1&page_size=10`);
    if (!response.ok) return;
    const result = await response.json();
    if (result.items.length) openInspectionWorkspace(result.items[0]);
    else closeInspectionWorkspace();
    await loadInspections();
}


function appendInspectionActions(row, record) {
    const cell = appendCell(row, "");
    const group = document.createElement("div");
    group.className = "action-group";
    const isInspector = record.created_by_user_id === currentUser.user_id;
    const maintenanceParticipated = latestInspectionAction(record, "Maintenance") !== "-";
    const managementParticipated = latestInspectionAction(record, "Management") !== "-";
    const canReview = isInspector
        || HQ_INSPECTION_ROLES.has(currentUser.department)
        || (MAINTENANCE_INSPECTION_ROLES.has(currentUser.department)
            && (record.workflow_stage === "Maintenance" || maintenanceParticipated))
        || (MANAGEMENT_INSPECTION_ROLES.has(currentUser.department)
            && (record.workflow_stage === "Management" || managementParticipated));
    if (canReview) {
        const review = document.createElement("button");
        review.type = "button";
        review.className = "secondary-button compact-button";
        review.textContent = "Review";
        review.addEventListener("click", () => openInspectionWorkspace(record));
        group.appendChild(review);
    }
    if (record.can_edit) {
        const edit = document.createElement("button");
        edit.type = "button";
        edit.className = "secondary-button compact-button";
        edit.textContent = "Edit";
        edit.addEventListener("click", () => editInspection(record));
        group.appendChild(edit);
    }
    if (record.can_delete) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "danger-button compact-button";
        remove.textContent = "Delete";
        remove.addEventListener("click", () => deleteInspection(record));
        group.appendChild(remove);
    }
    if (!group.children.length) {
        group.textContent = "-";
    }
    cell.appendChild(group);
}


async function loadInspections() {
    const table = document.getElementById("inspectionTable");
    showTableMessage(table, 14, "Loading station inspections...");
    const parameters = collectionParameters(inspectionCollection, {
        stage: document.getElementById("inspectionStageFilter").value,
        status: document.getElementById("inspectionStatusFilter").value,
        date_from: document.getElementById("inspectionDateFrom").value,
        date_to: document.getElementById("inspectionDateTo").value
    });
    const response = await apiFetch(`/station-inspections?${parameters}`);
    if (!response.ok) {
        showTableMessage(table, 14, await getErrorMessage(response, "Unable to load inspections"));
        return;
    }
    const result = await response.json();
    inspectionRecords = result.items;
    renderInspectionSummary(result.summary);
    renderPagination("inspectionPagination", result, inspectionCollection, loadInspections);
    table.replaceChildren();
    if (!inspectionRecords.length) {
        showTableMessage(table, 14, "No station inspections match the current filters.");
        return;
    }
    inspectionRecords.forEach((record) => {
        const row = document.createElement("tr");
        appendCell(row, record.inspection_date);
        appendCell(row, `${record.station_code} - ${record.station_name}`);
        appendCell(row, record.finding);
        appendCell(row, record.workflow_stage);
        const statusCell = appendCell(row, "");
        statusCell.appendChild(inspectionStatusElement(record.status));
        appendCell(row, record.not_solved_reason || "-");
        appendCell(row, latestInspectionAction(record, "Inspector"));
        appendCell(row, latestInspectionAction(record, "HQ Review"));
        appendCell(row, latestInspectionAction(record, "Maintenance"));
        appendCell(row, latestInspectionAction(record, "Management"));
        appendCell(row, record.created_by_username || "Former user");
        appendCell(row, new Date(record.updated_at).toLocaleString());
        appendCell(
            row,
            `${record.photo ? "Photo" : "Photo missing"} · ${record.reports.length} report${record.reports.length === 1 ? "" : "s"}`
        );
        appendInspectionActions(row, record);
        table.appendChild(row);
    });
}


document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    resetInspectionForm();
    try {
        await loadInspectionStations();
        await loadInspections();
        resetInspectionReportForm();
        await loadInspectionReports();
    } catch (error) {
        setMessage(document.getElementById("inspectionMessage"), error.message, "error");
    }

    document.getElementById("inspectionForm").addEventListener("submit", async (event) => {
        event.preventDefault();
        const id = document.getElementById("inspectionEditId").value;
        const stationId = Number(document.getElementById("inspectionStation").value);
        const stationPhoto = document.getElementById("inspectionPhoto").files[0];
        const message = document.getElementById("inspectionMessage");
        setMessage(message, "Saving inspection evidence...");
        const response = await apiFetch(id ? `/station-inspections/${id}` : "/station-inspections", {
            method: id ? "PUT" : "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                station_id: stationId,
                inspection_date: document.getElementById("inspectionDate").value,
                finding: document.getElementById("inspectionFinding").value.trim()
            })
        });
        if (!response.ok) {
            setMessage(message, await getErrorMessage(response, "Unable to save inspection"), "error");
            return;
        }
        const result = await response.json();
        const inspectionId = Number(id || result.inspection_id);
        if (stationPhoto) {
            const photoResponse = await uploadInspectionPhoto(inspectionId, stationPhoto);
            if (!photoResponse.ok) {
                setMessage(message, `${result.message}. Photo upload failed: ${await getErrorMessage(photoResponse, "Unable to upload station photo")}`, "error");
                await loadInspections();
                return;
            }
        }
        resetInspectionForm();
        setMessage(message, stationPhoto ? `${result.message}. Station photo uploaded.` : result.message, "success");
        await loadInspections();
    });

    document.querySelectorAll("[data-panel-form]").forEach((form) => {
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (!selectedInspection) return;
            const action = form.querySelector(".panel-action").value;
            const message = form.querySelector(".panel-message");
            const response = await apiFetch(`/station-inspections/${selectedInspection.inspection_id}/actions`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    action,
                    comment: form.querySelector(".panel-comment").value.trim() || null,
                    status: action === "status" ? form.querySelector(".panel-status").value : null,
                    reason: action === "status" ? form.querySelector(".panel-reason")?.value.trim() || null : null
                })
            });
            if (!response.ok) {
                setMessage(message, await getErrorMessage(response, "Unable to record action"), "error");
                return;
            }
            const result = await response.json();
            setMessage(message, result.message, "success");
            await reloadSelectedInspection();
        });
        form.querySelector(".panel-action").addEventListener("change", () => updatePanelFields(form));
        form.querySelector(".panel-status")?.addEventListener("change", () => updatePanelFields(form));
    });

    document.getElementById("inspectionReportForm").addEventListener("submit", async (event) => {
        event.preventDefault();
        const file = document.getElementById("inspectionReportFile").files[0];
        if (!file) return;
        const notes = document.getElementById("inspectionReportNotes").value.trim();
        const start = document.getElementById("inspectionReportPeriodStart").value;
        const end = document.getElementById("inspectionReportPeriodEnd").value;
        const stationIds = [...selectedInspectionReportStationIds];
        if (!stationIds.length) {
            setMessage(document.getElementById("inspectionReportMessage"), "Select at least one station covered by this report.", "error");
            return;
        }
        if (end < start) {
            setMessage(document.getElementById("inspectionReportMessage"), "Inspection end date cannot be before the start date.", "error");
            return;
        }
        setMessage(document.getElementById("inspectionReportMessage"), "Uploading inspection report...");
        const response = await uploadInspectionFile(file, notes, stationIds, start, end);
        if (!response.ok) {
            setMessage(document.getElementById("inspectionReportMessage"), await getErrorMessage(response, "Unable to upload report"), "error");
            return;
        }
        setMessage(document.getElementById("inspectionReportMessage"), "Inspection report uploaded.", "success");
        resetInspectionReportForm();
        await loadInspectionReports();
        await loadInspections();
    });

    document.getElementById("inspectionReportStationSearch").addEventListener("input", renderInspectionReportStationOptions);
    document.getElementById("cancelInspectionEdit").addEventListener("click", resetInspectionForm);
    document.getElementById("closeInspectionWorkspace").addEventListener("click", closeInspectionWorkspace);
    document.getElementById("refreshInspections").addEventListener("click", loadInspections);
    ["inspectionStageFilter", "inspectionStatusFilter", "inspectionDateFrom", "inspectionDateTo"].forEach((id) => {
        document.getElementById(id).addEventListener("change", () => {
            inspectionCollection.page = 1;
            loadInspections();
        });
    });
    bindCollectionControls({
        state: inspectionCollection,
        reload: loadInspections,
        searchId: "inspectionSearch",
        pageSizeId: "inspectionPageSize",
        tableSelector: ".inspection-table"
    });

    const notificationRecord = Number(new URLSearchParams(window.location.search).get("record"));
    if (notificationRecord) {
        const response = await apiFetch(`/station-inspections?inspection_id=${notificationRecord}&page=1&page_size=10`);
        if (response.ok) {
            const result = await response.json();
            if (result.items.length) openInspectionWorkspace(result.items[0]);
        }
    }
});
