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
    "deleted-items.html",
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


if ("serviceWorker" in navigator) {
    navigator.serviceWorker.getRegistrations().then(registrations =>
        Promise.all(registrations
            .filter(registration => registration.active?.scriptURL.endsWith("/app/sw.js"))
            .map(registration => registration.unregister()))
    ).catch(() => {});
    if ("caches" in window) {
        caches.keys().then(keys => Promise.all(keys
            .filter(key => key.startsWith("station-operations-inspection-"))
            .map(key => caches.delete(key)))).catch(() => {});
    }
}


function needsEditReason(path, method) {
    if (method === "PUT" || method === "PATCH") {
        return !/^\/(auth|notifications)\//.test(path);
    }
    if (method !== "POST") return false;
    if (/^\/(stations|instruments|station-instruments)\/import\?/.test(path)) {
        return new URLSearchParams(path.split("?", 2)[1]).get("mode") === "upsert";
    }
    return /^\/station-inspections\/\d+\/actions(?:\?|$)/.test(path);
}


function requestEditReason() {
    return new Promise(resolve => {
        const dialog = document.createElement("dialog");
        dialog.className = "edit-reason-dialog";
        const form = document.createElement("form");
        const title = document.createElement("h2");
        title.textContent = "Reason for change";
        const label = document.createElement("label");
        label.textContent = "Explain why this record is being changed";
        const input = document.createElement("textarea");
        input.required = true;
        input.maxLength = 500;
        input.rows = 4;
        label.append(input);
        const actions = document.createElement("div");
        actions.className = "edit-reason-actions";
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "secondary-button";
        cancel.textContent = "Cancel";
        cancel.addEventListener("click", () => dialog.close());
        const save = document.createElement("button");
        save.type = "submit";
        save.textContent = "Continue";
        actions.append(cancel, save);
        form.append(title, label, actions);
        form.addEventListener("submit", event => {
            event.preventDefault();
            if (input.value.trim()) dialog.close(input.value.trim());
            else input.setCustomValidity("Enter a reason for this change");
        });
        input.addEventListener("input", () => input.setCustomValidity(""));
        dialog.addEventListener("close", () => {
            const reason = dialog.returnValue || null;
            dialog.remove();
            resolve(reason);
        }, {once: true});
        dialog.append(form);
        document.body.append(dialog);
        dialog.showModal();
        input.focus();
    });
}


