// Real lifecycle modules bundled in memory; dependency state and network are isolated.
// No DOM/server/native solver. Deferred responses exercise ownership, not elapsed timers.
import assert from 'node:assert/strict';
import {build} from 'vite';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=fileURLToPath(new URL('../',import.meta.url)).replaceAll('\\','/');
const files=['src/runner/designRun.ts','src/designer/runResults.ts'].map(f=>root+f);
const imports=new Map();
for(const file of files){
 for(const match of readFileSync(file,'utf8').matchAll(/import\s+(type\s+)?\{([^}]+)\}\s+from\s+["']([^"']+)["']/g)){
  if(match[1])continue;
  const id=match[3];
  if(id==='solid-js'||['../workspace','../lib/validate','../lib/resultIdentity','./resultFollow','./navModel','../lib/nativeDocuments'].includes(id))continue;
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
  if(id==='\0lifecycle-entry')return `export * as native from ${JSON.stringify(root+'src/lib/nativeDocuments.ts')};export * as runs from ${JSON.stringify(files[1])};export * as design from ${JSON.stringify(files[0])};export * as state from ${JSON.stringify(control)};export * as workspace from ${JSON.stringify(root+'src/workspace.ts')};`;
  if(id.startsWith('\0stub:'))return [...imports.get(id.slice(6))].map(n=>exposed.has(n)?`export {${n}} from ${JSON.stringify(control)};`:`export const ${n}=()=>undefined;`).join('\n');
 } }],build:{write:false,minify:false,lib:{entry:'lifecycle-entry',formats:['es']}}});

