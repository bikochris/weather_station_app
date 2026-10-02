let preMaintenanceStations = [];
let preMaintenanceReports = [];
let selectedPreMaintenanceReportStationIds = new Set();
let activePreMaintenanceReportDistrict = "";
const preMaintenanceCollection = createCollectionState("period_end", "desc");

function reportPeriod(report) {
    return report.period_start === report.period_end
        ? report.period_start
        : `${report.period_start} to ${report.period_end}`;
}

function reportStationNames(report) {
    return report.stations.map((station) =>
        `${station.station_code} - ${station.station_name}`).join(", ") || "-";
}

function filteredPreMaintenanceReports() {
    const stationId = Number(document.getElementById("stationFilter").value);
    const dateFrom = document.getElementById("maintenanceDateFrom").value;
    const dateTo = document.getElementById("maintenanceDateTo").value;
    const search = preMaintenanceCollection.search.toLowerCase();
    return preMaintenanceReports.filter((report) => {
        const stationMatch = !stationId || report.stations.some((station) =>
            station.station_id === stationId
        );
        const dateMatch = (!dateFrom || report.period_end >= dateFrom)
            && (!dateTo || report.period_start <= dateTo);
        const text = [
            reportPeriod(report),
            reportStationNames(report),
            report.original_filename,
            report.notes,
            report.uploaded_by_username
        ].join(" ").toLowerCase();
        return stationMatch && dateMatch && (!search || text.includes(search));
    }).sort((left, right) => {
        const leftValue = left[preMaintenanceCollection.sortBy] || "";
        const rightValue = right[preMaintenanceCollection.sortBy] || "";
        const order = String(leftValue).localeCompare(String(rightValue));
        return preMaintenanceCollection.sortOrder === "asc" ? order : -order;
    });
}

function renderPreMaintenanceStatistics(reports) {
    const coveredStations = new Set();
    reports.forEach((report) => {
        report.stations.forEach((station) => coveredStations.add(station.station_id));
    });
    setMetric("maintenanceTotalMetric", reports.length);
    setMetric("maintenanceStationMetric", coveredStations.size);
    setMetric("maintenanceInstrumentMetric", reports.length);
    setMetric("maintenanceLatestMetric", reports[0]?.period_end || "-");

    const byUploader = {};
    reports.forEach((report) => {
        const uploader = report.uploaded_by_username || "Legacy record";
        byUploader[uploader] = (byUploader[uploader] || 0) + 1;
    });
    renderBreakdown(
        "maintenanceCategoryBreakdown",
        Object.entries(byUploader).sort(([left], [right]) => left.localeCompare(right)),
        "No pre-maintenance reports uploaded."
    );
}

async function loadStationOptions() {
    const filterSelect = document.getElementById("stationFilter");
    const response = await apiFetch("/stations");
    if (!response.ok) {
        throw new Error(await getErrorMessage(response, "Unable to load stations"));
    }

    preMaintenanceStations = await response.json();
    filterSelect.replaceChildren();
    filterSelect.appendChild(new Option("All stations", ""));
    preMaintenanceStations.forEach((station) => {
        const category = station.station_category || "Not classified";
        const label = `${station.station_code} - ${station.station_name} (${category})`;
        filterSelect.appendChild(new Option(label, station.station_id));
    });
    renderPreMaintenanceReportDistricts();
    renderPreMaintenanceReportStationOptions();
}

function renderPreMaintenanceReportDistricts() {
    const container = document.getElementById("maintenanceReportDistricts");
    const districts = Array.from(new Set(
        preMaintenanceStations.map((station) => station.district).filter(Boolean)
    )).sort((left, right) => left.localeCompare(right));
    if (!districts.includes(activePreMaintenanceReportDistrict)) {
        activePreMaintenanceReportDistrict = districts[0] || "";
    }
    container.replaceChildren();
    districts.forEach((district) => {
        const districtStations = preMaintenanceStations.filter((station) =>
            station.district === district
        );
        const selected = districtStations.filter((station) =>
            selectedPreMaintenanceReportStationIds.has(station.station_id)).length;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "report-district-tab";
        button.classList.toggle("active", district === activePreMaintenanceReportDistrict);
        const name = document.createElement("span");
        name.textContent = district;
        const count = document.createElement("small");
        count.textContent = selected ? `${selected}/${districtStations.length}` : districtStations.length;
        button.append(name, count);
        button.addEventListener("click", () => {
            activePreMaintenanceReportDistrict = district;
            document.getElementById("reportStationSearch").value = "";
            renderPreMaintenanceReportDistricts();
            renderPreMaintenanceReportStationOptions();
        });
        container.appendChild(button);
    });
}

