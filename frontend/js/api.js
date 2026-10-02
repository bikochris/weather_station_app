const API_URL = window.WEATHER_API_URL || window.location.origin;
const TOKEN_KEY = "weather_station_access_token";
let currentUser = null;
let notificationTimer = null;
let notificationPage = 1;
let notificationUnreadOnly = false;
const OBSERVATION_ROLES = new Set([
    "Observation Officer",
    "Observation Supervisor"
]);
const DATA_QUALITY_ROLES = new Set([
    "Data Quality Control Officer",
    "Observation Processing Officer",
    "Observation Supervisor at HQ"
]);
const DATA_OPERATIONS_ROLES = new Set([
    "Data Quality Control Officer",
    "Observation Processing Officer"
]);
const READ_ONLY_ALL_ROLES = new Set([
    "Big Data Specialist",
    "Data Quality Control Specialist",
    "Division Manager"
]);
const MAINTENANCE_ROLE = "Instrument Maintenance and Calibration Officer";
const ADMIN_ONLY_PAGES = new Set([
    "activity.html",
    "users.html",
    "kpi.html",
    "search.html"
]);


function getAccessToken() {
    return sessionStorage.getItem(TOKEN_KEY);
}


function saveAccessToken(token) {
    sessionStorage.setItem(TOKEN_KEY, token);
}


function clearAccessToken() {
    sessionStorage.removeItem(TOKEN_KEY);
}


async function apiFetch(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const token = getAccessToken();

    if (token) {
        headers.set("Authorization", `Bearer ${token}`);
    }

    const response = await fetch(`${API_URL}${path}`, {
        ...options,
        headers,
        cache: options.cache || "no-store"
    });

    if (response.status === 401) {
        clearAccessToken();
        if (!window.location.pathname.endsWith("login.html")) {
            window.location.href = "login.html";
        }
    }

    return response;
}


async function requireSession() {
    if (!getAccessToken()) {
        window.location.href = "login.html";
        return null;
    }

    const response = await apiFetch("/auth/me");
    if (!response.ok) {
        return null;
    }

    currentUser = await response.json();
    if (currentUser.must_change_password) {
        window.location.href = "login.html";
        return null;
    }
    document.querySelectorAll("[data-user-name]").forEach((element) => {
        element.textContent = currentUser.full_name;
    });
    document.querySelectorAll("[data-user-department]").forEach((element) => {
        element.textContent = currentUser.department;
    });
    document.querySelectorAll(".it-only").forEach((element) => {
        element.hidden = currentUser.department !== "Admin";
    });
    document.querySelectorAll(".data-only").forEach((element) => {
        element.hidden = !DATA_QUALITY_ROLES.has(currentUser.department);
    });
    document.querySelectorAll(".maintenance-only").forEach((element) => {
        element.hidden = currentUser.department !== MAINTENANCE_ROLE;
    });
    document.querySelectorAll(".user-directory-access").forEach((element) => {
        element.hidden = !canViewUserDirectory();
    });
    document.querySelectorAll("nav a[href]").forEach((link) => {
        if (ADMIN_ONLY_PAGES.has(link.getAttribute("href"))) {
            link.hidden = !isITUser();
        }
    });
    const allowedPages = {
        "Admin": null,
        [MAINTENANCE_ROLE]: new Set([
            "index.html", "stations.html", "sites.html", "maintenance.html", "pre-maintenance-reports.html", "station-inspections.html", "discussions.html", "suspected-data.html",
            "station-instruments.html", "instrument-status-summary.html", "maintenance-summary.html", "data-counts.html", "station-visitors.html"
        ]),
        "Data Quality Control Officer": new Set([
            "index.html", "stations.html", "sites.html", "maintenance.html", "pre-maintenance-reports.html", "station-inspections.html", "discussions.html", "suspected-data.html",
            "station-instruments.html", "volunteer-data.html", "station-volunteers.html", "reporting-status.html",
            "data-requests.html", "data-counts.html", "instrument-status-summary.html", "maintenance-summary.html", "station-visitors.html"
        ]),
        "Observation Processing Officer": new Set([
            "index.html", "stations.html", "sites.html", "maintenance.html", "pre-maintenance-reports.html", "station-inspections.html", "discussions.html", "suspected-data.html",
            "station-instruments.html", "volunteer-data.html", "station-volunteers.html", "reporting-status.html",
            "data-requests.html", "data-counts.html", "instrument-status-summary.html", "maintenance-summary.html", "station-visitors.html"
        ]),
        "Observation Officer": new Set([
            "index.html", "stations.html", "sites.html", "maintenance.html", "pre-maintenance-reports.html", "station-inspections.html", "discussions.html", "suspected-data.html",
            "station-instruments.html", "data-counts.html", "instrument-status-summary.html", "maintenance-summary.html", "station-visitors.html"
        ]),
        "Observation Supervisor": new Set([
            "index.html", "stations.html", "sites.html", "maintenance.html", "pre-maintenance-reports.html", "station-inspections.html", "discussions.html", "suspected-data.html",
            "station-instruments.html", "data-counts.html", "instrument-status-summary.html", "maintenance-summary.html", "station-visitors.html"
        ]),
        "Observation Supervisor at HQ": new Set([
            "index.html", "stations.html", "sites.html", "maintenance.html", "pre-maintenance-reports.html", "station-inspections.html", "discussions.html", "suspected-data.html",
            "station-instruments.html", "volunteer-data.html", "station-volunteers.html", "reporting-status.html", "data-counts.html", "instrument-status-summary.html", "maintenance-summary.html", "station-visitors.html"
        ]),
        "Big Data Specialist": null,
        "Data Quality Control Specialist": null,
        "Division Manager": null
    };
    const currentPage = window.location.pathname.split("/").pop() || "index.html";
    if (ADMIN_ONLY_PAGES.has(currentPage) && !isITUser()) {
        window.location.href = "index.html";
        return null;
    }
    const pageAccess = allowedPages[currentUser.department];
    document.querySelectorAll("nav a[href]").forEach((link) => {
        if (pageAccess && !pageAccess.has(link.getAttribute("href"))) {
            link.hidden = true;
        }
    });
    initializeNotificationCenter();
    return currentUser;
}


