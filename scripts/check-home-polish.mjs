// Isolated real UI: no user documents, no solver and no native filesystem deletion.
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {join, resolve} from 'node:path';
import {tmpdir} from 'node:os';
import puppeteer from 'puppeteer-core';
import {startStack, chromePath} from './scenarios/stack.mjs';
import {Session, auditInPage} from './scenarios/harness.mjs';

const artifacts = resolve(process.env.FAIRBEAM_TEST_ARTIFACTS || join(tmpdir(), 'fairbeam-home-polish'));
await mkdir(artifacts, {recursive:true});
const stack = await startStack({log:console.log});
let browser;
try {
 browser = await puppeteer.launch({headless:true, executablePath:await chromePath(), args:['--no-sandbox','--enable-unsafe-swiftshader']});
 for (let i=0;i<8;i++) {
  const response=await fetch(`${stack.apiUrl}/api/designs`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:`polish_${i}`,name:`Polish ${i}`,template:'empty'})});
  assert.ok(response.ok, await response.text());
 }
 for (const [lang,width] of [['en',1280],['tr',768]]) {
  const context=await browser.createBrowserContext(), page=await context.newPage();
  await page.setViewport({width,height:1000});
  await page.evaluateOnNewDocument(language=>{
   if(!localStorage.getItem('fairbeam.generalSettings')) localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language}));
  },lang);
  const s=new Session(page,{lang,scenario:'home-polish',url:stack.url});
  await page.goto(stack.url,{waitUntil:'domcontentloaded'});await s.wait('[data-home-design]');
  assert.equal((await page.$$('[data-home-design]')).length,5);
  await s.click('home.designs.showMore');assert.equal((await page.$$('[data-home-design]')).length,8);
  await s.click('home.designs.showLess');assert.equal((await page.$$('[data-home-design]')).length,5);
  await s.fill('.home-design-search input','Polish 0');assert.equal((await page.$$('[data-home-design]')).length,1);
  await s.fill('.home-design-search input','');assert.equal((await page.$$('[data-home-design]')).length,5);
  assert.ok((await page.$$eval('.home-example-row',rows=>rows.every(row=>{
   const item=row.querySelector('.home-item'),copy=row.querySelector('[data-example-copy]');
   return item && copy && copy.textContent.trim().length>3 && copy.getBoundingClientRect().top>=item.getBoundingClientRect().bottom;
  }))));
  await page.screenshot({path:join(artifacts,`home-${lang}.png`),fullPage:true});
  if(width<900) await s.clickSel('.header-more-trigger');
  await s.click('header.settings');await s.wait('#gs-title');await s.clickSel('#gs-tab-appearance');
  for (const preset of ['forest','academic']) {
   await s.click(`settings.appearance.preset.${preset}`);
   assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),preset==='forest'?'dark':'light');
   const issues=await page.evaluate(auditInPage,'.gs-panel');
   assert.deepEqual(issues,[],`${preset}/${lang} layout`);
   await page.screenshot({path:join(artifacts,`${preset}-${lang}.png`)});
  }
  await s.press('Escape');await page.reload({waitUntil:'domcontentloaded'});await s.wait('.home');
  assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'light');
  assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('fairbeam.generalSettings')).accent),'blue');
  // Native transport is mocked, but deletion still uses the real isolated workspace API.
  await s.ev(async (_,m)=>{
   await m.r.refreshModels();
   const entry=m.r.models().find(x=>x.key==='polish_7');
   window.__archiveCalls=[];
   window.__TAURI_INTERNALS__={invoke:async(command,args)=>{
    if(command!=='native_document_archive') return null;
    window.__archiveCalls.push(args);
    const response=await fetch(`/api/designs/${args.modelId}/delete`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({backup_scope:args.backupScope})});
    if(!response.ok) throw new Error(await response.text());
    return {...await response.json(),recovery_path:'Fixture/Fairbeam Deleted/recovery'};
   }};
   m.r.setModels(m.r.models().map(x=>x.key===entry.key?{...x,document:{id:'fixture',path:'Fixture/project.fb',results_directory:'project.results'}}:x));
  },null,{r:'/src/runner/store.ts'});
  await s.fill('.home-design-search input','Polish 7');
  await s.clickSel('#home-delete-polish_7');
  assert.equal(await page.$eval('.home-remove-files input',e=>e.checked),false);
  await page.screenshot({path:join(artifacts,`remove-${lang}.png`)});
  await s.clickSel('#home-keep-polish_7');
  assert.equal(await page.evaluate(()=>window.__archiveCalls.length),0);
  await s.clickSel('#home-delete-polish_7');await s.clickSel('.home-remove-files input');
  await s.clickSel('.home-del-btn');await s.wait('#home-delete-note');
  assert.equal(await page.evaluate(()=>window.__archiveCalls[0].path),'Fixture/project.fb');
  assert.equal(await page.evaluate(()=>window.__archiveCalls[0].resultsDirectory),'project.results');
  assert.ok((await page.$eval('#home-delete-note',e=>e.textContent)).includes('Fairbeam Deleted'));
  // Restore only the isolated fixture for the next language.
  await fetch(`${stack.apiUrl}/api/designs`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:'polish_7',name:'Polish 7',template:'empty'})});
  await context.close();
 }
 console.log(`Home polish passed: pagination/search, labelled example actions, theme application/persistence, EN/TR layout. Screenshots: ${artifacts}`);
} finally {await browser?.close();await stack.stop();}
