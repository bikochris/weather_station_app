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
    document.getElementById("stationCode").value = station.station_code;
    document.getElementById("stationName").value = station.station_name;
    document.getElementById("latitude").value = station.latitude;
    document.getElementById("longitude").value = station.longitude;
    document.getElementById("altitude").value = station.altitude ?? "";
    document.getElementById("province").value = station.province || "";
    document.getElementById("district").value = station.district || "";
    document.getElementById("sector").value = station.sector || "";
    document.getElementById("stationCategory").value = station.station_category || "";
    document.getElementById("status").value = station.status;
    document.getElementById("stationComment").value = station.comment || "";
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
    const columnCount = isITUser() ? 13 : 12;
    showTableMessage(table, columnCount, "Loading stations...");

    try {
        const response = await apiFetch("/stations");
        if (!response.ok) {
            throw new Error(await getErrorMessage(response, "Unable to load stations"));
        }
        stationRecords = await response.json();
        table.replaceChildren();

        const categoryFilter = document.getElementById("stationCategoryFilter").value;
        const visibleStations = categoryFilter
            ? stationRecords.filter(
                (station) => station.station_category === categoryFilter
            )
            : stationRecords;

        if (!visibleStations.length) {
            showTableMessage(
                table,
                columnCount,
                categoryFilter
                    ? "No stations found in this category."
                    : "No stations found."
            );
            return;
        }

        visibleStations.forEach((station) => {
            const row = document.createElement("tr");
            appendCell(row, station.station_code);
            appendCell(row, station.station_name);
            appendCell(row, station.latitude);
            appendCell(row, station.longitude);
            appendCell(row, station.altitude);
            appendCell(row, station.province);
            appendCell(row, station.district);
            appendCell(row, station.sector);
            appendCell(row, station.station_category || "Not classified");
            appendCell(row, station.status);
            appendCell(row, station.comment);
            appendCell(row, station.recorded_by || "Legacy record");
            if (isITUser()) appendStationActions(row, station);
            table.appendChild(row);
        });
    } catch (error) {
        showTableMessage(table, columnCount, error.message);
    }
}


function downloadStationTemplate() {
    const header = [
        "station_id",
        "station_name",
        "latitude",
        "longitude",
        "altitude",
        "province",
        "district",
        "sector",
        "station_category",
        "operational_status",
        "comment"
    ];
    const example = [
        "ST-001",
        "Central Station",
        "-1.9441",
        "30.0619",
        "1490",
        "Kigali City",
        "Nyarugenge",
        "Nyarugenge",
        "Automatic Weather stations",
        "Operational",
        "Primary station"
    ];
    const blob = new Blob(
        [`${header.join(",")}\r\n${example.join(",")}\r\n`],
        {type: "text/csv;charset=utf-8"}
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "station_import_template.csv";
    link.click();
    URL.revokeObjectURL(url);
}


async function uploadStations() {
    const input = document.getElementById("stationCsvFile");
    const message = document.getElementById("importMessage");
    const file = input.files[0];
    if (!file) {
        setMessage(message, "Select a CSV file.", "error");
        return;
    }

    setMessage(message, "Uploading stations...");
    try {
        const response = await apiFetch("/stations/import", {
            method: "POST",
            headers: {"Content-Type": "text/csv; charset=utf-8"},
            body: await file.text()
        });
        if (!response.ok) {
            throw new Error(await getErrorMessage(response, "Unable to import stations"));
        }
        const result = await response.json();
        input.value = "";
        setMessage(message, `${result.imported} station(s) imported.`, "success");
        await loadStations();
    } catch (error) {
        setMessage(message, error.message, "error");
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
            station_code: document.getElementById("stationCode").value.trim(),
            station_name: document.getElementById("stationName").value.trim(),
            latitude: Number(document.getElementById("latitude").value),
            longitude: Number(document.getElementById("longitude").value),
            altitude: Number(document.getElementById("altitude").value),
            province: document.getElementById("province").value.trim(),
            district: document.getElementById("district").value.trim(),
            sector: document.getElementById("sector").value.trim(),
            station_category: document.getElementById("stationCategory").value,
            status: document.getElementById("status").value,
            comment: document.getElementById("stationComment").value.trim() || null
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
    document.getElementById("stationCategoryFilter").addEventListener(
        "change",
        loadStations
    );
    document.getElementById("downloadStationTemplate").addEventListener(
        "click",
        downloadStationTemplate
    );
    document.getElementById("uploadStations").addEventListener("click", uploadStations);
    loadStations();
});
