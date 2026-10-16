const assert = require("node:assert/strict");
const path = require("node:path");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");

async function run() {
    const browser = await chromium.launch({channel: "chrome", headless: true});
    try {
        const page = await browser.newPage();
        await page.addInitScript(() => sessionStorage.setItem("weather_station_access_token", "test"));
        const station = {station_id: 1, station_code: "TEST1", station_name: "Performance Test AWS", district: "Gasabo", station_category: "Automatic Weather station"};
        const records = [];
        const writes = [];
        const errors = [];
        page.on("pageerror", error => errors.push(error.message));
        await page.route("**/*", route => {
            const request = route.request(); const url = new URL(request.url());
            const pathname = url.pathname;
            if (pathname.startsWith("/app/")) return route.continue();
            if (pathname === "/auth/me") return route.fulfill({json: {user_id: 1, full_name: "Test Admin", username: "tester", department: "Admin", must_change_password: false}});
            if (pathname === "/stations") return route.fulfill({json: [station, {...station, station_id: 2, station_code: "TEST2", station_name: "Second station"}]});
            if (pathname.startsWith("/notifications")) return route.fulfill({json: {items: [], unread_count: 0, pages: 1}});
            if (pathname.startsWith("/record-history")) return route.fulfill({json: {items: [], total: 0, page: 1, pages: 1, page_size: 25}});
            if (pathname === "/station-performance" && request.method() === "GET") {
                if (!url.searchParams.has("page")) return route.fulfill({json: records});
                return route.fulfill({json: {items: records, total: records.length, pages: 1, page: 1, page_size: 25,
                    summary: {total: records.length, station_count: records.length, average: records.length ? records[0].score : null, months_count: records.length,
                        station_labels: {1: "TEST1 - Performance Test AWS", 2: "TEST2 - Second station"},
                        reports: records.length ? [{performance_id: 1, filename: "report.pdf", months: [records[0].report_month.slice(0, 7)], stations: {1: "TEST1 - Performance Test AWS", 2: "TEST2 - Second station"}}] : [],
                        station_period_averages: {months: records.length ? {"2026-10": {1: records[0].score, 2: 75}} : {}, quarters: records.length ? {"2026/2027 Q2": {1: records[0].score, 2: 75}} : {}, years: records.length ? {"2026/2027": {1: records[0].score, 2: 75}} : {}},
                        averages: {stations: records.length ? {1: records[0].score} : {},
                            months: records.length ? {"2026-10": records[0].score} : {}, quarters: records.length ? {"2026/2027 Q2": records[0].score} : {}, years: records.length ? {"2026/2027": records[0].score} : {}}}}});
            }
            if (pathname === "/station-performance/bulk-month") {
                const data = JSON.parse(request.postData());
                writes.push({method: request.method(), bulk: data, reason: request.headers()["x-edit-reason"]});
                records[0].report_month = `${data.report_month}-01`;
                return route.fulfill({json: {updated: 1, unchanged: 0}});
            }
            if (pathname.startsWith("/station-performance") && ["POST", "PUT"].includes(request.method())) {
                writes.push({method: request.method(), query: Object.fromEntries(url.searchParams), bytes: request.postDataBuffer()?.length || 0});
                const entries = url.searchParams.has("entries") ? JSON.parse(url.searchParams.get("entries")).stations : null;
                records[0] = {...station, performance_id: 1, report_month: "2026-10-01", score: entries ? entries[0].score : Number(url.searchParams.get("score")), notes: "Assessment", original_filename: "report.pdf", created_by_username: "tester", updated_at: "2026-10-14", can_edit: true};
                return route.fulfill({json: {performance_id: 1}});
            }
            if (pathname.endsWith("/report")) return route.fulfill({body: "report", headers: {"Content-Disposition": 'attachment; filename="report.pdf"', "Content-Type": "application/pdf"}});
            return route.fulfill({json: []});
        });
        await page.goto(`${process.env.TEST_APP_URL || "http://127.0.0.1:8001"}/app/station-performance.html`);
        await page.waitForFunction(() => document.querySelector("#performanceStation").options.length === 3);
        await page.evaluate(() => revealPagePanel(document.getElementById("performanceEditor")));
        await page.evaluate(() => {
            const select = document.getElementById("performanceStation"); select.value = "1|2";
            select.dispatchEvent(new Event("change", {bubbles: true}));
        });
        await page.fill("#performanceMonth", "2026-10");
        await page.fill("#stationScore1", "82.5");
        await page.fill("#stationScore2", "75");
        for (const width of [1440, 390]) {
            await page.setViewportSize({width, height: 1000});
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
            await page.screenshot({path: path.join(process.env.TEMP || "/tmp", `station-performance-batch-${width}.png`), fullPage: true});
        }
        await page.setInputFiles("#performanceReport", {name: "report.pdf", mimeType: "application/pdf", buffer: Buffer.from("report")});
        await page.click("#savePerformance");
        await page.waitForFunction(() => document.querySelector("#performanceAverage").textContent === "82.5%");
        assert.equal(writes[0].method, "POST");
        assert.deepEqual(JSON.parse(writes[0].query.entries).stations, [{station_id: 1, score: 82.5}, {station_id: 2, score: 75}]);
        assert.equal(writes[0].bytes, 6);
        assert.equal(await page.locator("#performanceStationChart .horizontal-bar-fill").evaluate(element => element.style.width), "82.5%");
        await page.evaluate(() => {
            revealPagePanel(document.getElementById("performanceRows"));
            requestEditReason = async () => "Correct assessment";
        });
        await page.getByRole("button", {name: "Edit", exact: true}).click();
        assert.equal(await page.locator("#performanceReport").getAttribute("required"), null);
        await page.fill("#performanceScore", "90");
        await page.click("#savePerformance");
        await page.waitForFunction(() => document.querySelector("#performanceAverage").textContent === "90%");
        assert.equal(writes[1].method, "PUT");
        assert.equal(writes[1].bytes, 0);
        for (const id of ["performanceMonthChart", "performanceQuarterChart", "performanceYearChart"]) {
            assert.equal(await page.locator(`#${id} .horizontal-bar-row`).count(), 2, "Period graphs must show every station, not just the current table page");
            assert.match(await page.locator(`#${id}`).innerText(), /TEST1.*Performance Test AWS/s);
            assert.match(await page.locator(`#${id}`).innerText(), /TEST2.*Second station/s);
            assert.match(await page.locator(`#${id} .performance-period-heading strong`).innerText(), /Overall: 90%/);
        }
        assert.equal(await page.locator("#performanceMonthMatrix thead th").count(), 14);
        const headings = await page.locator("#performanceMonthMatrix thead th").allTextContents();
        assert.equal(headings[1], "Jul");
        assert.equal(headings[12], "Jun");
        assert.equal(await page.locator("#performanceMonthMatrix tbody tr").count(), 2);
        assert.equal(await page.locator("#performanceMonthMatrix tbody tr").first().locator("td").first().innerText(), "-", "Missing months must not become zero");
        assert.match(await page.locator("#performanceMonthMatrix tfoot").innerText(), /Overall \(%\)/i);
        assert.equal(await page.locator("#performanceTable thead th").count(), await page.locator("#performanceRows tr").first().locator("td").count());
        await page.evaluate(() => revealPagePanel(document.getElementById("performanceBulkPanel")));
        await page.click("#selectFilteredPerformance");
        await page.waitForFunction(() => document.querySelector("#performanceSelectedCount").textContent === "1 records selected");
        await page.fill("#performanceBulkMonth", "2026-07");
        await page.fill("#performanceBulkReason", "October was entered instead of July");
        page.once("dialog", dialog => dialog.accept());
        await page.click("#applyPerformanceBulk");
        await page.waitForFunction(() => document.querySelector("#performanceBulkMessage").textContent.includes("1 records updated"));
        assert.deepEqual(writes[2].bulk, {performance_ids: [1], report_month: "2026-07"});
        assert.equal(decodeURIComponent(writes[2].reason), "October was entered instead of July");
        assert.equal(records[0].score, 90);
        assert.equal(await page.locator("#performanceReports a").count(), 1, "Shared report should appear once");
        await page.evaluate(() => revealPagePanel(document.getElementById("performanceReports")));
        const downloadEvent = page.waitForEvent("download");
        await page.locator("#performanceReports a").click();
        assert.equal((await downloadEvent).suggestedFilename(), "report.pdf");
        for (const width of [1440, 390]) {
            await page.setViewportSize({width, height: 1000});
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
            await page.screenshot({path: path.join(process.env.TEMP || "/tmp", `station-performance-${width}.png`), fullPage: true});
        }
        await page.evaluate(() => renderPerformanceMonthMatrix({
            station_labels: {1: "Alpha", 2: "Beta", 3: "Gamma"},
            averages: {years: {"2026/2027": 55, "2027/2028": 30}, months: {"2026-07": 51.67, "2026-08": 80, "2027-07": 30}},
            station_period_averages: {months: {"2026-07": {1: 60, 2: 95, 3: 0}, "2026-08": {1: 80}, "2027-07": {2: 30}},
                years: {"2026/2027": {1: 70, 2: 95, 3: 0}, "2027/2028": {2: 30}}}
        }));
        assert.equal(await page.locator("#performanceMonthMatrix table").count(), 2, "New fiscal years must appear automatically");
        const matrix = page.locator("#performanceMonthMatrix table").first();
        assert.deepEqual(await matrix.locator("tbody th").allTextContents(), ["Beta", "Alpha", "Gamma"], "Default ranking must be highest score first");
        assert.equal(await matrix.locator("tbody tr").nth(1).locator("td").nth(1).innerText(), "80%", "New months must fill their column");
        await matrix.locator("thead th").last().click();
        await page.waitForFunction(() => document.querySelector("#performanceMonthMatrix tbody th").textContent === "Gamma");
        await matrix.locator("thead th").last().click();
        await page.waitForFunction(() => document.querySelector("#performanceMonthMatrix tbody th").textContent === "Beta");
        await matrix.locator("thead th").nth(2).click();
        await page.waitForFunction(() => document.querySelector("#performanceMonthMatrix tbody th").textContent === "Alpha");
        assert.equal(await matrix.locator("tfoot th").innerText(), "OVERALL (%)", "Overall percentages must not move into the ranking");
        assert.deepEqual(errors, []);
        console.log("PASS: multi-station scores, bulk month correction, shared report download, charts, table/history, desktop/mobile.");
    } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
