import assert from 'node:assert/strict';
import puppeteer from 'puppeteer-core';
import {startStack,chromePath} from './scenarios/stack.mjs';
import {Session} from './scenarios/harness.mjs';
const stack=await startStack();let browser;
try {
  browser=await puppeteer.launch({executablePath:await chromePath(),headless:true,args:['--no-sandbox','--enable-unsafe-swiftshader']});
  for(const lang of ['en','tr']) {
    const context=await browser.createBrowserContext(),page=await context.newPage();
    await page.evaluateOnNewDocument(lang=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language:lang})),lang);
    const s=new Session(page,{lang,scenario:'shape-material-library',url:stack.url});
    await page.goto(stack.url,{waitUntil:'networkidle0'});await s.wait('.home');
    await s.fill(await s.field(await s.T('home.newProject.name')),`Library ${lang}`,{blur:false});
    await s.click('home.newProject.create');await s.wait('.rb');
    for(const prim of [{kind:'box',start:[0,0,0],stop:[1,1,1]},{kind:'sphere',center:[0,0,0],radius:1},{kind:'cylinder',axis:'z',center:[0,0],range:[0,2],radius:1}]) {
      await page.evaluate(async prim=>(await import('/src/designer/draw.ts')).setShapeRequest({prim,into:-1,drawn:false}),prim);
      await s.wait('#sd-form');
      const choices=await page.$$eval('#sd-form option[data-library]',xs=>xs.map(x=>x.value));
      assert.equal(choices.length,9);
      for(const choice of choices) {
        const name=await page.evaluate(async choice=>{
          const option=[...document.querySelectorAll('#sd-form option')].find(o=>o.value===choice);
          const select=option.closest('select');select.value=choice;select.dispatchEvent(new Event('change',{bubbles:true}));
          await new Promise(r=>setTimeout(r,0));
          const d=(await import('/src/designer/store.ts')).draft;
          return {selected:select.value,last:d.materials.at(-1).name,library:d.materials.at(-1).library};
        },choice);
        assert.equal(name.selected,name.last);assert.equal(name.library,choice.slice(8));
      }
      await page.$eval('#sd-form',f=>f.requestSubmit());await page.waitForFunction(()=>!document.querySelector('#sd-form'));
      assert.equal(await page.evaluate(async()=> (await import('/src/designer/store.ts')).draft.parts.at(-1).material.startsWith('air')),true);
    }
    assert.ok(!s.consoleIssues.some(x=>x.startsWith('page error')),s.consoleIssues.join('\n'));
    await context.close();
  }
  console.log('PASS: all 9 library entries on brick, sphere and cylinder; material copied and shape assigned; EN/TR.');
} finally {await browser?.close();await stack.stop();}
