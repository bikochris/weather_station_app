let instrumentRecords = [];


function resetInstrumentForm() {
    document.getElementById("instrumentForm").reset();
    document.getElementById("instrumentEditId").value = "";
    document.getElementById("isActive").checked = true;
    document.getElementById("instrumentSubmit").textContent = "Add instrument";
    document.getElementById("cancelInstrumentEdit").hidden = true;
}


function selectedStationCategories() {
    return Array.from(
        document.querySelectorAll('input[name="stationCategories"]:checked')
    ).map((checkbox) => checkbox.value);
}


function editInstrument(instrumentId) {
    const instrument = instrumentRecords.find((item) => item.instrument_id === instrumentId);
    if (!instrument) return;
    document.getElementById("instrumentEditId").value = instrument.instrument_id;
    document.getElementById("instrumentName").value = instrument.instrument_name;
    document.getElementById("parametersTaken").value = instrument.parameters_taken || "";
    document.getElementById("category").value = instrument.category || "";
    const categories = new Set(instrument.station_categories || []);
    document.querySelectorAll('input[name="stationCategories"]').forEach((checkbox) => {
        checkbox.checked = categories.has(checkbox.value);
    });
    document.getElementById("description").value = instrument.description || "";
    document.getElementById("isActive").checked = Boolean(instrument.is_active);
    document.getElementById("instrumentSubmit").textContent = "Save changes";
    document.getElementById("cancelInstrumentEdit").hidden = false;
    document.getElementById("instrumentForm").scrollIntoView({behavior: "smooth"});
}


async function deleteInstrument(instrumentId) {
    const instrument = instrumentRecords.find((item) => item.instrument_id === instrumentId);
    if (!instrument || !window.confirm(`Delete instrument "${instrument.instrument_name}"?`)) return;
    const response = await apiFetch(`/instruments/${instrumentId}`, {method: "DELETE"});
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to delete instrument"));
        return;
    }
    await loadInstruments();
}


function appendInstrumentActions(row, instrument) {
    const cell = appendCell(row, "");
    const group = document.createElement("div");
    group.className = "action-group";
    const editButton = document.createElement("button");
    editButton.type = "button";
    editButton.className = "secondary-button";
    editButton.textContent = "Edit";
    editButton.addEventListener("click", () => editInstrument(instrument.instrument_id));
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "danger-button";
    deleteButton.textContent = "Delete";
    deleteButton.addEventListener("click", () => deleteInstrument(instrument.instrument_id));
    group.append(editButton, deleteButton);
    cell.appendChild(group);
}


async function loadInstruments() {
    const table = document.getElementById("instrumentTable");
    const columnCount = isITUser() ? 9 : 8;
    showTableMessage(table, columnCount, "Loading instruments...");
    try {
        const response = await apiFetch("/instruments");
        if (!response.ok) {
            throw new Error(await getErrorMessage(response, "Unable to load instruments"));
        }
        instrumentRecords = await response.json();
        table.replaceChildren();
        if (!instrumentRecords.length) {
            showTableMessage(table, columnCount, "No instruments found.");
            return;
        }
        instrumentRecords.forEach((instrument) => {
            const row = document.createElement("tr");
            appendCell(row, instrument.instrument_id);
            appendCell(row, instrument.instrument_name);
            appendCell(row, instrument.parameters_taken || "Not specified");
            appendCell(row, instrument.category || "Not categorized");
            appendCell(
                row,
                instrument.station_categories?.join(", ") || "Not assigned"
            );
            appendCell(row, instrument.description || "");
            const statusCell = appendCell(row, "");
            const status = document.createElement("span");
            status.className = instrument.is_active ? "status active" : "status";
            status.textContent = instrument.is_active ? "Active" : "Inactive";
            statusCell.appendChild(status);
            appendCell(row, instrument.recorded_by || "Legacy record");
            if (isITUser()) appendInstrumentActions(row, instrument);
            table.appendChild(row);
        });
    } catch (error) {
        showTableMessage(table, columnCount, error.message);
    }
}


function downloadInstrumentTemplate() {
    const csv = [
        "instrument_name,parameters_it_takes,category,station_categories,description",
        'Temperature Sensor,"Air temperature",Temperature,"Automatic Weather stations|Principal stations","Measures air temperature"'
    ].join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"}));
    const link = document.createElement("a");
    link.href = url;
    link.download = "instrument_import_template.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
}


async function uploadInstruments() {
    const input = document.getElementById("instrumentCsvFile");
    const message = document.getElementById("importMessage");
    const file = input.files[0];
    if (!file) {
        setMessage(message, "Choose a CSV file first.", "error");
        return;
    }
    setMessage(message, "Uploading...");
    try {
        const response = await apiFetch("/instruments/import", {
            method: "POST",
            headers: {"Content-Type": "text/csv;charset=utf-8"},
            body: await file.text()
        });
        if (!response.ok) {
            throw new Error(await getErrorMessage(response, "Unable to import instruments"));
        }
        const result = await response.json();
        input.value = "";
        setMessage(
            message,
            `${result.imported} instrument${result.imported === 1 ? "" : "s"} imported.`,
            "success"
        );
        await loadInstruments();
    } catch (error) {
        setMessage(message, error.message, "error");
    }
}


document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    const form = document.getElementById("instrumentForm");
    const message = document.getElementById("formMessage");

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        setMessage(message, "Saving...");
        const editId = document.getElementById("instrumentEditId").value;
        const stationCategories = selectedStationCategories();
        if (!stationCategories.length) {
            setMessage(message, "Select at least one station category.", "error");
            return;
        }
        const instrument = {
            instrument_name: document.getElementById("instrumentName").value.trim(),
            parameters_taken: document.getElementById("parametersTaken").value.trim(),
            category: document.getElementById("category").value.trim(),
            station_categories: stationCategories,
            description: document.getElementById("description").value.trim() || null,
            is_active: document.getElementById("isActive").checked
        };
        try {
            const response = await apiFetch(
                editId ? `/instruments/${editId}` : "/instruments",
                {
                    method: editId ? "PUT" : "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(instrument)
                }
            );
            if (!response.ok) {
                throw new Error(await getErrorMessage(response, "Unable to save instrument"));
            }
            resetInstrumentForm();
            setMessage(message, editId ? "Instrument updated." : "Instrument added.", "success");
            await loadInstruments();
        } catch (error) {
            setMessage(message, error.message, "error");
        }
    });

    document.getElementById("cancelInstrumentEdit").addEventListener("click", resetInstrumentForm);
    document.getElementById("refreshInstruments").addEventListener("click", loadInstruments);
    document.getElementById("downloadInstrumentTemplate").addEventListener(
        "click",
        downloadInstrumentTemplate
    );
    document.getElementById("uploadInstruments").addEventListener("click", uploadInstruments);
    loadInstruments();
});
