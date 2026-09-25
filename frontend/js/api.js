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

    const response = await fetch(`${API_URL}${path}`, {...options, headers});

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
        element.hidden = currentUser.department !== "IT";
    });
    return currentUser;
}


function isITUser() {
    return currentUser?.department === "IT";
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
