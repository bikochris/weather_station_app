const deletedState = {page: 1, total: 0, sortBy: "deleted_at", sortOrder: "desc"};


function deletedParameters(includePage = true) {
    const params = new URLSearchParams();
    for (const [key, id] of Object.entries({
        search: "deletedSearch", entity_type: "deletedType", district: "deletedDistrict",
        status: "deletedStatus", date_from: "deletedFrom", date_to: "deletedTo"
    })) {
        const value = document.getElementById(id).value.trim();
        if (value) params.set(key, value);
    }
    params.set("sort_by", deletedState.sortBy);
    params.set("sort_order", deletedState.sortOrder);
    if (includePage) {
        params.set("page", deletedState.page);
        params.set("page_size", document.getElementById("deletedPageSize").value);
    }
    return params;
}


function deletedTypeLabel(value) {
    return value.replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase());
}


async function loadDeletedFilters() {
    const response = await apiFetch("/deleted-items/filters");
    if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load filters"));
    const data = await response.json();
    const type = document.getElementById("deletedType");
    const district = document.getElementById("deletedDistrict");
    data.types.forEach(value => type.append(new Option(deletedTypeLabel(value), value)));
    data.districts.forEach(value => district.append(new Option(value, value)));
}


async function restoreItem(item) {
    if (!confirm(`Restore ${item.title}? Current records will not be overwritten.`)) return;
    const response = await apiFetch(`/deleted-items/${item.deleted_item_id}/restore`, {method: "POST"});
    if (!response.ok) {
        setMessage(document.getElementById("deletedMessage"),
            await getErrorMessage(response, "Unable to restore item"), "error");
        return;
    }
    setMessage(document.getElementById("deletedMessage"), `${item.title} restored.`, "success");
    await loadDeletedItems();
}


async function downloadItem(item) {
    const response = await apiFetch(`/deleted-items/${item.deleted_item_id}/download`);
    if (!response.ok) {
        setMessage(document.getElementById("deletedMessage"),
            await getErrorMessage(response, "Unable to download backup"), "error");
        return;
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `deleted-item-${item.deleted_item_id}.zip`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}


async function toggleDeletedHistory(item, button, detail) {
    detail.hidden = !detail.hidden;
    button.setAttribute("aria-expanded", String(!detail.hidden));
    if (detail.hidden || detail.dataset.loaded === "true") return;
    const content = detail.querySelector(".record-history-content");
    content.textContent = "Loading edit history...";
    try {
        const response = await apiFetch(`/deleted-items/${item.deleted_item_id}/history`);
        if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load edit history"));
        const result = await response.json();
        content.replaceChildren();
        if (!result.items.length) content.textContent = "No edits recorded for this item.";
        renderRecordHistoryTable(content, result.items, item.entity_type);
        detail.dataset.loaded = "true";
    } catch (error) {
        content.textContent = error.message;
    }
}


async function loadDeletedItems() {
    const body = document.getElementById("deletedItemsRows");
    showTableMessage(body, 9, "Loading backups...");
    try {
        const response = await apiFetch(`/deleted-items?${deletedParameters()}`);
        if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load deleted items"));
        const result = await response.json();
        deletedState.total = result.total;
        body.replaceChildren();
        if (!result.items.length) showTableMessage(body, 9, "No deleted items match these filters.");
        result.items.forEach(item => {
            const row = body.insertRow();
            appendCell(row, deletedTypeLabel(item.entity_type));
            appendCell(row, item.title);
            appendCell(row, item.entity_id);
            appendCell(row, item.district?.replaceAll(",", ", ") || "-");
            appendCell(row, item.deleted_by_username);
            appendCell(row, new Date(item.deleted_at).toLocaleString());
            appendCell(row, item.row_count);
            const status = row.insertCell();
            status.textContent = item.restored_at ? `Restored by ${item.restored_by_username || "Admin"}` : "Awaiting restore";
            status.className = item.restored_at ? "deleted-status-restored" : "deleted-status-pending";
            const actions = row.insertCell();
            actions.className = "deleted-item-actions";
            const history = document.createElement("button");
            history.type = "button";
            history.className = "secondary-button";
            history.textContent = "History";
            history.setAttribute("aria-expanded", "false");
            const detail = document.createElement("tr");
            detail.className = "record-history-detail";
            detail.hidden = true;
            detail.id = `deleted-history-${item.deleted_item_id}`;
            history.setAttribute("aria-controls", detail.id);
            const detailCell = detail.insertCell();
            detailCell.colSpan = row.cells.length;
            const detailContent = document.createElement("div");
            detailContent.className = "record-history-content";
            detailCell.append(detailContent);
            history.addEventListener("click", () => toggleDeletedHistory(item, history, detail));
            row.addEventListener("click", event => {
                if (!event.target.closest("button, a, input, select"))
                    toggleDeletedHistory(item, history, detail);
            });
            actions.append(history);
            const download = document.createElement("button");
            download.type = "button";
            download.className = "secondary-button";
            download.textContent = "Download";
            download.addEventListener("click", () => downloadItem(item));
            actions.append(download);
            if (!item.restored_at) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "secondary-button";
                button.textContent = "Restore";
                button.addEventListener("click", async () => {
                    button.disabled = true;
                    try { await restoreItem(item); } finally { button.disabled = false; }
                });
                actions.append(button);
            }
            row.after(detail);
        });
        const pageSize = Number(document.getElementById("deletedPageSize").value);
        const start = result.total ? (result.page - 1) * pageSize + 1 : 0;
        const end = Math.min(result.page * pageSize, result.total);
        document.getElementById("deletedCount").textContent = `${start}-${end} of ${result.total}`;
        document.getElementById("deletedPageLabel").textContent = `Page ${result.page}`;
        document.getElementById("deletedPrevious").disabled = result.page <= 1;
        document.getElementById("deletedNext").disabled = end >= result.total;
    } catch (error) {
        showTableMessage(body, 9, error.message);
    }
}


