const API_URL = window.WEATHER_API_URL || window.location.origin;
const TOKEN_KEY = "weather_station_access_token";
let currentUser = null;
let notificationTimer = null;


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
        element.hidden = currentUser.department !== "Data Quality Control";
    });
    document.querySelectorAll(".maintenance-only").forEach((element) => {
        element.hidden = currentUser.department !== "Maintenance";
    });
    const allowedPages = {
        "Admin": null,
        "Maintenance": new Set([
            "index.html", "maintenance.html", "suspected-data.html",
            "station-instruments.html"
        ]),
        "Data Quality Control": new Set([
            "index.html", "maintenance.html", "suspected-data.html",
            "station-instruments.html"
        ]),
        "Observation Officer": new Set([
            "index.html", "maintenance.html", "suspected-data.html",
            "station-instruments.html"
        ])
    };
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
    return currentUser?.department === "Data Quality Control";
}


function isMaintenanceUser() {
    return currentUser?.department === "Maintenance";
}


function isObservationOfficer() {
    return currentUser?.department === "Observation Officer";
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
    document.querySelectorAll("[data-logout]").forEach((button) => {
        button.addEventListener("click", logout);
    });
}


function notificationTarget(notification) {
    if (notification.related_record_type === "suspected_data") {
        return `suspected-data.html?record=${notification.related_record_id}`;
    }
    return null;
}


async function markNotificationRead(notification) {
    await apiFetch(`/notifications/${notification.notification_id}/read`, {
        method: "PUT"
    });
    const target = notificationTarget(notification);
    if (target) window.location.href = target;
    else await loadNotifications();
}


async function loadNotifications() {
    const list = document.getElementById("notificationList");
    const badge = document.getElementById("notificationBadge");
    if (!list || !badge || !currentUser) return;
    try {
        const response = await apiFetch("/notifications?limit=20");
        if (!response.ok) return;
        const result = await response.json();
        badge.textContent = result.unread_count > 99 ? "99+" : result.unread_count;
        badge.hidden = result.unread_count === 0;
        list.replaceChildren();
        if (!result.items.length) {
            const empty = document.createElement("p");
            empty.className = "notification-empty";
            empty.textContent = "No notifications";
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
        // Notification failures must not interrupt the working page.
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
        await apiFetch("/notifications/read-all", {method: "PUT"});
        await loadNotifications();
    });
    heading.append(title, readAll);
    const list = document.createElement("div");
    list.id = "notificationList";
    list.className = "notification-list";
    panel.append(heading, list);
    menu.insertBefore(toggle, menu.querySelector("[data-logout]"));
    menu.appendChild(panel);
    toggle.addEventListener("click", () => {
        panel.hidden = !panel.hidden;
        toggle.setAttribute("aria-expanded", String(!panel.hidden));
        if (!panel.hidden) loadNotifications();
    });
    loadNotifications();
    if (notificationTimer) window.clearInterval(notificationTimer);
    notificationTimer = window.setInterval(loadNotifications, 60000);
}


async function getErrorMessage(response, fallback) {
    try {
        const body = await response.json();
        return body.detail || fallback;
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
    document.querySelectorAll(`${tableSelector} th[data-sort]`).forEach((heading) => {
        heading.classList.add("sortable-heading");
        heading.tabIndex = 0;
        const sort = () => {
            const column = heading.dataset.sort;
            state.sortOrder = state.sortBy === column && state.sortOrder === "asc"
                ? "desc"
                : "asc";
            state.sortBy = column;
            state.page = 1;
            reload();
        };
        heading.addEventListener("click", sort);
        heading.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") sort();
        });
    });
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
