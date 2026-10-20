// Fault injection for evidence retention; no browser, app server or solver is started.
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { access, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { scenarioAttemptDirectory, withScenarioAttemptDiagnostics } from './scenarios/attempt-diagnostics.mjs';
import { Session } from './scenarios/harness.mjs';

class FixturePage extends EventEmitter {
  screenshotError;
  url() { return 'http://127.0.0.1/fixture?token=url-secret'; }
  async evaluate(fn) {
    if (fn.name === 'auditInPage') return [];
    // Only startup capture asks for a DOM snapshot in this fixture.
    return { readyState: 'complete', text: 'Fixture', html: '<main>Fixture</main>', regions: {} };
  }
  async screenshot({ path }) {
    if (this.screenshotError) throw this.screenshotError;
    await writeFile(path, 'fixture screenshot');
  }
}

export async function scenarioAttemptDiagnosticsSelfTest() {
  const root = await mkdtemp(join(tmpdir(), 'fairbeam-attempt-evidence-test-'));
  const output = [], originalLog = console.log, originalError = console.error;
  console.log = (...args) => output.push(args.join(' '));
  console.error = (...args) => output.push(args.join(' '));
  try {
    const page = new FixturePage();
    const directories = [1, 2, 3].map(attempt => scenarioAttemptDirectory(root, 'S1', 'en', attempt));
    const run = async (attempt, message) => {
      const directory = directories[attempt - 1];
      const session = new Session(page, { scenario: 'S1', lang: 'en', url: page.url(), artifactDirectory: directory });
      const counts = page.eventNames().map(event => [event, page.listenerCount(event)]);
      await assert.rejects(withScenarioAttemptDiagnostics(page, {
        directory, scenario: 'S1', lang: 'en', attempt, results: session.results,
      }, async () => {
        const request = { url: () => 'http://user:password@127.0.0.1/api/preview?token=request-secret', method: () => 'POST', resourceType: () => 'fetch' };
        page.emit('request', request);
        page.emit('response', { status: () => 422, request: () => request });
        await session.step('same failing step', async () => { throw new Error(message); }, { settle: 0 });
      }), error => error.fatalStep === true, 'capturing evidence must preserve the failed step exception');
      assert.equal(session.results[0].ok, false, 'capturing evidence must not turn a failed step into success');
      assert.deepEqual(page.eventNames().map(event => [event, page.listenerCount(event)]), counts);
      return JSON.parse(await readFile(join(directory, 'attempt.json'), 'utf8'));
    };
    const first = await run(1, 'first attempt token=problem-secret');
    const firstBytes = await readFile(join(directories[0], 'attempt.json'));
    const firstScreenshot = await readFile(first.steps[0].screenshot);
    const second = await run(2, 'second attempt');
    assert.notEqual(first.steps[0].screenshot, second.steps[0].screenshot);
    assert.deepEqual(await readFile(join(directories[0], 'attempt.json')), firstBytes);
    assert.deepEqual(await readFile(first.steps[0].screenshot), firstScreenshot, 'later failures cannot overwrite earlier evidence');
    const record = JSON.parse(await readFile(join(directories[0], 'startup.json'), 'utf8'));
    assert.equal(record.non2xx[0].url, 'http://127.0.0.1/api/preview');
    assert.equal(record.non2xx[0].status, 422);
    assert.doesNotMatch(JSON.stringify(record) + firstBytes, /user:password|url-secret|request-secret|problem-secret/);
    assert.doesNotMatch(await readFile(first.steps[0].screenshot.replace(/\.png$/, '.json'), 'utf8'), /problem-secret/);

    await withScenarioAttemptDiagnostics(page, {
      directory: directories[2], scenario: 'S1', lang: 'en', attempt: 3, results: [{ ok: true }],
    }, async () => 'success');
    await assert.rejects(access(directories[2]), { code: 'ENOENT' }, 'successful attempts create no failure artifacts');
    assert.deepEqual(await readFile(join(directories[0], 'attempt.json')), firstBytes, 'a successful retry preserves the first failed attempt');

    page.screenshotError = new Error('injected screenshot failure');
    const failedCapture = await run(3, 'failed step with unavailable screenshot');
    assert.equal(failedCapture.steps[0].ok, false);
    const captureRecord = JSON.parse(await readFile(join(directories[2], 'startup.json'), 'utf8'));
    assert.ok(captureRecord.captureErrors.some(error => error.operation === 'screenshot'));
    await assert.rejects(access(join(directories[2], 'page.png')), { code: 'ENOENT' });

    const unwritable = join(root, 'regular-file');
    await writeFile(unwritable, 'fixture');
    const sentinel = new Error('original scenario exception');
    await assert.rejects(withScenarioAttemptDiagnostics(page, {
      directory: unwritable, scenario: 'S1', lang: 'en', attempt: 4, results: [],
    }, async () => { throw sentinel; }), error => error === sentinel, 'artifact errors cannot mask the original exception');
    assert.ok(output.some(line => line.includes('Could not save scenario attempt diagnostics')));

    const visualResults = [{ ok: false, step: 'nonfatal visual failure', problems: ['overflow'] }];
    await withScenarioAttemptDiagnostics(page, {
      directory: join(root, 'visual'), scenario: 'S1', lang: 'en', attempt: 5, results: visualResults,
    }, async () => {});
    assert.equal(visualResults[0].ok, false, 'nonfatal failures remain available to the existing result aggregator');
    assert.equal(JSON.parse(await readFile(join(root, 'visual', 'attempt.json'), 'utf8')).steps[0].ok, false);
  } finally {
    console.log = originalLog; console.error = originalError;
    await rm(root, { recursive: true, force: true });
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await scenarioAttemptDiagnosticsSelfTest();
  console.log('Scenario attempt evidence retention checks passed.');
}
