// Browser regression for display-only material overrides, overlapping port labels, and missing-Elmer recovery.
// Uses the real local designer/preview server. The Elmer capability response alone is mocked: no solver runs.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import puppeteer from 'puppeteer-core';
import { startStack, chromePath } from './scenarios/stack.mjs';
import { Session } from './scenarios/harness.mjs';

const stack = await startStack({ log: console.log });
const evidence = process.env.FAIRBEAM_EVIDENCE || '.impeccable/review/design-display';
let browser;
try {
  await mkdir(evidence, { recursive: true });
  browser = await puppeteer.launch({ executablePath: await chromePath(), headless: true,
    args: ['--no-sandbox', '--enable-unsafe-swiftshader'] });
  for (const lang of ['en', 'tr']) {
    const context = await browser.createBrowserContext();
    const page = await context.newPage();
    await page.setViewport({ width: 1280, height: 960 });
    await page.evaluateOnNewDocument(language => localStorage.setItem('fairbeam.generalSettings', JSON.stringify({ language })), lang);
    let submissions = 0;
    await page.setRequestInterception(true);
    page.on('request', request => {
      if (request.url().endsWith('/api/research/probe')) {
        void request.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({
          backend: 'elmer', available: false, reason: 'Test fixture: set FAIRBEAM_ELMER_ROOT or --root',
        }) });
      } else {
        if (request.method() === 'POST' && request.url().endsWith('/api/research/runs')) submissions++;
        void request.continue();
      }
    });
    const s = new Session(page, { lang, scenario: 'design-display', url: stack.url });
    await page.goto(stack.url, { waitUntil: 'networkidle0' });
    await s.wait('.home');
    await page.click('input[name="home-template"][value="patch"]');
    await s.fill(await s.field(await s.T('home.newProject.name')), `Display review ${lang}`, { blur: false });
    await s.click('home.newProject.create');
    await s.wait('.rb');
    await s.store((_, { s }) => s.setSelection({ type: 'part', i: 0 }));
    await s.showDesignPanel('inspector');
    const slider = await s.field(await s.T('props.opacity.part'));
    await slider.evaluate(el => { el.value = '35'; el.dispatchEvent(new Event('input', { bubbles: true })); });
    await page.waitForFunction(async () => (await import('/src/designer/store.ts')).draft.parts[0].opacity === 0.35);
    await page.waitForFunction(async () => (await import('/src/state.ts')).bundle()?.parts.some(p => p.opacity === 0.35));
    assert.equal(await s.store((_, { s }) => s.save()), true);
    // Read the native preview response too: client quick-preview metadata alone is insufficient.
    const nativeOpacity = await page.evaluate(async () => {
      const { api } = await import('/src/runner/api.ts');
      const { draft } = await import('/src/designer/store.ts');
      const result = await api.previewDesign(JSON.parse(JSON.stringify(draft)), {});
      return result.bundle.parts.some(p => p.opacity === 0.35);
    });
    assert.equal(nativeOpacity, true);
    await page.screenshot({ path: `${evidence}/opacity-${lang}.png` });
    await s.store((_, { s }) => s.edit(d => {
      const port = JSON.parse(JSON.stringify(d.ports[0]));
      port.number = 2; port.excite = false;
      d.ports.push(port);
    }));
    await page.waitForFunction(() => document.querySelectorAll('.vp-label-port').length === 2);
    await page.waitForFunction(() => {
      const [a, b] = [...document.querySelectorAll('.vp-label-port')].map(el => el.getBoundingClientRect());
      return a.height && b.height && (a.bottom <= b.top || b.bottom <= a.top);
    });
    await page.screenshot({ path: `${evidence}/labels-${lang}.png` });
    await s.store((_, { s }) => s.undo());
    await s.click('ribbon.tab.sim', { sel: '.rb-tab' });
    await s.click('ribbon.sim.run', { sel: '.rb-btn' });
    await page.select('.rd-solver select', 'elmer-driven');
    await s.wait('[data-testid="elmer-antenna-form"]');
    await page.click('[data-testid="elmer-antenna-form"] input[type=checkbox]');
    await page.click('[data-testid="elmer-antenna-form"] button[type=submit]');
    await s.wait('[data-testid="elmer-antenna-form"] [role=alert]');
    assert.equal(await page.$eval('[data-testid="elmer-antenna-form"] [role=alert]', e => e.textContent), await s.T('elmerAntenna.unavailable'));
    assert.equal(await page.$eval('[data-testid="elmer-antenna-form"] details', e => e.open), false);
    await page.waitForFunction(() => {
      const alert = document.querySelector('[data-testid="elmer-antenna-form"] [role=alert]')?.getBoundingClientRect();
      const body = document.querySelector('[data-testid="elmer-antenna-form"]')?.getBoundingClientRect();
      // scrollTop is integer-rounded in Chromium; allow the remaining fractional pixel.
      return alert && body && alert.top >= body.top - 1 && alert.bottom <= body.bottom + 1;
    });
    assert.equal(submissions, 0, 'unavailable Elmer must not queue a solver job');
    await page.screenshot({ path: `${evidence}/elmer-unavailable-${lang}.png` });
    await s.click('elmerAntenna.setup');
    await s.wait('.research-dialog');
    assert.equal(await page.$eval('#research-form select', e => e.value), 'elmer');
    assert.equal(await page.$('[data-testid="elmer-antenna-form"]'), null, 'setup replaces, rather than stacks, the run dialog');
    assert.equal(await page.$eval('#research-form a', e => e.href), `https://fairbeam.org/docs/${lang === "tr" ? "tr/" : ""}experimental-solvers.html`);
    await page.screenshot({ path: `${evidence}/elmer-setup-${lang}.png` });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.ok(!s.consoleIssues.some(x => x.startsWith('page error')), s.consoleIssues.join('\n'));
    await context.close();
    console.log(`PASS ${lang}: opacity persists through native preview, labels do not overlap, missing Elmer opens setup without a run`);
  }
} finally {
  await browser?.close();
  await stack.stop();
}
