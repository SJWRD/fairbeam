// Exercise the Run panel's real state and request boundary, without submitting solver jobs.
import assert from 'node:assert/strict';
import { build } from 'vite';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url)).replaceAll('\\', '/');
const stub = '\0runner-sweep-fixture';
const result = await build({ root, configFile: false, logLevel: 'silent', resolve: { conditions: ['browser'] },
  plugins: [{ name: 'runner-sweep-fixture', enforce: 'pre',
    resolveId(id, importer) {
      if (id.endsWith('runner-sweep-entry')) return '\0runner-sweep-entry';
      if (id === 'sweep-fixture' || importer?.endsWith('/runner/sweep.ts') &&
        ['../compare/store', './api', './store', '../i18n'].includes(id)) return stub;
    },
    load(id) {
      if (id === '\0runner-sweep-entry') return `export * as sweep from '${root}src/runner/sweep.ts'; export * as fixture from 'sweep-fixture';`;
      if (id === stub) return `
        import { createSignal } from 'solid-js';
        export const requests = [];
        export const api = { submitSweep: async value => { requests.push(structuredClone(value)); return {runs: []}; } };
        export class ApiError extends Error {}
        export const [specs, setSpecs] = createSignal([]);
        export const [submitting, setSubmitting] = createSignal(false);
        export const currentModel = () => ({key: 'test-model'});
        export const jobs = () => [];
        export const values = {};
        export const paramPayload = () => ({integer: 1, length: 10, fixed: 7});
        export const runSettings = () => ({threads: 2});
        export const refreshRuns = async () => {};
        export const pinAll = async () => {};
        export const openResult = async () => {};
        export const attach = () => {};
        export const setFieldErrors = () => {};
        export const setNotice = () => {};
        export const setSubmitError = () => {};
        export const isTerminal = () => true;
        export const t = key => key;`;
    } }], build: { write: false, minify: false, lib: { entry: 'runner-sweep-entry', formats: ['es'] } } });
const code = (Array.isArray(result) ? result[0] : result).output.find(o => o.type === 'chunk').code;
const { sweep: s, fixture: f } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
f.setSpecs([{key: 'integer', type: 'int', minimum: -10, maximum: 10}, {key: 'length', type: 'float', minimum: 0, maximum: 100}]);
const range = (key, start, stop, steps) => ({key, kind: 'range', start: String(start), stop: String(stop), steps: String(steps), list: ''});
for (const [axis, expected] of [
  [range('integer', 0, 5, 3), [0, 3, 5]],
  [range('integer', -5, 0, 3), [-5, -2, 0]],
  [range('integer', 0, 2, 5), [0, 1, 2]],
  [range('length', 0, 1, 4), [0, 0.3333333333, 0.6666666667, 1]],
  [range('length', 2, 9, 1), [2]],
  [{key: 'integer', kind: 'list', list: '1, 2.5, 3', start: '', stop: '', steps: ''}, [1, 3]],
]) {
  s.setAxes([axis]);
  assert.deepEqual(s.sweepPlan().per[0].values, expected);
  assert.equal(s.sweepPlan().count, expected.length);
  await s.startSweep();
  const request = f.requests.at(-1);
  assert.deepEqual(request.sweep, [{key: axis.key, values: expected}], 'request must use the values the user reviewed');
  assert.equal(request.params[axis.key], undefined);
  assert.equal(request.params.fixed, 7);
  assert.equal(request.threads, 2);
  assert.equal(f.submitting(), false);
}
s.setAxes([range('integer', 0, 2, 5), range('length', 1, 2, 2)]);
assert.equal(s.sweepPlan().count, 6);
await s.startSweep();
assert.deepEqual(f.requests.at(-1).sweep, [{key: 'integer', values: [0, 1, 2]}, {key: 'length', values: [1, 2]}]);
assert.deepEqual(f.requests.at(-1).params, {fixed: 7});
for (const axes of [[], [range('integer', 0, 20, 3)], [range('integer', 0, 5, 0)]]) {
  s.setAxes(axes);
  const count = f.requests.length;
  assert.ok(s.sweepPlan().error);
  await s.startSweep();
  assert.equal(f.requests.length, count, 'invalid previews must not submit');
}
console.log('Run panel sweep requests match the preview: integer rounding, negative ties, deduplication, float precision, lists, two axes and invalid input.');
