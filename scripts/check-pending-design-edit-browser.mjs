// Actual user edits survive a target-design response arriving after navigation started.
// Isolated empty designs only; no solver. FAIRBEAM_PYTHON selects the local run-server runtime.
import assert from 'node:assert/strict';
import puppeteer from 'puppeteer-core';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {startStack,chromePath} from './scenarios/stack.mjs';
import {Session} from './scenarios/harness.mjs';
const out=process.env.FAIRBEAM_SCREENSHOT_DIR??join(tmpdir(),'fairbeam-pending-design-edit');
mkdirSync(out,{recursive:true});
let stack,browser;
const cleanup=async()=>{await browser?.close();browser=undefined;await stack?.stop();stack=undefined;};
for(const sig of ['SIGINT','SIGTERM'])process.once(sig,async()=>{await cleanup();process.exit(130);});
const results=[];
try{
 stack=await startStack({log:console.log});console.log('Owned stack PIDs:',stack.pids.join(', '));
 browser=await puppeteer.launch({headless:true,executablePath:await chromePath(),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
 for(const lang of ['en','tr']){
  const a=`edit_a_${lang}`,b=`edit_b_${lang}`,edited=`Edited ${lang} while the other design loads`;
  for(const id of [a,b]){const response=await fetch(stack.apiUrl+'/api/designs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,name:id,template:'empty'})});assert.equal(response.status,201,await response.text());}
  const context=await browser.createBrowserContext(),page=await context.newPage();let cdp,held;
  try{
   await page.setViewport({width:1280,height:800});page.setDefaultTimeout(20000);
   await page.evaluateOnNewDocument(language=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language})),lang);
   const s=new Session(page,{lang,scenario:'pending-design-edit',url:stack.url});
   await page.goto(stack.url,{waitUntil:'domcontentloaded'});await s.wait('.home');
   await page.locator(`[data-home-design="${a}"] .home-item`).click();
   await s.waitFor(async id=>{const m=await import('/src/designer/store.ts');return !m.loading()&&m.file()?.id===id;},a);
   cdp=await page.createCDPSession();let ready;const waiting=new Promise(resolve=>ready=resolve);
   await cdp.send('Fetch.enable',{patterns:[{urlPattern:`${stack.url}api/designs/${b}`,requestStage:'Response'}]});
   cdp.on('Fetch.requestPaused',event=>{held=event;ready();});
   let targetReads=0;page.on('request',r=>{if(r.url()===`${stack.url}api/designs/${b}`&&r.method()==='GET')targetReads++;});
   await s.click('header.screen.start',{sel:'.mode-switch button'});await s.wait('.home');
   await page.locator(`[data-home-design="${b}"] .home-item`).click();
   let timer;try{await Promise.race([waiting,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('target design response not intercepted')),15000))]);}finally{clearTimeout(timer);}
   await s.wait('.rb');assert.equal(await s.store((_,m)=>m.s.loading()),true);
   await s.fill(await s.field(await s.T('props.name')),edited);
   const before=await s.store((_,m)=>({file:m.s.file(),text:JSON.stringify(m.s.draft),history:m.s.historyMark()}));
   assert.equal(before.file.id,a);assert.equal(await s.store((_,m)=>m.s.dirty()),true);
   await cdp.send('Fetch.continueResponse',{requestId:held.requestId});held=null;await cdp.send('Fetch.disable');
   await s.waitFor(async()=>!(await import('/src/designer/store.ts')).loading());
   await s.wait('.dz-msg',await s.T('store.openCancelledByEdit'));
   const after=await s.store((_,m)=>({file:m.s.file(),text:JSON.stringify(m.s.draft),history:m.s.historyMark()}));
   assert.deepEqual(after,before,'opening another design must preserve the edited file, draft and Undo');
   assert.equal(await s.ev((_,m)=>m.runner.modelKey(),null,{runner:'/src/runner/store.ts'}),a);
   assert.equal(await s.store((_,m)=>m.s.defaultDesignerIO.navigationTarget()),null);
   await s.store((_,m)=>m.s.syncToModel());await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
   assert.equal(targetReads,1,'selection synchronization cannot reopen the cancelled target');
   await s.waitFor(({id,name})=>Object.keys(localStorage).filter(k=>k.startsWith('fairbeam:draft:v3:')).some(k=>{const d=JSON.parse(localStorage.getItem(k));return d.design?.model?.name===name&&d.design?.model?.id===id.replaceAll('_','-');}),{id:a,name:edited});
   await page.screenshot({path:join(out,`retained-edit-${lang}.png`)});
   await s.click('ribbon.tab.home',{sel:'.rb-tab'});await s.click('ribbon.home.undo',{sel:'.rb-btn'});
   assert.equal(await s.store((_,m)=>m.s.draft.model.name),a);
   await s.click('ribbon.home.redo',{sel:'.rb-btn'});assert.equal(await s.store((_,m)=>m.s.draft.model.name),edited);
   await s.click('common.save',{sel:'.rb-btn'});await s.waitFor(async()=>{const m=await import('/src/designer/store.ts');return !m.saving()&&!m.dirty();});
   await s.click('header.screen.start',{sel:'.mode-switch button'});await s.wait('.home');
   await page.locator(`[data-home-design="${b}"] .home-item`).click();
   await s.waitFor(async id=>{const m=await import('/src/designer/store.ts');return !m.loading()&&m.file()?.id===id;},b);
   assert.equal(targetReads,2,'a later explicit open still succeeds');
   results.push({lang,retainedDraft:true,retainedUndo:true,backupWritten:true,selectionRestored:true,noAutomaticReopen:true,explicitLaterOpen:true});
   console.log(`${lang}: held GET + actual Name edit retained; warning, backup, Undo/Redo, selection and later explicit open passed`);
  }finally{if(held)await cdp.send('Fetch.continueResponse',{requestId:held.requestId});held=null;await cdp?.detach();await context.close();}
 }
}finally{writeFileSync(join(out,'result.json'),JSON.stringify(results,null,2));await cleanup();}
