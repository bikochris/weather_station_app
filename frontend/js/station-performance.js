const performanceState = createCollectionState("score", "desc");
let editingPerformance = null;
let performanceSequence = 0;
const performanceDraftScores = new Map();
const performanceSelected = new Set();
const performanceField = id => document.getElementById(id);

function renderPerformanceScores() {
    const container = performanceField("performanceStationScores");
    container.querySelectorAll("input[data-station-id]").forEach(input => performanceDraftScores.set(input.dataset.stationId, input.value));
    container.replaceChildren();
    if (editingPerformance) return;
    const ids = performanceField("performanceStation").value.split("|").filter(Boolean);
    for (const id of ids) {
        const row = document.createElement("div"); row.className = "performance-score-row";
        const label = document.createElement("label"); label.htmlFor = `stationScore${id}`;
        label.textContent = [...performanceField("performanceStation").options].find(option => option.value === id)?.textContent || id;
        const field = document.createElement("div"); field.className = "field";
        const caption = document.createElement("label"); caption.htmlFor = label.htmlFor; caption.textContent = "Score (%)";
        const input = document.createElement("input"); input.id = label.htmlFor; input.dataset.stationId = id;
        input.type = "number"; input.min = "0"; input.max = "100"; input.step = "0.01"; input.required = true;
        input.value = performanceDraftScores.get(id) || "";
        field.append(caption, input); row.append(label, field); container.append(row);
    }
}

async function performanceError(response, fallback) {
    if ([404, 405].includes(response.status)) return "Station Performance is not available on this running backend. Restart the backend from the updated app folder, then refresh with Ctrl+F5.";
    return getErrorMessage(response, fallback);
}

function performanceFilters() {
    return {district: performanceField("performanceDistrictFilter").value,
        station_id: performanceField("performanceStationFilter").value,
        station_category: performanceField("performanceCategoryFilter").value,
        month_from: performanceField("performanceFrom").value, month_to: performanceField("performanceTo").value};
}

function renderPerformanceChart(id, values, labels = {}, chronological = false) {
    const chart = typeof id === "string" ? performanceField(id) : id;
    chart.replaceChildren();
    Object.entries(values).sort((a, b) => chronological ? a[0].localeCompare(b[0]) : b[1] - a[1]).forEach(([key, score]) => {
        const row = document.createElement("div"); row.className = "horizontal-bar-row";
        const name = document.createElement("span"); name.textContent = labels[key] || key;
        const track = document.createElement("div"); track.className = "horizontal-bar-track";
        const bar = document.createElement("div"); bar.className = "horizontal-bar-fill";
        bar.style.width = `${score}%`; track.append(bar);
        const value = document.createElement("strong"); value.textContent = `${score.toLocaleString(undefined, {maximumFractionDigits: 2})}%`;
        row.append(name, track, value); chart.append(row);
    });
    if (!chart.childElementCount) chart.textContent = "No performance scores in this period.";
}

function renderPerformancePeriodStations(id, periods, labels, monthly = false, overall = {}) {
    const container = performanceField(id);
    container.replaceChildren();
    if (!periods) {
        container.textContent = "Restart the backend to load per-station period scores.";
        return;
    }
    for (const [period, scores] of Object.entries(periods).sort((a, b) => a[0].localeCompare(b[0]))) {
        const group = document.createElement("div"); group.className = "performance-period-group";
        const heading = document.createElement("h4");
        const periodLabel = monthly
            ? new Intl.DateTimeFormat(undefined, {month: "long", year: "numeric"}).format(new Date(Number(period.slice(0, 4)), Number(period.slice(5, 7)) - 1, 1))
            : `FY ${period}`;
        heading.textContent = periodLabel;
        const header = document.createElement("div"); header.className = "performance-period-heading";
        const total = document.createElement("strong");
        total.textContent = `Overall: ${overall[period] == null ? "-" : `${overall[period].toLocaleString(undefined, {maximumFractionDigits: 2})}%`}`;
        total.title = "Average of all recorded station-month scores in this period";
        header.append(heading, total);
        const chart = document.createElement("div"); chart.className = "horizontal-chart";
        chart.setAttribute("aria-label", `Station scores for ${periodLabel}`);
        renderPerformanceChart(chart, scores, labels);
        group.append(header, chart); container.append(group);
    }
    if (!container.childElementCount) container.textContent = "No station performance scores in this period.";
}

