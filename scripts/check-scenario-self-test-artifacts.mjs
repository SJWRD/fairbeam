// Fault injection for self-test evidence retention; no browser, solver or application stack.
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { spawnSync } from 'node:child_process';
import { access, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { withSelfTestArtifacts } from './check-scenario-startup-diagnostics.mjs';
import { startupDiagnostics } from './scenarios/startup-diagnostics.mjs';

const script = fileURLToPath(import.meta.url);

async function fault(mode, failureRoot) {
  // The collector must retain its actual capture error while the PNG assertion still fails.
  const page = Object.assign(new EventEmitter(), {
    url: () => 'http://127.0.0.1/fixture',
    evaluate: async () => ({ text: 'fixture body', html: '<main>fixture body</main>', readyState: 'complete' }),
    screenshot: async () => { throw new Error('injected screenshot capture failure'); },
  });
  await withSelfTestArtifacts(async directory => {
    const failure = join(directory, 'failure');
    const diagnostics = startupDiagnostics(page, { directory: failure });
    try { await diagnostics.capture(new Error('original readiness failure')); }
    finally { diagnostics.dispose(); }
    const record = JSON.parse(await readFile(join(failure, 'startup.json'), 'utf8'));
    console.error('Startup diagnostics self-test capture errors:', JSON.stringify(record.captureErrors));
    if (mode === 'cleanup') throw new Error('original self-test assertion');
    assert.ok((await readFile(join(failure, 'page.png'))).length > 100, 'a real screenshot was saved');
  }, async () => {
    if (mode === 'cleanup') throw new Error('injected cleanup failure');
  }, { failureRoot });
}

export async function selfTestArtifactsSelfTest() {
  const root = await mkdtemp(join(tmpdir(), 'fairbeam-self-test-artifacts-regression-'));
  try {
    const success = join(root, 'success');
    let cleaned = false;
    await withSelfTestArtifacts(async directory => {
      await writeFile(join(directory, 'evidence.txt'), 'success fixture');
      await access(directory);
    }, async () => { cleaned = true; }, { failureRoot: success });
    assert.equal(cleaned, true);
    assert.deepEqual(await readdir(success), [], 'successful self-tests remove their temporary evidence');

    for (const mode of ['screenshot', 'cleanup']) {
      const failureRoot = join(root, mode);
      await mkdir(failureRoot);
      const child = spawnSync(process.execPath, [script, '--fault', mode, failureRoot], {
        encoding: 'utf8', windowsHide: true, timeout: 15000,
      });
      assert.ifError(child.error);
      assert.notEqual(child.status, 0, `${mode}: a failed self-test remains nonzero`);
      const output = child.stdout + child.stderr;
      assert.match(output, /self-test capture errors:.*injected screenshot capture failure/);
      assert.match(output, /self-test evidence retained:/);
      if (mode === 'screenshot') assert.match(output, /ENOENT.*page\.png/);
      else {
        assert.match(output, /cleanup failed: Error: injected cleanup failure/);
        assert.match(output, /\nError: original self-test assertion/);
      }
      const entries = await readdir(failureRoot);
      assert.equal(entries.length, 1, `${mode}: failure evidence survives cleanup`);
      assert.match(entries[0], /^startup-diagnostics-self-test-/);
      const failure = join(failureRoot, entries[0], 'failure');
      const record = JSON.parse(await readFile(join(failure, 'startup.json'), 'utf8'));
      assert.match(record.error, /original readiness failure/);
      assert.deepEqual(record.captureErrors, [{ operation: 'screenshot', error: 'injected screenshot capture failure' }]);
      assert.equal(await readFile(join(failure, 'body.txt'), 'utf8'), 'fixture body');
      assert.equal(await readFile(join(failure, 'dom.html'), 'utf8'), '<main>fixture body</main>');
      await assert.rejects(access(join(failure, 'page.png')), { code: 'ENOENT' });
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

if (process.argv[1] && resolve(process.argv[1]) === script) {
  if (process.argv[2] === '--fault') await fault(process.argv[3], process.argv[4]);
  else { await selfTestArtifactsSelfTest(); console.log('Startup self-test evidence retention checks passed.'); }
}
