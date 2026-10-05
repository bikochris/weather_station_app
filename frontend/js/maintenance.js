let maintenanceRecords = [];
let maintenanceStations = [];
let maintenanceInstrumentCatalog = [];
let maintenanceReports = [];
let selectedMaintenanceReportStationIds = new Set();
let activeMaintenanceReportDistrict = "";
const maintenanceCollection = createCollectionState("maintenance_date", "desc");


function renderMaintenanceStatistics(summary) {
    setMetric("maintenanceTotalMetric", summary.total || 0);
    setMetric("maintenanceStationMetric", summary.stations || 0);
    setMetric("maintenanceInstrumentMetric", summary.instruments || 0);
    setMetric("maintenanceLatestMetric", summary.latest || "-");
    renderBreakdown(
        "maintenanceCategoryBreakdown",
        Object.entries(summary.categories || {}).sort(([left], [right]) =>
            left.localeCompare(right)
        ),
        "No maintenance activity in this period"
    );
}


async function loadStationOptions() {
    const formSelect = document.getElementById("stationId");
    const filterSelect = document.getElementById("stationFilter");
    const response = await apiFetch("/stations");
    if (!response.ok) {
        throw new Error(await getErrorMessage(response, "Unable to load stations"));
    }

    maintenanceStations = await response.json();
    formSelect.replaceChildren();
    filterSelect.replaceChildren();
    const placeholder = new Option(
        maintenanceStations.length ? "Select a station" : "No stations available",
        ""
    );
    formSelect.appendChild(placeholder);
    filterSelect.appendChild(new Option("All stations", ""));
    maintenanceStations.forEach((station) => {
        const category = station.station_category || "Not classified";
        const label = `${station.station_code} - ${station.station_name} (${category})`;
        formSelect.appendChild(new Option(label, station.station_id));
        filterSelect.appendChild(new Option(label, station.station_id));
    });
    renderMaintenanceInstrumentOptions();
    renderMaintenanceReportDistricts();
    renderMaintenanceReportStationOptions();
}


function renderMaintenanceReportDistricts() {
    const container = document.getElementById("maintenanceReportDistricts");
    const districts = Array.from(new Set(
        maintenanceStations.map((station) => station.district).filter(Boolean)
    )).sort((left, right) => left.localeCompare(right));
    if (!districts.includes(activeMaintenanceReportDistrict)) {
        activeMaintenanceReportDistrict = districts[0] || "";
    }
    container.replaceChildren();
    districts.forEach((district) => {
        const districtStations = maintenanceStations.filter((station) => station.district === district);
        const selected = districtStations.filter((station) =>
            selectedMaintenanceReportStationIds.has(station.station_id)).length;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "report-district-tab";
        button.classList.toggle("active", district === activeMaintenanceReportDistrict);
        const name = document.createElement("span");
        name.textContent = district;
        const count = document.createElement("small");
        count.textContent = selected ? `${selected}/${districtStations.length}` : districtStations.length;
        button.append(name, count);
        button.addEventListener("click", () => {
            activeMaintenanceReportDistrict = district;
            document.getElementById("reportStationSearch").value = "";
            renderMaintenanceReportDistricts();
            renderMaintenanceReportStationOptions();
        });
        container.appendChild(button);
    });
}


