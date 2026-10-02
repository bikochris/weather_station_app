document.addEventListener("DOMContentLoaded", async () => {
    if (!await requireSession()) return;
    if (!isITUser()) return;
    const panel = document.getElementById("categoryFrequencyPanel");
    panel.hidden = false;
    const yearSelect = document.getElementById("frequencyFiscalYear");
    const categorySelect = document.getElementById("frequencyCategory");
    const today = new Date();
    const currentYear = today.getFullYear() - (today.getMonth() < 6 ? 1 : 0);
    for (let year = currentYear - 2; year <= currentYear + 2; year++) {
        yearSelect.appendChild(new Option(`${year}/${year + 1}`, year));
    }
    yearSelect.value = currentYear;
    const stationsResponse = await apiFetch("/stations");
    if (stationsResponse.ok) {
        const stations = await stationsResponse.json();
        [...new Set(stations.map(station => station.station_category).filter(Boolean))].sort()
            .forEach(category => categorySelect.appendChild(new Option(category, category)));
    }
    async function loadTargets() {
        const response = await apiFetch(`/maintenance-frequencies?fiscal_start_year=${yearSelect.value}`);
        if (!response.ok) return;
        const items = await response.json();
        const body = document.getElementById("frequencyRows");
        body.replaceChildren();
        if (!items.length) {
            const row = body.insertRow();
            const cell = row.insertCell();
            cell.colSpan = 5;
            cell.textContent = "No category targets configured for this fiscal year.";
        }
        items.forEach(item => {
            const row = body.insertRow();
            [item.station_category, `${item.fiscal_start_year}/${item.fiscal_start_year + 1}`,
                item.cadence, item.target_visits].forEach(value => { row.insertCell().textContent = value; });
            const actions = row.insertCell();
            const edit = document.createElement("button");
            edit.type = "button";
            edit.className = "secondary-button";
            edit.textContent = "Edit";
            edit.onclick = () => {
                categorySelect.value = item.station_category;
                document.getElementById("frequencyCadence").value = item.cadence;
                document.getElementById("frequencyTarget").value = item.target_visits;
                panel.scrollIntoView({behavior: "smooth"});
            };
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "danger-button";
            remove.textContent = "Delete";
            remove.onclick = async () => {
                if (!confirm(`Remove the ${item.station_category} target for this fiscal year?`)) return;
                const result = await apiFetch(`/maintenance-frequencies/${encodeURIComponent(item.station_category)}?fiscal_start_year=${yearSelect.value}`, {method: "DELETE"});
                if (!result.ok) { alert(await getErrorMessage(result, "Unable to delete target")); return; }
                loadTargets();
            };
            actions.append(edit, remove);
        });
        attachRecordHistoryRows(body, "maintenance_frequencies", items,
            item => `${item.fiscal_start_year}|${item.station_category}`);
    }
    yearSelect.onchange = loadTargets;
    document.getElementById("categoryFrequencyForm").onsubmit = async event => {
        event.preventDefault();
        const message = document.getElementById("frequencyMessage");
        const response = await apiFetch("/maintenance-frequencies", {
            method: "PUT", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({fiscal_start_year: Number(yearSelect.value),
                station_category: categorySelect.value,
                cadence: document.getElementById("frequencyCadence").value,
                target_visits: Number(document.getElementById("frequencyTarget").value)})
        });
        message.textContent = response.ok ? "Frequency saved." : await getErrorMessage(response, "Unable to save frequency");
        if (response.ok) loadTargets();
    };
    loadTargets();
});