function renderPerformanceMonthMatrix(summary) {
    const container = performanceField("performanceMonthMatrix"); container.replaceChildren();
    const monthly = summary.station_period_averages?.months;
    if (!monthly) { container.textContent = "Restart the backend to load the monthly comparison."; return; }
    const percent = value => value == null ? "-" : `${value.toLocaleString(undefined, {maximumFractionDigits: 2})}%`;
    for (const year of Object.keys(summary.averages.years).sort()) {
        const months = Array.from({length: 12}, (_, index) => {
            const date = new Date(Number(year.slice(0, 4)), index + 6, 1);
            return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
        });
        const heading = document.createElement("h4"); heading.textContent = `FY ${year} - July to June`;
        const wrap = document.createElement("div"); wrap.className = "table-wrap performance-month-matrix";
        const table = document.createElement("table");
        const caption = document.createElement("caption"); caption.className = "performance-matrix-caption";
        caption.textContent = `Station scores and overall percentages, FY ${year}`;
        table.append(caption);
        const header = table.createTHead().insertRow();
        const headingCell = text => { const cell = document.createElement("th"); cell.scope = "col"; cell.textContent = text; header.append(cell); };
        headingCell("Station");
        months.forEach(month => headingCell(new Intl.DateTimeFormat(undefined, {month: "short"}).format(new Date(Number(month.slice(0, 4)), Number(month.slice(5)) - 1, 1))));
        headingCell("FY average");
        const body = table.createTBody();
        const stationIds = [...new Set(months.flatMap(month => Object.keys(monthly[month] || {})))];
        const fiscalScores = summary.station_period_averages.years?.[year] || {};
        stationIds.sort((a, b) => ((fiscalScores[b] ?? -Infinity) - (fiscalScores[a] ?? -Infinity))
            || (summary.station_labels[a] || a).localeCompare(summary.station_labels[b] || b));
        for (const station of stationIds) {
            const row = body.insertRow();
            const name = document.createElement("th"); name.scope = "row"; name.textContent = summary.station_labels[station] || station; row.append(name);
            months.forEach(month => appendCell(row, percent(monthly[month]?.[station])));
            appendCell(row, percent(summary.station_period_averages.years?.[year]?.[station]));
        }
        const footer = table.createTFoot().insertRow();
        const label = document.createElement("th"); label.scope = "row"; label.textContent = "Overall (%)"; footer.append(label);
        months.forEach(month => appendCell(footer, percent(summary.averages.months[month])));
        appendCell(footer, percent(summary.averages.years[year]));
        enableLocalTableSorting(table);
        // Sorting is attached here because comparison tables are created after page load.
        table.dataset.serverSorted = "true";
        header.lastElementChild.setAttribute("aria-sort", "descending");
        wrap.append(table); container.append(heading, wrap);
    }
    if (!container.childElementCount) container.textContent = "No scores match this period.";
}

function resetPerformance() {
    editingPerformance = null;
    performanceField("performanceForm").reset();
    performanceField("performanceStation").value = "";
    performanceField("performanceStationScores").replaceChildren();
    performanceDraftScores.clear();
    performanceField("performanceBatchStations").hidden = false;
    performanceField("performanceEditStationField").hidden = true;
    performanceField("performanceEditStation").required = false;
    performanceField("performanceEditStation").disabled = true;
    performanceField("performanceSingleScore").hidden = true;
    performanceField("performanceScore").required = false;
    performanceField("performanceScore").disabled = true;
    renderPerformanceScores();
    const today = new Date();
    performanceField("performanceMonth").value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
    performanceField("performanceReport").required = true;
    performanceField("performanceReportHint").textContent = "Maximum 20 MB.";
    performanceField("performanceHeading").textContent = "Record station performance";
    performanceField("cancelPerformance").hidden = true;
}

