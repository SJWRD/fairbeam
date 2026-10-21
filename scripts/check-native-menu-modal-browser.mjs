// Real App routing, modal and draft ownership with controlled native replies.
// This does not exercise AppKit accelerators or a native file panel.
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import puppeteer from "puppeteer-core";
import { chromePath, startStack } from "./scenarios/stack.mjs";

let stack, browser, page;
const errors = [];
try {
  stack = await startStack({ log: console.log, niceSolver: false });
  browser = await puppeteer.launch({ executablePath: await chromePath(), headless: true,
    args: ["--no-sandbox", "--enable-unsafe-swiftshader"] });
  for (const lang of ["en", "tr"]) {
    const context = await browser.createBrowserContext();
    page = await context.newPage();
    page.on("pageerror", error => errors.push(String(error)));
    await page.setViewport({ width: 1440, height: 900 });
    await page.evaluateOnNewDocument(lang => {
      localStorage.setItem("fairbeam.generalSettings", JSON.stringify({ language: lang }));
    }, lang);
    await page.goto(stack.url, { waitUntil: "domcontentloaded" });
    await page.waitForSelector(".home");
    await page.evaluate(async lang => {
      const store = await import("/src/designer/store.ts");
      await store.createDesign({ id: `modal_${lang}`, name: `Modal ${lang}`, template: "dipole" });
      store.edit(d => { d.model.description = "Keep this unsaved draft"; });
      window.__modalDraft = JSON.stringify(store.draft);
      window.__nativeCalls = [];
      window.__pendingOpen = { handle: `queued-${lang}`, displayName: "Queued.fb" };
      window.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
        window.__nativeCalls.push({ command, args, pending: !!window.__pendingOpen });
        if (command === "native_document_next_open") return window.__pendingOpen;
        if (command === "native_document_dismiss_pending") { window.__pendingOpen = null; return true; }
        if (command === "native_document_open") return new Promise(resolve => { window.__finishPicker = resolve; });
        return null;
      } };
      (await import("/src/designer/simSettingsState.ts")).openSimSettings();
    }, lang);
    await page.waitForSelector(".ss-dialog");
    const snapshot = () => page.evaluate(async () => {
      const store = await import("/src/designer/store.ts");
      return { mode: (await import("/src/workspace.ts")).appMode(), draft: JSON.stringify(store.draft),
        dirty: store.dirty(), replace: !!store.replaceRequest(),
        picker: window.__nativeCalls.filter(call => call.command === "native_document_open").length,
        peek: window.__nativeCalls.filter(call => call.command === "native_document_next_open" && call.pending).length,
        dismissed: window.__nativeCalls.filter(call => call.command === "native_document_dismiss_pending").length };
    });
    const initial = await snapshot();
    await page.evaluate(() => {
      document.querySelector(".ss-dialog input")?.focus();
      for (const action of ["file-new", "file-open", "file-new", "file-open"]) {
        window.fairbeamMenuAction(action);
        window.dispatchEvent(new CustomEvent("fairbeam:menu-action", { detail: JSON.stringify(action) }));
      }
      window.dispatchEvent(new Event("fairbeam:native-document-open"));
    });
    assert.deepEqual(await snapshot(), initial, `${lang}: modal keeps screen/draft and blocks picker, replace and OS queue reads`);
    assert.ok(await page.$(".ss-dialog"), `${lang}: original modal remains open`);

    // A deferred OS request is neither lost nor handled twice after the modal closes.
    await page.keyboard.press("Escape");
    await page.waitForSelector(".ss-dialog", { hidden: true });
    await page.evaluate(() => {
      window.dispatchEvent(new Event("fairbeam:native-document-open"));
      window.dispatchEvent(new Event("fairbeam:native-document-open"));
    });
    await page.waitForSelector("#close-project-title");
    assert.equal(await page.$$eval("#close-project-title", nodes => nodes.length), 1);
    assert.equal((await snapshot()).peek, 1, `${lang}: one queue read after dismissal`);
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => window.__pendingOpen === null);
    assert.deepEqual(await snapshot(), { ...initial, peek: 1, dismissed: 1 }, `${lang}: Cancel retains draft and consumes only queued request`);

    // All three modal markers are honored by the real router, independent of focus.
    for (const marker of ["scrim", "aria", "dialog"]) {
      await page.evaluate(marker => {
        const element = document.createElement(marker === "dialog" ? "dialog" : "div");
        element.id = "native-modal-fixture";
        if (marker === "scrim") element.className = "scrim";
        if (marker === "aria") element.setAttribute("aria-modal", "true");
        if (marker === "dialog") element.setAttribute("open", "");
        document.body.append(element);
        window.fairbeamMenuAction("file-new");
        window.fairbeamMenuAction("file-open");
      }, marker);
      assert.deepEqual(await snapshot(), { ...initial, peek: 1, dismissed: 1 }, `${lang}: ${marker} independently blocks native New/Open`);
      await page.evaluate(() => document.getElementById("native-modal-fixture").remove());
    }

    // Outside a modal, text-field focus still permits Open; existing single-flight
    // ownership covers repeated commands both during the question and the picker.
    await page.evaluate(() => {
      const field = document.createElement("input"); field.id = "native-field-fixture";
      document.body.append(field); field.focus();
      window.fairbeamMenuAction("file-open");
      window.fairbeamMenuAction("file-open");
    });
    await page.waitForSelector("#close-project-title");
    await page.evaluate(() => window.fairbeamMenuAction("file-new"));
    assert.equal((await snapshot()).mode, "design");
    assert.equal(await page.$$eval("#close-project-title", nodes => nodes.length), 1);
    assert.equal(await page.$eval(".close-project-dialog .dialog-actions button:first-child", button => button.textContent),
      await page.evaluate(async () => (await import("/src/i18n/index.ts")).t("closeProject.dontSave")));
    await page.click(".close-project-dialog .dialog-actions button:first-child");
    await page.waitForFunction(() => typeof window.__finishPicker === "function");
    await page.evaluate(() => { window.fairbeamMenuAction("file-open"); window.fairbeamMenuAction("file-open"); });
    assert.equal((await snapshot()).picker, 1, `${lang}: one native picker after dismissal, including repeated Open`);
    await page.evaluate(() => window.__finishPicker({ status: "canceled" }));
    await page.waitForFunction(async () => !(await import("/src/designer/store.ts")).loading());
    const afterPicker = await snapshot();
    assert.equal(afterPicker.draft, initial.draft, `${lang}: native picker Cancel preserves the dirty draft`);
    assert.equal(afterPicker.dirty, true);
    await page.evaluate(() => {
      document.getElementById("native-field-fixture").focus();
      window.fairbeamMenuAction("file-new");
    });
    assert.equal((await snapshot()).mode, "home", `${lang}: New works from a field outside a modal`);
    assert.equal((await snapshot()).draft, initial.draft);
    console.log(`${lang}: real modal/router, unchanged dirty draft, deferred OS Open, single question/picker and outside-modal field commands pass`);
    await context.close();
  }
  assert.deepEqual(errors, [], "no page errors");
} catch (error) {
  const out = process.env.FAIRBEAM_SCREENSHOT_DIR || join("fairbeam-scenario-failures", "native-menu-modal");
  mkdirSync(out, { recursive: true });
  writeFileSync(join(out, "native-menu-modal-error.txt"), String(error?.stack || error));
  writeFileSync(join(out, "native-menu-modal-diagnostics.json"), JSON.stringify({ errors, children: stack?.diagnostics() }, null, 2));
  await page?.screenshot({ path: join(out, "native-menu-modal-failure.png") }).catch(() => {});
  throw error;
} finally {
  try { await browser?.close(); } finally { await stack?.stop(); }
}
