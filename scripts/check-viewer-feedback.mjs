// Real stores and asynchronous ownership, no browser, solver or user documents.
// FAIRBEAM_TEST_BASELINE=<git ref> loads earlier versions without replacing working files.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
import solid from 'vite-plugin-solid';

const root=fileURLToPath(new URL('../',import.meta.url)).replaceAll('\\','/');
const baseline=process.env.FAIRBEAM_TEST_BASELINE;
const modules=['state.ts','workspace.ts','designer/navModel.ts','home/projectList.ts','runner/designRun.ts',
  'designer/dockState.ts','designer/layoutState.ts','compare/store.ts','runner/dropFile.ts','lib/toast.ts','i18n/index.ts','runner/optimizationText.ts','runner/openProject.ts','runner/store.ts'];
const original=new Map();
if(baseline) for(const path of ['src/state.ts','src/designer/navModel.ts','src/home/projectList.ts','src/runner/designRun.ts','src/compare/store.ts','src/runner/dropFile.ts','src/runner/optimizationText.ts'])
  original.set(root+path,execFileSync('git',['show',`${baseline}:${path}`],{cwd:root,encoding:'utf8'}));
const built=await build({root,configFile:false,logLevel:'silent',resolve:{conditions:['browser']},css:{postcss:{}},
  plugins:[{name:'viewer-feedback-baseline',enforce:'pre',load(id){return original.get(id.replaceAll('\\','/'));}},solid(),{
    name:'viewer-feedback-entry',resolveId(id){if(id.endsWith('viewer-feedback-entry'))return '\0viewer-feedback-entry';},
    load(id){if(id==='\0viewer-feedback-entry')return modules.map((path,i)=>`export * as m${i} from ${JSON.stringify(root+'src/'+path)};`).join('\n');},
  }],build:{write:false,minify:false,cssCodeSplit:false,lib:{entry:'viewer-feedback-entry',formats:['es']}}});
const chunk=(Array.isArray(built)?built[0]:built).output.find(item=>item.type==='chunk');
const storage=new Map([['fairbeam.bottomDockCollapsed','true']]);
const stub={addEventListener(){},removeEventListener(){},dispatchEvent(){},querySelector:()=>null,documentElement:{}};
globalThis.window={...stub,document:stub,matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}})};
globalThis.document=stub;
globalThis.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)};
globalThis.requestAnimationFrame=()=>1;
globalThis.fetch=async()=>({ok:true,json:async()=>({runs:[],models:[]})});
const {m0:state,m1:workspace,m2:nav,m3:projects,m4:run,m5:dock,m6:layout,m7:compare,m8:drop,m9:toast,m10:i18n,m11:opt,m12:userOpen,m13:runner}=
  await import(`data:text/javascript;base64,${Buffer.from(chunk.code).toString('base64')}`);
const failures=[];let checks=0;
const check=async(name,fn)=>{try{await fn();checks++;console.log(`ok ${name}`);}catch(error){failures.push(name+': '+error.message);console.log(`FAIL ${name}: ${error.message}`);}};
const patch=JSON.parse(readFileSync(root+'public/projects/patch-antenna.json','utf8'));
const open=()=>state.openBundle(structuredClone(patch),'own-result.json','local-file');
const response=(ok,json={})=>({ok,status:ok?200:503,json:async()=>json});
const pending=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve:value=>resolve(value)};};

