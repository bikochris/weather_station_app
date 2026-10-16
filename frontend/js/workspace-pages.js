const workspaceEntity = document.body.dataset.workspace;
const workspaceDocuments = workspaceEntity === "documents";
const workspaceIdeas = workspaceEntity === "ideas";
const workspaceId = workspaceDocuments ? "document_id" : workspaceIdeas ? "idea_id" : "agenda_id";
const workspaceState = createCollectionState(workspaceDocuments ? "document_date" : workspaceIdeas ? "idea_date" : "due_date");
workspaceState.sortOrder = workspaceDocuments ? "desc" : "asc";
let workspaceEditing = null;
let workspacePages = 1;
let workspaceRequestSequence = 0;
const workspaceField = id => document.getElementById(id);

function workspaceToday() {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function syncAgendaProgress() {
    const status = workspaceField("entryStatus");
    const progress = Number(workspaceField("entryProgress").value);
    if (status.value === "Cancelled") return;
    if (progress === 100) status.value = "Completed";
    else if (progress > 0 || status.value === "Completed") status.value = "In progress";
}

function workspaceFilters() {
    const values = {date_from: workspaceField("dateFrom").value, date_to: workspaceField("dateTo").value,
        mine: workspaceField("mineOnly").checked ? "true" : ""};
    if (workspaceDocuments) values.file_type = workspaceField("fileTypeFilter").value;
    else {
        values.status = workspaceField("statusFilter").value;
        values.priority = workspaceField("priorityFilter").value;
        if (!workspaceIdeas) values.timing = workspaceField("timingFilter").value;
    }
    if (workspaceIdeas || workspaceDocuments) values.category = workspaceField("categoryFilter").value;
    return values;
}

async function loadIdeaCategories() {
    if (!workspaceIdeas && !workspaceDocuments) return;
    const categories = await workspaceRequest(`/${workspaceEntity}/categories`);
    const choices = [...new Set(["General", ...categories])];
    const filter = workspaceField("categoryFilter");
    const selection = filter.value;
    filter.replaceChildren(new Option("All categories", ""), ...choices.map(value => new Option(value, value)));
    filter.value = selection;
    if (workspaceDocuments) {
        const entry = workspaceField("entryCategory");
        const selected = entry.value;
        entry.replaceChildren(...choices.map(value => new Option(value, value)));
        entry.value = choices.includes(selected) ? selected : "General";
        const rows = workspaceField("documentCategoryRows");
        rows.replaceChildren();
        choices.forEach(value => appendCell(rows.insertRow(), value));
    } else workspaceField("ideaCategories").replaceChildren(...choices.map(value => new Option(value, value)));
}

async function addWorkspaceDocumentCategory(event) {
    event.preventDefault();
    const button = workspaceField("addDocumentCategory");
    const message = workspaceField("documentCategoryMessage");
    button.disabled = true;
    try {
        const name = workspaceField("documentCategoryName").value.trim();
        await workspaceRequest("/documents/categories", {method: "POST",
            headers: {"Content-Type": "application/json"}, body: JSON.stringify({name})});
        await loadIdeaCategories();
        workspaceField("documentCategoryForm").reset();
        workspaceField("entryCategory").value = name;
        setMessage(message, "Document category added.", "success");
    } catch (error) { setMessage(message, error.message, "error"); }
    finally { button.disabled = false; }
}

async function workspaceRequest(path, options) {
    const response = await apiFetch(path, options);
    if (response.status === 405 && path === "/documents/categories") {
        throw new Error("The backend is still running an older version without document categories. Restart the app backend, then refresh this page with Ctrl+F5.");
    }
    if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to complete the request"));
    return response.status === 204 ? null : response.json();
}

function resetWorkspaceEditor() {
    workspaceEditing = null;
    workspaceField("workspaceForm").reset();
    workspaceField("entryDate").value = workspaceToday();
    workspaceField("editorHeading").textContent = workspaceDocuments ? "Upload document" : "Add agenda item";
    workspaceField("saveEntry").textContent = workspaceDocuments ? "Upload document" : "Save agenda item";
    if (workspaceIdeas) {
        workspaceField("editorHeading").textContent = "Add idea";
        workspaceField("saveEntry").textContent = "Save idea";
        workspaceField("entryCategory").value = "General";
    }
    workspaceField("cancelEntry").hidden = true;
    if (workspaceDocuments) {
        workspaceField("entryCategory").value = "General";
        workspaceField("entryFile").required = true;
        workspaceField("fileLabel").textContent = "Document file";
        workspaceField("fileHint").textContent = "PDF, Office files, ZIP and other archives, images, or text. Maximum 20 MB.";
    }
}

function editWorkspaceEntry(item) {
    resetWorkspaceEditor();
    workspaceEditing = item[workspaceId];
    workspaceField("entryTitle").value = item.title;
    workspaceField("entryDescription").value = item.description || "";
    workspaceField("entryDate").value = item[workspaceDocuments ? "document_date" : workspaceIdeas ? "idea_date" : "due_date"];
    workspaceField("editorHeading").textContent = workspaceDocuments ? "Edit document" : "Update agenda item";
    if (workspaceIdeas) workspaceField("editorHeading").textContent = "Edit idea";
    workspaceField("saveEntry").textContent = "Save changes";
    workspaceField("cancelEntry").hidden = false;
    workspaceField("workspaceMessage").textContent = "";
    if (workspaceDocuments) {
        workspaceField("entryCategory").value = item.category || "General";
        workspaceField("entryFile").required = false;
        workspaceField("fileLabel").textContent = "Replacement file (optional)";
        workspaceField("fileHint").textContent = `Current file: ${item.original_filename}. Maximum 20 MB.`;
    } else {
        workspaceField("entryStatus").value = item.status;
        workspaceField("entryPriority").value = item.priority;
        if (workspaceIdeas) {
            workspaceField("entryCategory").value = item.category || "General";
            workspaceField("entryBenefit").value = item.expected_benefit || "";
            workspaceField("entryNextSteps").value = item.next_steps || "";
        } else {
            workspaceField("entryProgress").value = item.progress;
            workspaceField("entryTracking").value = item.tracking_notes || "";
        }
    }
    revealPagePanel(workspaceField("workspaceEditor"), {behavior: "smooth"});
    workspaceField("entryTitle").focus({preventScroll: true});
}

async function downloadWorkspaceDocument(item) {
    try {
        const response = await apiFetch(`/documents/${item.document_id}/download`);
        if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to download document"));
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a"); link.href = url; link.download = item.original_filename;
        link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { window.alert(error.message); }
}

function workspaceActions(row, item) {
    const cell = row.insertCell();
    const group = document.createElement("div"); group.className = "table-actions";
    if (workspaceDocuments) {
        const download = document.createElement("button"); download.type = "button";
        download.className = "secondary-button compact-button"; download.textContent = "Download";
        download.onclick = () => downloadWorkspaceDocument(item); group.append(download);
    }
    if (item.can_edit) {
        const edit = document.createElement("button"); edit.type = "button";
        edit.className = "secondary-button compact-button"; edit.textContent = "Edit";
        edit.onclick = () => editWorkspaceEntry(item);
        const remove = document.createElement("button"); remove.type = "button";
        remove.className = "danger-button compact-button"; remove.textContent = "Delete";
        remove.onclick = async () => {
            if (!confirm(`Delete "${item.title}"? Admin can restore it from Deleted items.`)) return;
            try {
                await workspaceRequest(`/${workspaceEntity}/${item[workspaceId]}`, {method: "DELETE"});
                if (workspaceEditing === item[workspaceId]) resetWorkspaceEditor();
                await loadWorkspaceEntries();
            } catch (error) { window.alert(error.message); }
        };
        group.append(edit, remove);
    }
    cell.append(group);
}

async function loadWorkspaceEntries() {
    const sequence = ++workspaceRequestSequence;
    const body = workspaceField("workspaceRows");
    const count = 12;
    showTableMessage(body, count, "Loading...");
    try {
        const query = buildQuery({page: workspaceState.page, page_size: workspaceState.pageSize,
            search: workspaceState.search, sort_by: workspaceState.sortBy, sort_order: workspaceState.sortOrder,
            ...workspaceFilters()});
        const result = await workspaceRequest(`/${workspaceEntity}?${query}`);
        if (sequence !== workspaceRequestSequence) return;
        workspaceState.page = result.page; workspacePages = result.pages;
        body.replaceChildren();
        const summary = result.summary;
        workspaceField("metricTotal").textContent = summary.total.toLocaleString();
        if (workspaceDocuments) {
            workspaceField("metricMine").textContent = summary.mine.toLocaleString();
            workspaceField("metricBytes").textContent = `${(summary.bytes / 1024 / 1024).toLocaleString(undefined, {maximumFractionDigits: 1})} MB`;
        } else if (workspaceIdeas) {
            workspaceField("metricProposed").textContent = summary.proposed.toLocaleString();
            workspaceField("metricUnderReview").textContent = summary.under_review.toLocaleString();
            workspaceField("metricImplemented").textContent = summary.implemented.toLocaleString();
        } else {
            workspaceField("metricCompleted").textContent = summary.completed.toLocaleString();
            workspaceField("metricOverdue").textContent = summary.overdue.toLocaleString();
            workspaceField("metricDueSoon").textContent = summary.due_soon.toLocaleString();
        }
        if (!result.items.length) showTableMessage(body, count, "No entries match these filters.");
        result.items.forEach(item => {
            const row = body.insertRow();
            const values = workspaceDocuments ? [item.document_id, item.title, item.category || "General", item.document_date,
                item.description, item.original_filename, item.file_type, item.file_size, item.version,
                item.created_by_username, item.updated_at] : workspaceIdeas ? [item.idea_id, item.title, item.category,
                item.description, item.idea_date, item.status, item.priority, item.expected_benefit,
                item.next_steps, item.created_by_username, item.updated_at] : [item.agenda_id, item.title, item.description,
                item.due_date, item.status, item.priority, item.progress, item.tracking_notes, item.timing,
                item.created_by_username, item.updated_at];
            values.forEach(value => appendCell(row, value || (value === 0 ? "0" : "-")));
            if (!workspaceDocuments && !workspaceIdeas) {
                const timing = row.cells[8];
                timing.classList.add("agenda-timing", `agenda-${item.timing.toLowerCase().replaceAll(" ", "-")}`);
                const progress = document.createElement("progress"); progress.max = 100; progress.value = item.progress;
                progress.setAttribute("aria-label", `${item.title}: ${item.progress}% complete`);
                row.cells[6].append(progress);
            }
            workspaceActions(row, item);
        });
        attachRecordHistoryRows(body, workspaceEntity, result.items, item => item[workspaceId]);
        workspaceField("pageSummary").textContent = result.total ?
            `${(result.page - 1) * result.page_size + 1}-${Math.min(result.page * result.page_size, result.total)} of ${result.total}` : "0 entries";
        workspaceField("pageLabel").textContent = `Page ${result.page} of ${result.pages}`;
        workspaceField("previousPage").disabled = result.page <= 1;
        workspaceField("nextPage").disabled = result.page >= result.pages;
    } catch (error) {
        if (sequence === workspaceRequestSequence) {
            showTableMessage(body, count, error.message);
            workspaceField("previousPage").disabled = true;
            workspaceField("nextPage").disabled = true;
        }
    }
}

async function saveWorkspaceEntry(event) {
    event.preventDefault();
    const button = workspaceField("saveEntry"); button.disabled = true;
    setMessage(workspaceField("workspaceMessage"), "Saving...");
    try {
        if (workspaceDocuments) await workspaceRequest("/documents/categories");
        const values = {title: workspaceField("entryTitle").value.trim(),
            description: workspaceField("entryDescription").value.trim()};
        const method = workspaceEditing ? "PUT" : "POST";
        let path = `/${workspaceEntity}${workspaceEditing ? `/${workspaceEditing}` : ""}`;
        let options;
        if (workspaceDocuments) {
            const file = workspaceField("entryFile").files[0];
            if (file && file.size > 20 * 1024 * 1024) throw new Error("The maximum document size is 20 MB");
            values.document_date = workspaceField("entryDate").value;
            values.category = workspaceField("entryCategory").value.trim();
            if (file) values.filename = file.name;
            path += `?${new URLSearchParams(values)}`;
            options = {method, headers: {"Content-Type": "application/octet-stream"}, body: file || null};
        } else if (workspaceIdeas) {
            Object.assign(values, {idea_date: workspaceField("entryDate").value,
                category: workspaceField("entryCategory").value.trim(),
                status: workspaceField("entryStatus").value, priority: workspaceField("entryPriority").value,
                expected_benefit: workspaceField("entryBenefit").value.trim(),
                next_steps: workspaceField("entryNextSteps").value.trim()});
            options = {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(values)};
        } else {
            syncAgendaProgress();
            Object.assign(values, {due_date: workspaceField("entryDate").value,
                status: workspaceField("entryStatus").value, priority: workspaceField("entryPriority").value,
                progress: Number(workspaceField("entryProgress").value), tracking_notes: workspaceField("entryTracking").value.trim()});
            options = {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(values)};
        }
        await workspaceRequest(path, options);
        resetWorkspaceEditor();
        setMessage(workspaceField("workspaceMessage"), workspaceDocuments ? "Document saved." : workspaceIdeas ? "Idea saved." : "Agenda item saved.", "success");
        await loadWorkspaceEntries();
        if (workspaceIdeas || workspaceDocuments) {
            try { await loadIdeaCategories(); }
            catch (error) { setMessage(workspaceField("workspaceMessage"), `Saved successfully. Category suggestions could not refresh: ${error.message}`, "success"); }
        }
    } catch (error) { setMessage(workspaceField("workspaceMessage"), error.message, "error"); }
    finally { button.disabled = false; }
}

document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    resetWorkspaceEditor();
    workspaceField("workspaceForm").onsubmit = saveWorkspaceEntry;
    if (workspaceDocuments) workspaceField("documentCategoryForm").onsubmit = addWorkspaceDocumentCategory;
    workspaceField("cancelEntry").onclick = () => { resetWorkspaceEditor(); workspaceField("workspaceMessage").textContent = ""; };
    const apply = () => { workspaceState.page = 1; loadWorkspaceEntries(); };
    bindCollectionControls({state: workspaceState, reload: loadWorkspaceEntries,
        searchId: "entrySearch", pageSizeId: "entryPageSize", tableSelector: "#workspaceTable",
        getExtraParameters: workspaceFilters});
    workspaceField("applyFilters").onclick = apply;
    workspaceField("resetFilters").onclick = () => {
        workspaceField("entrySearch").value = ""; workspaceState.search = "";
        workspaceField("dateFrom").value = ""; workspaceField("dateTo").value = "";
        workspaceField("mineOnly").checked = false;
        if (workspaceIdeas || workspaceDocuments) workspaceField("categoryFilter").value = "";
        for (const id of workspaceDocuments ? ["fileTypeFilter"] : workspaceIdeas ? ["statusFilter", "priorityFilter"] : ["statusFilter", "priorityFilter", "timingFilter"]) workspaceField(id).value = "";
        apply();
    };
    workspaceField("previousPage").onclick = () => { if (workspaceState.page > 1) { workspaceState.page--; loadWorkspaceEntries(); } };
    workspaceField("nextPage").onclick = () => { if (workspaceState.page < workspacePages) { workspaceState.page++; loadWorkspaceEntries(); } };
    for (const format of ["csv", "pdf"]) workspaceField(format === "csv" ? "exportCsv" : "exportPdf").onclick = () =>
        downloadCollectionExport(`/${workspaceEntity}`, format, workspaceState, workspaceFilters(),
            workspaceField("includeHistory").checked ? workspaceEntity : null,
            historyColumnsForTable(workspaceField("workspaceTable"), workspaceEntity));
    if (!workspaceDocuments && !workspaceIdeas) {
        workspaceField("entryProgress").addEventListener("input", syncAgendaProgress);
        workspaceField("entryStatus").onchange = () => {
            if (workspaceField("entryStatus").value === "Completed") workspaceField("entryProgress").value = 100;
            if (workspaceField("entryStatus").value === "Open") workspaceField("entryProgress").value = 0;
        };
    }
    await loadWorkspaceEntries();
    if (workspaceIdeas || workspaceDocuments) {
        try { await loadIdeaCategories(); }
        catch (error) { setMessage(workspaceField("workspaceMessage"), error.message, "error"); }
    }
});
