// Deterministic launch failures: no browser, app server or solver is started.
import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launchWithEvidence } from './scenarios/launch-evidence.mjs';

export async function browserLaunchEvidenceSelfTest() {
  const root = await mkdtemp(join(tmpdir(), 'fairbeam-launch-evidence-test-'));
  const records = async directory => {
    const entries = await readdir(directory);
    assert.equal(entries.length, 1);
    const folder = join(directory, entries[0]);
    return { folder, read: async name => JSON.parse(await readFile(join(folder, name), 'utf8')) };
  };
  try {
    const directory = join(root, 'failed'), events = [], messages = [];
    const options = { executablePath: 'fixture-browser', headless: true,
      args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader'], marker: {} };
    const original = new Error('fixture timeout https://user:pass@127.0.0.1/error?token=url-secret#fragment token=error-secret');
    const secretObject = { toString() { throw new Error('Logger must not inspect an object'); }, secret: 'object-secret' };
    let calls = 0, cleaned = false;
    const stack = { diagnostics: () => {
      assert.equal(cleaned, false, 'Capture must precede caller cleanup');
      events.push('capture');
      return Array.from({ length: 8 }, () => ({ name: 'vite password=name-secret', pid: 123,
        exited: false, output: 'https://user:pass@127.0.0.1/?token=stack-url-secret Bearer bearer-secret\n'
          + '"token": "quoted secret with spaces"\n'
          + 'powershell -EncodedCommand ZW5jb2RlZC1zZWNyZXQ=\n' + 'x'.repeat(20000),
        env: { secret: 'environment-secret' }, command: 'command-secret' }));
    } };
    const fake = { launch: async supplied => {
      calls++;
      assert.deepEqual(Object.keys(supplied).sort(), [...Object.keys(options), 'logger'].sort());
      for (const key of Object.keys(options)) assert.equal(supplied[key], options[key], `Preserve option ${key}`);
      const { read } = await records(directory);
      assert.equal((await read('prelaunch.json')).phase, 'before-launch');
      events.push('launch');
      assert.equal(supplied.logger('puppeteer:protocol:SEND ►'), undefined);
      const log = supplied.logger('puppeteer:browsers:launcher');
      log(secretObject); log('Launching secret command', secretObject);
      log('Launched 123', secretObject); log('Browser process 999 exists');
      for (let i = 0; i < 90; i++) log('Browser process 123 exists', secretObject);
      throw original;
    } };
    try {
      await assert.rejects(launchWithEvidence(fake, options, { directory, stack, report: message => messages.push(message) }),
        error => error === original, 'Rethrow the exact original object');
    } finally {
      const { read } = await records(directory);
      assert.equal((await read('failure.json')).phase, 'launch-failed', 'Evidence must exist before caller cleanup');
      cleaned = true; events.push('cleanup');
    }
    assert.equal(calls, 1, 'Never retry a failed launch');
    assert.deepEqual(events, ['launch', 'capture', 'cleanup']);
    assert.deepEqual(messages, []);
    const { read } = await records(directory), failed = await read('failure.json'), intent = await read('prelaunch.json');
    assert.equal(failed.ownedPid, 123);
    assert.equal(failed.lifecycle.length, 64); assert.equal(failed.dropped, 27);
    assert(failed.lifecycle.every(entry => entry.pid === 123));
    assert.equal(failed.processes.length, 4);
    assert(failed.processes.every(entry => entry.output.length <= 4000));
    assert.equal(intent.explicitTimeout, false); assert.equal(intent.explicitProfile, false);
    assert.equal(intent.explicitTransport, false);
    assert.deepEqual(intent.approvedArguments, options.args);
    assert.doesNotMatch(JSON.stringify({ failed, intent }), /url-secret|error-secret|name-secret|bearer-secret|quoted secret|ZW5jb2Rl|object-secret|environment-secret|command-secret|user:pass|fragment/);

    const successDir = join(root, 'success'), browser = {};
    let succeeded = 0;
    assert.equal(await launchWithEvidence({ launch: async () => { succeeded++; return browser; } }, options,
      { directory: successDir, stack: { diagnostics: () => { throw new Error('Successful launch needs no failure snapshot'); } } }), browser);
    assert.equal(succeeded, 1);
    const success = await records(successDir);
    assert.equal((await success.read('launched.json')).phase, 'launched');
    await assert.rejects(success.read('failure.json'), { code: 'ENOENT' });

    const badDirectory = join(root, 'regular-file');
    await writeFile(badDirectory, 'not a directory');
    const warnings = [], secondary = { directory: badDirectory, report: message => warnings.push(message) };
    await assert.rejects(launchWithEvidence({ launch: async () => { throw original; } }, options, secondary), error => error === original);
    assert.equal(await launchWithEvidence({ launch: async () => browser }, options, secondary), browser);
    assert(warnings.length >= 2, 'Evidence write failures must be reported, not change launch outcomes');
    assert.doesNotMatch(warnings.join('\n'), /error-secret|url-secret/);
    const captureDir = join(root, 'capture-error');
    await assert.rejects(launchWithEvidence({ launch: async () => { throw original; } }, options,
      { directory: captureDir, stack: { diagnostics: () => { throw new Error('token=capture-secret'); } } }), error => error === original);
    assert.doesNotMatch(JSON.stringify(await (await records(captureDir)).read('failure.json')), /capture-secret/);
    const unreadable = Object.create(null, { stack: { get() { throw new Error('unreadable stack'); } } });
    await assert.rejects(launchWithEvidence({ launch: async () => { throw unreadable; } }, options,
      { directory: join(root, 'unreadable') }), error => error === unreadable);
  } finally {
    assert.equal(resolve(dirname(root)), resolve(tmpdir()));
    assert(basename(root).startsWith('fairbeam-launch-evidence-test-'));
    await rm(root, { recursive: true, force: true });
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await browserLaunchEvidenceSelfTest();
  console.log('Browser launch evidence checks passed (no browser started).');
}
