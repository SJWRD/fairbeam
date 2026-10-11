// Real native Design -> queue -> S11/far-field UI smoke. Requires a local Elmer installation.
import assert from 'node:assert/strict';
import {mkdir,cp} from 'node:fs/promises';
import puppeteer from 'puppeteer-core';
import {startStack,chromePath} from './scenarios/stack.mjs';
import {Session} from './scenarios/harness.mjs';
const stack=await startStack({log:console.log});
const evidence=process.env.FAIRBEAM_EVIDENCE || '.impeccable/review/elmer-driven-run';
let browser,jobId;
try{
 await mkdir(evidence,{recursive:true});
 browser=await puppeteer.launch({executablePath:await chromePath(),headless:true,args:['--no-sandbox','--enable-unsafe-swiftshader']});
 for(const lang of ['en','tr']){
  const context=await browser.createBrowserContext(),page=await context.newPage();
  await page.setViewport({width:1280,height:960});
  await page.evaluateOnNewDocument(lang=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language:lang})),lang);
  const s=new Session(page,{lang,scenario:'elmer-antenna',url:stack.url});
  await page.goto(stack.url,{waitUntil:'networkidle0'});await s.wait('.home',undefined,{timeout:30000});
  await page.click('input[name="home-template"][value="patch"]');
  await s.fill(await s.field(await s.T('home.newProject.name')),`FEM patch ${lang}`,{blur:false});
  await s.click('home.newProject.create');await s.wait('.rb');
  await s.click('ribbon.tab.sim',{sel:'.rb-tab'});await s.click('ribbon.sim.run',{sel:'.rb-btn'});
  await page.select('.rd-solver select','elmer-driven');await s.wait('[data-testid="elmer-antenna-form"]');
  assert.ok(await page.$eval('[data-testid="elmer-antenna-form"] button[type=submit]',e=>e.disabled));
  for(const [key,val] of Object.entries({f_min_hz:'2.1',f_max_hz:'2.6',farfield_hz:'2.35',points:'3'}))
   await s.fill(await s.field(await s.T(`elmerAntenna.${key}`)),val);
  await page.click('[data-testid="elmer-antenna-form"] input[type=checkbox]');
  await page.screenshot({path:`${evidence}/setup-${lang}.png`});
  if(lang==='en'){
   await page.click('[data-testid="elmer-antenna-form"] button[type=submit]');
   await page.waitForSelector('.elmer-farfield-view canvas',{timeout:180000});
   const runs=await (await fetch(stack.apiUrl+'/api/research/runs')).json();
   const job=runs.runs.find(j=>j.research?.settings.analysis==='driven');
   assert.equal(job.status,'done');jobId=job.id;
   assert.deepEqual(job.result.frequency_hz,[2.1e9,2.35e9,2.6e9]);
  }else{
   await page.evaluate(async id=>{
    (await import('/src/runner/designRun.ts')).setRunDialogOpen(false);
    (await import('/src/runner/researchState.ts')).openResearch(id,'elmer',true);
   },jobId);
   await page.waitForSelector('.elmer-farfield-view canvas',{timeout:30000});
  }
  assert.equal(await page.$('#research-form'),null);
  assert.ok(await page.$('.research-chart svg'));
  await page.screenshot({path:`${evidence}/result-${lang}.png`});
  await page.setViewport({width:390,height:844});
  assert.ok(await page.$eval('.research-dialog',e=>e.scrollWidth<=e.clientWidth));
  const ports=await page.evaluate(async()=>JSON.parse(JSON.stringify((await import('/src/designer/store.ts')).draft.ports)));
  assert.equal(ports.length,1);assert.equal(ports[0].start[1],ports[0].stop[1]);
  assert.ok(!s.consoleIssues.some(x=>x.startsWith('page error')),s.consoleIssues.join('\n'));
  await context.close();
 }
 console.log('PASS: native patch S11 and 3D/cut directivity through Run; EN/TR; input port preserved.');
}finally{
 await cp(stack.dir+'/sim',evidence+'/sim',{recursive:true}).catch(()=>{});
 await browser?.close();await stack.stop();
}
