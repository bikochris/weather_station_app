const assert = require("node:assert/strict");
const path = require("node:path");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");

async function run() {
    const browser = await chromium.launch({channel: "chrome", headless: true});
    try {
        const context = await browser.newContext();
        const base = process.env.TEST_APP_URL || "http://127.0.0.1:8000";
        await context.route("**/*", route => {
            const url = new URL(route.request().url());
            if (url.hostname === "tile.openstreetmap.org") return process.env.TEST_MAP_ONLINE ? route.continue() : route.abort();
            if (/\/js\/(stations|sites)\.js$/.test(url.pathname)) {
                return route.fulfill({body: "", contentType: "text/javascript"});
            }
            return url.pathname.startsWith("/app/") ? route.continue() : route.fulfill({json: []});
        });
        const page = await context.newPage();
        const errors = [];
        page.on("pageerror", error => errors.push(error.message));
        for (const kind of ["stations", "sites"]) {
            await page.goto(`${base}/app/${kind}.html`);
            await page.addScriptTag({path: path.join(__dirname, `../frontend/js/${kind}.js`)});
            await page.evaluate(async kind => {
                const record = {station_id: 1, station_code: "123", station_name: "Test <station>",
                    latitude: -1.95, longitude: 30.06, status: "Operational", district: "Gasabo"};
                const site = {...record, site_code: "SITE1", site_name: "Test site", stations: [record]};
                window.mapRequests = [];
                document.getElementById(kind === "stations" ? "stationStatusFilter" : "siteStatusFilter").value = "Operational";
                apiFetch = async url => {
                    window.mapRequests.push(url);
                    const query = new URL(url, location.origin).searchParams;
                    const first = kind === "sites" ? site : record;
                    const other = {...first, station_name: "Another station", site_name: kind === "sites" ? "Another site" : undefined, latitude: -2.1};
                    return {ok: true, json: async () => query.has("page")
                        ? ({items: [first], summary: {}, total: 2, page: 1, page_size: 25}) : [first, other]};
                };
                renderPagination = () => {};
                attachRecordHistoryRows = () => {};
                isITUser = () => false;
                if (kind === "stations") await loadStations();
                else await loadSites();
                revealPagePanel(document.getElementById(kind === "stations" ? "stationTable" : "siteTable"));
            }, kind);
            const rowButton = page.locator("tbody .location-map-button");
            await rowButton.click();
            await page.waitForSelector(".leaflet-interactive");
            assert.equal(await page.locator(".location-map-dialog").isVisible(), true);
            assert.match(await page.locator(".leaflet-popup-content").innerText(), /Test <station>/);
            assert.equal(await page.locator(".leaflet-popup-content station").count(), 0, "Popup must escape record content");
            assert.match(await page.locator(".location-map-count").innerText(), /1 mapped/);
            assert.equal(await page.locator(".location-map-marker img").count(), 0, "Map markers must be compact dots");
            assert.equal(await page.locator(".location-marker-dot").evaluate(element => element.getBoundingClientRect().width), 16);
            assert.ok(await page.locator(".location-results-map img").count(), "Map button icons must remain");
            await page.keyboard.press("Escape");
            assert.equal(await page.locator(".location-map-dialog").isVisible(), false);
            await page.locator(".location-results-map").click();
            assert.match(await page.locator("#locationMapTitle").innerText(), /current results page/);
            await page.keyboard.press("Escape");
            await page.locator(".location-filtered-map").click();
            await page.waitForFunction(() => document.querySelector(".location-map-count").textContent.startsWith("2 mapped"));
            assert.match(await page.locator("#locationMapTitle").innerText(), /all filtered results/);
            const request = await page.evaluate(() => window.mapRequests.at(-1));
            const query = new URL(request, base).searchParams;
            assert.equal(query.has("page"), false, "Filtered map must not be restricted to the table page");
            assert.equal(query.get("status"), "Operational", "Map must preserve active filters");
            await page.keyboard.press("Escape");
            for (const width of [1440, 390]) {
                await page.setViewportSize({width, height: 900});
                await page.evaluate(() => locationMap.show([
                    {station_name: "Missing", latitude: "", longitude: null},
                    {station_name: "Invalid", latitude: 91, longitude: 30},
                    {station_name: "Mapped", latitude: -1.95, longitude: 30.06}
                ], "Locations"));
                await page.waitForTimeout(200);
                if (process.env.TEST_MAP_ONLINE) await page.waitForFunction(() =>
                    [...document.querySelectorAll(".leaflet-tile")].some(tile => tile.complete && tile.naturalWidth > 0), {timeout: 20000});
                assert.match(await page.locator(".location-map-count").innerText(), /1 mapped \/ 3 selected/);
                const fits = await page.locator(".location-map-dialog").evaluate(element => {
                    const rect = element.getBoundingClientRect();
                    return rect.left >= 0 && rect.right <= innerWidth && element.scrollWidth <= element.clientWidth;
                });
                assert.ok(fits, "Map dialog must fit the viewport");
                await page.screenshot({path: path.join(process.env.TEMP || "/tmp", `location-map-${kind}-${width}.png`)});
                await page.keyboard.press("Escape");
            }
        }
        assert.deepEqual(errors, []);
        console.log("PASS: Sites/Stations map buttons, safe popups, current results, invalid coordinates, desktop/mobile and Escape close.");
    } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
