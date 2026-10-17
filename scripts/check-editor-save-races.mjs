// Exercise the real source-editor store with controllable asynchronous API responses.
import assert from 'node:assert/strict';
import { build } from 'vite';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url)).replaceAll('\\', '/');
const stub = '\0editor-race-stubs';
const built = await build({ root, configFile: false, logLevel: 'silent', resolve: { conditions: ['browser'] },
  plugins: [{ name: 'editor-race-fixture', enforce: 'pre',
    resolveId(id, importer) {
      if (id.endsWith('editor-race-entry')) return '\0editor-race-entry';
      if (id === 'race-stubs' || importer?.endsWith('/editor/store.ts') &&
          ['../runner/api','../runner/store','../lib/shortcut','../lib/ConfirmDialog','../i18n'].includes(id)) return stub;
    },
    load(id) {
      if (id === '\0editor-race-entry') return `export * as editor from '${root}src/editor/store.ts'; export * as fixture from 'race-stubs';`;
      if (id === stub) return `
        export const api = {};
        export class ApiError extends Error {}
        export const effects = [];
        export const applyModelEntry = v => effects.push(['apply', v]);
        export const runPreview = () => effects.push(['preview']);
        export const modelKey = () => null;
        export const refreshModels = async () => {};
        export const selectModel = () => {};
        export const modifierShortcut = s => s;
        export const confirmDraftDiscard = async () => true;
        export const t = s => s;
        export const fmt = {dateTime: () => ''};`;
    } }],
  build: { write: false, minify: false, lib: { entry: 'editor-race-entry', formats: ['es'] } } });
const code = (Array.isArray(built) ? built[0] : built).output.find(o => o.type === 'chunk').code;
const { editor: e, fixture: f } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; };
const source = id => ({id, file: `${id}.py`, source: `${id}=1`, hash: `${id}-hash`, readonly: false});
f.api.modelSource = async id => source(id);
f.api.modelHistory = async () => [];
const saved = {hash: 'saved', validation: {valid: true, model: {id: 'A'}}};

for (const failure of [false, true]) {
  await e.openSource('A'); e.setDraft('A=2');
  const a = deferred(); f.api.saveSource = () => a.promise; const saveA = e.save();
  await e.openSource('B'); e.setDraft('B=2');
  const b = deferred(); f.api.saveSource = () => b.promise; const saveB = e.save();
  const effects = f.effects.length;
  if (failure) a.reject(Object.assign(new Error('conflict'), {status: 409, data: {current_hash: 'A-other'}}));
  else a.resolve(saved);
  await saveA;
  assert.equal(e.file().id, 'B'); assert.equal(e.draft(), 'B=2'); assert.equal(e.dirty(), true);
  assert.equal(e.saving(), true, 'A finally must not finish B save');
  assert.equal(e.conflict(), null); assert.equal(e.message(), null); assert.equal(f.effects.length, effects);
  b.resolve({...saved, validation: {valid: true, model: {id: 'B'}}}); await saveB;
  assert.equal(e.dirty(), false); assert.equal(e.saving(), false);
}
await e.openSource('A'); e.setDraft('old A');
const oldA = deferred(); f.api.saveSource = () => oldA.promise; const pendingA = e.save();
await e.openSource('B'); await e.openSource('A'); e.setDraft('new A');
oldA.resolve(saved); await pendingA;
assert.equal(e.draft(), 'new A'); assert.equal(e.file().hash, 'A-hash'); assert.equal(e.dirty(), true);

const own = deferred(); f.api.saveSource = () => own.promise; const saveOwn = e.save();
e.setDraft('newer A'); own.resolve(saved); await saveOwn;
assert.equal(e.file().source, 'new A'); assert.equal(e.draft(), 'newer A'); assert.equal(e.dirty(), true);

const history = deferred(); f.api.modelHistory = () => history.promise; const pendingHistory = e.refreshHistory();
f.api.modelHistory = async () => [{version: 'B-version'}]; await e.openSource('B');
history.resolve([{version: 'A-version'}]); await pendingHistory;
assert.deepEqual(e.versions(), [{version: 'B-version'}]);

const version = deferred(); f.api.modelVersion = () => version.promise; const restore = e.loadVersion('old');
await Promise.resolve(); e.setDraft('B edited meanwhile'); version.resolve({source: 'B old'}); await restore;
assert.equal(e.draft(), 'B edited meanwhile');

const loading = deferred(); f.api.modelSource = () => loading.promise; const openA = e.openSource('A');
f.api.modelSource = async id => source(id); await e.openSource('B'); e.setDraft('B unsaved');
loading.reject(new Error('A fetch failed')); await openA;
assert.equal(e.file().id, 'B'); assert.equal(e.draft(), 'B unsaved'); assert.equal(e.message(), null);
console.log('Source editor races passed: success, 409, A-B-A, concurrent save, new edits, history, restore and stale load failure.');
