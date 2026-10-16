const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");

async function run() {
    const browser = await chromium.launch({channel: "chrome", headless: true});
    try {
        const page = await browser.newPage();
        const errors = [];
        page.on("pageerror", error => errors.push(error.message));
        await page.route("**/*", route => {
            const pathname = new URL(route.request().url()).pathname;
            if (pathname.startsWith("/app/js/") && !/\/(api|config)\.js$/.test(pathname)) return route.fulfill({body: "", contentType: "text/javascript"});
            return pathname.startsWith("/app/") ? route.continue() : route.fulfill({json: []});
        });
        const base = process.env.TEST_APP_URL || "http://127.0.0.1:8000";
        for (const file of fs.readdirSync(path.join(__dirname, "../frontend")).filter(file => file.endsWith(".html"))) {
            await page.goto(`${base}/app/${file}`);
            assert.equal(await page.locator('input[type="file"]').count(), await page.locator(".file-drop-zone").count(), `${file}: every upload must have a drop zone`);
        }
        await page.goto(`${base}/app/documents.html`);
        await page.evaluate(() => revealPagePanel(document.getElementById("entryFile")));
        async function drop(files) {
            return page.evaluate(files => {
                const input = document.getElementById("entryFile");
                const transfer = new DataTransfer();
                files.forEach(file => transfer.items.add(new File(["test"], file.name, {type: file.type})));
                input.closest(".file-drop-zone").dispatchEvent(new DragEvent("drop", {dataTransfer: transfer, bubbles: true, cancelable: true}));
                return [...input.files].map(file => file.name);
            }, files);
        }
        assert.deepEqual(await drop([{name: "report.pdf", type: "application/pdf"}]), ["report.pdf"]);
        assert.deepEqual(await drop([{name: "run.exe", type: "application/octet-stream"}]), ["report.pdf"]);
        assert.match(await page.locator(".file-drop-status").innerText(), /Allowed file types/);
        assert.deepEqual(await drop([{name: "one.pdf"}, {name: "two.pdf"}]), ["report.pdf"]);
        assert.match(await page.locator(".file-drop-status").innerText(), /one file/);
        await page.evaluate(() => { document.getElementById("entryFile").disabled = true; });
        assert.deepEqual(await drop([{name: "other.pdf"}]), ["report.pdf"]);
        await page.evaluate(() => {
            document.getElementById("entryFile").disabled = false;
            document.getElementById("workspaceForm").reset();
        });
        await page.waitForFunction(() => !document.querySelector(".file-drop-status").textContent);
        await page.evaluate(() => {
            const input = document.createElement("input"); input.type = "file"; input.id = "dynamicUpload";
            document.getElementById("workspaceForm").append(input);
        });
        await page.waitForSelector(".file-drop-zone #dynamicUpload");
        for (const width of [1440, 390]) {
            await page.setViewportSize({width, height: 1000});
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
            await page.screenshot({path: path.join(process.env.TEMP || "/tmp", `file-drop-${width}.png`), fullPage: true});
        }
        assert.deepEqual(errors, []);
        console.log("PASS: all file inputs enhanced, file drops, type/count validation, disabled state, reset, dynamic uploads, desktop/mobile.");
    } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
