// User geometry must retain its viewer identity when its active screen is selected again.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import puppeteer from 'puppeteer-core';
import { startStack, chromePath } from './scenarios/stack.mjs';
import { Session, auditInPage } from './scenarios/harness.mjs';

const out = resolve(process.env.FAIRBEAM_TEST_ARTIFACTS || join(tmpdir(), `fairbeam-user-project-${Date.now()}`));
await mkdir(out, { recursive: true });
const original = JSON.parse(await readFile('public/projects/dipole.json', 'utf8'));
const stack = await startStack({ log: console.log });
let browser, page;
try {
  browser = await puppeteer.launch({ headless: true, executablePath: await chromePath(), args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader'] });
  for (const lang of ['en', 'tr']) {
    const context = await browser.createBrowserContext();
    page = await context.newPage();
    await page.setViewport({ width: 1440, height: 1000 });
    await page.evaluateOnNewDocument(language => localStorage.setItem('fairbeam.generalSettings', JSON.stringify({ language })), lang);
    const s = new Session(page, { lang, scenario: 'user-project', url: stack.url });
    await page.goto(stack.url, { waitUntil: 'domcontentloaded' });
    await s.wait('.home');
    for (const ports of [0, 1]) {
      const fixture = structuredClone(original);
      fixture.model.id = `user-geometry-${ports}`;
      fixture.name = fixture.model.name = `User geometry ${ports}`;
      fixture.results = null;
      fixture.run = null;
      fixture.ports = fixture.ports.slice(0, ports);
      const file = join(out, `${fixture.model.id}.json`);
      await writeFile(file, JSON.stringify(fixture));
      await (await page.$('.app-header input[type=file]')).uploadFile(file);
      await s.waitFor(async id => (await import('/src/state.ts')).bundle()?.model.id === id, fixture.model.id);
      const nav = '.mode-switch button[aria-current=page]';
      assert.equal(await page.$eval(nav, e => e.textContent), await s.T('header.screen.geometry'));
      assert.equal(await page.$eval(nav, e => e.title), await s.T('header.screen.geometryTitle'));
      const before = await page.evaluate(async () => { const m = await import('/src/state.ts'); return { source: m.source(), bundle: m.bundle(), count: m.openCount() }; });
      await page.click(nav);
      await page.focus(nav);
      await page.keyboard.press('Enter');
      await page.keyboard.press('End');
      // A gallery request in the broken implementation is asynchronous.
      await new Promise(r => setTimeout(r, 500));
      const after = await page.evaluate(async () => { const m = await import('/src/state.ts'); return { source: m.source(), bundle: m.bundle(), count: m.openCount() }; });
      assert.deepEqual(after, before, `${lang}: active geometry screen retains ${ports}-port file`);
      assert.deepEqual(await page.evaluate(auditInPage, '.mode-switch'), []);
      await page.screenshot({ path: join(out, `${lang}-geometry-${ports}.png`) });
    }
    const result = structuredClone(original);
    result.model.id = 'user-saved-result';
    const resultFile = join(out, 'user-saved-result.json');
    await writeFile(resultFile, JSON.stringify(result));
    await (await page.$('.app-header input[type=file]')).uploadFile(resultFile);
    await s.waitFor(async () => (await import('/src/state.ts')).bundle()?.model.id === 'user-saved-result');
    await s.click('header.screen.results');
    assert.equal(await page.evaluate(async () => (await import('/src/state.ts')).bundle()?.model.id), 'user-saved-result');
    // Gallery navigation remains an explicit choice from Start.
    await s.click('header.screen.start');
    await s.click('header.screen.examples');
    await s.waitFor(async () => (await import('/src/runner/examples.ts')).isExample({ file: (await import('/src/state.ts')).source() }));
    const exampleSource = await page.evaluate(async () => (await import('/src/state.ts')).source());
    await s.click('header.screen.examples');
    assert.equal(await page.evaluate(async () => (await import('/src/state.ts')).source()), exampleSource);
    const design = JSON.parse(await readFile('examples/designs/wideband_dipole_867.design.json', 'utf8'));
    design.model.id = `user_editable_${lang}`;
    design.model.name = `User editable ${lang}`;
    const designFile = join(out, `user-editable-${lang}.design.json`);
    await writeFile(designFile, JSON.stringify(design));
    await (await page.$('.app-header input[type=file]')).uploadFile(designFile);
    await s.waitFor(async () => (await import('/src/workspace.ts')).appMode() === 'design');
    assert.equal(await page.$eval('.mode-switch button[aria-current=page]', e => e.textContent), await s.T('header.screen.design'));
    assert.equal(await page.evaluate(async () => (await import('/src/designer/store.ts')).draft.model.name), design.model.name);
    assert.deepEqual(s.consoleIssues, []);
    await context.close();
  }
  console.log('User project browser: EN/TR geometry with 0/1 ports, repeated mouse/keyboard selection, saved result, explicit gallery, active example and editable design import passed.');
} catch (error) {
  await page?.screenshot({ path: join(out, 'failure.png') });
  throw error;
} finally {
  await browser?.close();
  await stack.stop();
}
