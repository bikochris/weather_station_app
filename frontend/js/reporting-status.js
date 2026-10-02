let reportingRecords = [];
let canManageReporting = false;
let reportingHistoryPage = 1;
let reportingHistorySortBy = "changed_at";
let reportingHistorySortOrder = "desc";
let openReportingMonth = null;

const reportingHistoryLabels = {
    expected_stations: "Expected", operational_stations: "Operational",
    under_maintenance_stations: "Under maintenance", suspended_stations: "Suspended",
    reported_stations: "Reported", notes: "Notes", recorded_by_username: "Recorded by",
    filename: "File", file_size: "File size", uploaded_by_username: "Uploaded by",
    non_reported_file: "Non-reported file"
};

function historyValue(value) {
    if (value == null || value === "") return "-";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
}

function renderReportingChange(item, cell, expanded = false) {
    const before = item.before_data || {};
    const after = item.after_data || {};
    const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
        .filter(key => !["reporting_status_id", "report_month"].includes(key));
    const changed = keys.filter(key => JSON.stringify(before[key]) !== JSON.stringify(after[key]));
    if (!changed.length) { cell.textContent = "Saved without field changes"; return; }
    const details = document.createElement("details");
    details.open = expanded;
    const summary = document.createElement("summary");
    summary.textContent = `${changed.length} change${changed.length === 1 ? "" : "s"}`;
    const list = document.createElement("ul");
    changed.forEach(key => {
        const line = document.createElement("li");
        line.textContent = `${reportingHistoryLabels[key] || key}: ${historyValue(before[key])} → ${historyValue(after[key])}`;
        list.append(line);
    });
    details.append(summary, list); cell.append(details);
}

async function loadInlineReportingHistory(detailRow, month, page = 1) {
    const content = detailRow.querySelector(".reporting-history-content");
    if (page === 1) content.textContent = "Loading changes...";
    try {
        const params = new URLSearchParams({
            report_month: month, page: String(page), page_size: "100",
            sort_by: "changed_at", sort_order: "desc"
        });
        const response = await apiFetch(`/reporting-status/history?${params}`);
        if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load change history"));
        const result = await response.json();
        if (!detailRow.isConnected || detailRow.hidden) return;
        if (page === 1) content.replaceChildren();
        const previousMore = content.querySelector(".reporting-history-more");
        if (previousMore) previousMore.remove();
        if (!result.items.length && page === 1) {
            content.textContent = "No changes recorded for this month yet.";
            return;
        }
        if (page === 1) {
            const count = document.createElement("p");
            count.className = "reporting-history-count";
            count.textContent = `${result.total} recorded change${result.total === 1 ? "" : "s"}`;
            content.append(count);
        }
        result.items.forEach(item => {
            const entry = document.createElement("div");
            entry.className = "reporting-history-entry";
            const meta = document.createElement("strong");
            meta.textContent = `${new Date(item.changed_at).toLocaleString()}  ·  ${item.action}  ·  ${item.changed_by_username}`;
            const changes = document.createElement("div");
            renderReportingChange(item, changes, true);
            entry.append(meta, changes);
            content.append(entry);
        });
        if (page * result.page_size < result.total) {
            const more = document.createElement("button");
            more.type = "button";
            more.className = "secondary-button reporting-history-more";
            more.textContent = "Show more changes";
            more.addEventListener("click", () => loadInlineReportingHistory(detailRow, month, page + 1));
            content.append(more);
        }
    } catch (error) {
        if (detailRow.isConnected) content.textContent = error.message;
    }
}

function toggleReportingHistory(row, detailRow, month, button) {
    const opening = detailRow.hidden;
    document.querySelectorAll("#reportingTable .reporting-history-detail:not([hidden])").forEach(other => {
        other.hidden = true;
        other.previousElementSibling?.querySelector(".reporting-row-toggle")?.setAttribute("aria-expanded", "false");
    });
    detailRow.hidden = !opening;
    button.setAttribute("aria-expanded", String(opening));
    openReportingMonth = opening ? month : null;
    if (opening) loadInlineReportingHistory(detailRow, month);
}

