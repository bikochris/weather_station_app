let volunteerPermissions = {};

function fileLabel(file, fallback) {
    if (!file) return document.createTextNode(fallback);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "text-button";
    button.textContent = `${file.original_filename} (${Math.ceil(file.file_size / 1024)} KB)`;
    button.addEventListener("click", async () => {
        const response = await apiFetch(`/volunteer-data/files/${file.file_id}`);
        if (!response.ok) return window.alert(await getErrorMessage(response, "Unable to download file"));
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a"); link.href = url; link.download = file.original_filename; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 0);
    });
    const wrapper = document.createElement("div");
    wrapper.append(button, document.createElement("br"), document.createTextNode(`By ${file.uploaded_by_username || "Unknown"} · ${new Date(file.uploaded_at).toLocaleString()}`));
    return wrapper;
}

async function loadVolunteerData() {
    const table = document.getElementById("volunteerTable");
    showTableMessage(table, 6, "Loading monthly submissions...");
    const response = await apiFetch("/volunteer-data");
    if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load volunteer data"));
    const result = await response.json(); volunteerPermissions = result.permissions;
    const hasActions = Boolean(result.permissions.edit || result.permissions.delete);
    document.getElementById("volunteerActionsHeading").hidden = !hasActions;
    const canUpload = result.permissions.upload_qc || result.permissions.upload_filtered || result.permissions.upload_filled;
    document.getElementById("volunteerUploader").hidden = !canUpload;
    document.getElementById("monthlyQcField").hidden = !result.permissions.upload_qc;
    document.getElementById("filteredDataField").hidden = !result.permissions.upload_filtered;
    document.getElementById("filledDataField").hidden = !result.permissions.upload_filled;
    document.getElementById("supervisorCommentSection").hidden = !result.permissions.comment;
    table.replaceChildren();
    if (!result.items.length) showTableMessage(table, hasActions ? 6 : 5, "No monthly volunteer data has been uploaded.");
    let files = 0, complete = 0, comments = 0;
    result.items.forEach((item) => {
        const row = document.createElement("tr"); appendCell(row, item.report_month);
        ["monthly_qc", "filtered_data", "filled_data"].forEach((kind) => { const cell = appendCell(row, ""); cell.append(fileLabel(item.files[kind], "Pending")); });
        const commentCell = appendCell(row, "");
        if (!item.comments.length) commentCell.textContent = "Awaiting supervisor comment";
        item.comments.forEach((entry) => { const block = document.createElement("div"); block.className = "comment-entry"; block.textContent = `${entry.comment} — ${entry.commented_by_username || "Unknown"}, ${new Date(entry.commented_at).toLocaleString()}`; commentCell.appendChild(block); });
        if (hasActions) {
            const actionCell = appendCell(row, ""); const group = document.createElement("div"); group.className = "action-group";
            if (result.permissions.edit) {
                const edit = document.createElement("button"); edit.type = "button"; edit.className = "secondary-button"; edit.textContent = "Edit";
                edit.addEventListener("click", () => {
                    document.getElementById("reportMonth").value = item.report_month;
                    document.getElementById("commentMonth").value = item.report_month;
                    const target = !document.getElementById("volunteerUploader").hidden ? document.getElementById("volunteerUploader") : document.getElementById("supervisorCommentSection");
                    target.scrollIntoView({behavior: "smooth"});
                }); group.appendChild(edit);
            }
            if (result.permissions.delete) {
                const remove = document.createElement("button"); remove.type = "button"; remove.className = "danger-button"; remove.textContent = "Delete";
                remove.addEventListener("click", async () => {
                    if (!window.confirm(`Delete the ${item.report_month} volunteer data components assigned to your role?`)) return;
                    const response = await apiFetch(`/volunteer-data/${item.report_month}`, {method: "DELETE"});
                    if (!response.ok) return window.alert(await getErrorMessage(response, "Unable to delete monthly data"));
                    await loadVolunteerData();
                }); group.appendChild(remove);
            }
            actionCell.appendChild(group);
        }
        files += Object.keys(item.files).length; comments += item.comments.length;
        if (["monthly_qc", "filtered_data", "filled_data"].every((kind) => item.files[kind]) && item.comments.length) complete += 1;
        table.appendChild(row);
    });
    setMetric("volunteerMonthsMetric", result.items.length); setMetric("completePackagesMetric", complete); setMetric("uploadedFilesMetric", files); setMetric("supervisorCommentsMetric", comments);
}

async function uploadFile(month, kind, input) {
    const file = input.files[0]; if (!file) return false;
    const query = new URLSearchParams({report_month: month, file_kind: kind, filename: file.name});
    const response = await apiFetch(`/volunteer-data/files?${query}`, {method: "POST", headers: {"Content-Type": file.type || "application/octet-stream"}, body: file});
    if (!response.ok) throw new Error(await getErrorMessage(response, `Unable to upload ${file.name}`));
    return true;
}

document.addEventListener("DOMContentLoaded", async () => {
    initializeShell(); if (!await requireSession()) return;
    if (!canAccessVolunteerData()) { window.location.href = "index.html"; return; }
    const uploadMessage = document.getElementById("uploadMessage"), commentMessage = document.getElementById("commentMessage");
    document.getElementById("reportMonth").value = new Date().toISOString().slice(0, 7);
    document.getElementById("commentMonth").value = new Date().toISOString().slice(0, 7);
    document.getElementById("volunteerUploadForm").addEventListener("submit", async (event) => {
        event.preventDefault(); setMessage(uploadMessage, "Uploading...");
        try { const month = document.getElementById("reportMonth").value; const uploaded = await Promise.all([
            volunteerPermissions.upload_qc ? uploadFile(month, "monthly_qc", document.getElementById("monthlyQcFile")) : false,
            volunteerPermissions.upload_filtered ? uploadFile(month, "filtered_data", document.getElementById("filteredDataFile")) : false,
            volunteerPermissions.upload_filled ? uploadFile(month, "filled_data", document.getElementById("filledDataFile")) : false
        ]); if (!uploaded.some(Boolean)) throw new Error("Select at least one file."); event.target.reset(); document.getElementById("reportMonth").value = month; setMessage(uploadMessage, "Monthly files saved.", "success"); await loadVolunteerData(); }
        catch (error) { setMessage(uploadMessage, error.message, "error"); }
    });
    document.getElementById("commentForm").addEventListener("submit", async (event) => {
        event.preventDefault(); setMessage(commentMessage, "Saving...");
        const response = await apiFetch("/volunteer-data/comments", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({report_month: document.getElementById("commentMonth").value, comment: document.getElementById("supervisorComment").value.trim()})});
        if (!response.ok) return setMessage(commentMessage, await getErrorMessage(response, "Unable to save comment"), "error");
        document.getElementById("supervisorComment").value = ""; setMessage(commentMessage, "Supervisor comment saved.", "success"); await loadVolunteerData();
    });
    document.getElementById("refreshVolunteer").addEventListener("click", loadVolunteerData);
    try { await loadVolunteerData(); } catch (error) { showTableMessage(document.getElementById("volunteerTable"), 5, error.message); }
});