function isITUser() {
    return currentUser?.department === "Admin";
}


function isDataUser() {
    return DATA_QUALITY_ROLES.has(currentUser?.department);
}


function isMaintenanceUser() {
    return currentUser?.department === MAINTENANCE_ROLE;
}


function isObservationOfficer() {
    return OBSERVATION_ROLES.has(currentUser?.department);
}


function isReadOnlyAllUser() {
    return READ_ONLY_ALL_ROLES.has(currentUser?.department);
}


function canViewUserDirectory() {
    return isITUser();
}


function canViewInstrumentCatalog() {
    return isITUser() || isMaintenanceUser() || isReadOnlyAllUser();
}


function canWriteDataOperations() {
    return isITUser() || DATA_OPERATIONS_ROLES.has(currentUser?.department);
}


function canAccessVolunteerData() {
    return isITUser() || DATA_QUALITY_ROLES.has(currentUser?.department) ||
        isReadOnlyAllUser();
}


function canViewMonthlyReporting() {
    return isITUser() || DATA_QUALITY_ROLES.has(currentUser?.department) ||
        isReadOnlyAllUser();
}


function canManageMaintenance() {
    return isITUser() || isMaintenanceUser();
}


function canPerformDataQualityActions() {
    return isITUser() || isDataUser() || isObservationOfficer();
}


async function logout() {
    try {
        await apiFetch("/auth/logout", {method: "POST"});
    } finally {
        clearAccessToken();
        window.location.href = "login.html";
    }
}


