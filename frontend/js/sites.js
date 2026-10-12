const siteCollection = createCollectionState("site_name", "asc");
let siteOrigin = null;

function siteParameters() {
    return {
        district: document.getElementById("districtFilter")?.value || "",
        status: document.getElementById("siteStatusFilter").value,
        ...(siteOrigin || {})
    };
}


function renderSiteSummary(summary) {
    setMetric("totalSitesMetric", summary.total_sites || 0);
    setMetric("siteStationsMetric", summary.stations || 0);
    setMetric("sharedSitesMetric", summary.shared_sites || 0);
    setMetric("singleSitesMetric", summary.single_station_sites || 0);
}


function appendSiteStations(row, site) {
    const cell = appendCell(row, "");
    const list = document.createElement("div");
    list.className = "site-station-list";
    site.stations.forEach((station) => {
        const item = document.createElement("div");
        const name = document.createElement("strong");
        name.textContent = `${station.station_code} - ${station.station_name}`;
        const details = document.createElement("small");
        details.textContent = `${station.station_category} · ${station.status}`;
        item.append(name, details);
        list.appendChild(item);
    });
    cell.appendChild(list);
}


async function loadSites() {
    const table = document.getElementById("siteTable");
    showTableMessage(table, 12, "Loading sites...");
    const parameters = collectionParameters(siteCollection, siteParameters());
    const response = await apiFetch(`/sites?${parameters}`);
    if (!response.ok) {
        showTableMessage(table, 12, await getErrorMessage(response, "Unable to load sites"));
        return;
    }
    const result = await response.json();
    renderSiteSummary(result.summary);
    renderPagination("sitePagination", result, siteCollection, loadSites);
    table.replaceChildren();
    if (!result.items.length) {
        showTableMessage(table, 12, "No sites match the current filters.");
        return;
    }
    result.items.forEach((site) => {
        const row = document.createElement("tr");
        appendCell(row, site.site_code);
        appendCell(row, site.site_name);
        const distance = appendCell(row, siteOrigin ? (site.distance_km == null ? "Unknown" : site.distance_km.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})) : "-");
        distance.title = "Straight-line distance, not road distance";
        appendCell(row, site.latitude);
        appendCell(row, site.longitude);
        appendCell(row, `${site.altitude} m`);
        appendCell(row, site.province);
        appendCell(row, site.district);
        appendCell(row, site.sector);
        appendCell(row, site.station_count);
        appendCell(row, site.status_summary);
        appendSiteStations(row, site);
        table.appendChild(row);
    });
}


document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    document.getElementById("siteLocationSearch").addEventListener("submit", event => {
        event.preventDefault();
        siteOrigin = {
            latitude: Number(document.getElementById("originLatitude").value),
            longitude: Number(document.getElementById("originLongitude").value)
        };
        siteCollection.page = 1;
        siteCollection.sortBy = "distance_km";
        siteCollection.sortOrder = "asc";
        setMessage(document.getElementById("siteLocationMessage"), `Nearest sites to ${siteOrigin.latitude}, ${siteOrigin.longitude}. Distances are straight-line kilometres.`, "success");
        loadSites();
    });
    document.getElementById("clearSiteLocation").addEventListener("click", () => {
        siteOrigin = null;
        document.getElementById("siteLocationSearch").reset();
        document.getElementById("siteLocationMessage").textContent = "";
        siteCollection.page = 1;
        siteCollection.sortBy = "site_name";
        siteCollection.sortOrder = "asc";
        loadSites();
    });
    const stationsResponse = await apiFetch("/stations");
    if (stationsResponse.ok) setupDistrictFilter(
        document.querySelector(".table-tools"), await stationsResponse.json(),
        () => { siteCollection.page = 1; loadSites(); }
    );
    document.getElementById("refreshSites").addEventListener("click", loadSites);
    document.getElementById("siteStatusFilter").addEventListener("change", () => {
        siteCollection.page = 1;
        loadSites();
    });
    bindCollectionControls({
        state: siteCollection,
        reload: loadSites,
        searchId: "siteSearch",
        pageSizeId: "sitePageSize",
        tableSelector: ".site-table",
        exportBasePath: "/sites",
        csvButtonId: "siteExportCsv",
        pdfButtonId: "siteExportPdf",
        getExtraParameters: siteParameters
    });
    await loadSites();
});
