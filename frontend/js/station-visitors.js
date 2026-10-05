let visitorStations = [];
let visitorCategories = [];
let editingVisitorId = null;
let editingCategoryId = null;

const visitorField = id => document.getElementById(id);

async function visitorRequest(path, options) {
    const response = await apiFetch(path, options);
    if (!response.ok) throw new Error(await getErrorMessage(response, "Request failed"));
    return response.status === 204 ? null : response.json();
}

function visitorOption(select, label, value) { select.append(new Option(label, value)); }

function refreshVisitorStations() {
    const district = visitorField("filterDistrict").value;
    const select = visitorField("filterStation");
    select.replaceChildren(); visitorOption(select, "All stations", "");
    visitorStations.filter(station => matchesSelectedFilter(station.district, district))
        .forEach(station => visitorOption(select, `${station.station_code} - ${station.station_name}`, station.station_id));
}

async function loadVisitorCategories() {
    visitorCategories = await visitorRequest("/visitor-categories");
    for (const [id, first] of [["visitorCategory", "Select a category"], ["filterCategory", "All categories"]]) {
        const select = visitorField(id), previous = select.value;
        select.replaceChildren(); visitorOption(select, first, "");
        visitorCategories.forEach(item => visitorOption(select, item.name, item.category_id));
        select.value = previous;
    }
    const list = visitorField("visitorCategoryList");
    list.replaceChildren();
    visitorCategories.forEach(item => {
        const row = document.createElement("div"); row.className = "visitor-category-item";
        const name = document.createElement("strong"); name.textContent = item.name;
        const edit = document.createElement("button"); edit.className = "secondary-button"; edit.textContent = "Edit";
        edit.onclick = () => { editingCategoryId = item.category_id; visitorField("newVisitorCategory").value = item.name; visitorField("newVisitorCategory").focus(); };
        const remove = document.createElement("button"); remove.className = "danger-button"; remove.textContent = "Delete";
        remove.onclick = async () => {
            if (!confirm(`Delete visitor category ${item.name}?`)) return;
            try { await visitorRequest(`/visitor-categories/${item.category_id}`, {method: "DELETE"}); await loadVisitorCategories(); }
            catch (error) { visitorField("categoryMessage").textContent = error.message; }
        };
        row.append(name, edit, remove);
        const group = document.createElement("div");
        group.append(row);
        attachRecordHistoryPanel(group, "visitor_categories", item.category_id);
        list.append(group);
    });
}

async function loadVisitors() {
    const values = {district: visitorField("filterDistrict").value, station_id: visitorField("filterStation").value,
        category_id: visitorField("filterCategory").value, month_from: visitorField("monthFrom").value,
        month_to: visitorField("monthTo").value, institution: visitorField("filterInstitution").value.trim()};
    const query = new URLSearchParams(Object.entries(values).filter(([, value]) => value));
    const body = visitorField("visitorRows"); body.replaceChildren();
    try {
        const result = await visitorRequest(`/station-visitors?${query}`);
        visitorField("metricVisitors").textContent = result.summary.visitors.toLocaleString();
        visitorField("metricVisits").textContent = result.summary.visits.toLocaleString();
        visitorField("metricStations").textContent = result.summary.stations.toLocaleString();
        const canWrite = !READ_ONLY_ALL_ROLES.has(currentUser.department);
        if (!result.items.length) { const row = body.insertRow(); row.insertCell().colSpan = canWrite ? 9 : 8; row.cells[0].textContent = "No visits in this period."; }
        result.items.forEach(item => {
            const row = body.insertRow();
            [item.visit_date === item.legacy_period_end ? item.visit_date
                : `${item.visit_date} to ${item.legacy_period_end} (legacy)`, item.district,
                `${item.station_code} - ${item.station_name}`, item.institution || "Not recorded",
                item.mission, item.category,
                item.visitor_count, item.recorded_by_username].forEach(value => { row.insertCell().textContent = value ?? "-"; });
            if (!canWrite) return;
            const actions = row.insertCell();
            if (currentUser.department !== "Admin" && item.recorded_by_user_id !== currentUser.user_id) return;
            const edit = document.createElement("button"); edit.className = "secondary-button"; edit.textContent = "Edit";
            edit.onclick = () => {
                editingVisitorId = item.visitor_id;
                visitorField("visitorStation").value = item.station_id;
                visitorField("visitorCategory").value = item.category_id;
                visitorField("visitorDate").value = item.visit_date;
                visitorField("visitorInstitution").value = item.institution || "";
                visitorField("visitorMission").value = item.mission;
                visitorField("visitorNumber").value = item.visitor_count;
                visitorField("cancelVisitor").hidden = false;
                visitorField("visitorEditor").scrollIntoView({behavior: "smooth"});
            };
            const remove = document.createElement("button"); remove.className = "danger-button"; remove.textContent = "Delete";
            remove.onclick = async () => {
                if (!confirm("Delete this visitor record?")) return;
                try { await visitorRequest(`/station-visitors/${item.visitor_id}`, {method: "DELETE"}); loadVisitors(); }
                catch (error) { alert(error.message); }
            };
            actions.append(edit, remove);
        });
        attachRecordHistoryRows(body, "station_visitors", result.items,
            item => item.visitor_id);
    } catch (error) { const row = body.insertRow(); row.insertCell().colSpan = visitorField("visitorActionHeader").hidden ? 8 : 9; row.cells[0].textContent = error.message; }
}