function initializeShell() {
    const navigation = document.querySelector(".app-header nav");
    if (navigation) {
        let anchor = navigation.querySelector('a[href="station-instruments.html"]');
        if (!anchor) {
            const pages = [
                ["index.html", "Home"], ["stations.html", "Stations"], ["sites.html", "Sites"],
                ["maintenance.html", "Maintenance"], ["station-inspections.html", "Inspections"],
                ["discussions.html", "Discussions"], ["suspected-data.html", "QC"],
                ["instruments.html", "Instruments"], ["station-instruments.html", "Station instruments"],
                ["volunteer-data.html", "Volunteer data"], ["station-volunteers.html", "Volunteers at stations"], ["reporting-status.html", "Reporting"],
                ["data-requests.html", "Requests"], ["kpi.html", "KPI"], ["search.html", "Search"],
                ["users.html", "Users"]
            ];
            navigation.replaceChildren();
            pages.forEach(([href, label]) => {
                const link = document.createElement("a");
                link.href = href; link.textContent = label;
                if (window.location.pathname.endsWith(href)) link.className = "active";
                navigation.append(link);
            });
            anchor = navigation.querySelector('a[href="station-instruments.html"]');
        }
        const maintenanceLink = navigation.querySelector('a[href="maintenance.html"]');
        if (maintenanceLink && !navigation.querySelector('a[href="pre-maintenance-reports.html"]')) {
            const link = document.createElement("a");
            link.href = "pre-maintenance-reports.html";
            link.textContent = "Pre-maintenance reports";
            if (window.location.pathname.endsWith("pre-maintenance-reports.html")) link.className = "active";
            maintenanceLink.after(link);
        }
        if (anchor) {
            const pages = [
                ["data-counts.html", "Data counts"],
                ["instrument-status-summary.html", "Instrument summary"],
                ["maintenance-summary.html", "Maintenance summary"],
                ["station-visitors.html", "Station visitors"],
                ["station-volunteers.html", "Volunteers at stations"]
            ];
            let previous = anchor;
            pages.forEach(([href, label]) => {
                let link = navigation.querySelector(`a[href="${href}"]`);
                if (!link) {
                    link = document.createElement("a");
                    link.href = href;
                    link.textContent = label;
                    previous.after(link);
                }
                if (window.location.pathname.endsWith(href)) link.classList.add("active");
                previous = link;
            });
        }
    }
    document.querySelectorAll("[data-logout]").forEach((button) => {
        button.addEventListener("click", logout);
    });
}


function setupDistrictFilter(container, stations, onChange) {
    const field = document.createElement("div");
    field.className = "field filter-field";
    const label = document.createElement("label");
    label.htmlFor = "districtFilter";
    label.textContent = "District";
    const select = document.createElement("select");
    select.id = "districtFilter";
    select.appendChild(new Option("All districts", ""));
    [...new Set(stations.map(station => station.district).filter(Boolean))]
        .sort().forEach(district => select.appendChild(new Option(district, district)));
    select.addEventListener("change", onChange);
    field.append(label, select);
    container.prepend(field);
    return select;
}


function notificationTarget(notification) {
    const pages = {
        stations: "stations.html", instruments: "instruments.html", users: "users.html",
        maintenance: "maintenance.html", maintenance_report: "maintenance.html#maintenanceReports",
        pre_maintenance_report: "pre-maintenance-reports.html", station_visitors: "station-visitors.html",
        station_volunteers: "station-volunteers.html", data_counts: "data-counts.html",
        maintenance_summary: "maintenance-summary.html", volunteer_data: "volunteer-data.html",
        reporting_status: "reporting-status.html", data_requests: "data-requests.html"
    };
    if (pages[notification.related_record_type]) return pages[notification.related_record_type];
    if (notification.related_record_type === "suspected_data") {
        return `suspected-data.html?record=${notification.related_record_id}`;
    }
    if (notification.related_record_type === "station_inspection") {
        return `station-inspections.html?record=${notification.related_record_id}`;
    }
    if (notification.related_record_type === "station_inspection_report") {
        return "station-inspections.html#inspectionReports";
    }
    if (notification.related_record_type === "discussion") {
        return `discussions.html?record=${notification.related_record_id}`;
    }
    if (notification.related_record_type === "station_instrument") {
        return "instrument-status-summary.html";
    }
    return null;
}


async function markNotificationRead(notification) {
    const response = await apiFetch(`/notifications/${notification.notification_id}/read`, {
        method: "PUT"
    });
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to mark notification as read"));
        return;
    }
    const target = notificationTarget(notification);
    if (target) window.location.href = target;
    else await loadNotifications();
}