function editPerformance(item) {
    resetPerformance(); editingPerformance = item.performance_id;
    performanceField("performanceBatchStations").hidden = true;
    performanceField("performanceStationScores").replaceChildren();
    performanceField("performanceEditStationField").hidden = false;
    performanceField("performanceEditStation").disabled = false;
    performanceField("performanceEditStation").required = true;
    performanceField("performanceEditStation").value = item.station_id;
    performanceField("performanceSingleScore").hidden = false;
    performanceField("performanceScore").disabled = false;
    performanceField("performanceScore").required = true;
    performanceField("performanceMonth").value = item.report_month.slice(0, 7);
    performanceField("performanceScore").value = item.score;
    performanceField("performanceNotes").value = item.notes || "";
    performanceField("performanceReport").required = false;
    performanceField("performanceReportHint").textContent = `Current report: ${item.original_filename}. Replacement optional, maximum 20 MB.`;
    performanceField("performanceHeading").textContent = "Edit station performance";
    performanceField("cancelPerformance").hidden = false;
    revealPagePanel(performanceField("performanceEditor"));
}

async function downloadPerformanceReport(item) {
    try {
        const response = await apiFetch(`/station-performance/${item.performance_id}/report`);
        if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to download report"));
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a"); link.href = url; link.download = item.original_filename; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { window.alert(error.message); }
}

function performanceReportLink(item) {
    const link = document.createElement("a");
    link.href = "#";
    link.textContent = item.original_filename;
    link.title = "Download performance report";
    link.addEventListener("click", event => { event.preventDefault(); event.stopPropagation(); downloadPerformanceReport(item); });
    return link;
}

function renderPerformanceReports(reports) {
    const body = performanceField("performanceReports"); body.replaceChildren();
    for (const report of reports || []) {
        const row = body.insertRow();
        row.insertCell().append(performanceReportLink({performance_id: report.performance_id, original_filename: report.filename}));
        appendCell(row, report.months.join(", "));
        const details = document.createElement("details");
        const summary = document.createElement("summary"); summary.textContent = `${Object.keys(report.stations).length} stations`;
        details.append(summary);
        for (const name of Object.values(report.stations)) {
            const item = document.createElement("div"); item.textContent = name; details.append(item);
        }
        row.insertCell().append(details);
    }
    if (!body.childElementCount) showTableMessage(body, 3, "No reports match these filters.");
}

function syncPerformanceSelection() {
    performanceField("performanceSelectedCount").textContent = `${performanceSelected.size} records selected`;
    performanceField("applyPerformanceBulk").disabled = performanceSelected.size === 0;
    document.querySelectorAll(".performance-select-record").forEach(input => { input.checked = performanceSelected.has(Number(input.value)); });
}

async function selectFilteredPerformance() {
    const button = performanceField("selectFilteredPerformance"); button.disabled = true;
    try {
        const query = buildQuery({search: performanceState.search, ...performanceFilters()});
        const response = await apiFetch(`/station-performance?${query}`);
        if (!response.ok) throw new Error(await performanceError(response, "Unable to select records"));
        const records = await response.json();
        const editable = records.filter(item => item.can_edit);
        if (editable.length > 5000) throw new Error("Select at most 5,000 records. Narrow your filters.");
        performanceSelected.clear(); editable.forEach(item => performanceSelected.add(item.performance_id));
        syncPerformanceSelection();
        setMessage(performanceField("performanceBulkMessage"), editable.length ? "Filtered editable records selected." : "No editable records match these filters.");
    } catch (error) { setMessage(performanceField("performanceBulkMessage"), error.message, "error"); }
    finally { button.disabled = false; }
}

async function bulkCorrectPerformance(event) {
    event.preventDefault();
    const ids = [...performanceSelected];
    const month = performanceField("performanceBulkMonth").value;
    const reason = performanceField("performanceBulkReason").value.trim();
    if (!ids.length || !reason) { setMessage(performanceField("performanceBulkMessage"), "Select records and give a reason for the correction.", "error"); return; }
    if (!confirm(`Move ${ids.length} selected assessments to ${month}? Scores and reports will remain unchanged.`)) return;
    const button = performanceField("applyPerformanceBulk"); button.disabled = true;
    try {
        const response = await apiFetch("/station-performance/bulk-month", {method: "PUT", editReason: reason,
            headers: {"Content-Type": "application/json"}, body: JSON.stringify({performance_ids: ids, report_month: month})});
        if (!response.ok) throw new Error(await performanceError(response, "Unable to update the selected records"));
        const result = await response.json();
        performanceSelected.clear(); syncPerformanceSelection();
        await loadPerformance();
        setMessage(performanceField("performanceBulkMessage"), `${result.updated} records updated; ${result.unchanged} already had this month. Changes are recorded in history.`, "success");
    } catch (error) { setMessage(performanceField("performanceBulkMessage"), error.message, "error"); }
    finally { syncPerformanceSelection(); }
}

async function loadPerformance() {
    const sequence = ++performanceSequence;
    const body = performanceField("performanceRows");
    showTableMessage(body, 11, "Loading performance...");
    try {
        const response = await apiFetch(`/station-performance?${collectionParameters(performanceState, performanceFilters())}`);
        if (!response.ok) throw new Error(await performanceError(response, "Unable to load station performance"));
        const result = await response.json();
        if (sequence !== performanceSequence) return;
        const summary = result.summary;
        performanceField("performanceAverage").textContent = summary.average == null ? "-" : `${summary.average}%`;
        performanceField("performanceStations").textContent = summary.station_count;
        performanceField("performanceAssessments").textContent = summary.total;
        performanceField("performanceMonths").textContent = summary.months_count;
        renderPerformanceChart("performanceStationChart", summary.averages.stations, summary.station_labels);
        renderPerformanceMonthMatrix(summary);
        renderPerformancePeriodStations("performanceMonthChart", summary.station_period_averages?.months, summary.station_labels, true, summary.averages.months);
        renderPerformancePeriodStations("performanceQuarterChart", summary.station_period_averages?.quarters, summary.station_labels, false, summary.averages.quarters);
        renderPerformancePeriodStations("performanceYearChart", summary.station_period_averages?.years, summary.station_labels, false, summary.averages.years);
        renderPerformanceReports(summary.reports);
        body.replaceChildren();
        for (const item of result.items) {
            const row = body.insertRow();
            [item.performance_id, item.station_code, item.station_name, item.district, item.report_month.slice(0, 7),
                item.score, item.notes, item.original_filename, item.created_by_username, item.updated_at].forEach(value => appendCell(row, value));
            row.cells[7].replaceChildren(performanceReportLink(item));
            const actions = row.insertCell(); actions.className = "table-actions";
            const button = (label, handler, danger = false) => {
                const element = document.createElement("button"); element.type = "button"; element.textContent = label;
                element.className = `${danger ? "danger-button" : "secondary-button"} compact-button`;
                element.onclick = handler; actions.append(element);
            };
            button("Report", () => downloadPerformanceReport(item));
            if (item.can_edit) {
                const label = document.createElement("label"); label.className = "checkbox-row";
                const selection = document.createElement("input"); selection.type = "checkbox";
                selection.className = "performance-select-record"; selection.value = item.performance_id;
                selection.setAttribute("aria-label", `Select ${item.station_name} ${item.report_month.slice(0, 7)} for bulk update`);
                selection.onchange = () => {
                    if (selection.checked) performanceSelected.add(item.performance_id); else performanceSelected.delete(item.performance_id);
                    syncPerformanceSelection();
                };
                label.append(selection, document.createTextNode("Select")); actions.append(label);
                button("Edit", () => editPerformance(item));
                button("Delete", async () => {
                    if (!confirm("Delete this monthly assessment? Admin can restore it from Deleted items.")) return;
                    const response = await apiFetch(`/station-performance/${item.performance_id}`, {method: "DELETE"});
                    if (!response.ok) { window.alert(await getErrorMessage(response, "Unable to delete assessment")); return; }
                    if (editingPerformance === item.performance_id) resetPerformance();
                    performanceSelected.delete(item.performance_id);
                    await loadPerformance();
                }, true);
            }
        }
        if (!result.items.length) showTableMessage(body, 11, "No performance records match these filters.");
        attachRecordHistoryRows(body, "station_performance", result.items, item => item.performance_id);
        syncPerformanceSelection();
        renderPagination("performancePagination", result, performanceState, loadPerformance);
        setMessage(performanceField("performanceFilterMessage"), "");
    } catch (error) {
        if (sequence !== performanceSequence) return;
        showTableMessage(body, 11, error.message);
        showTableMessage(performanceField("performanceReports"), 3, error.message);
        setMessage(performanceField("performanceFilterMessage"), error.message, "error");
        for (const id of ["performanceMonthMatrix", "performanceStationChart", "performanceMonthChart", "performanceQuarterChart", "performanceYearChart"]) performanceField(id).replaceChildren();
    }
}

async function savePerformance(event) {
    event.preventDefault(); const button = performanceField("savePerformance"); button.disabled = true;
    try {
        const file = performanceField("performanceReport").files[0];
        if (file && file.size > 20 * 1024 * 1024) throw new Error("Maximum report size is 20 MB");
        const values = {report_month: performanceField("performanceMonth").value, notes: performanceField("performanceNotes").value.trim()};
        if (editingPerformance) {
            values.station_id = performanceField("performanceEditStation").value;
            values.score = performanceField("performanceScore").value;
        } else {
            const inputs = [...performanceField("performanceStationScores").querySelectorAll("input[data-station-id]")];
            if (!inputs.length || inputs.length > 100) throw new Error("Select between 1 and 100 stations.");
            if (!file) throw new Error("Upload the monthly report for the selected stations.");
            if (inputs.some(input => !input.checkValidity() || input.value === "")) throw new Error("Enter a score from 0 to 100 for each selected station.");
            values.entries = JSON.stringify({stations: inputs.map(input => ({station_id: Number(input.dataset.stationId), score: Number(input.value)}))});
        }
        if (file) values.filename = file.name;
        const response = await apiFetch(`/station-performance${editingPerformance ? `/${editingPerformance}` : "/batch"}?${buildQuery(values)}`,
            {method: editingPerformance ? "PUT" : "POST", headers: {"Content-Type": "application/octet-stream"}, body: file || null});
        if (!response.ok) throw new Error(await performanceError(response, "Unable to save station performance"));
        resetPerformance(); await loadPerformance();
        setMessage(performanceField("performanceMessage"), "Station performance saved.", "success");
    } catch (error) { setMessage(performanceField("performanceMessage"), error.message, "error"); }
    finally { button.disabled = false; }
}

document.addEventListener("DOMContentLoaded", async () => {
    initializeShell(); if (!await requireSession()) return;
    enhanceMultiFilter(performanceField("performanceStation"));
    performanceField("performanceStation").addEventListener("change", renderPerformanceScores);
    resetPerformance();
    performanceField("performanceForm").onsubmit = savePerformance;
    performanceField("cancelPerformance").onclick = resetPerformance;
    performanceField("selectFilteredPerformance").onclick = selectFilteredPerformance;
    performanceField("clearPerformanceSelection").onclick = () => { performanceSelected.clear(); syncPerformanceSelection(); };
    performanceField("performanceBulkForm").onsubmit = bulkCorrectPerformance;
    try {
        const response = await apiFetch("/stations");
        if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load stations"));
        const stations = await response.json();
        for (const station of stations) {
            const label = `${station.station_code} - ${station.station_name}`;
            performanceField("performanceStation").add(new Option(label, station.station_id));
            performanceField("performanceEditStation").add(new Option(label, station.station_id));
            performanceField("performanceStationFilter").add(new Option(label, station.station_id));
        }
        for (const [id, key] of [["performanceDistrictFilter", "district"], ["performanceCategoryFilter", "station_category"]]) {
            [...new Set(stations.map(station => station[key]).filter(Boolean))].sort().forEach(value => performanceField(id).add(new Option(value, value)));
        }
    } catch (error) { setMessage(performanceField("performanceMessage"), error.message, "error"); }
    performanceField("applyPerformance").onclick = () => { performanceState.page = 1; loadPerformance(); };
    performanceField("resetPerformanceFilters").onclick = () => {
        for (const id of ["performanceSearch", "performanceStationFilter", "performanceDistrictFilter", "performanceCategoryFilter", "performanceFrom", "performanceTo"]) performanceField(id).value = "";
        performanceState.search = ""; performanceState.page = 1; loadPerformance();
    };
    bindCollectionControls({state: performanceState, reload: loadPerformance, searchId: "performanceSearch", pageSizeId: "performancePageSize",
        tableSelector: "#performanceTable", getExtraParameters: performanceFilters});
    for (const format of ["csv", "pdf"]) performanceField(format === "csv" ? "performanceCsv" : "performancePdf").onclick = () =>
        downloadCollectionExport("/station-performance", format, performanceState, performanceFilters(), performanceField("performanceHistory").checked ? "station_performance" : null,
            historyColumnsForTable(performanceField("performanceTable"), "station_performance"));
    await loadPerformance();
});
