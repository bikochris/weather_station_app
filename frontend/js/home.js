function setHomeMetric(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = value ?? "--";
}


function filterHomeTools() {
    document.querySelectorAll("[data-tool-page]").forEach((link) => {
        const page = link.dataset.toolPage;
        const navLink = document.querySelector(`nav a[href="${page}"]`);
        const available = Boolean(navLink && !navLink.hidden);
        link.hidden = !available;
    });
}


async function loadHomeSummary(path, metricId, summaryKey) {
    try {
        const response = await apiFetch(path);
        if (!response.ok) throw new Error("Summary unavailable");
        const result = await response.json();
        setHomeMetric(metricId, result.summary?.[summaryKey] ?? 0);
    } catch (_error) {
        setHomeMetric(metricId, "--");
    }
}


document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    const user = await requireSession();
    if (!user) return;

    document.getElementById("welcomeName").textContent = user.full_name.split(/\s+/)[0];
    document.getElementById("welcomeRole").textContent = user.department;
    document.getElementById("welcomeDate").textContent = new Intl.DateTimeFormat(
        undefined,
        {weekday: "long", day: "numeric", month: "long", year: "numeric"}
    ).format(new Date());

    filterHomeTools();
    await Promise.all([
        loadHomeSummary("/stations?page=1&page_size=10", "homeStationMetric", "total"),
        loadHomeSummary("/sites?page=1&page_size=10", "homeSiteMetric", "total_sites"),
        loadHomeSummary("/maintenance?page=1&page_size=10", "homeMaintenanceMetric", "total"),
        loadHomeSummary("/station-inspections?page=1&page_size=10", "homeInspectionMetric", "total"),
        loadHomeSummary("/discussions?page=1&page_size=10", "homeDiscussionMetric", "open")
    ]);
});