async function loadNotifications() {
    const list = document.getElementById("notificationList");
    const badge = document.getElementById("notificationBadge");
    if (!list || !badge || !currentUser) return;
    try {
        const response = await apiFetch(`/notifications?limit=20&page=${notificationPage}&unread_only=${notificationUnreadOnly}`);
        if (!response.ok) throw new Error("Unable to load notifications");
        const result = await response.json();
        if (notificationPage > result.total_pages) {
            notificationPage = result.total_pages;
            return await loadNotifications();
        }
        document.getElementById("notificationPageLabel").textContent = `${result.page} / ${result.total_pages}`;
        document.getElementById("notificationPrevious").disabled = result.page <= 1;
        document.getElementById("notificationNext").disabled = result.page >= result.total_pages;
        badge.textContent = result.unread_count > 99 ? "99+" : result.unread_count;
        badge.hidden = result.unread_count === 0;
        list.replaceChildren();
        if (!result.items.length) {
            const empty = document.createElement("p");
            empty.className = "notification-empty";
            empty.textContent = notificationUnreadOnly ? "No unread notifications" : "No notifications";
            list.appendChild(empty);
            return;
        }
        result.items.forEach((notification) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = `notification-item${notification.is_read ? "" : " unread"}`;
            const title = document.createElement("strong");
            title.textContent = notification.title;
            const message = document.createElement("span");
            message.textContent = notification.message;
            const time = document.createElement("small");
            time.textContent = new Date(notification.created_at).toLocaleString();
            button.append(title, message, time);
            button.addEventListener("click", () => markNotificationRead(notification));
            list.appendChild(button);
        });
    } catch {
        list.replaceChildren();
        const error = document.createElement("p");
        error.className = "notification-empty";
        error.textContent = "Notifications are unavailable. Try refreshing.";
        list.append(error);
    }
}


function initializeNotificationCenter() {
    const menu = document.querySelector(".user-menu");
    if (!menu || document.getElementById("notificationToggle")) return;
    menu.classList.add("notification-host");
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.id = "notificationToggle";
    toggle.className = "header-button notification-toggle";
    toggle.setAttribute("aria-label", "Notifications");
    toggle.setAttribute("aria-expanded", "false");
    toggle.textContent = "Notifications";
    const badge = document.createElement("span");
    badge.id = "notificationBadge";
    badge.className = "notification-badge";
    badge.hidden = true;
    toggle.appendChild(badge);

    const panel = document.createElement("div");
    panel.id = "notificationPanel";
    panel.className = "notification-panel";
    panel.hidden = true;
    const heading = document.createElement("div");
    heading.className = "notification-heading";
    const title = document.createElement("strong");
    title.textContent = "Notifications";
    const readAll = document.createElement("button");
    readAll.type = "button";
    readAll.className = "text-button";
    readAll.textContent = "Mark all read";
    readAll.addEventListener("click", async () => {
        const response = await apiFetch("/notifications/read-all", {method: "PUT"});
        if (!response.ok) {
            window.alert(await getErrorMessage(response, "Unable to mark notifications as read"));
            return;
        }
        notificationPage = 1;
        await loadNotifications();
    });
    heading.append(title, readAll);
    const list = document.createElement("div");
    list.id = "notificationList";
    list.className = "notification-list";
    const controls = document.createElement("div");
    controls.className = "notification-controls";
    const unreadLabel = document.createElement("label");
    const unread = document.createElement("input");
    unread.type = "checkbox";
    unread.addEventListener("change", () => {
        notificationUnreadOnly = unread.checked;
        notificationPage = 1;
        loadNotifications();
    });
    unreadLabel.append(unread, document.createTextNode(" Unread only"));
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "text-button";
    refresh.textContent = "Refresh";
    refresh.addEventListener("click", loadNotifications);
    controls.append(unreadLabel, refresh);
    if (isITUser()) {
        const activity = document.createElement("a");
        activity.href = "activity.html";
        activity.textContent = "Activity log";
        controls.append(activity);
    }
    const footer = document.createElement("div");
    footer.className = "notification-controls";
    const previous = document.createElement("button");
    previous.id = "notificationPrevious";
    previous.type = "button";
    previous.textContent = "<";
    previous.title = "Previous page";
    previous.setAttribute("aria-label", "Previous notifications");
    previous.addEventListener("click", () => {notificationPage = Math.max(1, notificationPage - 1); loadNotifications();});
    const pageLabel = document.createElement("span");
    pageLabel.id = "notificationPageLabel";
    const next = document.createElement("button");
    next.id = "notificationNext";
    next.type = "button";
    next.textContent = ">";
    next.title = "Next page";
    next.setAttribute("aria-label", "Next notifications");
    next.addEventListener("click", () => {notificationPage += 1; loadNotifications();});
    footer.append(previous, pageLabel, next);
    panel.append(heading, controls, list, footer);
    menu.insertBefore(toggle, menu.querySelector("[data-logout]"));
    menu.appendChild(panel);
    toggle.addEventListener("click", () => {
        panel.hidden = !panel.hidden;
        toggle.setAttribute("aria-expanded", String(!panel.hidden));
        if (!panel.hidden) loadNotifications();
    });
    loadNotifications();
    if (notificationTimer) window.clearInterval(notificationTimer);
    document.addEventListener("click", (event) => {
        if (!panel.hidden && !panel.contains(event.target) && !toggle.contains(event.target)) {
            panel.hidden = true;
            toggle.setAttribute("aria-expanded", "false");
        }
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !panel.hidden) {
            panel.hidden = true;
            toggle.setAttribute("aria-expanded", "false");
            toggle.focus();
        }
    });
    notificationTimer = window.setInterval(loadNotifications, 30000);
}