function renderPreMaintenanceReportStationOptions() {
    const container = document.getElementById("maintenanceReportStations");
    const search = document.getElementById("reportStationSearch").value.trim().toLowerCase();
    container.replaceChildren();
    const matching = preMaintenanceStations.filter((station) =>
        station.district === activePreMaintenanceReportDistrict
        && (!search || `${station.station_code} ${station.station_name}`.toLowerCase().includes(search))
    );
    matching.forEach((station) => {
        const label = document.createElement("label");
        label.className = "report-station-option";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = station.station_id;
        checkbox.checked = selectedPreMaintenanceReportStationIds.has(station.station_id);
        checkbox.addEventListener("change", () => {
            if (checkbox.checked) selectedPreMaintenanceReportStationIds.add(station.station_id);
            else selectedPreMaintenanceReportStationIds.delete(station.station_id);
            setMetric("selectedReportStationCount", selectedPreMaintenanceReportStationIds.size);
            renderPreMaintenanceReportDistricts();
        });
        const text = document.createElement("span");
        text.textContent = station.station_name;
        text.title = station.station_name;
        label.append(checkbox, text);
        container.appendChild(label);
    });
    if (!matching.length) {
        const message = document.createElement("p");
        message.className = "muted";
        message.textContent = activePreMaintenanceReportDistrict
            ? "No stations match this search in the selected district."
            : "No district stations are available.";
        container.appendChild(message);
    }
    setMetric("selectedReportStationCount", selectedPreMaintenanceReportStationIds.size);
}

function resetPreMaintenanceReportForm() {
    document.getElementById("maintenanceReportForm").reset();
    const today = new Date().toISOString().slice(0, 10);
    document.getElementById("reportPeriodStart").value = today;
    document.getElementById("reportPeriodEnd").value = today;
    selectedPreMaintenanceReportStationIds.clear();
    document.getElementById("reportStationSearch").value = "";
    renderPreMaintenanceReportDistricts();
    renderPreMaintenanceReportStationOptions();
}

async function downloadPreMaintenanceReport(report) {
    const response = await apiFetch(`/pre-maintenance-reports/${report.report_id}/file`);
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to download pre-maintenance report"));
        return;
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = report.original_filename;
    link.click();
    URL.revokeObjectURL(link.href);
}

async function deletePreMaintenanceReport(reportId) {
    if (!window.confirm("Delete this pre-maintenance report?")) return;
    const response = await apiFetch(`/pre-maintenance-reports/${reportId}`, {method: "DELETE"});
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to delete pre-maintenance report"));
        return;
    }
    await loadPreMaintenanceReports();
}

function appendReportRow(table, report, canManage) {
    const row = document.createElement("tr");
    appendCell(row, reportPeriod(report));
    appendCell(row, reportStationNames(report));
    const fileCell = appendCell(row, "");
    const download = document.createElement("button");
    download.type = "button";
    download.className = "text-button";
    download.textContent = report.original_filename;
    download.addEventListener("click", () => downloadPreMaintenanceReport(report));
    fileCell.appendChild(download);
    appendCell(row, report.notes || "-");
    appendCell(row, report.uploaded_by_username || "Legacy record");
    appendCell(row, new Date(report.uploaded_at).toLocaleString());
    if (canManage) {
        const actions = appendCell(row, "");
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "danger-button compact-button";
        remove.textContent = "Delete";
        remove.addEventListener("click", () => deletePreMaintenanceReport(report.report_id));
        actions.appendChild(remove);
    }
    table.appendChild(row);
}

function renderReportTables(canManage) {
    const uploadTable = document.getElementById("maintenanceReportTable");
    const historyTable = document.getElementById("maintenanceTable");
    const filtered = filteredPreMaintenanceReports();
    const columnCount = canManage ? 7 : 6;
    renderPreMaintenanceStatistics(preMaintenanceReports);
    uploadTable.replaceChildren();
    historyTable.replaceChildren();
    if (!preMaintenanceReports.length) {
        showTableMessage(uploadTable, columnCount, "No pre-maintenance reports uploaded.");
    } else {
        preMaintenanceReports.slice(0, 10).forEach((report) =>
            appendReportRow(uploadTable, report, canManage)
        );
    }
    if (!filtered.length) {
        showTableMessage(historyTable, columnCount, "No pre-maintenance reports found.");
        document.getElementById("maintenancePagination").replaceChildren();
        return;
    }

    const total = filtered.length;
    const pages = Math.max(1, Math.ceil(total / preMaintenanceCollection.pageSize));
    if (preMaintenanceCollection.page > pages) preMaintenanceCollection.page = pages;
    const start = (preMaintenanceCollection.page - 1) * preMaintenanceCollection.pageSize;
    filtered.slice(start, start + preMaintenanceCollection.pageSize).forEach((report) =>
        appendReportRow(historyTable, report, canManage)
    );
    renderPagination(
        "maintenancePagination",
        {
            total,
            page: preMaintenanceCollection.page,
            page_size: preMaintenanceCollection.pageSize,
            pages
        },
        preMaintenanceCollection,
        () => renderReportTables(canManage)
    );
}

