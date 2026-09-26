let stationInstrumentRecords = [];
let stationInstrumentStations = [];
let stationInstrumentCatalog = [];
const stationInstrumentCollection = createCollectionState("station", "asc");


function renderStationInstrumentStatistics(summary) {
    setMetric("stationInstrumentTotalMetric", summary.total || 0);
    setMetric("stationInstrumentStationMetric", summary.stations || 0);
    setMetric("stationInstrumentOperationalMetric", summary.operational || 0);
    setMetric("stationInstrumentAttentionMetric", summary.attention || 0);
    renderBreakdown(
        "stationInstrumentStatusBreakdown",
        Object.entries(summary.statuses || {}),
        "No instruments installed in this period"
    );
    renderBreakdown(
        "stationInstrumentCategoryBreakdown",
        Object.entries(summary.categories || {}).sort(([left], [right]) =>
            left.localeCompare(right)
        ),
        "No station categories in this period"
    );
}


function formatStationInstrumentTimestamp(value) {
    if (!value) return "-";
    const timestamp = new Date(value);
    return Number.isNaN(timestamp.getTime()) ? value : timestamp.toLocaleString();
}


async function loadStationInstrumentOptions() {
    const stationResponse = await apiFetch("/stations");
    if (!stationResponse.ok) {
        throw new Error(await getErrorMessage(stationResponse, "Unable to load stations"));
    }
    stationInstrumentStations = await stationResponse.json();
    if (canManageMaintenance()) {
        const instrumentResponse = await apiFetch("/instruments?active_only=true");
        if (!instrumentResponse.ok) {
            throw new Error(await getErrorMessage(
                instrumentResponse,
                "Unable to load instruments"
            ));
        }
        stationInstrumentCatalog = await instrumentResponse.json();
    }
    const stationSelect = document.getElementById("stationId");
    const stationFilter = document.getElementById("stationFilter");
    const instrumentSelect = document.getElementById("instrumentId");
    stationSelect.replaceChildren(new Option(
        stationInstrumentStations.length ? "Select a station" : "No stations available",
        ""
    ));
    stationFilter.replaceChildren(new Option("All stations", ""));
    stationInstrumentStations.forEach((station) => {
        const category = station.station_category || "Not classified";
        const label = `${station.station_code} - ${station.station_name} (${category})`;
        stationSelect.appendChild(new Option(label, station.station_id));
        stationFilter.appendChild(new Option(label, station.station_id));
    });
    if (canManageMaintenance()) populateStationCategoryInstruments();
}


function populateStationCategoryInstruments(selectedInstrumentId = "") {
    const stationId = Number(document.getElementById("stationId").value);
    const instrumentSelect = document.getElementById("instrumentId");
    const station = stationInstrumentStations.find(
        (item) => item.station_id === stationId
    );
    const compatible = station?.station_category
        ? stationInstrumentCatalog.filter((instrument) =>
            (instrument.station_categories || []).includes(station.station_category)
        )
        : [];
    const placeholder = !station
        ? "Select a station first"
        : !station.station_category
            ? "Classify this station first"
            : compatible.length
                ? "Select an instrument"
                : "No instruments assigned to this category";
    instrumentSelect.replaceChildren(new Option(placeholder, ""));
    compatible.forEach((instrument) => {
        instrumentSelect.appendChild(new Option(
            instrument.instrument_name,
            instrument.instrument_id
        ));
    });
    instrumentSelect.value = String(selectedInstrumentId || "");
}


function resetStationInstrumentForm() {
    document.getElementById("stationInstrumentForm").reset();
    document.getElementById("stationInstrumentEditId").value = "";
    document.getElementById("installationDate").value =
        new Date().toISOString().slice(0, 10);
    document.getElementById("stationInstrumentSubmit").textContent =
        "Add station instrument";
    document.getElementById("cancelStationInstrumentEdit").hidden = true;
    populateStationCategoryInstruments();
}


function editStationInstrument(recordId) {
    const record = stationInstrumentRecords.find(
        (item) => item.station_instrument_id === recordId
    );
    if (!record) return;
    document.getElementById("stationInstrumentEditId").value = recordId;
    document.getElementById("stationId").value = record.station_id;
    populateStationCategoryInstruments(record.instrument_id);
    document.getElementById("model").value = record.model || "";
    document.getElementById("manufacturer").value = record.manufacturer || "";
    document.getElementById("serialNumber").value = record.serial_number || "";
    document.getElementById("installationDate").value = record.installation_date;
    document.getElementById("calibrationReplacementDate").value =
        record.calibration_replacement_date;
    document.getElementById("stationInstrumentStatus").value = record.status;
    document.getElementById("stationInstrumentSubmit").textContent = "Save changes";
    document.getElementById("cancelStationInstrumentEdit").hidden = false;
    document.getElementById("stationInstrumentForm").scrollIntoView({behavior: "smooth"});
}


async function deleteStationInstrument(recordId) {
    if (!window.confirm("Delete this station instrument record?")) return;
    const response = await apiFetch(`/station-instruments/${recordId}`, {method: "DELETE"});
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to delete station instrument"));
        return;
    }
    resetStationInstrumentForm();
    await loadStationInstruments();
}


function appendStationInstrumentActions(row, record) {
    const cell = appendCell(row, "");
    const group = document.createElement("div");
    group.className = "action-group";
    const editButton = document.createElement("button");
    editButton.type = "button";
    editButton.className = "secondary-button";
    editButton.textContent = "Edit";
    editButton.addEventListener("click", () => {
        editStationInstrument(record.station_instrument_id);
    });
    group.appendChild(editButton);
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "danger-button";
    deleteButton.textContent = "Delete";
    deleteButton.addEventListener("click", () => {
        deleteStationInstrument(record.station_instrument_id);
    });
    group.appendChild(deleteButton);
    cell.appendChild(group);
}