async function getErrorMessage(response, fallback) {
    try {
        const body = await response.json();
        const detail = body.detail;
        if (typeof detail === "string") return detail;
        if (Array.isArray(detail)) {
            const messages = detail.map((error) => {
                if (typeof error === "string") return error;
                const location = Array.isArray(error?.loc)
                    ? error.loc.filter((part) => !["body", "query", "path"].includes(part))
                    : [];
                const field = location
                    .map((part) => String(part).replaceAll("_", " "))
                    .join(" > ");
                const message = error?.msg || "Invalid value";
                return field ? `${field}: ${message}` : message;
            }).filter(Boolean);
            return messages.join("; ") || fallback;
        }
        if (detail && typeof detail === "object") {
            return detail.message || JSON.stringify(detail);
        }
        return fallback;
    } catch {
        return fallback;
    }
}


function appendCell(row, value) {
    const cell = document.createElement("td");
    cell.textContent = value ?? "";
    row.appendChild(cell);
    return cell;
}


function showTableMessage(table, columnCount, message) {
    table.replaceChildren();
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = columnCount;
    cell.textContent = message;
    row.appendChild(cell);
    table.appendChild(row);
}


function setMessage(element, message, type = "") {
    element.textContent = message;
    element.className = `message ${type}`.trim();
}


function isWithinDateRange(value, dateFrom, dateTo) {
    if (!value) return !dateFrom && !dateTo;
    const date = String(value).slice(0, 10);
    return (!dateFrom || date >= dateFrom) && (!dateTo || date <= dateTo);
}


function setMetric(elementId, value) {
    document.getElementById(elementId).textContent = value;
}


function renderBreakdown(containerId, entries, emptyMessage = "No data available") {
    const container = document.getElementById(containerId);
    container.replaceChildren();
    if (!entries.length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = emptyMessage;
        container.appendChild(empty);
        return;
    }
    entries.forEach(([label, value]) => {
        const item = document.createElement("div");
        item.className = "breakdown-item";
        const name = document.createElement("span");
        name.textContent = label;
        const count = document.createElement("strong");
        count.textContent = value;
        item.append(name, count);
        container.appendChild(item);
    });
}


function createCollectionState(sortBy, sortOrder = "asc") {
    return {page: 1, pageSize: 25, search: "", sortBy, sortOrder};
}


function buildQuery(parameters) {
    const query = new URLSearchParams();
    Object.entries(parameters).forEach(([key, value]) => {
        if (value !== "" && value !== null && value !== undefined) {
            query.set(key, value);
        }
    });
    return query;
}


function collectionParameters(state, extra = {}) {
    return buildQuery({
        page: state.page,
        page_size: state.pageSize,
        search: state.search,
        sort_by: state.sortBy,
        sort_order: state.sortOrder,
        ...extra
    });
}


