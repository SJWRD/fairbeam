import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import { gpuInstallError, gpuProgress } from '../src/lib/gpuRuntimeFeedback.ts';

// Run the component's real async handlers with deferred IPC replies. No GPU, app window,
// installer or run server is needed to reproduce replies racing live installer events.
const source = readFileSync(new URL('../src/components/GeneralSettings.tsx', import.meta.url), 'utf8');
const start = source.indexOf('  let box!:');
const end = source.indexOf('  return <Show when={props.open}>');
assert.ok(start >= 0 && end > start, 'the settings component body is found');
const compiled = await transformWithEsbuild(`function create(props: any) {
${source.slice(start, end)}
return { loadDesktop, installGpuRuntime, progressListener,
  state: () => ({ installing: gpuInstalling(), progress: gpuInstallProgress(),
    workspace: nextWorkspace(), message: message(), ready: gpuChoiceReady() }) };
}`, 'gpu-progress-handlers.ts', { loader: 'ts' });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
function harness() {
  const queries = [], installs = [], cleanup = [], listeners = new Map();
  const deps = {
    createSignal: initial => { let value = initial; return [() => value, next => { value = next; }]; },
    createEffect: () => {}, useModal: () => {},
    onMount: run => run(), onCleanup: run => cleanup.push(run),
    window: { addEventListener: (name, listener) => listeners.set(name, listener), removeEventListener: name => listeners.delete(name) },
    document: { documentElement: { dataset: {} } },
    readGeneralSettings: () => ({ units: 'mm' }), gpuProgress, gpuInstallError,
    t: key => key, errorText: error => error instanceof Error ? error.message : String(error),
    native: () => ({ invoke: command => {
      const call = deferred();
      if (command === 'get_general_settings') queries.push(call);
      else if (command === 'install_gpu_runtime') installs.push(call);
      else throw new Error(`Unexpected command: ${command}`);
      return call.promise;
    } }),
  };
  const create = Function('deps', `const {${Object.keys(deps).join(',')}} = deps; ${compiled.code}; return create;`)(deps);
  const component = create({ open: false, close() {} });
  return { ...component, queries, installs,
    progress: detail => listeners.get('fairbeam:gpu-runtime-progress')?.({ detail }),
    dispose: () => cleanup.forEach(run => run()) };
}
const idle = { gpu_runtime_installing: false, gpu_runtime_progress: null, next_workspace: 'original' };
const running = { gpu_runtime_installing: true, gpu_runtime_progress: { step: 'download-openems', fraction: .1 } };
const results = [];
async function check(name, test) {
  try { await test(); results.push([name, true]); }
  catch (error) { results.push([name, false]); console.error(name, error.message); }
}
await check('live progress wins over an older idle snapshot', async () => {
  const h = harness(), load = h.loadDesktop();
  h.progress({ step: 'download-openems', fraction: .42 });
  h.queries[0].resolve(idle); await load;
  assert.equal(h.state().installing, true);
  assert.equal(h.state().progress.fraction, .42);
  assert.equal(h.state().workspace, 'original', 'non-GPU settings still load');
});
await check('a terminal event cannot be undone by an older running snapshot', async () => {
  const h = harness(), load = h.loadDesktop();
  h.progress({ finished: true, success: false });
  h.queries[0].resolve(running); await load;
  assert.equal(h.state().installing, false);
  assert.equal(h.state().progress.finished, true);
});
await check('local start wins before the first progress event; failure also invalidates old state', async () => {
  const h = harness(), before = h.loadDesktop(), install = h.installGpuRuntime();
  h.queries[0].resolve(idle); await before;
  assert.equal(h.state().installing, true, 'local start is not waiting for the first native event');
  const beforeFailure = h.loadDesktop();
  h.installs[0].reject({ code: 'gpu_runtime_install_failed', detail: 'download interrupted' }); await install;
  h.queries[1].resolve(running); await beforeFailure;
  assert.equal(h.state().installing, false);
  assert.match(h.state().message, /download interrupted/);
});
await check('newer settings reply wins regardless of delivery order', async () => {
  const h = harness(), first = h.loadDesktop(), second = h.loadDesktop();
  h.queries[1].resolve({ ...idle, next_workspace: 'new' }); await second;
  h.queries[0].resolve({ ...running, next_workspace: 'old' }); await first;
  assert.equal(h.state().workspace, 'new');
  assert.equal(h.state().installing, false);
});
await check('an obsolete query error does not overwrite newer success', async () => {
  const h = harness(), first = h.loadDesktop(), second = h.loadDesktop();
  h.queries[1].resolve(idle); await second;
  h.queries[0].reject(new Error('old probe failed')); await first;
  assert.equal(h.state().message, '');
});
await check('disposing the component ignores pending replies and removes progress listener', async () => {
  const h = harness(), load = h.loadDesktop();
  h.dispose(); h.progress({ step: 'verify' }); h.queries[0].resolve(running); await load;
  assert.equal(h.state().installing, false);
  assert.equal(h.state().progress, null);
});
await check('a fresh snapshot still restores in-progress state after reopening', async () => {
  const h = harness(), load = h.loadDesktop();
  h.queries[0].resolve(running); await load;
  assert.equal(h.state().installing, true);
  assert.equal(h.state().progress.fraction, .1);
});
await check('ignored progress events neither start installation nor invalidate a valid snapshot', async () => {
  const h = harness(), load = h.loadDesktop();
  for (const value of [null, undefined, 'invalid', [], {}, { fraction: NaN }, { fraction: Infinity }, { finished: false }]) {
    h.progress(value);
    assert.equal(h.state().installing, false);
    assert.equal(h.state().progress, null);
  }
  h.queries[0].resolve(running); await load;
  assert.equal(h.state().installing, true);
  assert.equal(h.state().progress.fraction, .1);
});
await check('successful installation refreshes availability and leaves progress stopped', async () => {
  const h = harness(), install = h.installGpuRuntime();
  h.progress({ finished: true, success: true });
  h.installs[0].resolve();
  await Promise.resolve();
  assert.equal(h.queries.length, 1, 'success refreshes desktop availability');
  h.queries[0].resolve({ ...idle, gpu_choice_ready: true, gpu_runtime_progress: { finished: true } });
  await install;
  assert.equal(h.state().installing, false);
  assert.equal(h.state().ready, true);
  assert.equal(h.state().message, 'settings.gpuManaged.installed');
});
console.log(`GPU progress ordering: ${results.filter(([, ok]) => ok).length}/${results.length} passed`);
assert.ok(results.every(([, ok]) => ok), 'GPU snapshot ordering regressions');
