(() => {
    "use strict";
    const states = new Map();
    let boundaries;
    let generation = 0;
    const colors = {rainfall: ["#e8f9bf", "#caf575", "#a6df24", "#73b319", "#397a16"],
        wind: ["#fff0c2", "#ffd26a", "#f69b37", "#dd591f", "#982d1c"]};
    const numeric = value => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
    const format = value => Number(value).toLocaleString(undefined, {maximumFractionDigits: 3});
    const loadBoundaries = () => boundaries ||= fetch("assets/maps/rwanda-districts.geojson").then(response => {
        if (!response.ok) throw new Error("Rwanda boundaries could not be loaded. Refresh to retry.");
        return response.json();
    }).catch(error => { boundaries = null; throw error; });

    function aggregate(data, stations, kind) {
        const registry = new Map(stations.map(station => [String(station.station_code), station]));
        const grouped = new Map();
        // Rainfall totals use the same 08:00-08:00 daily values as the records.
        const source = kind === "rainfall" ? data.daily_rows || [] : data.series || [];
        for (const row of source) {
            const raw = kind === "rainfall" ? row.value : row.wind;
            const station = registry.get(String(row.station_code));
            if (!station) continue;
            const key = JSON.stringify([row.station_code, row.element]);
            if (!grouped.has(key)) grouped.set(key, {station, name: station?.station_name || row.station_name || row.station_code,
                element: String(row.element), value: null, count: 0});
            const point = grouped.get(key);
            if (!numeric(raw)) continue;
            point.value = kind === "rainfall" ? (point.value ?? 0) + Number(raw) : Math.max(point.value ?? -Infinity, Number(raw));
            point.count++;
        }
        return [...grouped.values()];
    }

    function location(point) {
        const station = point.station;
        if (!station || !numeric(station.latitude) || !numeric(station.longitude)) return null;
        if ([station.latitude, station.longitude].some(value => typeof value === "string" && !value.trim())) return null;
        const lat = Number(station.latitude), lng = Number(station.longitude);
        return Math.abs(lat) <= 90 && Math.abs(lng) <= 180 ? [lng, lat] : null;
    }

    function pathFor(geometry, project) {
        const path = new Path2D();
        const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
        for (const polygon of polygons) for (const ring of polygon) {
            ring.forEach((coord, index) => { const p = project(coord); path[index ? "lineTo" : "moveTo"](p.x, p.y); });
            path.closePath();
        }
        return path;
    }

    function surface(geo, points, palette, low, high) {
        const bbox = turf.bbox(geo);
        const sw = L.CRS.EPSG3857.project(L.latLng(bbox[1], bbox[0]));
        const ne = L.CRS.EPSG3857.project(L.latLng(bbox[3], bbox[2]));
        const canvas = document.createElement("canvas"); canvas.width = 1000;
        canvas.height = Math.round(1000 * (ne.y - sw.y) / (ne.x - sw.x));
        const ctx = canvas.getContext("2d");
        const project = coord => {
            const p = L.CRS.EPSG3857.project(L.latLng(coord[1], coord[0]));
            return {x: (p.x - sw.x) / (ne.x - sw.x) * canvas.width, y: (ne.y - p.y) / (ne.y - sw.y) * canvas.height};
        };
        const country = new Path2D();
        for (const feature of geo.features) country.addPath(pathFor(feature.geometry, project));
        const coLocated = new Map();
        for (const point of points) {
            const coord = location(point), key = JSON.stringify(coord);
            if (!coLocated.has(key)) coLocated.set(key, {coord, sum: 0, count: 0});
            const group = coLocated.get(key); group.sum += point.value; group.count++;
        }
        const features = turf.featureCollection([...coLocated.values()].map(group => turf.point(group.coord, {value: group.sum / group.count})));
        const hull = features.features.length >= 3 ? turf.convex(features) : null;
        ctx.save(); ctx.clip(country, "evenodd"); ctx.fillStyle = "#f1f4f0"; ctx.fillRect(0, 0, canvas.width, canvas.height);
        let estimated = false;
        if (hull) {
            ctx.save(); ctx.clip(pathFor(hull.geometry, project));
            if (low === high) { ctx.fillStyle = palette[0]; ctx.fillRect(0, 0, canvas.width, canvas.height); }
            else {
                const grid = turf.interpolate(features, 2, {gridType: "point", property: "value", weight: 2, bbox});
                const breaks = Array.from({length: 6}, (_, index) => low + (high - low) * index / 5);
                // Small outer padding includes observations equal to the extrema.
                breaks[0] -= (high - low) * 1e-8; breaks[5] += (high - low) * 1e-8;
                const bands = turf.isobands(grid, breaks, {zProperty: "value"});
                bands.features.forEach((feature, index) => { ctx.fillStyle = palette[index]; ctx.fill(pathFor(feature.geometry, project), "evenodd"); });
            }
            ctx.restore(); estimated = true;
        }
        ctx.restore();
        return {canvas, project, bbox, estimated, sites: coLocated.size};
    }

    function create(host, geo, points, kind, range) {
        const old = states.get(kind);
        if (old) { old.observer.disconnect(); old.map.remove(); states.delete(kind); }
        host.replaceChildren();
        const heading = document.createElement("h3"); heading.className = "weather-map-title";
        heading.textContent = `${kind === "rainfall" ? "Rainfall total" : "Maximum hourly wind speed"} | ${range}`;
        const toolbar = document.createElement("div"); toolbar.className = "weather-map-toolbar";
        toolbar.textContent = "All accessible stations | Manual station preferred at shared sites";
        const mapHost = document.createElement("div"); mapHost.className = "weather-geographic-map";
        mapHost.setAttribute("aria-label", `${kind} geographic map`);
        const legend = document.createElement("div"); legend.className = "weather-map-legend";
        const notice = document.createElement("p"); notice.className = "analysis-scope"; notice.setAttribute("role", "status");
        host.append(heading, toolbar, mapHost, legend, notice);
        const map = L.map(mapHost, {scrollWheelZoom: false, zoomSnap: 0.25}).setView([-1.94, 29.87], 8);
        map.attributionControl.addAttribution('<a href="https://www.geoboundaries.org/">geoBoundaries</a> / Open Data Rwanda (CC BY 4.0)');
        L.control.scale({imperial: false}).addTo(map);
        const north = L.control({position: "topright"});
        north.onAdd = () => { const element = L.DomUtil.create("div", "weather-map-north"); element.textContent = "N\n\u2191"; element.setAttribute("aria-label", "North"); return element; }; north.addTo(map);
        const bounds = L.geoJSON(geo).getBounds();
        const layer = L.layerGroup().addTo(map);
        const observer = new ResizeObserver(() => {
            map.invalidateSize(); if (mapHost.clientWidth && mapHost.clientHeight) map.fitBounds(bounds, {padding: [20, 20], animate: false});
        }); observer.observe(mapHost); states.set(kind, {map, observer});
        function draw() {
            layer.clearLayers(); legend.replaceChildren();
            const chosen = points;
            const located = chosen.filter(location);
            const observed = located.filter(point => numeric(point.value));
            const values = observed.map(point => point.value), low = Math.min(...values), high = Math.max(...values);
            let raster;
            if (observed.length) {
                raster = surface(geo, observed, colors[kind], low, high);
                L.imageOverlay(raster.canvas.toDataURL(), [[raster.bbox[1], raster.bbox[0]], [raster.bbox[3], raster.bbox[2]]], {opacity: 0.95}).addTo(layer);
                colors[kind].forEach((color, index) => {
                    if (low === high && index) return;
                    const item = document.createElement("span"), swatch = document.createElement("i"); swatch.style.background = color;
                    item.append(swatch, document.createTextNode(low === high ? format(low) : `${format(low + (high - low) * index / 5)} - ${format(low + (high - low) * (index + 1) / 5)}`)); legend.append(item);
                });
            }
            if (located.length > observed.length) {
                const item = document.createElement("span"), swatch = document.createElement("i"); swatch.style.background = "#a7afb6";
                item.append(swatch, document.createTextNode("No reading")); legend.append(item);
            }
            L.geoJSON(geo, {style: {color: "#455044", weight: 0.8, fillOpacity: 0}, onEachFeature: (feature, polygon) => {
                const text = document.createElement("span"); text.textContent = feature.properties.shapeName;
                polygon.bindTooltip(text);
                const anchor = turf.pointOnFeature(feature).geometry.coordinates;
                const name = document.createElement("span"); name.textContent = feature.properties.shapeName;
                L.marker([anchor[1], anchor[0]], {interactive: false, keyboard: false,
                    icon: L.divIcon({className: "weather-district-label", html: name, iconSize: [80, 16], iconAnchor: [40, 8]})}).addTo(layer);
            }}).addTo(layer);
            const sites = new Map();
            for (const point of located) {
                const key = JSON.stringify(location(point));
                if (!sites.has(key)) sites.set(key, []);
                sites.get(key).push(point);
            }
            for (const site of sites.values()) {
                const coord = location(site[0]);
                const readings = site.filter(point => numeric(point.value));
                const average = readings.length ? readings.reduce((sum, point) => sum + point.value, 0) / readings.length : null;
                const level = high === low ? 0 : Math.min(4, Math.floor((average - low) / (high - low) * 5));
                const dot = document.createElement("span"); dot.className = "weather-station-dot"; dot.style.background = readings.length ? colors[kind][level] : "#a7afb6";
                const content = document.createElement("div");
                for (const point of site) {
                    const title = document.createElement("strong"); title.textContent = point.name; content.append(title);
                    for (const text of [`Element: ${point.element}`, `${kind === "rainfall" ? "Rainfall total" : "Maximum hourly wind"}: ${numeric(point.value) ? format(point.value) : "No reading"}`, range]) {
                        const line = document.createElement("div"); line.textContent = text; content.append(line);
                    }
                }
                const names = site.map(point => point.name).join(" / ");
                L.marker([coord[1], coord[0]], {title: names, alt: names,
                    icon: L.divIcon({className: "weather-station-marker", html: dot, iconSize: [12, 12], iconAnchor: [6, 6]})}).bindPopup(content).addTo(layer);
            }
            mapHost.dataset.stationCount = String(located.length);
            mapHost.dataset.estimated = String(Boolean(raster?.estimated));
            const method = raster?.estimated ? "IDW estimated surface within station coverage; co-located values averaged for the surface only." : "Station observations only; at least three non-collinear locations are needed for a surface.";
            notice.textContent = located.length ? `${located.length} stations mapped; ${observed.length} with readings. ${method}${located.length < chosen.length ? ` ${chosen.length - located.length} have missing or invalid coordinates.` : ""}` : `No located ${kind} observations for these filters.${chosen.length ? ` ${chosen.length} have missing or invalid coordinates.` : ""}`;
        }
        draw();
    }

    window.renderMonitoringMaps = async (data, options) => {
        const request = ++generation;
        for (const [kind, state] of states) { state.observer.disconnect(); state.map.remove(); document.getElementById(`${kind}GeographicMap`)?.replaceChildren(); }
        states.clear();
        if (!(data.series?.length || data.daily_rows?.length)) {
            for (const kind of ["rainfall", "wind"]) {
                const host = document.getElementById(`${kind}GeographicMap`);
                if (host) host.textContent = `No ${kind} observations for the applied filters.`;
            }
            return;
        }
        const range = `${data.start_day || String(data.start || "").slice(0, 10)} to ${data.end_day || String(data.end || "").slice(0, 10)}`;
        try {
            const geo = await loadBoundaries(); if (request !== generation) return;
            for (const kind of ["rainfall", "wind"]) {
                const host = document.getElementById(`${kind}GeographicMap`); if (!host) continue;
                create(host, geo, aggregate(data, options?.stations || [], kind), kind, range);
            }
        } catch (error) {
            if (request !== generation) return;
            for (const kind of ["rainfall", "wind"]) { const host = document.getElementById(`${kind}GeographicMap`); if (host) host.textContent = error.message; }
        }
    };
})();
