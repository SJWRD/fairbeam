// Real lifecycle modules bundled in memory; dependency state and network are isolated.
// No DOM/server/native solver. Deferred responses exercise ownership, not elapsed timers.
import assert from 'node:assert/strict';
import {build} from 'vite';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {LEGACY_SCHEMAS} from '../src/lib/legacy.ts';
const legacyProjectSchema=Object.entries(LEGACY_SCHEMAS).find(([,current])=>current==='fairbeam.project/1')?.[0];
assert.ok(legacyProjectSchema && legacyProjectSchema!=='fairbeam.project/1','canonical legacy project schema is available');
const root=fileURLToPath(new URL('../',import.meta.url)).replaceAll('\\','/');
const files=['src/runner/designRun.ts','src/designer/runResults.ts'].map(f=>root+f);
const imports=new Map();
for(const file of files){
 for(const match of readFileSync(file,'utf8').matchAll(/import\s+(type\s+)?\{([^}]+)\}\s+from\s+["']([^"']+)["']/g)){
  if(match[1])continue;
  const id=match[3];
  if(id==='solid-js'||['../workspace','../lib/validate','../lib/resultIdentity','./resultFollow'].includes(id))continue;
  const key=path.posix.resolve(path.posix.dirname(file),id).replace(/\.ts$/,'');
  if(key.endsWith('/src/runner/designRun')||key.endsWith('/src/designer/runResults'))continue;
  const names=imports.get(key)??new Set();
  for(const spec of match[2].split(',').map(s=>s.trim()))if(!spec.startsWith('type '))names.add(spec.split(/\s+as\s+/)[0]);
  imports.set(key,names);
 }
}
const control='\0result-lifecycle-control';
const controlSource=`import {createSignal} from 'solid-js';
export const [file,setFile]=createSignal(null),[index,setIndex]=createSignal([]),[bundle,setBundle]=createSignal(null),[source,setSource]=createSignal(''),[resultFocus,setFocus]=createSignal(null);
export const ctl={indices:[],projects:[],clears:0,opened:0,loads:[]};
export const openCount=()=>ctl.opened;
export function clearProject(){ctl.clears++;ctl.opened++;setBundle(null);setSource('');}
export function openBundle(b,f){ctl.opened++;setBundle(b);setSource(f);}
export const loadIndex=()=>ctl.indices.shift().promise;
export const lastProject=()=>null;
export async function loadProject(file,current=()=>true){ctl.loads.push(file);const b=await ctl.projects.shift().promise;if(!current())return false;openBundle(b,file);return true;}
export const [jobs,setJobs]=createSignal([]);
export const models=()=>[],selection=()=>null,notice=()=>null,runOpen=()=>false,live={job:null};
export const designDockTab=()=> 'checks';
export const projectUrl=f=>f;
export const newestResults=x=>x,exampleEntries=x=>x;
export const runRows=(xs,model)=>xs.filter(x=>x.model===model&&x.simulated).map(x=>({...x,label:x.name,title:x.created}));
export const projectLabels=()=>new Map(),runLetters=()=>new Map(),indexQuality=()=>null;
export const runQuality=b=>b.name,runContent=b=>({name:b.name}),rawMetrics=b=>({name:b.name}),bundleMetrics=b=>({name:b.name}),indexMetrics=()=>null;
export const activeMainResult=()=>null,focusResult=setFocus,resultTarget=()=> 'keep';
export const serverActivity=()=>({other:null});
export const t=k=>k,fmt={fixed:String};
`;
const exposed=new Set([...controlSource.matchAll(/(?:export (?:const|function|async function)\s+)(\w+)/g)].map(m=>m[1]));
for(const n of ['file','setFile','index','setIndex','bundle','setBundle','source','setSource','resultFocus','setFocus','jobs','setJobs','models','selection','notice','runOpen','live','exampleEntries','runContent','rawMetrics','bundleMetrics','indexMetrics','runLetters','indexQuality','focusResult','resultTarget','fmt'])exposed.add(n);
const built=await build({root,configFile:false,logLevel:'silent',resolve:{conditions:['browser']},plugins:[{
 name:'result-lifecycle',enforce:'pre',resolveId(id,importer){
  if(id.endsWith('lifecycle-entry'))return '\0lifecycle-entry';if(id===control)return control;
  if(files.includes(importer?.replaceAll('\\','/'))){const key=path.posix.resolve(path.posix.dirname(importer.replaceAll('\\','/')),id).replace(/\.ts$/,'');if(imports.has(key))return '\0stub:'+key;}
 },load(id){
  if(id===control)return controlSource;
  if(id==='\0lifecycle-entry')return `export * as runs from ${JSON.stringify(files[1])};export * as design from ${JSON.stringify(files[0])};export * as state from ${JSON.stringify(control)};export * as workspace from ${JSON.stringify(root+'src/workspace.ts')};`;
  if(id.startsWith('\0stub:'))return [...imports.get(id.slice(6))].map(n=>exposed.has(n)?`export {${n}} from ${JSON.stringify(control)};`:`export const ${n}=()=>undefined;`).join('\n');
 } }],build:{write:false,minify:false,lib:{entry:'lifecycle-entry',formats:['es']}}});
