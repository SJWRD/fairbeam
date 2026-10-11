import assert from 'node:assert/strict';
import puppeteer from 'puppeteer-core';
import {startStack,chromePath} from './scenarios/stack.mjs';
import {Session} from './scenarios/harness.mjs';
const stack=await startStack({log:console.log});let browser;
try {
 browser=await puppeteer.launch({headless:true,executablePath:await chromePath(),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
 for(const lang of ['en','tr']) {
 const page=await browser.newPage();await page.setViewport({width:1440,height:1000});await page.evaluateOnNewDocument(l=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language:l})),lang);
 const s=new Session(page,{lang,scenario:'coax',url:stack.url});let expected,id;
 const field=async key=>s.field(await s.T(key));
 await s.step('create coaxial TEM feed through dialog',async()=>{
 await page.goto(stack.url,{waitUntil:'domcontentloaded'});await s.wait('.home');await s.fill(await field('home.newProject.name'),`Coax UI ${lang}`);await s.click('home.newProject.create');await s.wait('.rb');
 await s.store((_,m)=>m.s.edit(d=>{d.materials.push({name:'PTFE',kind:'dielectric',eps_r:2.1,mu_r:1,tan_d:0});const lines=Array.from({length:61},(_,i)=>Number((-3+i*0.1).toFixed(4)));d.mesh={mode:'manual',lines:{x:lines,y:lines,z:lines}};}));await s.click('tree.add.port');await s.wait('.dz-feed-dialog');await (await field('props.port.type')).select('coaxial');
 assert.ok((await page.$eval('.dz-feed-dialog',e=>e.textContent)).includes(await s.T('props.port.noteCoaxial')));
 await s.fill(await field('props.port.innerRadius'),'0.4');await s.fill(await field('props.port.outerRadius'),'1.4');await s.fill(await field('props.port.shellRadius'),'1.8');await s.fill(await field('props.port.feedShift'),'0.1');await s.fill(await field('props.port.measurementShift'),'0.6');
 const choices=await (await field('props.port.conductor')).evaluate(e=>Array.from(e.options).map(o=>o.value).filter(Boolean));assert.ok(choices.length);await (await field('props.port.conductor')).select(choices[0]);
 const fills=await (await field('props.port.dielectric')).evaluate(e=>Array.from(e.options).map(o=>o.value).filter(Boolean));assert.ok(fills.length);await (await field('props.port.dielectric')).select(fills[0]);
 await s.click('feed.create',{within:'.dz-feed-dialog',sel:'.btn-primary',exact:true});await s.gone('.dz-feed-dialog');expected=await s.store((_,m)=>JSON.parse(JSON.stringify(m.s.draft.ports[0])));assert.equal(expected.type,'coaxial');assert.equal(expected.mode,'TEM');assert.ok(!('R'in expected));id=await s.store((_,m)=>m.s.file().id);
 });
 await s.step('edit radii, offsets and persistent material references',async()=>{
 await s.store((_,m)=>m.s.setSelection({type:'port',i:0}));await s.fill(await field('props.port.innerRadius'),'0.45');await s.fill(await field('props.port.measurementShift'),'0.7');
 const materialIndex=await s.store((name,m)=>m.s.draft.materials.findIndex(x=>x.name===name),expected.conductor);await s.store((i,m)=>m.s.setSelection({type:'material',i}),materialIndex);await s.fill(await field('props.name'),`Coax PEC ${lang}`);
 assert.equal(await s.store((_,m)=>m.s.draft.ports[0].conductor),`Coax PEC ${lang}`);
 await s.store((_,m)=>m.s.removeSelected());
 assert.ok(await s.store((name,m)=>m.s.draft.materials.some(x=>x.name===name),`Coax PEC ${lang}`),'in-use coax material cannot be deleted');
 expected=await s.store((_,m)=>JSON.parse(JSON.stringify(m.s.draft.ports[0])));
 await s.waitFor(async()=>{const b=(await import('/src/state.ts')).bundle();return !!b?.parts.some(p=>p.name==='Coaxial port 1 conductor')&&!!b?.parts.some(p=>p.name==='Coaxial port 1 dielectric');},null,{what:'generated coax preview geometry'});
 });
 await s.step('save, close and reopen retaining coax references',async()=>{
 await s.click('ribbon.tab.home',{sel:'.rb-tab'});await s.click('common.save',{sel:'.rb-btn'});await s.waitFor(async()=>!(await import('/src/designer/store.ts')).dirty(),null,{timeout:20000,what:'saved coax'});
 await s.click('ribbon.tab.home',{sel:'.rb-tab'});await s.click('common.close',{sel:'.rb-btn',exact:true});await s.wait('.home');await (await s.wait('.home-item',`Coax UI ${lang}`)).click();await s.wait('.rb');await s.waitFor(async id=>(await import('/src/designer/store.ts')).file()?.id===id,id);
 assert.deepEqual(await s.store((_,m)=>JSON.parse(JSON.stringify(m.s.draft.ports[0]))),expected);
 });
 await s.step('CST export gives explicit unsupported coax warning',async()=>{
 await s.waitFor(async()=>!!(await import('/src/state.ts')).bundle()?.ports.some(p=>p.type==='coaxial'),null);
 await s.clickSel('.header-cst');await s.wait('.context-export-menu');await s.clickSel('[data-export-action="geometry"]');await s.wait('.export-dialog');await (await s.field(await s.T('export.format'))).select('cst');await s.wait('.export-warnings');assert.ok((await s.text('.export-warnings')).includes(await s.T('export.coaxialPortWarning',{port:1})));
 await s.click('common.close',{within:'.export-dialog',attr:'aria-label'});await s.gone('.export-dialog');
 });
 assert.equal(s.results.filter(r=>!r.ok).length,0,JSON.stringify(s.results));console.log(`coax browser ${lang}: 4 steps passed`);await page.close();
 }
} finally {if(browser)await browser.close();await stack.stop();}
