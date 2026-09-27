let searchStationContext = null;


function appendChatMessage(role, text) {
    const message = document.createElement("div");
    message.className = `chat-message ${role}-message`;
    const heading = document.createElement("strong");
    heading.textContent = role === "user" ? currentUser.full_name : "Database assistant";
    const content = document.createElement("p");
    content.textContent = text;
    message.append(heading, content);
    const thread = document.getElementById("searchConversation");
    thread.appendChild(message);
    message.scrollIntoView({behavior: "smooth", block: "nearest"});
}


function renderSearchResults(items) {
    const container = document.getElementById("searchResults");
    container.replaceChildren();
    if (!items.length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = "No additional matching records found.";
        container.appendChild(empty);
        return;
    }
    items.forEach((item) => {
        const article = document.createElement("article");
        article.className = "search-result";
        const type = document.createElement("span");
        type.className = "status active";
        type.textContent = item.type;
        const title = document.createElement("strong");
        title.textContent = item.title;
        const detail = document.createElement("p");
        detail.textContent = item.detail || "";
        article.append(type, title, detail);
        container.appendChild(article);
    });
}


function clearConversation() {
    searchStationContext = null;
    const thread = document.getElementById("searchConversation");
    thread.replaceChildren();
    const welcome = document.createElement("div");
    welcome.className = "chat-message assistant-message";
    const heading = document.createElement("strong");
    heading.textContent = "Database assistant";
    const content = document.createElement("p");
    content.textContent = "Conversation cleared. Ask a new question about the database.";
    welcome.append(heading, content);
    thread.appendChild(welcome);
    renderSearchResults([]);
}


document.addEventListener("DOMContentLoaded", async () => {
    initializeShell();
    if (!await requireSession()) return;
    document.getElementById("clearConversation").addEventListener("click", clearConversation);
    document.getElementById("searchForm").addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = document.getElementById("searchMessage");
        const input = document.getElementById("searchPrompt");
        const prompt = input.value.trim();
        appendChatMessage("user", prompt);
        input.value = "";
        setMessage(message, "Searching the database...");
        try {
            const parameters = new URLSearchParams({q: prompt});
            if (searchStationContext) {
                parameters.set("context_station_id", searchStationContext);
            }
            const response = await apiFetch(`/data-search?${parameters}`);
            if (!response.ok) {
                throw new Error(await getErrorMessage(response, "Unable to search database"));
            }
            const result = await response.json();
            setMessage(message, "");
            searchStationContext = result.context_station_id || searchStationContext;
            appendChatMessage("assistant", result.answer);
            renderSearchResults(result.results);
            input.focus();
        } catch (error) {
            setMessage(message, error.message, "error");
            appendChatMessage("assistant", "I could not complete that database search.");
        }
    });
});