async function loadReportingHistory() {
    const body = document.getElementById("reportingHistoryRows");
    const params = new URLSearchParams({
        page: String(reportingHistoryPage), page_size: "25",
        sort_by: reportingHistorySortBy, sort_order: reportingHistorySortOrder
    });
    const month = document.getElementById("reportingHistoryMonth").value;
    if (month) params.set("report_month", month);
    try {
        const response = await apiFetch(`/reporting-status/history?${params}`);
        if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load change history"));
        const result = await response.json();
        body.replaceChildren();
        if (!result.items.length) showTableMessage(body, 5, "No reporting changes found.");
        result.items.forEach(item => {
            const row = body.insertRow();
            appendCell(row, new Date(item.changed_at).toLocaleString());
            appendCell(row, item.report_month);
            appendCell(row, item.action);
            appendCell(row, item.changed_by_username);
            renderReportingChange(item, row.insertCell());
        });
        const start = result.total ? (result.page - 1) * result.page_size + 1 : 0;
        const end = Math.min(result.page * result.page_size, result.total);
        document.getElementById("reportingHistoryCount").textContent = `${start}-${end} of ${result.total}`;
        document.getElementById("reportingHistoryPage").textContent = `Page ${result.page}`;
        document.getElementById("reportingHistoryPrevious").disabled = result.page <= 1;
        document.getElementById("reportingHistoryNext").disabled = end >= result.total;
    } catch (error) {
        showTableMessage(body, 5, error.message);
    }
}

