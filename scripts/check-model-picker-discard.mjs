// Real source/designer stores and the Run panel's actual picker body. Only the
// dialog, storage, run-server responses and geometry-preview side effects are controlled.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { build } from 'vite';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url)).replaceAll('\\', '/');
const panel = readFileSync(`${root}src/components/RunPanel.tsx`, 'utf8');
const picker = panel.match(/<select id="rp-model"[\s\S]*?void \(async \(\) => \{([\s\S]*?)\}\)\(\);/);
assert.ok(picker, 'exercise the actual Run-model picker confirmation flow');
const built = await build({ root, configFile: false, logLevel: 'silent', resolve: { conditions: ['browser'] },
  plugins: [{ name: 'model-picker-fixture', enforce: 'pre',
    resolveId(id) {
      if (id.endsWith('model-picker-entry')) return '\0model-picker-entry';
      if (id.includes('lib/ConfirmDialog') || id === 'picker-dialog') return '\0picker-dialog';
    },
    load(id) {
      if (id === '\0picker-dialog') return `export const questions = []; export const confirmDraftDiscard = message => new Promise(resolve => questions.push({ message, resolve }));`;
      if (id === '\0model-picker-entry') return `
        import { batch } from 'solid-js';
        import * as editor from '${root}src/editor/store.ts';
        import * as designer from '${root}src/designer/store.ts';
        import { selectModel, modelKey } from '${root}src/runner/store.ts';
        export { editor, designer, modelKey, selectModel };
        export { questions } from 'picker-dialog';
        const { confirmDiscard, revokeDiscardApproval } = editor;
        const confirmDesignDiscard = designer.confirmDiscard;
        const bundle = () => null, setStartedPythonModel = () => {}, resetAxes = () => {}, runPreview = () => {};
        export async function pick(select) { ${picker[1]} }
      `;
    } }],
  build: { write: false, minify: false, lib: { entry: 'model-picker-entry', formats: ['es'] } } });

const storage = new Map();
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
  get length() { return storage.size; }, key: i => [...storage.keys()][i] ?? null,
  getItem: k => storage.get(k) ?? null, setItem: (k,v) => storage.set(k,String(v)), removeItem: k => storage.delete(k),
} });
globalThis.window = { dispatchEvent: () => true, addEventListener() {}, removeEventListener() {} };
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
globalThis.fetch = async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ models: [], versions: [] }) });
const code = (Array.isArray(built) ? built[0] : built).output.find(o => o.type === 'chunk').code;
const { editor, designer, questions, pick, selectModel, modelKey } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const tick = async () => { for (let i=0;i<20;i++) await Promise.resolve(); };
const scope = 'models-v1:' + 'a'.repeat(64);
const design = { schema: 'fairbeam.design/1', model: { id: 'design', name: 'Design' }, params: [], materials: [], parts: [], ports: [], resistors: [], simulation: { f_min: 1, f_max: 2, boundaries: 'MUR' }, mesh: {}, far_field: { enabled: false } };
designer.defaultDesignerIO.take({ id: 'design', file: 'design.design.json', hash: 'base', readonly: false, backup_scope: scope, design });
designer.edit(d => { d.model.name = 'Unsaved design'; });
editor.setFile({ id: 'A', file: 'A.py', source: 'A=1', hash: 'a1', readonly: false });
editor.setDraft('A=2');
selectModel('A');
await new Promise(resolve => setTimeout(resolve, 1150));
const key = [...storage.keys()].find(k => k.startsWith(`fairbeam:draft:v3:${scope}:design:base:`));
assert.ok(key, 'the production backup timer persisted the dirty design');
const backup = storage.get(key);

const first = pick('B'); await tick();
assert.equal(questions.length, 1);
questions.shift().resolve(false); await first;
assert.equal(questions.length, 0, 'canceling the first question never asks the second');
assert.equal(storage.get(key), backup, 'Cancel preserves the design recovery bytes');
assert.equal(modelKey(), 'A'); assert.equal(editor.draft(), 'A=2');

const second = pick('B'); await tick();
questions.shift().resolve(true); await tick();
assert.equal(questions.length, 1, 'both dirty owners must approve replacement');
questions.shift().resolve(false); await second;
assert.equal(storage.get(key), backup, 'canceling the second question preserves the design recovery bytes');
assert.equal(designer.dirty(), true); assert.equal(editor.dirty(), true); assert.equal(modelKey(), 'A');

// The source approval from the aborted picker is not a reusable permission.
const retry = editor.openSource('B'); await tick();
assert.equal(questions.length, 1, 'an aborted picker revokes the exact target approval');
questions.shift().resolve(false); assert.equal(await retry, false);
assert.equal(editor.file().id, 'A'); assert.equal(editor.draft(), 'A=2');
assert.equal(storage.get(key), backup);
designer.closeDesign();
console.log('Run-model picker passed: either canceled confirmation preserves both drafts and the design backup; canceled second confirmation revokes source approval.');