async function loadStationInstruments() {
    const table = document.getElementById("stationInstrumentTable");
    const stationId = document.getElementById("stationFilter").value;
    const canManage = isITUser() || isMaintenanceUser();
    const columnCount = canManage ? 15 : 14;
    const parameters = collectionParameters(stationInstrumentCollection, {
        station_id: stationId,
        date_from: document.getElementById("installationDateFrom").value,
        date_to: document.getElementById("installationDateTo").value
    });
    const endpoint = `/station-instruments?${parameters}`;
    showTableMessage(table, columnCount, "Loading station instruments...");
    try {
        const response = await apiFetch(endpoint);
        if (!response.ok) {
            throw new Error(await getErrorMessage(
                response,
                "Unable to load station instruments"
            ));
        }
        const result = await response.json();
        stationInstrumentRecords = result.items;
        renderStationInstrumentStatistics(result.summary);
        renderPagination(
            "stationInstrumentPagination",
            result,
            stationInstrumentCollection,
            loadStationInstruments
        );
        table.replaceChildren();
        if (!stationInstrumentRecords.length) {
            showTableMessage(table, columnCount, "No station instruments found.");
            return;
        }
        stationInstrumentRecords.forEach((record) => {
            const row = document.createElement("tr");
            appendCell(row, `${record.station_code} - ${record.station_name}`);
            appendCell(row, record.station_category || "Not classified");
            appendCell(row, record.instrument_name);
            appendCell(row, record.parameters_taken || "-");
            appendCell(row, record.model || "-");
            appendCell(row, record.manufacturer || "-");
            appendCell(row, record.serial_number || "-");
            appendCell(row, record.installation_date);
            appendCell(row, record.calibration_replacement_date);
            appendCell(row, record.status);
            appendCell(row, record.recorded_by || "Legacy record");
            appendCell(row, formatStationInstrumentTimestamp(record.created_at));
            appendCell(row, record.updated_by || "-");
            appendCell(
                row,
                record.updated_by
                    ? formatStationInstrumentTimestamp(record.updated_at)
                    : "-"
            );
            if (canManage) appendStationInstrumentActions(row, record);
            table.appendChild(row);
        });
    } catch (error) {
        showTableMessage(table, columnCount, error.message);
    }
}


document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    const canManage = isITUser() || isMaintenanceUser();
    document.getElementById("stationInstrumentEditor").hidden = !canManage;
    document.getElementById("stationInstrumentActionsHeading").hidden = !canManage;
    const form = document.getElementById("stationInstrumentForm");
    const message = document.getElementById("stationInstrumentMessage");
    resetStationInstrumentForm();
    try {
        await loadStationInstrumentOptions();
        await loadStationInstruments();
    } catch (error) {
        setMessage(message, error.message, "error");
    }

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const editId = document.getElementById("stationInstrumentEditId").value;
        setMessage(message, "Saving...");
        try {
            const response = await apiFetch(
                editId ? `/station-instruments/${editId}` : "/station-instruments",
                {
                    method: editId ? "PUT" : "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        station_id: Number(document.getElementById("stationId").value),
                        instrument_id: Number(document.getElementById("instrumentId").value),
                        model: document.getElementById("model").value.trim() || null,
                        manufacturer:
                            document.getElementById("manufacturer").value.trim() || null,
                        serial_number:
                            document.getElementById("serialNumber").value.trim() || null,
                        installation_date: document.getElementById("installationDate").value,
                        calibration_replacement_date:
                            document.getElementById("calibrationReplacementDate").value,
                        status: document.getElementById("stationInstrumentStatus").value
                    })
                }
            );
            if (!response.ok) {
                throw new Error(await getErrorMessage(
                    response,
                    "Unable to save station instrument"
                ));
            }
            resetStationInstrumentForm();
            setMessage(
                message,
                editId ? "Station instrument updated." : "Station instrument added.",
                "success"
            );
            await loadStationInstruments();
        } catch (error) {
            setMessage(message, error.message, "error");
        }
    });

    document.getElementById("cancelStationInstrumentEdit").addEventListener(
        "click",
        resetStationInstrumentForm
    );
    document.getElementById("refreshStationInstruments").addEventListener(
        "click",
        loadStationInstruments
    );
    document.getElementById("stationFilter").addEventListener(
        "change",
        () => {
            stationInstrumentCollection.page = 1;
            loadStationInstruments();
        }
    );
    document.getElementById("installationDateFrom").addEventListener(
        "change",
        () => {
            stationInstrumentCollection.page = 1;
            loadStationInstruments();
        }
    );
    document.getElementById("installationDateTo").addEventListener(
        "change",
        () => {
            stationInstrumentCollection.page = 1;
            loadStationInstruments();
        }
    );
    bindCollectionControls({
        state: stationInstrumentCollection,
        reload: loadStationInstruments,
        searchId: "stationInstrumentSearch",
        pageSizeId: "stationInstrumentPageSize",
        tableSelector: ".station-instrument-table",
        exportBasePath: "/station-instruments",
        csvButtonId: "stationInstrumentExportCsv",
        pdfButtonId: "stationInstrumentExportPdf",
        getExtraParameters: () => ({
            station_id: document.getElementById("stationFilter").value,
            date_from: document.getElementById("installationDateFrom").value,
            date_to: document.getElementById("installationDateTo").value
        })
    });
    document.getElementById("stationId").addEventListener(
        "change",
        () => populateStationCategoryInstruments()
    );
});
