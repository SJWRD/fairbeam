// Import real magnitude-only CSV through the comparison UI. No solver; copied workspace only.
import assert from 'node:assert/strict';
import {mkdir,writeFile,readdir} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import puppeteer from 'puppeteer-core';
import {startStack,chromePath} from './scenarios/stack.mjs';
import {Session,auditInPage} from './scenarios/harness.mjs';
const out=resolve(process.env.FAIRBEAM_TEST_ARTIFACTS||join(tmpdir(),`fairbeam-magnitude-reference-${Date.now()}`));
await mkdir(out,{recursive:true});
await writeFile(join(out,'old.s2p'),'# GHz S RI R 50\n2 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8\n3 0.2 0.1 0.4 0.3 0.6 0.5 0.8 0.7\n');
await writeFile(join(out,'magnitude.csv'),'Frequency (GHz),S11 Magnitude (dB)\n2,-10\n3,-20');
await writeFile(join(out,'phase.csv'),'Frequency (GHz),S11 Real,S11 Imaginary\n2,0.223606797749979,0.223606797749979\n3,0.07071067811865475,-0.07071067811865475');
const stack=await startStack({log:console.log});let browser;const outcomes=[];
try{
 browser=await puppeteer.launch({headless:true,executablePath:await chromePath(),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
 for(const lang of ['en','tr']){
  const context=await browser.createBrowserContext();await context.overridePermissions(stack.url,['clipboard-read','clipboard-write','clipboard-sanitized-write']);
  const page=await context.newPage();await page.setViewport({width:1280,height:1000});
  page.on('pageerror',error=>console.error(`${lang} page error: ${error.message}`));
  page.on('requestfailed',request=>console.error(`${lang} request failed: ${request.url()} ${request.failure()?.errorText}`));
  await page.evaluateOnNewDocument(language=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language})),lang);
  const s=new Session(page,{lang,scenario:'magnitude-reference',url:stack.url});
  const downloads=join(out,lang);await mkdir(downloads,{recursive:true});
  await(await page.createCDPSession()).send('Page.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});
  await page.goto(stack.url,{waitUntil:'domcontentloaded'});await s.wait('.home');await s.click('header.screen.examples');await s.wait('.example-picker-trigger');
  await page.locator('.example-picker-trigger').click();await s.wait('.example-picker-pop input');await page.type('.example-picker-pop input','Inset');await page.keyboard.press('Enter');
  await s.waitFor(async()=>(await import('/src/state.ts')).source()==='inset-patch.json');
  await page.locator('.dock [data-tab="reflection"]').click();
  const upload=async filename=>{await s.click('results.toolbar.compare',{within:'.dock'});await s.wait('.cmp-pop');await(await page.$('.cmp input[type=file]')).uploadFile(join(out,filename));await s.waitFor(async name=>(await import('/src/compare/store.ts')).reference()?.reference.files.at(-1)===name,filename);};
  // Retain a complex format while adding a magnitude-only sweep after a full matrix.
  // The new reference replaces that matrix; neither the old phase nor its old magnitude survives.
  await page.select('.dock .result-format','db_phase');
  await upload('old.s2p');await page.keyboard.press('Escape');
  await upload('magnitude.csv');await page.keyboard.press('Escape');
  await page.select('.dock .result-format','db_phase');
  await s.wait('.dock [role=status]',await s.T('compare.phaseUnavailable'));
  const groups=await s.ev((_,m)=>m.s.compareSParamQuantities(m.s.traces(m.st.bundle(),m.c.compareBundles()),[[1,1]],'db_phase').map(g=>({key:g.key,labels:g.series.map(s=>s.label)})),null,{s:'/src/compare/series.ts',st:'/src/state.ts',c:'/src/compare/store.ts'});
  assert.ok(groups.find(g=>g.key==='db').labels.some(l=>l.includes('magnitude.csv')));
  assert.ok(!groups.find(g=>g.key==='phase').labels.some(l=>l.includes('magnitude.csv')));
  // Both one-port and multi-port Smith use the same explicit phase-availability contract.
  assert.equal(await s.ev((_,m)=>m.sp.portReflection(m.c.reference(),1),null,{sp:'/src/components/SParamView.tsx',c:'/src/compare/store.ts'}),null);
  await page.evaluate(()=>navigator.clipboard.writeText('unchanged clipboard'));
  const referenceLabel=await s.ev((_,m)=>m.c.reference().reference.label,null,{c:'/src/compare/store.ts'});
  const reason=await s.T('results.data.phaseRequired',{label:referenceLabel});
  await s.click('results.toolbar.copyData',{within:'.dock'});
  await s.waitFor(async reason=>(await import('/src/lib/toast.ts')).toasts().some(t=>t.text.includes(reason)),reason);
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'unchanged clipboard');
  await s.click('results.toolbar.csvTitle',{within:'.dock',sel:'button'});
  await s.waitFor(async reason=>(await import('/src/lib/toast.ts')).toasts().some(t=>t.text.includes(reason)),reason);
  await s.click('results.toolbar.touchstoneTitle',{within:'.dock',sel:'button'});
  const phaseReason=await s.T('results.data.phaseRequired',{label:referenceLabel});
  await s.waitFor(async text=>(await import('/src/lib/toast.ts')).toasts().some(t=>t.text.includes(text)),phaseReason);
  assert.deepEqual(await readdir(downloads),[],'blocked CSV/Touchstone offers no misleading file');
  await page.screenshot({path:join(out,`reference-blocked-${lang}.png`)});
  await page.select('.dock .result-format','db');await s.click('results.toolbar.copyData',{within:'.dock'});await s.wait('.dock-copy.copied');
  const magnitude=await page.evaluate(()=>navigator.clipboard.readText());assert.ok(magnitude.includes('magnitude.csv'));assert.ok(!magnitude.includes('∠'));
  const magnitudeRows=magnitude.trim().split(/\r?\n/).map(r=>r.split('\t'));
  const dbColumn=magnitudeRows[0].indexOf('|S11| (dB)'),referenceDb=magnitudeRows.slice(1).filter(r=>r[0]==='ref:reference');
  assert.ok(dbColumn>=0);assert.equal(referenceDb.length,2);
  assert.ok(Math.abs(Number(referenceDb[0][dbColumn])+10)<1e-10);assert.ok(Math.abs(Number(referenceDb[1][dbColumn])+20)<1e-10);
  assert.ok(!magnitudeRows[0].some(h=>/S12|S21|S22/.test(h)),'old network columns must not survive Add File');
  await upload('phase.csv');await s.waitFor(async()=>{const m=await import('/src/compare/store.ts');return m.reference()?.reference.phaseKnown===true||!!m.refError();});
  assert.equal(await s.ev((_,m)=>m.c.refError(),null,{c:'/src/compare/store.ts'}),null);await page.keyboard.press('Escape');
  await page.select('.dock .result-format','db_phase');await s.click('results.toolbar.copyData',{within:'.dock'});
  await page.waitForFunction(async()=>!(await navigator.clipboard.readText()).includes('unchanged clipboard')&&(await navigator.clipboard.readText()).includes('∠S11'));
  const completed=await page.evaluate(()=>navigator.clipboard.readText());
  const rows=completed.trim().split(/\r?\n/).map(r=>r.split('\t')),phaseColumn=rows[0].indexOf('∠S11 (deg)');
  assert.ok(phaseColumn>=0);const referenceRows=rows.slice(1).filter(r=>r[0]==='ref:reference');assert.equal(referenceRows.length,2);
  assert.ok(Math.abs(Number(referenceRows[0][phaseColumn])-45)<1e-10);assert.ok(Math.abs(Number(referenceRows[1][phaseColumn])+45)<1e-10);
  const plotted=await s.ev((_,m)=>m.s.compareSParamQuantities(m.s.traces(m.st.bundle(),m.c.compareBundles()),[[1,1]],'db_phase').find(g=>g.key==='phase').series.find(s=>s.id==='cmp-1-1,1:phase'),null,{s:'/src/compare/series.ts',st:'/src/state.ts',c:'/src/compare/store.ts'});
  assert.ok(Math.abs(plotted.y[0]-45)<1e-10);assert.ok(Math.abs(plotted.y.at(-1)+45)<1e-10);
  await s.gone('.dock [role=status]',await s.T('compare.phaseUnavailable'));
  await page.screenshot({path:join(out,`reference-replaced-${lang}.png`)});
  outcomes.push({lang,groups,clipboardBlocked:true,downloadsBlocked:true,magnitudePreserved:true,oldMatrixReplaced:true,phaseImportRestoresMeasuredData:true,visual:await page.evaluate(auditInPage,'.dock')});
  await context.close();
 }
 await writeFile(join(out,'result.json'),JSON.stringify(outcomes,null,2));console.log(JSON.stringify({out,outcomes},null,2));
}catch(error){
 if(browser)for(const [index,page] of (await browser.pages()).entries()){
  await page.screenshot({path:join(out,`failure-${index}.png`)}).catch(()=>{});
  await writeFile(join(out,`failure-${index}.txt`),await page.evaluate(()=>document.body.innerText).catch(()=>''));
 }
 throw error;
}finally{await browser?.close();await stack.stop();}
