let reportingRecords = [];
let canManageReporting = false;

function renderReportingChart(items) {
    const chart = document.getElementById("reportingChart");
    chart.replaceChildren();
    items.slice(0, 12).reverse().forEach((item) => {
        const group = document.createElement("div");
        group.className = "bar-group";
        const value = document.createElement("strong");
        value.textContent = `${item.coverage_percent}%`;
        const track = document.createElement("div");
        track.className = "bar-track";
        const bar = document.createElement("div");
        bar.className = "bar-fill";
        bar.style.height = `${Math.min(item.coverage_percent, 100)}%`;
        track.appendChild(bar);
        const label = document.createElement("span");
        label.textContent = item.report_month;
        group.append(value, track, label);
        chart.appendChild(group);
    });
    if (!items.length) chart.textContent = "No reporting status has been recorded.";
}

function resetReportingForm() {
    document.getElementById("reportingForm").reset();
    document.getElementById("reportingMonth").value = new Date().toISOString().slice(0, 7);
    document.getElementById("saveReporting").textContent = "Save monthly status";
    document.getElementById("cancelReportingEdit").hidden = true;
}

function editReportingRecord(id) {
    const item = reportingRecords.find((record) => record.reporting_status_id === id);
    if (!item) return;
    document.getElementById("reportingMonth").value = item.report_month;
    document.getElementById("reportedStations").value = item.reported_stations;
    document.getElementById("reportingNotes").value = item.notes || "";
    document.getElementById("nonReportedFile").value = "";
    document.getElementById("saveReporting").textContent = "Save changes";
    document.getElementById("cancelReportingEdit").hidden = false;
    document.getElementById("reportingEditor").scrollIntoView({behavior: "smooth"});
}

async function deleteReportingRecord(id) {
    if (!window.confirm("Delete this reporting record and its uploaded station list?")) return;
    const response = await apiFetch(`/reporting-status/${id}`, {method: "DELETE"});
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to delete reporting status"));
        return;
    }
    resetReportingForm();
    await loadReportingStatus();
}

async function downloadNonReportedFile(file) {
    const response = await apiFetch(`/reporting-status/non-reported-file/${file.file_id}`);
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to download station list"));
        return;
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = file.original_filename;
    link.click();
    URL.revokeObjectURL(link.href);
}

async function loadReportingStatus() {
    const response = await apiFetch("/reporting-status");
    if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load reporting status"));
    const result = await response.json();
    reportingRecords = result.items;
    canManageReporting = result.can_manage;
    const table = document.getElementById("reportingTable");
    document.getElementById("reportingActionsHeading").hidden = !canManageReporting;
    table.replaceChildren();
    setMetric("operationalStationCount", result.operational_stations || 0);
    document.getElementById("reportedStations").max = result.operational_stations;
    if (!reportingRecords.length) showTableMessage(table, canManageReporting ? 9 : 8, "No monthly reporting status recorded.");
    reportingRecords.forEach((item) => {
        const row = document.createElement("tr");
        [item.report_month, item.expected_stations, item.reported_stations, item.pending_stations,
            `${item.coverage_percent}%`].forEach((value) => appendCell(row, value));
        const fileCell = document.createElement("td");
        if (item.non_reported_file) {
            const download = document.createElement("button");
            download.type = "button";
            download.className = "text-button";
            download.textContent = item.non_reported_file.original_filename;
            download.addEventListener("click", () => downloadNonReportedFile(item.non_reported_file));
            fileCell.appendChild(download);
        } else {
            fileCell.textContent = "-";
        }
        row.appendChild(fileCell);
        [item.recorded_by_username || "-", item.notes || "-"].forEach((value) => appendCell(row, value));
        if (canManageReporting) {
            const actions = document.createElement("td");
            actions.className = "table-actions";
            const edit = document.createElement("button");
            edit.type = "button";
            edit.className = "secondary-button compact-button";
            edit.textContent = "Edit";
            edit.addEventListener("click", () => editReportingRecord(item.reporting_status_id));
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "danger-button compact-button";
            remove.textContent = "Delete";
            remove.addEventListener("click", () => deleteReportingRecord(item.reporting_status_id));
            actions.append(edit, remove);
            row.appendChild(actions);
        }
        table.appendChild(row);
    });
    renderReportingChart(reportingRecords);
    const latest = reportingRecords[0] || {};
    setMetric("latestExpected", latest.expected_stations || result.operational_stations || 0);
    setMetric("latestReported", latest.reported_stations || 0);
    setMetric("latestPending", latest.pending_stations ?? result.operational_stations ?? 0);
    setMetric("latestCoverage", `${latest.coverage_percent || 0}%`);
}

document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    if (!canViewMonthlyReporting()) {
        window.location.href = "index.html";
        return;
    }
    document.getElementById("reportingEditor").hidden = !canWriteDataOperations();
    resetReportingForm();
    document.getElementById("cancelReportingEdit").addEventListener("click", resetReportingForm);
    document.getElementById("reportingForm").addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = document.getElementById("reportingMessage");
        const month = document.getElementById("reportingMonth").value;
        const file = document.getElementById("nonReportedFile").files[0];
        setMessage(message, "Saving...");
        const response = await apiFetch("/reporting-status", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                report_month: month,
                reported_stations: Number(document.getElementById("reportedStations").value),
                notes: document.getElementById("reportingNotes").value.trim() || null
            })
        });
        if (!response.ok) {
            setMessage(message, await getErrorMessage(response, "Unable to save status"), "error");
            return;
        }
        if (file) {
            const upload = await apiFetch(`/reporting-status/non-reported-file?report_month=${encodeURIComponent(month)}&filename=${encodeURIComponent(file.name)}`, {
                method: "POST",
                headers: {"Content-Type": file.type || "application/octet-stream"},
                body: file
            });
            if (!upload.ok) {
                setMessage(message, await getErrorMessage(upload, "Status saved, but the station list could not be uploaded"), "error");
                await loadReportingStatus();
                return;
            }
        }
        resetReportingForm();
        setMessage(message, "Monthly reporting status saved.", "success");
        await loadReportingStatus();
    });
    document.getElementById("refreshReporting").addEventListener("click", loadReportingStatus);
    try {
        await loadReportingStatus();
    } catch (error) {
        showTableMessage(document.getElementById("reportingTable"), 9, error.message);
    }
});
