// Real selected-result timeout and Retry UI, with an isolated clock/network and no solver.
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import puppeteer from 'puppeteer-core';
import {startStack,chromePath,root} from './scenarios/stack.mjs';
import {Session} from './scenarios/harness.mjs';
const out=process.env.FAIRBEAM_TEST_ARTIFACTS||join(tmpdir(),`fairbeam-selected-deadline-${Date.now()}`);
await mkdir(out,{recursive:true});
const template=JSON.parse(await readFile(join(root,'public/projects/patch-antenna.json'),'utf8'));
const stack=await startStack({log:console.log});let browser;const results=[];
try {
 browser=await puppeteer.launch({headless:true,executablePath:await chromePath(),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
 console.log(`Owned stack PIDs: ${stack.pids.join(', ')}`);
 for(const lang of ['en','tr']) {
  const context=await browser.createBrowserContext(),page=await context.newPage();await page.setViewport({width:1440,height:1000});
  await page.evaluateOnNewDocument(language=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language})),lang);
  const s=new Session(page,{lang,scenario:'selected-deadline',url:stack.url});
  await page.goto(stack.url,{waitUntil:'domcontentloaded'});await s.wait('.home');
  await s.fill(await s.field(await s.T('home.newProject.name')),`Selected deadline ${lang}`,{blur:false});await s.click('home.newProject.create');await s.wait('.rb');
  const own=await s.store((_,m)=>JSON.parse(JSON.stringify(m.s.file()))),file=`${own.id}--deadline.json`;
  const fixture=structuredClone(template);fixture.model.id=own.design.model.id;fixture.created='2026-10-11T01:00:00Z';fixture.name='Selected deadline fixture';
  let mode='hold',requests=0;const held=[];
  await page.setRequestInterception(true);page.on('request',request=>{
   if(new URL(request.url()).pathname.endsWith('/'+file)){
    requests++;if(mode==='hold')held.push(request);else void request.respond({status:200,contentType:'application/json',body:JSON.stringify(fixture)});return;
   }void request.continue();
  });
  // Capture only the production deadline after startup. Advancing it avoids a 30 s test sleep;
  // the request, AbortController, loading/error state and Retry button are all production code.
  await page.evaluate(()=>{
   const originalSet=window.setTimeout.bind(window),originalClear=window.clearTimeout.bind(window),timers=new Map();let id=-1;
   window.setTimeout=(fn,delay,...args)=>{if(delay!==30000)return originalSet(fn,delay,...args);const key=id--;timers.set(key,()=>fn(...args));return key;};
   window.clearTimeout=key=>{if(!timers.delete(key))originalClear(key);};
   window.__selectedDeadline={fire(){const pending=[...timers.values()];timers.clear();pending.forEach(fn=>fn());return pending.length;}};
  });
  await s.ev(({file,fixture},m)=>{
   m.st.setIndex([...m.st.index(),{file,model:fixture.model.id,name:fixture.name,created:fixture.created,engine:'CPU',simulated:true,bands:[]}]);
   m.rf.focusResult({file,view:'sparams'},'main');
  },{file,fixture},{st:'/src/state.ts',rf:'/src/designer/resultFocus.ts'});
  await s.waitFor(async file=>(await import('/src/runner/designRun.ts')).designResultLoading()===file,file);
  assert.ok(await page.evaluate(()=>window.__selectedDeadline.fire())>0);
  await s.wait('.dw-result [role="alert"]');
  const alert=await page.$eval('.dw-result [role="alert"]',el=>el.textContent);
  assert.ok(alert.includes(await s.T('load.runResultTimedOut')));assert.ok(alert.includes(await s.T('common.retry')));
  assert.equal(await s.ev(()=>import('/src/runner/designRun.ts').then(m=>m.designResultLoading()),null,{}),null);
  await page.screenshot({path:join(out,`timeout-${lang}.png`)});
  mode='success';await s.click('common.retry',{sel:'.dw-result [role="alert"] button'});
  await s.waitFor(async file=>(await import('/src/runner/designRun.ts')).designResult()?.file===file,file);
  await s.wait('.dw-result-plot .c-line');assert.equal(await page.$('.dw-result [role="alert"]'),null);
  const recovered=await s.ev(()=>import('/src/runner/designRun.ts').then(m=>({name:m.designResult()?.bundle.name,loading:m.designResultLoading(),failed:m.failedResultLoad()})),null,{});
  assert.deepEqual(recovered,{name:fixture.name,loading:null,failed:null});await page.screenshot({path:join(out,`recovered-${lang}.png`)});
  results.push({lang,alert,requests,recovered});
  for(const request of held)if(!request.isInterceptResolutionHandled())await request.abort().catch(()=>{});
  await context.close();
 }
 await writeFile(join(out,'result.json'),JSON.stringify(results,null,2));console.log(JSON.stringify({out,results},null,2));
} finally {await browser?.close();await stack.stop();}
