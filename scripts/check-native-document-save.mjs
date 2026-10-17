// Real session/native adapters; controlled history and native replies, no solver or desktop dialog.
import assert from 'node:assert/strict';
import {build} from 'vite';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url)).replaceAll('\\','/');
const built=await build({root,configFile:false,logLevel:'silent',resolve:{conditions:['browser']},plugins:[{
 name:'native-save-entry',resolveId(id){if(id.endsWith('native-save-entry'))return '\0native-save-entry';},
 load(id){if(id==='\0native-save-entry')return [
  `export {createDesignerSession} from ${JSON.stringify(root+'src/designer/sessionCore.ts')};`,
  `export {createDesignerIO} from ${JSON.stringify(root+'src/designer/sessionIO.ts')};`,
  `export * as native from ${JSON.stringify(root+'src/lib/nativeDocuments.ts')};`,
  `export {createRoot,createEffect,createSignal} from 'solid-js';`,
 ].join('\n');},
}],build:{write:false,minify:false,lib:{entry:'native-save-entry',formats:['es']}}});
const previous=new Map(['localStorage','sessionStorage','window'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
const memory=new Map(),storage={getItem:key=>memory.get(key)??null,setItem:(key,value)=>memory.set(key,String(value)),removeItem:key=>memory.delete(key)};
globalThis.localStorage={getItem:()=>null,setItem(){},removeItem(){}};globalThis.sessionStorage=storage;
const code=(Array.isArray(built)?built[0]:built).output.find(x=>x.type==='chunk').code;
const url='data:text/javascript;base64,'+Buffer.from(code).toString('base64');
const {createDesignerSession,createDesignerIO,native:n,createRoot,createEffect,createSignal}=await import(url);
const tick=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const deferred=()=>{let resolve;const promise=new Promise(yes=>resolve=yes);return{promise,resolve};};
const design={schema:'fairbeam.design/1',model:{id:'embedded-model',name:'Antenna'},params:[],materials:[],parts:[],ports:[],resistors:[],simulation:{},mesh:{}};
const f={id:'owned',file:'owned.design.json',hash:'base',readonly:false,design,backup_scope:'workspace-A',document_id:'uuid-A'};
const response=(file=f)=>({status:'saved',handle:'grant-A',path:'/Antenna.fb',file,document:{id:'uuid-A',path:'/Antenna.fb',revision:'portable-new',results_directory:'Antenna.results'},results:[],warnings:[]});
const job=(id,status='done',model=f.id)=>({id,model,status,bundle:status==='done'?`${id}.json`:null,finished:status==='done'?Number(id.replace(/\D/g,''))||1:null});
const old=job('old'),running=job('run-1','running');
let nativeReply=()=>Promise.resolve(response()),readDesign=async()=>f,nativeCalls=0,writes=0;
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
 rememberLastDesign(){},readBackup(){return null;},clearBackup(){},stopPreview(){},forgetPreviewFailure(){},resetParamAsks(){},schedulePreview(){},applyModelEntry(){},restoreModelSelection(){},
 persistSavedDesign:n.persistNativeDocument,
});
try {
 n.resetNativeDocumentResults(f,jobs(),scope());n.rememberNativeDocument(response());core.takeFile(f);
 assert.equal(core.dirty(),false,'existing history is the explicit Open baseline');
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
 assert.equal(await io.save(),false);assert.equal(core.resultsPending(),true);assert.equal(core.persistencePending(),true);
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
 core.takeFile(sourceFile);setJobs([]);n.resetNativeDocumentResults(sourceFile,jobs(),scope());n.rememberNativeDocument(response(sourceFile));
 setJobs([job('source-1','done',sourceFile.id)]);nativeReply=async()=>response(savedCopy);
 assert.equal(core.resultsPending(),true);assert.equal(await io.saveAs(),true);assert.equal(core.file().id,savedCopy.id);assert.equal(core.dirty(),false);
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
