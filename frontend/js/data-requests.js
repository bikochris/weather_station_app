let dataRequestRecords = [];
let editingRequestId = null;
let canManageRequests = false;

function renderRequestChart(categories) {
    const chart = document.getElementById("requestCategoryChart");
    chart.replaceChildren();
    const entries = Object.entries(categories).sort((a, b) => b[1] - a[1]);
    const max = Math.max(...entries.map((entry) => entry[1]), 1);
    entries.forEach(([label, value]) => {
        const row = document.createElement("div");
        row.className = "horizontal-bar-row";
        const name = document.createElement("span");
        name.textContent = label;
        const track = document.createElement("div");
        track.className = "horizontal-bar-track";
        const bar = document.createElement("div");
        bar.className = "horizontal-bar-fill";
        bar.style.width = `${value * 100 / max}%`;
        track.appendChild(bar);
        const count = document.createElement("strong");
        count.textContent = value;
        row.append(name, track, count);
        chart.appendChild(row);
    });
    if (!entries.length) chart.textContent = "No request categories recorded.";
}

function addRequestCategoryRow(values = {}) {
    const container = document.getElementById("requestCategoryRows");
    const row = document.createElement("div");
    row.className = "request-category-row";
    const field = (labelText, input) => {
        const wrapper = document.createElement("div");
        wrapper.className = "field";
        const label = document.createElement("label");
        label.textContent = labelText;
        wrapper.append(label, input);
        return wrapper;
    };
    const category = document.createElement("input");
    category.className = "request-category";
    category.setAttribute("list", "requestCategories");
    category.maxLength = 100;
    category.placeholder = "Type or select a requester category";
    category.required = true;
    category.value = values.category || "";
    const count = document.createElement("input");
    count.className = "request-count";
    count.type = "number";
    count.min = "0";
    count.required = true;
    count.value = values.served_requests ?? "";
    const notes = document.createElement("input");
    notes.className = "request-note";
    notes.maxLength = 1000;
    notes.placeholder = "Optional context";
    notes.value = values.notes || "";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger-button icon-button";
    remove.textContent = "×";
    remove.title = "Remove category";
    remove.setAttribute("aria-label", "Remove category");
    remove.addEventListener("click", () => {
        if (container.children.length === 1) {
            category.value = "";
            count.value = "";
            notes.value = "";
        } else {
            row.remove();
        }
    });
    row.append(field("Requester category", category), field("Requests served", count), field("Notes", notes), remove);
    container.appendChild(row);
}

function resetRequestForm() {
    editingRequestId = null;
    document.getElementById("requestCategoryRows").replaceChildren();
    addRequestCategoryRow();
    document.getElementById("requestMonth").value = new Date().toISOString().slice(0, 7);
    document.getElementById("saveRequests").textContent = "Save monthly categories";
    document.getElementById("cancelRequestEdit").hidden = true;
    document.getElementById("addRequestCategory").hidden = false;
}

function editDataRequest(id) {
    const item = dataRequestRecords.find((record) => record.data_request_id === id);
    if (!item) return;
    editingRequestId = id;
    document.getElementById("requestMonth").value = item.request_month;
    document.getElementById("requestCategoryRows").replaceChildren();
    addRequestCategoryRow(item);
    document.getElementById("saveRequests").textContent = "Save changes";
    document.getElementById("cancelRequestEdit").hidden = false;
    document.getElementById("addRequestCategory").hidden = true;
    document.getElementById("requestEditor").scrollIntoView({behavior: "smooth"});
}

async function deleteDataRequest(id) {
    if (!window.confirm("Delete this data request record?")) return;
    const response = await apiFetch(`/data-requests/${id}`, {method: "DELETE"});
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to delete data request"));
        return;
    }
    resetRequestForm();
    await loadDataRequests();
}

function populateCategorySuggestions(categories) {
    const list = document.getElementById("requestCategories");
    list.replaceChildren();
    Object.keys(categories).sort().forEach((category) => {
        const option = document.createElement("option");
        option.value = category;
        list.appendChild(option);
    });
}

