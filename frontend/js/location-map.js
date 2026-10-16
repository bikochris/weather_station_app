/* Shared read-only map; all locations come from the page's permission-filtered results. */
const locationMap = (() => {
    let dialog, map, layer, title, notice, count;
    let tileUnavailable = false;
    const offlineMessage = "Background map unavailable. Check your internet connection. Recorded location markers are still available.";
    const colors = {Operational: "#087f5b", "Under maintenance": "#a85b08", Suspended: "#c92b43", Closed: "#526273"};

    function coordinates(record) {
        const values = [record.latitude, record.longitude];
        if (values.some(value => value == null || String(value).trim() === "")) return null;
        const [lat, lng] = values.map(Number);
        return Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180
            ? [lat, lng] : null;
    }

    function iconButton(label, handler) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "location-map-button";
        button.title = label;
        button.setAttribute("aria-label", label);
        const icon = document.createElement("img");
        icon.src = "assets/icons/map-pinned.svg";
        icon.alt = "";
        button.append(icon);
        button.addEventListener("click", event => { event.stopPropagation(); handler(); });
        return button;
    }

    function initialize() {
        if (dialog) return;
        dialog = document.createElement("dialog");
        dialog.className = "location-map-dialog";
        dialog.setAttribute("aria-labelledby", "locationMapTitle");
        const header = document.createElement("div");
        header.className = "location-map-heading";
        title = document.createElement("h2");
        title.id = "locationMapTitle";
        const close = document.createElement("button");
        close.type = "button";
        close.textContent = "Close";
        close.addEventListener("click", () => dialog.close());
        header.append(title, close);
        count = document.createElement("p");
        count.className = "location-map-count";
        const canvas = document.createElement("div");
        canvas.className = "location-map-canvas";
        canvas.setAttribute("aria-label", "Station and site locations");
        notice = document.createElement("p");
        notice.className = "location-map-notice";
        notice.setAttribute("role", "status");
        const legend = document.createElement("div");
        legend.className = "location-map-legend";
        for (const [status, color] of Object.entries(colors)) {
            const item = document.createElement("span");
            const swatch = document.createElement("i");
            swatch.style.backgroundColor = color;
            const symbol = document.createElement("img");
            symbol.src = "assets/icons/map-pinned.svg";
            symbol.alt = "";
            swatch.append(symbol);
            item.append(swatch, document.createTextNode(status));
            legend.append(item);
        }
        dialog.append(header, count, canvas, legend, notice);
        document.body.append(dialog);
        map = L.map(canvas).setView([-1.94, 29.87], 8);
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        }).on("tileerror", () => {
            tileUnavailable = true;
            notice.textContent = offlineMessage;
        }).addTo(map);
        layer = L.featureGroup().addTo(map);
    }

    function popup(record) {
        const content = document.createElement("div");
        const heading = document.createElement("strong");
        heading.textContent = [record.site_code || record.station_code, record.site_name || record.station_name || "Location"].filter(Boolean).join(" - ");
        content.append(heading);
        const details = [record.station_category, record.status, [record.province, record.district, record.sector].filter(Boolean).join(" / "),
            `${record.latitude}, ${record.longitude}`];
        if (record.altitude != null) details.push(`Elevation: ${record.altitude} m`);
        for (const value of details.filter(Boolean)) {
            const line = document.createElement("div");
            line.textContent = value;
            content.append(line);
        }
        for (const station of record.stations || []) {
            const line = document.createElement("div");
            line.textContent = `${station.station_code} - ${station.station_name} (${station.status})`;
            content.append(line);
        }
        return content;
    }

    function show(records, label) {
        if (typeof L === "undefined") {
            window.alert("The map component could not load. Refresh the page and try again.");
            return;
        }
        initialize();
        title.textContent = label;
        notice.textContent = tileUnavailable ? offlineMessage : "";
        layer.clearLayers();
        const located = records.filter(record => coordinates(record));
        count.textContent = `${located.length} mapped / ${records.length} selected${located.length < records.length ? " - some coordinates are missing or invalid" : ""}`;
        for (const record of located) {
            const statuses = record.stations?.map(station => station.status) || [record.status];
            const status = ["Suspended", "Under maintenance", "Operational", "Closed"].find(value => statuses.includes(value));
            const markerContent = document.createElement("span");
            markerContent.className = "location-marker-dot";
            markerContent.style.setProperty("--marker-color", colors[status] || "#087f5b");
            const markerLabel = `${record.site_name || record.station_name || "Location"} - ${status || "Status unknown"}`;
            const tooltip = document.createElement("span");
            tooltip.textContent = markerLabel;
            L.marker(coordinates(record), {
                title: markerLabel, alt: markerLabel, riseOnHover: true,
                icon: L.divIcon({className: "location-map-marker leaflet-interactive", html: markerContent,
                    iconSize: [16, 16], iconAnchor: [8, 8], popupAnchor: [0, -8], tooltipAnchor: [8, 0]})
            }).bindPopup(popup(record)).bindTooltip(tooltip).addTo(layer);
        }
        if (!dialog.open) dialog.showModal();
        requestAnimationFrame(() => {
            map.invalidateSize();
            if (located.length) map.fitBounds(layer.getBounds(), {padding: [40, 40], maxZoom: 14});
            else map.setView([-1.94, 29.87], 8);
            if (located.length === 1) layer.getLayers()[0].openPopup();
        });
    }

    function appendButton(cell, record) {
        const label = `Show ${record.site_name || record.station_name || "location"} on map`;
        const button = iconButton(label, () => show([record], record.site_name || record.station_name || "Location"));
        button.disabled = !coordinates(record);
        if (button.disabled) button.title = "Map unavailable: coordinates are missing or invalid";
        cell.append(button);
    }

    function setLoading(table) {
        table.closest("section").querySelectorAll(".location-results-map, .location-filtered-map")
            .forEach(button => { button.disabled = true; });
    }

    function bindResults(table, records, label, endpoint, parameters) {
        const section = table.closest("section");
        let button = section.querySelector(".location-results-map");
        if (!button) {
            button = iconButton("Map current results", () => {});
            button.classList.add("location-results-map");
            button.append(document.createTextNode("Map current results"));
            table.closest(".table-wrap").before(button);
        }
        button.onclick = () => show(records, `${label} - current results page`);
        button.disabled = !records.some(record => coordinates(record));
        let filtered = section.querySelector(".location-filtered-map");
        if (!filtered) {
            filtered = iconButton("Map all filtered results", () => {});
            filtered.classList.add("location-filtered-map");
            filtered.append(document.createTextNode("Map all filtered results"));
            button.after(filtered);
            const message = document.createElement("p");
            message.className = "location-map-feedback";
            message.setAttribute("role", "status");
            filtered.after(message);
        }
        const message = section.querySelector(".location-map-feedback");
        message.textContent = "";
        filtered.disabled = !records.length;
        const query = new URLSearchParams(parameters);
        query.delete("page");
        query.delete("page_size");
        filtered.onclick = async () => {
            filtered.disabled = true;
            message.textContent = "Loading all matching locations...";
            try {
                const response = await apiFetch(`${endpoint}?${query}`);
                if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load filtered locations"));
                const results = await response.json();
                if (!Array.isArray(results)) throw new Error("Unable to load the complete filtered results. Refresh and try again.");
                message.textContent = "";
                show(results, `${label} - all filtered results`);
            } catch (error) {
                message.textContent = error.message;
            } finally {
                filtered.disabled = !records.length;
            }
        };
    }
    return {appendButton, bindResults, setLoading, coordinates, show};
})();