function renderMaintenanceReportStationOptions() {
    const container = document.getElementById("maintenanceReportStations");
    const search = document.getElementById("reportStationSearch").value.trim().toLowerCase();
    container.replaceChildren();
    const matching = maintenanceStations.filter((station) =>
        station.district === activeMaintenanceReportDistrict
        && (!search || `${station.station_code} ${station.station_name}`.toLowerCase().includes(search))
    );
    matching.forEach((station) => {
        const label = document.createElement("label");
        label.className = "report-station-option";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = station.station_id;
        checkbox.checked = selectedMaintenanceReportStationIds.has(station.station_id);
        checkbox.addEventListener("change", () => {
            if (checkbox.checked) selectedMaintenanceReportStationIds.add(station.station_id);
            else selectedMaintenanceReportStationIds.delete(station.station_id);
            setMetric("selectedReportStationCount", selectedMaintenanceReportStationIds.size);
            renderMaintenanceReportDistricts();
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
        message.textContent = activeMaintenanceReportDistrict
            ? "No stations match this search in the selected district."
            : "No district stations are available.";
        container.appendChild(message);
    }
    setMetric("selectedReportStationCount", selectedMaintenanceReportStationIds.size);
}


function resetMaintenanceReportForm() {
    document.getElementById("maintenanceReportForm").reset();
    const today = new Date().toISOString().slice(0, 10);
    document.getElementById("reportPeriodStart").value = today;
    document.getElementById("reportPeriodEnd").value = today;
    selectedMaintenanceReportStationIds.clear();
    document.getElementById("reportStationSearch").value = "";
    renderMaintenanceReportDistricts();
    renderMaintenanceReportStationOptions();
}


async function downloadMaintenanceReport(report) {
    const response = await apiFetch(`/maintenance-reports/${report.report_id}/file`);
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to download maintenance report"));
        return;
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = report.original_filename;
    link.click();
    URL.revokeObjectURL(link.href);
}


async function deleteMaintenanceReport(reportId) {
    if (!window.confirm("Delete this maintenance report?")) return;
    const response = await apiFetch(`/maintenance-reports/${reportId}`, {method: "DELETE"});
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to delete maintenance report"));
        return;
    }
    await loadMaintenanceReports();
}


async function loadMaintenanceReports() {
    const table = document.getElementById("maintenanceReportTable");
    const district = document.getElementById("districtFilter")?.value || "";
    const response = await apiFetch(`/maintenance-reports?${new URLSearchParams({district})}`);
    if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load maintenance reports"));
    const result = await response.json();
    maintenanceReports = result.items;
    document.getElementById("maintenanceReportActionsHeading").hidden = !result.can_manage;
    table.replaceChildren();
    if (!maintenanceReports.length) {
        showTableMessage(table, result.can_manage ? 7 : 6, "No maintenance reports uploaded.");
        return;
    }
    maintenanceReports.forEach((report) => {
        const row = document.createElement("tr");
        appendCell(row, report.period_start === report.period_end
            ? report.period_start
            : `${report.period_start} to ${report.period_end}`);
        appendCell(row, report.stations.map((station) =>
            `${station.station_code} - ${station.station_name}`).join(", ") || "-");
        const fileCell = appendCell(row, "");
        const download = document.createElement("button");
        download.type = "button";
        download.className = "text-button";
        download.textContent = report.original_filename;
        download.addEventListener("click", () => downloadMaintenanceReport(report));
        fileCell.appendChild(download);
        appendCell(row, report.notes || "-");
        appendCell(row, report.uploaded_by_username || "Legacy record");
        appendCell(row, new Date(report.uploaded_at).toLocaleString());
        if (result.can_manage) {
            const actions = appendCell(row, "");
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "danger-button compact-button";
            remove.textContent = "Delete";
            remove.addEventListener("click", () => deleteMaintenanceReport(report.report_id));
            actions.appendChild(remove);
        }
        table.appendChild(row);
    });
}


async function loadInstrumentOptions() {
    const container = document.getElementById("instrumentOptions");
    const response = await apiFetch("/instruments?active_only=true");
    if (!response.ok) {
        throw new Error(await getErrorMessage(response, "Unable to load instruments"));
    }

    maintenanceInstrumentCatalog = await response.json();
    renderMaintenanceInstrumentOptions();
}


function renderMaintenanceInstrumentOptions(selectedIds = new Set()) {
    const container = document.getElementById("instrumentOptions");
    const stationId = Number(document.getElementById("stationId").value);
    const station = maintenanceStations.find((item) => item.station_id === stationId);
    const instruments = station?.station_category
        ? maintenanceInstrumentCatalog.filter((instrument) =>
            (instrument.station_categories || []).includes(station.station_category)
        )
        : [];
    container.replaceChildren();
    document.getElementById("maintenanceInstrumentDetails").replaceChildren();
    if (!station) {
        const message = document.createElement("p");
        message.className = "muted";
        message.textContent = "Select a station to see its assigned instruments.";
        container.appendChild(message);
        return;
    }
    if (!station.station_category) {
        const message = document.createElement("p");
        message.className = "muted";
        message.textContent = "Classify this station before selecting instruments.";
        container.appendChild(message);
        return;
    }
    if (!instruments.length) {
        const message = document.createElement("p");
        message.className = "muted";
        message.textContent = "No active instruments are assigned to this station category.";
        container.appendChild(message);
        return;
    }

    instruments.forEach((instrument) => {
        const label = document.createElement("label");
        label.className = "instrument-option";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.name = "instrumentIds";
        checkbox.value = instrument.instrument_id;
        checkbox.checked = selectedIds.has(instrument.instrument_id);
        checkbox.addEventListener("change", () => renderMaintenanceInstrumentDetails());
        const details = document.createElement("span");
        const name = document.createElement("strong");
        name.textContent = instrument.instrument_name;
        const category = document.createElement("small");
        category.textContent = instrument.category || "Uncategorized";
        details.append(name, category);
        label.append(checkbox, details);
        container.appendChild(label);
    });
    renderMaintenanceInstrumentDetails();
}


function renderMaintenanceInstrumentDetails(initialDetails = null) {
    const container = document.getElementById("maintenanceInstrumentDetails");
    const previous = new Map();
    if (initialDetails) {
        initialDetails.forEach(item => previous.set(Number(item.instrument_id), item));
    } else {
        container.querySelectorAll(".maintenance-instrument-detail").forEach(section => {
            previous.set(Number(section.dataset.instrumentId), {
                issue: section.querySelector('[data-field="issue"]').value,
                action_done: section.querySelector('[data-field="action_done"]').value,
                recommendation: section.querySelector('[data-field="recommendation"]').value
            });
        });
    }
    const selected = [...document.querySelectorAll('input[name="instrumentIds"]:checked')];
    container.replaceChildren();
    if (!selected.length) return;
    const heading = document.createElement("h3");
    heading.className = "maintenance-details-heading";
    heading.textContent = "Work completed by instrument";
    container.append(heading);
    selected.forEach((checkbox) => {
        const instrumentId = Number(checkbox.value);
        const instrument = maintenanceInstrumentCatalog.find(item => item.instrument_id === instrumentId);
        const values = previous.get(instrumentId) || {};
        const section = document.createElement("section");
        section.className = "maintenance-instrument-detail";
        section.dataset.instrumentId = String(instrumentId);
        const title = document.createElement("h4");
        title.textContent = instrument?.instrument_name || `Instrument ${instrumentId}`;
        section.append(title);
        const fields = document.createElement("div");
        fields.className = "maintenance-instrument-fields";
        [["issue", "Issue", true], ["action_done", "Action done", true],
            ["recommendation", "Recommendation", false]].forEach(([key, label, required]) => {
            const field = document.createElement("div"); field.className = "field";
            const id = `maintenance-${key}-${instrumentId}`;
            const caption = document.createElement("label"); caption.htmlFor = id; caption.textContent = label;
            const input = document.createElement("textarea");
            input.id = id; input.dataset.field = key; input.maxLength = 2000;
            input.required = required; input.rows = 3; input.value = values[key] || "";
            field.append(caption, input); fields.append(field);
        });
        section.append(fields); container.append(section);
    });
}


function resetMaintenanceForm() {
    document.getElementById("maintenanceForm").reset();
    document.getElementById("maintenanceEditId").value = "";
    document.getElementById("maintenanceDate").value = new Date().toISOString().slice(0, 10);
    document.getElementById("maintenanceSubmit").textContent = "Save maintenance record";
    document.getElementById("cancelMaintenanceEdit").hidden = true;
    renderMaintenanceInstrumentOptions();
}


function editMaintenance(maintenanceId) {
    const record = maintenanceRecords.find((item) => item.maintenance_id === maintenanceId);
    if (!record) return;
    document.getElementById("maintenanceEditId").value = record.maintenance_id;
    document.getElementById("stationId").value = record.station_id;
    const selectedIds = new Set(record.instruments.map((item) => item.instrument_id));
    renderMaintenanceInstrumentOptions(selectedIds);
    renderMaintenanceInstrumentDetails(record.instruments);
    document.getElementById("maintenanceDate").value = record.maintenance_date;
    document.getElementById("technicians").value = record.technicians;
    document.getElementById("maintenanceSubmit").textContent = "Save changes";
    document.getElementById("cancelMaintenanceEdit").hidden = false;
    document.getElementById("maintenanceForm").scrollIntoView({behavior: "smooth"});
}


async function deleteMaintenance(maintenanceId) {
    if (!window.confirm("Delete this entire maintenance visit and all its instrument entries?")) return;
    const response = await apiFetch(`/maintenance/${maintenanceId}`, {method: "DELETE"});
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to delete maintenance record"));
        return;
    }
    await loadMaintenanceRecords();
}


function appendMaintenanceActions(row, record) {
    const cell = appendCell(row, "");
    const group = document.createElement("div");
    group.className = "action-group";
    const editButton = document.createElement("button");
    editButton.type = "button";
    editButton.className = "secondary-button";
    editButton.textContent = "Edit visit";
    editButton.addEventListener("click", () => editMaintenance(record.maintenance_id));
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "danger-button";
    deleteButton.textContent = "Delete visit";
    deleteButton.addEventListener("click", () => deleteMaintenance(record.maintenance_id));
    group.append(editButton, deleteButton);
    cell.appendChild(group);
}


function appendInstrumentHistory(row, record, instrument, columnCount) {
    const historyColumns = historyColumnsForRow(row, "maintenance");
    const instrumentCell = row.cells[3];
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "record-history-toggle";
    toggle.textContent = "History";
    toggle.setAttribute("aria-expanded", "false");
    const detailRow = document.createElement("tr");
    detailRow.className = "record-history-detail";
    detailRow.hidden = true;
    detailRow.id = `instrument-history-${record.maintenance_id}-${instrument.instrument_id}`;
    toggle.setAttribute("aria-controls", detailRow.id);
    const detailCell = detailRow.insertCell();
    detailCell.colSpan = columnCount;
    const content = document.createElement("div");
    content.className = "instrument-maintenance-history";
    detailCell.append(content);
    instrumentCell.append(document.createElement("br"), toggle);
    row.after(detailRow);
    let loaded = false;
    const open = async () => {
        detailRow.hidden = !detailRow.hidden;
        toggle.setAttribute("aria-expanded", String(!detailRow.hidden));
        if (detailRow.hidden || loaded) return;
        content.textContent = "Loading instrument history...";
        try {
            const response = await apiFetch(`/maintenance/instrument-history/${record.station_id}/${instrument.instrument_id}`);
            if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load instrument history"));
            const history = await response.json();
            if (!detailRow.isConnected) return;
            content.replaceChildren();
            const heading = document.createElement("h4");
            heading.textContent = `${instrument.instrument_name} at ${record.station_name} · ${history.total} visit${history.total === 1 ? "" : "s"}`;
            content.append(heading);
            if (history.items.length) content.append(recordHistoryDownloadButton(
                "maintenance", history.items.map(item => item.maintenance_id), historyColumns));
            history.items.forEach(item => {
                const entry = document.createElement("div");
                entry.className = "instrument-history-entry";
                const title = document.createElement("strong");
                title.textContent = `${item.maintenance_date} · Visit #${item.maintenance_id}`;
                entry.append(title);
                [["Issue", item.issue], ["Action done", item.action_done],
                    ["Recommendation", item.recommendation], ["Technicians", item.technicians],
                    ["Recorded by", item.recorded_by]].forEach(([label, value]) => {
                    const line = document.createElement("div");
                    line.textContent = `${label}: ${value || "-"}`;
                    entry.append(line);
                });
                if (item.changes.length) {
                    const changes = document.createElement("details");
                    const summary = document.createElement("summary");
                    summary.textContent = `${item.changes.length} tracked edit${item.changes.length === 1 ? "" : "s"}`;
                    changes.append(summary);
                    const instrumentSnapshot = (shared, detail) => {
                        const {instrument_details, ...record} = shared || {};
                        return {maintenance_id: item.maintenance_id,
                            instrument_id: instrument.instrument_id,
                            instrument_name: instrument.instrument_name,
                            ...record,
                            issue: detail?.issue || null,
                            action_done: detail?.action_done || null,
                            recommendation: detail?.recommendation || null};
                    };
                    renderRecordHistoryTable(changes, item.changes.map(change => ({
                            changed_at: change.changed_at,
                            changed_by_username: change.changed_by_username,
                            edit_reason: change.edit_reason,
                            before_data: instrumentSnapshot(change.before_full, change.before),
                            after_data: instrumentSnapshot(change.after_full, change.after)
                        })), "maintenance", historyColumns);
                    entry.append(changes);
                }
                content.append(entry);
            });
            loaded = true;
        } catch (error) { content.textContent = error.message; }
    };
    toggle.addEventListener("click", event => { event.stopPropagation(); open(); });
    row.addEventListener("click", event => {
        if (!event.target.closest("button, a, input, select, textarea, summary")) open();
    });
}


async function loadMaintenanceRecords() {
    const table = document.getElementById("maintenanceTable");
    const stationId = document.getElementById("stationFilter").value;
    const parameters = collectionParameters(maintenanceCollection, {
        station_id: stationId,
        district: document.getElementById("districtFilter")?.value || "",
        date_from: document.getElementById("maintenanceDateFrom").value,
        date_to: document.getElementById("maintenanceDateTo").value
    });
    const endpoint = `/maintenance?${parameters}`;
    const canManage = canManageMaintenance();
    const columnCount = canManage ? 10 : 9;
    showTableMessage(table, columnCount, "Loading maintenance records...");

    try {
        const response = await apiFetch(endpoint);
        if (!response.ok) {
            throw new Error(await getErrorMessage(response, "Unable to load maintenance records"));
        }
        const result = await response.json();
        maintenanceRecords = result.items;
        renderMaintenanceStatistics(result.summary);
        renderPagination(
            "maintenancePagination",
            result,
            maintenanceCollection,
            loadMaintenanceRecords
        );
        table.replaceChildren();
        if (!maintenanceRecords.length) {
            showTableMessage(table, columnCount, "No maintenance records found.");
            return;
        }
        maintenanceRecords.forEach((record) => {
            const instruments = record.instruments.length ? record.instruments : [null];
            instruments.forEach((instrument, index) => {
                const row = document.createElement("tr");
                appendCell(row, record.maintenance_id);
                appendCell(row, `${record.station_code} - ${record.station_name}`);
                appendCell(row, record.maintenance_date);
                appendCell(row, instrument?.instrument_name || "Not specified");
                appendCell(row, instrument?.issue || record.issue);
                appendCell(row, instrument?.action_done || record.activity_done);
                appendCell(row, instrument?.recommendation || record.recommendations);
                appendCell(row, record.technicians);
                appendCell(row, record.recorded_by || "Legacy record");
                if (canManage) {
                    if (index === 0) appendMaintenanceActions(row, record);
                    else appendCell(row, "");
                }
                table.appendChild(row);
                if (instrument) appendInstrumentHistory(row, record, instrument, columnCount);
            });
        });
    } catch (error) {
        showTableMessage(table, columnCount, error.message);
    }
}


document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    const canManage = canManageMaintenance();
    document.getElementById("maintenanceEditor").hidden = !canManage;
    document.getElementById("maintenanceReportForm").hidden = !canManage;
    document.getElementById("maintenanceActionsHeading").hidden = !canManage;
    const form = document.getElementById("maintenanceForm");
    const message = document.getElementById("formMessage");
    resetMaintenanceForm();
    resetMaintenanceReportForm();

    try {
        if (canManage) {
            await Promise.all([loadStationOptions(), loadInstrumentOptions()]);
        } else {
            await loadStationOptions();
        }
        setupDistrictFilter(document.querySelector(".history-heading .filter-bar"), maintenanceStations,
            () => { maintenanceCollection.page = 1; loadMaintenanceRecords(); loadMaintenanceReports(); });
        await Promise.all([loadMaintenanceRecords(), loadMaintenanceReports()]);
    } catch (error) {
        setMessage(message, error.message, "error");
    }

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const instrumentIds = Array.from(
            document.querySelectorAll('input[name="instrumentIds"]:checked')
        ).map((checkbox) => Number(checkbox.value));
        if (!instrumentIds.length) {
            setMessage(message, "Select at least one instrument.", "error");
            return;
        }

        const editId = document.getElementById("maintenanceEditId").value;
        setMessage(message, "Saving...");
        const record = {
            station_id: Number(document.getElementById("stationId").value),
            maintenance_date: document.getElementById("maintenanceDate").value,
            instrument_ids: instrumentIds,
            instrument_details: [...document.querySelectorAll(".maintenance-instrument-detail")].map(section => ({
                instrument_id: Number(section.dataset.instrumentId),
                issue: section.querySelector('[data-field="issue"]').value.trim(),
                action_done: section.querySelector('[data-field="action_done"]').value.trim(),
                recommendation: section.querySelector('[data-field="recommendation"]').value.trim() || null
            })),
            technicians: document.getElementById("technicians").value.trim()
        };
        try {
            const response = await apiFetch(
                editId ? `/maintenance/${editId}` : "/maintenance",
                {
                    method: editId ? "PUT" : "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(record)
                }
            );
            if (!response.ok) {
                throw new Error(await getErrorMessage(response, "Unable to save maintenance record"));
            }
            resetMaintenanceForm();
            setMessage(message, editId ? "Maintenance record updated." : "Maintenance record saved.", "success");
            await loadMaintenanceRecords();
        } catch (error) {
            setMessage(message, error.message, "error");
        }
    });

    document.getElementById("maintenanceReportForm").addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = document.getElementById("maintenanceReportMessage");
        const start = document.getElementById("reportPeriodStart").value;
        const end = document.getElementById("reportPeriodEnd").value;
        const file = document.getElementById("maintenanceReportFile").files[0];
        if (!selectedMaintenanceReportStationIds.size) {
            setMessage(message, "Select at least one station covered by this report.", "error");
            return;
        }
        if (end < start) {
            setMessage(message, "Maintenance end date cannot be before the start date.", "error");
            return;
        }
        const parameters = new URLSearchParams({
            period_start: start,
            period_end: end,
            station_ids: Array.from(selectedMaintenanceReportStationIds).join(","),
            filename: file.name
        });
        const notes = document.getElementById("maintenanceReportNotes").value.trim();
        if (notes) parameters.set("notes", notes);
        setMessage(message, "Uploading report...");
        const response = await apiFetch(`/maintenance-reports?${parameters}`, {
            method: "POST",
            headers: {"Content-Type": file.type || "application/octet-stream"},
            body: file
        });
        if (!response.ok) {
            setMessage(message, await getErrorMessage(response, "Unable to upload maintenance report"), "error");
            return;
        }
        resetMaintenanceReportForm();
        setMessage(message, "Maintenance report saved for the selected stations.", "success");
        await loadMaintenanceReports();
    });

    document.getElementById("cancelMaintenanceEdit").addEventListener("click", resetMaintenanceForm);
    document.getElementById("refreshMaintenance").addEventListener("click", loadMaintenanceRecords);
    document.getElementById("stationFilter").addEventListener("change", () => {
        maintenanceCollection.page = 1;
        loadMaintenanceRecords();
    });
    document.getElementById("maintenanceDateFrom").addEventListener(
        "change",
        () => {
            maintenanceCollection.page = 1;
            loadMaintenanceRecords();
        }
    );
    document.getElementById("maintenanceDateTo").addEventListener(
        "change",
        () => {
            maintenanceCollection.page = 1;
            loadMaintenanceRecords();
        }
    );
    bindCollectionControls({
        state: maintenanceCollection,
        reload: loadMaintenanceRecords,
        searchId: "maintenanceSearch",
        pageSizeId: "maintenancePageSize",
        tableSelector: ".maintenance-table",
        exportBasePath: "/maintenance",
        csvButtonId: "maintenanceExportCsv",
        pdfButtonId: "maintenanceExportPdf",
        getExtraParameters: () => ({
            station_id: document.getElementById("stationFilter").value,
            district: document.getElementById("districtFilter")?.value || "",
            date_from: document.getElementById("maintenanceDateFrom").value,
            date_to: document.getElementById("maintenanceDateTo").value
        })
    });
    document.getElementById("stationId").addEventListener(
        "change",
        () => renderMaintenanceInstrumentOptions()
    );
    document.getElementById("reportStationSearch").addEventListener(
        "input",
        renderMaintenanceReportStationOptions
    );
});