const originalGlobals = new Map(['localStorage', 'fetch'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
try {
globalThis.localStorage={getItem:()=>null,setItem(){}};
const code=(Array.isArray(built)?built[0]:built).output.find(x=>x.type==='chunk').code;
const {runs:r,design:d,state:s,workspace:w,native:n}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const tick=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
const deferred=()=>{let resolve;const promise=new Promise(yes=>resolve=yes);return{promise,resolve};};
const fixture=JSON.parse(readFileSync(root+'public/projects/patch-antenna.json','utf8'));
const f={id:'fb_'+'a'.repeat(32),backup_scope:'workspace-A',design:{model:{id:fixture.model.id}}};
const document=(file,handle,results=[])=>({status:'opened',handle,path:`/${handle}.fb`,file,document:{id:'uuid-A',path:`/${handle}.fb`,revision:'rev-'+handle,results_directory:'results'},results,warnings:[]});
const ref=file=>({file,project_file:file,status:'current'});
const row=file=>({file,name:file,model:fixture.model.id,created:fixture.created,engine:'CPU',simulated:true,bands:[],cells:1});
const requests=[];
let pending;
globalThis.fetch=url=>{requests.push(url);return pending?.promise??Promise.resolve({ok:true,json:async()=>structuredClone(fixture)});};
w.setAppMode('home');n.rememberNativeDocument(document(f,'original',[ref('own.json')]));s.setFile(f);s.setIndex([row('own.json'),row('sibling.json')]);await tick();
assert.deepEqual(r.designRuns().map(x=>x.file),['own.json'],'tree admits only document refs');
assert.deepEqual(r.otherProjectRuns().map(x=>x.file),['sibling.json'],'same-model siblings remain available as explicit comparisons');
const sibling={id:'foreign-Elmer',model:'fb_'+'b'.repeat(32),model_id:fixture.model.id,status:'done',bundle:'sibling.json',finished:10,kind:'research',research:{backend:'elmer',settings:{analysis:'driven'}}};
s.setJobs([sibling]);await tick();
assert.equal(d.designResult(),null,'foreign Elmer history cannot become the primary document result');
assert.deepEqual(requests,[],'foreign Elmer completion makes no automatic bundle request');
assert.equal(await d.loadDesignResult('sibling.json'),false,'foreign primary loads are rejected');
assert.deepEqual(requests,[],'membership is checked before fetching a foreign primary result');
assert.equal(await d.loadDesignResult('own.json'),true,'admitted portable result loads normally');
assert.equal(d.designResult()?.file,'own.json');

// Sweep metadata cannot create another document's empty/active group in this tree.
s.setJobs([{...sibling,kind:'run',status:'running',bundle:null,sweep:{id:'foreign-sweep',name:'Sibling sweep',total:3,index:0}}]);await tick();
assert.equal(r.designResultNodes().some(node=>node.id==='sweep:foreign-sweep'),false);

// Results produced after opening are admitted by exact workspace key, even without a finish time.
s.ctl.indices.push({promise:Promise.resolve(s.index())});
s.setJobs([{...sibling,id:'own-job',model:f.id,kind:'run',research:undefined,finished:0}]);await tick();
assert.deepEqual(new Set(r.designRuns().map(x=>x.file)),new Set(['own.json','sibling.json']));
assert.equal(d.designResult()?.file,'sibling.json','own workspace completion still follows automatically');

// Driven Elmer uses a server-verified source key/scope, never an embedded-model alias.
const elmerSource={model_id:f.id,workspace_hash:'a'.repeat(64),backup_scope:f.backup_scope,design_sha256:'b'.repeat(64)};
const ownElmer={...sibling,id:'own-Elmer',model:f.id,research:{...sibling.research,source:elmerSource}};
s.setJobs([]);d.clearDesignResult();
s.setJobs([{...ownElmer,research:sibling.research}]);await tick();
assert.deepEqual(r.designRuns().map(x=>x.file),['own.json'],'source-less research cannot gain native ownership by an equal model key');
assert.equal(await d.loadDesignResult('sibling.json'),false);
s.setJobs([{...ownElmer,research:{...ownElmer.research,source:{...elmerSource,backup_scope:'other-workspace'}}}]);await tick();
assert.deepEqual(r.designRuns().map(x=>x.file),['own.json'],'research from another workspace scope stays excluded');
s.ctl.indices.push({promise:Promise.resolve(s.index())});s.setJobs([ownElmer]);await tick();
assert.deepEqual(new Set(r.designRuns().map(x=>x.file)),new Set(['own.json','sibling.json']));
assert.equal(d.designResult()?.file,'sibling.json','verified driven Elmer completion follows in its source document');

// A new native selection for the same UUID revokes even a transport ignoring AbortSignal.
s.setJobs([]);d.clearDesignResult();pending=deferred();
const request=d.loadDesignResult('own.json');
n.rememberNativeDocument(document(f,'other-location',[]));await tick();
pending.resolve({ok:true,json:async()=>structuredClone(fixture)});
assert.equal(await request,false);await tick();pending=undefined;
assert.equal(d.designResult(),null,'late response from previous native selection stays retired');
assert.deepEqual(r.designRuns(),[]);

// Renderer reload may restore the workspace before the shell returns its grant metadata.
s.setFile({...f,id:'fb_'+'c'.repeat(32)});await tick();
assert.deepEqual(r.designRuns(),[],'bindingless portable workspace does not use embedded model identity');
assert.equal(await d.loadDesignResult('own.json'),false);
n.rememberNativeDocument(document(s.file(),'restored',[ref('own.json')]));await tick();
assert.deepEqual(r.designRuns().map(x=>x.file),['own.json'],'restored grant exposes only admitted refs');

// First Save can bind a pre-existing plain workspace key. Its server identity survives a restart.
s.setFile({...f,id:'plain-workspace-key',document_id:'11111111-1111-4111-8111-111111111111'});await tick();
assert.deepEqual(r.designRuns(),[],'durable identity prevents fallback before a native grant exists');
assert.equal(await d.loadDesignResult('own.json'),false);

// Legacy unbound designs keep the existing driven-Elmer model-ID behavior.
s.setJobs([]);s.setFile({...f,id:'legacy-design'});await tick();
s.ctl.indices.push({promise:Promise.resolve(s.index())});s.setJobs([sibling]);await tick();
assert.equal(d.designResult()?.file,'sibling.json');
d.clearDesignResult();s.setJobs([]);s.setFile(null);
console.log('Native result isolation: refs, exact workspace jobs, same-model comparisons, Elmer/sweep isolation, late selection responses and renderer reload passed');
} finally {
 for(const [key,descriptor] of originalGlobals) { if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key]; }
}
