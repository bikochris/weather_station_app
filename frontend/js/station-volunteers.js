const volunteerPage = id => document.getElementById(id);
const volunteerState = createCollectionState("station_name");
let volunteerStations = [];
let editingVolunteerId = null;

async function volunteerRequest(path, options) {
    const response = await apiFetch(path, options);
    if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load volunteer records"));
    return response.status === 204 ? null : response.json();
}

function volunteerFilters() {
    return {
        province: volunteerPage("filterProvince").value,
        district: volunteerPage("filterDistrict").value,
        sector: volunteerPage("filterSector").value,
        station_id: volunteerPage("filterStation").value,
        account_type: volunteerPage("filterAccountType").value.trim()
    };
}

function fillVolunteerOptions(id, label, values) {
    const select = volunteerPage(id);
    const previous = select.value;
    select.replaceChildren(new Option(label, ""));
    [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b))
        .forEach(value => select.append(new Option(value, value)));
    if ([...select.options].some(option => option.value === previous)) select.value = previous;
}

function refreshVolunteerLocationFilters(changed) {
    if (changed === "province") {
        volunteerPage("filterDistrict").value = "";
        volunteerPage("filterSector").value = "";
        volunteerPage("filterStation").value = "";
    } else if (changed === "district") {
        volunteerPage("filterSector").value = "";
        volunteerPage("filterStation").value = "";
    } else if (changed === "sector") {
        volunteerPage("filterStation").value = "";
    }
    const province = volunteerPage("filterProvince").value;
    const inProvince = volunteerStations.filter(station => !province || station.province === province);
    fillVolunteerOptions("filterDistrict", "All districts", inProvince.map(station => station.district));
    const district = volunteerPage("filterDistrict").value;
    const inDistrict = inProvince.filter(station => !district || station.district === district);
    fillVolunteerOptions("filterSector", "All sectors", inDistrict.map(station => station.sector));
    const sector = volunteerPage("filterSector").value;
    const inSector = inDistrict.filter(station => !sector || station.sector === sector);
    const select = volunteerPage("filterStation"), previous = select.value;
    select.replaceChildren(new Option("All stations", ""));
    inSector.forEach(station => select.append(new Option(
        `${station.station_code} - ${station.station_name}`, station.station_id)));
    if ([...select.options].some(option => option.value === previous)) select.value = previous;
}

function showVolunteerStation() {
    const station = volunteerStations.find(item => String(item.station_id) === volunteerPage("volunteerStation").value);
    for (const [id, field] of [["stationCode", "station_code"], ["stationName", "station_name"],
        ["stationProvince", "province"], ["stationDistrict", "district"], ["stationSector", "sector"]]) {
        volunteerPage(id).value = station?.[field] || "";
    }
}

function resetVolunteerForm() {
    editingVolunteerId = null;
    volunteerPage("volunteerForm").reset();
    volunteerPage("volunteerFormHeading").textContent = "Add volunteer";
    volunteerPage("saveVolunteer").textContent = "Save volunteer";
    volunteerPage("cancelVolunteer").hidden = true;
    showVolunteerStation();
}

function editVolunteer(item) {
    editingVolunteerId = item.volunteer_id;
    for (const [id, value] of [["volunteerStation", item.station_id],
        ["volunteerName", item.volunteer_name], ["volunteerId", item.volunteer_identifier],
        ["accountType", item.account_type], ["accountName", item.account_name],
        ["accountNumber", item.account_number], ["mobilePhone", item.mobile_phone]]) {
        volunteerPage(id).value = value;
    }
    showVolunteerStation();
    volunteerPage("volunteerFormHeading").textContent = "Edit volunteer";
    volunteerPage("saveVolunteer").textContent = "Save changes";
    volunteerPage("cancelVolunteer").hidden = false;
    volunteerPage("volunteerEditor").scrollIntoView({behavior: "smooth"});
}