function renderPagination(containerId, result, state, reload) {
    const container = document.getElementById(containerId);
    container.replaceChildren();
    const start = result.total ? (result.page - 1) * result.page_size + 1 : 0;
    const end = Math.min(result.page * result.page_size, result.total);
    const summary = document.createElement("span");
    summary.textContent = `Showing ${start}-${end} of ${result.total}`;
    const controls = document.createElement("div");
    controls.className = "pagination-buttons";
    const previous = document.createElement("button");
    previous.type = "button";
    previous.className = "secondary-button";
    previous.textContent = "Previous";
    previous.disabled = result.page <= 1;
    previous.addEventListener("click", () => {
        state.page -= 1;
        reload();
    });
    const page = document.createElement("span");
    page.textContent = `Page ${result.page} of ${result.pages}`;
    const next = document.createElement("button");
    next.type = "button";
    next.className = "secondary-button";
    next.textContent = "Next";
    next.disabled = result.page >= result.pages;
    next.addEventListener("click", () => {
        state.page += 1;
        reload();
    });
    controls.append(previous, page, next);
    container.append(summary, controls);
}


function bindCollectionControls({
    state,
    reload,
    searchId,
    pageSizeId,
    tableSelector,
    exportBasePath,
    csvButtonId,
    pdfButtonId,
    getExtraParameters = () => ({})
}) {
    const table = document.querySelector(tableSelector);
    table.dataset.serverSorted = "true";
    const headings = [...table.querySelectorAll("thead th[data-sort]")];
    const updateSortHeadings = () => headings.forEach((heading) => {
        const active = heading.dataset.sort === state.sortBy;
        heading.setAttribute("aria-sort", active
            ? (state.sortOrder === "asc" ? "ascending" : "descending")
            : "none");
        heading.title = active
            ? `Sort ${state.sortOrder === "asc" ? "descending" : "ascending"}`
            : "Sort ascending";
    });
    let searchTimer;
    document.getElementById(searchId).addEventListener("input", (event) => {
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(() => {
            state.search = event.target.value.trim();
            state.page = 1;
            reload();
        }, 350);
    });
    document.getElementById(pageSizeId).addEventListener("change", (event) => {
        state.pageSize = Number(event.target.value);
        state.page = 1;
        reload();
    });
    headings.forEach((heading) => {
        heading.classList.add("sortable-heading");
        heading.tabIndex = 0;
        const sort = () => {
            const column = heading.dataset.sort;
            state.sortOrder = state.sortBy === column && state.sortOrder === "asc"
                ? "desc"
                : "asc";
            state.sortBy = column;
            state.page = 1;
            updateSortHeadings();
            reload();
        };
        heading.addEventListener("click", sort);
        heading.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                sort();
            }
        });
    });
    updateSortHeadings();
    document.getElementById(csvButtonId).addEventListener(
        "click",
        () => downloadCollectionExport(
            exportBasePath,
            "csv",
            state,
            getExtraParameters()
        )
    );
    document.getElementById(pdfButtonId).addEventListener(
        "click",
        () => downloadCollectionExport(
            exportBasePath,
            "pdf",
            state,
            getExtraParameters()
        )
    );
}


function sortableCellValue(cell, dateColumn) {
    const raw = (cell?.dataset.sortValue || cell?.textContent || "").trim();
    if (!raw || raw === "-") return null;
    if (dateColumn) {
        const localDate = raw.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})(.*)$/);
        if (localDate) return `${localDate[3]}-${localDate[2].padStart(2, "0")}-${localDate[1].padStart(2, "0")}${localDate[4]}`;
        const parsed = Date.parse(raw);
        if (!Number.isNaN(parsed)) return parsed;
    }
    const numeric = raw.replace(/,/g, "").replace(/%$/, "");
    if (/^-?\d+(?:\.\d+)?$/.test(numeric)) return Number(numeric);
    return raw;
}


