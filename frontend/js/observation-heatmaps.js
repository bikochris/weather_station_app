(() => {
    "use strict";
    const palettes = {
        rainfall: ["#eff6ff", "#bfdbfe", "#60a5fa", "#2563eb", "#1e40af", "#172554"],
        wind: ["#fff7ed", "#fed7aa", "#fdba74", "#f97316", "#c2410c", "#7c2d12"]
    };
    const format = value => value.toLocaleString(undefined, {maximumFractionDigits: 3});
    const numeric = value => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));

    function heatmap(host, series, kind) {
        if (!host) return;
        host.replaceChildren();
        const rows = new Map(), times = new Set();
        let min = Infinity, max = -Infinity;
        for (const point of series) {
            if (!numeric(point[kind])) continue;
            const value = Number(point[kind]);
            const key = JSON.stringify([point.station_code, point.element]);
            if (!rows.has(key)) rows.set(key, {name: point.station_name || point.station_code,
                element: point.element, values: new Map()});
            rows.get(key).values.set(point.time, value);
            times.add(point.time); min = Math.min(min, value); max = Math.max(max, value);
        }
        if (!rows.size) {
            const empty = document.createElement("p"); empty.className = "analysis-scope";
            empty.textContent = `No ${kind} readings for the applied filters.`; host.append(empty); return;
        }
        const stations = [...rows.values()].sort((a, b) => String(a.name).localeCompare(String(b.name)) || String(a.element).localeCompare(String(b.element)));
        const stamps = [...times].sort();
        const controls = document.createElement("div"); controls.className = "heatmap-controls";
        let rowPage = 0, timePage = 0;
        const rowSize = 20, timeSize = 14;
        const navigators = [];
        for (const dimension of ["Stations", "Periods"]) {
            const group = document.createElement("div"); group.className = "heatmap-pager";
            const label = document.createElement("span"); label.setAttribute("aria-live", "polite");
            const buttons = [-1, 1].map(step => {
                const button = document.createElement("button"); button.type = "button";
                button.className = "secondary-button"; button.textContent = step < 0 ? "\u2190" : "\u2192";
                button.title = `${step < 0 ? "Previous" : "Next"} ${dimension.toLowerCase()}`;
                button.setAttribute("aria-label", `${kind}: ${button.title}`);
                button.onclick = () => { if (dimension === "Stations") rowPage += step; else timePage += step; draw(); };
                return button;
            });
            group.append(buttons[0], label, buttons[1]); controls.append(group);
            navigators.push({dimension, label, buttons});
        }
        const legend = document.createElement("div"); legend.className = "heatmap-legend";
        const scaleLabel = document.createElement("span"); scaleLabel.textContent = `${format(min)} - ${format(max)}`;
        legend.append(scaleLabel);
        palettes[kind].forEach(color => { const swatch = document.createElement("span"); swatch.className = "heatmap-swatch"; swatch.style.background = color; swatch.setAttribute("aria-hidden", "true"); legend.append(swatch); });
        const missing = document.createElement("span"); missing.className = "heatmap-no-data"; missing.textContent = "No reading"; legend.append(missing);
        const viewport = document.createElement("div"); viewport.className = "heatmap-scroll";
        viewport.tabIndex = 0; viewport.setAttribute("role", "region"); viewport.setAttribute("aria-label", `${kind} heatmap`);
        const detail = document.createElement("p"); detail.className = "analysis-scope"; detail.setAttribute("role", "status"); detail.hidden = true;
        host.append(controls, legend, viewport, detail);
        function draw() {
            const table = document.createElement("table"); table.className = "heatmap-table";
            const caption = document.createElement("caption"); caption.textContent = kind === "rainfall" ? "Rainfall totals by station and period" : "Maximum hourly wind by station and period"; table.append(caption);
            const header = table.createTHead().insertRow();
            const stationHeading = document.createElement("th"); stationHeading.scope = "col"; stationHeading.textContent = "Station / element"; header.append(stationHeading);
            const visibleTimes = stamps.slice(timePage * timeSize, (timePage + 1) * timeSize);
            for (const stamp of visibleTimes) {
                const heading = document.createElement("th"); heading.scope = "col";
                heading.textContent = stamp.replace("T", " "); header.append(heading);
            }
            const body = table.createTBody();
            for (const station of stations.slice(rowPage * rowSize, (rowPage + 1) * rowSize)) {
                const row = body.insertRow(); const heading = document.createElement("th"); heading.scope = "row";
                heading.textContent = `${station.name} / ${station.element}`; row.append(heading);
                for (const stamp of visibleTimes) {
                    const cell = row.insertCell(); const value = station.values.get(stamp);
                    if (value === undefined) { cell.className = "heatmap-no-data"; cell.textContent = "-"; cell.title = `${station.name} / ${station.element} | ${stamp} | No reading`; continue; }
                    const level = max === min ? 0 : Math.min(5, Math.max(0, Math.floor((value - min) / (max - min) * 6)));
                    const button = document.createElement("button"); button.type = "button"; button.textContent = format(value);
                    button.style.background = palettes[kind][level]; button.style.color = level >= 3 ? "#fff" : "#17212b";
                    button.title = `${station.name} / ${station.element} | ${stamp} | ${kind}: ${String(value)}`;
                    button.setAttribute("aria-label", button.title); button.dataset.value = String(value);
                    button.onclick = () => { detail.textContent = button.title; detail.hidden = false; };
                    cell.append(button);
                }
            }
            viewport.replaceChildren(table); viewport.scrollLeft = 0; detail.hidden = true;
            for (const {dimension, label, buttons} of navigators) {
                const page = dimension === "Stations" ? rowPage : timePage;
                const size = dimension === "Stations" ? rowSize : timeSize;
                const count = dimension === "Stations" ? stations.length : stamps.length;
                label.textContent = `${dimension} ${page * size + 1}-${Math.min(count, (page + 1) * size)} of ${count}`;
                buttons[0].disabled = page === 0; buttons[1].disabled = (page + 1) * size >= count;
            }
        }
        draw();
    }
    window.renderMonitoringHeatmaps = data => {
        for (const kind of ["rainfall", "wind"]) heatmap(document.getElementById(`${kind}Heatmap`), data.series || [], kind);
    };
})();
