const assert = require("node:assert/strict");
const path = require("node:path");
const os = require("node:os");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");

async function run() {
    const browser = await chromium.launch({channel: "chrome", headless: true});
    try {
        const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
        const errors = [], writes = [];
        const instrument = {instrument_id: 1, registry_number: 55, instrument_name: "Temperature probe", parameters_taken: "Air temperature",
            category: "Sensing", station_categories: ["Principal Station"], element_ids: ["101", "102"], is_active: true};
        const station = {station_id: 1, station_code: "A", station_name: "Alpha", station_category: "Principal Station", district: "North"};
        const record = {...station, ...instrument, station_instrument_id: 1, status: "Operational", installation_date: null,
            wiring_colors: [], status_effective_date: "2026-07-01"};
        page.on("pageerror", error => errors.push(error.message));
        page.on("dialog", dialog => dialog.accept(dialog.type() === "prompt" ? "Correct element mapping" : undefined));
        await page.addInitScript(() => sessionStorage.setItem("weather_station_access_token", "test"));
        await page.route("**/*", route => {
            const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
            if (pathname.startsWith("/app/")) return route.continue();
            if (request.method() !== "GET") {
                writes.push({path: pathname, body: request.postData() ? request.postDataJSON() : null});
                return route.fulfill({json: {added: 2, message: "Added category assignments"}});
            }
            if (pathname === "/auth/me") return route.fulfill({json: {user_id: 1, username: "test", full_name: "Test Admin", department: "Admin"}});
            if (pathname === "/station-categories") return route.fulfill({json: [{name: "Principal Station"}]});
            if (pathname === "/stations") return route.fulfill({json: [station]});
            if (pathname === "/instruments") return route.fulfill({json: url.searchParams.has("page")
                ? {items: [instrument], total: 1, pages: 1, page: 1, page_size: 25} : [instrument]});
            if (pathname === "/station-instruments") return route.fulfill({json: {items: [record], total: 1, pages: 1,
                page: 1, page_size: 25, summary: {total: 1, stations: 1, operational: 1}}});
            return route.fulfill({json: {items: [], total: 0, unread_count: 0}});
        });
        const base = process.env.TEST_APP_URL || "http://127.0.0.1:8000";
        const reveal = id => page.evaluate(id => revealPagePanel(document.getElementById(id)), id);
        await page.goto(`${base}/app/instruments.html`);
        await page.waitForFunction(() => document.querySelector("#instrumentTable").textContent.includes("Temperature probe"));
        await reveal("instrumentTable");
        assert.ok((await page.locator("#instrumentTable").innerText()).includes("101 | 102"));
        await page.evaluate(() => editInstrument(1));
        assert.equal(await page.locator("#instrumentRegistry").inputValue(), "55");
        assert.equal(await page.locator("#instrumentRegistry").getAttribute("readonly"), "");
        assert.equal(await page.locator("#instrumentElements").inputValue(), "101|102");
        await page.locator("#instrumentElements").fill("101|102|105");
        await page.locator("#instrumentSubmit").click();
        await page.locator(".edit-reason-dialog textarea").fill("Correct element mapping");
        await page.locator(".edit-reason-dialog button[type=submit]").click();
        await page.waitForFunction(() => document.querySelector("#formMessage").textContent.includes("updated"));
        assert.deepEqual(writes.find(write => write.path === "/instruments/1").body.element_ids, ["101", "102", "105"]);
        await page.goto(`${base}/app/station-instruments.html`);
        await page.waitForFunction(() => document.querySelector("#stationInstrumentTable").textContent.includes("Temperature probe"));
        await reveal("stationInstrumentTable");
        assert.ok((await page.locator("#stationInstrumentTable").innerText()).includes("101 | 102"));
        assert.equal((await page.locator('th[data-sort="elements"]').innerText()).toLowerCase(), "element ids");
        await page.evaluate(() => editStationInstrument(1));
        await page.locator("#stationInstrumentStatus").selectOption("Unavailable");
        await page.locator("#statusEffectiveDate").fill("2026-07-11");
        await page.locator("#stationInstrumentSubmit").click();
        await page.locator(".edit-reason-dialog textarea").fill("Instrument unavailable from this date");
        await page.locator(".edit-reason-dialog button[type=submit]").click();
        await page.waitForFunction(() => document.querySelector("#stationInstrumentMessage").textContent.includes("updated"));
        const update = writes.find(write => write.path === "/station-instruments/1").body;
        assert.equal(update.status, "Unavailable");
        assert.equal(update.status_effective_date, "2026-07-11");
        assert.equal(update.installation_date, null);
        await reveal("categoryDefaultsPanel");
        await page.locator("#assignCategoryDefaults").click();
        await page.locator(".edit-reason-dialog textarea").fill("Initial category assignments");
        await page.locator(".edit-reason-dialog button[type=submit]").click();
        await page.waitForFunction(() => document.querySelector("#categoryDefaultsMessage").textContent.includes("2 assignments"));
        for (const width of [1440, 390]) {
            await page.setViewportSize({width, height: 1000});
            await reveal("stationInstrumentForm");
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
            await page.screenshot({path: path.join(os.tmpdir(), `instrument-inventory-${width}.png`), fullPage: true});
        }
        assert.deepEqual(errors, []);
        console.log("Instrument inventory UI: element edits, current status, category defaults and responsive layouts passed.");
    } finally {
        await browser.close();
    }
}
run().catch(error => {console.error(error); process.exitCode = 1;});