async function loadStationVolunteers() {
    const body = volunteerPage("volunteerRows");
    try {
        const result = await volunteerRequest(`/station-volunteers?${collectionParameters(volunteerState, volunteerFilters())}`);
        volunteerPage("metricVolunteers").textContent = result.summary.volunteers.toLocaleString();
        volunteerPage("metricStations").textContent = result.summary.stations.toLocaleString();
        body.replaceChildren();
        if (!result.items.length) {
            showTableMessage(body, 12, "No volunteers match these filters.");
        }
        result.items.forEach(item => {
            const row = body.insertRow();
            for (const key of ["station_code", "station_name", "province", "district", "sector",
                "volunteer_name", "volunteer_identifier", "account_type", "account_name",
                "account_number", "mobile_phone"]) {
                appendCell(row, item[key]);
            }
            const actions = row.insertCell();
            if (READ_ONLY_ALL_ROLES.has(currentUser.department) ||
                (currentUser.department !== "Admin" && item.recorded_by_user_id !== currentUser.user_id)) return;
            const edit = document.createElement("button");
            edit.className = "secondary-button"; edit.type = "button"; edit.textContent = "Edit";
            edit.onclick = () => editVolunteer(item);
            const remove = document.createElement("button");
            remove.className = "danger-button"; remove.type = "button"; remove.textContent = "Delete";
            remove.onclick = async () => {
                if (!confirm(`Delete ${item.volunteer_name} from ${item.station_name}?`)) return;
                try {
                    await volunteerRequest(`/station-volunteers/${item.volunteer_id}`, {method: "DELETE"});
                    if (editingVolunteerId === item.volunteer_id) resetVolunteerForm();
                    await loadStationVolunteers();
                } catch (error) { setMessage(volunteerPage("volunteerMessage"), error.message, "error"); }
            };
            actions.append(edit, remove);
        });
        attachRecordHistoryRows(body, "station_volunteers", result.items, item => item.volunteer_id);
        renderPagination("volunteerPagination", result, volunteerState, loadStationVolunteers);
    } catch (error) {
        showTableMessage(body, 12, error.message);
        volunteerPage("volunteerPagination").replaceChildren();
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    if (!canAccessVolunteerData()) { location.href = "index.html"; return; }
    try {
        volunteerStations = await volunteerRequest("/stations");
        volunteerStations.sort((a, b) => a.station_name.localeCompare(b.station_name));
        const stationSelect = volunteerPage("volunteerStation");
        stationSelect.append(new Option("Select a station", ""));
        volunteerStations.forEach(station => stationSelect.append(new Option(
            `${station.station_code} - ${station.station_name}`, station.station_id)));
        stationSelect.onchange = showVolunteerStation;
        fillVolunteerOptions("filterProvince", "All provinces", volunteerStations.map(station => station.province));
        refreshVolunteerLocationFilters();
        for (const [id, changed] of [["filterProvince", "province"], ["filterDistrict", "district"],
            ["filterSector", "sector"]]) {
            volunteerPage(id).onchange = () => { refreshVolunteerLocationFilters(changed); volunteerState.page = 1; loadStationVolunteers(); };
        }
        volunteerPage("filterStation").onchange = () => { volunteerState.page = 1; loadStationVolunteers(); };
        volunteerPage("applyVolunteerFilters").onclick = () => { volunteerState.page = 1; loadStationVolunteers(); };
        volunteerPage("cancelVolunteer").onclick = resetVolunteerForm;
        volunteerPage("volunteerEditor").hidden = READ_ONLY_ALL_ROLES.has(currentUser.department);
        bindCollectionControls({state: volunteerState, reload: loadStationVolunteers,
            searchId: "searchVolunteers", pageSizeId: "volunteerPageSize",
            tableSelector: ".station-volunteer-table", exportBasePath: "/station-volunteers",
            csvButtonId: "exportVolunteerCsv", pdfButtonId: "exportVolunteerPdf",
            getExtraParameters: volunteerFilters});
        volunteerPage("volunteerForm").onsubmit = async event => {
            event.preventDefault();
            const item = {station_id: Number(stationSelect.value),
                volunteer_name: volunteerPage("volunteerName").value.trim(),
                volunteer_identifier: volunteerPage("volunteerId").value.trim(),
                account_type: volunteerPage("accountType").value.trim(),
                account_name: volunteerPage("accountName").value.trim(),
                account_number: volunteerPage("accountNumber").value.trim(),
                mobile_phone: volunteerPage("mobilePhone").value.trim()};
            try {
                await volunteerRequest(editingVolunteerId ? `/station-volunteers/${editingVolunteerId}` : "/station-volunteers",
                    {method: editingVolunteerId ? "PUT" : "POST", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify(item)});
                resetVolunteerForm();
                setMessage(volunteerPage("volunteerMessage"), "Volunteer record saved.", "success");
                await loadStationVolunteers();
            } catch (error) { setMessage(volunteerPage("volunteerMessage"), error.message, "error"); }
        };
        await loadStationVolunteers();
    } catch (error) { setMessage(volunteerPage("volunteerMessage"), error.message, "error"); }
});
