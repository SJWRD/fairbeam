// Small local HTTP fixtures exercise failure evidence without a solver or application stack.
import assert from 'node:assert/strict';
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { withStartupDiagnostics } from './scenarios/startup-diagnostics.mjs';

const events = ['console', 'pageerror', 'request', 'requestfinished', 'requestfailed', 'response'];
const listenerCounts = page => events.map(event => page.listenerCount(event));

/** Keep failed self-test evidence where CI uploads it; cleanup must not mask the test failure. */
export async function withSelfTestArtifacts(run, cleanup, {
  failureRoot = join(tmpdir(), 'fairbeam-scenario-failures'),
} = {}) {
  await mkdir(failureRoot, { recursive: true });
  const directory = await mkdtemp(join(failureRoot, 'startup-diagnostics-self-test-'));
  let failed = false, original;
  try { await run(directory); }
  catch (error) { failed = true; original = error; }
  try { await cleanup(); }
  catch (error) {
    console.error('Startup diagnostics self-test cleanup failed:', error);
    if (!failed) { failed = true; original = error; }
  }
  if (failed) {
    console.error(`Startup diagnostics self-test evidence retained: ${directory}`);
    throw original;
  }
  await rm(directory, { recursive: true, force: true });
}

export async function startupDiagnosticsSelfTest(browser) {
  const held = new Set();
  const server = createServer((request, response) => {
    const path = new URL(request.url, 'http://fixture').pathname;
    if (path === '/pending') { held.add(response); response.once('close', () => held.delete(response)); return; }
    if (path === '/broken') { request.socket.destroy(); return; }
    if (path === '/unavailable') { response.writeHead(503); response.end('unavailable'); return; }
    response.setHeader('Content-Type', 'text/html');
    if (path === '/ok') { response.end('<!doctype html><main class="home">Ready</main>'); return; }
    response.end(`<!doctype html><main class="loading">Still loading</main><input value="input-secret"><script>
      const sourceOnly = 'source-secret';
      for (let i = 0; i < 125; i++) console.warn('startup warning ' + i);
      console.error('fixture console error token=console-secret');
      setTimeout(() => { throw new Error('fixture page error'); });
      fetch('/unavailable?token=query-secret').then(() => window.unavailableSeen = true);
      fetch('/broken?token=query-secret').catch(() => window.brokenSeen = true);
      fetch('/pending?token=query-secret');
    </script>`);
  });
  let context;
  await withSelfTestArtifacts(async directory => {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const url = `http://127.0.0.1:${server.address().port}`;
    context = await browser.createBrowserContext();
    const page = await context.newPage(), baseline = listenerCounts(page);
    const success = join(directory, 'success');
    await withStartupDiagnostics(page, { directory: success }, async () => {
      await page.goto(`${url}/ok`);
      await page.waitForSelector('.home');
    });
    await assert.rejects(access(success), { code: 'ENOENT' }, 'successful startup writes no artifacts');
    assert.deepEqual(listenerCounts(page), baseline, 'successful startup disposes every diagnostic listener');

    const failure = join(directory, 'failure');
    let original;
    await assert.rejects(withStartupDiagnostics(page, {
      directory: failure,
      stack: { diagnostics: () => [{ name: 'fixture', pid: 123, exited: false, exitCode: null, output: 'child ready; token=child-secret' }] },
    }, async () => {
      await page.goto(`${url}/failure`, { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => window.unavailableSeen && window.brokenSeen);
      try { await page.waitForSelector('.home', { visible: true, timeout: 100 }); }
      catch (error) { original = error; throw error; }
    }), error => error === original && error.name === 'TimeoutError', 'the original readiness failure must remain a failure');
    // Puppeteer also attaches completion listeners for the pending fixture request.
    // Finish it explicitly, then check that our listeners have all been removed.
    for (const response of held) response.end('released');
    await page.waitForNetworkIdle({ idleTime: 50, timeout: 5000 });
    assert.deepEqual(listenerCounts(page), baseline, 'failed startup also disposes every diagnostic listener');
    const record = JSON.parse(await readFile(join(failure, 'startup.json'), 'utf8'));
    if (record.captureErrors.length) {
      console.error('Startup diagnostics self-test capture errors:', JSON.stringify(record.captureErrors));
    }
    assert.match(record.error, /\.home/);
    assert.equal(record.dom.regions['.home'].length, 0);
    assert.equal(record.dom.regions['.loading'][0].visible, true);
    assert.ok(record.pageErrors.some(entry => entry.text.includes('fixture page error')));
    assert.ok(record.consoleMessages.some(entry => entry.text.includes('fixture console error')));
    assert.equal(record.consoleMessages.length, 100);
    assert.ok(record.dropped.console >= 25, 'noisy startup output is bounded and truncation is explicit');
    assert.ok(record.non2xx.some(entry => entry.status === 503 && entry.url.endsWith('/unavailable')));
    assert.ok(record.failures.some(entry => entry.url.endsWith('/broken')));
    assert.ok(record.outstanding.some(entry => entry.url.endsWith('/pending') && entry.durationMs >= 0));
    assert.equal(record.processes[0].name, 'fixture');
    assert.match(record.processes[0].output, /child ready/);
    const text = await readFile(join(failure, 'body.txt'), 'utf8'), html = await readFile(join(failure, 'dom.html'), 'utf8');
    assert.match(text, /Still loading/);
    assert.match(html, /class="loading"/);
    assert.ok((await readFile(join(failure, 'page.png'))).length > 100, 'a real screenshot was saved');
    for (const output of [JSON.stringify(record), text, html]) {
      assert.doesNotMatch(output, /query-secret|child-secret|console-secret|input-secret|source-secret/, 'diagnostics omit source, input values and credentials');
    }
    const unwritable = join(directory, 'regular-file');
    await writeFile(unwritable, 'not a directory');
    const sentinel = new Error('original setup error');
    await assert.rejects(withStartupDiagnostics(page, { directory: unwritable }, async () => { throw sentinel; }), error => error === sentinel,
      'artifact write failure must not replace the original setup error');
    assert.deepEqual(listenerCounts(page), baseline);
  }, async () => {
    // Close the fixture even if closing its browser context fails.
    try { await context?.close(); }
    finally {
      server.closeAllConnections();
      await new Promise(resolve => server.close(resolve));
    }
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { default: puppeteer } = await import('puppeteer-core');
  const { chromePath } = await import('./scenarios/stack.mjs');
  const browser = await puppeteer.launch({ headless: true, executablePath: await chromePath(), args: ['--no-sandbox'] });
  try { await startupDiagnosticsSelfTest(browser); console.log('Startup diagnostics self-test passed.'); }
  finally { await browser.close(); }
}
