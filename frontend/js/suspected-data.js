let suspectedDataRecords = [];


async function loadSuspectedDataStationOptions() {
    const response = await apiFetch("/stations");
    if (!response.ok) {
        throw new Error(await getErrorMessage(response, "Unable to load stations"));
    }
    const stations = await response.json();
    const formSelect = document.getElementById("stationId");
    const resolutionSelect = document.getElementById("resolutionStationId");
    const filterSelect = document.getElementById("stationFilter");
    formSelect.replaceChildren(new Option(
        stations.length ? "Select a station" : "No stations available",
        ""
    ));
    resolutionSelect.replaceChildren(new Option("Select a station", ""));
    filterSelect.replaceChildren(new Option("All stations", ""));
    stations.forEach((station) => {
        const label = `${station.station_code} - ${station.station_name}`;
        formSelect.appendChild(new Option(label, station.station_id));
        resolutionSelect.appendChild(new Option(label, station.station_id));
        filterSelect.appendChild(new Option(label, station.station_id));
    });
}


function closeResolutionForm() {
    document.getElementById("resolutionForm").reset();
    document.getElementById("resolutionId").value = "";
    document.getElementById("resolutionSection").hidden = true;
    setMessage(document.getElementById("resolutionMessage"), "");
}


function closeFinalReviewForm() {
    document.getElementById("finalReviewForm").reset();
    document.getElementById("finalReviewRecordId").value = "";
    document.getElementById("finalReviewSection").hidden = true;
    setMessage(document.getElementById("finalReviewMessage"), "");
}


function reviewFinalResult(recordId) {
    const record = suspectedDataRecords.find(
        (item) => item.suspected_data_id === recordId
    );
    if (!record || record.status !== "Resolved") return;
    document.getElementById("finalReviewRecordId").value = record.suspected_data_id;
    document.getElementById("finalReviewRecord").value =
        `${record.station_code} - ${record.station_name}: ${record.issue}`;
    document.getElementById("finalReviewSolved").value =
        record.final_is_solved === 0 || record.final_is_solved === false
            ? "false"
            : "true";
    document.getElementById("finalReviewComment").value = record.final_comment || "";
    const section = document.getElementById("finalReviewSection");
    section.hidden = false;
    section.scrollIntoView({behavior: "smooth"});
}


function reviewSuspectedData(recordId) {
    const record = suspectedDataRecords.find(
        (item) => item.suspected_data_id === recordId
    );
    if (!record) return;
    document.getElementById("resolutionId").value = record.suspected_data_id;
    document.getElementById("resolutionStationId").value = record.station_id;
    document.getElementById("resolutionIssue").value = record.issue;
    document.getElementById("resolutionDescription").value = record.description;
    document.getElementById("maintenanceDate").value =
        record.maintenance_date || new Date().toISOString().slice(0, 10);
    document.getElementById("maintenanceIssue").value = record.maintenance_issue || "";
    document.getElementById("howSolved").value = record.how_solved || "";
    document.getElementById("resolutionStatus").value = record.status;
    const section = document.getElementById("resolutionSection");
    section.hidden = false;
    section.scrollIntoView({behavior: "smooth"});
}


async function deleteSuspectedData(recordId) {
    if (!window.confirm("Delete this suspected data record?")) return;
    const response = await apiFetch(`/suspected-data/${recordId}`, {method: "DELETE"});
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to delete record"));
        return;
    }
    closeResolutionForm();
    await loadSuspectedDataRecords();
}


function appendSuspectedDataActions(row, record) {
    const cell = appendCell(row, "");
    const group = document.createElement("div");
    group.className = "action-group";
    if (isMaintenanceUser() || isITUser()) {
        const reviewButton = document.createElement("button");
        reviewButton.type = "button";
        reviewButton.className = "secondary-button";
        reviewButton.textContent = "Resolve";
        reviewButton.addEventListener("click", () => {
            reviewSuspectedData(record.suspected_data_id);
        });
        group.appendChild(reviewButton);
    }
    if ((isDataUser() || isITUser()) && record.status === "Resolved") {
        const finalReviewButton = document.createElement("button");
        finalReviewButton.type = "button";
        finalReviewButton.className = "secondary-button";
        finalReviewButton.textContent = record.final_comment
            ? "Edit final review"
            : "Final review";
        finalReviewButton.addEventListener("click", () => {
            reviewFinalResult(record.suspected_data_id);
        });
        group.appendChild(finalReviewButton);
    }
    if (isITUser()) {
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "danger-button";
        deleteButton.textContent = "Delete";
        deleteButton.addEventListener("click", () => {
            deleteSuspectedData(record.suspected_data_id);
        });
        group.appendChild(deleteButton);
    }
    cell.appendChild(group);
}


function formatTimestamp(value) {
    if (!value) return "-";
    const timestamp = new Date(value);
    return Number.isNaN(timestamp.getTime()) ? value : timestamp.toLocaleString();
}


function appendStatusCell(row, value) {
    const cell = appendCell(row, "");
    const status = document.createElement("span");
    status.className = `status ${value.toLowerCase().replaceAll(" ", "-")}`;
    status.textContent = value;
    cell.appendChild(status);
}


