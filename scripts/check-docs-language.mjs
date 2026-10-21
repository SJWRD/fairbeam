#!/usr/bin/env node
// Build with npm run build:site, then serve site-dist/ and set FAIRBEAM_DOCS_URL to that URL.
// This checks the built documentation site, not the app's Vite development server.
// Optional screenshots: FAIRBEAM_SCREENSHOT_DIR=/tmp/fairbeam-docs-tr
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import puppeteer from "puppeteer-core";
import { chromePath } from "./scenarios/stack.mjs";

const base = process.env.FAIRBEAM_DOCS_URL?.replace(/\/$/, "");
if (!base) throw new Error("Build the site with npm run build:site, serve site-dist/, then set FAIRBEAM_DOCS_URL to its local URL (see docs/CHECKS.md).");
try {
  const response = await fetch(`${base}/docs/tr/getting-started.html`, { signal: AbortSignal.timeout(5000) });
  if (!response.ok || !(await response.text()).includes('class="doc-body"')) throw new Error("built documentation page not found");
} catch (error) {
  throw new Error(`FAIRBEAM_DOCS_URL must serve the built site-dist/ documentation: ${error.message}`);
}
const screenshots = process.env.FAIRBEAM_SCREENSHOT_DIR;
if (screenshots) mkdirSync(screenshots, { recursive: true });
const profile = mkdtempSync(join(tmpdir(), "fairbeam-docs-browser-"));
let browser;
try {
  browser = await puppeteer.launch({ userDataDir: profile, executablePath: process.env.CHROME_PATH || await chromePath(), headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => { if (response.status() >= 400 && response.url().startsWith(base)) errors.push(`${response.status()} ${response.url()}`); });
  for (const width of [1440, 390]) {
    await page.setViewport({ width, height: width === 390 ? 844 : 1000, deviceScaleFactor: 1 });
    for (const theme of ["light", "dark"]) {
      await page.goto(`${base}/docs/tr/`, { waitUntil: "networkidle0" });
      await page.evaluate((value) => localStorage.setItem("fairbeam.theme", value), theme);
      for (const slug of ["getting-started", "designer", "cli", "project-files", "experimental-solvers", "coaxial-ports", "appearance"]) {
        await page.goto(`${base}/docs/tr/${slug}.html`, { waitUntil: "networkidle0" });
        const state = await page.evaluate(() => ({
          lang: document.documentElement.lang,
          bodyLang: document.querySelector(".doc-body").lang,
          theme: document.documentElement.dataset.theme,
          overflow: document.documentElement.scrollWidth > innerWidth + 1,
          current: document.querySelector('.docs-nav [aria-current="page"]').textContent,
          title: document.querySelector("h1").textContent,
          links: [...document.querySelectorAll(".docs-nav a, .doc-pager a")].map((a) => new URL(a.href).pathname),
        }));
        assert.equal(state.lang, "tr");
        assert.equal(state.bodyLang, "tr");
        assert.equal(state.theme, theme);
        assert.equal(state.overflow, false, `${slug}: horizontal overflow at ${width}`);
        assert.ok(state.links.every((path) => path.startsWith("/docs/tr/")));
        assert.ok(state.current && state.title);
        if (screenshots) await page.screenshot({ path: join(screenshots, `${slug}-${width}-${theme}.png`) });
      }
    }
  }
  await page.setViewport({ width: 1440, height: 1000 });
  await page.goto(`${base}/docs/designer.html?lang=en#files`, { waitUntil: "networkidle0" });
  await page.evaluate(() => localStorage.setItem("fairbeam.generalSettings", JSON.stringify({ language: "en", preserved: "keep" })));
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click('[data-site-language="tr"]')]);
  assert.equal(new URL(page.url()).pathname, "/docs/tr/designer.html");
  assert.equal(new URL(page.url()).hash, "#files");
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem("fairbeam.generalSettings"))), { language: "tr", preserved: "keep" });
  await page.goto(`${base}/docs/cli.html`, { waitUntil: "networkidle0" });
  await page.waitForFunction(() => location.pathname === "/docs/tr/cli.html" && document.documentElement.lang === "tr");
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click('[data-site-language="en"]')]);
  assert.equal(new URL(page.url()).pathname, "/docs/cli.html");
  assert.equal(await page.$eval(".doc-body", (node) => node.lang), "en");
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem("fairbeam.generalSettings"))), { language: "en", preserved: "keep" });

  // Evidence links must survive translated text-node splitting on the public pages.
  for (const width of [1440, 390]) {
    await page.setViewport({ width, height: width === 390 ? 844 : 1000 });
    for (const lang of ["en", "tr"]) {
      await page.goto(`${base}/features.html`, { waitUntil: "networkidle0" });
      await page.evaluate(lang => window.fairbeamSiteLanguage.setLanguage(lang), lang);
      for (const path of ["validation.html", "benchmarks.html", "linux.html#tested-configuration-and-limits"]) {
        assert.ok(await page.$(`a[href="docs/${path}"]`), `features ${lang}: evidence link ${path}`);
      }
      const values = await page.$eval(".table-block table", node => node.textContent);
      assert.ok(values.includes(lang === "tr" ? "2,13–2,17" : "2.13–2.17"), `features ${lang}: documented dipole range`);
      assert.ok(values.includes(lang === "tr" ? "15,52" : "15.52"), `features ${lang}: horn evidence`);
      assert.ok(values.includes(lang === "tr" ? "0,004" : "0.004"), `features ${lang}: waveguide evidence`);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `features ${lang}: fits ${width}`);
      if (screenshots) await page.screenshot({ path: join(screenshots, `features-${lang}-${width}.png`), fullPage: true });
      await page.goto(`${base}/roadmap.html`, { waitUntil: "networkidle0" });
      assert.ok(await page.$('a[href="https://github.com/ismailakdag/fairbeam/issues/228"]'));
      assert.match(await page.$eval(".rm-intro + .note", node => node.textContent), lang === "tr" ? /yeniden doğrulanmamıştır/ : /have not been reverified/);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `roadmap ${lang}: fits ${width}`);
    }
  }
  await page.goto(base, { waitUntil: "networkidle0" });
  assert.ok(await page.$('#download a[href="docs/linux.html"]'));
  assert.ok(await page.$('#download a[href="docs/from-source.html"]'));
  await page.evaluate(() => window.fairbeamSiteLanguage.setLanguage("en"));

  await page.goto(`${base}/docs/#get-started`, { waitUntil: "networkidle0" });
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click('[data-site-language="tr"]')]);
  assert.equal(new URL(page.url()).hash, "#get-started");
  assert.ok(await page.$("#get-started"));

  // Direct Turkish links and the static page remain readable with JavaScript disabled.
  await page.setJavaScriptEnabled(false);
  await page.goto(`${base}/docs/tr/getting-started.html`, { waitUntil: "networkidle0" });
  assert.equal(await page.$eval("html", (node) => node.lang), "tr");
  assert.match(await page.$eval(".docs-nav", (node) => node.textContent), /Belgeler|Genel bakış/);
  assert.equal(await page.$eval(".doc-body", (node) => node.lang), "tr");
  assert.deepEqual(errors, []);

  // The switch must still work when browser storage is unavailable.
  const blocked = await browser.newPage();
  await blocked.evaluateOnNewDocument(() => {
    Object.defineProperty(window, "localStorage", { get() { throw new Error("Storage unavailable"); } });
    Object.defineProperty(navigator, "languages", { get() { return ["tr-TR"]; } });
  });
  await blocked.goto(`${base}/docs/tr/cli.html`, { waitUntil: "networkidle0" });
  await Promise.all([blocked.waitForNavigation({ waitUntil: "networkidle0" }), blocked.click('[data-site-language="en"]')]);
  assert.equal(new URL(blocked.url()).pathname, "/docs/cli.html");
  assert.equal(await blocked.$eval("html", (node) => node.lang), "en");
  console.log("check-docs-language: 7 pages × 2 widths × 2 themes; EN/TR evidence links, roadmap provenance, language switch, anchors, saved settings, no-JS and unavailable storage OK");
} finally { await browser?.close(); rmSync(profile, { recursive: true, force: true }); }
