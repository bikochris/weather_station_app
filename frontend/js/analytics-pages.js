const analyticsPage = location.pathname.split("/").pop();
let analyticsStations = [];
let analyticsRows = [];
let analyticsCategories = [];
let editingDataCount = null;

function metric(id, value) {
    document.getElementById(id).textContent = Number(value || 0).toLocaleString();
}

function cell(row, value) {
    const td = document.createElement("td");
    td.textContent = value == null || value === "" ? "-" : String(value);
    row.appendChild(td);
    return td;
}

function fillDistricts(id) {
    const select = document.getElementById(id);
    select.replaceChildren(new Option("All districts", ""));
    [...new Set(analyticsStations.map(station => station.district).filter(Boolean))]
        .sort().forEach(district => select.appendChild(new Option(district, district)));
}

function fillStations(id, district = "", allLabel = "All stations") {
    const select = document.getElementById(id);
    const previous = select.value;
    select.replaceChildren(new Option(allLabel, ""));
    analyticsStations.filter(station => !district || station.district === district)
        .forEach(station => select.appendChild(new Option(
            `${station.station_code} - ${station.station_name}`, station.station_id
        )));
    if ([...select.options].some(option => option.value === previous)) select.value = previous;
}

function requestParameters(extra = {}) {
    const values = {
        district: document.getElementById("filterDistrict").value,
        station_id: document.getElementById("filterStation").value,
        ...extra
    };
    return new URLSearchParams(Object.entries(values).filter(([, value]) => value));
}

async function jsonRequest(path, options) {
    const response = await apiFetch(path, options);
    if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load records"));
    return response.status === 204 ? null : response.json();
}

function showError(bodyId, columns, error) {
    const body = document.getElementById(bodyId);
    body.replaceChildren();
    const row = body.insertRow();
    cell(row, error.message).colSpan = columns;
}

function formatDataMonth(month) {
    const [year, number] = month.split("-").map(Number);
    return new Intl.DateTimeFormat(undefined, {month: "short", year: "numeric"}).format(new Date(year, number - 1, 1));
}

function formatFiscalQuarter(period) {
    const quarterMonths = {Q1: "Jul-Sep", Q2: "Oct-Dec", Q3: "Jan-Mar", Q4: "Apr-Jun"};
    const [year, quarter] = period.split(" ");
    return `FY ${year} ${quarter} (${quarterMonths[quarter]})`;
}

function renderDataCountChart(id, values, emptyMessage, labelFor = value => value) {
    const chart = document.getElementById(id);
    chart.replaceChildren();
    const max = Math.max(1, ...Object.values(values));
    Object.entries(values).sort((a, b) => a[0].localeCompare(b[0]))
        .forEach(([name, value]) => {
            const row = document.createElement("div"); row.className = "horizontal-bar-row";
            const label = document.createElement("span"); label.textContent = labelFor(name);
            const track = document.createElement("div"); track.className = "horizontal-bar-track";
            const bar = document.createElement("div"); bar.className = "horizontal-bar-fill";
            bar.style.width = `${value * 100 / max}%`; track.append(bar);
            const count = document.createElement("strong"); count.textContent = value.toLocaleString();
            row.append(label, track, count); chart.append(row);
        });
    if (!chart.childElementCount) chart.textContent = emptyMessage;
}

function renderDataPeriods(id, values, labelFor = value => value) {
    const container = document.getElementById(id);
    container.replaceChildren();
    Object.entries(values).sort((a, b) => b[0].localeCompare(a[0])).forEach(([period, count]) => {
        const row = document.createElement("div"); row.className = "data-period-row";
        const label = document.createElement("span"); label.textContent = labelFor(period);
        const total = document.createElement("strong"); total.textContent = count.toLocaleString();
        row.append(label, total); container.append(row);
    });
    if (!container.childElementCount) container.textContent = "No records";
}

function resetDataCountForm() {
    editingDataCount = null;
    document.querySelectorAll("#entryCategoryOptions input").forEach(input => { input.checked = false; });
    document.getElementById("entryCount").value = "";
    document.getElementById("entryNotes").value = "";
    document.getElementById("cancelCountEdit").hidden = true;
}