document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    try {
        visitorStations = await visitorRequest("/stations");
        const district = visitorField("filterDistrict"); visitorOption(district, "All districts", "");
        [...new Set(visitorStations.map(station => station.district).filter(Boolean))].sort()
            .forEach(name => visitorOption(district, name, name));
        district.onchange = refreshVisitorStations; refreshVisitorStations();
        visitorOption(visitorField("visitorStation"), "Select a station", "");
        visitorStations.forEach(station => visitorOption(visitorField("visitorStation"),
            `${station.station_code} - ${station.station_name}`, station.station_id));
        await loadVisitorCategories();
        const canWrite = !READ_ONLY_ALL_ROLES.has(currentUser.department);
        visitorField("visitorEditor").hidden = !canWrite;
        visitorField("visitorActionHeader").hidden = !canWrite;
        visitorField("visitorCategoryPanel").hidden = !isITUser();
        const now = new Date();
        const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
        visitorField("visitorDate").value = today;
        visitorField("visitorForm").onsubmit = async event => {
            event.preventDefault();
            const item = {station_id: Number(visitorField("visitorStation").value),
                category_id: Number(visitorField("visitorCategory").value),
                visit_date: visitorField("visitorDate").value,
                institution: visitorField("visitorInstitution").value.trim(),
                mission: visitorField("visitorMission").value.trim(), visitor_count: Number(visitorField("visitorNumber").value)};
            try {
                await visitorRequest(editingVisitorId ? `/station-visitors/${editingVisitorId}` : "/station-visitors",
                    {method: editingVisitorId ? "PUT" : "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(item)});
                visitorField("visitorMessage").textContent = "Visit saved.";
                editingVisitorId = null; visitorField("cancelVisitor").hidden = true;
                visitorField("visitorForm").reset(); visitorField("visitorDate").value = today; await loadVisitors();
            } catch (error) { visitorField("visitorMessage").textContent = error.message; }
        };
        visitorField("cancelVisitor").onclick = () => { editingVisitorId = null; visitorField("visitorForm").reset(); visitorField("visitorDate").value = today; visitorField("cancelVisitor").hidden = true; };
        visitorField("visitorCategoryForm").onsubmit = async event => {
            event.preventDefault();
            try {
                await visitorRequest(editingCategoryId ? `/visitor-categories/${editingCategoryId}` : "/visitor-categories",
                    {method: editingCategoryId ? "PUT" : "POST", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({name: visitorField("newVisitorCategory").value.trim()})});
                editingCategoryId = null; visitorField("newVisitorCategory").value = "";
                visitorField("categoryMessage").textContent = "Category saved."; await loadVisitorCategories();
            } catch (error) { visitorField("categoryMessage").textContent = error.message; }
        };
        visitorField("applyVisitorFilters").onclick = loadVisitors;
        await loadVisitors();
    } catch (error) { visitorField("visitorMessage").textContent = error.message; }
});