async function loadDataRequests() {
    const parameters = new URLSearchParams();
    const monthFrom = document.getElementById("requestMonthFrom").value;
    const monthTo = document.getElementById("requestMonthTo").value;
    if (monthFrom && monthTo && monthFrom > monthTo) {
        setMessage(
            document.getElementById("requestRangeMessage"),
            "From month cannot be after To month.",
            "error"
        );
        return;
    }
    if (monthFrom) parameters.set("month_from", monthFrom);
    if (monthTo) parameters.set("month_to", monthTo);
    const response = await apiFetch(`/data-requests?${parameters}`);
    if (!response.ok) {
        const message = await getErrorMessage(response, "Unable to load data requests");
        setMessage(document.getElementById("requestRangeMessage"), message, "error");
        return;
    }
    setMessage(document.getElementById("requestRangeMessage"), "");
    const result = await response.json();
    dataRequestRecords = result.items;
    canManageRequests = result.can_manage;
    const table = document.getElementById("requestTable");
    document.getElementById("requestActionsHeading").hidden = !canManageRequests;
    table.replaceChildren();
    if (!dataRequestRecords.length) showTableMessage(table, canManageRequests ? 7 : 6, "No monthly data requests recorded.");
    dataRequestRecords.forEach((item) => {
        const row = document.createElement("tr");
        [item.request_month, item.category, item.served_requests, item.recorded_by_username || "-",
            new Date(item.updated_at).toLocaleString(), item.notes || "-"].forEach((value) => appendCell(row, value));
        if (canManageRequests) {
            const actions = document.createElement("td");
            actions.className = "table-actions";
            const edit = document.createElement("button");
            edit.type = "button";
            edit.className = "secondary-button compact-button";
            edit.textContent = "Edit";
            edit.addEventListener("click", () => editDataRequest(item.data_request_id));
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "danger-button compact-button";
            remove.textContent = "Delete";
            remove.addEventListener("click", () => deleteDataRequest(item.data_request_id));
            actions.append(edit, remove);
            row.appendChild(actions);
        }
        table.appendChild(row);
    });
    populateCategorySuggestions(result.category_totals);
    renderRequestChart(result.category_totals);
    const months = Object.entries(result.monthly_totals).sort((a, b) => b[0].localeCompare(a[0]));
    setMetric("totalRequestsMetric", Object.values(result.category_totals).reduce((sum, value) => sum + value, 0));
    setMetric("requestCategoriesMetric", Object.keys(result.category_totals).length);
    setMetric("requestMonthsMetric", months.length);
    setMetric("latestRequestsMetric", months[0]?.[1] || 0);
}

document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    if (!canViewMonthlyReporting()) {
        window.location.href = "index.html";
        return;
    }
    document.getElementById("requestEditor").hidden = !canWriteDataOperations();
    resetRequestForm();
    document.getElementById("addRequestCategory").addEventListener("click", () => addRequestCategoryRow());
    document.getElementById("cancelRequestEdit").addEventListener("click", resetRequestForm);
    document.getElementById("requestForm").addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = document.getElementById("requestMessage");
        const categories = Array.from(document.querySelectorAll(".request-category-row")).map((row) => ({
            category: row.querySelector(".request-category").value.trim(),
            served_requests: Number(row.querySelector(".request-count").value),
            notes: row.querySelector(".request-note").value.trim() || null
        }));
        const editing = editingRequestId !== null;
        setMessage(message, editing ? "Saving changes..." : "Saving monthly categories...");
        const response = await apiFetch(editing ? `/data-requests/${editingRequestId}` : "/data-requests/batch", {
            method: editing ? "PUT" : "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(editing ? {
                request_month: document.getElementById("requestMonth").value,
                ...categories[0]
            } : {
                request_month: document.getElementById("requestMonth").value,
                categories
            })
        });
        if (!response.ok) {
            setMessage(message, await getErrorMessage(response, "Unable to save request categories"), "error");
            return;
        }
        const result = await response.json();
        resetRequestForm();
        setMessage(message, editing ? "Data request record updated." : `${result.saved} requester categor${result.saved === 1 ? "y" : "ies"} saved.`, "success");
        await loadDataRequests();
    });
    document.getElementById("refreshRequests").addEventListener("click", loadDataRequests);
    document.getElementById("clearRequestRange").addEventListener("click", () => {
        document.getElementById("requestMonthFrom").value = "";
        document.getElementById("requestMonthTo").value = "";
        loadDataRequests();
    });
    document.getElementById("requestMonthFrom").addEventListener("change", loadDataRequests);
    document.getElementById("requestMonthTo").addEventListener("change", loadDataRequests);
    try {
        await loadDataRequests();
    } catch (error) {
        showTableMessage(document.getElementById("requestTable"), 7, error.message);
    }
});
