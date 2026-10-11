// Compare stays inside its dock at wrapped and resized widths; keyboard use and import failures.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import puppeteer from 'puppeteer-core';
import {startStack,chromePath} from './scenarios/stack.mjs';
import {Session,auditInPage} from './scenarios/harness.mjs';
const out=resolve(process.env.FAIRBEAM_TEST_ARTIFACTS||join(tmpdir(),`fairbeam-compare-bounds-${Date.now()}`));
await mkdir(out,{recursive:true});const bad=join(out,'invalid-reference.csv');await writeFile(bad,'not a frequency or S-parameter table\n');
const stack=await startStack({log:console.log});let browser,page;const results=[];
const settle=()=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
async function bounds(lang,width,size){
 const boxes=await page.evaluate(()=>{
  const pop=document.querySelector('.cmp-pop'),dock=document.querySelector('.cmp').closest('.dock');
  return {pop:pop.getBoundingClientRect().toJSON(),dock:dock.getBoundingClientRect().toJSON(),height:innerHeight,focusInside:pop.contains(document.activeElement),scroll:pop.scrollWidth,client:pop.clientWidth};
 });
 assert.ok(boxes.pop.left>=Math.max(0,boxes.dock.left)+7,`${lang}/${width}/${size}: left ${boxes.pop.left}`);
 assert.ok(boxes.pop.right<=Math.min(width,boxes.dock.right)-7,`${lang}/${width}/${size}: right ${boxes.pop.right}`);
 assert.ok(boxes.pop.width<=340.5&&boxes.pop.top>=7&&boxes.pop.bottom<=boxes.height+1);
 assert.ok(boxes.scroll<=boxes.client+1);assert.ok(boxes.focusInside,'focus remains inside open comparison');
 const issues=await page.evaluate(auditInPage,'.cmp-pop');assert.deepEqual(issues,[]);
 results.push({lang,width,size,...boxes,issues});return boxes;
}
try{
 browser=await puppeteer.launch({headless:true,executablePath:await chromePath(),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
 for(const lang of ['en','tr']){
  const context=await browser.createBrowserContext();page=await context.newPage();await page.setViewport({width:1440,height:1000});
  await page.evaluateOnNewDocument(l=>{localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language:l}));window.__cmpObservers=0;window.__cmpResizeCalls=0;const Native=ResizeObserver;window.ResizeObserver=class extends Native {tracked=false;constructor(fn){super((...args)=>{if(this.tracked)window.__cmpResizeCalls++;fn(...args);});}observe(el,...args){if(!this.tracked&&el.matches('.cmp')){this.tracked=true;window.__cmpObservers++;}return super.observe(el,...args);}disconnect(){if(this.tracked){this.tracked=false;window.__cmpObservers--;}return super.disconnect();}};},lang);
  const s=new Session(page,{lang,scenario:'compare-bounds',url:stack.url});await page.goto(stack.url,{waitUntil:'domcontentloaded'});await s.wait('.home');
  await s.click('header.screen.examples');await s.wait('.example-picker-trigger');await s.waitFor(async()=>!!(await import('/src/state.ts')).bundle()?.results);await settle();
  for(const c of [{width:1440,size:'default'},{width:1440,size:'wide',key:'Home'},{width:1440,size:'narrow',key:'End'},{width:1024,size:'responsive'},{width:768,size:'responsive'},{width:390,size:'compact'},{width:768,height:600,size:'short'}]){
   await page.setViewport({width:c.width,height:c.height??1000});await settle();
   if(c.key)for(const side of ['left','right']){await s.clickSel(`.panel-resize-${side}`);await page.keyboard.press(c.key);}await settle();
   await page.focus('.cmp > button');await page.keyboard.press('Enter');await s.wait('.cmp-pop');await settle();
   await bounds(lang,c.width,c.size);assert.equal(await page.evaluate(()=>window.__cmpObservers),1);const calls=await page.evaluate(()=>window.__cmpResizeCalls);
   const check=await page.$('.cmp-pop input[type=checkbox]');await check.focus();const before=await check.evaluate(e=>e.checked);await page.keyboard.press('Space');assert.equal(await check.evaluate(e=>e.checked),!before);await page.keyboard.press('Space');assert.equal(await check.evaluate(e=>e.checked),before);
   await page.screenshot({path:join(out,`${lang}-${c.width}-${c.size}.png`)});
   // Resize while open: the observer must keep the same dialog and its keyboard focus in bounds.
   if(c.width===1024){await page.setViewport({width:768,height:1000});await settle();await bounds(lang,768,'open-resize');}
   await page.keyboard.press('Escape');await s.gone('.cmp-pop');assert.equal(await page.evaluate(()=>document.activeElement===document.querySelector('.cmp > button')),true);assert.equal(await page.evaluate(()=>window.__cmpObservers),0);assert.ok((await page.evaluate(()=>window.__cmpResizeCalls))-calls<20,'no ResizeObserver feedback loop');
  }
  await page.focus('.cmp > button');await page.keyboard.press('Enter');await s.wait('.cmp-pop');
  await(await page.$('.cmp input[type=file]')).uploadFile(bad);await s.wait('.cmp-pop [role=alert]');
  assert.ok((await page.$eval('.cmp-pop [role=alert]',e=>e.textContent)).trim().length>10);await page.keyboard.press('Escape');await s.gone('.cmp-pop');
  assert.deepEqual(s.consoleIssues,[]);await context.close();
 }
 await writeFile(join(out,'result.json'),JSON.stringify(results,null,2));console.log(`Compare bounds: ${results.length} EN/TR layout/resize cases plus keyboard selection, Escape and invalid import passed.`);
}catch(error){await page?.screenshot({path:join(out,'failure.png')});console.error(error);throw error;}finally{await writeFile(join(out,'result.json'),JSON.stringify(results,null,2));await browser?.close();await stack.stop();}
