// Isolated 40-run table/tree loading and failed-read Retry regression. No solver.
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import puppeteer from 'puppeteer-core';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {startStack,chromePath,root} from './scenarios/stack.mjs';
import {Session} from './scenarios/harness.mjs';
const out=process.env.FAIRBEAM_TEST_ARTIFACTS||join(tmpdir(),'fairbeam-run-metadata-'+Date.now());await mkdir(out,{recursive:true});
const template=JSON.parse(await readFile(root+'/public/projects/patch-array-4x1.json','utf8'));
const stack=await startStack({log:console.log});let browser;
try{
 browser=await puppeteer.launch({headless:true,executablePath:await chromePath(),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
 console.log('Owned stack PIDs',stack.pids,'Chrome',browser.process().pid);
 const results=[];for(const lang of ['en','tr']){const context=await browser.createBrowserContext();const page=await context.newPage();await page.setViewport({width:1440,height:1000});
 await page.evaluateOnNewDocument(language=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language})),lang);
 const s=new Session(page,{lang,scenario:'metadata-audit',url:stack.url});await page.goto(stack.url,{waitUntil:'domcontentloaded'});await s.wait('.home');
 await s.fill(await s.field(await s.T('home.newProject.name')),`Metadata audit ${lang}`,{blur:false});await s.click('home.newProject.create');await s.wait('.rb');
 const own=await s.store((_,m)=>JSON.parse(JSON.stringify(m.s.file())));
 let held=[],mode='hold',attempts=0,payloads=[],phase='01';await page.setRequestInterception(true);page.on('request',r=>{
  if(/\/audit-run-\d+\.json$/.test(new URL(r.url()).pathname)){attempts++;if(mode==='hold')held.push(r);else if(mode==='success')void r.respond({status:200,contentType:'application/json',body:payloads[Number(/audit-run-(\d+)/.exec(r.url())[1])]});else void r.respond({status:503,contentType:'application/json',body:'{}'});return;}void r.continue();
 });
 const row=(i)=>({file:`audit-run-${i}.json`,model:own.design.model.id,name:`Sweep point ${i}`,created:`2026-10-11T${phase}:${String(i).padStart(2,'0')}:00Z`,engine:'Metal',simulated:true,bands:[]});
 const setRows=async n=>s.ev(rows=>import('/src/state.ts').then(m=>m.setIndex(rows)),Array.from({length:n},(_,i)=>row(i)),{});
 mode='fail';await setRows(8);await s.click('dock.tab.runs',{sel:'.rdk [role=tab]'});await s.sleep(1200);
 const failure=await page.$eval('.rdk-runs-tab',n=>({text:n.innerText,buttons:[...n.querySelectorAll('button')].map(b=>b.textContent),rows:n.querySelectorAll('tbody tr').length}));
 await page.screenshot({path:out+`/all-failed-${lang}.png`});const failAttempts=attempts;assert.equal(failure.rows,0);assert.equal(failure.buttons.length,1,'failed table offers Retry');assert.ok(failure.text.includes(await s.T('runDock.runs.readFailed',{count:8})));
 const makePayloads=()=>Array.from({length:40},(_,i)=>{const b=structuredClone(template);b.model.id=own.design.model.id;b.name=`Sweep point ${i}`;b.created=row(i).created;b.model.params=[{key:'length',label:'Length',value:20+i/10,unit:'mm'}];return JSON.stringify(b);});
 payloads=makePayloads();mode='success';await s.click('common.retry',{sel:'.rdk-runs-tab button'});await s.waitFor(()=>document.querySelectorAll('.rdk-runs tbody tr').length===8);await s.waitFor(()=>!document.querySelector('.rdk-runs-tab > .status-block'));
 phase='02';
 // A realistic 40-point sweep: same shipped array geometry/results, distinct point parameters.
 mode='hold';held=[];attempts=0;await setRows(40);await s.sleep(600);
 const startedBeforeAnyResponse=held.length;const perFile={};for(const request of held){const name=new URL(request.url()).pathname;perFile[name]=(perFile[name]??0)+1;}console.log('Held request counts',startedBeforeAnyResponse,JSON.stringify(perFile));assert.equal(startedBeforeAnyResponse,4,'bounded metadata reads');
 payloads=makePayloads();
 const bytes=payloads.reduce((n,p)=>n+Buffer.byteLength(p),0),began=Date.now();
 mode='success';for(const request of held.splice(0)){const i=Number(/audit-run-(\d+)/.exec(request.url())[1]);await request.respond({status:200,contentType:'application/json',body:payloads[i]});}
 await s.waitFor(()=>document.querySelectorAll('.rdk-runs tbody tr').length===40);
 const timeToAllRowsMs=Date.now()-began;
 const retained=await s.ev(async()=>{const m=await import('/src/designer/runResults.ts');const runs=m.designRuns().map(r=>m.designRunBundle(r.file)).filter(Boolean);return {bundles:runs.length,geometry: runs.filter(b=>b.geometry).length,farfieldEntries:runs.reduce((n,b)=>n+(b.results?.farfield?.length??0),0),jsonBytes:runs.reduce((n,b)=>n+new TextEncoder().encode(JSON.stringify(b)).length,0)};},null,{});
 await page.screenshot({path:out+`/40-runs-${lang}.png`});assert.equal(attempts,40,'one full JSON read per run');
 const result={lang,requests:attempts,retryRecovered:true,fixture:'shipped patch-array-4x1; 40 synthetic sweep points, no simulations',failure:{...failure,attempts:failAttempts},startedBeforeAnyResponse,perFile,totalJsonBytes:bytes,timeToAllRowsMs,retained};
 results.push(result);console.log(JSON.stringify(result,null,2));await context.close();}
 await writeFile(out+'/result.json',JSON.stringify(results,null,2));
}finally{await browser?.close();await stack.stop();}
