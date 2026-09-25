let maintenanceRecords = [];
let maintenanceStations = [];
let maintenanceInstrumentCatalog = [];


function renderMaintenanceStatistics(records) {
    const stationIds = new Set(records.map((record) => record.station_id));
    const instrumentIds = new Set();
    const categories = new Map();
    records.forEach((record) => {
        record.instruments.forEach((instrument) => {
            instrumentIds.add(instrument.instrument_id);
        });
        const category = record.station_category || "Not classified";
        categories.set(category, (categories.get(category) || 0) + 1);
    });
    const latest = records.reduce(
        (value, record) => !value || record.maintenance_date > value
            ? record.maintenance_date
            : value,
        ""
    );
    setMetric("maintenanceTotalMetric", records.length);
    setMetric("maintenanceStationMetric", stationIds.size);
    setMetric("maintenanceInstrumentMetric", instrumentIds.size);
    setMetric("maintenanceLatestMetric", latest || "-");
    renderBreakdown(
        "maintenanceCategoryBreakdown",
        Array.from(categories.entries()).sort(([left], [right]) =>
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
        const details = document.createElement("span");
        const name = document.createElement("strong");
        name.textContent = instrument.instrument_name;
        const category = document.createElement("small");
        category.textContent = instrument.category || "Uncategorized";
        details.append(name, category);
        label.append(checkbox, details);
        container.appendChild(label);
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
    document.getElementById("maintenanceDate").value = record.maintenance_date;
    document.getElementById("issue").value = record.issue;
    document.getElementById("activityDone").value = record.activity_done;
    document.getElementById("recommendations").value = record.recommendations || "";
    document.getElementById("technicians").value = record.technicians;
    document.getElementById("maintenanceSubmit").textContent = "Save changes";
    document.getElementById("cancelMaintenanceEdit").hidden = false;
    document.getElementById("maintenanceForm").scrollIntoView({behavior: "smooth"});
}


async function deleteMaintenance(maintenanceId) {
    if (!window.confirm("Delete this maintenance record?")) return;
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
    editButton.textContent = "Edit";
    editButton.addEventListener("click", () => editMaintenance(record.maintenance_id));
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "danger-button";
    deleteButton.textContent = "Delete";
    deleteButton.addEventListener("click", () => deleteMaintenance(record.maintenance_id));
    group.append(editButton, deleteButton);
    cell.appendChild(group);
}


async function loadMaintenanceRecords() {
    const table = document.getElementById("maintenanceTable");
    const stationId = document.getElementById("stationFilter").value;
    const endpoint = stationId
        ? `/maintenance?station_id=${encodeURIComponent(stationId)}`
        : "/maintenance";
    const canManage = canManageMaintenance();
    const columnCount = canManage ? 10 : 9;
    showTableMessage(table, columnCount, "Loading maintenance records...");

    try {
        const response = await apiFetch(endpoint);
        if (!response.ok) {
            throw new Error(await getErrorMessage(response, "Unable to load maintenance records"));
        }
        const records = await response.json();
        const dateFrom = document.getElementById("maintenanceDateFrom").value;
        const dateTo = document.getElementById("maintenanceDateTo").value;
        maintenanceRecords = records.filter((record) =>
            isWithinDateRange(record.maintenance_date, dateFrom, dateTo)
        );
        renderMaintenanceStatistics(maintenanceRecords);
        table.replaceChildren();
        if (!maintenanceRecords.length) {
            showTableMessage(table, columnCount, "No maintenance records found.");
            return;
        }
        maintenanceRecords.forEach((record) => {
            const row = document.createElement("tr");
            const names = record.instruments.length
                ? record.instruments.map((instrument) => instrument.instrument_name).join(", ")
                : "Not specified";
            appendCell(row, record.maintenance_id);
            appendCell(row, `${record.station_code} - ${record.station_name}`);
            appendCell(row, record.maintenance_date);
            appendCell(row, names);
            appendCell(row, record.issue);
            appendCell(row, record.activity_done);
            appendCell(row, record.recommendations);
            appendCell(row, record.technicians);
            appendCell(row, record.recorded_by || "Legacy record");
            if (canManage) appendMaintenanceActions(row, record);
            table.appendChild(row);
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
    document.getElementById("maintenanceActionsHeading").hidden = !canManage;
    const form = document.getElementById("maintenanceForm");
    const message = document.getElementById("formMessage");
    resetMaintenanceForm();

    try {
        if (canManage) {
            await Promise.all([loadStationOptions(), loadInstrumentOptions()]);
        } else {
            await loadStationOptions();
        }
        await loadMaintenanceRecords();
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
            issue: document.getElementById("issue").value.trim(),
            activity_done: document.getElementById("activityDone").value.trim(),
            recommendations: document.getElementById("recommendations").value.trim() || null,
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

    document.getElementById("cancelMaintenanceEdit").addEventListener("click", resetMaintenanceForm);
    document.getElementById("refreshMaintenance").addEventListener("click", loadMaintenanceRecords);
    document.getElementById("stationFilter").addEventListener("change", loadMaintenanceRecords);
    document.getElementById("maintenanceDateFrom").addEventListener(
        "change",
        loadMaintenanceRecords
    );
    document.getElementById("maintenanceDateTo").addEventListener(
        "change",
        loadMaintenanceRecords
    );
    document.getElementById("stationId").addEventListener(
        "change",
        () => renderMaintenanceInstrumentOptions()
    );
});