const originalGlobals = new Map(['localStorage', 'fetch'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
try {
Object.defineProperty(globalThis, 'localStorage', {configurable:true, writable:true, value:{getItem:()=>null,setItem(){}}});
const code=(Array.isArray(built)?built[0]:built).output.find(x=>x.type==='chunk').code;
const {runs:r,design:d,state:s,workspace:w}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return{promise,resolve,reject};};
const tick=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const fixture=JSON.parse(readFileSync(root+'public/projects/patch-antenna.json','utf8'));
const b=(name,created)=>({...structuredClone(fixture),name,created});
const entry=x=>({file:'same.json',name:x.name,model:x.model.id,created:x.created,engine:'CPU',simulated:true,bands:[],cells:1});
let checks=0;
for(const mode of ['home','design','results']){
 const p=deferred();s.ctl.indices.push(p);s.setBundle(null);const before=s.ctl.clears;
 const task=d.openExamples();w.setAppMode(mode);s.openBundle(b('new selection','new'),'new.json');p.resolve([{file:'old.json',simulated:true}]);await task;
 assert.equal(s.bundle().name,'new selection');assert.equal(s.ctl.clears,before);checks++;
}
// Navigation away and back to Results must also revoke the old request.
{const p=deferred();s.ctl.indices.push(p);s.setBundle(null);const task=d.openExamples();w.setAppMode('home');w.setAppMode('results');p.resolve([]);const before=s.ctl.clears;await task;assert.equal(s.ctl.clears,before);checks++;}
// A newer examples invocation owns its index response; stale completion cannot clear it.
{const old=deferred(),fresh=deferred(),body=deferred();s.ctl.indices.push(old,fresh);s.ctl.projects.push(body);s.setBundle(null);const a=d.openExamples(),z=d.openExamples();fresh.resolve([{file:'fresh.json',simulated:true}]);await tick();body.resolve(b('fresh','fresh'));await z;old.resolve([{file:'old.json',simulated:true}]);await a;assert.equal(s.bundle().name,'fresh');checks++;}
// Ownership predicate remains active while the example's file body is pending.
{const idx=deferred(),body=deferred();s.ctl.indices.push(idx);s.ctl.projects.push(body);s.setBundle(null);const task=d.openExamples();idx.resolve([{file:'old.json',simulated:true}]);await tick();w.setAppMode('design');s.openBundle(b('new design','new'),'new.json');body.resolve(b('old result','old'));await task;assert.equal(s.bundle().name,'new design');checks++;}
const requests=[];Object.defineProperty(globalThis, 'fetch', {configurable:true, writable:true, value:url=>{const response=deferred();requests.push({url,...response});return response.promise;}});
const reply=(request,bundle)=>request.resolve({ok:true,json:async()=>bundle});
w.setAppMode('home');s.setFile({id:'model-file',design:{model:{id:fixture.model.id}}});await tick();
const old=b('old','2026-10-10T01:00:00Z'),fresh=b('fresh','2026-10-11T01:00:00Z');
s.setIndex([entry(old)]);d.setDesignResult({file:'same.json',bundle:old});r.ensureDesignRunBundles();assert.equal(r.designRunBundle('same.json').name,'old');
s.setIndex([entry(fresh)]);assert.equal(r.designRunBundle('same.json'),undefined,'obsolete cached metadata hidden immediately');r.ensureDesignRunBundles();const first=requests.at(-1);d.setDesignResult({file:'same.json',bundle:fresh});r.ensureDesignRunBundles();reply(first,fresh);await tick();assert.equal(r.designRunBundle('same.json').name,'fresh');assert.equal(r.runMetricsOf('same.json').name,'fresh');checks++;
// Old in-flight data cannot overwrite a newer generation, nor delete its shared request.
d.setDesignResult(null);s.setIndex([entry(old)]);const before=requests.length;const stale=r.loadRunBundle('same.json').then(()=>false,()=>true);const oldRequest=requests.at(-1);
s.setIndex([entry(fresh)]);const active=r.loadRunBundle('same.json');const freshRequest=requests.at(-1);reply(oldRequest,old);assert.equal(await stale,true);assert.equal(r.loadRunBundle('same.json'),active);assert.equal(requests.length,before+2);reply(freshRequest,fresh);assert.equal((await active).name,'fresh');checks++;
// A stale response from disk itself is refused even when the request's index stays current.
{const task=r.loadRunBundle('same.json').then(()=>false,()=>true);reply(requests.at(-1),old);assert.equal(await task,true);checks++;}
// Same-second CPU/GPU replacements still have distinct identity; backend labels match the index writer.
for(const [engine,os,log] of [['CUDA','Windows',['Create FDTD engine (GPU, backend: CUDA (test))']],['Metal','Darwin',[]],['GPU','Linux',[]]]){
 const gpu={...fresh,run:{...fresh.run,engine:'gpu',host:{...fresh.run.host,os},log_tail:log}};
 s.setIndex([{...entry(fresh),engine}]);
 const rejected=r.loadRunBundle('same.json').then(()=>false,()=>true);reply(requests.at(-1),fresh);assert.equal(await rejected,true,'old same-second CPU bytes rejected');
 const accepted=r.loadRunBundle('same.json');reply(requests.at(-1),gpu);assert.equal((await accepted).run.engine,'gpu');checks++;
}
// Tree reads can overlap generations; late old completion cannot restore old content/quality.
const treeFresh={...fresh,created:'2026-10-11T02:00:00Z'};s.setIndex([entry(old)]);const oldRead=r.readRunContent('same.json'),oldTree=requests.at(-1);s.setIndex([entry(treeFresh)]);const freshRead=r.readRunContent('same.json'),freshTree=requests.at(-1);assert.notEqual(oldTree,freshTree);reply(freshTree,treeFresh);await freshRead;reply(oldTree,old);await oldRead;assert.equal(r.runContentOf('same.json').name,'fresh');assert.equal(r.runQualityOf('same.json'),'fresh');checks++;
// The metadata cache also ignores a late old callback after a newer index generation is read.
{const middle=b('middle','2026-10-12T01:00:00Z'),latest=b('latest','2026-10-13T01:00:00Z');s.setIndex([entry(middle)]);r.ensureDesignRunBundles();const obsolete=requests.at(-1);s.setIndex([entry(latest)]);r.ensureDesignRunBundles();const current=requests.at(-1);reply(current,latest);await tick();reply(obsolete,middle);await tick();assert.equal(r.designRunBundle('same.json').name,'latest');checks++;}
// Design switch revokes cache and tree loads even when returning to the same file/stamp.
{d.setDesignResult(null);s.setIndex([entry(fresh)]);const pending=r.loadRunBundle('same.json').then(()=>false,()=>true),req=requests.at(-1);s.setIndex([entry(old)]);const tree=r.readRunContent('same.json'),treeRequest=requests.at(-1);s.setFile({id:'another-file',design:{model:{id:'another'}}});await tick();reply(req,fresh);reply(treeRequest,old);await tree;assert.equal(await pending,true);assert.equal(r.designRunBundle('same.json'),undefined);assert.equal(r.runContentOf('same.json'),null);checks++;}
// Older indexes omit timestamps/engine. Bundle validation repairs an absent date to an empty
// string, while the Python index writer emits null; neither should make a valid old run unreadable.
for (const indexDate of [undefined,null,'']) {
 for (const bundleDate of [undefined,null,'',fresh.created]) {
  const legacy=b('legacy',bundleDate),row={...entry(legacy),created:indexDate};
 legacy.schema=legacyProjectSchema;delete row.engine;
  s.setIndex([row]);
  const task=r.loadRunBundle('same.json');reply(requests.at(-1),legacy);
  assert.equal((await task).name,'legacy','unknown index timestamp accepts valid legacy bundle');checks++;
 }
}
// Known timestamps remain strict, even when the bundle has no timestamp of its own.
{
 const row=entry(fresh);delete row.engine;s.setIndex([row]);
 const task=r.loadRunBundle('same.json');reply(requests.at(-1),fresh);
 assert.equal((await task).name,'fresh','missing engine does not reject a matching known date');checks++;
}
for (const bundleDate of [undefined,null,'',old.created]) {
 s.setIndex([entry(fresh)]);
 const task=r.loadRunBundle('same.json').then(()=>false,()=>true);reply(requests.at(-1),b('undated or stale',bundleDate));
 assert.equal(await task,true,'known timestamp rejects absent or mismatched bundle timestamp');checks++;
}
// Lack of an index timestamp never bypasses model or available engine identity.
for (const changed of [{model:{...fresh.model,id:'foreign-model'}},{run:{...fresh.run,engine:'gpu'}}]) {
 s.setIndex([{...entry(fresh),created:null}]);
 const task=r.loadRunBundle('same.json').then(()=>false,()=>true);reply(requests.at(-1),{...fresh,...changed});
 assert.equal(await task,true,'legacy timestamp still enforces model and engine');checks++;
}
// An index gaining a timestamp revokes its old in-flight response and makes a separate request.
{
 s.setIndex([{...entry(fresh),created:null}]);
 const oldTask=r.loadRunBundle('same.json').then(()=>false,()=>true),oldRequest=requests.at(-1);
 s.setIndex([entry(fresh)]);
 const newTask=r.loadRunBundle('same.json'),newRequest=requests.at(-1);
 assert.notEqual(oldRequest,newRequest);reply(oldRequest,fresh);assert.equal(await oldTask,true);
 assert.equal(r.loadRunBundle('same.json'),newTask,'obsolete completion cannot delete current request');
 reply(newRequest,fresh);assert.equal((await newTask).name,'fresh');checks++;
}

// Selected plots/viewport must not retain A under a refreshed B/C label or headline.
s.setFile({id:'active-generation-review',design:{model:{id:fixture.model.id}}});await tick();
w.setAppMode('design');await tick();
const genA=b('Active A','2026-10-20T01:00:00Z'),genB=b('Active B','2026-10-20T02:00:00Z'),genC=b('Active C','2026-10-20T03:00:00Z');
for (const [value,generation] of [[0.1,genA],[0.2,genB],[0.3,genC]]) generation.results.ports['1'].s11_re[0]=value;
s.setIndex([entry(genA)]);d.setDesignResult({file:'same.json',bundle:genA});s.setFocus({file:'same.json',view:'sparams'});await tick();
assert.equal(s.bundle().name,'Active A');
const modelBefore=structuredClone(s.file());
let offset=requests.length;s.setIndex([entry(genB)]);await tick();const pendingB=requests.slice(offset);
assert.ok(pendingB.length);assert.equal(d.designResult(),null,'old trace hidden immediately');assert.equal(s.bundle(),null,'old viewport cleared');
assert.equal(s.resultFocus().view,'sparams');assert.equal(d.designResultLoading(),'same.json');
s.setFocus({file:'same.json',view:'impedance'});await tick();assert.equal(requests.length,offset+pendingB.length,'same-file view switch shares pending refresh');
offset=requests.length;s.setIndex([entry(genC)]);await tick();const pendingC=requests.slice(offset);assert.ok(pendingC.length);
for(const request of pendingC)reply(request,genC);await tick();
assert.equal(d.designResult().bundle.results.ports['1'].s11_re[0],0.3);assert.equal(s.bundle().name,'Active C');assert.equal(s.resultFocus().view,'impedance');
for(const request of pendingB)reply(request,genB);await tick();
assert.equal(d.designResult().bundle.name,'Active C');assert.deepEqual(s.file(),modelBefore);checks++;
// A failed replacement stays selected with an explicit Retry; it never falls back to A/C.
const genD=b('Active D','2026-10-20T04:00:00Z');
s.setIndex([entry(genD)]);await tick();reply(requests.at(-1),{invalid:true});await tick();
assert.equal(d.designResult(),null);assert.equal(s.bundle(),null);assert.equal(s.resultFocus().file,'same.json');
assert.ok(d.designResultError());assert.equal(d.failedResultLoad().file,'same.json');
d.retryResultLoad();reply(requests.at(-1),genD);await tick();
assert.equal(d.designResult().bundle.name,'Active D');assert.equal(d.designResultError(),null);assert.equal(s.resultFocus().view,'impedance');checks++;
// A finishing job owns the index refresh: auto-reload must not steal its newer job ID.
const genJob=b('Finished job','2026-10-20T05:00:00Z'),jobIndex=deferred();s.ctl.indices.push(jobIndex);
const finishing=d.loadDesignResult('same.json','new-job-id',true);
s.setIndex([entry(genJob)]);jobIndex.resolve([entry(genJob)]);await tick();
reply(requests.at(-1),genJob);assert.equal(await finishing,true);assert.equal(d.designResult().jobId,'new-job-id');checks++;
s.setIndex([entry(genD)]);await tick();reply(requests.at(-1),genD);await tick();
// Compared traces use the same generation as their labels, including while a newer body is held.
const cmpA=b('Compare A','2026-10-21T01:00:00Z'),cmpB=b('Compare B','2026-10-21T02:00:00Z');
const cmpEntry=value=>({...entry(value),file:'compare.json'});
s.setIndex([entry(genD),cmpEntry(cmpA)]);s.setFocus({file:'same.json',view:'sparams',compare:['compare.json']});await tick();
reply(requests.at(-1),cmpA);await tick();assert.equal(r.comparedRuns()[0].bundle.name,'Compare A');
s.setIndex([entry(genD),cmpEntry(cmpB)]);await tick();
assert.deepEqual(r.comparedRuns(),[]);assert.equal(r.comparisonReady(),false);assert.equal(r.comparedLoadState()['compare.json'],'loading');
reply(requests.at(-1),cmpB);await tick();assert.equal(r.comparedRuns()[0].bundle.name,'Compare B');assert.equal(r.comparisonReady(),true);checks++;
// Even the first selected response must be revoked when its index changes before it arrives.
d.clearDesignResult();s.setIndex([entry(genA)]);s.setFocus({file:'same.json',view:'sparams'});await tick();const initial=requests.at(-1);
s.setIndex([entry(genB)]);await tick();const replacement=requests.at(-1);assert.notEqual(initial,replacement);
reply(initial,genA);await tick();assert.equal(d.designResult(),null);
reply(replacement,genB);await tick();assert.equal(d.designResult().bundle.name,'Active B');checks++;

// Empty or unrelated job polling never owns/cancels an explicitly selected external result.
for (const newJobs of [[],[{id:'unrelated',model:'another-design',status:'done',bundle:'foreign.json',finished:10}]]) {
 const task=d.loadDesignResult('same.json'),request=requests.at(-1);s.setJobs(newJobs);await tick();
 reply(request,genB);assert.equal(await task,true);assert.equal(d.designResultLoading(),null);assert.equal(d.designResult().bundle.name,'Active B');checks++;
}
// Picking another file hides the previously displayed A while B is pending or failed.
const other=b('Other selected result','2026-10-22T01:00:00Z');
s.setIndex([entry(genB),{...entry(other),file:'other.json'}]);s.setFocus({file:'other.json',view:'sparams'});await tick();
assert.equal(d.designResult(),null);assert.equal(s.bundle(),null);assert.equal(d.pendingDesignResultFile(),'other.json');
reply(requests.at(-1),{invalid:true});await tick();assert.equal(d.designResult(),null);assert.equal(d.pendingDesignResultFile(),'other.json');
d.retryResultLoad();reply(requests.at(-1),other);await tick();assert.equal(d.designResult().bundle.name,'Other selected result');checks++;
// Background table/tree reads share one bounded queue; foreground selection is promoted.
w.setAppMode('home');s.setFocus(null);s.setFile({id:'bounded-reads',design:{model:{id:fixture.model.id}}});await tick();
const many=Array.from({length:12},(_,i)=>({...entry(fresh),file:`point-${i}.json`}));s.setIndex(many);
let start=requests.length;r.ensureDesignRunBundles();assert.equal(requests.length-start,4,'only four concurrent full bundle reads');
const sharedTree=r.readRunContent('point-0.json');assert.equal(requests.length-start,4,'tree joins metadata request');
const foreground=r.loadRunBundle('point-11.json');assert.equal(requests.length-start,4,'foreground shares queued request');
reply(requests[start],fresh);await tick();assert.equal(requests.at(-1).url,'point-11.json','explicit selection promoted ahead of background queue');
const answered=new Set([requests[start]]);
for(let pass=0;pass<12;pass++){for(const request of requests.slice(start))if(!answered.has(request)){answered.add(request);reply(request,fresh);}await tick();}
await sharedTree;await foreground;assert.equal(requests.length-start,12,'one network read per run');assert.equal(many.filter(e=>r.designRunBundle(e.file)).length,12);checks++;
// A failed metadata read is visible, does not silently loop, and can explicitly be retried.
const failedRow={...entry(fresh),file:'failed-point.json'};s.setIndex([failedRow]);start=requests.length;r.ensureDesignRunBundles();
requests.at(-1).resolve({ok:false,status:503});await tick();assert.equal(r.designRunBundleLoadState(failedRow.file),'error');
r.ensureDesignRunBundles();assert.equal(requests.length,start+1,'failed reads wait for intentional retry');
r.retryDesignRunBundles();assert.equal(r.designRunBundleLoadState(failedRow.file),'loading');reply(requests.at(-1),fresh);await tick();
assert.equal(r.designRunBundleLoadState(failedRow.file),undefined);assert.equal(r.designRunBundle(failedRow.file).name,'fresh');checks++;
// Queued reads from a closed design are rejected without starting additional HTTP requests.
s.setFile({id:'queued-old',design:{model:{id:fixture.model.id}}});await tick();s.setIndex(many);start=requests.length;r.ensureDesignRunBundles();assert.equal(requests.length-start,4);
s.setFile({id:'queued-new',design:{model:{id:'other-model'}}});await tick();for(const request of requests.slice(start))reply(request,fresh);await tick();
assert.equal(requests.length-start,4);assert.equal(r.designRunBundle('point-0.json'),undefined);checks++;
// Four held old-design reads cannot starve the next design. Late old completions do not
// release the four new slots twice or restore old cache data.
s.setFile({id:'abort-old',design:{model:{id:fixture.model.id}}});await tick();s.setIndex(many);start=requests.length;r.ensureDesignRunBundles();
const heldOld=requests.slice(start);assert.equal(heldOld.length,4);
s.setFile({id:'abort-new',design:{model:{id:fixture.model.id}}});await tick();r.ensureDesignRunBundles();assert.equal(requests.length-start,8,'new design starts before any old response');
for(const request of heldOld)reply(request,fresh);await tick();assert.equal(requests.length-start,8,'late old responses cannot double-release slots');
const newAnswered=new Set(heldOld);for(let pass=0;pass<12;pass++){for(const request of requests.slice(start))if(!newAnswered.has(request)){newAnswered.add(request);reply(request,fresh);}await tick();}checks++;
// A transport ignoring abort still frees one slot on its full-body deadline. Selecting the
// main result uses its independent loader and is not blocked behind background metadata.
const realSetTimeout=globalThis.setTimeout,realClearTimeout=globalThis.clearTimeout,deadlines=[];
try {
 globalThis.setTimeout=(fn,delay,...args)=>{if(delay!==30000)return realSetTimeout(fn,delay,...args);const timer={fn};deadlines.push(timer);return timer;};
 globalThis.clearTimeout=timer=>{if(!deadlines.includes(timer))realClearTimeout(timer);};
 s.setFile({id:'timeout-design',design:{model:{id:fixture.model.id}}});await tick();s.setIndex(many);start=requests.length;r.ensureDesignRunBundles();assert.equal(requests.length-start,4);
 const selected=d.loadDesignResult('point-0.json'),selectedRequest=requests.at(-1);assert.equal(requests.length-start,5,'selected main result is not queued');reply(selectedRequest,fresh);assert.equal(await selected,true);
 deadlines[0].fn();await tick();assert.equal(requests.length-start,6,'timeout frees exactly one slot');assert.equal(r.designRunBundleLoadState('point-0.json'),'error');
 reply(requests[start],fresh);await tick();assert.equal(requests.length-start,6,'late timed-out response cannot free a second slot');
 const done=new Set([requests[start],selectedRequest]);for(let pass=0;pass<12;pass++){for(const request of requests.slice(start))if(!done.has(request)){done.add(request);reply(request,fresh);}await tick();}
 assert.equal(r.designRunBundleLoadState('point-0.json'),'error');checks++;
} finally {globalThis.setTimeout=realSetTimeout;globalThis.clearTimeout=realClearTimeout;}
// A separately successful selection recovers a metadata error without another HTTP read.
// An older displayed generation must neither clear the error nor enter the current table.
s.setFile({id:'known-recovery',design:{model:{id:fixture.model.id}}});await tick();s.setIndex([entry(fresh)]);r.ensureDesignRunBundles();
requests.at(-1).resolve({ok:false,status:503});await tick();assert.equal(r.designRunBundleLoadState('same.json'),'error');
start=requests.length;d.setDesignResult({file:'same.json',bundle:old});r.ensureDesignRunBundles();assert.equal(r.designRunBundleLoadState('same.json'),'error');assert.equal(r.designRunBundle('same.json'),undefined);
d.setDesignResult({file:'same.json',bundle:fresh});r.ensureDesignRunBundles();assert.equal(requests.length,start,'known current selection is reused without refetch');assert.equal(r.designRunBundle('same.json').name,'fresh');assert.equal(r.designRunBundleLoadState('same.json'),undefined);checks++;
console.log(`Result lifecycle: ${checks} deferred navigation, replacement-cache, legacy identity and stale-response checks passed`);
} finally {
 for (const [key, descriptor] of originalGlobals) {
  if (descriptor) Object.defineProperty(globalThis, key, descriptor);
  else delete globalThis[key];
 }
}





