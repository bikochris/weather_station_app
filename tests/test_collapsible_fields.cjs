// Run against the local app with Playwright installed or PLAYWRIGHT_MODULE_PATH set.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");

async function run() {
    const browser = await chromium.launch({channel: "chrome", headless: true});
    try {
        const context = await browser.newContext();
        // Isolate shared field panels from page-specific data fetching.
        await context.route("**/*", route => {
            const pathname = new URL(route.request().url()).pathname;
            if (pathname.startsWith("/app/js/") && !pathname.endsWith("/api.js") && !pathname.endsWith("/config.js")) {
                return route.fulfill({body: "", contentType: "text/javascript"});
            }
            return pathname.startsWith("/app/") ? route.continue() : route.fulfill({json: {items: [], total: 0}});
        });
        const page = await context.newPage();
        const errors = [];
        page.on("pageerror", error => errors.push(error.message));
        const files = fs.readdirSync(path.join(__dirname, "../frontend"))
            .filter(file => file.endsWith(".html") && !["login.html", "index.html"].includes(file));
        const base = process.env.TEST_APP_URL || "http://127.0.0.1:8000";
        for (const file of files) {
            await page.goto(`${base}/app/${file}`);
            const visibleFields = await page.evaluate(() => [...document.querySelectorAll(
                "main input:not([type=hidden]), main textarea, main select"
            )].filter(element => element.checkVisibility()).map(element => element.id));
            assert.deepEqual(visibleFields, [], `${file} has expanded fields on first load`);
            assert.equal(await page.locator(".page-field-panel[open]").count(), 0);
            assert.equal(await page.locator(".page-section-panel[open]").count(), 0);
            assert.equal(await page.locator("main table:visible").count(), 0,
                `${file} has expanded results on first load`);
            assert.equal(await page.locator(".page-section-panel > summary > img").count(),
                await page.locator(".page-section-panel").count(), `${file} has a section without an icon`);
            const descriptions = await page.locator(".section-tile-description").allTextContents();
            assert.equal(descriptions.length, await page.locator(".page-section-panel").count());
            assert.ok(descriptions.every(text => text.trim().length > 0), `${file} has an empty description`);
            if (await page.locator("aside.dashboard-sidebar .metric-grid").count()) {
                assert.equal(await page.locator("aside.dashboard-sidebar .metric-grid").first().isVisible(), true,
                    `${file} statistics must stay visible`);
            }
        }
        await page.goto(`${base}/app/index.html`);
        assert.equal(await page.locator(".page-field-panel, .page-section-panel").count(), 0, "Home must remain unchanged");
        assert.equal(await page.locator(".operation-route").first().isVisible(), true);
        await page.goto(`${base}/app/ideas.html`);
        const form = page.locator("#workspaceForm");
        assert.equal(await form.isVisible(), false);
        await page.locator("details.page-section-panel").first().locator(":scope > summary").click();
        await page.locator("details.page-field-panel").first().locator("summary").click();
        assert.equal(await form.isVisible(), true);
        await page.locator("details.page-field-panel").first().locator("summary").click();
        await page.evaluate(() => revealPagePanel(document.getElementById("workspaceEditor")));
        assert.equal(await form.isVisible(), true, "Edit target must reveal its form");
        assert.equal(await page.locator(".table-tools").isVisible(), false, "Edit must not open unrelated filters");
        await page.evaluate(() => {
            const form = document.createElement("form");
            form.id = "dynamicPanelTest";
            form.hidden = true;
            const input = document.createElement("input");
            input.required = true;
            form.append(input);
            document.querySelector("main section").append(form);
        });
        await page.waitForFunction(() => document.getElementById("dynamicPanelTest").closest("details")?.hidden);
        await page.evaluate(() => { document.getElementById("dynamicPanelTest").hidden = false; });
        await page.waitForFunction(() => !document.getElementById("dynamicPanelTest").closest("details").hidden);
        assert.equal(await page.locator("#dynamicPanelTest").isVisible(), false, "Newly permitted panel still starts closed");
        await page.evaluate(() => document.querySelector("#dynamicPanelTest input").dispatchEvent(new Event("invalid")));
        assert.equal(await page.locator("#dynamicPanelTest").isVisible(), true, "Validation must reveal the invalid field");
        await page.evaluate(() => { document.getElementById("dynamicPanelTest").hidden = true; });
        await page.waitForFunction(() => document.getElementById("dynamicPanelTest").closest("details").hidden);
        assert.deepEqual(errors, []);
        for (const width of [1440, 390]) {
            await page.setViewportSize({width, height: 1000});
            await page.goto(`${base}/app/maintenance.html`);
            const layout = await page.evaluate(() => {
                const icon = [...document.querySelectorAll(".section-tile-icon")].find(element => element.checkVisibility());
                return {iconWidth: icon.getBoundingClientRect().width,
                    overflow: document.documentElement.scrollWidth > window.innerWidth};
            });
            assert.ok(layout.iconWidth >= 64, "Section icons must remain large");
            assert.equal(layout.overflow, false, "Tiles must fit the viewport");
            await page.screenshot({path: path.join(process.env.TEMP || "/tmp", `section-large-icons-${width}.png`), fullPage: true});
        }
        console.log(`PASS: ${files.length} pages have icon sections; Home and statistics stay visible; toggle, editor reveal, permissions and validation work.`);
    } finally {
        await browser.close();
    }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
