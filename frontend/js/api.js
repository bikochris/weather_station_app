const API_URL = window.WEATHER_API_URL || window.location.origin;
const TOKEN_KEY = "weather_station_access_token";
let currentUser = null;


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
