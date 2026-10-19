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
let browser, page, session;
try {
  browser = await puppeteer.launch({ headless: true, executablePath: await chromePath(), args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader'] });
  for (const lang of ['en', 'tr']) {
    const context = await browser.createBrowserContext();
    page = await context.newPage();
    await page.setViewport({ width: 1440, height: 1000 });
    await page.evaluateOnNewDocument(language => localStorage.setItem('fairbeam.generalSettings', JSON.stringify({ language })), lang);
    const s = new Session(page, { lang, scenario: 'user-project', url: stack.url });
    session = s;
    const started = Date.now();
    await page.goto(stack.url, { waitUntil: 'domcontentloaded' });
    // Match the full scenario suite's cold Vite startup budget; action assertions keep their defaults.
    await s.wait('.home', undefined, { timeout: 45000 });
    console.log(`${lang}: initial interface ready in ${Date.now() - started} ms`);
    for (const [ports, localName] of [[0, 'custom.json'], [1, 'user-port.json'], [0, 'dipole.json']]) {
      const fixture = structuredClone(original);
      fixture.model.id = localName === 'dipole.json' ? original.model.id : `user-geometry-${ports}`;
      fixture.name = fixture.model.name = `User geometry ${ports}`;
      fixture.results = null;
      fixture.run = null;
      fixture.ports = fixture.ports.slice(0, ports);
      const file = join(out, localName);
      await writeFile(file, JSON.stringify(fixture));
      await (await page.$('.app-header input[type=file]')).uploadFile(file);
      await s.waitFor(async id => (await import('/src/state.ts')).bundle()?.model.id === id, fixture.model.id);
      const nav = '.mode-switch button[aria-current=page]';
      assert.equal(await page.$eval(nav, e => e.textContent), await s.T('header.screen.geometry'));
      assert.equal(await page.$eval(nav, e => e.title), await s.T('header.screen.geometryTitle'));
      const before = await page.evaluate(async () => { const m = await import('/src/state.ts'); return { source: m.source(), origin: m.sourceOrigin(), bundle: m.bundle(), count: m.openCount() }; });
      assert.equal(before.origin, 'local-file');
      assert.equal(await page.$eval('.example-picker-trigger-label', e => e.textContent), localName);
      assert.equal(await page.$eval('.example-copy-btn', e => e.getAttribute('aria-disabled')), 'true');
      await page.click(nav);
      await page.focus(nav);
      await page.keyboard.press('Enter');
      await page.keyboard.press('End');
      // A gallery request in the broken implementation is asynchronous.
      await new Promise(r => setTimeout(r, 500));
      const after = await page.evaluate(async () => { const m = await import('/src/state.ts'); return { source: m.source(), origin: m.sourceOrigin(), bundle: m.bundle(), count: m.openCount() }; });
      assert.deepEqual(after, before, `${lang}: active geometry screen retains ${ports}-port file`);
      assert.deepEqual(await page.evaluate(auditInPage, '.mode-switch'), []);
      await page.screenshot({ path: join(out, `${lang}-geometry-${ports}-${localName}.png`) });
    }
    await page.evaluate(async () => (await import('/src/runner/store.ts')).showEmptyDesign('Temporary preview'));
    assert.equal(await page.evaluate(async () => (await import('/src/state.ts')).sourceOrigin()), 'generated');
    await page.evaluate(async () => (await import('/src/runner/store.ts')).restoreProject());
    assert.equal(await page.evaluate(async () => (await import('/src/state.ts')).sourceOrigin()), 'local-file');
    assert.equal(await page.$eval('.mode-switch button[aria-current=page]', e => e.textContent), await s.T('header.screen.geometry'));
    // An explicit gallery choice must still work when a local file has the same basename/model ID.
    await page.click('.example-picker-trigger');
    await s.wait('.example-picker-pop');
    const dipoleChoices = await page.$$('.example-picker-option[title="dipole.json"]');
    assert.equal(dipoleChoices.length, 2, 'local opened project and actual gallery dipole are distinct choices');
    await dipoleChoices[0].click();
    assert.equal(await page.evaluate(async () => (await import('/src/state.ts')).sourceOrigin()), 'local-file');
    await page.click('.example-picker-trigger');
    await s.wait('.example-picker-pop');
    await (await page.$$('.example-picker-option[title="dipole.json"]'))[1].click();
    await s.waitFor(async () => { const m = await import('/src/state.ts'); return m.sourceOrigin() === 'project' && m.source() === 'dipole.json'; });
    assert.equal(await page.$eval('.mode-switch button[aria-current=page]', e => e.textContent), await s.T('header.screen.examples'));
    const result = structuredClone(original);
    result.model.id = 'user-saved-result';
    const resultFile = join(out, 'patch-antenna.json');
    await writeFile(resultFile, JSON.stringify(result));
    await (await page.$('.app-header input[type=file]')).uploadFile(resultFile);
    await s.waitFor(async () => (await import('/src/state.ts')).bundle()?.model.id === 'user-saved-result');
    const screen = { within: '.mode-switch', exact: true };
    await s.click('header.screen.results', screen);
    assert.equal(await page.evaluate(async () => (await import('/src/state.ts')).bundle()?.model.id), 'user-saved-result');
    const savedResult = await page.evaluate(async () => { const m = await import('/src/state.ts'); return { source: m.source(), origin: m.sourceOrigin(), bundle: m.bundle(), count: m.openCount() }; });
    // Start retains the local Results tab; returning to it must not select a gallery example.
    await s.click('header.screen.start', screen);
    await s.wait('.home');
    await s.need('button', await s.T('header.screen.results'), screen);
    await s.click('header.screen.results', screen);
    await s.waitFor(async () => (await import('/src/workspace.ts')).appMode() === 'results');
    assert.deepEqual(await page.evaluate(async () => { const m = await import('/src/state.ts'); return { source: m.source(), origin: m.sourceOrigin(), bundle: m.bundle(), count: m.openCount() }; }), savedResult,
      `${lang}: Start -> Results retains the local saved result`);
    // Gallery navigation is an explicit choice of an example in Start, not the retained Results tab.
    await s.click('header.screen.start', screen);
    const homeExample = '.home-examples .home-item[title="dipole.json"]';
    await s.wait(homeExample);
    assert.equal((await page.$$(homeExample)).length, 1, 'the Home example is one unambiguous action');
    await s.clickSel(homeExample);
    await s.waitFor(async () => { const m = await import('/src/state.ts'); return m.sourceOrigin() === 'project' && m.source() === 'dipole.json'; });
    const exampleSource = await page.evaluate(async () => (await import('/src/state.ts')).source());
    await s.click('header.screen.examples', screen);
    assert.equal(await page.evaluate(async () => (await import('/src/state.ts')).source()), exampleSource);
    assert.equal(await page.evaluate(async () => (await import('/src/state.ts')).sourceOrigin()), 'project');
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
  console.log('User project browser: EN/TR geometry with 0/1 ports, repeated mouse/keyboard selection, Start -> Results retention, explicit Home gallery choice, active example and editable design import passed.');
} catch (error) {
  await page?.screenshot({ path: join(out, 'failure.png') });
  await writeFile(join(out, 'failure.json'), JSON.stringify({ error: String(error), console: session?.consoleIssues }, null, 2));
  throw error;
} finally {
  await browser?.close();
  await stack.stop();
}
