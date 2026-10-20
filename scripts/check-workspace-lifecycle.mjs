// Actual default store with deferred server and confirmation transports; no production test exports.
import assert from 'node:assert/strict';
import {build} from 'vite';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url)).replaceAll('\\','/');
const built=await build({root,configFile:false,logLevel:'silent',resolve:{conditions:['browser']},plugins:[{
 name:'workspace-lifecycle-transports',enforce:'pre',
 resolveId(id){if(id.endsWith('workspace-lifecycle-entry'))return '\0workspace-lifecycle-entry';if(id.endsWith('/ConfirmDialog'))return '\0deferred-discard';},
 load(id){if(id==='\0deferred-discard')return 'export const confirmDraftDiscard=message=>globalThis.testDiscard(message);';if(id==='\0workspace-lifecycle-entry')return ['designer/store','runner/store','workspace','designer/draftBackup','lib/nativeDocuments'].map((p,i)=>`export * as m${i} from ${JSON.stringify(root+'src/'+p+'.ts')};`).join('\n');},
}],build:{write:false,minify:false,lib:{entry:'workspace-lifecycle-entry',formats:['es']}}});
const local=new Map(),session=new Map();
const storage=m=>({getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,String(v)),removeItem:k=>m.delete(k),get length(){return m.size;},key:i=>[...m.keys()][i]??null});
globalThis.localStorage=storage(local);globalThis.sessionStorage=storage(session);
globalThis.window={dispatchEvent:()=>true,addEventListener(){},removeEventListener(){}};globalThis.requestAnimationFrame=()=>0;globalThis.cancelAnimationFrame=()=>{};
const defer=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
const scopeA='models-v1:'+'a'.repeat(64),scopeB='models-v1:'+'b'.repeat(64);let scope=scopeA,del,discard;
globalThis.testDiscard=()=>{discard=defer();return discard.promise;};
const design=name=>({schema:'fairbeam.design/1',model:{id:'same',name},params:[],materials:[{name:'copper',kind:'metal'}],parts:[{name:'brick',material:'copper',primitives:[{kind:'box',start:[0,0,0],stop:[1,1,1]}]}],ports:[],resistors:[],simulation:{},mesh:{}});
const json=body=>({ok:true,status:200,text:async()=>JSON.stringify(body)});let gets=0,deleteScope;
let nativeSource=false,pendingCreate,previewCalls=0;const modelEntries=[{key:'same',kind:'design',file:'same.design.json',model:{name:'same'}}];
globalThis.fetch=async(url,init={})=>{
 const path=String(url).replace(/^\/api/,'');
 if(path==='/preview'){previewCalls++;return {ok:false,status:422,text:async()=>JSON.stringify({error:'fixture preview',checks:[]})};}
 if(path==='/designs')return pendingCreate.promise;
 if(path==='/designs/same/delete'){deleteScope=JSON.parse(init.body).backup_scope;return del.promise;}
 if(path==='/designs/same'){gets++;return json({id:'same',file:'same.design.json',hash:'same-hash',...(nativeSource?{document_id:'native-source'}:{}),readonly:false,backup_scope:scope,design:design(scope===scopeA?'A':'B')});}
 if(path==='/models')return json({models:modelEntries});
 return {ok:false,status:404,text:async()=>JSON.stringify({error:'unmocked '+path})};
};
const code=(Array.isArray(built)?built[0]:built).output[0].code;
const {m0:store,m1:runner,m2:workspace,m3:backup,m4:native}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
// Unknown workspace cannot consume legacy reopen intent or initiate an unowned GET.
session.set('fairbeam:lastDesign','same');local.set('fairbeam:draft:same',JSON.stringify({at:Date.now(),base:'same-hash',design:design('legacy')}));
const legacy=local.get('fairbeam:draft:same');assert.equal(runner.health(),null);await store.reopenLastDesign();assert.equal(gets,0);assert.equal(store.file(),null);assert.equal(session.get('fairbeam:lastDesign'),'same');assert.equal(local.get('fairbeam:draft:same'),legacy);
// Delete A resolves only after B with the same ID/hash has a new draft, history and backup.
await store.openDesign('same');del=defer();const deleting=store.deleteDesign('same');assert.equal(deleteScope,scopeA);
scope=scopeB;await store.openDesign('same');workspace.setAppMode('design');runner.selectModel('same');store.edit(d=>d.model.description='B edited');store.setSelection({type:'part',i:0});await sleep(1100);
const bMark=store.historyMark(),bSelection=store.selection(),bBackup=backup.readBackup('same','same-hash',scopeB);assert.equal(bBackup.design.model.description,'B edited');
del.resolve(json({id:'same',file:'same.design.json',moved_to:'history/A.json',backup_scope:scopeA}));assert.equal(await deleting,'history/A.json');
assert.equal(store.file().backup_scope,scopeB);assert.equal(store.draft.model.name,'B');assert.equal(store.dirty(),true);assert.deepEqual(store.historyMark(),bMark);assert.deepEqual(store.selection(),bSelection);assert.deepEqual(backup.readBackup('same','same-hash',scopeB),bBackup);assert.equal(workspace.appMode(),'design');assert.equal(runner.modelKey(),'same');
// A delayed discard result is rejected after switching and editing B; B's timer still writes.
store.releaseDraft();scope=scopeA;await store.openDesign('same');store.edit(d=>d.model.description='A pending discard');const confirming=store.confirmDiscard();assert(discard,'actual store called deferred confirmation transport');
scope=scopeB;await store.openDesign('same');store.edit(d=>d.model.description='B timer survives');const mark=store.historyMark();discard.resolve(true);assert.equal(await confirming,false);assert.deepEqual(store.historyMark(),mark);assert.equal(store.dirty(),true);
await sleep(1100);assert.equal(backup.readBackup('same','same-hash',scopeB).design.model.description,'B timer survives');assert.equal(local.get('fairbeam:draft:same'),legacy);
const currentDiscard=store.confirmDiscard();discard.resolve(true);assert.equal(await currentDiscard,true,'current-document discard still succeeds');assert.equal(backup.readBackup('same','same-hash',scopeB),null);
store.closeDesign();assert.equal(local.get('fairbeam:draft:same'),legacy);
console.log('Workspace lifecycle: deferred delete/discard retain B same-ID history, selection and backup; unknown-scope startup preserves legacy intent');

