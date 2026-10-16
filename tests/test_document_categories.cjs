const assert = require("node:assert/strict");
const path = require("node:path");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");

async function run() {
    const browser = await chromium.launch({channel: "chrome", headless: true});
    try {
        const page = await browser.newPage();
        await page.route("**/*", route => {
            const url = new URL(route.request().url());
            if (url.pathname.endsWith("workspace-pages.js")) return route.fulfill({body: "", contentType: "text/javascript"});
            return url.pathname.startsWith("/app/") ? route.continue() : route.fulfill({json: []});
        });
        await page.goto(`${process.env.TEST_APP_URL || "http://127.0.0.1:8000"}/app/documents.html`);
        await page.addScriptTag({path: path.join(__dirname, "../frontend/js/workspace-pages.js")});
        const result = await page.evaluate(async () => {
            const item = {document_id: 1, title: "Reference", category: "Policy", document_date: "2026-10-13",
                original_filename: "reference.pdf", description: "Rules", file_type: "PDF", file_size: 100,
                version: 1, created_by_username: "tester", can_edit: true};
            const saves = [];
            const categories = ["Policy", "Reports"];
            workspaceRequest = async (url, options) => {
                if (options) saves.push({url, method: options.method});
                if (url.endsWith("/categories")) {
                    if (options) categories.push(JSON.parse(options.body).name);
                    return categories;
                }
                return {items: [item], page: 1, pages: 1, page_size: 25, total: 1, summary: {total: 1, mine: 1, bytes: 100}};
            };
            attachRecordHistoryRows = () => {};
            resetWorkspaceEditor();
            const initial = workspaceField("entryCategory").value;
            await loadIdeaCategories();
            await loadWorkspaceEntries();
            editWorkspaceEntry(item);
            const edited = workspaceField("entryCategory").value;
            workspaceField("entryCategory").value = "Reports";
            await saveWorkspaceEntry({preventDefault() {}});
            workspaceField("documentCategoryName").value = "Training";
            await addWorkspaceDocumentCategory({preventDefault() {}});
            workspaceField("categoryFilter").value = "Policy";
            return {initial, edited, saves, filters: workspaceFilters(),
                cells: [...document.querySelectorAll("#workspaceRows tr:first-child td")].map(cell => cell.textContent),
                suggestions: [...document.querySelectorAll("#entryCategory option")].map(option => option.value)};
        });
        assert.equal(result.initial, "General");
        assert.equal(result.edited, "Policy");
        assert.equal(new URL(result.saves[0].url, "http://localhost").searchParams.get("category"), "Reports");
        assert.equal(result.saves[0].method, "PUT");
        assert.equal(result.filters.category, "Policy");
        assert.equal(result.cells[2], "Policy");
        assert.equal(result.cells.length, 12);
        assert.deepEqual(result.suggestions, ["General", "Policy", "Reports", "Training"]);
        assert.equal(result.saves[1].url, "/documents/categories");
        assert.equal(result.saves[1].method, "POST");
        for (const width of [1440, 390]) {
            await page.setViewportSize({width, height: 1000});
            await page.evaluate(() => revealPagePanel(document.getElementById("workspaceEditor")));
            await page.screenshot({path: path.join(process.env.TEMP || "/tmp", `document-category-${width}.png`), fullPage: true});
        }
        console.log("PASS: document category defaults, suggestions, editing, save, filter, and table alignment.");
    } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
