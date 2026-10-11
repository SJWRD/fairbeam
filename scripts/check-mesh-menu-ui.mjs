// Real UI layout regression, isolated workspace; no solver run.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import puppeteer from 'puppeteer-core';
import { startStack, chromePath } from './scenarios/stack.mjs';
import { Session } from './scenarios/harness.mjs';
const stack = await startStack({ log: console.log });
const evidence = new URL('../.impeccable/review/', import.meta.url);
await mkdir(evidence, { recursive: true });
let browser;
try {
  browser = await puppeteer.launch({ executablePath: await chromePath(), headless: true, args: ['--no-sandbox', '--enable-unsafe-swiftshader'] });
  for (const lang of ['en', 'tr']) {
    const context = await browser.createBrowserContext();
    const page = await context.newPage();
    await page.setViewport({ width: 1100, height: 850 });
    await page.evaluateOnNewDocument(language => localStorage.setItem('fairbeam.generalSettings', JSON.stringify({ language })), lang);
    const s = new Session(page, { lang, scenario: 'mesh-menu', url: stack.url });
    await page.goto(stack.url, { waitUntil: 'domcontentloaded' });
    await s.wait('.home');
    await s.fill(await s.field(await s.T('home.newProject.name')), `Mesh menu ${lang}`, { blur: false });
    await s.click('home.newProject.create'); await s.wait('.rb');
    await s.click('ribbon.tab.sim', { sel: '.rb-tab' });
    const group = '.rb-group:has(.rb-mesh-fields)';
    await page.waitForSelector(`${group}[data-collapsed]`);
    await page.click(`${group} > .rb-group-toggle`);
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
      const layout = await page.$eval(`${group} > .rb-items`, el => {
        const buttons = [...el.querySelectorAll(':scope > .rb-btn')];
        const rects = buttons.map(b => { const r = b.getBoundingClientRect(); return { x: r.x, y: r.y, bottom: r.bottom }; });
        return { direction: getComputedStyle(el).flexDirection, rects, overflow: el.scrollWidth > el.clientWidth,
          labelsFit: buttons.every(b => { const label = b.querySelector('span'); return !label || label.scrollWidth <= label.clientWidth + 1; }) };
      });
      assert.equal(layout.direction, 'column'); assert.equal(layout.overflow, false);
      assert.ok(layout.labelsFit); assert.ok(layout.rects.length >= 3);
      assert.ok(layout.rects.every((r, i) => Math.abs(r.x - layout.rects[0].x) < 1 && (!i || r.y >= layout.rects[i - 1].bottom)));
      await page.screenshot({ path: new URL(`mesh-menu-${lang}-${theme}.png`, evidence).pathname.replace(/^\/(\w:)/, '$1') });
    }
    await context.close();
  }
  console.log('Mesh menu EN/TR: aligned action rows, readable labels, no overflow; light/dark screenshots saved.');
} finally { await browser?.close(); await stack.stop(); }