async function apiFetch(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const token = getAccessToken();
    const method = (options.method || "GET").toUpperCase();

    const requiresEditReason = options.requiresEditReason ?? needsEditReason(path, method);
    const reason = options.editReason || (requiresEditReason ? await requestEditReason() : null);
    if (requiresEditReason && !reason) {
        return new Response(JSON.stringify({detail: "Change cancelled"}), {
            status: 400, headers: {"Content-Type": "application/json"}
        });
    }
    if (reason) headers.set("X-Edit-Reason", encodeURIComponent(reason.trim()));

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
                ["data-requests.html", "Requests"], ["kpi.html", "Dashboard"], ["search.html", "Search"],
                ["users.html", "Users"], ["deleted-items.html", "Deleted items"]
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
        if (!navigation.querySelector('a[href="deleted-items.html"]')) {
            const link = document.createElement("a");
            link.href = "deleted-items.html";
            link.textContent = "Deleted items";
            if (window.location.pathname.endsWith("deleted-items.html")) link.className = "active";
            navigation.append(link);
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


const PAGE_HELP = {
    "login.html": ["Sign in", "Enter the username and password assigned to your account, then select Sign in. If this is your first sign-in, follow the prompt to replace your temporary password. Contact an administrator if you cannot access your account."],
    "index.html": ["Home", "Select a workspace tile or use the navigation to open the area you need. The tiles and navigation reflect the pages available to your account."],
    "stations.html": ["Stations", "Use the filters to find stations by location, category, or status. Open a row to view its edit history. Authorized users can add or edit a station, import records from the CSV template, and export the filtered list."],
    "sites.html": ["Sites", "Sites group stations that share a location. Filter the list to find a site, inspect its stations, and export the displayed results when needed."],
    "maintenance.html": ["Maintenance", "Choose a station and date, then select each serviced instrument and enter its issue, action, and recommendation. Maintenance reports are uploaded separately and may cover several stations. Use the history table to review past work. Administrators can configure category frequency targets."],
    "pre-maintenance-reports.html": ["Pre-maintenance reports", "Choose the period and stations covered, attach the report, and submit it. Use the register to find and download earlier reports."],
    "station-inspections.html": ["Inspections", "Select the station and inspection date, describe the finding, and attach a station photo. Submit the inspection for HQ review. The inspector, HQ, Maintenance, and Management panels appear as the case progresses; each team records its own action. Inspection reports can cover multiple stations."],
    "discussions.html": ["Discussions", "Open a discussion with a title and selected attendees. Attendees receive a notification and can respond in the discussion. The owner can close it when the conversation is complete."],
    "suspected-data.html": ["Quality control", "Report suspected data with the station and observation details. Follow the issue through resolution and final review. Use the filters to locate cases and export results where available."],
    "instruments.html": ["Instruments", "Manage the instrument catalog, including its category and parameters. Use the CSV template for bulk imports. This page defines instrument types; assign actual equipment to stations on Station instruments."],
    "station-instruments.html": ["Station instruments", "Assign an instrument to a station and record its model, status, installation and calibration dates. For sensing equipment, enter logger port, algorithm, and wiring information. Filter or export the inventory, and expand a row for its edit history."],
    "data-counts.html": ["Data counts", "Choose a month, select one or more station categories, and enter the combined number of records. Review totals by month, fiscal quarter, and fiscal year. The fiscal year runs from July through June."],
    "instrument-status-summary.html": ["Instrument summary", "Filter by district, station, or instrument to review operational condition and upcoming calibration or replacement dates. The summary reflects the station instrument records."],
    "maintenance-summary.html": ["Maintenance summary", "Select a fiscal year and apply the district, station, or category filters. Quarter cells show maintenance completed against the configured target. Open a row to see recorded maintenance dates, and export the filtered summary."],
    "station-visitors.html": ["Station visitors", "Record the visit date, station, institution or school, visitor category, mission, and number of visitors. Filter the register to review past visits. Administrators can manage visitor categories."],
    "volunteer-data.html": ["Volunteer data", "Upload monthly volunteer data files for the selected station and period. Review previous submissions and supervisor comments in the register."],
    "station-volunteers.html": ["Volunteers at stations", "Record each volunteer's station, identity and contact details, and account information. Filter, sort, or export the register; expand a row to review edits."],
    "reporting-status.html": ["Reporting", "Record the number of stations that reported for a month and optionally attach the non-reported list. Expected stations include Operational, Under maintenance, and Suspended. Review the monthly trend and open a row to see its edit history; the side panel shows current database counts."],
    "data-requests.html": ["Requests", "Record requests served by category for each month. Use the date range to compare monthly, fiscal-quarter, and fiscal-year totals and charts."],
    "kpi.html": ["Dashboard", "Choose a month to review activity and pending work across users. Refresh to load the latest figures. This page is available to administrators."],
    "search.html": ["Search", "Ask a question about station sites, locations, elevation, suspension, or maintenance. Review the related database records shown with the answer, and use follow-up questions for more detail."],
    "users.html": ["Users", "Administrators can create accounts, assign roles and stations, and manage account status. Search or export the directory, and expand a row to inspect its edit history."],
    "deleted-items.html": ["Deleted items", "Administrators can filter backed-up deleted records by type, district, status, and date. Review an item's details and history before restoring it. Export the filtered register when needed."],
    "activity.html": ["Activity log", "Review recorded user activity and apply the available filters to narrow the log. This page is available to administrators."]
};


function initializeHelp() {
    const page = window.location.pathname.split("/").pop() || "index.html";
    const [title, guidance] = PAGE_HELP[page] || ["Help", "Use the page controls to review available records. Contact an administrator if you need access or assistance."];
    const host = document.querySelector(".user-menu") || document.querySelector(".auth-panel");
    if (!host || host.querySelector(".page-help-button")) return;

    const button = document.createElement("button");
    button.type = "button";
    button.className = host.classList.contains("auth-panel") ? "page-help-button auth-help-button" : "header-button page-help-button";
    button.textContent = "Help";
    button.setAttribute("aria-label", `Help for ${title}`);
    const dialog = document.createElement("dialog");
    dialog.className = "page-help-dialog";
    dialog.setAttribute("aria-label", `${title} help`);
    const heading = document.createElement("h2");
    heading.textContent = `${title} help`;
    const paragraph = document.createElement("p");
    paragraph.textContent = guidance;
    const close = document.createElement("button");
    close.type = "button";
    close.className = "secondary-button";
    close.textContent = "Close";
    close.addEventListener("click", () => dialog.close());
    dialog.append(heading, paragraph, close);
    button.addEventListener("click", () => dialog.showModal());
    dialog.addEventListener("close", () => button.focus());
    if (host.classList.contains("auth-panel")) host.prepend(button);
    else host.insertBefore(button, host.querySelector("[data-logout]"));
    document.body.append(dialog);
}


document.addEventListener("DOMContentLoaded", initializeHelp);


function enhanceMultiFilter(select) {
    if (select.dataset.multiFilterReady) return;
    select.dataset.multiFilterReady = "true";
    const first = select.options[select.selectedIndex];
    const selected = new Set(first?.value && first.value !== "all" ? [first.value] : []);
    const emptyValue = select.id === "deletedStatus" ? "all" : "";
    const label = select.closest(".field")?.querySelector("label")?.textContent.trim() || "Options";
    const details = document.createElement("details");
    details.className = "multi-filter";
    details.setAttribute("aria-label", label);
    const summary = document.createElement("summary");
    const menu = document.createElement("div");
    menu.className = "multi-filter-menu";
    const search = document.createElement("input");
    search.type = "search";
    search.className = "multi-filter-search";
    search.placeholder = `Search ${label.toLowerCase()}`;
    search.setAttribute("aria-label", `Search ${label.toLowerCase()}`);
    const choices = document.createElement("div");
    choices.className = "multi-filter-choices";
    const empty = document.createElement("p");
    empty.className = "multi-filter-empty";
    empty.textContent = "No matching options";
    details.append(summary, menu);
    select.after(details);
    select.hidden = true;

    function updateSummary() {
        const firstSelected = [...select.options].find(option => selected.has(option.value));
        const emptyOption = [...select.options].find(option => option.value === emptyValue);
        summary.textContent = selected.size ?
            `[${selected.size}] ${firstSelected?.textContent || `${selected.size} selected`}` :
            (emptyOption?.textContent || select.options[0]?.textContent || `All ${label.toLowerCase()}`);
        const clear = menu.querySelector(".multi-filter-clear");
        if (clear) clear.disabled = !selected.size;
    }

    function filterChoices() {
        const query = search.value.trim().toLocaleLowerCase();
        let visible = 0;
        choices.querySelectorAll("label").forEach(choice => {
            choice.hidden = !choice.dataset.searchText.includes(query);
            if (!choice.hidden) visible += 1;
        });
        empty.hidden = visible > 0;
    }

    function render() {
        const options = [...select.options].filter(option => option.value && option.value !== "all");
        const available = new Set(options.map(option => option.value));
        for (const value of selected) if (!available.has(value)) selected.delete(value);
        menu.replaceChildren();
        const clear = document.createElement("button");
        clear.type = "button";
        clear.className = "multi-filter-clear";
        clear.textContent = "Clear selection";
        clear.disabled = !selected.size;
        clear.onclick = () => {
            selected.clear();
            choices.querySelectorAll("input").forEach(checkbox => {
                checkbox.checked = false;
                checkbox.closest("label").classList.remove("selected");
            });
            updateSummary();
            select.dispatchEvent(new Event("change", {bubbles: true}));
        };
        choices.replaceChildren();
        options.forEach(option => {
            const choice = document.createElement("label");
            choice.dataset.searchText = `${option.textContent} ${option.value}`.toLocaleLowerCase();
            choice.classList.toggle("selected", selected.has(option.value));
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.value = option.value;
            checkbox.setAttribute("aria-label", option.textContent);
            checkbox.checked = selected.has(option.value);
            checkbox.onchange = () => {
                if (checkbox.checked) selected.add(option.value);
                else selected.delete(option.value);
                choice.classList.toggle("selected", checkbox.checked);
                updateSummary();
                select.dispatchEvent(new Event("change", {bubbles: true}));
            };
            choice.append(checkbox, document.createTextNode(option.textContent));
            choices.append(choice);
        });
        menu.append(search, choices, empty, clear);
        filterChoices();
        updateSummary();
    }
    search.addEventListener("input", filterChoices);
    details.addEventListener("toggle", () => {
        if (details.open) {
            document.querySelectorAll(".multi-filter[open]").forEach(other => {
                if (other !== details) other.open = false;
            });
            search.focus();
        }
    });
    Object.defineProperty(select, "value", {
        configurable: true,
        get: () => [...selected].join("|") || emptyValue,
        set: value => {
            selected.clear();
            String(value || "").split("|").filter(Boolean).forEach(part => selected.add(part));
            render();
        }
    });
    new MutationObserver(render).observe(select, {childList: true, subtree: true});
    render();
}


function initializeMultiFilters() {
    const supported = select => select.matches("select[id$='Filter'], select[id^='filter'], select[id^='deleted']")
        && !["deletedPageSize"].includes(select.id);
    const scan = node => {
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        if (node.matches?.("select") && supported(node)) enhanceMultiFilter(node);
        node.querySelectorAll?.("select").forEach(select => {
            if (supported(select)) enhanceMultiFilter(select);
        });
    };
    scan(document.body);
    new MutationObserver(records => records.forEach(record =>
        record.addedNodes.forEach(scan))).observe(document.body, {childList: true, subtree: true});
    document.addEventListener("click", event => {
        document.querySelectorAll(".multi-filter[open]").forEach(details => {
            if (!details.contains(event.target)) details.open = false;
        });
    });
    document.addEventListener("keydown", event => {
        if (event.key === "Escape") {
            document.querySelectorAll(".multi-filter[open]").forEach(details => {
                details.open = false;
                details.querySelector("summary")?.focus();
            });
        }
    });
}


document.addEventListener("DOMContentLoaded", initializeMultiFilters);


function matchesSelectedFilter(value, selection) {
    return !selection || selection === "all" || selection.split("|").includes(String(value));
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
    const csvButton = csvButtonId ? document.getElementById(csvButtonId) : null;
    const pdfButton = pdfButtonId ? document.getElementById(pdfButtonId) : null;
    const entityType = {
        "/stations": "stations", "/instruments": "instruments",
        "/maintenance": "maintenance", "/station-instruments": "station_instruments",
        "/suspected-data": "suspected_data", "/users": "users",
        "/station-volunteers": "station_volunteers"
    }[exportBasePath];
    let historyChoice = null;
    if (entityType && csvButton) {
        const label = document.createElement("label");
        label.className = "export-history-option";
        historyChoice = document.createElement("input");
        historyChoice.type = "checkbox";
        label.append(historyChoice, document.createTextNode("Include edit history (separate CSV)"));
        csvButton.before(label);
    }
    const exportWithChoice = format => downloadCollectionExport(
        exportBasePath, format, state, getExtraParameters(),
        historyChoice?.checked ? entityType : null,
        historyChoice?.checked ? historyColumnsForTable(table, entityType,
            table.querySelector("tbody tr.record-history-parent")?.cells.length || null) : null
    );
    csvButton?.addEventListener("click", () => exportWithChoice("csv"));
    pdfButton?.addEventListener("click", () => exportWithChoice("pdf"));
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


function stationInstrumentHistoryValue(key, value) {
    if (value != null && key === "station_id" && typeof stationInstrumentStations !== "undefined") {
        const station = stationInstrumentStations.find(item => String(item.station_id) === String(value));
        if (station) return `${station.station_code} - ${station.station_name} (ID ${value})`;
    }
    if (value != null && key === "instrument_id") {
        const catalog = typeof stationInstrumentCatalog === "undefined" ? [] : stationInstrumentCatalog;
        const record = typeof stationInstrumentRecords === "undefined" ? null
            : stationInstrumentRecords.find(item => String(item.instrument_id) === String(value));
        const instrument = catalog.find(item => String(item.instrument_id) === String(value));
        const name = instrument?.instrument_name || record?.instrument_name;
        if (name) return `${name} (ID ${value})`;
    }
    if (key !== "wiring_colors") return auditDisplayValue(value);
    let colors = value;
    if (typeof colors === "string") {
        try { colors = JSON.parse(colors); } catch { return colors; }
    }
    if (!Array.isArray(colors)) return "-";
    return colors.map((color, index) => color ? `${index + 1}: ${color}` : null)
        .filter(Boolean).join("; ") || "-";
}


function historyVisibleKeys(before, after) {
    return [...new Set([...Object.keys(after), ...Object.keys(before)])]
        .filter(key => !/(password|secret|token|session|^created_by_user_id$|^updated_by_user_id$|^recorded_by_user_id$|^resolved_by_user_id$|^final_reviewed_by_user_id$)/i.test(key));
}


function historySnapshotValue(entityType, key, value) {
    if (entityType === "station_instruments") return stationInstrumentHistoryValue(key, value);
    if (value == null || value === "") return "-";
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (Array.isArray(value)) return value.map(item =>
        typeof item === "object" ? Object.values(item).filter(part => part != null).join(" / ") : item
    ).join("; ") || "-";
    if (typeof value === "object") return Object.entries(value)
        .map(([name, part]) => `${name.replaceAll("_", " ")}: ${part}`).join("; ") || "-";
    return String(value);
}


const historyColumnAliases = {
    stations: {"station id": "station_code", "station": "station_name",
        "station status": "status", "registered at": "created_at", "recorded by": "recorded_by_username"},
    instruments: {"instrument": "instrument_name", "parameters it takes": "parameters_taken",
        "station categories": "station_categories", "status": "is_active", "recorded by": "recorded_by_username"},
    station_instruments: {"station": "station_id", "station category": "station_category",
        "instrument": "instrument_id", "parameters": "parameters_taken", "logger ports": "data_logger_ports",
        "wiring": "wiring_colors", "recorded by": "recorded_by_username", "recorded at": "created_at",
        "updated by": "updated_by_username"},
    maintenance: {"visit id": "maintenance_id", "station": "station_id", "date": "maintenance_date",
        "instrument": "instrument_details", "issue": "issue", "action done": "activity_done",
        "recommendations": "recommendations", "recorded by": "recorded_by_username"},
    suspected_data: {"station": "station_id", "measurement": "issue",
        "maintenance findings": "maintenance_issue",
        "reported by": "reported_by_username", "resolved by": "resolved_by_username",
        "final reviewed by": "final_reviewed_by_username", "finally solved?": "final_is_solved"},
    station_inspections: {"date": "inspection_date", "station": "station_id",
        "stage": "workflow_stage", "reason": "not_solved_reason", "inspector": "created_by_username",
        "updated": "updated_at", "evidence": "evidence"},
    data_requests: {"month": "request_month", "category": "category",
        "requests served": "served_requests", "recorded by": "recorded_by_username", "updated": "updated_at"},
    category_data_counts: {"month": "record_month", "station category": "station_category",
        "records": "record_count", "recorded by": "recorded_by_username", "updated": "updated_at"},
    combined_data_counts: {"month": "record_month", "station category": "station_categories",
        "records": "record_count", "recorded by": "recorded_by_username", "updated": "updated_at"},
    station_visitors: {"visit date": "period_start", "station": "station_id",
        "institution or school": "institution", "mission": "mission", "category": "category_id",
        "visitors": "visitor_count", "recorded by": "recorded_by_username"},
    station_volunteers: {"station id": "station_id", "station name": "station_id",
        "volunteer name": "volunteer_name", "id": "volunteer_identifier"},
    users: {"name": "full_name", "role": "department", "assigned stations": "assigned_station_ids",
        "status": "is_active"},
    reporting_status: {"month": "report_month", "operational": "operational_stations",
        "under maintenance": "under_maintenance_stations", "suspended": "suspended_stations",
        "expected": "expected_stations", "reported": "reported_stations",
        "pending": "pending_stations", "reporting %": "reporting_percent",
        "non-reported list": "non_reported_file", "recorded by": "recorded_by_username"},
    volunteer_data: {"month": "report_month", "monthly qc report": "files",
        "filtered data": "files", "filled data": "files", "supervisor comments": "comments"}
};


function historyColumnsForTable(table, entityType, count = null, row = null) {
    const headings = [...table.querySelectorAll("thead tr:first-child th")];
    return headings.slice(0, count ?? headings.filter(heading => !heading.hidden).length).map((heading, index) => {
        const label = heading?.textContent.trim() || `Column ${index + 1}`;
        const normalized = label.toLowerCase();
        const source = /^(actions?|manage)$/.test(normalized) ? null
            : historyColumnAliases[entityType]?.[normalized]
                || heading?.dataset.sort || normalized.replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
        const fallback = row?.cells[index]?.dataset.sortValue || row?.cells[index]?.textContent.trim() || "-";
        return {label, source, fallback};
    });
}


function historyColumnsForRow(row, entityType) {
    return historyColumnsForTable(row.closest("table"), entityType, row.cells.length, row);
}


function historyColumnValue(entityType, column, snapshot) {
    if (!column.source) return "";
    if (column.source === "pending_stations") {
        const expected = Number(snapshot.expected_stations);
        const reported = Number(snapshot.reported_stations);
        return Number.isFinite(expected) && Number.isFinite(reported)
            && snapshot.expected_stations != null && snapshot.reported_stations != null
            ? String(Math.max(0, expected - reported)) : "-";
    }
    if (column.source === "reporting_percent") {
        const expected = Number(snapshot.expected_stations);
        return snapshot.operational_stations != null && expected > 0
            ? `${((Number(snapshot.operational_stations) / expected) * 100).toFixed(1)}%` : "-";
    }
    if (column.source === "is_active") return snapshot.is_active == null ? "-" : snapshot.is_active ? "Active" : "Inactive";
    if (column.source === "files" && Array.isArray(snapshot.files)) {
        const kinds = {"monthly qc report": "monthly_qc", "filtered data": "filtered_data", "filled data": "filled_data"};
        const file = snapshot.files.find(item => item.file_kind === kinds[column.label.toLowerCase()]);
        return file?.original_filename || "-";
    }
    const alternatives = entityType === "maintenance" ? {
        instrument_details: "instrument_name", activity_done: "action_done",
        recommendations: "recommendation"
    } : {};
    const value = snapshot[column.source] === undefined
        ? snapshot[alternatives[column.source]] : snapshot[column.source];
    if (value === undefined) return column.fallback || "-";
    return historySnapshotValue(entityType, column.source, value);
}


function renderRecordHistoryTable(container, items, entityType, columns = null) {
    if (!items.length) return;
    let wrap = [...container.children].find(child => child.classList.contains("record-history-table-wrap"));
    if (!wrap) {
        wrap = document.createElement("div");
        wrap.className = "record-history-table-wrap";
        container.append(wrap);
    }
    const table = wrap.querySelector("table") || document.createElement("table");
    table.className = "record-history-table";
    table.setAttribute("aria-label", "Edit history");
    table.historyItems = [...(table.historyItems || []), ...items];
    const displayColumns = columns || [...new Set(table.historyItems.flatMap(item =>
        historyVisibleKeys(item.before_data || {}, item.after_data || {})))].map(key => ({
        label: key.replace(/_/g, " ").replace(/^./, letter => letter.toUpperCase()), source: key
    }));
    const head = document.createElement("thead");
    const header = document.createElement("tr");
    ["Changed at", "Changed by", "Change type", "Reason", ...displayColumns.map(column => column.label)]
        .forEach(label => {
            const heading = document.createElement("th");
            heading.scope = "col";
            heading.textContent = label;
            header.append(heading);
        });
    head.append(header);
    const body = document.createElement("tbody");
    table.historyItems.forEach(item => {
        const row = document.createElement("tr");
        const before = item.before_data || {};
        const after = item.after_data || {};
        const action = item.action || (Object.keys(before).length ? "Edited" : "Created");
        [new Date(item.changed_at).toLocaleString(), item.changed_by_username, action,
            item.edit_reason || "-"].forEach(value => appendCell(row, value));
        displayColumns.forEach(column => {
            const cell = row.insertCell();
            const previousValue = historyColumnValue(entityType, column, before);
            const nextValue = historyColumnValue(entityType, column, after);
            cell.textContent = nextValue;
            if (column.source && previousValue !== nextValue) {
                cell.className = "history-value-changed";
                const previous = document.createElement("small");
                previous.textContent = `Was: ${previousValue}`;
                cell.append(previous);
            }
        });
        body.append(row);
    });
    table.replaceChildren(head, body);
    wrap.append(table);
}


async function downloadRecordHistoryCsv(entityType, entityIds, columns = null) {
    const response = await apiFetch("/record-history/export", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({entity_type: entityType, entity_ids: entityIds.map(String), columns})
    });
    if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to export edit history"));
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `${entityType}-edit-history.csv`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}


function recordHistoryDownloadButton(entityType, entityId, columns = null) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary-button record-history-download";
    button.textContent = "Download history CSV";
    button.addEventListener("click", async event => {
        event.stopPropagation();
        button.disabled = true;
        try { await downloadRecordHistoryCsv(entityType, Array.isArray(entityId) ? entityId : [entityId], columns); }
        catch (error) { window.alert(error.message); }
        finally { button.disabled = false; }
    });
    return button;
}


async function loadRecordHistoryPanel(panel, content, entityType, entityId, page = 1, columns = null) {
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
        renderRecordHistoryTable(content, result.items, entityType, columns);
        if (page * result.page_size < result.total) {
            const more = document.createElement("button");
            more.type = "button";
            more.className = "secondary-button record-history-more";
            more.textContent = "Show more changes";
            more.addEventListener("click", () => loadRecordHistoryPanel(panel, content, entityType, entityId, page + 1, columns));
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
    panel.append(recordHistoryDownloadButton(entityType, entityId), content);
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
        const columns = historyColumnsForRow(row, itemEntity);
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
        const download = recordHistoryDownloadButton(itemEntity, entityId, columns);
        cell.append(download, content);
        row.after(detail);
        const toggle = () => {
            detail.hidden = !detail.hidden;
            button.setAttribute("aria-expanded", String(!detail.hidden));
            if (!detail.hidden) loadRecordHistoryPanel(detail, content, itemEntity, entityId, 1, columns);
        };
        button.addEventListener("click", event => { event.stopPropagation(); toggle(); });
        row.addEventListener("click", event => {
            if (event.target.closest("button, a, input, select, textarea, summary")) return;
            toggle();
        });
    });
}


