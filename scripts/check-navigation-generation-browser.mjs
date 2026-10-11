// Browser integration of delayed Examples navigation and same-file run generation guards.
// Network responses are controlled fixtures; no solver or live workspace.
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import puppeteer from 'puppeteer-core';
import {startStack,chromePath,root} from './scenarios/stack.mjs';
import {Session} from './scenarios/harness.mjs';
const out=resolve(process.env.FAIRBEAM_TEST_ARTIFACTS||join(tmpdir(),`fairbeam-navigation-generation-${Date.now()}`));await mkdir(out,{recursive:true});
const template=JSON.parse(await readFile(join(root,'public/projects/patch-antenna.json'),'utf8'));
const stack=await startStack({log:console.log});let browser;const results=[];
const waitHeld=async get=>{for(let i=0;i<150;i++){if(get())return get();await new Promise(r=>setTimeout(r,20));}throw Error('expected held network request');};
try{
 browser=await puppeteer.launch({headless:true,executablePath:await chromePath(),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});console.log(`Owned PIDs: ${stack.pids.join(', ')}`);
 for(const lang of ['en','tr']){
  const context=await browser.createBrowserContext(),page=await context.newPage();await page.setViewport({width:1440,height:1000});
  await page.evaluateOnNewDocument(language=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language})),lang);
  const s=new Session(page,{lang,scenario:'navigation-generation',url:stack.url});await page.goto(stack.url,{waitUntil:'domcontentloaded'});await s.wait('.home');
  await s.fill(await s.field(await s.T('home.newProject.name')),`Navigation ${lang}`,{blur:false});await s.click('home.newProject.create');await s.wait('.rb');
  const own=await s.store((_,m)=>JSON.parse(JSON.stringify(m.s.file())));
  const snapshot=()=>s.ev((_,m)=>({mode:m.w.appMode(),source:m.st.source(),model:m.st.bundle()?.model.id,design:m.d.file()?.id}),null,{w:'/src/workspace.ts',st:'/src/state.ts',d:'/src/designer/store.ts'});
  let holdIndex=false,heldIndex,heldRuns=[],runFile='',requestGeneration;const released=new Map();
  await page.setRequestInterception(true);page.on('request',r=>{
   const path=new URL(r.url()).pathname;
   if(holdIndex&&path.endsWith('/projects/index.json')){heldIndex=r;return;}
   if(runFile&&path.endsWith('/'+runFile)){const pending={request:r,generation:requestGeneration};heldRuns.push(pending);const value=released.get(pending.generation);if(value)void r.respond({status:200,contentType:'application/json',body:JSON.stringify(value)});return;}
   void r.continue();
  });
  const releaseIndex=async()=>{holdIndex=false;await heldIndex.continue();heldIndex=null;await s.waitFor(async()=>!(await import('/src/state.ts')).indexLoading());};
  holdIndex=true;await s.click('header.screen.examples');await waitHeld(()=>heldIndex);
  await s.click('header.screen.design',{sel:'.mode-switch button'});await s.wait('.rb');const chosenDesign=await snapshot();await releaseIndex();assert.deepEqual(await snapshot(),chosenDesign,'late Examples index preserves newer Design');
  // A new selection in the Results workspace must also supersede the older Examples request.
  holdIndex=true;await s.click('header.screen.examples');await waitHeld(()=>heldIndex);
  await s.wait('.example-picker-trigger');await page.locator('.example-picker-trigger').click();await s.wait('.example-picker-pop input');await page.type('.example-picker-pop input','Inset');await page.keyboard.press('Enter');
  await s.waitFor(async()=>(await import('/src/state.ts')).source()==='inset-patch.json');const chosenResults=await snapshot();await releaseIndex();assert.deepEqual(await snapshot(),chosenResults,'late index preserves a newer picked result');
  await page.screenshot({path:join(out,`navigation-${lang}.png`)});
  await s.click('header.screen.design',{sel:'.mode-switch button'});await s.wait('.rb');
  // Synthetic generations use a valid shipped bundle, but deliberately different timestamps/parameters.
  runFile=`${own.id}--generation.json`;
  const generation=(stamp,value)=>{const b=structuredClone(template);b.model.id=own.design.model.id;b.created=stamp;b.name=`Generation ${value}`;b.results.ports['1'].s11_re=b.results.frequency.map((_,i)=>0.1+0.7*(i/(b.results.frequency.length-1))**(value/10));b.results.ports['1'].s11_im=b.results.frequency.map(()=>0);b.model.params=[{key:'audit_length',label:'Audit length',value,unit:'mm'}];return b;};
  const a=generation('2026-10-11T00:00:01Z',10),b=generation('2026-10-11T00:00:02Z',20),c=generation('2026-10-11T00:00:03Z',30);
  const entry=x=>({file:runFile,model:own.design.model.id,name:'Generation fixture',created:x.created,engine:'CPU',simulated:true,bands:[],params:[]});
  const install=async (x,shown)=>{requestGeneration=x.created;return s.ev(({entry,shown,file},m)=>{m.st.setIndex([...m.st.index().filter(r=>r.file!==file),entry]);if(shown)m.dr.setDesignResult({file,bundle:shown});m.rr.ensureDesignRunBundles();},{entry:entry(x),shown,file:runFile},{st:'/src/state.ts',dr:'/src/runner/designRun.ts',rr:'/src/designer/runResults.ts'});};
  await install(a,a);await s.click('dock.tab.runs',{sel:'.rdk [role=tab]'});await s.wait('.rdk-runs');
  const cached=()=>s.ev(file=>import('/src/designer/runResults.ts').then(m=>m.designRunBundle(file)?.model.params[0].value),runFile,{});
  assert.equal(await cached(),10);
  await s.ev(file=>import('/src/designer/resultFocus.ts').then(m=>m.focusResult({file,view:'sparams'},'main')),runFile,{});
  await s.wait('.dw-result-plot .c-line');
  const tracePath=()=>page.$eval('.dw-result-plot .c-line',node=>node.getAttribute('d'));
  const pathA=await tracePath();assert.ok(pathA?.length>20);
  const shown=()=>s.ev((_,m)=>({created:m.dr.designResult()?.bundle.created??null,focus:m.rf.resultFocus(),viewport:m.st.bundle()?.created??null,error:m.dr.designResultError()}),null,{dr:'/src/runner/designRun.ts',rf:'/src/designer/resultFocus.ts',st:'/src/state.ts'});
  await install(b,null);await waitHeld(()=>heldRuns.some(r=>r.generation===b.created));assert.equal(await cached(),undefined,'old generation metadata is hidden before replacement resolves');
  await s.waitFor(()=>document.querySelectorAll('.dw-result-plot .c-line').length===0);
  assert.equal((await shown()).created,null,'old plotted bundle must disappear while B is held');
  assert.equal((await shown()).viewport,null,'old viewport must disappear while B is held');
  assert.equal((await shown()).focus?.view,'sparams','focused view remains selected');
  const heldExport=await s.ev(()=>import('/src/designer/activeResult.ts').then(m=>({run:m.designResultState().bundle,package:m.exportBundle()})),null,{});
  assert.equal(heldExport.run,null);assert.equal(heldExport.package,null,'exports must not silently fall back to stale data');
  await page.screenshot({path:join(out,`generation-loading-${lang}.png`)});
  await install(c,null);await waitHeld(()=>heldRuns.some(r=>r.generation===c.created));
  const release=async value=>{released.set(value.created,value);for(const pending of heldRuns.filter(r=>r.generation===value.created))if(!pending.request.isInterceptResolutionHandled())await pending.request.respond({status:200,contentType:'application/json',body:JSON.stringify(value)});};
  await release(c);
  await s.waitFor(async file=>(await import('/src/designer/runResults.ts')).designRunBundle(file)?.model.params[0].value===30,runFile);
  await release(b);await s.sleep(100);
  assert.equal(await cached(),30,'late old generation cannot replace current data');
  await s.wait('.dw-result-plot .c-line');const pathC=await tracePath();assert.notEqual(pathC,pathA,'rendered trace must use replacement data');
  assert.equal((await shown()).created,c.created);assert.equal((await shown()).viewport,c.created);assert.equal((await shown()).focus?.view,'sparams');
  const displayedSample=await s.ev(()=>import('/src/runner/designRun.ts').then(m=>m.designResult()?.bundle.results.ports['1'].s11_re[100]),null,{});
  assert.equal(displayedSample,c.results.ports['1'].s11_re[100]);
  const exportCreated=await s.ev(()=>import('/src/designer/activeResult.ts').then(m=>m.exportBundle()?.created),null,{});assert.equal(exportCreated,c.created);await s.wait('.rdk-runs');await page.screenshot({path:join(out,`generation-${lang}.png`)});
  results.push({lang,chosenDesign,chosenResults,staleMetadataHidden:true,currentValue:await cached(),lateOldResponseIgnored:true,heldOldTraceRemoved:true,renderedTraceChanged:pathC!==pathA,currentPlot:await shown(),displayedSample});
  // Picking a different file must not export the previously displayed valid result while held.
  runFile=`${own.id}--other-result.json`;
  const other=generation('2026-10-11T00:00:04Z',40);
  await install(other,null);
  await s.ev(file=>import('/src/designer/resultFocus.ts').then(m=>m.focusResult({file,view:'sparams'},'main')),runFile,{});
  await waitHeld(()=>heldRuns.some(r=>r.generation===other.created));
  await s.waitFor(()=>document.querySelectorAll('.dw-result-plot .c-line').length===0);
  const pendingOther=await s.ev(()=>import('/src/designer/activeResult.ts').then(m=>({run:m.designResultState().bundle,package:m.exportBundle()})),null,{});
  assert.equal(pendingOther.run,null);assert.equal(pendingOther.package,null);
  assert.equal((await shown()).created,null);
  await release(other);
  await s.waitFor(async stamp=>(await import('/src/runner/designRun.ts')).designResult()?.bundle.created===stamp,other.created);
  await s.wait('.dw-result-plot .c-line');
  results.at(-1).differentFilePendingExportBlocked=true;
  for(const {request} of heldRuns)if(!request.isInterceptResolutionHandled())await request.abort();await context.close();
 }
 await writeFile(join(out,'result.json'),JSON.stringify(results,null,2));console.log(JSON.stringify({out,results},null,2));
}finally{await browser?.close();await stack.stop();}
