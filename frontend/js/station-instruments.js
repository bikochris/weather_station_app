let stationInstrumentRecords = [];
let stationInstrumentStations = [];
let stationInstrumentCatalog = [];
const stationInstrumentCollection = createCollectionState("station", "asc");
let stationInstrumentPreviewedFile = null;


function selectedInstrumentIsSensing() {
    const instrumentId = Number(document.getElementById("instrumentId").value);
    return stationInstrumentCatalog.some((instrument) =>
        instrument.instrument_id === instrumentId &&
        (instrument.category || "").trim().toLowerCase() === "sensing"
    );
}


function updateSensingConnectionFields() {
    const sensing = selectedInstrumentIsSensing();
    document.getElementById("sensingConnectionFields").hidden = !sensing;
    if (!sensing) {
        document.getElementById("dataLoggerPorts").value = "";
        document.getElementById("stationInstrumentAlgorithm").value = "";
        document.querySelectorAll(".wire-color").forEach((input) => { input.value = ""; });
    }
}


function renderWiringEditor() {
    const tbody = document.getElementById("wiringEditorRows");
    for (let number = 1; number <= 10; number += 1) {
        const row = document.createElement("tr");
        appendCell(row, String(number));
        const cell = document.createElement("td");
        const input = document.createElement("input");
        input.className = "wire-color";
        input.type = "text";
        input.maxLength = 50;
        input.setAttribute("aria-label", `Wire ${number} color`);
        cell.appendChild(input);
        row.appendChild(cell);
        tbody.appendChild(row);
    }
}


function appendWiringCell(row, colors) {
    const cell = appendCell(row, "");
    const wired = (colors || []).map((color, index) => ({number: index + 1, color})).filter(({color}) => color);
    if (!wired.length) {
        cell.textContent = "-";
        return;
    }
    const details = document.createElement("details");
    details.className = "wiring-detail";
    const summary = document.createElement("summary");
    summary.textContent = `${wired.length} wire${wired.length === 1 ? "" : "s"}`;
    details.appendChild(summary);
    const table = document.createElement("table");
    const body = document.createElement("tbody");
    wired.forEach(({number, color}) => {
        const wireRow = document.createElement("tr");
        appendCell(wireRow, String(number));
        appendCell(wireRow, color);
        body.appendChild(wireRow);
    });
    table.appendChild(body);
    details.appendChild(table);
    cell.appendChild(details);
}


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
    const districtFilter = document.getElementById("districtFilter");
    districtFilter.replaceChildren(new Option("All districts", ""));
    [...new Set(stationInstrumentStations.map((station) => station.district).filter(Boolean))]
        .sort().forEach((district) => districtFilter.appendChild(new Option(district, district)));
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
    updateSensingConnectionFields();
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
    updateSensingConnectionFields();
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
    document.getElementById("calibrationDate").value = record.calibration_date || "";
    document.getElementById("replacementDate").value = record.replacement_date || "";
    document.getElementById("recommendedCalibrationDate").value = record.recommended_calibration_date || "";
    document.getElementById("recommendedReplacementDate").value = record.recommended_replacement_date || "";
    document.getElementById("stationInstrumentStatus").value = record.status;
    document.getElementById("stationInstrumentComment").value = record.comment || "";
    document.getElementById("dataLoggerPorts").value = record.data_logger_ports || "";
    document.getElementById("stationInstrumentAlgorithm").value = record.algorithm || "";
    document.querySelectorAll(".wire-color").forEach((input, index) => {
        input.value = (record.wiring_colors || [])[index] || "";
    });
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
    const columnCount = canManage ? 22 : 21;
    const parameters = collectionParameters(stationInstrumentCollection, {
        station_id: stationId,
        district: document.getElementById("districtFilter").value,
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
            appendCell(row, record.calibration_date || "-");
            appendCell(row, record.replacement_date || "-");
            appendCell(row, record.recommended_calibration_date || "-");
            appendCell(row, record.recommended_replacement_date || "-");
            appendCell(row, record.status);
            appendCell(row, record.comment || "-");
            appendCell(row, record.data_logger_ports || "-");
            appendCell(row, record.algorithm || "-");
            appendWiringCell(row, record.wiring_colors);
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
        attachRecordHistoryRows(table, "station_instruments", stationInstrumentRecords,
            record => record.station_instrument_id);
    } catch (error) {
        showTableMessage(table, columnCount, error.message);
    }
}


