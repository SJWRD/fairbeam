// Focused real-UI regression: gallery pattern selection must survive Copy data and CSV.
// Starts an isolated stack over copied example bundles; no solver is launched.
// FAIRBEAM_PYTHON / FAIRBEAM_CHROME select local runtimes. Optional FAIRBEAM_TEST_ARTIFACTS.
import assert from 'node:assert/strict';
import { readFile, mkdir, readdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import puppeteer from 'puppeteer-core';
import { chromePath, root, startStack } from './scenarios/stack.mjs';
import { Session } from './scenarios/harness.mjs';
const artifacts = resolve(process.env.FAIRBEAM_TEST_ARTIFACTS || join(tmpdir(), `fairbeam-pattern-selection-${Date.now()}`));
await mkdir(artifacts, {recursive:true});
const stack = await startStack({log:console.log});
let browser;
const outcomes = [];
try {
  browser = await puppeteer.launch({headless:true, executablePath:await chromePath(), args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
  for (const lang of ['en','tr']) {
    const context = await browser.createBrowserContext();
    await context.overridePermissions(stack.url, ['clipboard-read','clipboard-write','clipboard-sanitized-write']);
    const page = await context.newPage();
    await page.setViewport({width:1440,height:1000});
    const s = new Session(page,{lang,scenario:'pattern-selection',url:stack.url});
    await page.evaluateOnNewDocument(language=>{localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language}));},lang);
    const downloads = join(artifacts,lang);
    await mkdir(downloads,{recursive:true});
    const cdp=await page.createCDPSession();
    await cdp.send('Page.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});
    await page.goto(stack.url,{waitUntil:'domcontentloaded'});
    await s.wait('.home');
    await s.click('header.screen.examples');
    await s.wait('.example-picker-trigger');
    const choose = async text=>{
      await page.click('.example-picker-trigger');
      await s.wait('.example-picker-pop input');
      await page.type('.example-picker-pop input',text);
      await page.keyboard.press('Enter');
      await s.wait('.dock [data-tab="pattern"]');
      await page.click('.dock [data-tab="pattern"]');
    };
    const exportPair = async stem=>{
      await s.click('results.toolbar.copyData',{within:'.dock'});
      await s.wait('.dock-copy.copied');
      const tsv=await page.evaluate(()=>navigator.clipboard.readText());
      const before=new Set(await readdir(downloads));
      await s.click('results.toolbar.csvTitle',{within:'.dock',sel:'button'});
      let file;
      for(let i=0;i<100;i++){
        file=(await readdir(downloads)).find(f=>!before.has(f)&&f.endsWith('.csv'));
        if(file)break;
        await new Promise(r=>setTimeout(r,100));
      }
      assert.ok(file,'actual browser CSV download completes');
      const csv=await readFile(join(downloads,file),'utf8');
      const a=tsv.trimEnd().split(/\r?\n/).map(r=>r.split('\t'));
      assert.equal(csv.charCodeAt(0),0xfeff,'toolbar CSV begins with a UTF-8 BOM');
      const b=csv.slice(1).trimEnd().split(/\r?\n/).map(r=>r.split(','));
      assert.deepEqual(a,b,'actual system clipboard and downloaded CSV contain the same table');
      await page.screenshot({path:join(artifacts,`${stem}-${lang}.png`)});
      await writeFile(join(artifacts,`${stem}-${lang}.tsv`),tsv);
      return a;
    };
    await choose('Patch array 2');
    await s.wait('.dock .freq-chips button','P2',{exact:true});
    await (await s.wait('.dock .freq-chips button','P2',{exact:true})).click();
    assert.equal(await (await s.wait('.dock .freq-chips button','P2',{exact:true})).evaluate(e=>e.getAttribute('aria-checked')),'true');
    const array=JSON.parse(await readFile(join(root,'public/projects/patch-array-2x1.json'),'utf8'));
    const p2=array.results.farfield.find(f=>f.port===2);
    const portData=await exportPair('pattern-port2');
    assert.deepEqual(portData[0].slice(0,2),['Pattern','Port']);
    const elementRows=portData.slice(1).filter(row=>row[0]==='Element P2');
    assert.ok(elementRows.length && elementRows.every(row=>row[1]==='2'));
    assert.equal(Number(elementRows[0][5]),p2.directivity_dbi[0][0]);
    assert.ok(portData.slice(1).some(row=>row[0]==='Array'&&row[1]===''),'combined array has no single driven port');
    outcomes.push({language:lang,case:'P2',rows:elementRows.length,port:2});
    await choose('Inset');
    const quantity=await s.wait('.dock select[aria-label]',null);
    await quantity.select('gain');
    assert.equal(await quantity.evaluate(e=>e.value),'gain');
    const inset=JSON.parse(await readFile(join(root,'public/projects/inset-patch.json'),'utf8'));
    const ff=inset.results.farfield[0];
    const gainData=await exportPair('pattern-gain');
    assert.equal(gainData[0].at(-1),'Gain (dBi)');
    const expected=ff.directivity_dbi[0][0]+10*Math.log10(ff.rad_efficiency);
    assert.ok(Math.abs(Number(gainData[1].at(-1))-expected)<1e-10);
    outcomes.push({language:lang,case:'Gain',rows:gainData.length-1,firstValue:Number(gainData[1].at(-1)),expected});
    assert.deepEqual(s.consoleIssues,[],'no app console errors or warnings');
    await context.close();
  }
  await writeFile(join(artifacts,'result.json'),JSON.stringify({status:'passed',outcomes},null,2));
  console.log(JSON.stringify({status:'passed',artifacts,outcomes},null,2));
} catch(error) {
  if(browser){for(const p of await browser.pages())try{await p.screenshot({path:join(artifacts,'failure.png')});}catch{}}
  throw error;
} finally {
  await browser?.close();
  await stack.stop();
}