async function exportDeletedItems(format) {
    const response = await apiFetch(`/deleted-items/export?${deletedParameters(false)}&format=${format}`);
    if (!response.ok) {
        setMessage(document.getElementById("deletedMessage"),
            await getErrorMessage(response, "Unable to export deleted items"), "error");
        return;
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `deleted-items.${format}`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}


document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    document.getElementById("deletedApply").addEventListener("click", () => {
        deletedState.page = 1; loadDeletedItems();
    });
    document.getElementById("deletedReset").addEventListener("click", () => {
        for (const id of ["deletedSearch", "deletedType", "deletedDistrict", "deletedFrom", "deletedTo"])
            document.getElementById(id).value = "";
        document.getElementById("deletedStatus").value = "deleted";
        deletedState.page = 1; loadDeletedItems();
    });
    document.getElementById("deletedSearch").addEventListener("keydown", event => {
        if (event.key === "Enter") { deletedState.page = 1; loadDeletedItems(); }
    });
    document.getElementById("deletedPageSize").addEventListener("change", () => {
        deletedState.page = 1; loadDeletedItems();
    });
    document.getElementById("deletedPrevious").addEventListener("click", () => {
        deletedState.page -= 1; loadDeletedItems();
    });
    document.getElementById("deletedNext").addEventListener("click", () => {
        deletedState.page += 1; loadDeletedItems();
    });
    document.getElementById("deletedExportCsv").addEventListener("click", () => exportDeletedItems("csv"));
    document.getElementById("deletedExportPdf").addEventListener("click", () => exportDeletedItems("pdf"));
    document.querySelectorAll("#deletedItemsTable th[data-sort]").forEach(heading => {
        heading.tabIndex = 0;
        const changeSort = () => {
            deletedState.sortOrder = deletedState.sortBy === heading.dataset.sort && deletedState.sortOrder === "asc"
                ? "desc" : "asc";
            deletedState.sortBy = heading.dataset.sort;
            deletedState.page = 1;
            document.querySelectorAll("#deletedItemsTable th[data-sort]").forEach(item =>
                item.setAttribute("aria-sort", item === heading
                    ? (deletedState.sortOrder === "asc" ? "ascending" : "descending") : "none"));
            loadDeletedItems();
        };
        heading.addEventListener("click", changeSort);
        heading.addEventListener("keydown", event => {
            if (event.key === "Enter" || event.key === " ") { event.preventDefault(); changeSort(); }
        });
    });
    try { await loadDeletedFilters(); await loadDeletedItems(); }
    catch (error) { showTableMessage(document.getElementById("deletedItemsRows"), 9, error.message); }
});
