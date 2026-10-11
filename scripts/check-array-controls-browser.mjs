// Responsive Array steering controls: real slider, numeric and plane interactions, no solver.
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import puppeteer from 'puppeteer-core';
import {startStack,chromePath,root} from './scenarios/stack.mjs';
import {Session,auditInPage} from './scenarios/harness.mjs';
const out=resolve(process.env.FAIRBEAM_TEST_ARTIFACTS||join(tmpdir(),`fairbeam-array-layout-${Date.now()}`));
await mkdir(out,{recursive:true});
const fixture=JSON.parse(await readFile(join(root,'examples/synthetic/array2x1.json'),'utf8'));
const stack=await startStack({log:console.log});let browser,activePage;const results=[];
try {
 browser=await puppeteer.launch({headless:true,executablePath:await chromePath(),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
 for(const lang of ['en','tr']) {
  const context=await browser.createBrowserContext(),page=await context.newPage();activePage=page;await page.setViewport({width:1440,height:1000});
  await page.evaluateOnNewDocument(l=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language:l})),lang);
  const s=new Session(page,{lang,scenario:'array-layout',url:stack.url});
  await page.goto(stack.url,{waitUntil:'domcontentloaded'});await s.wait('.home');await s.click('header.screen.examples');await s.wait('.example-picker-trigger');
  await s.waitFor(async()=>!!(await import('/src/state.ts')).bundle());
  await s.ev((b,m)=>m.st.openBundle(m.v.validateBundle(b).bundle,'synthetic-array-layout.json'),fixture,{st:'/src/state.ts',v:'/src/lib/validate.ts'});
  await s.waitFor(async()=> (await import('/src/state.ts')).bundle()?.model.id === 'synthetic-array2x1' && (await import('/src/lib/arrayStore.ts')).hasArray(), null, {what:'synthetic array fixture ready'});
  await s.clickSel('.dock [data-tab="array"]');await s.wait('.array-steer');
  for(const c of [{width:1440,size:'default'},{width:1440,size:'wide',key:'Home'},{width:1440,size:'narrow',key:'End'},{width:1024,size:'responsive'},{width:768,size:'responsive'}]) {
   await page.setViewport({width:c.width,height:1000});
   if(c.key) for(const side of ['left','right']) {await page.locator(`.panel-resize-${side}`).click();await page.keyboard.press(c.key);}
   await page.$eval('.array-steer',el=>el.scrollIntoView({block:'center'}));
   await page.waitForFunction(()=>document.querySelector('.array-scan input[type=range]')?.clientWidth>=80);
   const geometry=await page.evaluate(()=>{
    const selectors=['.array-wrap','.array-panel','.array-block.stack','.array-steer','.array-scan','.array-scan input[type=range]','.array-scan .array-input'];
    return Object.fromEntries(selectors.map(k=>{const e=document.querySelector(k),r=e.getBoundingClientRect();return[k,{width:r.width,left:r.left,right:r.right,client:e.clientWidth,scroll:e.scrollWidth}];}));
   });
   for(const k of ['.array-block.stack','.array-steer','.array-scan']) assert.ok(geometry[k].scroll<=geometry[k].client+1,`${lang}/${c.width}/${c.size} ${k} overflows: ${JSON.stringify(geometry)}`);
   assert.ok(geometry['.array-scan input[type=range]'].width>=80);assert.ok(geometry['.array-scan .array-input'].width>=64);
   assert.ok(geometry['.array-scan'].right<=geometry['.array-steer'].right+1);
   // Pointer and keyboard both change the actual slider. NumberField commit and plane switches follow.
   const range=await page.$('.array-scan input[type=range]'),box=await range.boundingBox();
   await page.mouse.click(box.x+box.width*.65,box.y+box.height/2);
   const before=Number(await range.evaluate(e=>e.value));await range.focus();await page.keyboard.press('ArrowRight');
   const after=Number(await range.evaluate(e=>e.value));assert.equal(after,Math.min(Number(await range.evaluate(e=>e.max)),before+1));
   const angle=await s.T('array.scanAngleAria'),yz=await s.T('array.planeYzAria'),xz=await s.T('array.planeXzAria');
   await page.locator(`button[aria-label="${yz}"]`).click();
   assert.equal(await s.ev((_,m)=>m.a.scanPhi(),null,{a:'/src/lib/arrayStore.ts'}),90);
   await page.locator(`input[aria-label="${angle}"]`).fill('25');await page.locator(`button[aria-label="${xz}"]`).click();
   const state=await s.ev((_,m)=>({theta:m.a.scanTheta(),phi:m.a.scanPhi(),weights:[...m.a.arrayWeights()]}),null,{a:'/src/lib/arrayStore.ts'});
   assert.equal(state.theta,25);assert.equal(state.phi,0);assert.ok(state.weights.some(([,w])=>Math.abs(w.phaseDeg)>1));
   const issues=await page.evaluate(auditInPage,'.array-panel');assert.deepEqual(issues,[],`${lang}/${c.width}/${c.size}: ${issues.join('; ')}`);
   await page.screenshot({path:join(out,`${lang}-${c.width}-${c.size}.png`)});
   results.push({lang,...c,geometry,state,issues});
  }
  await context.close();
 }
 await writeFile(join(out,'result.json'),JSON.stringify(results,null,2));console.log(`Array controls: ${results.length} EN/TR viewport/dock cases passed; artifacts ${out}`);
}catch(error){await activePage?.screenshot({path:join(out,'failure.png')});console.error(error);throw error;}finally{await browser?.close();await stack.stop();}