function renderReportingChart(items) {
    const chart = document.getElementById("reportingChart");
    chart.replaceChildren();
    items.slice(0, 12).reverse().forEach((item) => {
        const group = document.createElement("div");
        group.className = "bar-group";
        const value = document.createElement("strong");
        value.textContent = item.reporting_percent == null ? "-" : `${item.reporting_percent}%`;
        const track = document.createElement("div");
        track.className = "bar-track";
        const bar = document.createElement("div");
        bar.className = "bar-fill";
        bar.style.height = `${Math.min(item.reporting_percent ?? 0, 100)}%`;
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
    setMetric("expectedStationCount", result.expected_stations);
    setMetric("operationalStationCount", result.operational_stations || 0);
    setMetric("underMaintenanceStationCount", result.under_maintenance_stations || 0);
    setMetric("suspendedStationCount", result.suspended_stations || 0);
    setMetric("currentTotal", result.total_stations);
    setMetric("currentOperational", result.operational_stations);
    setMetric("currentMaintenance", result.under_maintenance_stations);
    setMetric("currentSuspended", result.suspended_stations);
    setMetric("currentClosed", result.closed_stations);
    setMetric("currentExpected", result.expected_stations);
    setMetric("currentPercentage", `${result.current_reporting_percent}%`);
    document.getElementById("reportedStations").max = result.expected_stations;
    document.getElementById("reportingLegacyNote").hidden = !reportingRecords.some(item => item.reporting_percent == null);
    if (!reportingRecords.length) showTableMessage(table, canManageReporting ? 12 : 11, "No monthly reporting status recorded.");
    reportingRecords.forEach((item) => {
        const row = document.createElement("tr");
        row.className = "reporting-record-row";
        row.dataset.rowId = item.report_month;
        const monthCell = row.insertCell();
        const toggleButton = document.createElement("button");
        toggleButton.type = "button";
        toggleButton.className = "reporting-row-toggle";
        toggleButton.textContent = item.report_month;
        toggleButton.setAttribute("aria-label", `Show change history for ${item.report_month}`);
        toggleButton.setAttribute("aria-expanded", "false");
        monthCell.append(toggleButton);
        [item.operational_stations ?? "-", item.under_maintenance_stations ?? "-",
            item.suspended_stations ?? "-",
            item.expected_stations ?? "-", item.reported_stations, item.pending_stations ?? "-",
            item.reporting_percent == null ? "-" : `${item.reporting_percent}%`]
            .forEach((value) => appendCell(row, value));
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
        const detailRow = document.createElement("tr");
        detailRow.className = "reporting-history-detail";
        detailRow.dataset.detailFor = item.report_month;
        detailRow.id = `reporting-history-${item.report_month}`;
        toggleButton.setAttribute("aria-controls", detailRow.id);
        detailRow.hidden = true;
        const detailCell = detailRow.insertCell();
        detailCell.colSpan = canManageReporting ? 12 : 11;
        const content = document.createElement("div");
        content.className = "reporting-history-content";
        detailCell.append(content);
        table.appendChild(detailRow);
        toggleButton.addEventListener("click", event => {
            event.stopPropagation();
            toggleReportingHistory(row, detailRow, item.report_month, toggleButton);
        });
        row.addEventListener("click", event => {
            if (event.target.closest("button, a, input, select, textarea, summary")) return;
            toggleReportingHistory(row, detailRow, item.report_month, toggleButton);
        });
    });
    if (openReportingMonth) {
        const row = [...table.querySelectorAll(".reporting-record-row")]
            .find(candidate => candidate.dataset.rowId === openReportingMonth);
        if (row) toggleReportingHistory(row, row.nextElementSibling, openReportingMonth,
            row.querySelector(".reporting-row-toggle"));
        else openReportingMonth = null;
    }
    renderReportingChart(reportingRecords);
    if (document.getElementById("reportingAuditOverview").open) await loadReportingHistory();
}

document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    if (!canViewMonthlyReporting()) {
        window.location.href = "index.html";
        return;
    }
    document.getElementById("reportingEditor").hidden = !canWriteDataOperations();
    const historyHeadings = [...document.querySelectorAll("#reportingHistoryTable th[data-sort]")];
    const updateHistorySort = () => historyHeadings.forEach(heading => {
        const active = heading.dataset.sort === reportingHistorySortBy;
        heading.setAttribute("aria-sort", active
            ? (reportingHistorySortOrder === "asc" ? "ascending" : "descending") : "none");
        heading.title = active && reportingHistorySortOrder === "asc"
            ? "Sort descending" : "Sort ascending";
    });
    historyHeadings.forEach(heading => {
        heading.classList.add("sortable-heading");
        heading.tabIndex = 0;
        const sort = () => {
            reportingHistorySortOrder = reportingHistorySortBy === heading.dataset.sort
                && reportingHistorySortOrder === "asc" ? "desc" : "asc";
            reportingHistorySortBy = heading.dataset.sort;
            reportingHistoryPage = 1;
            updateHistorySort();
            loadReportingHistory();
        };
        heading.addEventListener("click", sort);
        heading.addEventListener("keydown", event => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                sort();
            }
        });
    });
    updateHistorySort();
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
    document.getElementById("reportingAuditOverview").addEventListener("toggle", event => {
        if (event.target.open) loadReportingHistory();
    });
    document.getElementById("reportingHistoryMonth").addEventListener("change", () => {
        reportingHistoryPage = 1;
        loadReportingHistory();
    });
    document.getElementById("clearReportingHistoryMonth").addEventListener("click", () => {
        document.getElementById("reportingHistoryMonth").value = "";
        reportingHistoryPage = 1;
        loadReportingHistory();
    });
    document.getElementById("reportingHistoryPrevious").addEventListener("click", () => {
        reportingHistoryPage -= 1;
        loadReportingHistory();
    });
    document.getElementById("reportingHistoryNext").addEventListener("click", () => {
        reportingHistoryPage += 1;
        loadReportingHistory();
    });
    try {
        await loadReportingStatus();
    } catch (error) {
        showTableMessage(document.getElementById("reportingTable"), 12, error.message);
    }
});
