let stationRecords = [];


function resetStationForm() {
    document.getElementById("stationForm").reset();
    document.getElementById("stationEditId").value = "";
    document.getElementById("stationSubmit").textContent = "Add station";
    document.getElementById("cancelStationEdit").hidden = true;
}


function editStation(stationId) {
    const station = stationRecords.find((item) => item.station_id === stationId);
    if (!station) return;

    document.getElementById("stationEditId").value = station.station_id;
    document.getElementById("stationName").value = station.station_name;
    document.getElementById("latitude").value = station.latitude;
    document.getElementById("longitude").value = station.longitude;
    document.getElementById("status").value = station.status;
    document.getElementById("stationSubmit").textContent = "Save changes";
    document.getElementById("cancelStationEdit").hidden = false;
    document.getElementById("stationForm").scrollIntoView({behavior: "smooth"});
}


async function deleteStation(stationId) {
    const station = stationRecords.find((item) => item.station_id === stationId);
    if (!station || !window.confirm(`Delete station "${station.station_name}"?`)) return;

    const response = await apiFetch(`/stations/${stationId}`, {method: "DELETE"});
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to delete station"));
        return;
    }
    await loadStations();
}


function appendStationActions(row, station) {
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
    editButton.addEventListener("click", () => editStation(station.station_id));
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "danger-button";
    deleteButton.textContent = "Delete";
    deleteButton.addEventListener("click", () => deleteStation(station.station_id));
    group.append(editButton, deleteButton);
    cell.appendChild(group);
}


async function loadStations() {
    const table = document.getElementById("stationTable");
    showTableMessage(table, 6, "Loading stations...");

    try {
        const response = await apiFetch("/stations");
        if (!response.ok) {
            throw new Error(await getErrorMessage(response, "Unable to load stations"));
        }
        stationRecords = await response.json();
        table.replaceChildren();

        if (!stationRecords.length) {
            showTableMessage(table, 6, "No stations found.");
            return;
        }

        stationRecords.forEach((station) => {
            const row = document.createElement("tr");
            appendCell(row, station.station_id);
            appendCell(row, station.station_name);
            appendCell(row, station.latitude);
            appendCell(row, station.longitude);
            appendCell(row, station.status);
            appendStationActions(row, station);
            table.appendChild(row);
        });
    } catch (error) {
        showTableMessage(table, 6, error.message);
    }
}


document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;

    const form = document.getElementById("stationForm");
    const message = document.getElementById("formMessage");

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        setMessage(message, "Saving...");
        const editId = document.getElementById("stationEditId").value;
        const station = {
            station_name: document.getElementById("stationName").value.trim(),
            latitude: Number(document.getElementById("latitude").value),
            longitude: Number(document.getElementById("longitude").value),
            status: document.getElementById("status").value
        };

        try {
            const response = await apiFetch(editId ? `/stations/${editId}` : "/stations", {
                method: editId ? "PUT" : "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(station)
            });
            if (!response.ok) {
                throw new Error(await getErrorMessage(response, "Unable to save station"));
            }
            resetStationForm();
            setMessage(message, editId ? "Station updated." : "Station added.", "success");
            await loadStations();
        } catch (error) {
            setMessage(message, error.message, "error");
        }
    });

    document.getElementById("cancelStationEdit").addEventListener("click", resetStationForm);
    document.getElementById("refreshStations").addEventListener("click", loadStations);
    loadStations();
});
