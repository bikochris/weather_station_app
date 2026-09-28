let discussionUsers = [];
let selectedDiscussion = null;
const selectedDiscussionUsers = new Set();
const discussionCollection = createCollectionState("created_at", "desc");

function renderDiscussionAttendees() {
    const search = document.getElementById("discussionAttendeeSearch").value.trim().toLowerCase();
    const container = document.getElementById("discussionAttendees");
    container.replaceChildren();
    const matching = discussionUsers.filter((user) =>
        [user.full_name, user.username, user.department].some((value) => String(value).toLowerCase().includes(search))
    );
    matching.forEach((user) => {
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = selectedDiscussionUsers.has(user.user_id);
        checkbox.addEventListener("change", () => {
            if (checkbox.checked) selectedDiscussionUsers.add(user.user_id);
            else selectedDiscussionUsers.delete(user.user_id);
            setMetric("discussionAttendeeCount", selectedDiscussionUsers.size);
        });
        const identity = document.createElement("span");
        const name = document.createElement("strong");
        name.textContent = user.full_name;
        const role = document.createElement("small");
        role.textContent = `${user.department} · ${user.username}`;
        identity.append(name, role);
        label.append(checkbox, identity);
        container.appendChild(label);
    });
    if (!matching.length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = "No users match this search.";
        container.appendChild(empty);
    }
    setMetric("discussionAttendeeCount", selectedDiscussionUsers.size);
}

async function loadDiscussionUsers() {
    const response = await apiFetch("/discussion-users");
    if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load users"));
    discussionUsers = await response.json();
    renderDiscussionAttendees();
}

function discussionStatus(value) {
    const status = document.createElement("span");
    status.className = `status ${value.toLowerCase()}`;
    status.textContent = value;
    return status;
}

function renderDiscussionList(items) {
    const container = document.getElementById("discussionList");
    container.replaceChildren();
    if (!items.length) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "No discussions match the current filters.";
        container.appendChild(empty);
        return;
    }
    items.forEach((discussion) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "discussion-list-item";
        const top = document.createElement("span");
        top.className = "discussion-list-heading";
        const title = document.createElement("strong");
        title.textContent = discussion.title;
        top.append(title, discussionStatus(discussion.status));
        const meta = document.createElement("span");
        meta.className = "discussion-list-meta";
        meta.textContent = `${discussion.created_by_username || "Former user"} · ${discussion.participants.length} attendees · ${discussion.messages.length} responses · ${new Date(discussion.created_at).toLocaleString()}`;
        button.append(top, meta);
        button.addEventListener("click", () => openDiscussion(discussion));
        container.appendChild(button);
    });
}

async function loadDiscussions() {
    const query = new URLSearchParams({
        page: discussionCollection.page,
        page_size: discussionCollection.pageSize,
        search: discussionCollection.search,
        status: document.getElementById("discussionStatusFilter").value
    });
    const response = await apiFetch(`/discussions?${query}`);
    if (!response.ok) throw new Error(await getErrorMessage(response, "Unable to load discussions"));
    const result = await response.json();
    setMetric("discussionTotalMetric", result.summary.total);
    setMetric("discussionOpenMetric", result.summary.open);
    setMetric("discussionClosedMetric", result.summary.closed);
    renderDiscussionList(result.items);
    renderPagination("discussionPagination", result, discussionCollection, loadDiscussions);
}

function renderDiscussionMessages(discussion) {
    const container = document.getElementById("discussionMessages");
    container.replaceChildren();
    if (!discussion.messages.length) {
        const empty = document.createElement("p");
        empty.className = "discussion-empty-message";
        empty.textContent = "No responses yet. Start the conversation below.";
        container.appendChild(empty);
        return;
    }
    discussion.messages.forEach((message) => {
        const article = document.createElement("article");
        if (message.posted_by_user_id === currentUser.user_id) article.classList.add("is-own-message");
        const heading = document.createElement("div");
        const author = document.createElement("strong");
        author.textContent = message.posted_by_full_name || message.posted_by_username || "Former user";
        const time = document.createElement("time");
        time.textContent = new Date(message.posted_at).toLocaleString();
        heading.append(author, time);
        const body = document.createElement("p");
        body.textContent = message.message;
        article.append(heading, body);
        container.appendChild(article);
    });
    container.scrollTop = container.scrollHeight;
}