document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    const canManage = isITUser() || isMaintenanceUser();
    document.getElementById("stationInstrumentEditor").hidden = !canManage;
    document.getElementById("stationInstrumentImport").hidden = !canManage;
    document.getElementById("stationInstrumentActionsHeading").hidden = !canManage;
    const form = document.getElementById("stationInstrumentForm");
    const message = document.getElementById("stationInstrumentMessage");
    renderWiringEditor();
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
                        calibration_date:
                            document.getElementById("calibrationDate").value || null,
                        replacement_date:
                            document.getElementById("replacementDate").value || null,
                        recommended_calibration_date:
                            document.getElementById("recommendedCalibrationDate").value || null,
                        recommended_replacement_date:
                            document.getElementById("recommendedReplacementDate").value || null,
                        status: document.getElementById("stationInstrumentStatus").value,
                        comment:
                            document.getElementById("stationInstrumentComment").value.trim() || null,
                        data_logger_ports: selectedInstrumentIsSensing()
                            ? document.getElementById("dataLoggerPorts").value.trim() || null : null,
                        algorithm: selectedInstrumentIsSensing()
                            ? document.getElementById("stationInstrumentAlgorithm").value.trim() || null : null,
                        wiring_colors: selectedInstrumentIsSensing()
                            ? [...document.querySelectorAll(".wire-color")].map((input) => input.value.trim() || null) : []
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
    document.getElementById("districtFilter").addEventListener("change", () => {
        stationInstrumentCollection.page = 1;
        loadStationInstruments();
    });
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
            district: document.getElementById("districtFilter").value,
            date_from: document.getElementById("installationDateFrom").value,
            date_to: document.getElementById("installationDateTo").value
        })
    });
    document.getElementById("stationId").addEventListener(
        "change",
        () => populateStationCategoryInstruments()
    );
    document.getElementById("instrumentId").addEventListener("change", updateSensingConnectionFields);
    if (canManage) bindStationInstrumentImport();
});


function bindStationInstrumentImport() {
    const fileInput = document.getElementById("stationInstrumentCsv");
    const upload = document.getElementById("stationInstrumentUpload");
    const message = document.getElementById("stationInstrumentImportMessage");
    fileInput.addEventListener("change", () => {
        stationInstrumentPreviewedFile = null;
        upload.disabled = true;
        setMessage(message, "");
    });
    document.getElementById("stationInstrumentTemplate").addEventListener("click", async () => {
        try {
            const response = await apiFetch("/station-instruments/template");
            if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to download template"));
            const url = URL.createObjectURL(await response.blob());
            const link = document.createElement("a");
            link.href = url;
            link.download = "station-instrument-template.csv";
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) {
            setMessage(message, error.message, "error");
        }
    });
    async function importCsv(mode) {
        const file = fileInput.files[0];
        if (!file) {
            setMessage(message, "Choose a CSV file first.", "error");
            return;
        }
        if (mode === "create" && stationInstrumentPreviewedFile !== file) {
            setMessage(message, "Preview this file before uploading.", "error");
            return;
        }
        setMessage(message, mode === "preview" ? "Checking CSV..." : "Uploading CSV...");
        upload.disabled = true;
        try {
            const response = await apiFetch(`/station-instruments/import?mode=${mode}`, {
                method: "POST", headers: {"Content-Type": "text/csv"}, body: await file.arrayBuffer()
            });
            if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to import CSV"));
            const result = await response.json();
            const summary = `${result.total} rows checked: ${result.new} new, ${result.skipped} already present.`;
            if (mode === "preview") {
                stationInstrumentPreviewedFile = file;
                upload.disabled = result.new === 0;
                setMessage(message, summary, "success");
            } else {
                stationInstrumentPreviewedFile = null;
                fileInput.value = "";
                setMessage(message, `${result.created} station instruments imported. ${summary}`, "success");
                await loadStationInstruments();
            }
        } catch (error) {
            stationInstrumentPreviewedFile = null;
            setMessage(message, error.message, "error");
        }
    }
    document.getElementById("stationInstrumentPreview").addEventListener("click", () => importCsv("preview"));
    upload.addEventListener("click", () => importCsv("create"));
}