async function loadSuspectedDataRecords() {
    const table = document.getElementById("suspectedDataTable");
    const stationId = document.getElementById("stationFilter").value;
    const status = document.getElementById("statusFilter").value;
    const parameters = new URLSearchParams();
    if (stationId) parameters.set("station_id", stationId);
    if (status) parameters.set("status", status);
    const endpoint = `/suspected-data${parameters.size ? `?${parameters}` : ""}`;
    const columnCount = 16;
    showTableMessage(table, columnCount, "Loading suspected data records...");
    try {
        const response = await apiFetch(endpoint);
        if (!response.ok) {
            throw new Error(await getErrorMessage(
                response,
                "Unable to load suspected data records"
            ));
        }
        suspectedDataRecords = await response.json();
        table.replaceChildren();
        if (!suspectedDataRecords.length) {
            showTableMessage(table, columnCount, "No suspected data records found.");
            return;
        }
        suspectedDataRecords.forEach((record) => {
            const row = document.createElement("tr");
            appendCell(row, `${record.station_code} - ${record.station_name}`);
            appendCell(row, record.issue);
            appendCell(row, record.description);
            appendCell(row, record.reported_by || "Legacy record");
            appendCell(row, formatTimestamp(record.reported_at));
            appendCell(row, record.maintenance_date || "-");
            appendCell(row, record.maintenance_issue || "-");
            appendCell(row, record.how_solved || "-");
            appendCell(row, record.resolved_by || "-");
            appendCell(row, formatTimestamp(record.resolved_at));
            appendStatusCell(row, record.status);
            appendCell(
                row,
                record.final_is_solved === null
                    ? "Pending"
                    : (record.final_is_solved ? "Yes" : "No")
            );
            appendCell(row, record.final_comment || "-");
            appendCell(row, record.final_reviewed_by || "-");
            appendCell(row, formatTimestamp(record.final_reviewed_at));
            appendSuspectedDataActions(row, record);
            table.appendChild(row);
        });
    } catch (error) {
        showTableMessage(table, columnCount, error.message);
    }
}


document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    const reportForm = document.getElementById("suspectedDataForm");
    const resolutionForm = document.getElementById("resolutionForm");
    const reportMessage = document.getElementById("reportMessage");
    const resolutionMessage = document.getElementById("resolutionMessage");
    const finalReviewMessage = document.getElementById("finalReviewMessage");
    document.getElementById("reportSection").hidden = !(isDataUser() || isITUser());

    try {
        await loadSuspectedDataStationOptions();
        await loadSuspectedDataRecords();
    } catch (error) {
        setMessage(reportMessage, error.message, "error");
    }

    reportForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        setMessage(reportMessage, "Saving...");
        try {
            const response = await apiFetch("/suspected-data", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    station_id: Number(document.getElementById("stationId").value),
                    issue: document.getElementById("issue").value.trim(),
                    description: document.getElementById("description").value.trim()
                })
            });
            if (!response.ok) {
                throw new Error(await getErrorMessage(response, "Unable to save report"));
            }
            reportForm.reset();
            setMessage(reportMessage, "Suspected data report saved.", "success");
            await loadSuspectedDataRecords();
        } catch (error) {
            setMessage(reportMessage, error.message, "error");
        }
    });

    resolutionForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const recordId = document.getElementById("resolutionId").value;
        setMessage(resolutionMessage, "Saving...");
        try {
            const response = await apiFetch(`/suspected-data/${recordId}`, {
                method: "PUT",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    station_id: Number(document.getElementById("resolutionStationId").value),
                    issue: document.getElementById("resolutionIssue").value.trim(),
                    description: document.getElementById("resolutionDescription").value.trim(),
                    maintenance_date: document.getElementById("maintenanceDate").value || null,
                    maintenance_issue: document.getElementById("maintenanceIssue").value.trim() || null,
                    how_solved: document.getElementById("howSolved").value.trim() || null,
                    status: document.getElementById("resolutionStatus").value
                })
            });
            if (!response.ok) {
                throw new Error(await getErrorMessage(response, "Unable to save resolution"));
            }
            closeResolutionForm();
            await loadSuspectedDataRecords();
        } catch (error) {
            setMessage(resolutionMessage, error.message, "error");
        }
    });

    document.getElementById("finalReviewForm").addEventListener(
        "submit",
        async (event) => {
            event.preventDefault();
            const recordId = document.getElementById("finalReviewRecordId").value;
            setMessage(finalReviewMessage, "Saving...");
            try {
                const response = await apiFetch(
                    `/suspected-data/${recordId}/final-review`,
                    {
                        method: "PUT",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({
                            issue_solved:
                                document.getElementById("finalReviewSolved").value === "true",
                            comment: document.getElementById("finalReviewComment").value.trim()
                        })
                    }
                );
                if (!response.ok) {
                    throw new Error(await getErrorMessage(
                        response,
                        "Unable to save final Data review"
                    ));
                }
                closeFinalReviewForm();
                await loadSuspectedDataRecords();
            } catch (error) {
                setMessage(finalReviewMessage, error.message, "error");
            }
        }
    );

    document.getElementById("cancelResolution").addEventListener("click", closeResolutionForm);
    document.getElementById("cancelFinalReview").addEventListener(
        "click",
        closeFinalReviewForm
    );
    document.getElementById("refreshSuspectedData").addEventListener(
        "click",
        loadSuspectedDataRecords
    );
    document.getElementById("stationFilter").addEventListener(
        "change",
        loadSuspectedDataRecords
    );
    document.getElementById("statusFilter").addEventListener(
        "change",
        loadSuspectedDataRecords
    );
});
