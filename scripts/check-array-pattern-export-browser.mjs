// Real Copy/CSV of the array+element overlay, checked against a separate numerical evaluation.
// No solver: the independent calculation uses the shipped embedded element fields and UI weights.
import assert from 'node:assert/strict';
import {mkdir,readFile,readdir,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import puppeteer from 'puppeteer-core';
import {elementPatterns} from '../src/lib/sparams.ts';
import {startStack,chromePath,root} from './scenarios/stack.mjs';
import {Session} from './scenarios/harness.mjs';

function independentDirectivity(fields,weights,halfSpace){
 const rad=Math.PI/180;
 const intensity=fields.theta.map((_,i)=>fields.phi.map((_,j)=>{
  const sum=[0,0,0,0];
  for(const element of fields.elements){
   const w=weights.find(([port])=>port===element.port)?.[1];if(!w)continue;
   const magnitude=10**(w.ampDb/20),real=magnitude*Math.cos(w.phaseDeg*rad),imag=magnitude*Math.sin(w.phaseDeg*rad);
   for(const [k,re,im] of [[0,element.eThetaRe[i][j],element.eThetaIm[i][j]],[2,element.ePhiRe[i][j],element.ePhiIm[i][j]]]){
    sum[k]+=real*re-imag*im;sum[k+1]+=real*im+imag*re;
   }
  }
  return sum.reduce((s,v)=>s+v*v,0);
 }));
 const limit=halfSpace?90:180;let integral=0;
 fields.theta.forEach((theta,i)=>{
  const low=i?(fields.theta[i-1]+theta)/2:0;
  const high=Math.min(limit,i+1<fields.theta.length?(theta+fields.theta[i+1])/2:180);
  const ring=high>low?Math.cos(low*rad)-Math.cos(high*rad):0;
  fields.phi.forEach((phi,j)=>{
   const previous=j?fields.phi[j-1]:fields.phi.at(-1)-360;
   const next=j+1<fields.phi.length?fields.phi[j+1]:fields.phi[0]+360;
   integral+=intensity[i][j]*ring*(next-previous)*rad/2;
  });
 });
 const raw=intensity.map(row=>row.map(u=>10*Math.log10(Math.max(4*Math.PI*u/integral,1e-30))));
 const peak=Math.max(...raw.filter((_,i)=>fields.theta[i]<=limit+1e-4).flat());
 return raw.map(row=>row.map(v=>Math.max(v,peak-60)));
}
const out=resolve(process.env.FAIRBEAM_TEST_ARTIFACTS||join(tmpdir(),`fairbeam-array-export-${Date.now()}`));
await mkdir(out,{recursive:true});
const bundle=JSON.parse(await readFile(join(root,'public/projects/patch-array-2x1.json'),'utf8'));
const fields=elementPatterns(bundle)[0],element=bundle.results.farfield.find(ff=>ff.port===2&&ff.f===fields.f);
const stack=await startStack({log:console.log});let browser;const outcomes=[];
try{
 browser=await puppeteer.launch({headless:true,executablePath:await chromePath(),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
 for(const lang of ['en','tr']){
  const context=await browser.createBrowserContext();await context.overridePermissions(stack.url,['clipboard-read','clipboard-write','clipboard-sanitized-write']);
  const page=await context.newPage();await page.setViewport({width:1440,height:1000});
  await page.evaluateOnNewDocument(language=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language})),lang);
  const s=new Session(page,{lang,scenario:'array-export',url:stack.url});
  const downloads=join(out,lang);await mkdir(downloads,{recursive:true});
  await(await page.createCDPSession()).send('Page.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});
  await page.goto(stack.url,{waitUntil:'domcontentloaded'});await s.wait('.home');await s.click('header.screen.examples');await s.wait('.example-picker-trigger');
  await page.locator('.example-picker-trigger').click();await s.wait('.example-picker-pop input');await page.type('.example-picker-pop input','Patch array 2');await page.keyboard.press('Enter');
  await s.waitFor(async()=>(await import('/src/state.ts')).source()==='patch-array-2x1.json');
  await s.wait('.dock [data-tab="array"]');await page.locator('.dock [data-tab="array"]').click();await s.wait('.array-scan');
  await s.fill('.array-scan .array-input','35');
  const weights=await s.ev((_,m)=>[...m.a.arrayWeights()],null,{a:'/src/lib/arrayStore.ts'});
  const expected=independentDirectivity(fields,weights,!!bundle.half_space);
  await page.locator('.dock [data-tab="pattern"]').click();await(await s.wait('.dock .freq-chips button','P2',{exact:true})).click();
  const copy=async()=>{await page.evaluate(()=>navigator.clipboard.writeText('waiting'));await s.click('results.toolbar.copyData',{within:'.dock'});await page.waitForFunction(async()=>(await navigator.clipboard.readText()).startsWith('Pattern\t'));return page.evaluate(()=>navigator.clipboard.readText());};
  const text=await copy();const table=text.trimEnd().split(/\r?\n/).map(row=>row.split('\t'));
  assert.deepEqual(table[0],['Pattern','Port','f (GHz)','theta (deg)','phi (deg)','Directivity (dBi)']);
  const arrayRows=table.slice(1).filter(row=>row[0]==='Array'),elementRows=table.slice(1).filter(row=>row[0]==='Element P2');
  assert.equal(arrayRows.length,fields.theta.length*fields.phi.length);assert.equal(elementRows.length,element.theta.length*element.phi.length);
  let maxError=0;
  arrayRows.forEach((row,k)=>{
   const i=Math.floor(k/fields.phi.length),j=k%fields.phi.length;
   assert.equal(row[1],'');assert.equal(Number(row[2]),fields.f/1e9);assert.equal(Number(row[3]),fields.theta[i] || 0);assert.equal(Number(row[4]),fields.phi[j] || 0);
   maxError=Math.max(maxError,Math.abs(Number(row[5])-expected[i][j]));
  });
  assert.ok(maxError<1e-10,`independent synthesis agrees: ${maxError}`);
  elementRows.forEach((row,k)=>{const i=Math.floor(k/element.phi.length),j=k%element.phi.length;assert.equal(row[1],'2');assert.equal(Number(row[2]),element.f/1e9);assert.equal(Number(row[5]),element.directivity_dbi[i][j] || 0);});
  const before=new Set(await readdir(downloads));await s.click('results.toolbar.csvTitle',{within:'.dock',sel:'button'});let csvFile;
  for(let i=0;i<100;i++){csvFile=(await readdir(downloads)).find(name=>!before.has(name)&&name.endsWith('.csv'));if(csvFile)break;await new Promise(r=>setTimeout(r,100));}
  assert.ok(csvFile,'browser downloads a CSV');const csv=await readFile(join(downloads,csvFile),'utf8');
  assert.equal(csv.charCodeAt(0),0xfeff,'toolbar CSV begins with a UTF-8 BOM');
  assert.deepEqual(csv.slice(1).trimEnd().split(/\r?\n/).map(row=>row.split(',')),table,'CSV and clipboard contain the same full precision overlay');
  // The radio selects the 3D view only; both plotted curves and their exported grids remain.
  await s.click('dock.results.element',{params:{port:2},sel:'button[role=radio]',within:'.dock'});
  assert.equal(await copy(),text,'3D Element toggle cannot discard the plotted Array trace');
  await s.click('farfield.arrayTag',{sel:'button[role=radio]',within:'.dock'});
  // Changing the cut plane does not change the full spherical grids exported by this command.
  await(await s.wait('.dock button[role=radio]','φ 90°',{exact:true})).click();assert.equal(await copy(),text);
  await page.screenshot({path:join(out,`array-${lang}.png`)});await writeFile(join(out,`array-${lang}.tsv`),text);
  outcomes.push({lang,arrayRows:arrayRows.length,elementRows:elementRows.length,arrayFirst:Number(arrayRows[0][5]),elementFirst:Number(elementRows[0][5]),maxIndependentError:maxError,weights,csvFile});
  await context.close();
 }
 await writeFile(join(out,'result.json'),JSON.stringify(outcomes,null,2));console.log(JSON.stringify({out,outcomes},null,2));
}finally{await browser?.close();await stack.stop();}
