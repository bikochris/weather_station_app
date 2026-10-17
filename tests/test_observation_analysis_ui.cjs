const assert = require("node:assert/strict");
const http = require("node:http");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");

async function run() {
    const root = path.resolve(__dirname, "../frontend");
    const server = http.createServer(async (request, response) => {
        const filename = path.resolve(root, decodeURIComponent(new URL(request.url, "http://localhost").pathname.replace(/^\/app\//, "")));
        if (!filename.startsWith(root + path.sep)) return response.writeHead(403).end();
        try {
            const data = await fs.readFile(filename);
            response.writeHead(200, {"Content-Type": ({".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml"})[path.extname(filename)] || "application/octet-stream"}).end(data);
        } catch { response.writeHead(404).end(); }
    });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    const base = process.env.TEST_APP_URL || `http://127.0.0.1:${server.address().port}`;
    const screenshots = path.join(os.tmpdir(), "observation-analysis-ui");
    await fs.mkdir(screenshots, {recursive: true});
    let browser;
    try {
        browser = await chromium.launch({channel: "chrome", headless: true});
        const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
        const errors = [], requests = [], csvQueries = [], mapQueries = [];
        let fail = false, empty = false, optionsFail = false, argOnly = false, nullOnly = false, positiveOnly = false;
        const pairs = [
            {id: "ARG", label: "Alpha rain gauge", manual_code: "R", automatic_code: "RA", manual_category: "Rainfall Station", automatic_category: "Automatic Rain Gauge", comparison_ids: ["rainfall"]},
            {id: "CLIM", label: "Climatic site", manual_code: "C", automatic_code: "CA", manual_category: "Climatic Station", automatic_category: "Automatic Weather station"},
            {id: "PRINCIPAL", label: "Principal site", manual_code: "P", automatic_code: "PA", manual_name: "Principal Manual Station", automatic_name: "Principal Automatic Weather Station", manual_category: "Principal Station", automatic_category: "Automatic Weather station", comparison_ids: ["temperature", "humidity", "wind_speed"]}
        ];
        const stations = Array.from({length: 323}, (_, index) => ({station_code: `S${index}`, station_name: `Station ${index}`, station_category: "Automatic Weather station"}));
        const rules = [{id: "temperature", label: "Air temperature", manual_element: "101", automatic_element: "1012", method: "hourly"}, {id: "humidity", label: "Humidity", manual_element: "105", automatic_element: "1022", method: "hourly"}, {id: "rainfall", label: "Rainfall", manual_element: "5", automatic_element: "1107", method: "sum"}, {id: "wind_speed", label: "Wind speed", unit: "knots", method: "hourly"}];
        const time = "2026-10-01T08:00:00";
        const defaultDates = {start: time, end: "2026-10-05T00:00:00", start_day: "2026-10-01", end_day: "2026-10-04"};
        page.on("pageerror", error => errors.push(error.message));
        await page.addInitScript(() => sessionStorage.setItem("weather_station_access_token", "test"));
        await page.route("**/*", async route => {
            const url = new URL(route.request().url());
            if (url.pathname.startsWith("/app/")) return route.continue();
            assert.equal(route.request().method(), "GET", "Analysis must never write observations");
            if (url.pathname === "/auth/me") return route.fulfill({json: {user_id: 1, full_name: "Test user", department: "Admin", must_change_password: false}});
            if (url.pathname.startsWith("/notifications")) return route.fulfill({json: {items: [], unread_count: 0, page: 1, total_pages: 1}});
            if (url.pathname.endsWith("/options")) return route.fulfill(optionsFail ? {status: 503, json: {detail: "Options temporarily unavailable"}} : {json: {stations, pairs: argOnly ? pairs.slice(0, 1) : pairs, comparisons: rules, runs: [{run_id: 1, filename: "Source.csv", created_at: time}]}});
            if (!url.pathname.startsWith("/observation-analysis/")) return route.fulfill({json: []});
            const query = Object.fromEntries(url.searchParams);
            if (url.pathname.endsWith('/maps')) {
                mapQueries.push(query);
                return route.fulfill({json: {...defaultDates, stations, daily_rows: [], series: [], warnings: []}});
            }
            if (query.format === "csv") { csvQueries.push(query); return route.fulfill({body: "station,value\nS0,0", contentType: "text/csv"}); }
            requests.push(query);
            await new Promise(resolve => setTimeout(resolve, 120));
            if (fail) return route.fulfill({status: 422, json: {detail: "Too many result rows; narrow the range"}});
            if (empty) return route.fulfill({json: {...defaultDates, rows: [], series: [], summary: {}, warnings: []}});
            if (url.pathname.endsWith("/comparison")) {
                const parameters = (query.comparison_id || "temperature").split("|");
                const rows = parameters.flatMap(parameter => [
                    {site: "Principal site", site_id: "PRINCIPAL", parameter, parameter_id: parameter, time, manual_station: "P", automatic_station: "PA", manual: 0, observed_automatic: 2, automatic: 2, difference: 2, inconsistent: true, manual_count: 1, automatic_count: 6, expected_automatic: 6, complete: true},
                    {site: "Second site", site_id: "SECOND", parameter, parameter_id: parameter, time: "2026-10-01T09:00:00", manual: 5, observed_automatic: 1.2, automatic: 1.2, difference: -3.8, complete: false, automatic_count: 1, expected_automatic: 6}
                ]);
                const series = parameters.flatMap(parameter => ["Principal site", "Second site"].flatMap((site, index) => [
                    {site, site_id: index ? "SECOND" : "PRINCIPAL", parameter, parameter_id: parameter, time, manual: parameter === "humidity" ? 20 : -5, automatic: parameter === "humidity" ? 40 : 0},
                    {site, site_id: index ? "SECOND" : "PRINCIPAL", parameter, parameter_id: parameter, time: "2026-10-01T09:00:00", manual: null, automatic: null},
                    {site, site_id: index ? "SECOND" : "PRINCIPAL", parameter, parameter_id: parameter, time: "2026-10-01 10:00:00", manual: parameter === "humidity" ? 80 : 5, automatic: parameter === "humidity" ? 100 : 10}
                ]));
                if (nullOnly) for (const point of series) { point.manual = null; point.automatic = null; }
                if (positiveOnly) for (const point of series) { if (point.manual !== null) point.manual += 20; if (point.automatic !== null) point.automatic += 20; }
                return route.fulfill({json: {...defaultDates, rows, series, summary: {paired: 72, missing: 1, bias: 0, mae: 0.70417, rmse: 1}, per_parameter: parameters.map(parameter => ({parameter, paired: 72, mae: 0.70417, r_squared: 0.875})), roses: {manual: {bins: Array(16).fill(2), calm: 1, total: 33}, automatic: {bins: Array(16).fill(4), calm: 2, total: 66}}, warnings: []}});
            }
            const daily = [{station_code: "S0", station_name: "Station 0", element: "1107", time, value: 134.2, automatic_count: 10, expected_automatic: 144, complete: false, partial_day: true, trusted_value: 62.2}, {station_code: "MAN", station_name: "Manual", element: "5", time, value: 5.4, manual_count: 1, automatic_count: 0, expected_automatic: 0, complete: true, trusted_value: 5.4}];
            const series = stations.flatMap(station => [{...station, element: "1107", time, rainfall: 0, wind: null}, {...station, element: "196", time, rainfall: null, wind: 5.1}]);
            series.push({...stations[0], element: "1107", time: "2026-10-02T08:00:00", rainfall: -1.5, wind: null},
                {...stations[0], element: "196", time: "2026-10-02T08:00:00", rainfall: null, wind: 7.5});
            return route.fulfill({json: {...defaultDates, rain_rows: [{...daily[0], value: 0}], daily_rows: daily, daily_max: daily, wind_max: [{...daily[0], value: 5.1}], series, summary: {rain_records: 1, max_daily_rainfall: 134.2, max_wind: 5.1}, warnings: []}});
        });
        const ready = () => page.waitForFunction(() => document.querySelector("#analysisMessage").textContent === "" && !document.querySelector("#analysisCsv").disabled);
        const reveal = id => page.evaluate(id => {
            const element = document.getElementById(id); revealPagePanel(element);
            for (let parent = element.parentElement; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
        }, id);
        async function screenshotsAndPixels(name) {
            for (const width of [1440, 390]) {
                await page.setViewportSize({width, height: 1000});
                await page.waitForTimeout(180);
                assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name}: horizontal page overflow at ${width}`);
                assert.equal(await page.locator(".dashboard-sidebar").isVisible(), true);
                const pixels = await page.locator(".analysis-grouped-charts canvas").evaluateAll(canvases => canvases.map(canvas => {
                    const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
                    let colored = 0; for (let index = 0; index < data.length; index += 4) if (data[index + 3] && Math.max(data[index], data[index + 1], data[index + 2]) - Math.min(data[index], data[index + 1], data[index + 2]) > 40) colored++;
                    return colored;
                }));
                assert.ok(pixels.length && pixels.every(count => count > 5), `${name}: nonblank chart pixels at ${width}: ${pixels}`);
                assert.ok(await page.locator(".analysis-track-legend label").evaluateAll(labels => labels.every(label => {
                    const box = label.querySelector("input").getBoundingClientRect();
                    const name = label.querySelector(".analysis-track-name").getBoundingClientRect();
                    return box.width === 16 && box.height === 16 && name.width > 50 && name.right <= label.getBoundingClientRect().right + 1;
                })), "Legend checkboxes and names fit their rows");
                await page.evaluate(() => window.scrollTo(0, 0));
                await page.screenshot({path: path.join(screenshots, `${name}-${width}.png`), fullPage: true});
            }
        }
        await page.goto(`${base}/app/manual-automatic.html`); await ready();
        assert.deepEqual(await page.locator('.analysis-track-swatch').evaluateAll(nodes => nodes.slice(0, 2).map(node => ({color: getComputedStyle(node).borderTopColor, style: getComputedStyle(node).borderTopStyle}))),
            [{color: "rgb(91, 155, 213)", style: "solid"}, {color: "rgb(237, 125, 49)", style: "solid"}]);
        assert.equal(await page.locator('.analysis-grouped-charts figure').first().evaluate(node => getComputedStyle(node).backgroundColor), "rgb(255, 255, 255)");
        assert.equal(await page.locator('.analysis-grouped-charts figcaption').first().evaluate(node => getComputedStyle(node).textAlign), "center");
        assert.equal(await page.locator(".analysis-grouped-charts canvas").count(), 1);
        assert.equal(await page.locator(".analysis-track-legend input:checked").count(), 4);
        assert.doesNotMatch(await page.locator(".analysis-grouped-charts canvas").getAttribute("aria-label"), /native units/i);
        assert.equal(await page.locator('#chartSeriesSearch, #chartSeriesChoice').count(), 0);
        assert.equal(await page.locator('#pairFilter option[value="PRINCIPAL"]').textContent(), "Principal site");
        assert.equal(await page.locator('#comparisonFilter option[value="rainfall"]').count(), 0);
        assert.equal(await page.locator('#manualRose').evaluate(canvas => canvas.closest('figure').querySelector('figcaption').textContent), "Principal Manual Station");
        await page.evaluate(() => { const select = document.getElementById('pairFilter'); select.value = 'ARG'; select.dispatchEvent(new Event('change', {bubbles: true})); });
        assert.deepEqual(await page.locator('#comparisonFilter option').evaluateAll(options => options.map(option => option.value)), ['', 'rainfall']);
        await page.evaluate(() => { const select = document.getElementById('pairFilter'); select.value = 'PRINCIPAL'; select.dispatchEvent(new Event('change', {bubbles: true})); document.getElementById('comparisonFilter').value = 'temperature'; });
        assert.equal(requests[0].pair_id, "PRINCIPAL"); assert.equal(requests[0].comparison_id, "temperature");
        assert.equal(await page.inputValue("#analysisStart"), "2026-10-01");
        assert.equal(await page.locator("#analysisStart").getAttribute("type"), "date");
        assert.equal(await page.locator('[data-metric="r_squared"]').innerText(), '0.875');
        assert.equal(await page.locator('[data-metric="bias"], [data-metric="mae"], [data-metric="rmse"]').count(), 0);
        assert.equal(requests[0].days, "true");
        assert.equal(await page.locator('#analysisTolerance, #analysisMinutes, #displayScale, #analysisPeriod option[value="custom"]').count(), 0);
        await page.locator('#pairFilter + details > summary').click();
        assert.ok(await page.locator('#pairFilter + details .multi-filter-choices label').evaluateAll(labels => labels.every(label => {
            const input = label.querySelector('input').getBoundingClientRect();
            const range = document.createRange(); range.selectNodeContents(label.lastChild);
            const text = range.getBoundingClientRect();
            return input.width === 16 && text.left >= input.right + 6 && text.width > 40;
        })));
        await page.locator('#pairFilter + details > summary').click();
        const initialCanvas = page.locator('.analysis-grouped-charts canvas');
        assert.equal(await initialCanvas.getAttribute('data-minimum'), '-5');
        assert.equal(await initialCanvas.getAttribute('data-maximum'), '10');
        await initialCanvas.click({position: {x: 65, y: 120}});
        assert.match(await page.locator('.analysis-point-value').innerText(), /Manual 101.*-5/s);
        assert.match(await page.locator('.analysis-point-value').innerText(), /Automatic 1012.*0/s);
        await page.getByRole('button', {name: 'Close values', exact: true}).click();
        await reveal("comparisonRows");
        const rows = page.locator("#comparisonRows tr");
        assert.equal(await page.locator('#comparisonTable th').count(), 8);
        assert.equal(await page.locator('#comparisonTable th').last().textContent(), 'Absolute difference');
        assert.deepEqual(await rows.locator('td:nth-child(8)').allTextContents(), ['2', '3.8']);
        assert.equal(await rows.first().locator("td").count(), 8);
        assert.equal(await rows.first().locator("td").nth(5).innerText(), "0");
        assert.equal(await rows.nth(1).locator("td").nth(6).innerText(), "1.2");
        assert.equal(await rows.nth(1).locator("td").nth(7).innerText(), "3.8");
        await page.locator("#comparisonTable thead th").nth(5).click();
        await page.waitForFunction(() => document.querySelector("#comparisonRows td:nth-child(6)").textContent === "0");
        await page.locator("#comparisonTable thead th").nth(5).press("Enter");
        await page.waitForFunction(() => document.querySelector("#comparisonRows td:nth-child(6)").textContent === "5");
        await screenshotsAndPixels("manual-automatic");
        assert.ok(await page.locator("#manualRose").evaluate(canvas => canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data.some((v, i) => i % 4 === 3 && v > 0)));
        await page.setViewportSize({width: 1440, height: 1000});
        positiveOnly = true; await page.click("#applyAnalysis"); await ready();
        assert.equal(await page.locator('.analysis-grouped-charts canvas').getAttribute('data-minimum'), '15');
        assert.equal(await page.locator('.analysis-grouped-charts canvas').getAttribute('data-maximum'), '30');
        positiveOnly = false; await page.click("#applyAnalysis"); await ready();
        await page.evaluate(() => { document.getElementById("pairFilter").value = "PRINCIPAL|CLIM"; document.getElementById("comparisonFilter").value = "temperature|humidity"; });
        await page.selectOption("#analysisPeriod", "day");
        await page.click("#applyAnalysis"); await ready();
        assert.equal(requests.at(-1).pair_id, "PRINCIPAL|CLIM"); assert.equal(requests.at(-1).comparison_id, "temperature|humidity"); assert.equal(requests.at(-1).period, "day");
        assert.equal(requests.at(-1).minutes, undefined); assert.equal(requests.at(-1).tolerance, undefined);
        assert.equal(await page.locator('[data-metric="r_squared"]').innerText(), "See by parameter");
        assert.equal(await page.locator("#parameterStatisticRows tr").count(), 2);
        assert.deepEqual(await page.locator('#parameterStatistics th').allTextContents(), ['Parameter', 'R\u00b2']);
        assert.deepEqual(await page.locator('#parameterStatisticRows td:nth-child(2)').allTextContents(), ['0.875', '0.875']);
        assert.equal(await page.locator(".analysis-grouped-charts canvas").count(), 1, "Multiple non-wind parameters share one canvas");
        assert.equal(await page.locator(".analysis-track-legend input:checked").count(), 8);
        assert.doesNotMatch(await page.locator(".analysis-grouped-charts canvas").getAttribute("aria-label"), /native units/i);
        const beforeLocalControls = requests.length;
        for (const type of ["line", "column", "area", "scatter"]) {
            await page.selectOption("#chartType", type);
            await screenshotsAndPixels(`combined-${type}`);
        }
        await page.locator(".analysis-track-legend input").first().uncheck();
        assert.match(await page.locator(".analysis-grouped-charts canvas").getAttribute("aria-label"), /7 visible tracks/);
        await page.locator(".analysis-track-legend input").first().check();
        assert.equal(await page.locator(".analysis-track-legend input:checked").count(), 8);
        assert.doesNotMatch(await page.locator(".analysis-grouped-charts canvas").getAttribute("aria-label"), /native units/i);
        assert.equal(requests.length, beforeLocalControls, "Type, scale, search and legend must not refetch");
        await page.setViewportSize({width: 1440, height: 1000});
        await page.click("#applyAnalysis"); await ready();
        assert.equal(await page.inputValue("#chartType"), "scatter");
        await page.evaluate(() => { document.getElementById("comparisonFilter").value = "temperature|humidity|wind_speed"; });
        await page.click("#applyAnalysis"); await ready();
        assert.equal(await page.locator(".analysis-grouped-charts canvas").count(), 2);
        assert.doesNotMatch(await page.locator('[data-chart-kind="wind"] canvas').getAttribute("aria-label"), /native units/i);
        const roseBefore = await page.locator("#manualRose").getAttribute("aria-label");
        const beforeWindType = requests.length;
        for (const type of ["line", "column", "area", "scatter"]) {
            await page.selectOption("#chartType", type);
            await screenshotsAndPixels(`separate-wind-${type}`);
            assert.doesNotMatch(await page.locator('[data-chart-kind="wind"] canvas').getAttribute("aria-label"), /native units/i);
        }
        assert.equal(requests.length, beforeWindType);
        assert.equal(await page.locator("#manualRose").getAttribute("aria-label"), roseBefore);
        await page.setViewportSize({width: 1440, height: 1000});
        nullOnly = true; await page.click("#applyAnalysis"); await ready();
        for (const type of ["line", "column", "area", "scatter"]) {
            await page.selectOption("#chartType", type);
            for (const label of await page.locator(".analysis-grouped-charts canvas").evaluateAll(items => items.map(item => item.getAttribute("aria-label")))) assert.match(label, /No visible observations/);
        }
        nullOnly = false; await page.click("#applyAnalysis"); await ready();
        await reveal("analysisCsv");
        await page.fill("#analysisEnd", "2026-10-20");
        const downloaded = page.waitForEvent("download"); await page.click("#analysisCsv"); await downloaded;
        assert.equal(csvQueries.at(-1).end, "2026-10-04", "CSV must use applied filters"); assert.equal(csvQueries.at(-1).format, "csv");
        await page.fill("#analysisEnd", "2026-10-04");
        const beforeValidation = requests.length;
        await page.fill("#analysisStart", "2026-10-05"); await page.click("#applyAnalysis"); assert.equal(requests.length, beforeValidation);
        await page.fill("#analysisStart", "2026-10-01");
        fail = true; await page.click("#applyAnalysis"); await page.waitForFunction(() => document.querySelector("#analysisMessage").textContent.includes("Too many"));
        assert.equal(await page.locator("#analysisCsv").isDisabled(), true); assert.equal(await page.locator("[data-metric=paired]").innerText(), "-");
        assert.equal(await page.locator(".analysis-track-legend input").count(), 0, "Failed requests clear stale legends");
        fail = false; empty = true; await page.click("#applyAnalysis"); await ready(); assert.match(await page.locator("#comparisonRows").innerText(), /No observations/); empty = false;
        await page.goto(`${base}/app/rainfall-wind.html`); await ready();
        assert.equal(requests.at(-1).period, "day"); assert.equal(requests.at(-1).station_code, undefined);
        await page.waitForFunction(() => document.querySelector('main').getAttribute('aria-busy') === 'false');
        const previousMapRequests = mapQueries.length;
        await page.evaluate(() => { document.getElementById('stationFilter').value = 'S0'; });
        await page.click('#applyAnalysis'); await ready();
        await page.waitForFunction(() => document.querySelector('main').getAttribute('aria-busy') === 'false');
        assert.equal(requests.at(-1).station_code, 'S0');
        assert.equal(mapQueries.length, previousMapRequests + 1);
        assert.equal(mapQueries.at(-1).station_code, undefined);
        assert.equal(mapQueries.at(-1).start, '2026-10-01');
        assert.equal(mapQueries.at(-1).end, '2026-10-04');
        assert.equal(await page.locator("[data-metric=max_daily_rainfall]").innerText(), "134.2");
        await reveal("rainfallHeatmap"); await reveal("windHeatmap");
        assert.equal(await page.locator('#rainfallHeatmap tbody tr').count(), 20);
        assert.equal(await page.locator('#windHeatmap tbody tr').count(), 20);
        assert.equal(await page.locator('#rainfallHeatmap button[data-value="0"]').count(), 20);
        assert.equal(await page.locator('#rainfallHeatmap button[data-value="-1.5"]').count(), 1);
        assert.equal(await page.locator('#rainfallHeatmap td.heatmap-no-data').count(), 19);
        await page.locator('#rainfallHeatmap button[data-value="-1.5"]').click();
        assert.match(await page.locator('#rainfallHeatmap [role=status]').innerText(), /Station 0.*2026-10-02.*-1.5/);
        assert.notEqual(await page.locator('#windHeatmap button[data-value="5.1"]').first().evaluate(el => el.style.background),
            await page.locator('#windHeatmap button[data-value="7.5"]').evaluate(el => el.style.background));
        await page.getByRole('button', {name: 'rainfall: Next stations', exact: true}).click();
        assert.match(await page.locator('#rainfallHeatmap .heatmap-pager').first().innerText(), /Stations 21-40 of 323/);
        await page.getByRole('button', {name: 'rainfall: Previous stations', exact: true}).click();
        assert.equal(await page.locator("#chartSeriesChoice").count(), 0);
        assert.equal(await page.locator(".analysis-grouped-charts canvas").count(), 2);
        assert.equal(await page.locator('[data-chart-kind="nonwind"] canvas').count(), 1);
        const beforeChartChange = requests.length;
        assert.equal(await page.locator("#chartSeriesSearch").count(), 0);
        assert.equal(await page.locator(".analysis-track-legend input").count(), 646);
        assert.equal(requests.length, beforeChartChange, "Chart selection must not refetch");
        await reveal("dailyObservationRows");
        assert.deepEqual(await page.locator("#dailyObservationRows tr").nth(1).locator("td").allTextContents(), ["MAN - Manual", "5", "2026-10-01", "5.4", "1"]);
        await reveal("dailyRows"); assert.match(await page.locator("#dailyRows").innerText(), /134.2.*10/s);
        await screenshotsAndPixels("rainfall-wind");
        const beforeHeatmapPaging = requests.length;
        await page.evaluate(() => {
            const series = Array.from({length: 16}, (_, index) => ({station_code: 'A', station_name: 'Byumba Automatic Weather Station',
                element: '1107', time: `2026-10-${String(index + 1).padStart(2, '0')} 00:00`, rainfall: index * 1.3, wind: null}));
            series.push({station_code: 'B', station_name: 'Byumba Manual Station', element: '5', time: '2026-10-01 00:00', rainfall: 0, wind: null});
            window.renderMonitoringHeatmaps({series});
        });
        await page.getByRole('button', {name: 'rainfall: Next periods', exact: true}).click();
        assert.match(await page.locator('#rainfallHeatmap .heatmap-pager').nth(1).innerText(), /Periods 15-16 of 16/);
        assert.equal(await page.locator('#rainfallHeatmap button[data-value="18.2"]').count(), 1);
        await page.getByRole('button', {name: 'rainfall: Previous periods', exact: true}).click();
        assert.equal(requests.length, beforeHeatmapPaging);
        for (const width of [1440, 390]) {
            await page.setViewportSize({width, height: 1000});
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
            await page.locator('#rainfallHeatmap').screenshot({path: path.join(screenshots, `rainfall-heatmap-${width}.png`)});
        }
        await page.evaluate(() => window.renderMonitoringHeatmaps({}));
        assert.match(await page.locator('#rainfallHeatmap').innerText(), /No rainfall readings/);
        assert.equal(await page.locator('#windHeatmap table').count(), 0);
        const beforeMapControls = requests.length;
        await page.evaluate(async () => {
            const stations = [
                {station_code: 'A2', station_name: 'Southern Automatic Weather Station', latitude: -2.6, longitude: 29.7},
                {station_code: 'A3', station_name: 'Western Automatic Weather Station', latitude: -1.9, longitude: 29.3},
                {station_code: 'M1', station_name: 'Northern Principal Station', latitude: -1.58, longitude: 30.06},
                {station_code: 'M2', station_name: 'Central Principal Station', latitude: -2.0, longitude: 29.9},
                {station_code: 'X', station_name: 'Unknown Location', latitude: null, longitude: null},
                {station_code: 'N', station_name: 'Station Without Readings', latitude: -2.1, longitude: 30.5}
            ];
            const daily_rows = stations.flatMap((station, index) => [1, 2].map(day => ({...station,
                element: station.station_code.startsWith('M') ? '5' : '1107', time: `2026-10-0${day} 00:00`, value: station.station_code === 'N' ? null : index * 10 + day})));
            const series = stations.map((station, index) => ({...station, element: station.station_code.startsWith('M') ? '196' : '1092',
                time: '2026-10-01 08:00', wind: station.station_code === 'N' ? null : 2 + index * 2}));
            await window.renderMonitoringMaps({daily_rows, series, start_day: '2026-10-01', end_day: '2026-10-02'}, {stations});
        });
        await reveal('rainfallGeographicMap'); await reveal('windGeographicMap');
        assert.equal(await page.locator('#rainfallGeographicMap .weather-geographic-map').getAttribute('data-station-count'), '5');
        assert.match(await page.locator('#rainfallGeographicMap .weather-map-legend').innerText(), /No reading/);
        assert.equal(await page.locator('#rainfallGeographicMap .weather-geographic-map').getAttribute('data-estimated'), 'true');
        assert.match(await page.locator('#rainfallGeographicMap [role=status]').innerText(), /1 have missing or invalid coordinates/);
        assert.equal(await page.locator('#rainfallGeographicMap .weather-district-label').count(), 30);
        const coloredMap = await page.locator('#rainfallGeographicMap .leaflet-image-layer').evaluate(async image => {
            await image.decode(); const canvas = document.createElement('canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
            const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0); const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
            let green = 0, transparent = 0;
            for (let i = 0; i < pixels.length; i += 4) { if (pixels[i + 3] && pixels[i + 1] > pixels[i] + 15) green++; if (!pixels[i + 3]) transparent++; }
            return {green, transparent};
        });
        assert.ok(coloredMap.green > 1000 && coloredMap.transparent > 1000, 'Colored surface clipped to Rwanda and station coverage');
        await page.locator('#rainfallGeographicMap .weather-station-marker').first().focus();
        await page.keyboard.press('Enter');
        assert.match(await page.locator('#rainfallGeographicMap .leaflet-popup-content').innerText(), /Rainfall total: 3/);
        await page.locator('#rainfallGeographicMap .weather-station-marker[title="Station Without Readings"]').focus();
        await page.keyboard.press('Enter');
        assert.match(await page.locator('#rainfallGeographicMap .leaflet-popup-content').last().innerText(), /Rainfall total: No reading/);
        await page.locator('#rainfallGeographicMap .leaflet-popup-close-button').last().click();
        assert.equal(await page.locator('[aria-label="rainfall map station type"]').count(), 0);
        assert.match(await page.locator('#rainfallGeographicMap .weather-map-toolbar').innerText(), /Manual station preferred/);
        assert.equal(requests.length, beforeMapControls);
        for (const width of [1440, 390]) {
            await page.setViewportSize({width, height: 1000}); await page.waitForTimeout(200);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
            const mapWidth = await page.locator('#rainfallGeographicMap .leaflet-image-layer').evaluate(el => el.getBoundingClientRect().width);
            assert.ok(mapWidth > (width === 1440 ? 400 : 200), 'Map fits its viewport after resizing');
            await page.locator('#rainfallGeographicMap').screenshot({path: path.join(screenshots, `rainfall-geographic-${width}.png`), style: '.app-header {visibility: hidden;}'});
            await page.locator('#windGeographicMap').screenshot({path: path.join(screenshots, `wind-geographic-${width}.png`), style: '.app-header {visibility: hidden;}'});
        }
        await page.evaluate(async () => { await window.renderMonitoringMaps({}, {}); });
        assert.equal(await page.locator('.weather-geographic-map').count(), 0);
        optionsFail = true; await page.goto(`${base}/app/manual-automatic.html`); await page.waitForSelector("#retryOptions:visible");
        optionsFail = false; argOnly = true; await page.click("#retryOptions"); await ready(); assert.equal(requests.at(-1).comparison_id, "rainfall");
        assert.deepEqual(errors, []);
        console.log(`PASS: aligned multi-select options, inclusive day-only filters, native min/max axes, click-to-view values, four chart types, combined/non-wind separation, available partial values without QC controls, sorting, filtered CSV, validation, responsive screenshots and canvas pixels. Screenshots: ${screenshots}`);
    } finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
