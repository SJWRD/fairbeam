// Known-value Smith tooltip and CSV checks using the real viewer. No solver or live workspace.
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile,readdir} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import puppeteer from 'puppeteer-core';
import {startStack,chromePath,root} from './scenarios/stack.mjs';
import {Session,auditInPage} from './scenarios/harness.mjs';
const out=resolve(process.env.FAIRBEAM_TEST_ARTIFACTS||join(tmpdir(),`fairbeam-smith-impedance-${Date.now()}`));
await mkdir(out,{recursive:true});
const fixture=JSON.parse(await readFile(join(root,'public/projects/branchline-coupler.json'),'utf8'));
fixture.name='Synthetic impedance boundary fixture';
fixture.ports=fixture.ports.slice(0,2).map((p,i)=>({...p,number:[2,5][i]})); fixture.ports[0].excite=true;fixture.ports[1].type='waveguide';
fixture.results.frequency=[1e9,2e9]; fixture.results.farfield=[];fixture.results.bands=[];
fixture.results.ports={
 '2':{s11_re:[1,-1],s11_im:[0,0],zin_re:[null,0],zin_im:[null,0],z_ref:50},
 '5':{s11_re:[0,0.5],s11_im:[0,0],zin_re:[600,1200],zin_im:[0,0],z_ref:500,z_ref_f:[600,400]},
};
fixture.results.sparams={ports:[1,2],port_numbers:[2,5],z_ref:[50,500],excited:[1,2],s:{'1,1':{re:[1,-1],im:[0,0]},'2,2':{re:[0,0.5],im:[0,0]}}};
const arrayFixture=JSON.parse(await readFile(join(root,'examples/synthetic/array2x1.json'),'utf8'));
arrayFixture.ports=arrayFixture.ports.map((p,i)=>({...p,number:[2,5][i]}));
arrayFixture.results.frequency=[1e9,3e9];
arrayFixture.results.sparams={ports:[1,2],port_numbers:[2,5],z_ref:[50,50],excited:[1,2],s:Object.fromEntries([['1,1',.1],['1,2',.2],['2,1',.3],['2,2',.5]].map(([k,v])=>[k,{re:[v,v],im:[0,0]}]))};
arrayFixture.results.element_patterns.forEach(set=>set.elements.forEach((e,i)=>e.port=[2,5][i]));
arrayFixture.results.farfield.forEach(ff=>{if(ff.port)ff.port=[2,5][ff.port-1];});
const stack=await startStack({log:console.log});let browser;const outcomes=[];
try{
 browser=await puppeteer.launch({headless:true,executablePath:await chromePath(),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
 for(const lang of ['en','tr']){
  const context=await browser.createBrowserContext();await context.overridePermissions(stack.url,['clipboard-read','clipboard-write','clipboard-sanitized-write']);
  const page=await context.newPage();await page.setViewport({width:1440,height:1000});
  const downloads=join(out,lang);await mkdir(downloads,{recursive:true});await(await page.createCDPSession()).send('Page.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});
  await page.evaluateOnNewDocument(language=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language})),lang);
  const s=new Session(page,{lang,scenario:'smith-impedance',url:stack.url});
  await page.goto(stack.url,{waitUntil:'domcontentloaded'});await s.wait('.home');await s.click('header.screen.examples');await s.wait('.example-picker-trigger');
  await s.waitFor(async()=>!!(await import('/src/state.ts')).bundle());
  await s.ev((b,m)=>{m.st.openBundle(m.v.validateBundle(b).bundle,'synthetic-smith.json');},fixture,{st:'/src/state.ts',v:'/src/lib/validate.ts'});
  await page.locator('.dock [data-tab="smith"]').click();
  // This fixture is purely real, so its SVG path has zero height; wait on the chart, not path bounds.
  await s.wait('.dock .chart svg');await page.waitForFunction(()=>document.querySelector('.dock .chart .c-line')?.getAttribute('d')?.includes('L'));
  const hover=async index=>{
   const pos=await page.$eval('.dock .chart .c-line',(el,index)=>{const points=[...el.getAttribute('d').matchAll(/[ML]([-\d.]+),([-\d.]+)/g)];const svg=el.closest('svg').getBoundingClientRect();return{x:svg.x+Number(points[index][1]),y:svg.y+Number(points[index][2])};},index);
   await page.mouse.move(pos.x,pos.y);await s.wait('.dock .chart-tip');
   return page.$eval('.dock .chart-tip',el=>el.textContent);
  };
  const openTip=await hover(0);assert.ok(openTip.includes(await s.T('chart.smith.openCircuit')));assert.ok(!openTip.includes('0.0 + j0.0 Ω'));
  await s.click('results.toolbar.copyData',{within:'.dock'});
  const openCopy=await page.evaluate(()=>navigator.clipboard.readText());const openRows=openCopy.trimEnd().split(/\r?\n/).map(r=>r.split('\t'));
  assert.equal(openRows[1][3],'');assert.equal(openRows[1][4],'');
  await hover(0);await page.screenshot({path:join(out,`open-${lang}.png`)});
  await page.locator('.dock [role=radiogroup] button[role=radio]:nth-child(2)').click();
  assert.equal(await page.$eval('.dock [role=radiogroup] button[role=radio]:nth-child(2)',el=>el.textContent.trim()),'S22 [P5]');
  const mappingTitle=await page.$eval('.dock [role=radiogroup] button[role=radio]:nth-child(2)',el=>el.title);assert.equal(mappingTitle,await s.T('sparams.portMapping',{mapping:'2 → 5'}));
  const waveTip1=await hover(0);assert.ok(waveTip1.includes('600.0 + j0.0 Ω')||waveTip1.includes('600,0 + j0,0 Ω'));
  assert.ok(waveTip1.includes('Zref'));
  assert.ok((await page.$eval('.dock .chart-note',el=>el.textContent)).includes(await s.T('chart.smith.frequencyReference')));
  const waveTip2=await hover(1);assert.ok(waveTip2.includes('1200.0 + j0.0 Ω')||waveTip2.includes('1200,0 + j0,0 Ω'));assert.ok(waveTip2.includes('400.0 Ω')||waveTip2.includes('400,0 Ω'));
  await s.click('results.toolbar.csvTitle',{within:'.dock',sel:'button'});
  let downloaded;for(let i=0;i<100;i++){downloaded=(await readdir(downloads)).find(f=>f.endsWith('.csv'));if(downloaded)break;await new Promise(r=>setTimeout(r,50));}
  assert.ok(downloaded,'CSV download completes');const csv=await readFile(join(downloads,downloaded),'utf8');
  assert.ok(csv.includes('Re S22 [P5]'));assert.ok(csv.includes('Re Zin model port 5 (matrix 2)'));assert.ok(csv.includes('1,0,0,600,0'));assert.ok(csv.includes('2,0.5,0,1200,0'));
  await hover(1);await page.screenshot({path:join(out,`waveguide-${lang}.png`)});
  const smithVisual=await page.evaluate(auditInPage,'.dock');
  await s.ev((b,m)=>m.st.openBundle(m.v.validateBundle(b).bundle,'synthetic-physical-array.json'),arrayFixture,{st:'/src/state.ts',v:'/src/lib/validate.ts'});
  await page.locator('.dock [data-tab="array"]').click();await s.wait('.array-table-wrap');
  const rows=()=>page.$$eval('.array-table-wrap tbody tr',rs=>rs.map(r=>[...r.querySelectorAll('td')].map(c=>c.textContent.trim())));
  const uniformRows=await rows();assert.equal(uniformRows[0][0],'P2');assert.equal(uniformRows[1][0],'P5');assert.match(uniformRows[0][4],/[-−]10[.,]5/);assert.match(uniformRows[1][4],/[-−]1[.,]9/);
  const amp=await s.T('array.amplitudeAria',{port:5}),phase=await s.T('array.phaseAria',{port:5});
  await page.locator(`input[aria-label="${amp}"]`).fill(String(20*Math.log10(.5)));await page.locator(`input[aria-label="${phase}"]`).fill('90');await page.locator('.array-table-wrap th:first-child').click();
  await s.waitFor(()=>{const r=document.querySelector('.array-table-wrap tbody tr');return /[-−]17[.,]0/.test(r?.textContent||'');});
  const unequalRows=await rows();assert.match(unequalRows[1][4],/[-−]2[.,]1/);
  const arrayCsv=await s.ev((_,m)=>m.csv.arrayWeightsCsv(m.st.bundle(),m.arr.arrayWeights(),m.arr.arraySet().f),null,{csv:'/src/export/csv.ts',st:'/src/state.ts',arr:'/src/lib/arrayStore.ts'});
  const arows=arrayCsv.trim().split(/\r?\n/).slice(1).map(r=>r.split(','));assert.equal(arows[0][0],'2');assert.equal(arows[1][0],'5');
  [[.1,.1],[.5,-.6]].forEach((expected,i)=>expected.forEach((v,j)=>assert.ok(Math.abs(Number(arows[i][5+j])-v)<1e-12)));
  await page.screenshot({path:join(out,`physical-array-${lang}.png`)});
  const arrayVisual=await page.evaluate(auditInPage,'.dock');
  const partial=structuredClone(arrayFixture);delete partial.results.sparams.s['1,2'];
  await s.ev((b,m)=>m.st.openBundle(m.v.validateBundle(b).bundle,'synthetic-partial-array.json'),partial,{st:'/src/state.ts',v:'/src/lib/validate.ts'});
  await s.wait('.array-block [role="status"]',await s.T('array.unavailableReflection'));
  const partialRows=await rows();assert.equal(partialRows[0][4],'—');assert.match(partialRows[1][4],/[-−]1[.,]9/);
  const partialCsv=await s.ev((_,m)=>m.csv.arrayWeightsCsv(m.st.bundle(),m.arr.arrayWeights(),m.arr.arraySet().f),null,{csv:'/src/export/csv.ts',st:'/src/state.ts',arr:'/src/lib/arrayStore.ts'});
  assert.ok(partialCsv.trim().split(/\r?\n/)[1].endsWith(',,,'),'unknown active reflection stays blank in CSV');
  await page.screenshot({path:join(out,`partial-array-${lang}.png`)});
  outcomes.push({lang,openTip,waveTip1,waveTip2,openCopy,csv,mappingTitle,uniformRows,unequalRows,arrayCsv,visual:smithVisual,arrayVisual});await context.close();
 }
 await writeFile(join(out,'result.json'),JSON.stringify(outcomes,null,2));console.log(JSON.stringify({out,outcomes},null,2));
}finally{await browser?.close();await stack.stop();}
