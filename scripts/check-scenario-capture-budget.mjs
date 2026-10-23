// Deterministic slow/hung screenshot checks; no browser, application stack or solver.
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { access, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { setImmediate } from 'node:timers/promises';
import { mock } from 'node:test';
import { fileURLToPath } from 'node:url';
import { withStartupDiagnostics } from './scenarios/startup-diagnostics.mjs';

export async function captureBudgetSelfTest() {
  const root = await mkdtemp(join(tmpdir(), 'fairbeam-capture-budget-'));
  const output = [], report = console.error;
  console.error = (...args) => output.push(args.join(' '));
  try {
    for (const mode of ['slow', 'hung']) {
      const directory = join(root, mode), original = new Error('original readiness failure');
      let release, started, options, capture, finished = false;
      const screenshot = new Promise(resolve => { release = resolve; });
      const screenshotStarted = new Promise(resolve => { started = resolve; });
      const bytes = Buffer.from('fixture screenshot');
      const page = Object.assign(new EventEmitter(), {
        url: () => 'http://127.0.0.1/fixture',
        evaluate: async () => ({ text: 'fixture body', html: '<main>fixture body</main>' }),
        screenshot: async input => {
          options = input; started();
          const result = await screenshot;
          if (input.path) await writeFile(input.path, result);
          return result;
        },
      });
      mock.timers.enable({ apis: ['setTimeout'] });
      try {
        capture = assert.rejects(withStartupDiagnostics(page, { directory }, async () => { throw original; }),
          error => error === original, 'capture must preserve the original readiness failure').finally(() => { finished = true; });
        await screenshotStarted;
        mock.timers.tick(5001);
        // Drain filesystem callbacks too: the old five-second race writes startup.json here.
        for (let i = 0; i < 20; i++) await setImmediate();
        assert.equal(finished, false, 'a screenshot taking over five seconds must still be awaited');
        assert.equal(options.path, undefined, 'only the collector may write a completed screenshot');
        assert.equal(options.timeout, undefined, 'Puppeteer ScreenshotOptions has no timeout field');
        assert.equal(options.optimizeForSpeed, true);
        if (mode === 'slow') release(bytes);
        else mock.timers.tick(25000);
        await capture;
        const record = JSON.parse(await readFile(join(directory, 'startup.json'), 'utf8'));
        assert.match(record.error, /original readiness failure/);
        if (mode === 'slow') {
          assert.deepEqual(record.captureErrors, []);
          assert.deepEqual(await readFile(join(directory, 'page.png')), bytes);
        } else {
          assert.deepEqual(record.captureErrors, [{ operation: 'screenshot', error: 'diagnostic capture exceeded 30 seconds' }]);
          await assert.rejects(access(join(directory, 'page.png')), { code: 'ENOENT' });
          release(bytes); // A late browser reply must not write evidence after the capture deadline.
          await setImmediate();
          await assert.rejects(access(join(directory, 'page.png')), { code: 'ENOENT' });
          assert.equal(await readFile(join(directory, 'body.txt'), 'utf8'), 'fixture body');
        }
        assert.deepEqual(page.eventNames(), [], 'listeners are disposed after slow and timed-out captures');
      } finally {
        release(bytes);
        await capture?.catch(() => {}); // Drain the collector even if a regression assertion fails.
        mock.timers.reset();
      }
    }
    assert.equal(output.filter(line => line.startsWith('Startup diagnostics:')).length, 2);
  } finally {
    console.error = report;
    await rm(root, { recursive: true, force: true });
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await captureBudgetSelfTest();
  console.log('Startup screenshot budget checks passed (no browser started).');
}
