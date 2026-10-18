// Real session/native adapters; controlled history and native replies, no solver or desktop dialog.
import assert from 'node:assert/strict';
import {build} from 'vite';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {stripTypeScriptTypes} from 'node:module';
const root=fileURLToPath(new URL('../',import.meta.url)).replaceAll('\\','/');
const built=await build({root,configFile:false,logLevel:'silent',resolve:{conditions:['browser']},plugins:[{
 name:'native-save-entry',resolveId(id){if(id.endsWith('native-save-entry'))return '\0native-save-entry';},
 load(id){if(id==='\0native-save-entry')return [
  `export {createDesignerSession} from ${JSON.stringify(root+'src/designer/sessionCore.ts')};`,
  `export {createDesignerIO} from ${JSON.stringify(root+'src/designer/sessionIO.ts')};`,
  `export * as native from ${JSON.stringify(root+'src/lib/nativeDocuments.ts')};`,
  `export * as runner from ${JSON.stringify(root+'src/runner/store.ts')};`,
  `export {api} from ${JSON.stringify(root+'src/runner/api.ts')};`,
  `export {createRoot,createEffect,createSignal} from 'solid-js';`,
 ].join('\n');},
}],build:{write:false,minify:false,lib:{entry:'native-save-entry',formats:['es']}}});
const previous=new Map(['localStorage','sessionStorage','window','EventSource'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
const memory=new Map(),storage={getItem:key=>memory.get(key)??null,setItem:(key,value)=>memory.set(key,String(value)),removeItem:key=>memory.delete(key)};
globalThis.localStorage={getItem:()=>null,setItem(){},removeItem(){}};globalThis.sessionStorage=storage;
const code=(Array.isArray(built)?built[0]:built).output.find(x=>x.type==='chunk').code;
const url='data:text/javascript;base64,'+Buffer.from(code).toString('base64');
const {createDesignerSession,createDesignerIO,native:n,runner,api,createRoot,createEffect,createSignal}=await import(url);
const tick=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const deferred=()=>{let resolve;const promise=new Promise(yes=>resolve=yes);return{promise,resolve};};
const design={schema:'fairbeam.design/1',model:{id:'embedded-model',name:'Antenna'},params:[],materials:[],parts:[],ports:[],resistors:[],simulation:{},mesh:{}};
const f={id:'owned',file:'owned.design.json',hash:'base',readonly:false,design,backup_scope:'workspace-A',document_id:'uuid-A'};
const response=(file=f)=>({status:'saved',handle:'grant-A',path:'/Antenna.fb',file,document:{id:'uuid-A',path:'/Antenna.fb',revision:'portable-new',results_directory:'Antenna.results'},results:[],warnings:[]});
const job=(id,status='done',model=f.id)=>({id,model,status,bundle:status==='done'?`${id}.json`:null,finished:status==='done'?Number(id.replace(/\D/g,''))||1:null});
const old=job('old'),running=job('run-1','running');
let nativeReply=()=>Promise.resolve(response()),readDesign=async()=>f,nativeCalls=0,writes=0;
let refreshModels=async()=>{},restoreModelSelection=()=>{};
const adapterSource=readFileSync(root+'src/designer/store.ts','utf8').match(/persistSavedDesign: (async \(f, hash, copy\) => \{[\s\S]*?\n  \}),/)[1];
const persistSavedDesign=Function('persistNativeDocument','refreshModels',`return (${stripTypeScriptTypes(adapterSource)});`)(n.persistNativeDocument,()=>refreshModels());
const firstAdapterSource=readFileSync(root+'src/designer/store.ts','utf8').match(/persistUnsavedDesign: (async \(f, design, copy\) => \{[\s\S]*?\n  \}),/)[1];
const persistUnsavedDesign=Function('persistNativeDocument','refreshModels','nativeDocumentsAvailable','hasDocumentResultScope',`return (${stripTypeScriptTypes(firstAdapterSource)});`)(n.persistNativeDocument,()=>refreshModels(),n.nativeDocumentsAvailable,n.hasDocumentResultScope);
globalThis.window={__TAURI_INTERNALS__:{invoke:async()=>{nativeCalls++;return nativeReply();}}};
const core=createDesignerSession();
const [jobs,setJobs]=createSignal([old,running]),[scope,setScope]=createSignal(f.backup_scope);
let disposeEffects;
createRoot(dispose=>{disposeEffects=dispose;
 createEffect(()=>n.observeNativeDocumentResults(core.file(),jobs(),scope()));
 createEffect(()=>core.setPendingResultsToken(n.nativeResultsPendingToken(core.file())));
});
const io=createDesignerIO(core,{
 transport:{design:id=>readDesign(id),saveDesign:async()=>({hash:`workspace-${++writes}`,validation:{valid:true,checks:[]}})},
 rememberLastDesign(){},readBackup(){return null;},clearBackup(){},stopPreview(){},forgetPreviewFailure(){},resetParamAsks(){},schedulePreview(){},applyModelEntry(){},
 restoreModelSelection:file=>restoreModelSelection(file),persistSavedDesign,persistUnsavedDesign,
});
try {
 for(const outcome of ['saved','cancel','clean-cancel','failure','late-edit','late-open','open-after-fork']) {
  const source={...f,id:`legacy-${outcome}`,document_id:undefined};
  core.takeFile(source);if(outcome!=='clean-cancel')core.edit(d=>d.model.description='Unsaved source edit');
  const draftAtSave=JSON.parse(core.snapshot()),beforeWrites=writes,wait=deferred();
  let request;
  globalThis.window.__TAURI_INTERNALS__.invoke=async(command,args)=>{nativeCalls++;request=args.request;return wait.promise;};
  let ready=0;
  if(outcome==='open-after-fork')restoreModelSelection=()=>queueMicrotask(()=>core.takeFile({...f,id:'after-fork-owner',hash:'after-fork-hash'}));
  const operation=io.saveBeforeLeaving(()=>ready++);await tick();
  assert.equal(writes,beforeWrites,'first native save never writes the legacy source JSON');
  assert.deepEqual(request.draftDesign,draftAtSave);assert.equal(request.workspaceHash,source.hash);
  assert.equal(request.handle,undefined);
  if(outcome==='late-edit')core.edit(d=>d.model.description='Newer live edit');
  if(outcome==='late-open')core.takeFile({...f,id:'new-owner',hash:'new-owner-hash'});
  const fork={...source,id:`fork-${outcome}`,document_id:`fork-uuid-${outcome}`,hash:'fork-hash',design:draftAtSave};
  if(outcome==='cancel'||outcome==='clean-cancel')wait.resolve({status:'canceled'});
  else if(outcome==='failure')wait.resolve(Promise.reject(Error('disk full')));
  else wait.resolve(response(fork));
  assert.equal(await operation,outcome==='saved');
  assert.equal(ready,outcome==='saved'?1:0,'only the intentionally saved and still-current replacement may leave');
  restoreModelSelection=()=>{};
  assert.equal(writes,beforeWrites);
  if(outcome==='saved'){assert.equal(core.file().id,fork.id);assert.equal(core.dirty(),false);}
  else if(outcome==='late-open'){assert.equal(core.file().id,'new-owner');assert.equal(core.file().hash,'new-owner-hash');}
  else if(outcome==='open-after-fork'){assert.equal(core.file().id,'after-fork-owner');assert.equal(core.file().hash,'after-fork-hash');}
  else {
   assert.equal(core.file().id,source.id);assert.equal(core.file().hash,source.hash);
   assert.equal(core.file().design,source.design,'source saved baseline is untouched');
   assert.equal(core.dirty(),outcome!=='clean-cancel');assert.equal(core.persistencePending(),false,'no source write needs a persistence reminder');
   assert.equal(JSON.parse(core.snapshot()).model.description,outcome==='clean-cancel'?undefined:outcome==='late-edit'?'Newer live edit':'Unsaved source edit');
  }
 }
 globalThis.window.__TAURI_INTERNALS__.invoke=async()=>{nativeCalls++;return nativeReply();};
 n.resetNativeDocumentResults(f,jobs(),scope());n.rememberNativeDocument(response());core.takeFile(f);
 assert.equal(core.dirty(),false,'existing history is the explicit Open baseline');
 // Cancelling a clean Save As is a neutral action, not an external-write failure.
 nativeReply=async()=>({status:'canceled'});
 assert.equal(await io.saveAs(),false);assert.equal(core.dirty(),false);
 assert.equal(core.persistencePending(),false);assert.equal(core.file().id,f.id);
 assert.equal(core.message().text,'File save cancelled.');
 assert.equal(n.nativeDocumentFor(f).handle,'grant-A','cancel retains the existing binding');
 nativeCalls=0;nativeReply=async()=>response();
 // Native Save remains available for a clean design: first save and attachment refresh are meaningful.
 const source=readFileSync(root+'src/designer/DesignWorkspace.tsx','utf8');
 const disabled=source.match(/<RButton icon=\{Save\}[^\n]+disabled=\{([^\n]+)\} \/>/)[1];
 const ribbonDisabled=()=>Function('saving','dirty','conflict','nativeDocumentsAvailable','file',`return (${disabled});`)(core.saving,core.dirty,core.conflict,n.nativeDocumentsAvailable,core.file);
 assert.equal(ribbonDisabled(),false);
 const first=job('run-1');setJobs([old,first]);
 assert.equal(core.resultsPending(),true);assert.equal(core.dirty(),true,'result-only work participates in existing close guards');
 assert.equal(core.snapshot(),JSON.stringify(design),'run completion does not edit geometry');
 const token=core.pendingResultsToken();setJobs(structuredClone(jobs()));assert.equal(core.pendingResultsToken(),token,'identical polls are stable');
 setJobs([...jobs(),job('foreign', 'done','copy-workspace'),{...job('research'),kind:'research'}]);assert.equal(core.pendingResultsToken(),token);
 setScope('workspace-B');setJobs([job('foreign-server')]);assert.equal(core.pendingResultsToken(),token,'other server scope cannot dirty this document');
 setJobs([old,first]);setScope(f.backup_scope);

 // Completion after the native request began stays pending even if the backend happened to include it.
 const later=job('run-2'),waiting=deferred();nativeReply=()=>waiting.promise;
 const saving=io.saveBeforeLeaving();await tick();assert.equal(nativeCalls,1);assert.equal(ribbonDisabled(),true);
 setJobs([old,first,later]);waiting.resolve(response());assert.equal(await saving,false);
 assert.equal(core.resultsPending(),true);assert.deepEqual(JSON.parse(core.pendingResultsToken()),[JSON.stringify([later.id,later.finished,later.bundle])]);
 nativeReply=async()=>({...response(),warnings:['An unavailable result was retained as a missing reference']});
 assert.equal(await io.saveBeforeLeaving(),true,'committed save with notices remains successful');
 assert.equal(core.dirty(),false);assert.equal(core.message().tone,'warn');assert.equal(core.message().sticky,true);
 assert.match(core.message().text,/saved\. Result notices: 1\./i);
 setJobs(structuredClone(jobs()));assert.equal(core.dirty(),false,'missing/skipped references cannot produce a warning loop');

 const third=job('run-3');setJobs([...jobs(),third]);nativeReply=async()=>({status:'canceled'});
 assert.equal(await io.save(),false);assert.equal(core.resultsPending(),true);assert.equal(core.persistencePending(),false);
 nativeReply=async()=>{throw Error('disk full');};assert.equal(await io.save(),false);assert.equal(core.resultsPending(),true);
 nativeReply=async()=>response();assert.equal(await io.save(),true);assert.equal(core.dirty(),false);
 setJobs([...jobs(),job('run-4')]);io.releaseDraft();assert.equal(io.isReleased(),true);
 setJobs([...jobs(),job('run-5')]);assert.equal(io.isReleased(),false,"another completion revokes an older Don't save choice");
 const opening=deferred();readDesign=()=>opening.promise;const open=io.openDesign('other');
 setJobs([...jobs(),job('run-6')]);opening.resolve({...f,id:'other'});await open;
 assert.equal(core.file().id,f.id,'a completion during Open retains the current document and its pending results');
 assert.match(core.message().text,/new results arrived/i);

 // The journal is IDs only and survives renderer reload; explicit Open establishes a new baseline.
 const pendingBeforeReload=core.pendingResultsToken(),reloaded=(await import(url+'#reload')).native;
 reloaded.observeNativeDocumentResults(f,jobs(),scope());assert.equal(reloaded.nativeResultsPendingToken(f),pendingBeforeReload);
 reloaded.resetNativeDocumentResults(f,jobs(),scope());assert.equal(reloaded.nativeResultsPendingToken(f),'[]');
 const copy={...f,id:'fb_'+'c'.repeat(32),document_id:'uuid-C'};
 reloaded.resetNativeDocumentResults(copy,jobs(),scope());reloaded.observeNativeDocumentResults(copy,jobs(),scope());assert.equal(reloaded.nativeResultsPendingToken(copy),'[]','source history never dirties a Save As copy');

 // A first native save can race a completion before the workspace has a portable binding.
 const firstFile={...f,id:'first-save',document_id:undefined};core.takeFile(firstFile);setJobs([]);
 const firstWaiting=deferred();nativeReply=()=>firstWaiting.promise;
 const firstSaving=io.save();await tick();const newest=job('new-after-save','done',firstFile.id);setJobs([newest]);
 firstWaiting.resolve(response(firstFile));await firstSaving;assert.equal(core.resultsPending(),true);
 nativeReply=async()=>response(firstFile);await io.save();assert.equal(core.dirty(),false);


 // Existing pending source results are acknowledged normally by a successful Save As.
 const sourceFile={...f,id:'copy-source',document_id:'copy-source-uuid'};
 const savedCopy={...f,id:'saved-copy',document_id:'saved-copy-uuid'};
 const sourceEntry={key:sourceFile.id,file:sourceFile.file,kind:'design',model:design.model,params:[]};
 const copyEntry={...sourceEntry,key:savedCopy.id,file:savedCopy.file,params:[{key:'length',type:'float',default:10,minimum:1,maximum:20}]};
 runner.setModels([sourceEntry]);let refreshCount=0;
 api.models=async()=>{await tick();refreshCount++;return [sourceEntry,copyEntry];};
 refreshModels=runner.refreshModels;
 restoreModelSelection=file=>{assert.ok(runner.models().some(m=>m.key===file.id),'the copy must be listed before selection/Run');runner.selectModel(file.id);};
 core.takeFile(sourceFile);setJobs([]);n.resetNativeDocumentResults(sourceFile,jobs(),scope());n.rememberNativeDocument(response(sourceFile));
 setJobs([job('source-1','done',sourceFile.id)]);nativeReply=async()=>response(savedCopy);
 assert.equal(core.resultsPending(),true);assert.equal(await io.saveAs(),true);assert.equal(core.file().id,savedCopy.id);assert.equal(core.dirty(),false);
 assert.equal(refreshCount,1,'Save As awaits the refreshed workspace list');
 assert.equal(runner.currentModel().key,savedCopy.id);assert.equal(runner.values.length,'10','copy parameter defaults are ready immediately');
 const submissions=[];api.submit=async payload=>{submissions.push(payload);return{id:'immediate-copy-run',model:payload.model,status:'queued'};};
 api.runs=async()=>[];
 globalThis.EventSource=class {addEventListener(){}close(){}};
 assert.equal((await runner.startRun()).model,savedCopy.id);
 assert.equal(submissions.length,1);assert.equal(submissions[0].model,savedCopy.id);assert.equal(submissions[0].params.length,10);
 runner.detach();runner.selectModel('missing-copy');runner.setSubmitErrorDetail('obsolete detail');
 assert.equal(await runner.startRun(),null);assert.match(runner.submitError(),/model is unavailable/i);assert.equal(runner.submitErrorDetail(),'');assert.equal(submissions.length,1);
 refreshModels=async()=>{};restoreModelSelection=()=>{};
 // A source completion after the copy request starts must retain the source session.
 core.takeFile(sourceFile);setJobs([]);n.resetNativeDocumentResults(sourceFile,jobs(),scope());
 setJobs([job('source-2','done',sourceFile.id)]);
 const copyWait=deferred();nativeReply=()=>copyWait.promise;const copying=io.saveAs();await tick();
 const sourceHash=core.file().hash;
 setJobs([...jobs(),job('source-3','done',sourceFile.id)]);copyWait.resolve(response(savedCopy));
 assert.equal(await copying,false,'late source result must prevent switching to the saved copy');
 assert.equal(core.file().id,sourceFile.id);assert.notEqual(core.file().hash,sourceHash);
 assert.equal(core.resultsPending(),true);assert.equal(core.persistencePending(),true);
 assert.match(core.message().text,/copy saved/i);

 // A cancelled picker preserves real work, but never creates it on a clean document.
 for(const change of ['clean','dirty','pending','results','late-edit','late-result','undo-during-picker']) {
  const current={...f,id:`cancel-${change}`,document_id:`uuid-${change}`};core.takeFile(current);setJobs([]);
  n.resetNativeDocumentResults(current,jobs(),scope());n.rememberNativeDocument(response(current));
  if(change==='dirty'||change==='undo-during-picker')core.edit(d=>d.model.description='Saved only to workspace');
  if(change==='pending')core.setPersistencePending(true);
  if(change==='results')setJobs([job('cancel-result','done',current.id)]);
  const cancelWait=deferred();nativeReply=()=>cancelWait.promise;const cancel=io.saveAs();await tick();
  if(change==='late-edit')core.edit(d=>d.model.description='Later draft');
  if(change==='late-result')setJobs([job('later-result','done',current.id)]);
  if(change==='undo-during-picker')core.undo();
  cancelWait.resolve({status:'canceled'});assert.equal(await cancel,false);
  assert.equal(core.file().id,current.id);assert.match(core.file().hash,/^workspace-/);
  assert.equal(core.file().design,current.design,'cancellation keeps the external saved baseline');
  assert.equal(core.dirty(),change!=='clean',change);
  assert.equal(core.persistencePending(),['dirty','pending','undo-during-picker'].includes(change),change);
  assert.match(core.message().text,change==='clean'?/^File save cancelled\.$/:/changes remain/);
  if(change==='dirty'){core.undo();assert.equal(core.dirty(),true,'Undo cannot conceal a workspace write missing from the external document');}
 }
 // The newly added catalog await cannot take over another document or newer draft.
 for(const change of ['edit','replace','results']) {
  const current={...f,id:`refresh-${change}`,document_id:`refresh-uuid-${change}`};core.takeFile(current);setJobs([]);
  n.resetNativeDocumentResults(current,jobs(),scope());n.rememberNativeDocument(response(current));
  const refreshWait=deferred();let refreshing=false;refreshModels=()=>{refreshing=true;return refreshWait.promise;};nativeReply=async()=>response(savedCopy);
  let selections=0;restoreModelSelection=()=>selections++;
  const copy=io.saveAs();await tick();assert.equal(refreshing,true);
  if(change==='edit')core.edit(d=>d.model.description='Newer draft');
  if(change==='replace')core.takeFile({...f,id:'replacement-owner',hash:'replacement-hash',backup_scope:'workspace-B'});
  if(change==='results')setJobs([job('during-refresh','done',current.id)]);
  refreshWait.resolve();assert.equal(await copy,false);assert.equal(selections,0);
  assert.equal(core.file().id,change==='replace'?'replacement-owner':current.id);
  assert.equal(core.dirty(),change!=='replace');
  if(change==='replace')assert.equal(core.file().hash,'replacement-hash');
 }
 refreshModels=async()=>{};restoreModelSelection=()=>{};

 // Even a previously clean truncated journal needs a conservative reminder after reload.
 const oldLarge={...f,id:'clean-large-history'};
 const complete=Array.from({length:1100},(_,i)=>job('saved-'+i,'done',oldLarge.id));
 n.resetNativeDocumentResults(oldLarge,complete,scope());n.rememberNativeDocument(response(oldLarge));nativeReply=async()=>response(oldLarge);
 await n.persistNativeDocument(oldLarge,oldLarge.hash,false);assert.equal(n.nativeResultsPendingToken(oldLarge),'[]');
 const largeReload=(await import(url+'#clean-overflow-reload')).native;
 const withNew=[...complete,job('finished-while-reloading','done',oldLarge.id)];
 largeReload.observeNativeDocumentResults(oldLarge,withNew,scope());
 assert.notEqual(largeReload.nativeResultsPendingToken(oldLarge),'[]','first poll after a capped clean reload must not silently acknowledge a new completion');
 const pendingLarge=largeReload.nativeResultsPendingToken(oldLarge);largeReload.observeNativeDocumentResults(oldLarge,withNew,scope());assert.equal(largeReload.nativeResultsPendingToken(oldLarge),pendingLarge);
 largeReload.rememberNativeDocument(response(oldLarge));await largeReload.persistNativeDocument(oldLarge,oldLarge.hash,false);
 largeReload.observeNativeDocumentResults(oldLarge,withNew,scope());assert.equal(largeReload.nativeResultsPendingToken(oldLarge),'[]');

 // Only explicitly source-bound driven Elmer completions create portable pending results.
 const elmerFile={...f,id:'elmer-source',document_id:'elmer-source-uuid'};
 core.takeFile(elmerFile);setJobs([]);n.resetNativeDocumentResults(elmerFile,jobs(),scope());n.rememberNativeDocument(response(elmerFile));
 const elmer={...job('elmer-1','done',elmerFile.id),kind:'research',research:{backend:'elmer',settings:{analysis:'driven'}}};
 setJobs([elmer]);assert.equal(core.resultsPending(),false,'source-less research is workspace-only');
 const elmerSource={model_id:elmerFile.id,workspace_hash:'a'.repeat(64),backup_scope:elmerFile.backup_scope,design_sha256:'b'.repeat(64)};
 setJobs([{...elmer,research:{...elmer.research,source:{...elmerSource,backup_scope:'other-workspace'}}}]);assert.equal(core.resultsPending(),false);
 setJobs([{...elmer,research:{...elmer.research,source:elmerSource}}]);assert.equal(core.resultsPending(),true);
 nativeReply=async()=>response(elmerFile);assert.equal(await io.save(),true);assert.equal(core.dirty(),false);
 setJobs(structuredClone(jobs()));assert.equal(core.dirty(),false,'acknowledged Elmer completion stays clean on repeated polls');

 // A bounded overflow journal restores one pending marker, then settles after one save.
 const large={...f,id:'large-history'};
 const many=Array.from({length:1100},(_,i)=>job(`many-${i}`,'done',large.id));
 n.resetNativeDocumentResults(large,[],scope());n.observeNativeDocumentResults(large,many,scope());
 const capped=(await import(url+'#overflow')).native;capped.observeNativeDocumentResults(large,many,scope());
 const overflowToken=capped.nativeResultsPendingToken(large);assert.notEqual(overflowToken,'[]');
 capped.observeNativeDocumentResults(large,many,scope());assert.equal(capped.nativeResultsPendingToken(large),overflowToken);
 capped.rememberNativeDocument(response(large));nativeReply=async()=>response(large);
 await capped.persistNativeDocument(large,large.hash,false);
 capped.observeNativeDocumentResults(large,many,scope());assert.equal(capped.nativeResultsPendingToken(large),'[]','overflow settles after one acknowledged save');
 for(let i=0;i<30;i++)capped.resetNativeDocumentResults({...f,id:`bounded-${i}`},[],scope());
 assert.ok(JSON.parse(storage.getItem('fairbeam.document-results/1')).length<=24);
 assert.ok(!storage.getItem('fairbeam.document-results/1').includes('/Antenna.fb'),'journal contains no filesystem path or grant');
 delete globalThis.window;n.observeNativeDocumentResults(firstFile,[newest,job('browser-only','done',firstFile.id)],scope());
 assert.equal(n.nativeResultsPendingToken(firstFile),'[]','browser-only saves never get trapped by native result guards');
 console.log('Native save: clean Save, scoped completion baselines, stable pending close guards, in-flight completion, warnings, cancel/failure, release revision, reload and bounded journals passed');
} finally {
 disposeEffects();io.dispose();core.dispose();
 for(const [key,descriptor] of previous){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}
}