function enableLocalTableSorting(table) {
    if (table.dataset.serverSorted) return;
    const body = table.tBodies[0];
    if (!body) return;
    const headings = [...table.querySelectorAll("thead th")];
    const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: "base"});
    let activeIndex = -1;
    let direction = "asc";
    let scheduled = false;
    const applySort = () => {
        scheduled = false;
        if (activeIndex < 0) return;
        const allRows = [...body.rows];
        const rows = allRows.filter(row => !row.dataset.detailFor);
        const visibleHeadings = headings.filter(heading => !heading.hidden);
        if (rows.some(row => row.cells.length !== visibleHeadings.length)) return;
        const detailRows = new Map(allRows.filter(row => row.dataset.detailFor)
            .map(row => [row.dataset.detailFor, row]));
        const cellIndex = headings.slice(0, activeIndex).filter(heading => !heading.hidden).length;
        const dateColumn = /date|month|period|time|updated|uploaded|registered|reviewed/i.test(headings[activeIndex].textContent);
        const sorted = rows.map((row, index) => ({row, index})).sort((left, right) => {
            const a = sortableCellValue(left.row.cells[cellIndex], dateColumn);
            const b = sortableCellValue(right.row.cells[cellIndex], dateColumn);
            if (a == null || b == null) return a == null && b == null ? left.index - right.index : (a == null ? 1 : -1);
            const result = typeof a === "number" && typeof b === "number"
                ? a - b : collator.compare(String(a), String(b));
            return (direction === "asc" ? result : -result) || left.index - right.index;
        });
        if (sorted.some((entry, index) => entry.row !== rows[index])) {
            body.append(...sorted.flatMap(({row}) => {
                const detail = detailRows.get(row.dataset.rowId);
                return detail ? [row, detail] : [row];
            }));
        }
    };
    const scheduleSort = () => {
        if (scheduled || activeIndex < 0) return;
        scheduled = true;
        requestAnimationFrame(applySort);
    };
    new MutationObserver(scheduleSort).observe(body, {childList: true});
    headings.forEach((heading, index) => {
        if (heading.hidden || /^(actions?|manage)$/i.test(heading.textContent.trim())) return;
        heading.classList.add("sortable-heading");
        heading.tabIndex = 0;
        heading.setAttribute("aria-sort", "none");
        heading.title = "Sort ascending";
        const sort = () => {
            direction = activeIndex === index && direction === "asc" ? "desc" : "asc";
            activeIndex = index;
            headings.forEach(item => {
                item.setAttribute("aria-sort", item === heading
                    ? (direction === "asc" ? "ascending" : "descending") : "none");
                item.title = item === heading && direction === "asc" ? "Sort descending" : "Sort ascending";
            });
            scheduleSort();
        };
        heading.addEventListener("click", sort);
        heading.addEventListener("keydown", event => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                sort();
            }
        });
    });
}


window.addEventListener("load", () => {
    document.querySelectorAll("main table").forEach(enableLocalTableSorting);
});


function auditDisplayValue(value) {
    if (value == null || value === "") return "-";
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (Array.isArray(value)) {
        if (!value.length) return "-";
        if (value[0] && typeof value[0] === "object") {
            if (value[0].instrument_id && Object.hasOwn(value[0], "issue")) {
                return value.map(item => `Instrument ${item.instrument_id}: ${item.issue || "-"} / ${item.action_done || "-"} / ${item.recommendation || "-"}`).join("; ");
            }
            if (value[0].original_filename) return value.map(file => `${file.file_kind}: ${file.original_filename}`).join(", ");
            if (value[0].comment) return value.map(entry => `${entry.commented_by_username || "Unknown"}: ${entry.comment}`).join("; ");
            const last = value[value.length - 1];
            return [last.action_type, last.comment, last.status_after, last.reason]
                .filter(Boolean).join(" · ") || `${value.length} entries`;
        }
        return value.join(", ");
    }
    return String(value);
}


function renderRecordHistoryEntry(container, item) {
    const entry = document.createElement("div");
    entry.className = "record-history-entry";
    const meta = document.createElement("strong");
    meta.textContent = `${new Date(item.changed_at).toLocaleString()} · ${item.changed_by_username}`;
    const list = document.createElement("ul");
    const before = item.before_data || {};
    const after = item.after_data || {};
    const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
        .filter(key => !/^(created_at|updated_at|password_hash|created_by_user_id|recorded_by_user_id|updated_by_user_id|resolved_by_user_id|final_reviewed_by_user_id)$/.test(key));
    keys.forEach(key => {
        if (JSON.stringify(before[key]) === JSON.stringify(after[key])) return;
        const line = document.createElement("li");
        const label = key.replace(/_/g, " ").replace(/^./, letter => letter.toUpperCase());
        line.textContent = `${label}: ${auditDisplayValue(before[key])} → ${auditDisplayValue(after[key])}`;
        list.append(line);
    });
    if (!list.childElementCount) {
        const line = document.createElement("li");
        line.textContent = "Saved without changes to displayed fields";
        list.append(line);
    }
    entry.append(meta, list);
    container.append(entry);
}