function openDiscussion(discussion) {
    selectedDiscussion = discussion;
    document.getElementById("discussionWorkspace").hidden = false;
    document.getElementById("discussionWorkspaceTitle").textContent = discussion.title;
    const statusHost = document.getElementById("discussionWorkspaceStatus");
    statusHost.className = `status ${discussion.status.toLowerCase()}`;
    statusHost.textContent = discussion.status;
    document.getElementById("discussionCreator").textContent = discussion.created_by_username || "Former user";
    document.getElementById("discussionCreatedAt").textContent = new Date(discussion.created_at).toLocaleString();
    setMetric("discussionParticipantCount", discussion.participants.length);
    const participants = document.getElementById("discussionParticipantList");
    participants.replaceChildren(...discussion.participants.map((participant) => {
        const chip = document.createElement("span");
        chip.textContent = `${participant.full_name} · ${participant.department}`;
        return chip;
    }));
    renderDiscussionMessages(discussion);
    const isOpen = discussion.status === "Open";
    document.getElementById("discussionMessageForm").hidden = !isOpen;
    document.getElementById("discussionClosedNotice").hidden = isOpen;
    document.getElementById("closeDiscussion").hidden = !discussion.can_close;
    document.getElementById("discussionWorkspace").scrollIntoView({behavior: "smooth", block: "start"});
}

async function reloadSelectedDiscussion() {
    if (!selectedDiscussion) return;
    const response = await apiFetch(`/discussions?discussion_id=${selectedDiscussion.discussion_id}&page=1&page_size=10`);
    if (!response.ok) return;
    const result = await response.json();
    if (result.items.length) openDiscussion(result.items[0]);
    await loadDiscussions();
}

document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    try {
        await Promise.all([loadDiscussionUsers(), loadDiscussions()]);
    } catch (error) {
        setMessage(document.getElementById("discussionFormMessage"), error.message, "error");
    }

    document.getElementById("discussionAttendeeSearch").addEventListener("input", renderDiscussionAttendees);
    document.getElementById("discussionForm").addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = document.getElementById("discussionFormMessage");
        const response = await apiFetch("/discussions", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                title: document.getElementById("discussionTitle").value.trim(),
                opening_message: document.getElementById("discussionOpeningMessage").value.trim(),
                participant_user_ids: [...selectedDiscussionUsers]
            })
        });
        if (!response.ok) {
            setMessage(message, await getErrorMessage(response, "Unable to open discussion"), "error");
            return;
        }
        const result = await response.json();
        document.getElementById("discussionForm").reset();
        selectedDiscussionUsers.clear();
        renderDiscussionAttendees();
        setMessage(message, result.message, "success");
        await loadDiscussions();
    });

    document.getElementById("discussionMessageForm").addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!selectedDiscussion) return;
        const status = document.getElementById("discussionMessageStatus");
        const response = await apiFetch(`/discussions/${selectedDiscussion.discussion_id}/messages`, {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({message: document.getElementById("discussionMessage").value.trim()})
        });
        if (!response.ok) {
            setMessage(status, await getErrorMessage(response, "Unable to post response"), "error");
            return;
        }
        document.getElementById("discussionMessageForm").reset();
        await reloadSelectedDiscussion();
    });

    document.getElementById("closeDiscussion").addEventListener("click", async () => {
        if (!selectedDiscussion || !window.confirm("Close this discussion for all attendees?")) return;
        const response = await apiFetch(`/discussions/${selectedDiscussion.discussion_id}/close`, {method: "POST"});
        if (!response.ok) {
            window.alert(await getErrorMessage(response, "Unable to close discussion"));
            return;
        }
        await reloadSelectedDiscussion();
    });
    document.getElementById("hideDiscussion").addEventListener("click", () => {
        selectedDiscussion = null;
        document.getElementById("discussionWorkspace").hidden = true;
    });
    document.getElementById("refreshDiscussions").addEventListener("click", loadDiscussions);
    document.getElementById("discussionStatusFilter").addEventListener("change", () => {discussionCollection.page = 1; loadDiscussions();});
    let searchTimer;
    document.getElementById("discussionSearch").addEventListener("input", (event) => {
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(() => {
            discussionCollection.search = event.target.value.trim();
            discussionCollection.page = 1;
            loadDiscussions();
        }, 350);
    });
    document.getElementById("discussionPageSize").addEventListener("change", (event) => {
        discussionCollection.pageSize = Number(event.target.value);
        discussionCollection.page = 1;
        loadDiscussions();
    });

    const recordId = Number(new URLSearchParams(window.location.search).get("record"));
    if (recordId) {
        const response = await apiFetch(`/discussions?discussion_id=${recordId}&page=1&page_size=10`);
        if (response.ok) {
            const result = await response.json();
            if (result.items.length) openDiscussion(result.items[0]);
        }
    }
});