async function loadDataCounts() {
    const body = document.getElementById("dataCountRows");
    try {
        const categories = [...document.querySelectorAll("#filterCategoryOptions input:checked")].map(input => input.value);
        const result = await jsonRequest(`/data-counts?${new URLSearchParams(Object.entries({
            station_categories: categories.join("|"),
            month_from: document.getElementById("monthFrom").value,
            month_to: document.getElementById("monthTo").value
        }).filter(([, value]) => value))}`);
        analyticsRows = result.items;
        metric("metricRecords", result.summary.records);
        metric("metricCategories", result.summary.categories);
        metric("metricEntries", result.summary.entries);
        renderDataPeriods("dataMonths", result.summary.by_month, formatDataMonth);
        renderDataPeriods("dataFiscalYears", result.summary.by_fiscal_year, year => `FY ${year}`);
        renderDataPeriods("dataFiscalQuarters", result.summary.by_fiscal_quarter, formatFiscalQuarter);
        renderDataCountChart("dataCountMonthChart", result.summary.by_month, "No monthly counts in this period.", formatDataMonth);
        renderDataCountChart("dataCountQuarterChart", result.summary.by_fiscal_quarter, "No fiscal-quarter counts in this period.", formatFiscalQuarter);
        renderDataCountChart("dataCountYearChart", result.summary.by_fiscal_year, "No fiscal-year counts in this period.", year => `FY ${year}`);
        body.replaceChildren();
        if (!analyticsRows.length) cell(body.insertRow(), "No monthly counts in this selection.").colSpan = 7;
        for (const item of analyticsRows) {
            const row = body.insertRow();
            cell(row, item.record_month);
            cell(row, item.station_category);
            cell(row, item.record_count);
            cell(row, item.notes);
            cell(row, item.recorded_by_username);
            cell(row, item.updated_at);
            if (document.getElementById("countActionsHeader").hidden) continue;
            const actions = cell(row, "");
            actions.textContent = "";
            const edit = document.createElement("button");
            edit.className = "secondary-button";
            edit.textContent = "Edit";
            edit.onclick = () => {
                editingDataCount = item;
                document.querySelectorAll("#entryCategoryOptions input").forEach(input => {
                    input.checked = item.station_categories.includes(input.value);
                });
                document.getElementById("entryMonth").value = item.record_month;
                document.getElementById("entryCount").value = item.record_count;
                document.getElementById("entryNotes").value = item.notes || "";
                document.getElementById("cancelCountEdit").hidden = false;
                document.getElementById("dataCountEditor").scrollIntoView({behavior: "smooth"});
            };
            const remove = document.createElement("button");
            remove.className = "danger-button";
            remove.textContent = "Delete";
            remove.onclick = async () => {
                if (!confirm("Delete this monthly data count?")) return;
                try {
                    const path = item.entry_type === "combined" ? `/data-counts/combined/${item.data_count_id}` : `/data-counts/${item.data_count_id}`;
                    await jsonRequest(path, {method: "DELETE"});
                    if (editingDataCount?.data_count_id === item.data_count_id && editingDataCount?.entry_type === item.entry_type) resetDataCountForm();
                    await loadDataCounts();
                }
                catch (error) { alert(error.message); }
            };
            actions.append(edit, remove);
        }
        attachRecordHistoryRows(body, item => item.entry_type === "combined"
            ? "combined_data_counts" : "category_data_counts", analyticsRows,
            item => item.data_count_id);
    } catch (error) { showError("dataCountRows", 7, error); }
}

