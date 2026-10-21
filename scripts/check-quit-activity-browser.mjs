// Native close protocol exercised in the real browser UI; no solver or native window is closed.
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import puppeteer from "puppeteer-core";
import { chromePath, startStack } from "./scenarios/stack.mjs";

let stack, browser, page;
const diagnostics = [];
const record = entry => { diagnostics.push(entry); if (diagnostics.length > 40) diagnostics.shift(); };
try {
  stack = await startStack({ log: console.log, niceSolver: false });
  browser = await puppeteer.launch({ executablePath: await chromePath(), headless: true, args: ["--no-sandbox", "--enable-unsafe-swiftshader"] });
  for (const lang of ["en", "tr"]) {
    const context = await browser.createBrowserContext();
    page = await context.newPage();
    page.on("console", message => { if (message.type() === "error") record({ console: message.text().slice(0, 2000) }); });
    page.on("pageerror", error => record({ pageerror: String(error?.stack || error).slice(0, 4000) }));
    page.on("requestfailed", request => record({ url: request.url(), failure: request.failure() }));
    page.on("response", response => { if (response.status() >= 400) record({ url: response.url(), status: response.status() }); });
    await page.setViewport({ width: lang === "en" ? 1440 : 760, height: 900 });
    await page.evaluateOnNewDocument(lang => {
      localStorage.setItem("fairbeam.generalSettings", JSON.stringify({ language: lang }));
      const fetch = window.fetch.bind(window);
      window.__activeForQuit = [
        { id: "external", status: "running", kind: "optimize" },
        { id: "queued-elmer", status: "queued", kind: "research" },
        { id: "done", status: "done" }, { id: "failed", status: "failed" }, { id: "cancelled", status: "cancelled" },
      ];
      window.__quitReads = 0;
      window.fetch = async (url, init) => {
        if (String(url) !== "/api/runs?summary=1") return fetch(url, init);
        window.__quitReads++;
        if (window.__quitUnavailable) throw new TypeError("offline fixture");
        return new Response(JSON.stringify({ runs: window.__activeForQuit }), { headers: { "Content-Type": "application/json" } });
      };
    }, lang);
    await page.goto(stack.url, { waitUntil: "domcontentloaded" });
    await page.waitForSelector(".home");
    const text = key => page.evaluate(async key => (await import("/src/i18n/index.ts")).t(key), key);
    const begin = id => page.evaluate(id => {
      window.__quitAnswer = null;
      const poll = () => {
        const answer = window.fairbeamWindowClose("quit", id);
        if (answer !== "pending") { window.__quitAnswer = answer; clearInterval(window.__quitTimer); }
      };
      window.__quitTimer = setInterval(poll, 30);
      poll();
    }, id);
    const answered = async expected => {
      await page.waitForFunction(() => window.__quitAnswer !== null);
      assert.equal(await page.evaluate(() => window.__quitAnswer), expected);
    };

    await begin(26201);
    await page.waitForSelector("#close-runs-title");
    assert.equal(await page.$eval("#close-runs-title", e => e.textContent), await text("closeRuns.title"));
    assert.match(await page.$eval("#close-runs-description", e => e.textContent), /2/);
    assert.equal(await page.evaluate(() => document.activeElement.textContent), await text("common.cancel"), "Cancel has safe initial focus");
    assert.equal(await page.$eval("#close-runs-title", e => {
      const dialog = e.closest(".dialog");
      return dialog.scrollWidth <= dialog.clientWidth && dialog.getBoundingClientRect().height < 400;
    }), true, "short confirmation stays compact without horizontal overflow");
    if (process.env.FAIRBEAM_SCREENSHOT_DIR) {
      mkdirSync(process.env.FAIRBEAM_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: join(process.env.FAIRBEAM_SCREENSHOT_DIR, `quit-runs-${lang}.png`) });
    }
    await page.keyboard.press("Escape");
    await answered("cancel");
    assert.equal(await page.evaluate(() => window.__activeForQuit.length), 5, "Cancel never cancels or removes a run");

    await page.evaluate(async lang => {
      const store = await import("/src/designer/store.ts");
      await store.createDesign({ id: `quit_${lang}`, name: `Quit ${lang}`, template: "dipole" });
      store.edit(d => { d.model.description = "Keep this after Cancel"; });
    }, lang);
    await begin(26202);
    await page.waitForSelector("#close-runs-title");
    await page.click("#close-runs-title + p"); // description does not approve
    await page.click(".dialog .dialog-actions button:first-child");
    await page.waitForSelector("#close-project-title");
    await page.evaluate(() => window.__activeForQuit.push({ id: "new-during-save", status: "queued" }));
    await page.click(".close-project-dialog .dialog-actions button:first-child"); // Don't save
    await page.waitForSelector("#close-runs-title");
    assert.match(await page.$eval("#close-runs-description", e => e.textContent), /3/);
    await page.keyboard.press("Escape");
    await answered("cancel");
    assert.equal(await page.evaluate(async () => (await import("/src/designer/store.ts")).draft.model.description), "Keep this after Cancel");
    assert.equal(await page.evaluate(async () => (await import("/src/designer/store.ts")).dirty()), true);

    await page.evaluate(async () => { (await import("/src/designer/store.ts")).closeDesign(); window.__quitUnavailable = true; });
    await begin(26203);
    await page.waitForSelector("#close-runs-title");
    assert.equal(await page.$eval("#close-runs-title", e => e.textContent), await text("closeRuns.unknown.title"));
    await page.keyboard.press("Escape");
    await answered("cancel");
    await page.evaluate(() => { window.__quitUnavailable = false; window.__activeForQuit = []; });
    await begin(26204);
    await answered("close");
    assert.ok(await page.evaluate(() => window.__quitReads >= 6), "every new exit obtains fresh server activity");
    console.log(`${lang}: fresh active/queued runs, Escape, deferred discard, new-run recheck and unavailable status pass`);
    await context.close();
  }
} catch (error) {
  const out = join("fairbeam-scenario-failures", "quit-activity");
  mkdirSync(out, { recursive: true });
  writeFileSync(join(out, "error.txt"), String(error?.stack || error));
  writeFileSync(join(out, "diagnostics.json"), JSON.stringify({ browser: diagnostics, children: stack?.diagnostics() }, null, 2));
  await page?.screenshot({ path: join(out, "failure.png") }).catch(() => {});
  throw error;
} finally {
  await browser?.close();
  await stack?.stop();
}
