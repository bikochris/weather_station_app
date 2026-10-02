let activityPage = 1;

async function loadActivity() {
    const message = document.getElementById("activityMessage");
    message.textContent = "Loading activity...";
    try {
        const response = await apiFetch(`/activity?page=${activityPage}`);
        if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load activity"));
        const result = await response.json();
        const rows = document.getElementById("activityRows");
        rows.replaceChildren();
        for (const item of result.items) {
            const row = document.createElement("tr");
            for (const value of [new Date(item.created_at).toLocaleString(), item.actor_name,
                item.action, item.resource, item.record_id || "-", item.affected_records]) {
                const cell = document.createElement("td");
                cell.textContent = value;
                row.append(cell);
            }
            rows.append(row);
        }
        message.textContent = result.total ? `${result.total} recorded actions` : "No activity recorded yet";
        document.getElementById("activityPage").textContent = `${result.page} / ${result.total_pages}`;
        document.getElementById("activityPrevious").disabled = result.page <= 1;
        document.getElementById("activityNext").disabled = result.page >= result.total_pages;
    } catch (error) {
        message.textContent = error.message;
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    const user = await requireSession();
    if (!user) return;
    if (!isITUser()) {
        window.location.href = "index.html";
        return;
    }
    document.getElementById("activityRefresh").addEventListener("click", loadActivity);
    document.getElementById("activityPrevious").addEventListener("click", () => {activityPage -= 1; loadActivity();});
    document.getElementById("activityNext").addEventListener("click", () => {activityPage += 1; loadActivity();});
    await loadActivity();
});