async function filteredHistoryRecordIds(basePath, entityType, state, extra, matches = () => true) {
    const idField = {
        stations: "station_id", instruments: "instrument_id",
        maintenance: "maintenance_id", station_instruments: "station_instrument_id",
        suspected_data: "suspected_data_id", users: "user_id",
        station_volunteers: "volunteer_id"
    }[entityType];
    const ids = [];
    for (let page = 1; page <= 50; page += 1) {
        const query = buildQuery({page, page_size: 100, search: state.search,
            sort_by: state.sortBy, sort_order: state.sortOrder, ...extra});
        const response = await apiFetch(`${basePath}?${query}`);
        if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load filtered records for history"));
        const result = await response.json();
        const items = Array.isArray(result) ? result : result.items || [];
        ids.push(...items.filter(matches).map(item => item[idField]).filter(id => id != null));
        if (ids.length > 5000) throw new Error("History export supports at most 5,000 filtered records. Narrow the filters and try again.");
        if (Array.isArray(result) || page >= result.pages || items.length < 100) break;
        if (page === 50) throw new Error("History export supports at most 5,000 filtered records. Narrow the filters and try again.");
    }
    return ids;
}


async function downloadCollectionExport(basePath, format, state, extra = {}, historyEntityType = null, historyColumns = null) {
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
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    if (historyEntityType) {
        try {
            const ids = await filteredHistoryRecordIds(basePath, historyEntityType, state, extra);
            if (ids.length) await downloadRecordHistoryCsv(historyEntityType, ids, historyColumns);
            else window.alert("No records match these filters, so there is no edit history to download.");
        } catch (error) {
            window.alert(`The main export downloaded, but edit history did not: ${error.message}`);
        }
    }
}
