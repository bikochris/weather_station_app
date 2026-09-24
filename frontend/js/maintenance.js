let maintenanceRecords = [];


async function loadStationOptions() {
    const formSelect = document.getElementById("stationId");
    const filterSelect = document.getElementById("stationFilter");
    const response = await apiFetch("/stations");
    if (!response.ok) {
        throw new Error(await getErrorMessage(response, "Unable to load stations"));
    }

    const stations = await response.json();
    formSelect.replaceChildren();
    filterSelect.replaceChildren();
    const placeholder = new Option(
        stations.length ? "Select a station" : "No stations available",
        ""
    );
    formSelect.appendChild(placeholder);
    filterSelect.appendChild(new Option("All stations", ""));
    stations.forEach((station) => {
        formSelect.appendChild(new Option(station.station_name, station.station_id));
        filterSelect.appendChild(new Option(station.station_name, station.station_id));
    });
}


async function loadInstrumentOptions() {
    const container = document.getElementById("instrumentOptions");
    const response = await apiFetch("/instruments?active_only=true");
    if (!response.ok) {
        throw new Error(await getErrorMessage(response, "Unable to load instruments"));
    }

    const instruments = await response.json();
    container.replaceChildren();
    if (!instruments.length) {
        const message = document.createElement("p");
        message.className = "muted";
        message.textContent = "No active instruments are available.";
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
}


function editMaintenance(maintenanceId) {
    const record = maintenanceRecords.find((item) => item.maintenance_id === maintenanceId);
    if (!record) return;
    document.getElementById("maintenanceEditId").value = record.maintenance_id;
    document.getElementById("stationId").value = record.station_id;
    document.getElementById("maintenanceDate").value = record.maintenance_date;
    document.getElementById("issue").value = record.issue;
    document.getElementById("activityDone").value = record.activity_done;
    document.getElementById("recommendations").value = record.recommendations || "";
    document.getElementById("technicians").value = record.technicians;
    const selectedIds = new Set(record.instruments.map((item) => item.instrument_id));
    document.querySelectorAll('input[name="instrumentIds"]').forEach((checkbox) => {
        checkbox.checked = selectedIds.has(Number(checkbox.value));
    });
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
    if (!isITUser()) {
        cell.textContent = "View only";
        return;
    }
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
    showTableMessage(table, 9, "Loading maintenance records...");

    try {
        const response = await apiFetch(endpoint);
        if (!response.ok) {
            throw new Error(await getErrorMessage(response, "Unable to load maintenance records"));
        }
        maintenanceRecords = await response.json();
        table.replaceChildren();
        if (!maintenanceRecords.length) {
            showTableMessage(table, 9, "No maintenance records found.");
            return;
        }
        maintenanceRecords.forEach((record) => {
            const row = document.createElement("tr");
            const names = record.instruments.length
                ? record.instruments.map((instrument) => instrument.instrument_name).join(", ")
                : "Not specified";
            appendCell(row, record.maintenance_id);
            appendCell(row, record.station_name);
            appendCell(row, record.maintenance_date);
            appendCell(row, names);
            appendCell(row, record.issue);
            appendCell(row, record.activity_done);
            appendCell(row, record.recommendations);
            appendCell(row, record.technicians);
            appendMaintenanceActions(row, record);
            table.appendChild(row);
        });
    } catch (error) {
        showTableMessage(table, 9, error.message);
    }
}


document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    const form = document.getElementById("maintenanceForm");
    const message = document.getElementById("formMessage");
    resetMaintenanceForm();

    try {
        await Promise.all([loadStationOptions(), loadInstrumentOptions()]);
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
});
