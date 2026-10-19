// Production editor state with controlled API/confirmation boundaries; no DOM or solver.
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
        let selected = null;
        export const modelKey = () => selected;
        export const refreshModels = async () => {};
        export const selectModel = id => { selected = id; };
        export const modifierShortcut = s => s;
        export const confirmations = [];
        export let automatic = true;
        export const setAutomatic = value => automatic = value;
        export const confirmDraftDiscard = () => automatic === null
          ? new Promise(resolve => confirmations.push(resolve)) : Promise.resolve(automatic);
        export const t = s => s;
        export const fmt = {dateTime: () => ''};`;
    } }],
  build: { write: false, minify: false, lib: { entry: 'editor-race-entry', formats: ['es'] } } });
const code = (Array.isArray(built) ? built[0] : built).output.find(o => o.type === 'chunk').code;
const { editor: e, fixture: f } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; };
const tick = async () => { for(let i=0;i<20;i++) await Promise.resolve(); };
const source = id => ({id, file: `${id}.py`, source: `${id}=1`, hash: `${id}-hash`, readonly: false});
let reads=[];
f.api.modelSource = async id => { reads.push(id); return source(id); };
f.api.modelHistory = async () => [];
const saved = {hash: 'saved', validation: {valid: true, model: {id: 'A'}}};
const resetA=async()=>{ f.setAutomatic(true); f.selectModel('A'); await e.openSource('A'); e.setDraft('A=2'); };

await resetA(); f.setAutomatic(null); f.selectModel('B');
const cancel=e.syncToModel(); assert.equal(e.syncToModel(),cancel,'mount/effect share one request'); await tick();
assert.equal(f.confirmations.length,1); assert.equal(e.draft(),'A=2');
f.confirmations.shift()(false); assert.equal(await cancel,false);
assert.equal(e.file().id,'A'); assert.equal(e.draft(),'A=2'); assert.equal(f.modelKey(),'A'); assert.equal(e.dirty(),true);

f.selectModel('B'); const discard=e.syncToModel(); await tick(); f.confirmations.shift()(true);
assert.equal(await discard,true); assert.equal(e.file().id,'B'); assert.equal(e.draft(),'B=1');

// The existing Run-model picker already asks: consume only its target/snapshot approval.
await resetA(); f.setAutomatic(null); const approval=e.confirmDiscard('B'); await tick(); f.confirmations.shift()(true); assert.equal(await approval,true);
f.selectModel('B'); assert.equal(await e.syncToModel(),true); assert.equal(f.confirmations.length,0);
await resetA(); f.setAutomatic(null); const oldApproval=e.confirmDiscard('B'); await tick(); f.confirmations.shift()(true); await oldApproval;
e.setDraft('A=3'); f.selectModel('B'); const changedApproval=e.syncToModel(); await tick(); assert.equal(f.confirmations.length,1); f.confirmations.shift()(false); await changedApproval;
assert.equal(e.draft(),'A=3'); assert.equal(f.modelKey(),'A');

// Navigation waits for a save; a failed save or newer edit refuses the switch.
for(const outcome of ['saved','conflict','newer-edit']) {
  await resetA(); const pending=deferred(); f.api.saveSource=()=>pending.promise;
  const save=e.save(); f.selectModel('B'); const before=reads.length, open=e.syncToModel(); await tick();
  assert.equal(e.saving(),true); assert.equal(reads.length,before); assert.equal(e.file().id,'A');
  if(outcome==='newer-edit')e.setDraft('A=3');
  if(outcome==='conflict')pending.reject(Object.assign(new Error('conflict'),{status:409,data:{current_hash:'other'}}));
  else pending.resolve(saved);
  const savedOk=await save, opened=await open;
  assert.equal(e.saving(),false);
  if(outcome==='saved'){assert.equal(savedOk,true);assert.equal(opened,true);assert.equal(e.file().id,'B');}
  else {assert.equal(savedOk,false);assert.equal(opened,false);assert.equal(e.file().id,'A');assert.equal(f.modelKey(),'A');assert.equal(e.draft(),outcome==='newer-edit'?'A=3':'A=2');}
}

// A newer model selection owns the eventual replacement; old modal approval cannot steal it.
await resetA(); f.setAutomatic(null); f.selectModel('B'); const old=e.syncToModel(); await tick();
f.selectModel('C'); const current=e.syncToModel(); await tick();
assert.equal(f.confirmations.length,2); f.confirmations.shift()(true); await tick();
assert.equal(await old,false); assert.equal(e.file().id,'A');
f.confirmations.shift()(true); assert.equal(await current,true); assert.equal(e.file().id,'C');

// Typing or starting a save while a fetch runs invalidates its previous discard approval.
for(const outcome of ['edit','save','failed-fetch']) {
 await resetA(); const response=deferred(); f.api.modelSource=()=>response.promise;
 f.selectModel('B'); const opening=e.syncToModel(); await tick();
 let save, pending;
 if(outcome==='save'){pending=deferred();f.api.saveSource=()=>pending.promise;save=e.save();}
 else e.setDraft('A newer');
 if(outcome==='failed-fetch')response.reject(new Error('B failed')); else response.resolve(source('B'));
 assert.equal(await opening,false); assert.equal(e.file().id,'A');
 if(save){assert.equal(e.saving(),true);pending.resolve(saved);await save;} else assert.equal(e.draft(),'A newer');
 f.api.modelSource=async id=>{reads.push(id);return source(id);};
}

await resetA(); f.setAutomatic(false); let creates=0;
f.api.createModel=async()=>{creates++;return {...source('D'),validation:{valid:true}};};
assert.equal(await e.createModel({id:'D'}),null); assert.equal(creates,0); assert.equal(e.draft(),'A=2');
f.setAutomatic(true); const createResponse=deferred(); f.api.createModel=()=>createResponse.promise;
const creating=e.createModel({id:'D'}); await tick(); f.selectModel('B'); await e.syncToModel();
createResponse.resolve({...source('D'),validation:{valid:true}}); assert.equal(await creating,null); assert.equal(e.file().id,'B');

// Delayed history/version replies still belong to their original editor revision.
const history=deferred(); f.api.modelHistory=()=>history.promise; const refreshing=e.refreshHistory();
f.api.modelHistory=async()=>[{version:'C-version'}]; f.selectModel('C'); await e.syncToModel();
history.resolve([{version:'B-version'}]); await refreshing; assert.deepEqual(e.versions(),[{version:'C-version'}]);
const version=deferred(); f.api.modelVersion=()=>version.promise; const restore=e.loadVersion('old');await tick();
e.setDraft('C newer');version.resolve({source:'C old'});await restore;assert.equal(e.draft(),'C newer');
console.log('Source editor guards passed: Cancel/Discard, target-bound approval, save completion/failure, competing selection, late edits, create, history and restore.');