async function loadInstrumentSummary() {
    const body = document.getElementById("instrumentSummaryRows");
    try {
        const result = await jsonRequest(`/instrument-status-summary?${requestParameters({
            instrument_id: document.getElementById("filterInstrument").value,
            search: document.getElementById("instrumentSearch").value.trim()
        })}`);
        metric("metricTotal", result.summary.total);
        metric("metricOperational", result.summary.statuses.Operational);
        metric("metricCalibration", result.summary.statuses["Needs Calibration"]);
        metric("metricReplacement", result.summary.statuses["Needs Replacement"]);
        metric("metricUnderMaintenance", result.summary.statuses["Under Maintenance"]);
        metric("metricInactive", result.summary.statuses.Inactive);
        metric("metricDueCalibration", result.summary.calibration_alerts);
        metric("metricDueReplacement", result.summary.replacement_alerts);
        body.replaceChildren();
        if (!result.items.length) cell(body.insertRow(), "No instruments match these filters.").colSpan = 9;
        result.items.forEach(item => {
            const row = body.insertRow();
            [item.district, `${item.station_code} - ${item.station_name}`, item.instrument_name,
                item.effective_status, item.installation_date, item.calibration_date]
                .forEach(value => cell(row, value));
            for (const kind of ["calibration", "replacement"]) {
                const due = item[`recommended_${kind}_date`];
                const alert = item[`${kind}_alert`];
                const td = cell(row, due ? `${due} · ${alert.label}` : "-");
                if (alert.level !== "none") td.classList.add(`due-${alert.level}`);
            }
            cell(row, item.replacement_date);
        });
    } catch (error) { showError("instrumentSummaryRows", 9, error); }
}

async function loadMaintenanceSummary() {
    const body = document.getElementById("maintenanceSummaryRows");
    try {
        const result = await jsonRequest(`/maintenance-summary?${maintenanceSummaryParameters()}`);
        metric("metricTotal", result.summary.total);
        metric("metricMaintained", result.summary.maintained);
        const maintainedRate = result.summary.total
            ? (result.summary.maintained / result.summary.total) * 100
            : 0;
        document.getElementById("metricMaintainedRate").textContent = `${maintainedRate.toFixed(1)}% of matching stations`;
        metric("metricNotMaintained", result.summary.not_maintained);
        metric("metricPending", result.summary.pending);
        metric("metricUnconfigured", result.summary.unconfigured);
        body.replaceChildren();
        if (!result.items.length) cell(body.insertRow(), "No stations match these filters.").colSpan = 13;
        result.items.forEach(item => {
            const row = body.insertRow();
            const level = {"Maintained": "good", "Pending": "pending", "Not maintained": "missed", "Not configured": "unconfigured"}[item.status];
            row.classList.add(`maintenance-row-${level}`);
            [item.district, `${item.station_code} - ${item.station_name}`, item.station_category]
                .forEach(value => cell(row, value));
            const statusCell = cell(row, ""); statusCell.textContent = "";
            statusCell.classList.add("maintenance-status-cell");
            const badge = document.createElement("span"); badge.className = `maintenance-status maintenance-status-${level}`;
            badge.textContent = item.status;
            statusCell.append(badge);
            if (item.progress_percent != null) {
                const progress = document.createElement("div");
                progress.className = "maintenance-progress";
                const value = document.createElement("strong");
                value.textContent = `${item.progress_percent}%`;
                const ratio = document.createElement("small");
                ratio.textContent = `${item.visits_toward_target} / ${item.target_for_period} target`;
                const bar = document.createElement("progress");
                bar.max = 100; bar.value = item.progress_percent;
                bar.setAttribute("aria-label", `Schedule progress: ${item.progress_percent}%`);
                progress.append(value, ratio, bar);
                statusCell.append(progress);
            } else {
                const detail = document.createElement("small"); detail.textContent = item.status_detail;
                statusCell.append(detail);
            }
            item.quarter_progress.forEach(quarter => {
                const quarterCell = cell(row, quarter.count == null ? "" : quarter.count);
                quarterCell.textContent = quarter.count == null ? "" : String(quarter.count);
                quarterCell.className = `maintenance-quarter maintenance-quarter-${quarter.state}`;
                const description = quarter.target
                    ? `${quarter.count ?? 0} of ${quarter.target} required visits`
                    : `${quarter.count ?? 0} recorded visits; annual target applies`;
                quarterCell.setAttribute("aria-label", `Q${quarter.quarter}: ${description}`);
                quarterCell.title = `Q${quarter.quarter}: ${description}`;
            });
            [item.visits_in_period, item.target_for_period, item.cadence || "-", item.last_maintenance_date]
                .forEach(value => cell(row, value));
            const datesCell = cell(row, ""); datesCell.textContent = "";
            if (!item.maintenance_dates.length) datesCell.textContent = "-";
            else if (item.maintenance_dates.length === 1) datesCell.textContent = item.maintenance_dates[0];
            else {
                const list = document.createElement("details");
                const summary = document.createElement("summary");
                summary.textContent = `${item.maintenance_dates.length} dates`;
                const dates = document.createElement("div");
                dates.className = "maintenance-date-list";
                item.maintenance_dates.forEach(value => {
                    const date = document.createElement("span"); date.textContent = value; dates.append(date);
                });
                list.append(summary, dates); datesCell.append(list);
            }
        });
    } catch (error) { showError("maintenanceSummaryRows", 13, error); }
}

