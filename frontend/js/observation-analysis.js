(() => {
    "use strict";
    const field = id => document.getElementById(id);
    const comparison = document.body.dataset.analysis === "comparison";
    const endpoint = `/observation-analysis/${comparison ? "comparison" : "monitoring"}`;
    const number = value => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)) ? Number(value) : null;
    const display = (value, digits = 3) => number(value) === null ? "-" : number(value).toLocaleString(undefined, {maximumFractionDigits: digits});
    const text = value => value == null || value === "" ? "-" : String(value);
    let options = null;
    let applied = null;
    let controller = null;
    let sequence = 0;
    let defaults = {pair: "", parameter: "temperature"};
    const charts = new Map();
    const observer = new ResizeObserver(entries => entries.forEach(entry => charts.get(entry.target)?.()));

    function table(id, rows, columns, empty = "No observations match the applied filters.") {
        const body = field(id);
        if (!body) return;
        const fragment = document.createDocumentFragment();
        for (const item of rows || []) {
            const row = document.createElement("tr");
            for (const column of columns) {
                const value = typeof column === "function" ? column(item) : item[column];
                const cell = appendCell(row, text(value));
                if (value == null) cell.className = "analysis-missing";
            }
            fragment.append(row);
        }
        if (!fragment.childNodes.length) {
            const row = document.createElement("tr");
            const cell = appendCell(row, empty); cell.colSpan = columns.length; fragment.append(row);
        }
        body.replaceChildren(fragment);
    }

    function setMessage(message, error = false) {
        field("analysisMessage").textContent = message;
        field("analysisMessage").hidden = !message;
        field("analysisMessage").classList.toggle("error", error);
    }

    async function checked(response, fallback) {
        if (response.ok) return response;
        if ([404, 405].includes(response.status)) throw new Error("Observation analysis is unavailable on this server. Retry after the updated backend is running.");
        throw new Error(await getErrorMessage(response, fallback));
    }

    function parameters() {
        const query = new URLSearchParams();
        const names = comparison ? {pair_id: "pairFilter", comparison_id: "comparisonFilter"} : {station_code: "stationFilter"};
        Object.assign(names, {start: "analysisStart", end: "analysisEnd", period: "analysisPeriod"});
        for (const [key, id] of Object.entries(names)) if (field(id).value !== "") query.set(key, field(id).value);
        if (comparison && !query.has("comparison_id")) {
            const available = [...field("comparisonFilter").options].map(option => option.value).filter(Boolean);
            if (available.length) query.set("comparison_id", available.join("|"));
        }
        query.set("days", "true");
        return query;
    }

    function validate() {
        const start = field("analysisStart"); const end = field("analysisEnd");
        end.setCustomValidity(start.value && end.value && start.value > end.value ? "End day must be on or after start day." : "");
        return field("analysisFilters").reportValidity();
    }

    function scope(query) {
        const selected = (id, value, all) => value ? value.split("|").map(key => [...field(id).options].find(option => option.value === key)?.textContent || key).join(", ") : all;
        const labels = comparison ? [selected("pairFilter", query.get("pair_id"), "All pairs"), selected("comparisonFilter", query.get("comparison_id"), "All parameters")] : [selected("stationFilter", query.get("station_code"), "All stations")];
        labels.push(`${query.get("start") || "Available start"} to ${query.get("end") || "Available end"}`);
        labels.push(({hour: "Hourly", day: "Daily", week: "Weekly", month: "Monthly"})[query.get("period")] || "Hourly");
        return labels.join(" | ");
    }

    function canvasContext(canvas) {
        const bounds = canvas.getBoundingClientRect();
        if (bounds.width < 1 || bounds.height < 1) return null;
        const ratio = window.devicePixelRatio || 1;
        canvas.width = Math.round(bounds.width * ratio); canvas.height = Math.round(bounds.height * ratio);
        const ctx = canvas.getContext("2d"); ctx.scale(ratio, ratio);
        ctx.font = "12px Arial"; ctx.fillStyle = "#526575"; ctx.lineWidth = 1;
        return {ctx, width: bounds.width, height: bounds.height};
    }

    function plot(canvas, tracks, title, type) {
        // Each stream owns its points; only the shared time domain is collected.
        const stamps = new Set();
        let min = Infinity, max = -Infinity, hasValues = false;
        for (const track of tracks) for (const point of track.points) {
            stamps.add(point.stamp);
            if (point.value !== null) { hasValues = true; min = Math.min(min, point.value); max = Math.max(max, point.value); }
        }
        const times = [...stamps].sort((a, b) => a - b);
        if (max === min) { min -= 0.5; max += 0.5; }
        let axisDivisions = comparison ? 5 : 4;
        let selectedPoint = 0;
        const draw = () => {
            const surface = canvasContext(canvas); if (!surface) return;
            canvas.parentElement.querySelector(".analysis-point-value")?.remove();
            const {ctx, width, height} = surface;
            canvas.dataset.minimum = String(min); canvas.dataset.maximum = String(max);
            if (comparison) { ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, width, height); ctx.fillStyle = "#595959"; }
            canvas.setAttribute("aria-label", `${title}; ${type}; ${tracks.length} visible tracks; ${times.length} time buckets. ${hasValues ? "Raw values in records and CSV." : "No visible observations in this period."}`);
            if (!hasValues || !times.length || !Number.isFinite(min) || !Number.isFinite(max)) {
                canvas.onclick = null; canvas.onkeydown = null;
                ctx.textAlign = "center"; ctx.fillText("No visible observations in this period.", width / 2, height / 2); return;
            }
            const left = 64, right = width - 18, top = 30, bottom = height - 55;
            const first = times[0], last = times.at(-1);
            let interval = Infinity;
            for (let i = 1; i < times.length; i++) interval = Math.min(interval, times[i] - times[i - 1]);
            const padding = type === "column" ? (Number.isFinite(interval) ? interval / 2 : 1) : 0;
            const from = first - padding, to = last + padding;
            const x = stamp => from === to ? (left + right) / 2 : left + (stamp - from) / (to - from) * (right - left);
            const y = value => bottom - (value - min) / (max - min) * (bottom - top);
            const baseline = Math.max(min, Math.min(max, 0));
            ctx.textAlign = "left";
            ctx.textAlign = "right";
            const divisions = axisDivisions;
            for (let index = 0; index <= divisions; index++) {
                const value = min + (max - min) * index / divisions, at = y(value);
                ctx.strokeStyle = comparison ? "#d9d9d9" : "#e0e7eb"; ctx.beginPath(); ctx.moveTo(left, at); ctx.lineTo(right, at); ctx.stroke();
                ctx.fillText(display(value), left - 8, at + 4, left - 12);
            }
            const xDivisions = width < 500 ? 3 : 6;
            const label = stamp => new Date(stamp).toLocaleString(undefined, {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"});
            if (comparison) {
                for (let index = 0; index <= xDivisions; index++) {
                    const at = left + (right - left) * index / xDivisions;
                    ctx.strokeStyle = "#d9d9d9"; ctx.beginPath(); ctx.moveTo(at, top); ctx.lineTo(at, bottom); ctx.stroke();
                }
                ctx.strokeStyle = "#bfbfbf"; ctx.strokeRect(left, top, right - left, bottom - top);
            }
            ctx.save(); ctx.beginPath(); ctx.rect(left, top, right - left, bottom - top); ctx.clip();
            const bucketWidth = Number.isFinite(interval) ? (right - left) * interval / (to - from || 1) : (right - left);
            const barWidth = Math.min(26, bucketWidth * 0.8 / Math.max(1, tracks.length));
            tracks.forEach((track, trackIndex) => {
                ctx.strokeStyle = track.color; ctx.fillStyle = track.color; ctx.lineWidth = comparison ? 2.5 : 1.8;
                ctx.setLineDash(!comparison && track.automatic ? [6, 4] : []);
                if (type === "column" || type === "scatter") {
                    for (const point of track.points) {
                        if (point.value === null) continue;
                        const at = x(point.stamp), valueY = y(point.value);
                        if (type === "column") {
                            const barX = at + (trackIndex - tracks.length / 2) * barWidth;
                            ctx.globalAlpha = track.automatic ? 0.6 : 0.9;
                            ctx.fillRect(barX, Math.min(valueY, y(baseline)), barWidth * 0.9, Math.max(1, Math.abs(y(baseline) - valueY)));
                        } else { ctx.beginPath(); ctx.arc(at, valueY, track.automatic ? 3.5 : 2.5, 0, Math.PI * 2); if (track.automatic) ctx.stroke(); else ctx.fill(); }
                    }
                    ctx.globalAlpha = 1; return;
                }
                // Finish each contiguous segment separately to preserve missing-value gaps.
                let segment = [];
                const finish = () => {
                    if (!segment.length) return;
                    if (type === "area") {
                        ctx.beginPath(); ctx.moveTo(x(segment[0].stamp), y(baseline));
                        for (const point of segment) ctx.lineTo(x(point.stamp), y(point.value));
                        ctx.lineTo(x(segment.at(-1).stamp), y(baseline)); ctx.closePath();
                        ctx.globalAlpha = 0.12; ctx.fill(); ctx.globalAlpha = 1;
                    }
                    ctx.beginPath();
                    segment.forEach((point, index) => ctx[index ? "lineTo" : "moveTo"](x(point.stamp), y(point.value)));
                    ctx.stroke();
                    if (segment.length === 1 || !comparison && track.points.length < 200) for (const point of segment) { ctx.beginPath(); ctx.arc(x(point.stamp), y(point.value), 2.5, 0, Math.PI * 2); ctx.fill(); }
                    segment = [];
                };
                for (const point of track.points) { if (point.value === null) finish(); else segment.push(point); }
                finish();
            });
            ctx.restore(); ctx.setLineDash([]); ctx.fillStyle = "#526575";
            if (comparison && last !== first) {
                for (let index = 0; index <= xDivisions; index++) {
                    ctx.textAlign = index === 0 ? "left" : index === xDivisions ? "right" : "center";
                    ctx.fillText(label(first + (last - first) * index / xDivisions), left + (right - left) * index / xDivisions, height - 22, (right - left) / xDivisions - 4);
                }
            } else {
                ctx.textAlign = "left"; ctx.fillText(label(first), left, height - 22, (right - left) / 2);
                ctx.textAlign = "right"; if (last !== first) ctx.fillText(label(last), right, height - 22, (right - left) / 2);
            }
            const showValues = index => {
                selectedPoint = Math.max(0, Math.min(times.length - 1, index));
                const stamp = times[selectedPoint];
                canvas.parentElement.querySelector(".analysis-point-value")?.remove();
                const tip = document.createElement("div"); tip.className = "analysis-point-value"; tip.setAttribute("role", "status");
                const heading = document.createElement("strong"); heading.textContent = new Date(stamp).toLocaleString();
                const close = document.createElement("button"); close.type = "button"; close.textContent = "x"; close.title = "Close values"; close.setAttribute("aria-label", "Close values"); close.onclick = () => tip.remove();
                tip.append(heading, close);
                for (const track of tracks) {
                    const point = track.points.find(point => point.stamp === stamp);
                    const value = document.createElement("p");
                    value.textContent = `${track.label}: ${point?.value == null ? "No reading" : String(point.value)}`;
                    tip.append(value);
                }
                canvas.parentElement.append(tip);
            };
            canvas.tabIndex = 0;
            canvas.onclick = event => {
                const requested = from + Math.max(0, Math.min(1, (event.clientX - canvas.getBoundingClientRect().left - left) / (right - left))) * (to - from);
                let low = 0, high = times.length - 1;
                while (low < high) { const middle = Math.floor((low + high) / 2); if (times[middle] < requested) low = middle + 1; else high = middle; }
                if (low > 0 && requested - times[low - 1] < times[low] - requested) low--;
                showValues(low);
            };
            canvas.onkeydown = event => {
                if (!["Enter", " ", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
                event.preventDefault(); showValues(selectedPoint + (event.key === "ArrowLeft" ? -1 : event.key === "ArrowRight" ? 1 : 0));
            };
        };
        charts.set(canvas, draw); observer.observe(canvas); draw();
    }

    function rose(canvas, data, max, color) {
        const bins = Array.isArray(data?.bins) ? data.bins : [];
        const draw = () => {
            const surface = canvasContext(canvas); if (!surface) return;
            const {ctx, width, height} = surface; const cx = width / 2, cy = height / 2; const radius = Math.min(width, height) / 2 - 38;
            if (bins.length !== 16 || !bins.every(value => number(value) !== null) || !number(data.total)) {
                ctx.textAlign = "center"; ctx.fillText("No wind direction data.", cx, cy); return;
            }
            for (let ring = 1; ring <= 4; ring++) {
                ctx.strokeStyle = "#d6e1e6"; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(cx, cy, radius * ring / 4, 0, Math.PI * 2); ctx.stroke();
            }
            bins.forEach((count, index) => {
                const direction = index * Math.PI / 8 - Math.PI / 2;
                const length = max > 0 ? radius * Math.sqrt(Number(count) / max) : 0;
                ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, length, direction - Math.PI / 18, direction + Math.PI / 18); ctx.closePath(); ctx.fillStyle = color; ctx.fill();
            });
            ctx.fillStyle = "#526575"; ctx.textAlign = "center";
            ["N", "NE", "E", "SE", "S", "SW", "W", "NW"].forEach((label, index) => {
                const angle = index * Math.PI / 4 - Math.PI / 2;
                ctx.fillText(label, cx + Math.cos(angle) * (radius + 20), cy + Math.sin(angle) * (radius + 20) + 4);
            });
            canvas.setAttribute("aria-label", `${canvas.closest("figure").querySelector("figcaption").textContent} wind rose; N clockwise in 16 sectors: ${bins.join(", ")}; calm ${text(data.calm)}; total ${text(data.total)}; shared outer count ${display(max)}.`);
        };
        charts.set(canvas, draw); observer.observe(canvas); draw();
    }

    function seriesCharts(data) {
        const host = field(comparison ? "comparisonChart" : "rainfallChart").closest(".analysis-chart-panel");
        let container = host.querySelector(".analysis-grouped-charts");
        if (!container) { container = document.createElement("div"); container.className = "analysis-grouped-charts"; host.append(container); }
        host.querySelectorAll(":scope > .analysis-canvas, :scope > .analysis-series-grid, :scope > .analysis-legend").forEach(element => { element.hidden = true; });
        const groups = new Map(), hidden = new Set();
        const source = data.series || [], fallback = data.rows?.[0] || {};
        const identities = new Set((data.rows || []).map(row => JSON.stringify([row.site, row.parameter])));
        const colorFor = id => {
            let hash = 0; for (const char of id) hash = (Math.imul(hash, 31) + char.charCodeAt(0)) | 0;
            return `hsl(${(hash >>> 0) % 360}, 65%, 38%)`;
        };
        for (const point of source) {
            if (comparison && identities.size > 1 && (point.site == null || point.parameter == null)) continue;
            const parameter = String(point.parameter_id ?? point.parameter ?? fallback.parameter ?? "");
            const definition = options?.comparisons?.find(item => String(item.id) === parameter);
            const wind = comparison ? /wind/i.test(parameter + " " + (point.parameter || definition?.label || "")) : ["196", "1092"].includes(String(point.element)) || number(point.wind) !== null;
            const kind = comparison ? parameter : wind ? "wind" : "rainfall";
            const id = JSON.stringify([comparison ? point.site_id ?? point.site ?? fallback.site : point.station_code ?? point.station_name, comparison ? parameter : point.element, wind]);
            if (!groups.has(id)) {
                const name = comparison ? point.parameter ?? definition?.label ?? fallback.parameter ?? parameter : wind ? "Wind" : "Rainfall";
                const unit = point.unit ?? point.units ?? definition?.unit ?? definition?.units ?? "";
                const label = comparison ? `${point.site ?? fallback.site ?? "Selected site"} / ${name}` : `${point.station_name || point.station_code || "Selected stations"} / ${point.element || ""} / ${name}`;
                const keys = comparison ? ["manual", "automatic"] : [wind ? "wind" : "rainfall"];
                const manualElement = point.manual_element ?? definition?.manual_element;
                const automaticElement = point.automatic_element ?? definition?.automatic_element;
                const pairColors = [["#5b9bd5", "#ed7d31"], ["#548235", "#c0504d"], ["#8064a2", "#bf9000"], ["#00899b", "#bf568b"]][groups.size % 4];
                groups.set(id, {id, label, wind, kind, unit, tracks: keys.map(key => ({
                    id: id + key, key, scaleKey: kind, color: comparison ? pairColors[key === "automatic" ? 1 : 0] : colorFor(id), automatic: key === "automatic",
                    label: `${label} / ${comparison ? key === "manual" ? `Manual${manualElement ? ` ${manualElement}` : ""}` : `Automatic${automaticElement ? ` ${automaticElement}` : ""}` : "Observed"}${unit ? ` (${unit})` : ""}`, points: []
                }))});
            }
            const stamp = Date.parse(String(point.time).replace(" ", "T")); if (!Number.isFinite(stamp)) continue;
            for (const track of groups.get(id).tracks) {
                const value = number(point[track.key]); track.points.push({stamp, value});
            }
        }
        for (const group of groups.values()) for (const track of group.tracks) track.points.sort((a, b) => a.stamp - b.stamp);
        let controls = host.querySelector(".analysis-series-controls");
        if (!controls) {
            controls = document.createElement("div"); controls.className = "analysis-series-controls";
            const control = (id, labelText, element) => {
                const wrap = document.createElement("div"); wrap.className = "field";
                const label = document.createElement("label"); label.htmlFor = id; label.textContent = labelText;
                element.id = id; wrap.append(label, element); controls.append(wrap); return element;
            };
            const type = control("chartType", "Chart type", document.createElement("select"));
            for (const value of ["line", "column", "area", "scatter"]) type.append(new Option(value[0].toUpperCase() + value.slice(1), value));
            host.insertBefore(controls, container);
        }
        const type = field("chartType");
        const release = () => {
            container.querySelectorAll("canvas").forEach(canvas => { observer.unobserve(canvas); charts.delete(canvas); });
            container.replaceChildren();
        };
        const draw = () => {
            release();
            const focused = [...groups.values()];
            for (const wind of [false, true]) {
                const selected = focused.filter(group => group.wind === wind);
                if (!selected.length) continue;
                const allTracks = selected.flatMap(group => group.tracks);
                const figure = document.createElement("figure"); figure.dataset.chartKind = wind ? "wind" : "nonwind";
                const caption = document.createElement("figcaption");
                caption.textContent = selected.length === 1 ? selected[0].label : wind ? "Wind" : "Station observations";
                const wrap = document.createElement("div"); wrap.className = "analysis-canvas";
                const canvas = document.createElement("canvas"); canvas.setAttribute("role", "img"); wrap.append(canvas);
                const legend = document.createElement("div"); legend.className = "analysis-track-legend"; legend.setAttribute("role", "group"); legend.setAttribute("aria-label", `${wind ? "Wind" : "Non-wind"} series visibility`);
                const availability = document.createElement("div"); availability.className = "analysis-chart-availability"; availability.setAttribute("role", "status");
                const redraw = () => {
                    const visible = allTracks.filter(track => !hidden.has(track.id));
                    plot(canvas, visible, caption.textContent, type.value);
                };
                for (const track of allTracks) {
                    const readingCount = track.points.reduce((count, point) => count + (point.value !== null ? 1 : 0), 0);
                    if (!readingCount) {
                        const warning = document.createElement("p");
                        warning.textContent = `No readings for ${track.label}. This curve cannot be drawn for the selected dates.`;
                        availability.append(warning);
                    }
                    const label = document.createElement("label"), checkbox = document.createElement("input"), swatch = document.createElement("span");
                    checkbox.type = "checkbox"; checkbox.checked = !hidden.has(track.id);
                    checkbox.addEventListener("change", () => { if (checkbox.checked) hidden.delete(track.id); else hidden.add(track.id); redraw(); });
                    swatch.className = "analysis-track-swatch"; swatch.style.borderColor = track.color; if (!comparison && track.automatic) swatch.style.borderTopStyle = "dashed";
                    const name = document.createElement("span"); name.className = "analysis-track-name"; name.textContent = `${track.label} / ${readingCount} readings`;
                    label.append(checkbox, swatch, name); legend.append(label);
                }
                availability.hidden = !availability.childElementCount;
                figure.append(caption, wrap, availability, legend); container.append(figure); redraw();
            }
            if (!container.childElementCount) {
                const empty = document.createElement("p"); empty.className = "analysis-scope";
                empty.textContent = groups.size ? "No chart series match your search." : "No series observations in this period."; container.append(empty);
            }
        };
        type.onchange = draw;
        draw();
    }

    function clearCharts() { observer.disconnect(); charts.clear(); }

    function render(data) {
        clearCharts();
        document.querySelectorAll("[data-metric]").forEach(element => { element.textContent = display(data.summary?.[element.dataset.metric]); });
        const warnings = field("analysisWarnings"); warnings.replaceChildren();
        for (const warning of data.warnings || []) { const item = document.createElement("li"); item.textContent = String(warning); warnings.append(item); }
        warnings.hidden = !warnings.childElementCount;
        const station = row => row.station_name ? `${row.station_code || row.station || ""} - ${row.station_name}` : row.station_code ?? row.station;
        if (comparison) {
            const stationName = code => options?.stations?.find(station => station.station_code === code)?.station_name || code;
            table("comparisonRows", data.rows, ["site", row => stationName(row.manual_station), row => stationName(row.automatic_station), "parameter", "time", row => display(row.manual), row => display(row.automatic), row => display(number(row.difference) === null ? null : Math.abs(number(row.difference)))]);
            const roses = data.roses || {};
            const max = Math.max(0, ...(roses.manual?.bins || []).map(value => number(value) || 0), ...(roses.automatic?.bins || []).map(value => number(value) || 0));
            for (const [name, color] of [["manual", "#147d70"], ["automatic", "#aa4168"]]) {
                const selectedIds = new Set((parameters().get("pair_id") || "").split("|").filter(Boolean));
                const pairs = (options?.pairs || []).filter(pair => !selectedIds.size || selectedIds.has(pair.id));
                const names = [...new Set(pairs.map(pair => pair[`${name}_name`] || stationName(pair[`${name}_code`])))];
                field(`${name}Rose`).closest("figure").querySelector("figcaption").textContent = names.join(", ") || (name === "manual" ? "Manual stations" : "Automatic stations");
                rose(field(`${name}Rose`), roses[name], max, color);
                field(`${name}RoseCaption`).textContent = roses[name] ? `Calm: ${display(roses[name].calm)} | Total: ${display(roses[name].total)} | Shared outer count: ${display(max)}` : "No wind direction data.";
            }
            let stats = field("parameterStatistics");
            if (!stats) {
                stats = document.createElement("section"); stats.id = "parameterStatistics";
                const heading = document.createElement("h2"); heading.textContent = "Statistics by parameter";
                const wrap = document.createElement("div"); wrap.className = "table-wrap";
                const grid = document.createElement("table"); const header = grid.createTHead().insertRow();
                for (const label of ["Parameter", "R\u00b2"]) { const cell = document.createElement("th"); cell.scope = "col"; cell.textContent = label; if (label !== "Parameter") cell.title = "R-squared of the fitted regression line: automatic = slope x manual + intercept"; header.append(cell); }
                grid.createTBody().id = "parameterStatisticRows"; wrap.append(grid); stats.append(heading, wrap); document.querySelector(".dashboard-content").append(stats); enableLocalTableSorting(grid); grid.dataset.serverSorted = "true";
            }
            const perParameter = data.per_parameter || data.summary?.per_parameter || [];
            const records = Array.isArray(perParameter) ? perParameter : Object.entries(perParameter).map(([parameter, values]) => ({parameter, ...values}));
            stats.hidden = !records.length;
            table("parameterStatisticRows", records, [row => row.parameter ?? row.parameter_id, row => display(row.r_squared, 4)]);
            const mixed = new Set((data.rows || []).map(row => row.parameter)).size > 1;
            document.querySelector('[data-metric="r_squared"]').textContent = mixed ? "See by parameter" : display(records[0]?.r_squared ?? data.summary?.r_squared, 4);
        } else {
            window.renderMonitoringHeatmaps?.(data);
            table("rainRows", data.rain_rows, [station, "element", "time", row => display(row.value)]);
            const count = row => display(String(row.element) === "5" ? row.manual_count : row.automatic_count ?? row.count);
            table("dailyRows", data.daily_max, [station, row => (row.time ?? row.date)?.slice(0, 10), row => display(row.value), count]);
            table("windRows", data.wind_max, [station, "time", row => display(row.value)]);
            table("dailyObservationRows", data.daily_rows, [station, "element", row => row.time?.slice(0, 10), row => display(row.value), count]);
        }
        seriesCharts(data);
    }

    async function load() {
        if (!options || !validate()) return;
        controller?.abort(); controller = new AbortController();
        const request = ++sequence; const query = parameters();
        if (comparison && field("comparisonFilter").options.length === 1) {
            applied = null; render({}); field("analysisCsv").disabled = true;
            field("applyAnalysis").disabled = false;
            document.querySelector("main").setAttribute("aria-busy", "false");
            field("analysisScope").textContent = "No comparable elements in the saved observations for these stations.";
            setMessage(""); return;
        }
        const started = Date.now();
        if (!comparison) {
            window.renderMonitoringMaps?.({}, options);
            for (const kind of ["rainfall", "wind"]) field(`${kind}GeographicMap`).textContent = "Loading all-station map...";
        }
        field("applyAnalysis").disabled = true; field("analysisCsv").disabled = true;
        document.querySelector("main").setAttribute("aria-busy", "true");
        const progress = () => setMessage(`Loading observation analysis (${Math.floor((Date.now() - started) / 1000)}s elapsed). Building the first observation index may take several minutes.`);
        progress(); const timer = window.setInterval(progress, 1000);
        try {
            const response = await checked(await apiFetch(`${endpoint}?${query}`, {signal: controller.signal}), "Unable to load observation analysis.");
            const data = await response.json(); if (request !== sequence) return;
            render(data);
            for (const key of ["start", "end"]) if (!query.has(key) && data[key]) {
                const local = data[key + "_day"] || (key === "end" ? new Date(Date.parse(String(data[key]).slice(0, 10) + "T00:00:00Z") - 86400000).toISOString().slice(0, 10) : String(data[key]).slice(0, 10));
                query.set(key, local);
                if (!field(key === "start" ? "analysisStart" : "analysisEnd").value) field(key === "start" ? "analysisStart" : "analysisEnd").value = local;
            }
            applied = new URLSearchParams(query); field("analysisScope").textContent = scope(applied);
            field("analysisCsv").disabled = false;
            setMessage("");
            if (!comparison) await loadMaps(query, request);
        } catch (error) {
            if (request !== sequence || error.name === "AbortError") return;
            applied = null; render({}); field("analysisScope").textContent = "No analysis loaded.";
            setMessage(error.message, true);
            if (!comparison) await loadMaps(query, request);
        } finally {
            window.clearInterval(timer);
            if (request === sequence) { field("applyAnalysis").disabled = false; document.querySelector("main").setAttribute("aria-busy", "false"); }
        }
    }

    async function loadMaps(query, request) {
        const dates = new URLSearchParams({days: "true"});
        for (const key of ["start", "end"]) if (query.has(key)) dates.set(key, query.get(key));
        try {
            const response = await checked(await apiFetch(`/observation-analysis/maps?${dates}`, {signal: controller.signal}), "Unable to load all-station maps.");
            const data = await response.json(); if (request !== sequence) return;
            await window.renderMonitoringMaps?.(data, {stations: data.stations || options.stations});
        } catch (error) {
            if (request !== sequence || error.name === "AbortError") return;
            for (const kind of ["rainfall", "wind"]) field(`${kind}GeographicMap`).textContent = error.message;
        }
    }

    function setDefaults() {
        field("analysisFilters").reset();
        if (comparison) { field("pairFilter").value = defaults.pair; updateElementChoices(); field("comparisonFilter").value = [...field("comparisonFilter").options].some(option => option.value === defaults.parameter) ? defaults.parameter : ""; }
        else { field("stationFilter").value = ""; field("analysisPeriod").value = "day"; }
        field("analysisEnd").setCustomValidity("");
    }

    function updateElementChoices() {
        const selected = new Set(field("pairFilter").value.split("|").filter(Boolean));
        const pairs = (options?.pairs || []).filter(pair => !selected.size || selected.has(pair.id));
        const available = new Set(pairs.flatMap(pair => pair.comparison_ids || (options.comparisons || []).map(rule => rule.id)));
        const select = field("comparisonFilter");
        const previous = select.value.split("|").filter(id => available.has(id));
        select.replaceChildren(new Option(available.size ? "All element pairs" : "No comparable elements", ""),
            ...(options.comparisons || []).filter(rule => available.has(rule.id)).map(rule =>
                new Option(`${rule.label} (${rule.manual_element} / ${rule.automatic_element}; ${rule.method})`, rule.id)));
        select.value = previous.join("|");
    }

    async function loadOptions() {
        field("retryOptions").hidden = true; field("applyAnalysis").disabled = true;
        setMessage("Loading observation options. Building the first observation index may take several minutes.");
        try {
            const response = await checked(await apiFetch("/observation-analysis/options"), "Unable to load observation options.");
            options = await response.json();
            const fill = (id, values, label, value) => {
                const select = field(id); select.replaceChildren(new Option(comparison ? (id === "pairFilter" ? "All pairs" : "All element pairs") : "All stations", ""));
                values.forEach(item => select.append(new Option(label(item), String(item[value]))));
                enhanceMultiFilter(select);
            };
            if (comparison) {
                fill("pairFilter", options.pairs || [], pair => pair.label, "id");
                fill("comparisonFilter", options.comparisons || [], item => `${item.label} (${item.manual_element} / ${item.automatic_element}; ${item.method})`, "id");
                const rank = pair => {
                    const manual = String(pair.manual_category || "").toLowerCase(); const automatic = String(pair.automatic_category || "").toLowerCase();
                    const aws = /automatic.*weather|\baws\b/.test(automatic);
                    return aws && /principal/.test(manual) ? 0 : aws && /climatic/.test(manual) ? 1 : 2;
                };
                const pairs = [...(options.pairs || [])].sort((a, b) => rank(a) - rank(b) || String(a.label).localeCompare(String(b.label)));
                const first = pairs[0]; const arg = /\barg\b|rain.*gauge/.test(String(first?.automatic_category || "").toLowerCase());
                defaults = {pair: first ? String(first.id) : "", parameter: arg ? "rainfall" : "temperature"};
                if (!(options.comparisons || []).some(item => item.id === defaults.parameter)) defaults.parameter = String(options.comparisons?.[0]?.id || "");
            } else fill("stationFilter", options.stations || [], station => `${station.station_code} - ${station.station_name}${station.station_category ? ` (${station.station_category})` : ""}`, "station_code");
            table("analysisRunRows", options.runs, ["run_id", "filename", "created_at"], "No observation uploads available.");
            setDefaults(); await load();
        } catch (error) { setMessage(error.message, true); field("retryOptions").hidden = false; }
    }

    document.addEventListener("DOMContentLoaded", async () => {
        initializeShell(); if (!await requireSession()) return;
        for (const id of ["analysisStart", "analysisEnd"]) field(id).addEventListener("input", () => field("analysisEnd").setCustomValidity(""));
        field("analysisFilters").addEventListener("submit", event => { event.preventDefault(); load(); });
        if (comparison) field("pairFilter").addEventListener("change", updateElementChoices);
        field("resetAnalysis").addEventListener("click", () => { setDefaults(); load(); });
        field("retryOptions").addEventListener("click", loadOptions);
        document.querySelectorAll(".analysis-chart-panel").forEach(panel => panel.addEventListener("toggle", () => charts.forEach(draw => draw())));
        field("analysisFilters").addEventListener("change", () => { if (applied) field("analysisScope").textContent = `${scope(applied)} | Filter changes pending Apply`; });
        field("analysisCsv").addEventListener("click", async () => {
            if (!applied) return;
            const query = new URLSearchParams(applied); query.set("format", "csv"); field("analysisCsv").disabled = true;
            try {
                const response = await checked(await apiFetch(`${endpoint}?${query}`), "Unable to download filtered CSV.");
                const url = URL.createObjectURL(await response.blob()); const link = document.createElement("a");
                link.href = url; link.download = comparison ? "manual-automatic.csv" : "rainfall-wind.csv"; document.body.append(link); link.click(); link.remove();
                window.setTimeout(() => URL.revokeObjectURL(url), 1000); setMessage("Filtered CSV downloaded.");
            } catch (error) { setMessage(error.message, true); }
            finally { field("analysisCsv").disabled = !applied; }
        });
        await loadOptions();
    });
})();
