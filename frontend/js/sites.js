const siteCollection = createCollectionState("site_name", "asc");


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
        details.textContent = `${station.station_category} · ${station.status}${station.suspended ? " · Suspended" : ""}`;
        item.append(name, details);
        list.appendChild(item);
    });
    cell.appendChild(list);
}


async function loadSites() {
    const table = document.getElementById("siteTable");
    showTableMessage(table, 10, "Loading sites...");
    const parameters = collectionParameters(siteCollection);
    const response = await apiFetch(`/sites?${parameters}`);
    if (!response.ok) {
        showTableMessage(table, 10, await getErrorMessage(response, "Unable to load sites"));
        return;
    }
    const result = await response.json();
    renderSiteSummary(result.summary);
    renderPagination("sitePagination", result, siteCollection, loadSites);
    table.replaceChildren();
    if (!result.items.length) {
        showTableMessage(table, 10, "No sites match the current search.");
        return;
    }
    result.items.forEach((site) => {
        const row = document.createElement("tr");
        appendCell(row, site.site_code);
        appendCell(row, site.site_name);
        appendCell(row, site.latitude);
        appendCell(row, site.longitude);
        appendCell(row, `${site.altitude} m`);
        appendCell(row, site.province);
        appendCell(row, site.district);
        appendCell(row, site.sector);
        appendCell(row, site.station_count);
        appendSiteStations(row, site);
        table.appendChild(row);
    });
}


document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    document.getElementById("refreshSites").addEventListener("click", loadSites);
    bindCollectionControls({
        state: siteCollection,
        reload: loadSites,
        searchId: "siteSearch",
        pageSizeId: "sitePageSize",
        tableSelector: ".site-table"
    });
    await loadSites();
});
