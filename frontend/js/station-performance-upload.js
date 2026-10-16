"use strict";
(() => {
    const el = id => document.getElementById(id);
    let config = null, stations = [], items = [], dirty = false, loadingConfig = false, saving = false, uploading = false, filtersReady = false, sequence = 0;
    const node = (tag, text, className) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (className) n.className = className; return n; };
    const percent = value => value == null || !Number.isFinite(Number(value)) ? "-" : `${Number(value).toLocaleString(undefined, {maximumFractionDigits: 2})}%`;
    const markDirty = () => { dirty = true; el("configDraft").textContent = "Unsaved changes"; };
    const noticeText = value => typeof value === "string" ? value : value?.message || value?.detail || value?.reason || "Review source observations.";
    function requireCatalogBackend(value) {
        if (value.catalog_schema_version !== 6) {
            el("configurationForm").hidden = true;
            el("parameterEditors").replaceChildren();
            throw new Error("The backend is outdated and does not support registry-wide station coverage and report filters. Restart the app backend, then refresh this page with Ctrl+F5. Station proposals have not been loaded.");
        }
    }
    async function checked(response, fallback) {
        if (!response.ok) {
            const message = await getErrorMessage(response, fallback);
            throw new Error(response.status === 409 ? `${message} Reload configuration to get the latest version before saving again. Your draft is still shown.` : message);
        }
        return response;
    }
    function options(select, entries, empty) {
        select.replaceChildren();
        if (empty != null) select.add(new Option(empty, ""));
        entries.forEach(([value, label]) => select.add(new Option(label, String(value))));
    }
    function field(parent, caption, control) {
        const wrapper = node("div", null, "field"), label = node("label", caption);
        if (!control.id) control.id = `uploadControl${++sequence}`;
        label.htmlFor = control.id; wrapper.append(label, control); parent.append(wrapper); return control;
    }
    function textField(parent, caption, value, update, required = false) {
        const input = node("input"); input.value = value ?? ""; input.required = required; input.maxLength = 200;
        input.addEventListener("input", () => { update(input.value.trim()); markDirty(); });
        return field(parent, caption, input);
    }
    function toggle(parent, caption, value, update) {
        const label = node("label", null, "checkbox-row"), input = node("input"); input.type = "checkbox"; input.checked = value;
        input.addEventListener("change", () => { update(input.checked); markDirty(); }); label.append(input, document.createTextNode(caption)); parent.append(label);
        return input;
    }
    function multi(parent, caption, entries, values, update, empty = "None", disabled = []) {
        // Shared multiselects expose pipe-delimited values through the original select.
        const known = new Set(entries.map(([id]) => String(id)));
        entries = [...entries, ...values.filter(id => !known.has(String(id))).map(id => [id, `${id} (retained rule)`])];
        const select = field(parent, caption, node("select")); options(select, entries, empty);
        [...select.options].forEach(option => option.disabled = disabled.includes(option.value));
        enhanceMultiFilter(select); select.value = values.map(String).join("|");
        select.addEventListener("change", () => { update(select.value.split("|").filter(Boolean)); markDirty(); }); return select;
    }
    function hours(parent, caption, values, update) {
        const details = node("details", null, "upload-hours"), summary = node("summary"), grid = node("div", null, "upload-hour-grid");
        const refresh = () => { summary.textContent = `${caption} (${values.length}/24)`; };
        for (let hour = 0; hour < 24; hour++) {
            const label = node("label"), input = node("input"); input.type = "checkbox"; input.checked = values.includes(hour); input.setAttribute("aria-label", `${caption}: ${String(hour).padStart(2, "0")}:00`);
            input.addEventListener("change", () => { values = [...grid.querySelectorAll("input")].flatMap((box, index) => box.checked ? [index] : []); update(values); refresh(); markDirty(); });
            label.append(input, document.createTextNode(String(hour).padStart(2, "0"))); grid.append(label);
        }
        refresh(); details.append(summary, grid); parent.append(details);
    }
    function button(parent, caption, action) { const b = node("button", caption, "secondary-button"); b.type = "button"; b.onclick = action; parent.append(b); return b; }
    function parameterOptions(cadence) { return config.parameters.filter(p => !cadence || p.cadence === cadence).map(p => [p.element_id, `${p.element_id} - ${p.name}`]); }
    function countingGroup(p, cadence) {
        p.cadence = cadence;
        if (cadence === "daily") {
            p.zero_parent = null;
            config.parameters.forEach(other => { if (other.zero_parent === p.element_id) other.zero_parent = null; });
            config.stations.forEach(station => { delete station.hourly_overrides[p.element_id]; });
        }
    }
    function renderParameters() {
        const container = el("parameterEditors"); container.replaceChildren();
        config.parameters.forEach((p, index) => {
            const group = node("fieldset", null, "upload-parameter"), legend = node("legend", `Parameter ${index + 1}`), grid = node("div", null, "upload-editor-grid");
            const code = textField(grid, "Element code", p.element_id, () => {}, true);
            code.addEventListener("change", () => {
                const next = code.value.trim(), previous = p.element_id;
                if (!next || config.parameters.some(other => other !== p && String(other.element_id) === next)) { code.setCustomValidity("Enter a unique element code."); code.reportValidity(); return; }
                code.setCustomValidity(""); p.element_id = next;
                config.parameters.forEach(other => { if (String(other.zero_parent) === String(previous)) other.zero_parent = next; });
                config.stations.forEach(s => {
                    s.daily_excluded = s.daily_excluded.map(id => String(id) === String(previous) ? next : id);
                    if (Object.hasOwn(s.hourly_overrides, previous)) { s.hourly_overrides[next] = s.hourly_overrides[previous]; delete s.hourly_overrides[previous]; }
                }); renderParameters(); renderStation();
            });
            code.addEventListener("input", () => code.setCustomValidity(""));
            code.readOnly = Boolean(config.catalog_managed);
            const nameInput = textField(grid, "Parameter name", p.name, value => p.name = value, true);
            nameInput.readOnly = Boolean(config.catalog_managed && !p.reserved);
            const cadence = field(grid, "Cadence", node("select")); options(cadence, [["daily", "Daily"], ["hourly", "Hourly"]]); cadence.value = p.cadence;
            cadence.onchange = () => {
                countingGroup(p, cadence.value);
                p.schedule_mode = "legacy"; p.times = [];
                if (p.cadence === "daily") {
                    p.hours = []; p.zero_parent = null;
                    config.parameters.forEach(other => { if (other.zero_parent === p.element_id) other.zero_parent = null; });
                    config.stations.forEach(station => { delete station.hourly_overrides[p.element_id]; });
                } else if (!p.hours.length) p.hours = Array.from({length: 24}, (_, index) => index);
                markDirty(); renderParameters(); renderStation();
            };
            const schedule = field(grid, "Observation schedule", node("select"));
            options(schedule, [["none", "Not scheduled"], ["interval", "Regular interval"], ["times", "Fixed daily times"], ["legacy", "Legacy counting"]]);
            schedule.value = p.schedule_mode || "legacy";
            schedule.onchange = () => {
                p.schedule_mode = schedule.value;
                p.interval_minutes ||= 60;
                if (p.schedule_mode === "none") { p.enabled = false; p.hours = []; p.times = []; }
                if (p.schedule_mode === "interval") { p.cadence = "hourly"; if (!p.hours.length) p.hours = Array.from({length: 24}, (_, i) => i); }
                if (p.schedule_mode === "times" && !p.times?.length) { p.times = ["08:00"]; countingGroup(p, "daily"); }
                markDirty(); renderParameters(); renderStation();
            };
            if (p.schedule_mode === "interval") {
                const interval = field(grid, "Interval (minutes)", node("input"));
                interval.type = "number"; interval.min = "1"; interval.max = "1440"; interval.step = "1"; interval.required = true;
                interval.value = p.interval_minutes || 60;
                interval.oninput = () => { p.interval_minutes = Number(interval.value); markDirty(); };
            } else if (p.schedule_mode === "times") {
                const times = field(grid, "Daily observation times", node("div"));
                (p.times || []).forEach((value, timeIndex) => {
                    const row = node("div", null, "upload-time-row"), input = node("input");
                    input.type = "time"; input.step = "60"; input.required = true; input.value = value;
                    input.setAttribute("aria-label", `${p.element_id} observation time ${timeIndex + 1}`);
                    input.oninput = () => { p.times[timeIndex] = input.value; markDirty(); };
                    row.append(input); const removeTime = button(row, "Remove time", () => { p.times.splice(timeIndex, 1); countingGroup(p, p.times.length === 1 ? "daily" : "hourly"); markDirty(); renderParameters(); renderStation(); }); removeTime.disabled = p.times.length === 1; times.append(row);
                });
                button(times, "Add time", () => { const next = Array.from({length: 24}, (_, i) => `${String(i).padStart(2, "0")}:00`).find(t => !p.times.includes(t)); if (next) p.times.push(next); p.cadence = p.times.length === 1 ? "daily" : "hourly"; p.hours = Array.from({length: 24}, (_, i) => i); markDirty(); renderParameters(); });
            }
            const enabled = toggle(grid, "Parameter enabled", p.enabled, value => p.enabled = value);
            enabled.disabled = p.schedule_mode === "none";
            const eligible = config.catalog_managed ? stations.filter(s => (p.eligible_station_codes || []).includes(s.station_code)) : stations;
            const inactive = p.inactive_station_codes || [];
            multi(grid, "Proposed stations", eligible.map(s => [s.station_code, `${s.station_code} - ${s.station_name}${inactive.includes(s.station_code) ? ` (${p.station_statuses?.[s.station_code] || "Not operational"})` : ""}`]), p.station_codes, value => {
                p.station_codes = value;
                if (config.catalog_managed) {
                    const selectable = eligible.map(s => s.station_code).filter(code => !inactive.includes(code));
                    const previouslyExcluded = (p.excluded_station_codes || []).filter(code => !selectable.includes(code));
                    p.excluded_station_codes = [...previouslyExcluded, ...selectable.filter(code => !value.includes(code))];
                }
            }, eligible.length ? "No operational stations selected" : "No matching station categories", inactive);
            if (config.catalog_managed) grid.append(node("p", (p.station_categories || []).join(" | "), "muted"));
            if (p.is_cloud_parameter ?? (Boolean(p.zero_parent) || /cloud/i.test(p.name))) {
            const parent = field(grid, "Zero / cloud parent", node("select")); options(parent, config.parameters.filter(other => other.cadence === "hourly" && (other.is_cloud_parameter ?? /cloud/i.test(other.name)) && other.element_id !== p.element_id).map(other => [other.element_id, `${other.element_id} - ${other.name}`]), "No parent");
            if (p.zero_parent != null && ![...parent.options].some(option => option.value === String(p.zero_parent))) parent.add(new Option(`${p.zero_parent} (retained rule)`, String(p.zero_parent)));
            parent.value = p.zero_parent ?? "";
            parent.onchange = () => { p.zero_parent = parent.value || null; markDirty(); };
            }
            if (!["times", "none"].includes(p.schedule_mode)) hours(grid, "Default hours", p.hours, value => p.hours = value);
            const remove = button(grid, "Remove parameter", () => {
                config.parameters.splice(index, 1);
                config.parameters.forEach(other => { if (String(other.zero_parent) === String(p.element_id)) other.zero_parent = null; });
                config.stations.forEach(s => { s.daily_excluded = s.daily_excluded.filter(id => String(id) !== String(p.element_id)); delete s.hourly_overrides[p.element_id]; });
                markDirty(); renderParameters(); renderStation();
            }); group.append(legend, grid); container.append(group);
            remove.hidden = Boolean(config.catalog_managed);
        });
    }
    function renderStation() {
        const container = el("stationEditor"); container.replaceChildren();
        const code = el("configStation").value; if (!code) return;
        let station = config.stations.find(s => s.station_code === code);
        if (!station) {
            const source = stations.find(s => s.station_code === code);
            station = {station_code: code, station_name: source?.station_name || code, enabled: true, daily_excluded: [], hourly_overrides: {}};
        }
        const commit = () => { if (!config.stations.includes(station)) config.stations.push(station); markDirty(); };
        if (!config.stations.includes(station)) button(container, "Add station to configuration", () => { commit(); renderStation(); });
        textField(container, "Station report name", station.station_name, value => { station.station_name = value; commit(); }, true);
        toggle(container, "Station enabled", station.enabled, value => { station.enabled = value; commit(); });
        multi(container, "Daily exclusions", parameterOptions("daily"), station.daily_excluded, value => { station.daily_excluded = value; commit(); });
        container.append(node("h3", "Hourly overrides"));
        Object.entries(station.hourly_overrides).forEach(([id, override]) => {
            const row = node("div", null, "upload-override"), grid = node("div", null, "upload-editor-grid");
            const select = field(grid, "Hourly parameter", node("select"));
            const choices = parameterOptions("hourly"); if (!choices.some(([key]) => String(key) === id)) choices.push([id, `${id} (retained rule)`]);
            options(select, choices); select.value = id;
            select.onchange = () => {
                if (Object.hasOwn(station.hourly_overrides, select.value)) { select.value = id; setMessage(el("configMessage"), "This parameter already has an override.", "error"); return; }
                delete station.hourly_overrides[id]; station.hourly_overrides[select.value] = override; commit(); renderStation();
            };
            toggle(grid, "Count in summary", override.count_in_summary, value => { override.count_in_summary = value; commit(); });
            hours(grid, "Expected hours", override.hours, value => { override.hours = value; commit(); });
            hours(grid, "Reported excluded hours", override.reported_excluded_hours, value => { override.reported_excluded_hours = value; commit(); });
            button(grid, "Remove override", () => { delete station.hourly_overrides[id]; commit(); renderStation(); }); row.append(grid); container.append(row);
        });
        const available = parameterOptions("hourly").filter(([id]) => !Object.hasOwn(station.hourly_overrides, id));
        const add = button(container, "Add hourly override", () => {
            const id = available[0][0], parameter = config.parameters.find(p => p.element_id === id);
            station.hourly_overrides[id] = {hours: [...parameter.hours], reported_excluded_hours: [], count_in_summary: true}; commit(); renderStation();
        }); add.disabled = !available.length;
    }
    async function loadConfig() {
        if (loadingConfig || saving) return;
        if (dirty && !window.confirm("Discard unsaved configuration changes and reload?")) return;
        loadingConfig = true; el("reloadConfig").disabled = true;
        try {
            const response = await checked(await apiFetch("/performance-upload/config"), "Unable to load calculation configuration.");
            const loaded = await response.json(); requireCatalogBackend(loaded);
            config = loaded; dirty = false; el("configDraft").textContent = ""; el("configVersion").textContent = `Version ${config.version}`;
            const scope = el("reportScopeEditor"); scope.replaceChildren();
            multi(scope, "Included station statuses", ["Operational", "Under maintenance", "Suspended", "Closed"].map(status => [status, status]),
                config.station_statuses || ["Operational", "Under maintenance", "Suspended"], value => config.station_statuses = value, "Select statuses");
            const known = new Map(stations.map(s => [s.station_code, s.station_name])); config.stations.forEach(s => known.set(s.station_code, s.station_name));
            options(el("configStation"), [...known].map(([id, name]) => [id, `${id} - ${name}`]), "Select station");
            el("configurationForm").hidden = false; el("addParameter").hidden = Boolean(config.catalog_managed); renderParameters(); renderStation();
            const messages = [...(config.catalog_errors || [])];
            if (config.catalog_pending?.length) messages.push(`Schedules required: ${config.catalog_pending.join(", ")}`);
            setMessage(el("configMessage"), messages.join(" "), messages.length ? "error" : "");
        } catch (error) { setMessage(el("configMessage"), error.message, "error"); }
        finally { loadingConfig = false; el("reloadConfig").disabled = false; }
    }
    function chart(id, groups, chronological = false) {
        const container = el(id); container.replaceChildren();
        const availability = values => {
            const expected = values.reduce((sum, item) => sum + Number(item.daily_expected) + Number(item.hourly_expected), 0);
            const available = values.reduce((sum, item) => sum + Number(item.daily_available) + Number(item.hourly_available), 0);
            return expected ? available * 100 / expected : 0;
        };
        [...groups].sort((a, b) => chronological ? a[0].localeCompare(b[0]) : availability(b[1]) - availability(a[1])).forEach(([label, values]) => {
            const score = availability(values);
            const row = node("div", null, "horizontal-bar-row"), track = node("div", null, "horizontal-bar-track"), fill = node("div", null, "horizontal-bar-fill");
            fill.style.width = `${Math.max(0, Math.min(100, score))}%`; track.append(fill); row.append(node("span", label), track, node("strong", percent(score))); container.append(row);
        }); if (!container.childElementCount) container.append(node("p", "No scores match this period."));
    }
    function renderCharts() {
        const stationGroups = new Map(), periodGroups = new Map(), mode = el("chartPeriod").value;
        items.forEach(item => {
            if (item.overall_percent == null || !Number.isFinite(Number(item.overall_percent))) return;
            const station = `${item.station_code} - ${item.station_name}`;
            if (!stationGroups.has(station)) stationGroups.set(station, []); stationGroups.get(station).push(item);
            const [year, month] = item.report_month.split("-").map(Number), fiscal = month >= 7 ? year : year - 1;
            const period = mode === "month" ? item.report_month : `FY ${fiscal}/${fiscal + 1}${mode === "quarter" ? ` Q${Math.floor(((month + 5) % 12) / 3) + 1}` : ""}`;
            if (!periodGroups.has(period)) periodGroups.set(period, []); periodGroups.get(period).push(item);
        }); chart("uploadStationChart", stationGroups); chart("uploadPeriodChart", periodGroups, true);
    }
    function saveBlob(blob, filename) {
        const url = URL.createObjectURL(blob), link = node("a"); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    async function report(run, format, control) {
        control.disabled = true; setMessage(el("reportMessage"), "Preparing report...");
        try {
            const query = new URLSearchParams(appliedFilters); query.set("format", format);
            const response = await checked(await apiFetch(`/performance-upload/runs/${encodeURIComponent(run.run_id)}/report?${query}`), "Unable to download report.");
            const disposition = response.headers.get("Content-Disposition") || "";
            let filename = disposition.match(/filename="([^"]+)"|filename=([^;]+)/i)?.slice(1).find(Boolean);
            const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]; if (encoded) { try { filename = decodeURIComponent(encoded); } catch { /* Use the plain filename if encoding is malformed. */ } }
            saveBlob(await response.blob(), filename || `performance-${run.run_id}.${format === "details" ? "zip" : format}`); setMessage(el("reportMessage"), "Report downloaded.", "success");
        } catch (error) { setMessage(el("reportMessage"), error.message, "error"); } finally { control.disabled = false; }
    }
    function renderResults(data) {
        const selected = (appliedFilters.get("element_id") || "").split("|").filter(Boolean);
        const names = selected.map(id => [...el("uploadElementFilter").options].find(option => option.value === id)?.textContent || id);
        const scope = names.length ? names.join("; ") : "All elements";
        el("resultScoreScope").textContent = scope;
        el("uploadAverageLabel").textContent = names.length ? "Selected element score" : "Average score";
        el("uploadOverallHeading").textContent = names.length ? "Selected elements (%)" : "Overall (%)";
        const knownElements = new Set([...el("uploadElementFilter").options].map(option => option.value));
        (data.element_summary || []).forEach(element => {
            if (!knownElements.has(element.element_id)) { el("uploadElementFilter").add(new Option(`${element.element_id} - ${element.element_name}`,element.element_id)); knownElements.add(element.element_id); }
        });
        items = data.items || []; const summary = data.summary || {};
        el("uploadAverage").textContent = percent(summary.average); ["Stations", "Months", "Records"].forEach(key => el(`upload${key}`).textContent = summary[key.toLowerCase()] ?? 0);
        const body = el("uploadResultRows"); body.replaceChildren();
        items.forEach(item => {
            const row = node("tr"); ["station_code", "station_name", "district", "report_month", "daily_expected", "daily_available", "daily_missing", "daily_percent", "hourly_expected", "hourly_available", "hourly_missing", "hourly_percent", "overall_percent", "created_at"].forEach(key => row.append(node("td", key.endsWith("percent") ? percent(item[key]) : item[key] ?? "-"))); body.append(row);
        });
        if (!items.length) { const row = body.insertRow(), cell = row.insertCell(); cell.colSpan = 14; cell.textContent = names.length ? `No saved results for ${scope} in the selected period.` : "No results match these filters."; }
        [["elementScoreRows", data.element_summary || [], ["element_id", "element_name", "expected", "available", "missing", "score"]],
         ["elementDetailRows", data.element_items || [], ["station_name", "report_month", "element_id", "element_name", "expected", "available", "missing", "score"]]].forEach(([id, entries, keys]) => {
            const target = el(id); target.replaceChildren();
            entries.forEach(entry => {
                const row = node("tr"); keys.forEach(key => row.append(node("td", key === "score" ? (entry.score == null ? "Not assessed" : percent(entry.score)) : entry[key]))); target.append(row);
            });
            if (!entries.length) { const cell = target.insertRow().insertCell(); cell.colSpan = keys.length; cell.textContent = "No element scores. Upload data using the instrument catalog configuration."; }
        });
        const runs = el("uploadRunRows"); runs.replaceChildren();
        [...(data.runs || [])].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).forEach(run => {
            const row = node("tr"); [run.filename, run.created_by_username, run.created_at, run.row_count, run.invalid_rows, run.duplicate_rows, (run.periods || []).join(", ")].forEach(value => row.append(node("td", value ?? "-")));
            const review = node("td"), notices = [...(run.warnings || []), ...(run.conflicts || []).map(value => `Conflict: ${noticeText(value)}`)];
            review.textContent = notices.map(noticeText).join("; ") || "No warnings";
            if (run.config_version) review.prepend(node("div", `Rule version ${run.config_version}`));
            if (run.published) review.append(node("div", `${run.published} published`)); row.append(review);
            const cell = node("td"), actions = node("div", null, "upload-report-actions");
            ["pdf", "csv", "docx", "details"].forEach(format => { const b = button(actions, format === "details" ? "Details ZIP" : format.toUpperCase(), () => report(run, format, b)); b.title = `Download ${format === "details" ? "detailed CSV archive" : format.toUpperCase()} for ${run.filename}`; }); cell.append(actions); row.append(cell); runs.append(row);
        });
        if (!runs.childElementCount) { const cell = runs.insertRow().insertCell(); cell.colSpan = 9; cell.textContent = "No uploads recorded."; }
        renderCharts();
    }
    let resultRequest = 0;
    let appliedFilters = new URLSearchParams();
    let filterTimer;
    async function loadResults() {
        clearTimeout(filterTimer);
        if (!filtersReady && ["uploadCategoryFilter", "uploadStatusFilter", "uploadElementFilter"].some(id => el(id).value)) {
            setMessage(el("resultsMessage"), "Restart the backend and refresh this page to enable category, status and element filters.", "error"); return false;
        }
        const from = el("uploadFrom").value, to = el("uploadTo").value;
        if (from && to && from > to) { setMessage(el("resultsMessage"), "From month must be on or before to month.", "error"); return false; }
        const request = ++resultRequest, query = new URLSearchParams();
        Object.entries({station_code: el("uploadStationFilter").value, district: el("uploadDistrictFilter").value,
            station_category: el("uploadCategoryFilter").value, station_status: el("uploadStatusFilter").value,
            element_id: el("uploadElementFilter").value, month_from: from, month_to: to}).forEach(([key, value]) => { if (value) query.set(key, value); });
        setMessage(el("resultsMessage"), "Loading results...");
        try {
            const response = await checked(await apiFetch(`/performance-upload/results?${query}`), "Unable to load performance results."); const data = await response.json();
            if (request !== resultRequest) return false; appliedFilters = query; renderResults(data); setMessage(el("resultsMessage"), ""); return true;
        } catch (error) { if (request === resultRequest) setMessage(el("resultsMessage"), error.message, "error"); return false; }
    }
    document.addEventListener("DOMContentLoaded", async () => {
        initializeShell(); const user = await requireSession(); if (!user) return;
        el("resultFilters").onsubmit = event => { event.preventDefault(); loadResults(); };
        ["uploadStationFilter", "uploadDistrictFilter", "uploadCategoryFilter", "uploadStatusFilter", "uploadElementFilter", "uploadFrom", "uploadTo"].forEach(id => {
            el(id).addEventListener("change", () => {
                clearTimeout(filterTimer);
                ++resultRequest;
                filterTimer = setTimeout(loadResults, 250);
            });
        });
        el("resetFilters").onclick = () => { ["uploadStationFilter", "uploadDistrictFilter", "uploadCategoryFilter", "uploadStatusFilter", "uploadElementFilter", "uploadFrom", "uploadTo"].forEach(id => el(id).value = ""); loadResults(); };
        el("chartPeriod").onchange = renderCharts;
        el("csvTemplate").onclick = () => saveBlob(new Blob(["station_id,element_id,element_name,date_time,value\r\n"], {type: "text/csv;charset=utf-8"}), "observation-template.csv");
        el("observationUpload").onsubmit = async event => {
            event.preventDefault(); if (uploading) return;
            const file = el("observationFile").files[0]; el("uploadWarnings").replaceChildren();
            const csvFile = file && /\.csv$/i.test(file.name);
            const limit = csvFile ? 2 * 1024 ** 3 : 20 * 1024 ** 2;
            if (!file || !/\.(csv|xlsx|xlsm)$/i.test(file.name) || !file.size || file.size > limit) { setMessage(el("uploadMessage"), "Choose a non-empty CSV, XLSX, or XLSM file. Maximum: 2 GB for CSV; 20 MB for Excel.", "error"); return; }
            uploading = true; el("analyzeFile").disabled = true; el("observationFile").disabled = true; setMessage(el("uploadMessage"), "Uploading and calculating observation completeness...");
            try {
                const backend = await checked(await apiFetch("/performance-upload/config"), "Unable to verify catalog configuration.");
                const capabilities = await backend.json(); requireCatalogBackend(capabilities);
                if (csvFile && file.size > 20 * 1024 ** 2 && !(capabilities.observation_upload_max_bytes >= file.size)) throw new Error("Restart the backend and refresh with Ctrl+F5 to enable CSV uploads up to 2 GB.");
                const response = await checked(await apiFetch(`/performance-upload/analyze?${new URLSearchParams({filename: file.name})}`, {method: "POST", headers: {"Content-Type": "application/octet-stream"}, body: file}), "Unable to process observations. Check dates use DD/MM/YYYY HH:MM or real Excel dates.");
                const result = await response.json();
                setMessage(el("uploadMessage"), `Run ${result.run_id}: ${result.results_count} station-month results. ${result.invalid_rows} invalid rows; ${result.duplicate_rows} duplicates. Months: ${(result.periods || []).join(", ") || "none"}.`, "success");
                (result.warnings || []).forEach(warning => el("uploadWarnings").append(node("li", noticeText(warning))));
                el("observationFile").value = ""; el("observationFile").dispatchEvent(new Event("change", {bubbles: true}));
                await loadResults();
            } catch (error) {
                const interrupted = error.name === "AbortError" || (error instanceof TypeError && /fetch|network|load failed/i.test(error.message));
                setMessage(el("uploadMessage"), interrupted ? "The upload connection was interrupted. Check Upload history before retrying. Keep this page open and use a stable connection." : error.message, "error");
            }
            finally { uploading = false; el("analyzeFile").disabled = false; el("observationFile").disabled = false; }
        };
        el("reloadConfig").onclick = loadConfig; el("configStation").onchange = renderStation;
        el("addParameter").onclick = () => {
            let id = "new_element", suffix = 1; while (config.parameters.some(p => p.element_id === id)) id = `new_element_${suffix++}`;
            config.parameters.push({element_id: id, name: "New parameter", cadence: "daily", hours: [], enabled: true, station_codes: [], zero_parent: null}); markDirty(); renderParameters(); renderStation();
        };
        el("configurationForm").onsubmit = async event => {
            event.preventDefault(); if (user.department !== "Admin" || !config || saving) return;
            const reason = el("configReason").value.trim(); if (!reason) { el("configReason").setCustomValidity("Enter a reason for this change."); el("configReason").reportValidity(); return; }
            saving = true; el("saveConfig").disabled = true; el("reloadConfig").disabled = true;
            // Lock all draft controls while the versioned snapshot is being saved.
            const controls = [...el("configurationForm").querySelectorAll("input, select, button")], prior = controls.map(control => control.disabled); controls.forEach(control => control.disabled = true);
            try {
                const response = await checked(await apiFetch("/performance-upload/config", {method: "PUT", headers: {"Content-Type": "application/json"}, editReason: reason, body: JSON.stringify(config)}), "Unable to save configuration.");
                const saved = response.status === 204 ? null : await response.json(); config.version = saved?.version ?? config.version + 1;
                dirty = false; el("configDraft").textContent = ""; el("configVersion").textContent = `Version ${config.version}`; el("configReason").value = ""; setMessage(el("configMessage"), "Configuration saved. New uploads will use these rules.", "success");
            } catch (error) { setMessage(el("configMessage"), error.message, "error"); }
            finally { controls.forEach((control, index) => control.disabled = prior[index]); saving = false; el("saveConfig").disabled = false; el("reloadConfig").disabled = false; }
        };
        el("configReason").oninput = () => el("configReason").setCustomValidity("");
        window.addEventListener("beforeunload", event => { if (dirty || uploading) { event.preventDefault(); event.returnValue = ""; } });
        window.addEventListener("focus", () => { if (user.department === "Admin" && config && !dirty && !saving) loadConfig(); });
        try {
            const response = await checked(await apiFetch("/stations"), "Unable to load station filters."); stations = await response.json();
            options(el("uploadStationFilter"), stations.map(s => [s.station_code, `${s.station_code} - ${s.station_name}`]), "All stations");
            options(el("uploadDistrictFilter"), [...new Set(stations.map(s => s.district).filter(Boolean))].sort().map(d => [d, d]), "All districts");
            const filtersResponse = await checked(await apiFetch("/performance-upload/filter-options"), "Unable to load category and element filters. Restart the backend, then refresh this page.");
            const filters = await filtersResponse.json();
            if (filters.report_filter_schema_version !== 1) throw new Error("Restart the backend and refresh this page to enable category, status and element filters.");
            filtersReady = true;
            options(el("uploadCategoryFilter"), (filters.station_categories || []).map(value => [value, value]), "All categories");
            options(el("uploadStatusFilter"), (filters.station_statuses || []).map(value => [value, value]), "All statuses");
            options(el("uploadElementFilter"), (filters.elements || []).map(value => [value.element_id, `${value.element_id} - ${value.element_name}`]), "All elements");
        } catch (error) { setMessage(el("uploadMessage"), error.message, "error"); }
        await loadResults(); if (user.department === "Admin") await loadConfig();
    });
})();