// A newly created model must not replace a native source that gains results or edits meanwhile.
nativeSource=true;globalThis.window.__TAURI_INTERNALS__={invoke:async()=>null};
await store.openDesign('same');workspace.setAppMode('design');runner.setJobsScope(scopeB);runner.setJobs([]);
const sourceBinding={handle:'source-grant',path:'E:/source.fb',file:{id:'same',backup_scope:scopeB},document:{id:'native-source',path:'E:/source.fb',revision:'source-revision',results_directory:'source.results'},results:[],warnings:[]};
native.rememberNativeDocument(sourceBinding);runner.setServerState('online');
assert.equal(store.dirty(),false);
const createdReply=id=>({id,file:id+'.design.json',design:design(id),hash:'new-'+id,readonly:false,backup_scope:scopeB,validation:{valid:true,checks:[]}});
const publishCreated=id=>{modelEntries.push({key:id,kind:'design',file:id+'.design.json',model:{name:id}});pendingCreate.resolve(json(createdReply(id)));};
pendingCreate=defer();const creating=store.createDesign({id:'newdesign',design:design('New')});await sleep(0);
const previewsBeforeCreate=previewCalls;
runner.setJobs([{id:'late-result',model:'same',kind:'run',status:'done',bundle:'late-result.json',finished:1}]);
assert.equal(store.resultsPending(),true);publishCreated('newdesign');await creating;
assert.equal(store.file()?.id,'same','completion during Create retains the native source');
assert.equal(store.resultsPending(),true);assert.equal(store.dirty(),true);assert.equal(runner.modelKey(),'same');
assert.equal(native.nativeDocumentFor(store.file()),sourceBinding,'the retained source keeps its native document grant');
await sleep(20);assert.ok(previewCalls>previewsBeforeCreate,'retained creation resumes the source preview after loading');
assert.ok(runner.models().some(model=>model.key==='newdesign'),'the completed creation stays visible in the model list');
assert.match(store.message().text,/project created.*newer changes/i);
store.releaseDraft();pendingCreate=defer();const editing=store.createDesign({id:'newedit',design:design('Edit')});await sleep(0);
store.edit(d=>d.model.description='edit after creation began');const editMark=store.historyMark();publishCreated('newedit');await editing;
assert.equal(store.file()?.id,'same');assert.deepEqual(store.historyMark(),editMark);assert.equal(store.draft.model.description,'edit after creation began');
assert.equal(store.defaultDesignerIO.isReleased(),false,"the earlier Don't save decision is revoked by the new edit");
assert.equal(native.nativeDocumentFor(store.file()),sourceBinding);
await sleep(1100);assert.equal(backup.readBackup('same','same-hash',scopeB).design.model.description,'edit after creation began','the newer source draft backup survives creation');
store.releaseDraft();pendingCreate=defer();const ordinary=store.createDesign({id:'normal',design:design('Normal')});await sleep(0);publishCreated('normal');await ordinary;
assert.equal(store.file()?.id,'normal','an unchanged released source still permits normal creation');assert.equal(store.dirty(),false);
store.closeDesign();
console.log('Native Create: late result and draft keep source, created models remain listed, unchanged creation switches normally');