function maintenanceSummaryParameters() {
    return requestParameters({
        fiscal_start_year: document.getElementById("fiscalYear").value,
        quarter: document.getElementById("quarter").value,
        station_category: document.getElementById("filterCategory").value,
        status: document.getElementById("filterScheduleStatus").value
    });
}

async function exportMaintenanceSummary(format) {
    try {
        const parameters = maintenanceSummaryParameters();
        parameters.set("format", format);
        const response = await apiFetch(`/maintenance-summary/export?${parameters}`);
        if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to export maintenance summary"));
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a");
        const fiscalYear = document.getElementById("fiscalYear").value;
        const quarter = document.getElementById("quarter").value;
        link.href = url;
        link.download = `maintenance-summary-${fiscalYear}-${Number(fiscalYear) + 1}${quarter ? `-Q${quarter}` : ""}.${format}`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) { window.alert(error.message); }
}

document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    try {
        analyticsStations = await jsonRequest("/stations");
        if (analyticsPage !== "data-counts.html") {
            fillDistricts("filterDistrict");
            fillStations("filterStation");
            document.getElementById("filterDistrict").onchange = () =>
                fillStations("filterStation", document.getElementById("filterDistrict").value);
        }
        if (analyticsPage === "data-counts.html") {
            const writer = isITUser() || DATA_OPERATIONS_ROLES.has(currentUser.department);
            document.getElementById("dataCountEditor").hidden = !writer;
            document.getElementById("countActionsHeader").hidden = !writer;
            analyticsCategories = [...new Set(analyticsStations.map(station => station.station_category).filter(Boolean))].sort();
            analyticsCategories.forEach(category => {
                const entryLabel = document.createElement("label");
                const entryInput = document.createElement("input"); entryInput.type = "checkbox"; entryInput.value = category;
                entryLabel.append(entryInput, document.createTextNode(category));
                document.getElementById("entryCategoryOptions").append(entryLabel);
                const label = document.createElement("label");
                const input = document.createElement("input"); input.type = "checkbox"; input.value = category;
                input.onchange = () => {
                    const selected = document.querySelectorAll("#filterCategoryOptions input:checked").length;
                    document.getElementById("filterCategorySummary").textContent = selected ? `${selected} categories selected` : "All categories";
                };
                label.append(input, document.createTextNode(category));
                document.getElementById("filterCategoryOptions").append(label);
            });
            document.getElementById("cancelCountEdit").onclick = resetDataCountForm;
            document.getElementById("entryMonth").value = new Date().toISOString().slice(0, 7);
            document.getElementById("dataCountForm").onsubmit = async event => {
                event.preventDefault();
                const message = document.getElementById("entryMessage");
                try {
                    const categories = [...document.querySelectorAll("#entryCategoryOptions input:checked")].map(input => input.value);
                    if (!categories.length) throw new Error("Select at least one station category.");
                    const entry = {record_month: document.getElementById("entryMonth").value,
                        record_count: Number(document.getElementById("entryCount").value),
                        notes: document.getElementById("entryNotes").value.trim() || null};
                    if (editingDataCount?.entry_type === "single" &&
                        (categories.length !== 1 || categories[0] !== editingDataCount.station_category ||
                         entry.record_month !== editingDataCount.record_month))
                        throw new Error("To change a single-category entry's month or categories, delete it and create a new count.");
                    const combined = categories.length > 1;
                    const path = combined
                        ? (editingDataCount?.entry_type === "combined" ? `/data-counts/combined/${editingDataCount.data_count_id}` : "/data-counts/combined")
                        : "/data-counts";
                    if (editingDataCount?.entry_type === "combined" && !combined)
                        throw new Error("To make this a single-category entry, delete it and create a new count.");
                    const result = await jsonRequest(path, {method: editingDataCount?.entry_type === "combined" ? "PUT" : "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify(combined ? {...entry, station_categories: categories} : {...entry, station_category: categories[0]})});
                    message.textContent = `Count saved: ${entry.record_count.toLocaleString()} record${entry.record_count === 1 ? "" : "s"} for ${categories.join(" + ")}.`;
                    resetDataCountForm();
                    await loadDataCounts();
                } catch (error) { message.textContent = error.message; }
            };
            document.getElementById("applyFilters").onclick = loadDataCounts;
            await loadDataCounts();
        } else if (analyticsPage === "instrument-status-summary.html") {
            const header = document.querySelector("thead tr");
            const replacementCell = [...header.cells].find(cell => cell.textContent === "Recommended replacement");
            const actualReplacement = document.createElement("th");
            actualReplacement.textContent = "Last replacement";
            replacementCell.after(actualReplacement);
            const metricGrid = document.querySelector(".dashboard-sidebar .metric-grid");
            [["metricDueCalibration", "Calibration alerts"], ["metricDueReplacement", "Replacement alerts"]]
                .forEach(([id, label]) => {
                    const item = document.createElement("div"); item.className = "metric-item";
                    const title = document.createElement("span"); title.textContent = label;
                    const value = document.createElement("strong"); value.id = id; value.textContent = "0";
                    item.append(title, value); metricGrid.append(item);
                });
            const searchField = document.createElement("div");
            searchField.className = "field";
            const searchLabel = document.createElement("label");
            searchLabel.htmlFor = "instrumentSearch";
            searchLabel.textContent = "Search";
            const searchInput = document.createElement("input");
            searchInput.id = "instrumentSearch";
            searchInput.type = "search";
            searchInput.placeholder = "Station, district or instrument";
            searchInput.addEventListener("keydown", event => {
                if (event.key === "Enter") loadInstrumentSummary();
            });
            searchField.append(searchLabel, searchInput);
            document.querySelector(".analytics-filter").insertBefore(searchField, document.getElementById("applyFilters"));
            const instruments = await jsonRequest("/instrument-status-summary");
            const select = document.getElementById("filterInstrument");
            select.replaceChildren(new Option("All instruments", ""));
            const available = new Map(instruments.items.map(item => [item.instrument_id, item.instrument_name]));
            [...available].sort((left, right) => left[1].localeCompare(right[1]))
                .forEach(([id, name]) => select.appendChild(new Option(name, id)));
            document.getElementById("applyFilters").onclick = loadInstrumentSummary;
            await loadInstrumentSummary();
        } else {
            const now = new Date();
            const currentFiscalYear = now.getFullYear() - (now.getMonth() < 6 ? 1 : 0);
            const yearSelect = document.getElementById("fiscalYear");
            for (let year = currentFiscalYear - 2; year <= currentFiscalYear + 2; year++)
                yearSelect.append(new Option(`${year}/${year + 1}`, year));
            yearSelect.value = currentFiscalYear;
            const categorySelect = document.getElementById("filterCategory");
            categorySelect.replaceChildren(new Option("All categories", ""));
            [...new Set(analyticsStations.map(station => station.station_category).filter(Boolean))]
                .sort().forEach(category => categorySelect.append(new Option(category, category)));
            document.getElementById("applyFilters").onclick = loadMaintenanceSummary;
            document.getElementById("exportMaintenanceCsv").onclick = () => exportMaintenanceSummary("csv");
            document.getElementById("exportMaintenancePdf").onclick = () => exportMaintenanceSummary("pdf");
            await loadMaintenanceSummary();
        }
    } catch (error) {
        const body = document.querySelector("tbody");
        if (body) { body.replaceChildren(); cell(body.insertRow(), error.message).colSpan = 8; }
    }
});
