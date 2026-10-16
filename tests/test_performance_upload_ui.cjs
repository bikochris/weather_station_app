const assert = require("node:assert/strict");
const http = require("node:http");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");
const base = process.env.TEST_APP_URL || "http://127.0.0.1:8001";

async function staticServer() {
    try { await fetch(`${base}/app/station-performance-upload.html`); return null; } catch {}
    const root = path.resolve(__dirname, "../frontend");
    const server = http.createServer(async (request, response) => {
        const url = new URL(request.url, base);
        const file = path.resolve(root, decodeURIComponent(url.pathname.replace(/^\/app\//, "")));
        if (!file.startsWith(root + path.sep)) { response.writeHead(403).end(); return; }
        try {
            const content = await fs.readFile(file);
            response.writeHead(200, {"Content-Type": ({".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml"})[path.extname(file)] || "application/octet-stream"}).end(content);
        } catch { response.writeHead(404).end(); }
    });
    await new Promise((resolve, reject) => server.once("error", reject).listen(Number(new URL(base).port), "127.0.0.1", resolve));
    return server;
}

async function run() {
    const server = await staticServer(); let browser;
    try {
        browser = await chromium.launch({channel: "chrome", headless: true});
        const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
        const errors = [], writes = [], filters = [], downloads = [], downloadQueries = [];
        let role = "Admin", uploadError = false, conflict = false, uploaded = false, configGets = 0, networkFailure = false;
        const stations = [{station_id: 1, station_code: "TEST1", station_name: "Central station", district: "North"}, {station_id: 2, station_code: "TEST2", station_name: "Riverside station", district: "South"}];
        let config = {version: 4, catalog_schema_version: 6, parameters: [
            {element_id: "TEMP", name: "Temperature", cadence: "daily", hours: [], enabled: true, station_codes: [], zero_parent: null, schedule_mode: "times", times: ["08:00"]},
            {element_id: "CLOUD", name: "Cloud amount", cadence: "hourly", hours: [0, 6, 12, 18], enabled: true, station_codes: ["TEST1"], zero_parent: "TEMP", schedule_mode: "interval", interval_minutes: 10}
        ], stations: stations.map(s => ({station_code: s.station_code, station_name: s.station_name, enabled: true, daily_excluded: [], hourly_overrides: s.station_code === "TEST1" ? {CLOUD: {hours: [6, 12], reported_excluded_hours: [0], count_in_summary: true}} : {}}))};
        page.on("pageerror", error => errors.push(error.message));
        await page.addInitScript(() => sessionStorage.setItem("weather_station_access_token", "test"));
        await page.route("**/*", async route => {
            const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
            if (pathname.startsWith("/app/")) return route.continue();
            if (pathname === "/auth/me") return route.fulfill({json: {user_id: 1, full_name: "Test user", department: role, must_change_password: false}});
            if (pathname === "/stations") return route.fulfill({json: stations});
            if (pathname === "/performance-upload/filter-options") return route.fulfill({json:{report_filter_schema_version:1,station_categories:["Principal Station","Rainfall Station"],station_statuses:["Operational","Under maintenance","Suspended","Closed"],elements:[{element_id:"TEMP",element_name:"Temperature"},{element_id:"CLOUD",element_name:"Cloud amount"}]}});
            if (pathname === "/performance-upload/config") {
                if (request.method() === "GET") { configGets++; return route.fulfill({json: config}); }
                writes.push({body: request.postDataJSON(), reason: decodeURIComponent(request.headers()["x-edit-reason"])});
                if (conflict) return route.fulfill({status: 409, json: {detail: "Configuration version has changed."}});
                config = {...request.postDataJSON(), version: config.version + 1}; return route.fulfill({json: config});
            }
            if (pathname === "/performance-upload/results") {
                filters.push(Object.fromEntries(url.searchParams));
                if (uploaded && url.searchParams.get("element_id") === "2") {
                    const empty = url.searchParams.get("month_from") === "2027-01";
                    return route.fulfill({json: {
                        items: empty ? [] : [{...stations[0], report_month:"2026-09", daily_expected:30,daily_available:12,daily_missing:18,daily_percent:40,hourly_expected:0,hourly_available:0,hourly_missing:0,hourly_percent:0,overall_percent:40}],
                        element_summary: [], element_items: [], runs: [],
                        summary:{average:empty ? null : 40,stations:empty ? 0 : 1,months:empty ? 0 : 1,records:empty ? 0 : 1}
                    }});
                }
                const items = uploaded ? stations.map((s, index) => ({...s, run_id: 7, report_month: index ? "2026-10" : "2026-07", daily_expected: 30, daily_available: index ? 30 : 15, daily_missing: index ? 0 : 15, hourly_expected: 120, hourly_available: index ? 60 : 120, hourly_missing: index ? 60 : 0, daily_percent: index ? 100 : 50, hourly_percent: index ? 50 : 100, overall_percent: index ? 60 : 90, created_at: "2026-10-14T12:00:00"})) : [];
                return route.fulfill({json: {items,
                    element_summary: uploaded ? [{element_id:"TEMP", element_name:"Catalog temperature", expected:100, available:75, missing:25, score:75}] : [],
                    element_items: uploaded ? [{station_name:"Central station",report_month:"2026-07",element_id:"TEMP",element_name:"Catalog temperature",expected:100,available:75,missing:25,score:75}] : [],
                    summary: {average: uploaded ? 75 : null, stations: uploaded ? 2 : 0, months: uploaded ? 2 : 0, records: items.length}, runs: uploaded ? [
                    {run_id: 6, filename: "older.csv", created_by_username: "tester", created_at: "2026-10-13", row_count: 4, invalid_rows: 0, duplicate_rows: 0, periods: ["2026-07"], warnings: [], conflicts: [], published: 0},
                    {run_id: 7, filename: "observations.csv", created_by_username: "tester", created_at: "2026-10-14", row_count: 10, invalid_rows: 1, duplicate_rows: 2, periods: ["2026-07", "2026-10"], warnings: ["One invalid date"], conflicts: [{message: "Existing observation"}], published: 0}
                ] : []}});
            }
            if (pathname === "/performance-upload/analyze") {
                if (networkFailure) { await new Promise(resolve => setTimeout(resolve, 500)); return route.abort("connectionreset"); }
                writes.push({filename: url.searchParams.get("filename"), query: Object.fromEntries(url.searchParams), body: request.postDataBuffer(), contentType: request.headers()["content-type"]});
                if (uploadError) return route.fulfill({status: 422, json: {detail: "Row 2: malformed date_time '2026-99-40'. Use a valid ISO date."}});
                uploaded = true; return route.fulfill({json: {run_id: 7, results_count: 2, invalid_rows: 1, duplicate_rows: 2, warnings: ["One invalid date"], periods: ["2026-07", "2026-10"]}});
            }
            if (/\/performance-upload\/runs\/\d+\/report/.test(pathname)) {
                downloadQueries.push(Object.fromEntries(url.searchParams));
                const format = url.searchParams.get("format"); downloads.push(format);
                return route.fulfill({body: "report", headers: {"Content-Disposition": `attachment; filename="result.${format === "details" ? "zip" : format}"`, "Content-Type": "application/octet-stream"}});
            }
            return route.fulfill({json: {items: [], unread_count: 0, pages: 1}});
        });
        await page.goto(`${base}/app/station-performance-upload.html`);
        await page.waitForSelector("#configVersion:text-is('Version 4')", {state: "attached"});
        assert.equal(await page.locator("#uploadAverage").innerText(), "-");
        assert.equal(await page.locator("#uploadInput").evaluate(n => n.closest("details.page-section-panel").open), false);
        assert.equal(await page.locator("#uploadReports").evaluate(n => n.closest("details.page-section-panel").open), false);
        assert.equal(await page.locator("#uploadStationChart").isVisible(), true);
        const reveal = id => page.evaluate(id => revealPagePanel(document.getElementById(id)), id);
        await reveal("uploadInput");
        let download = page.waitForEvent("download"); await page.click("#csvTemplate");
        const template = await download; assert.equal(template.suggestedFilename(), "observation-template.csv");
        assert.equal(await fs.readFile(await template.path(), "utf8"), "station_id,element_id,element_name,date_time,value\r\n");
        await page.setInputFiles("#observationFile", {name: "bad.txt", mimeType: "text/plain", buffer: Buffer.from("bad")});
        await page.click("#analyzeFile"); assert.match(await page.locator("#uploadMessage").innerText(), /CSV, XLSX, or XLSM/);
        await page.setInputFiles("#observationFile", {name: "large.csv", mimeType: "text/csv", buffer: Buffer.from("test")});
        await page.evaluate(() => Object.defineProperty(document.getElementById("observationFile").files[0], "size", {value: 2 * 1024 ** 3 + 1}));
        await page.click("#analyzeFile"); assert.match(await page.locator("#uploadMessage").innerText(), /2 GB/);
        const csv = Buffer.from("station_id,element_id,element_name,date_time,value\r\n1,TEMP,Temperature,2026-10-01,20\r\n");
        networkFailure = true;
        await page.setInputFiles("#observationFile", {name: "interrupted.csv", mimeType: "text/csv", buffer: csv});
        await page.click("#analyzeFile");
        assert.equal(await page.evaluate(() => { const event = new Event("beforeunload", {cancelable:true}); window.dispatchEvent(event); return event.defaultPrevented; }), true);
        await page.waitForFunction(() => document.getElementById("uploadMessage").textContent.includes("connection was interrupted"));
        assert.equal(await page.locator("#analyzeFile").isEnabled(), true);
        assert.equal(await page.evaluate(() => { const event = new Event("beforeunload", {cancelable:true}); window.dispatchEvent(event); return event.defaultPrevented; }), false);
        networkFailure = false;
        await page.setInputFiles("#observationFile", {name: "observations.csv", mimeType: "text/csv", buffer: csv});
        await page.click("#analyzeFile"); await page.waitForFunction(() => document.getElementById("uploadAverage").textContent === "75%");
        assert.equal(writes[0].body.equals(csv), true); assert.equal(writes[0].query.publish, undefined); assert.equal(writes[0].contentType, "application/octet-stream");
        assert.match(await page.locator("#uploadMessage").innerText(), /1 invalid rows; 2 duplicates/);
        assert.equal(await page.locator("#uploadWarnings").innerText(), "One invalid date");
        await reveal("elementScores");
        assert.match(await page.locator("#elementScoreRows").innerText(), /Catalog temperature.*75%/s);
        assert.match(await page.locator("#elementDetailRows").innerText(), /Central station.*Catalog temperature/s);
        assert.equal(await page.locator("#uploadStationChart .horizontal-bar-row").count(), 2);
        await reveal("chartPeriod");
        await page.selectOption("#chartPeriod", "quarter"); assert.match(await page.locator("#uploadPeriodChart").innerText(), /FY 2026\/2027 Q1.*90%.*Q2.*60%/s);
        await page.selectOption("#chartPeriod", "year"); assert.match(await page.locator("#uploadPeriodChart").innerText(), /75%/);
        await reveal("uploadRegister");
        await page.locator("#uploadResults th").nth(12).click(); await page.waitForFunction(() => document.querySelector("#uploadResultRows td").textContent === "TEST2");
        await page.locator("#uploadResults th").nth(12).press("Enter"); await page.waitForFunction(() => document.querySelector("#uploadResultRows td").textContent === "TEST1");
        assert.equal(await page.locator("#uploadResults").getAttribute("data-server-sorted"), null);
        await reveal("resultFilters");
        await page.evaluate(() => {
            const select = document.getElementById("uploadElementFilter");
            select.add(new Option("2 - Daily Maximum Temperature", "2"));
            select.value = "2";
            document.getElementById("uploadFrom").value = "2026-09";
            document.getElementById("uploadTo").value = "2026-09";
            select.dispatchEvent(new Event("change", {bubbles:true}));
        });
        await page.waitForFunction(() => document.getElementById("uploadAverage").textContent === "40%");
        assert.equal(filters.at(-1).element_id, "2");
        assert.match(await page.locator("#resultScoreScope").innerText(), /2 - Daily Maximum Temperature/);
        assert.equal(await page.locator("#uploadAverageLabel").textContent(), "Selected element score");
        assert.match(await page.locator("#uploadStationChart").innerText(), /40%/);
        assert.match(await page.locator("#uploadPeriodChart").innerText(), /40%/);
        assert.equal(await page.locator("#uploadResultRows tr td").nth(12).innerText(), "40%");
        await page.evaluate(() => {
            document.getElementById("uploadFrom").value = "2027-01";
            document.getElementById("uploadTo").value = "2027-01";
            document.getElementById("uploadFrom").dispatchEvent(new Event("change", {bubbles:true}));
        });
        await page.waitForFunction(() => document.getElementById("uploadResultRows").textContent.includes("No saved results for 2"));
        assert.equal(await page.locator("#uploadAverage").innerText(), "-");
        await page.click("#resetFilters");
        await page.waitForFunction(() => document.getElementById("uploadAverage").textContent === "75%");
        await page.evaluate(() => { document.getElementById("uploadStationFilter").value = "TEST1|TEST2"; document.getElementById("uploadDistrictFilter").value = "North|South"; document.getElementById("uploadCategoryFilter").value = "Principal Station|Rainfall Station"; document.getElementById("uploadStatusFilter").value = "Operational|Suspended"; document.getElementById("uploadElementFilter").value = "TEMP|CLOUD"; });
        await page.fill("#uploadFrom", "2026-07"); await page.fill("#uploadTo", "2026-10");
        await page.locator("#resultFilters button[type=submit]").click(); await page.waitForTimeout(100);
        assert.deepEqual(filters.at(-1), {station_code: "TEST1|TEST2", district: "North|South", station_category:"Principal Station|Rainfall Station",station_status:"Operational|Suspended",element_id:"TEMP|CLOUD",month_from: "2026-07", month_to: "2026-10"});
        const count = filters.length; await page.fill("#uploadFrom", "2027-01"); await page.locator("#resultFilters button[type=submit]").click();
        assert.match(await page.locator("#resultsMessage").innerText(), /on or before/); assert.equal(filters.length, count);
        await page.click("#resetFilters"); await page.waitForTimeout(100); assert.deepEqual(filters.at(-1), {});
        await reveal("uploadReports"); assert.match(await page.locator("#uploadRunRows tr").first().innerText(), /^observations.csv/);
        assert.match(await page.locator("#uploadRunRows tr").first().innerText(), /Conflict: Existing observation/);
        for (const [name, extension] of [["PDF", "pdf"], ["CSV", "csv"], ["DOCX", "docx"], ["Details ZIP", "zip"]]) {
            download = page.waitForEvent("download"); await page.locator("#uploadRunRows tr").first().getByRole("button", {name, exact: true}).click(); assert.equal((await download).suggestedFilename(), `result.${extension}`);
        }
        assert.deepEqual(downloads, ["pdf", "csv", "docx", "details"]);
        await reveal("resultFilters");
        await page.evaluate(() => { document.getElementById("uploadCategoryFilter").value = "Principal Station|Rainfall Station"; document.getElementById("uploadElementFilter").value = "TEMP"; });
        await page.locator("#resultFilters button[type=submit]").click(); await page.waitForTimeout(100);
        await reveal("uploadReports");
        download = page.waitForEvent("download"); await page.locator("#uploadRunRows tr").first().getByRole("button", {name:"CSV",exact:true}).click(); await download;
        assert.deepEqual(downloadQueries.at(-1), {station_category:"Principal Station|Rainfall Station",element_id:"TEMP",format:"csv"});
        await page.click("#resetFilters"); await page.waitForTimeout(100);
        await reveal("uploadInput");
        config.observation_upload_max_bytes = 2 * 1024 ** 3;
        await page.setInputFiles("#observationFile", {name:"over-one-gb.csv",mimeType:"text/csv",buffer:csv});
        await page.evaluate(() => Object.defineProperty(document.getElementById("observationFile").files[0], "size", {value: 1100286051}));
        await page.click("#analyzeFile");
        await page.waitForFunction(() => document.getElementById("uploadMessage").textContent.includes("Run 7:"));
        assert.equal(writes.at(-1).filename, "over-one-gb.csv");
        await reveal("uploadConfiguration");
        await page.evaluate(() => { const select = document.querySelector("#reportScopeEditor select"); select.value = "Operational|Closed"; select.dispatchEvent(new Event("change",{bubbles:true})); });
        await page.locator("#parameterEditors").getByLabel("Interval (minutes)", {exact: true}).fill("5");
        await page.locator('#parameterEditors input[type="time"]').fill("08:30");
        await page.locator("#parameterEditors").getByLabel("Parameter name", {exact: true}).first().fill("Air temperature");
        await page.selectOption("#configStation", "TEST1");
        await page.locator("#stationEditor").getByLabel("Station enabled", {exact: true}).uncheck();
        await page.evaluate(() => { const s = [...document.querySelectorAll("#stationEditor select")].find(n => n.closest(".field").textContent.includes("Daily exclusions")); s.value = "TEMP"; s.dispatchEvent(new Event("change", {bubbles: true})); });
        const elementCode = page.locator("#parameterEditors").getByLabel("Element code", {exact: true}).first();
        await elementCode.fill("TA"); await elementCode.press("Tab");
        assert.equal(await page.locator("#parameterEditors").getByLabel("Zero / cloud parent", {exact: true}).first().inputValue(), "TA");
        await page.locator("#parameterEditors").getByLabel("Parameter enabled", {exact: true}).nth(1).uncheck();
        await page.locator("#parameterEditors .upload-parameter").nth(1).locator(".upload-hours summary").click();
        await page.locator("#parameterEditors .upload-parameter").nth(1).getByLabel("Default hours: 03:00", {exact: true}).check();
        await page.getByRole("button", {name: "Add parameter", exact: true}).click();
        const extra = page.locator("#parameterEditors .upload-parameter").last();
        await extra.getByLabel("Cadence", {exact: true}).selectOption("hourly");
        await page.locator("#parameterEditors .upload-parameter").last().getByRole("button", {name: "Remove parameter", exact: true}).click();
        await page.locator("#stationEditor").getByLabel("Count in summary", {exact: true}).uncheck();
        await page.locator("#stationEditor .upload-hours summary").first().click();
        await page.locator("#stationEditor").getByLabel("Expected hours: 18:00", {exact: true}).check();
        await page.selectOption("#configStation", "TEST2"); await page.getByRole("button", {name: "Add hourly override", exact: true}).click();
        await page.selectOption("#configStation", "TEST1"); assert.equal(await page.locator("#stationEditor").getByLabel("Station enabled", {exact: true}).isChecked(), false);
        await page.fill("#configReason", "Update station coverage"); await page.click("#saveConfig"); await page.waitForFunction(() => document.getElementById("configVersion").textContent === "Version 5");
        const save = writes.find(w => w.reason); assert.equal(save.body.version, 4); assert.equal(save.reason, "Update station coverage"); assert.equal(save.body.parameters[0].name, "Air temperature");
        assert.deepEqual(save.body.station_statuses, ["Operational","Closed"]);
        assert.deepEqual(save.body.stations[0].daily_excluded, ["TA"]); assert.deepEqual(save.body.stations[0].hourly_overrides.CLOUD.hours, [6, 12, 18]); assert.equal(save.body.stations[0].hourly_overrides.CLOUD.count_in_summary, false); assert.ok(save.body.stations[1].hourly_overrides.CLOUD);
        assert.equal(save.body.parameters[1].zero_parent, "TA"); assert.equal(save.body.parameters[1].enabled, false); assert.deepEqual(save.body.parameters[1].hours, [0, 3, 6, 12, 18]); assert.equal(save.body.parameters.length, 2);
        assert.equal(save.body.parameters[1].interval_minutes, 5);
        assert.deepEqual(save.body.parameters[0].times, ["08:30"]);
        conflict = true; await page.fill("#configReason", "Concurrent change"); await page.locator("#parameterEditors").getByLabel("Parameter name", {exact: true}).first().fill("Draft temperature");
        await page.click("#saveConfig"); await page.waitForFunction(() => document.getElementById("configMessage").textContent.includes("latest version"));
        assert.equal(await page.locator("#parameterEditors").getByLabel("Parameter name", {exact: true}).first().inputValue(), "Draft temperature");
        page.once("dialog", d => d.accept()); await page.click("#reloadConfig"); await page.waitForFunction(() => document.getElementById("configDraft").textContent === "");
        uploadError = true; await reveal("uploadInput"); await page.setInputFiles("#observationFile", {name: "bad-date.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("mock xlsx")});
        await page.click("#analyzeFile"); await page.waitForFunction(() => document.getElementById("uploadMessage").textContent.includes("malformed date_time"));
        assert.equal(await page.locator("#analyzeFile").isEnabled(), true);
        assert.equal(writes.at(-1).filename, "bad-date.xlsx"); assert.equal(writes.at(-1).body.toString(), "mock xlsx");
        await page.selectOption("#configStation", "TEST1");
        for (const width of [1440, 768, 390, 320]) {
            await page.setViewportSize({width, height: 1000});
            await page.screenshot({path: path.join(os.tmpdir(), `performance-upload-${width}.png`), fullPage: true});
            if ([1440, 390].includes(width)) await page.locator("section.data-graphs").screenshot({path: path.join(os.tmpdir(), `performance-upload-charts-${width}.png`)});
            const overflow = await page.evaluate(() => [...document.querySelectorAll("body *")].filter(n => { const r = n.getBoundingClientRect(); return r.width && r.right > innerWidth + 1 && !n.closest(".table-wrap, .multi-filter-menu, nav"); }).map(n => `${n.tagName}.${n.className}#${n.id}: ${Math.round(n.getBoundingClientRect().right)}`).slice(0, 15));
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `Overflow at ${width}px: ${overflow.join(", ")}`);
        }
        config.catalog_managed = true; config.catalog_errors = ["Correct pipe-separated names"];
        config.parameters.forEach(parameter => {parameter.eligible_station_codes = ["TEST1"]; parameter.station_codes = ["TEST1"];});
        await page.reload(); await page.waitForFunction(() => document.getElementById("configMessage").textContent.includes("pipe-separated"));
        await reveal("uploadConfiguration");
        assert.equal(await page.locator("#addParameter").isVisible(), false);
        assert.equal(await page.locator("#parameterEditors").getByLabel("Element code", {exact:true}).first().getAttribute("readonly"), "");
        const eligible = await page.locator("#parameterEditors .multi-filter").evaluateAll(menus => menus.map(menu => [...menu.previousElementSibling.options].map(option => option.value)));
        assert.ok(eligible.every(values => !values.includes("TEST2")));
        assert.ok(await page.locator('#parameterEditors .multi-filter input[type="checkbox"]:enabled').count() > 0);
        const stationMenu = page.locator('#parameterEditors .multi-filter').first();
        await stationMenu.locator('summary').click();
        await stationMenu.locator('input[type="checkbox"]').first().uncheck();
        conflict = false; await page.fill("#configReason", "Exclude station from element");
        await page.click("#saveConfig"); await page.waitForFunction(() => document.getElementById("configVersion").textContent === "Version 6");
        const selectionSave = writes.filter(write => write.reason).at(-1);
        assert.deepEqual(selectionSave.body.parameters[0].station_codes, []);
        assert.deepEqual(selectionSave.body.parameters[0].excluded_station_codes, ["TEST1"]);
        config.parameters.push({element_id: "116", name: "Element 116", cadence: "hourly", schedule_mode: "none", hours: [], times: [], enabled: false, station_codes: [], eligible_station_codes: [], reserved: true, is_cloud_parameter: true});
        config.parameters[1].station_codes = [];
        config.parameters[1].inactive_station_codes = ["TEST1"];
        config.parameters[1].station_statuses = {TEST1: "Under Maintenance"};
        await page.reload(); await page.waitForSelector("#parameterEditors .upload-parameter", {state:"attached"}); await reveal("uploadConfiguration");
        const placeholder = page.locator("#parameterEditors .upload-parameter").last();
        assert.equal(await placeholder.locator(".upload-hours").count(), 0);
        assert.equal(await placeholder.getByLabel("Parameter enabled", {exact:true}).isDisabled(), true);
        assert.equal(await page.locator("#parameterEditors .upload-parameter").first().getByLabel("Zero / cloud parent", {exact:true}).count(), 0);
        const unavailable = page.locator("#parameterEditors .upload-parameter").nth(1).locator('.multi-filter input[type="checkbox"]').first();
        assert.equal(await unavailable.isChecked(), false);
        assert.equal(await unavailable.isDisabled(), true);
        config.parameters[1].station_codes = ["TEST1"];
        config.parameters[1].inactive_station_codes = [];
        await page.reload(); await page.waitForSelector("#parameterEditors .upload-parameter", {state:"attached"}); await reveal("uploadConfiguration");
        assert.equal(await page.locator("#parameterEditors .upload-parameter").nth(1).locator('.multi-filter input[type="checkbox"]').first().isChecked(), true);
        role = "Observation Officer"; const gets = configGets; await page.reload(); await page.waitForSelector("#uploadStationChart .horizontal-bar-row");
        assert.equal(await page.locator("#uploadConfiguration").isVisible(), false); assert.equal(configGets, gets);
        role = "Admin"; delete config.catalog_schema_version;
        await page.reload(); await page.waitForFunction(() => document.getElementById("configMessage").textContent.includes("backend is outdated"));
        assert.equal(await page.locator("#parameterEditors .upload-parameter").count(), 0);
        const beforeUpload = writes.length;
        await reveal("uploadInput"); await page.setInputFiles("#observationFile", {name:"data.csv",mimeType:"text/csv",buffer:csv});
        await page.click("#analyzeFile"); await page.waitForFunction(() => document.getElementById("uploadMessage").textContent.includes("backend is outdated"));
        assert.equal(writes.length, beforeUpload);
        assert.deepEqual(errors, []);
        console.log("PASS: uploads, limits, date errors, template, filters, sorting, charts, reports, structured config, conflict preservation, role visibility, desktop/mobile.");
    } finally { if (browser) await browser.close(); if (server) await new Promise(resolve => server.close(resolve)); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
