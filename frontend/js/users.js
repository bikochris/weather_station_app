let userRecords = [];
let stationRecords = [];


function updateStationAssignmentVisibility() {
    document.getElementById("stationAssignmentFieldset").hidden =
        document.getElementById("department").value !== "Observation Officer";
}


function selectedStationIds() {
    return Array.from(
        document.querySelectorAll('input[name="assignedStationIds"]:checked')
    ).map((input) => Number(input.value));
}


function renderStationAssignments(selectedIds = []) {
    const selected = new Set(selectedIds);
    const container = document.getElementById("stationAssignmentOptions");
    container.replaceChildren();
    stationRecords.forEach((station) => {
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.name = "assignedStationIds";
        checkbox.value = station.station_id;
        checkbox.checked = selected.has(station.station_id);
        label.append(
            checkbox,
            ` ${station.station_code} - ${station.station_name}`
        );
        container.appendChild(label);
    });
}


async function loadAssignmentStations() {
    const response = await apiFetch("/stations");
    if (!response.ok) {
        throw new Error(await getErrorMessage(response, "Unable to load stations"));
    }
    stationRecords = await response.json();
    renderStationAssignments();
}


function resetUserForm() {
    const form = document.getElementById("userForm");
    form.reset();
    document.getElementById("userEditId").value = "";
    document.getElementById("userIsActive").checked = true;
    document.getElementById("password").required = true;
    document.getElementById("passwordLabel").textContent = "Temporary password";
    document.getElementById("userSubmit").textContent = "Create user";
    document.getElementById("cancelUserEdit").hidden = true;
    renderStationAssignments();
    updateStationAssignmentVisibility();
}


function editUser(userId) {
    const user = userRecords.find((item) => item.user_id === userId);
    if (!user) return;
    document.getElementById("userEditId").value = user.user_id;
    document.getElementById("fullName").value = user.full_name;
    document.getElementById("username").value = user.username;
    document.getElementById("email").value = user.email || "";
    document.getElementById("department").value = user.department;
    renderStationAssignments(user.station_ids || []);
    updateStationAssignmentVisibility();
    document.getElementById("userIsActive").checked = Boolean(user.is_active);
    document.getElementById("password").value = "";
    document.getElementById("password").required = false;
    document.getElementById("passwordLabel").textContent =
        "New temporary password (leave blank to keep current)";
    document.getElementById("userSubmit").textContent = "Save changes";
    document.getElementById("cancelUserEdit").hidden = false;
    document.getElementById("userForm").scrollIntoView({behavior: "smooth"});
}


async function deleteUser(userId) {
    const user = userRecords.find((item) => item.user_id === userId);
    if (!user || !window.confirm(`Delete user "${user.username}"?`)) return;
    const response = await apiFetch(`/users/${userId}`, {method: "DELETE"});
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to delete user"));
        return;
    }
    await loadUsers();
}


function appendUserActions(row, user) {
    const cell = appendCell(row, "");
    const group = document.createElement("div");
    group.className = "action-group";
    const editButton = document.createElement("button");
    editButton.type = "button";
    editButton.className = "secondary-button";
    editButton.textContent = "Edit";
    editButton.addEventListener("click", () => editUser(user.user_id));
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "danger-button";
    deleteButton.textContent = "Delete";
    deleteButton.addEventListener("click", () => deleteUser(user.user_id));
    group.append(editButton, deleteButton);
    cell.appendChild(group);
}


async function loadUsers() {
    const table = document.getElementById("userTable");
    showTableMessage(table, 7, "Loading users...");
    try {
        const response = await apiFetch("/users");
        if (!response.ok) {
            throw new Error(await getErrorMessage(response, "Unable to load users"));
        }
        userRecords = await response.json();
        table.replaceChildren();
        userRecords.forEach((user) => {
            const row = document.createElement("tr");
            appendCell(row, user.full_name);
            appendCell(row, user.username);
            appendCell(row, user.email || "");
            appendCell(row, user.department);
            const assignedStations = (user.station_ids || []).map((stationId) => {
                const station = stationRecords.find((item) => item.station_id === stationId);
                return station ? station.station_code : `#${stationId}`;
            });
            appendCell(
                row,
                user.department === "Observation Officer"
                    ? (assignedStations.join(", ") || "None")
                    : "-"
            );
            const status = user.is_active ? "Active" : "Inactive";
            appendCell(
                row,
                user.must_change_password
                    ? `${status} - password change required`
                    : status
            );
            appendUserActions(row, user);
            table.appendChild(row);
        });
    } catch (error) {
        showTableMessage(table, 7, error.message);
    }
}


document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    const signedInUser = await requireSession();
    if (!signedInUser) return;
    if (!isITUser()) {
        window.location.href = "index.html";
        return;
    }

    const form = document.getElementById("userForm");
    const message = document.getElementById("formMessage");
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const editId = document.getElementById("userEditId").value;
        const password = document.getElementById("password").value;
        setMessage(message, editId ? "Updating user..." : "Creating user...");

        const user = {
            full_name: document.getElementById("fullName").value.trim(),
            username: document.getElementById("username").value.trim(),
            email: document.getElementById("email").value.trim() || null,
            department: document.getElementById("department").value,
            password: password || null,
            station_ids: selectedStationIds()
        };
        if (user.department === "Observation Officer" && !user.station_ids.length) {
            setMessage(message, "Assign at least one station to the Observation Officer.", "error");
            return;
        }
        if (editId) {
            user.is_active = document.getElementById("userIsActive").checked;
        } else {
            delete user.is_active;
        }

        try {
            const response = await apiFetch(editId ? `/users/${editId}` : "/users", {
                method: editId ? "PUT" : "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(user)
            });
            if (!response.ok) {
                throw new Error(await getErrorMessage(response, "Unable to save user"));
            }
            resetUserForm();
            setMessage(message, editId ? "User updated." : "User created.", "success");
            await loadUsers();
        } catch (error) {
            setMessage(message, error.message, "error");
        }
    });

    document.getElementById("cancelUserEdit").addEventListener("click", resetUserForm);
    document.getElementById("department").addEventListener(
        "change",
        updateStationAssignmentVisibility
    );
    document.getElementById("refreshUsers").addEventListener("click", loadUsers);
    try {
        await loadAssignmentStations();
        await loadUsers();
    } catch (error) {
        setMessage(message, error.message, "error");
    }
});