async function loadRecordHistoryPanel(panel, content, entityType, entityId, page = 1) {
    if (page === 1) content.textContent = "Loading changes...";
    try {
        const params = new URLSearchParams({page: String(page), page_size: "100"});
        const response = await apiFetch(`/record-history/${entityType}/${encodeURIComponent(entityId)}?${params}`);
        if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load history"));
        const result = await response.json();
        if (!panel.isConnected || panel.hidden) return;
        if (page === 1) content.replaceChildren();
        content.querySelector(".record-history-more")?.remove();
        if (!result.items.length && page === 1) {
            content.textContent = "No edits recorded for this record yet.";
            return;
        }
        result.items.forEach(item => renderRecordHistoryEntry(content, item));
        if (page * result.page_size < result.total) {
            const more = document.createElement("button");
            more.type = "button";
            more.className = "secondary-button record-history-more";
            more.textContent = "Show more changes";
            more.addEventListener("click", () => loadRecordHistoryPanel(panel, content, entityType, entityId, page + 1));
            content.append(more);
        }
    } catch (error) {
        if (panel.isConnected) content.textContent = error.message;
    }
}


function attachRecordHistoryPanel(container, entityType, entityId) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "record-history-toggle";
    button.textContent = "History";
    button.setAttribute("aria-expanded", "false");
    const panel = document.createElement("div");
    panel.className = "record-history-content";
    panel.hidden = true;
    const content = document.createElement("div");
    panel.append(content);
    button.addEventListener("click", () => {
        panel.hidden = !panel.hidden;
        button.setAttribute("aria-expanded", String(!panel.hidden));
        if (!panel.hidden) loadRecordHistoryPanel(panel, content, entityType, entityId);
    });
    container.append(button, panel);
}


function attachRecordHistoryRows(tbody, entityType, items, idForItem) {
    const rows = [...tbody.rows];
    if (!items.length || rows.length !== items.length) return;
    rows.forEach((row, index) => {
        const itemEntity = typeof entityType === "function" ? entityType(items[index]) : entityType;
        const entityId = idForItem(items[index]);
        if (entityId == null) return;
        const key = `${itemEntity}:${entityId}`;
        row.dataset.rowId = key;
        row.classList.add("record-history-parent");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "record-history-toggle";
        button.textContent = "History";
        button.setAttribute("aria-label", `Show edit history for this record`);
        button.setAttribute("aria-expanded", "false");
        const firstCell = row.cells[0];
        firstCell.dataset.sortValue = firstCell.textContent.trim();
        firstCell.prepend(button);
        const detail = document.createElement("tr");
        detail.className = "record-history-detail";
        detail.dataset.detailFor = key;
        detail.id = `record-history-${itemEntity}-${String(entityId).replace(/[^a-zA-Z0-9-]/g, "-")}`;
        detail.hidden = true;
        button.setAttribute("aria-controls", detail.id);
        const cell = detail.insertCell();
        cell.colSpan = row.cells.length;
        const content = document.createElement("div");
        content.className = "record-history-content";
        cell.append(content);
        row.after(detail);
        const toggle = () => {
            detail.hidden = !detail.hidden;
            button.setAttribute("aria-expanded", String(!detail.hidden));
            if (!detail.hidden) loadRecordHistoryPanel(detail, content, itemEntity, entityId);
        };
        button.addEventListener("click", event => { event.stopPropagation(); toggle(); });
        row.addEventListener("click", event => {
            if (event.target.closest("button, a, input, select, textarea, summary")) return;
            toggle();
        });
    });
}


async function downloadCollectionExport(basePath, format, state, extra = {}) {
    const query = buildQuery({
        format,
        search: state.search,
        sort_by: state.sortBy,
        sort_order: state.sortOrder,
        ...extra
    });
    const response = await apiFetch(`${basePath}/export?${query}`);
    if (!response.ok) {
        window.alert(await getErrorMessage(response, "Unable to export records"));
        return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${basePath.split("/").filter(Boolean).pop()}.${format}`;
    link.click();
    URL.revokeObjectURL(url);
}