await check('#209 initial store effects preserve remembered dock collapse',()=>{
  assert.equal(layout.bottomDockCollapsed(),true);assert.equal(storage.get('fairbeam.bottomDockCollapsed'),'true');
});
await check('#209 reset follows Checks without revealing; explicit user action still reveals',()=>{
  layout.setBottomDockCollapsed(true);run.clearDesignResult();assert.equal(layout.bottomDockCollapsed(),true);
  assert.equal(dock.designDockTab(),'checks');dock.setDesignDockTab('runs');assert.equal(layout.bottomDockCollapsed(),false);
});
for(const lang of ['en','tr']) await check(`#210 ${lang} tree and Start find ASCII I and decomposed Turkish names`,()=>{
  i18n.setLanguage(lang);
  for(const [query,text] of [['pifa','PIFA'],['inset','Inset feed'],['igdir','IĞDIR'],['cok uzun','çok uzun'],['ğüşıöç','ğüşıöç'.normalize('NFD')]]) {
    assert.equal(nav.matchesFilter(query,text),true,`${query} matches ${text}`);
    const entries=[{key:'fixture',model:{name:text},document:{path:`/tmp/${text}.fb`,results_directory:`${text}.results`}}];
    const before=JSON.stringify(entries);assert.equal(projects.visibleProjects(entries,query,'name',false,new Set()).length,1);
    assert.equal(JSON.stringify(entries),before,'fold never changes stored spelling');
  }
  assert.equal(nav.matchesFilter('-1.25','−1.25'),true);assert.equal(nav.matchesFilter('farfield','Far field'),true);
  assert.equal(nav.matchesFilter('elsewhere','PIFA'),false);
});
await check('#205 retry clears old error and retry target synchronously',async()=>{
  open();globalThis.fetch=async()=>response(false);assert.equal(await state.loadProject('missing.json'),false);
  assert.equal(state.failedProject(),'missing.json');assert.ok(state.loadError());
  const held=pending();globalThis.fetch=()=>held.promise;const retry=state.loadProject('missing.json');
  assert.equal(state.loadError(),null);assert.equal(state.failedProject(),null);
  held.resolve(response(false));assert.equal(await retry,false);assert.equal(state.failedProject(),'missing.json');assert.ok(state.loadError());
});
await check('#205 superseded retry cannot restore stale error or replace newer project',async()=>{
  const held=pending();globalThis.fetch=()=>held.promise;const retry=state.loadProject('missing.json');
  state.openBundle(structuredClone(patch),'newer.json','local-file');held.resolve(response(false));await retry;
  assert.equal(state.source(),'newer.json');assert.equal(state.loadError(),null);assert.equal(state.failedProject(),null);
});
await check('#205 local file intent clears a previous retry while its read is pending',async()=>{
  state.setLoadError('old failure');state.setFailedProject('old.json');
  const held=pending();const load=state.loadFile({name:'new.json',text:()=>held.promise});
  assert.equal(state.loadError(),null);assert.equal(state.failedProject(),null);
  held.resolve(JSON.stringify(patch));assert.equal(await load,true);
});
await check('#205 Retry action uses the ownership-aware viewer entry point',()=>{
  const app=readFileSync(root+'src/App.tsx','utf8');
  assert.match(app,/action: file \? \{ label: t\("common.retry"\), run: \(\) => void retryUserProject\(file\)/);
});
await check('#205 Retry finishing after Start cannot replace the last viewer file',async()=>{
  open();workspace.setAppMode('results');const held=pending();globalThis.fetch=()=>held.promise;
  const retry=userOpen.retryUserProject('abandoned.json');workspace.setAppMode('home');
  held.resolve(response(true,patch));assert.equal(await retry,false);
  assert.equal(workspace.appMode(),'home');assert.equal(state.source(),'own-result.json');assert.equal(state.loadError(),null);
});
await check('#205 leaving and returning to Results still cancels an older Retry',async()=>{
  open();workspace.setAppMode('results');const held=pending();globalThis.fetch=()=>held.promise;
  const retry=userOpen.retryUserProject('abandoned.json');workspace.setAppMode('home');workspace.setAppMode('results');
  held.resolve(response(true,patch));assert.equal(await retry,false);assert.equal(state.source(),'own-result.json');
});
await check('#205 selecting another model cancels Retry before its preview arrives',async()=>{
  open();workspace.setAppMode('results');runner.selectModel('previous');
  const held=pending();globalThis.fetch=()=>held.promise;const retry=userOpen.retryUserProject('abandoned.json');
  runner.selectModel('new-model');held.resolve(response(true,patch));assert.equal(await retry,false);
  assert.equal(runner.modelKey(),'new-model');assert.equal(state.source(),'own-result.json');
});
await check('#205 newer project loads own their error and Retry target',async()=>{
  open();workspace.setAppMode('results');const first=pending(),second=pending();let reads=0;
  globalThis.fetch=()=>++reads===1?first.promise:second.promise;
  const retry=userOpen.retryUserProject('old.json'),newer=state.loadProject('new.json');
  second.resolve(response(false));await newer;first.resolve(response(true,patch));assert.equal(await retry,false);
  assert.equal(state.failedProject(),'new.json');assert.ok(state.loadError().includes('new.json'));assert.equal(state.source(),'own-result.json');
});
await check('#205 an abandoned Retry failure does not restore its obsolete error',async()=>{
  open();workspace.setAppMode('results');const held=pending();globalThis.fetch=()=>held.promise;
  const retry=userOpen.retryUserProject('old.json');workspace.setAppMode('home');held.resolve(response(false));
  assert.equal(await retry,false);assert.equal(state.loadError(),null);assert.equal(state.failedProject(),null);
});
await check('#205 current Retry succeeds and leaves the selected screen unchanged',async()=>{
  open();workspace.setAppMode('results');state.setLoadError('previous');state.setFailedProject('recovered.json');
  globalThis.fetch=async()=>response(true,patch);assert.equal(await userOpen.retryUserProject('recovered.json'),true);
  assert.equal(state.source(),'recovered.json');assert.equal(workspace.appMode(),'results');assert.equal(state.loadError(),null);assert.equal(state.failedProject(),null);
});
await check('#183 failed and superseded reference imports report truthful outcomes',async()=>{
  open();const result=await compare.importReferenceFile({name:'bad.s1p',text:async()=>''});
  assert.equal(result?.status,'failed');assert.ok(result.error.includes('bad.s1p'));
  const held=pending();const old=compare.importReferenceFile({name:'old.s1p',text:()=>held.promise});
  compare.clearReference();held.resolve('# GHz S RI R 50\n1 0.1 0\n2 0.2 0');
  assert.equal((await old)?.status,'superseded');assert.equal(compare.reference(),undefined);assert.equal(compare.refError(),null);
});
await check('#183 drop rejects hidden reference imports and unsupported formats with feedback',async()=>{
  workspace.setAppMode('home');compare.clearReference();
  await drop.openDroppedFile({name:'measurement.s1p',text:async()=>{throw Error('must not read hidden reference');}});
  assert.ok(toast.toasts().at(-1).text.includes('measurement.s1p'));assert.equal(compare.reference(),undefined);
  await drop.openDroppedFile({name:'outline.dxf',text:async()=>''});assert.equal(toast.toasts().at(-1).tone,'error');
  await drop.openDroppedFile({name:'image.png',text:async()=>''});assert.equal(toast.toasts().at(-1).tone,'error');
});
await check('#183 visible result import retains real reference data and reports success',async()=>{
  open();workspace.setAppMode('results');
  await drop.openDroppedFile({name:'measurement.s1p',text:async()=>'# GHz S RI R 50\n1 0.1 0\n2 0.2 0'});
  assert.equal(compare.reference()?.reference.files[0],'measurement.s1p');assert.equal(toast.toasts().at(-1).tone,'info');
});
const numericReference='Frequency (GHz)\tS11 (dB)\n1\t-10\n2\t-20';
for(const lang of ['en','tr']) for(const mode of ['home','design']) await check(`#183 ${lang}/${mode} .txt macros route to VBA import without touching hidden references`,async()=>{
  i18n.setLanguage(lang);open();compare.clearReference();
  await compare.importReferenceFile({name:'existing.s1p',text:async()=>'# GHz S RI R 50\n1 0.1 0\n2 0.2 0'});
  await compare.importReferenceFile({name:'previous-error.s1p',text:async()=>''});
  const existing=compare.reference(),error=compare.refError(),owner=state.bundle();
  workspace.setAppMode(mode);
  for(const macro of ['Sub Main()\nEnd Sub','Private Sub Main()\nEnd Sub','With Brick\n .Name "body"\nEnd With','AddToHistory "brick", sCommand']) {
    let reads=0;
    await drop.openDroppedFile({name:'history.txt',text:async()=>{reads++;return macro;}});
    assert.equal(reads,1);assert.equal(toast.toasts().at(-1).text,i18n.t('app.dropCst',{name:'history.txt'}));
    assert.equal(compare.reference(),existing);assert.equal(compare.refError(),error);
    assert.equal(state.bundle(),owner);assert.equal(workspace.appMode(),mode);
  }
});
for(const mode of ['home','design']) await check(`#183 ${mode} numeric .txt only explains the reference context`,async()=>{
  open();compare.clearReference();workspace.setAppMode(mode);let reads=0;
  await drop.openDroppedFile({name:'measurement.txt',text:async()=>{reads++;return `! AddToHistory is a measurement comment\n${numericReference}`;}});
  assert.equal(reads,1);assert.equal(toast.toasts().at(-1).text,i18n.t('app.dropReferenceContext',{name:'measurement.txt'}));
  assert.equal(toast.toasts().at(-1).tone,'info');assert.equal(compare.reference(),undefined);assert.equal(compare.refError(),null);
});
for(const mode of ['home','design']) await check(`#183 ${mode} .txt read failure reports the real error without mutating reference state`,async()=>{
  open();compare.clearReference();workspace.setAppMode(mode);
  for(const failure of [Error('fixture read failed'),'fixture string failure']) {
    await drop.openDroppedFile({name:'unreadable.txt',text:async()=>{throw failure;}});
    assert.equal(toast.toasts().at(-1).text,`unreadable.txt: ${failure instanceof Error?failure.message:failure}`);
    assert.equal(toast.toasts().at(-1).tone,'error');assert.equal(compare.reference(),undefined);assert.equal(compare.refError(),null);
  }
});
for(const change of ['navigation','leave-and-return','same-screen','bundle']) await check(`#183 pending outside-Results .txt classification yields to ${change}`,async()=>{
  open();workspace.setAppMode('home');compare.clearReference();toast.dismissToast('file-drop');
  const held=pending();let reads=0;
  const opening=drop.openDroppedFile({name:'old-history.txt',text:()=>{reads++;return held.promise;}});
  assert.equal(reads,1);assert.equal(toast.toasts().some(item=>item.key==='file-drop'),false);
  if(change==='navigation') workspace.setAppMode('results');
  else if(change==='leave-and-return') {workspace.setAppMode('design');workspace.setAppMode('home');}
  else if(change==='same-screen') workspace.setAppMode('home');
  else state.openBundle(structuredClone(patch),'newer-project.json','local-file');
  const owner=state.bundle(),mode=workspace.appMode();
  held.resolve('Sub Main()\nEnd Sub');await opening;
  assert.equal(toast.toasts().some(item=>item.key==='file-drop'),false);
  assert.equal(compare.reference(),undefined);assert.equal(compare.refError(),null);
  assert.equal(state.bundle(),owner);assert.equal(workspace.appMode(),mode);
});
await check('#183 a newer drop owns feedback while an outside-Results .txt read is pending',async()=>{
  open();workspace.setAppMode('design');compare.clearReference();toast.dismissToast('file-drop');
  const held=pending();const opening=drop.openDroppedFile({name:'old-history.txt',text:()=>held.promise});
  assert.equal(toast.toasts().some(item=>item.key==='file-drop'),false);
  await drop.openDroppedFile({name:'new-image.png',text:async()=>''});
  const current=toast.toasts().at(-1);
  held.resolve('AddToHistory "brick", sCommand');await opening;
  assert.equal(toast.toasts().at(-1),current);assert.ok(current.text.includes('new-image.png'));
  assert.equal(compare.reference(),undefined);assert.equal(compare.refError(),null);
});
await check('#183 JSON open supersedes an outside-Results .txt classification',async()=>{
  open();workspace.setAppMode('home');compare.clearReference();toast.dismissToast('file-drop');
  const held=pending();const opening=drop.openDroppedFile({name:'old-history.txt',text:()=>held.promise});
  assert.equal(toast.toasts().some(item=>item.key==='file-drop'),false);
  await drop.openDroppedFile({name:'new-result.json',text:async()=>JSON.stringify(patch)});
  held.resolve('Sub Main()\nEnd Sub');await opening;
  assert.equal(state.source(),'new-result.json');assert.equal(workspace.appMode(),'results');
  assert.equal(toast.toasts().some(item=>item.key==='file-drop'),false);assert.equal(compare.reference(),undefined);
});
await check('#183 stale outside-Results .txt read failure stays silent after a newer drop',async()=>{
  open();workspace.setAppMode('home');compare.clearReference();toast.dismissToast('file-drop');
  let rejectRead;const reading=new Promise((_,reject)=>{rejectRead=reject;});
  const opening=drop.openDroppedFile({name:'unreadable-old.txt',text:()=>reading});
  assert.equal(toast.toasts().some(item=>item.key==='file-drop'),false);
  await drop.openDroppedFile({name:'new-history.bas',text:async()=>''});const current=toast.toasts().at(-1);
  rejectRead(Error('stale read failure'));await opening;
  assert.equal(toast.toasts().at(-1),current);assert.equal(compare.refError(),null);assert.equal(compare.reference(),undefined);
});
for(const extension of ['txt','dat','tsv','snp']) await check(`#183 dropped .${extension} reference matches file-picker support`,async()=>{
  open();workspace.setAppMode('results');compare.clearReference();let reads=0;
  const text=extension==='snp'?'# GHz S RI R 50\n1 0.1 0\n2 0.2 0':numericReference;
  await drop.openDroppedFile({name:`measurement.${extension}`,text:async()=>{reads++;return text;}});
  assert.equal(compare.reference()?.reference.files[0],`measurement.${extension}`);
  assert.deepEqual(compare.reference()?.results.frequency,[1e9,2e9]);assert.equal(reads,1);
  assert.equal(toast.toasts().at(-1).tone,'info');
});
await check('#183 retains arbitrary Touchstone port counts and plain-text MIME acceptance',async()=>{
  open();workspace.setAppMode('results');compare.clearReference();
  const row=Array.from({length:81},()=> '0.1 0').join(' ');
  await drop.openDroppedFile({name:'measurement.s9p',text:async()=>`# GHz S RI R 50\n1 ${row}\n2 ${row}`});
  assert.equal(compare.reference()?.results.sparams.ports.length,9);
  compare.clearReference();
  await drop.openDroppedFile({name:'measurement',type:'text/plain',text:async()=>numericReference});
  assert.equal(compare.reference()?.reference.files[0],'measurement');
});
await check('#183 .txt VBA/history gets macro guidance but numeric text with marker comments imports',async()=>{
  open();workspace.setAppMode('results');compare.clearReference();
  for(const macro of ['Sub Main()\nEnd Sub','With Brick\n .Name "body"\nEnd With','AddToHistory "brick", sCommand']) {
    await drop.openDroppedFile({name:'history.txt',text:async()=>macro});
    assert.equal(compare.reference(),undefined);assert.equal(toast.toasts().at(-1).text,i18n.t('app.dropCst',{name:'history.txt'}));
  }
  await drop.openDroppedFile({name:'measurement.txt',text:async()=>`! AddToHistory is a comment in this exported measurement\n${numericReference}`});
  assert.equal(compare.reference()?.reference.files[0],'measurement.txt');
});
await check('#183 invalid/read-failed .txt keeps the reference import error contract',async()=>{
  open();workspace.setAppMode('results');compare.clearReference();
  await drop.openDroppedFile({name:'invalid.txt',text:async()=>'not numeric data'});
  assert.equal(compare.reference(),undefined);assert.equal(toast.toasts().at(-1).text,compare.refError());
  assert.ok(compare.refError().startsWith('invalid.txt:'));
  await drop.openDroppedFile({name:'unreadable.txt',text:async()=>{throw Error('fixture read failed');}});
  assert.equal(toast.toasts().at(-1).text,'unreadable.txt: fixture read failed');
});
await check('#183 pending .txt drop cannot replace a newer import or a newer project',async()=>{
  open();workspace.setAppMode('results');compare.clearReference();
  const first=pending();const old=drop.openDroppedFile({name:'old.txt',text:()=>first.promise});
  await drop.openDroppedFile({name:'new.dat',text:async()=>numericReference});
  first.resolve(numericReference);await old;
  assert.deepEqual(compare.reference()?.reference.files,['new.dat']);
  const second=pending();const departed=drop.openDroppedFile({name:'departed.txt',text:()=>second.promise});
  state.openBundle(structuredClone(patch),'newer-project.json','local-file');compare.clearReference();
  second.resolve(numericReference);await departed;assert.equal(compare.reference(),undefined);
});
await check('#183 JSON and native-document extensions still use the project loader',async()=>{
  workspace.setAppMode('home');compare.clearReference();
  await drop.openDroppedFile({name:'dropped.json',text:async()=>JSON.stringify(patch)});
  assert.equal(state.source(),'dropped.json');assert.equal(workspace.appMode(),'results');assert.equal(compare.reference(),undefined);
  await drop.openDroppedFile({name:'invalid.fb',text:async()=>'not a document'});
  assert.ok(state.loadError()?.includes('invalid.fb'));assert.equal(compare.reference(),undefined);
});
for(const lang of ['en','tr']) await check(`#202 ${lang} reason/value formatting and matched units`,()=>{
  i18n.setLanguage(lang);i18n.setDecimalChoice('point');assert.equal(opt.reasonText('simplex converged'),i18n.t('opt.reason.simplexConverged'));
  assert.equal(opt.reasonText('goals met'),i18n.t('opt.reason.goalsMet'));
  assert.equal(opt.reasonText('unfamiliar solver diagnostic'),'unfamiliar solver diagnostic');
  assert.equal(opt.optimizationValues({L:31.427583920113,n:2},[{key:'L',unit:'mm'}]),'L=31.4276 mm; n=2');
  i18n.setDecimalChoice('comma');assert.equal(opt.optimizationValues({L:31.427583920113},[{key:'L',unit:'mm'}]),'L=31,4276 mm');
});
for(const lang of ['en','tr']) for(const decimals of ['point','comma','language']) await check(`#202 ${lang}/${decimals} preserves tiny, large, negative and zero values`,()=>{
  i18n.setLanguage(lang);i18n.setDecimalChoice(decimals);
  const comma=decimals==='comma'||(decimals==='language'&&lang==='tr'),sep=comma?',':'.';
  const values={C:1.37e-12,R:1.23456789e9,offset:-1.37e-12,zero:0,negativeZero:-0};
  const before=JSON.stringify(values);
  assert.equal(opt.optimizationValues(values,[{key:'C',unit:'F'},{key:'R',unit:'Ω'}]),
    `C=1${sep}37E-12 F; R=1${sep}23457E9 Ω; offset=−1${sep}37E-12; zero=0; negativeZero=0`);
  assert.equal(JSON.stringify(values),before,'display formatting does not change optimizer values');
});
console.log(`viewer-feedback: ${checks} passed, ${failures.length} failed`);
if(failures.length)console.error(failures.join('\n'));
process.exit(failures.length?1:0);
