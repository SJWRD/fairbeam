// Narrow real-server UI smoke; only the research submission is intercepted (no native solve).
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import puppeteer from 'puppeteer-core';
import {startStack, chromePath} from './scenarios/stack.mjs';
import {Session} from './scenarios/harness.mjs';
const stack = await startStack({log: console.log});
let browser;
try {
  browser = await puppeteer.launch({executablePath: await chromePath(), headless:true,
    args:['--no-sandbox','--enable-unsafe-swiftshader']});
  await mkdir('.impeccable/review', {recursive:true});
  for (const lang of ['en','tr']) {
    const context=await browser.createBrowserContext(), page=await context.newPage();
    await page.setViewport({width:1100,height:900});
    await page.evaluateOnNewDocument(language=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language})),lang);
    const s=new Session(page,{lang,scenario:'elmer-design',url:stack.url});
    await page.goto(stack.url,{waitUntil:'domcontentloaded'});
    await s.wait('.home');
    await s.fill(await s.field(await s.T('home.newProject.name')),`Elmer UI ${lang}`,{blur:false});
    await s.click('home.newProject.create'); await s.wait('.rb');
    await s.click('ribbon.tab.sim',{sel:'.rb-tab'});
    await s.click('ribbon.sim.run',{sel:'.rb-btn'});
    await s.wait('.rd-dialog');
    assert.ok(await page.$eval('.rd-solver', e=>e.scrollHeight<=e.clientHeight), 'solver selector must not have an inner scrollbar');
    const selector='.rd-dialog select[aria-label]';
    assert.equal(await page.$eval(selector,e=>e.value),'openems');
    await page.select(selector,'elmer');
    await s.wait('[data-testid="elmer-design-form"]');
    assert.equal(await page.$('#rd-form'),null,'FDTD controls unmounted for FEM');
    assert.ok(await page.$eval('[data-testid="elmer-design-form"] button[type=submit]',e=>e.disabled));
    const fields=await page.$$('[data-testid="elmer-design-form"] .rd-grid input');
    for (const [i,value] of ['0','100','0','50','0','200'].entries()) {
      await fields[i].click({clickCount:3}); await fields[i].type(value); await fields[i].press('Tab');
    }
    await page.click('[data-testid="elmer-design-form"] input[type=checkbox]');
    assert.equal(await page.$eval('[data-testid="elmer-design-form"] button[type=submit]',e=>e.disabled),false);
    await page.screenshot({path:`.impeccable/review/elmer-design-${lang}.png`});
    let submitted;
    await page.setRequestInterception(true);
    page.on('request',request=>{
      if(new URL(request.url()).pathname==='/api/research/runs' && request.method()==='POST'){
        submitted=JSON.parse(request.postData());
        return request.respond({status:400,contentType:'application/json',body:JSON.stringify({error:'Synthetic unsupported port'})});
      }
      void request.continue();
    });
    await page.click('[data-testid="elmer-design-form"] button[type=submit]');
    await page.waitForFunction(()=>document.querySelector('[data-testid="elmer-design-form"] [role=alert]')?.textContent.includes('Synthetic unsupported port'));
    assert.equal(submitted.backend,'elmer'); assert.equal(submitted.settings.closed_pec,true);
    assert.deepEqual(submitted.settings.domain_mm,[0,0,0,100,50,200]);
    assert.ok(submitted.design.model.id);
    // Reject antenna input before submitting; never drop a port to make an eigenmode run pass.
    await page.evaluate(async()=>{
      const store=await import('/src/designer/store.ts');
      store.setDraft('ports',[{type:'lumped',number:1,R:50,start:[0,0,0],stop:[0,0,1],direction:'z'}]);
    });
    assert.ok(await page.$eval('[data-testid="elmer-design-form"] button[type=submit]',e=>e.disabled));
    await s.click('elmerDesign.useOpenEMS');
    await s.wait('#rd-form');
    assert.equal(await page.evaluate(async()=>(await import('/src/designer/store.ts')).draft.ports.length),1,'switching solvers preserves the feed');
    await page.evaluate(async()=>{(await import('/src/designer/store.ts')).setDraft('ports',[]);});
    const envelope=await page.evaluate(async()=>{
      const {elmerEnclosure}=await import('/src/designer/elmerDomain.ts');
      const d={parts:[{name:'box',material:'air',primitives:[{kind:'box',start:[0,0,0],stop:['W',50,200]}]}]};
      return {bounds:elmerEnclosure(d,{W:100}),invalid:elmerEnclosure(d,{})};
    });
    assert.deepEqual(envelope.bounds,[0,0,0,100,50,200]);
    assert.equal(envelope.invalid,null,'unresolved expressions must not invent an enclosure');
    await page.select(selector,'openems'); await s.wait('#rd-form');
    assert.ok(await page.$('#rd-form select'),'openEMS engine controls preserved');
    assert.equal(await page.$('[data-testid="elmer-design-form"]'),null);
    // Result rendering uses a synthetic completed Design job; native queue evidence is separate.
    await page.setRequestInterception(false);
    page.removeAllListeners('request');
    await page.setRequestInterception(true);
    const job={id:`design-modes-${lang}`,kind:'research',status:'done',name:'Design cavity',
      created:1,created_iso:'2026-10-11T12:00:00Z',error:null,
      research:{backend:'elmer',settings:{closed_pec:true}},
      result:{schema:'fairbeam.elmer-design-result/1',status:'results_exported',frequency_hz:[1.7128e9,2.1747e9]}};
    page.on('request',request=>{
      const path=new URL(request.url()).pathname;
      if(path==='/api/research/runs' || path===`/api/research/runs/${job.id}`)
        return request.respond({status:200,contentType:'application/json',body:JSON.stringify(path.endsWith(job.id)?job:{runs:[job]})});
      void request.continue();
    });
    await page.evaluate(async id=>{
      (await import('/src/runner/designRun.ts')).setRunDialogOpen(false);
      (await import('/src/runner/researchState.ts')).openResearch(id,'elmer',true);
    },job.id);
    await page.waitForSelector('.research-results .table');
    assert.equal(await page.$('#research-form'),null,'Design result must not show fixed-cavity setup');
    assert.equal(await page.$('.research-results dl.kv'),null,'no fabricated analytical comparison');
    assert.ok(await page.$('.research-results .status-warn'),'uncertified result warning');
    assert.match(await page.$eval('.research-results .table',e=>e.textContent),/1[.,]712800/);
    await page.setViewport({width:390,height:844});
    assert.ok(await page.$eval('.research-dialog',e=>e.scrollWidth<=e.clientWidth),'narrow result layout');
    await context.close();
  }
  console.log('PASS: EN/TR Run solver selection, FEM bounds/acknowledgment/snapshot, errors and unchanged openEMS controls.');
} finally {await browser?.close();await stack.stop();}