async function loadPreMaintenanceReports() {
    const response = await apiFetch("/pre-maintenance-reports");
    if (!response.ok) {
        throw new Error(await getErrorMessage(response, "Unable to load pre-maintenance reports"));
    }
    const result = await response.json();
    preMaintenanceReports = result.items;
    document.getElementById("maintenanceReportActionsHeading").hidden = !result.can_manage;
    document.getElementById("maintenanceActionsHeading").hidden = !result.can_manage;
    renderReportTables(result.can_manage);
}

function downloadFilteredCsv() {
    const rows = [["Period", "Stations", "Report", "Notes", "Uploaded by", "Uploaded at"]];
    filteredPreMaintenanceReports().forEach((report) => {
        rows.push([
            reportPeriod(report),
            reportStationNames(report),
            report.original_filename,
            report.notes || "",
            report.uploaded_by_username || "Legacy record",
            new Date(report.uploaded_at).toLocaleString()
        ]);
    });
    const csv = rows.map((row) =>
        row.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(",")
    ).join("\n");
    const url = URL.createObjectURL(new Blob([csv], {type: "text/csv"}));
    const link = document.createElement("a");
    link.href = url;
    link.download = "pre-maintenance-reports.csv";
    link.click();
    URL.revokeObjectURL(url);
}

document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    const canManage = canManageMaintenance();
    const message = document.getElementById("maintenanceReportMessage");
    document.getElementById("maintenanceReportForm").hidden = !canManage;
    resetPreMaintenanceReportForm();

    try {
        await loadStationOptions();
        await loadPreMaintenanceReports();
    } catch (error) {
        setMessage(message, error.message, "error");
    }

    document.getElementById("maintenanceReportForm").addEventListener("submit", async (event) => {
        event.preventDefault();
        const start = document.getElementById("reportPeriodStart").value;
        const end = document.getElementById("reportPeriodEnd").value;
        const file = document.getElementById("maintenanceReportFile").files[0];
        if (!selectedPreMaintenanceReportStationIds.size) {
            setMessage(message, "Select at least one station covered by this report.", "error");
            return;
        }
        if (end < start) {
            setMessage(message, "Pre-maintenance end date cannot be before the start date.", "error");
            return;
        }
        const parameters = new URLSearchParams({
            period_start: start,
            period_end: end,
            station_ids: Array.from(selectedPreMaintenanceReportStationIds).join(","),
            filename: file.name
        });
        const notes = document.getElementById("maintenanceReportNotes").value.trim();
        if (notes) parameters.set("notes", notes);
        setMessage(message, "Uploading report...");
        const response = await apiFetch(`/pre-maintenance-reports?${parameters}`, {
            method: "POST",
            headers: {"Content-Type": file.type || "application/octet-stream"},
            body: file
        });
        if (!response.ok) {
            setMessage(message, await getErrorMessage(response, "Unable to upload pre-maintenance report"), "error");
            return;
        }
        resetPreMaintenanceReportForm();
        setMessage(message, "Pre-maintenance report saved for the selected stations.", "success");
        await loadPreMaintenanceReports();
    });

    document.getElementById("refreshMaintenance").addEventListener("click", () =>
        renderReportTables(canManage)
    );
    document.getElementById("stationFilter").addEventListener("change", () => {
        preMaintenanceCollection.page = 1;
        renderReportTables(canManage);
    });
    document.getElementById("maintenanceDateFrom").addEventListener("change", () => {
        preMaintenanceCollection.page = 1;
        renderReportTables(canManage);
    });
    document.getElementById("maintenanceDateTo").addEventListener("change", () => {
        preMaintenanceCollection.page = 1;
        renderReportTables(canManage);
    });
    document.getElementById("maintenanceSearch").addEventListener("input", (event) => {
        preMaintenanceCollection.search = event.target.value.trim();
        preMaintenanceCollection.page = 1;
        renderReportTables(canManage);
    });
    document.getElementById("maintenancePageSize").addEventListener("change", (event) => {
        preMaintenanceCollection.pageSize = Number(event.target.value);
        preMaintenanceCollection.page = 1;
        renderReportTables(canManage);
    });
    document.querySelectorAll(".maintenance-table th[data-sort]").forEach((heading) => {
        heading.classList.add("sortable-heading");
        heading.tabIndex = 0;
        const sort = () => {
            const column = heading.dataset.sort;
            preMaintenanceCollection.sortOrder =
                preMaintenanceCollection.sortBy === column
                    && preMaintenanceCollection.sortOrder === "asc"
                    ? "desc"
                    : "asc";
            preMaintenanceCollection.sortBy = column;
            preMaintenanceCollection.page = 1;
            renderReportTables(canManage);
        };
        heading.addEventListener("click", sort);
        heading.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") sort();
        });
    });
    document.getElementById("maintenanceExportCsv").addEventListener("click", downloadFilteredCsv);
    document.getElementById("maintenanceExportPdf").addEventListener("click", () => window.print());
    document.getElementById("reportStationSearch").addEventListener(
        "input",
        renderPreMaintenanceReportStationOptions
    );
});
