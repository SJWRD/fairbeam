// Real EN/TR browser regression for the modeless shape/camera workflow; no solver runs.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import puppeteer from 'puppeteer-core';
import {startStack,chromePath} from './scenarios/stack.mjs';

let stack,browser;
try {
  stack=await startStack({log:console.log,niceSolver:false});
  browser=await puppeteer.launch({executablePath:await chromePath(),headless:true,args:['--no-sandbox','--enable-unsafe-swiftshader']});
  for (const language of ['en','tr']) {
    const translations=JSON.parse(await readFile(new URL(`../src/i18n/${language}.json`,import.meta.url),'utf8'));
    const context=await browser.createBrowserContext();
    const page=await context.newPage();
    page.setDefaultTimeout(20000);
    await page.setViewport({width:1024,height:768});
    await page.evaluateOnNewDocument(language=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language})),language);
    await page.goto(stack.url,{waitUntil:'domcontentloaded'});
    await page.waitForSelector('.home form input[type=text]');
    const fill=async(selector,value)=>{
      await page.click(selector,{clickCount:3});
      // Pointer multi-click selection differs between platforms; explicitly select the input,
      // as the shared scenario harness does, so typed coordinates replace rather than append.
      await page.$eval(selector,e=>e.select());
      await page.keyboard.type(value);
      assert.equal(await page.$eval(selector,e=>e.value),value,`${selector}: requested coordinate entered`);
      await page.keyboard.press('Tab');
    };
    const click=async(key,scope='')=>{
      const button=await page.evaluateHandle(({label,scope})=>[...document.querySelectorAll(`${scope} button`)].find(e=>(e.getAttribute('aria-label')||e.textContent.trim())===label),{label:translations[key],scope});
      assert.ok(button.asElement(),`control ${key} exists`);
      const point=await button.evaluate(el=>{
        const r=el.getBoundingClientRect();
        for(const u of [.5,.1,.9]) for(const v of [.5,.1,.9]) {
          const x=r.left+r.width*u,y=r.top+r.height*v,hit=document.elementFromPoint(x,y);
          if(hit&&el.contains(hit)) return {x,y};
        }
        return null;
      });
      // A modeless form can cover the center of a camera button at this narrow width.
      // Click its exposed portion, never a different element through an occluding form.
      assert.ok(point,`control ${key} has a visible pointer target`);
      await page.mouse.click(point.x,point.y);await button.dispose();
    };
    await fill('.home form input[type=text]',`Shape workflow ${language}`);
    await click('home.newProject.create','.home');
    await page.waitForSelector('.viewport canvas');
    const open=async()=>{await click('ribbon.shapes.group','.rb');await click('ribbon.shapes.box','.rb');await page.waitForSelector('.sd');};
    await open();
    await click('common.cancel','.sd');
    await page.waitForSelector('.sd',{hidden:true});
    await page.waitForFunction(()=>document.activeElement?.classList.contains('viewport'));
    assert.equal(await page.$eval('.viewport',e=>e.getAttribute('tabindex')),'0','fallback remains keyboard navigable');
    await open();
    for (const [axis,start,stop] of [[0,'-20','20'],[1,'-10','10'],[2,'0','0']]) {
      await fill(`#dz-__shape-start-${axis}-`,start);await fill(`#dz-__shape-stop-${axis}-`,stop);
    }
    const summary=await page.$eval('.sd .dialog-foot',e=>e.textContent);
    assert.ok(summary.includes(translations['shape.summary.sheet'].replace('{axis}','Z')),`${language}: zero-thickness sheet is explicitly described; actual footer: ${JSON.stringify(summary)}`);
    await click('common.ok','.sd');
    await page.waitForSelector('.sd',{hidden:true});
    await page.waitForFunction(()=>document.activeElement?.classList.contains('viewport'));
    // Modeless editing lets the user choose a camera while the dialog stays open. Its cleanup
    // must preserve that deliberately chosen focus rather than pulling it back to the canvas.
    await open();
    await click('viewport.view.top','.viewport');
    await page.waitForFunction(()=>document.activeElement?.getAttribute('aria-pressed')==='true');
    await page.keyboard.press('Escape');
    await page.waitForSelector('.sd',{hidden:true});
    assert.equal(await page.evaluate(()=>document.activeElement?.textContent.trim()),translations['viewport.view.top']);
    const announcements=await page.$eval('.viewport [role=status], .viewport [aria-live]',e=>e.textContent);
    assert.ok(announcements.includes(translations['viewport.view.top']),`${language}: camera view name is translated`);
    assert.ok(!announcements.includes('viewport.'),'no raw translation key in the camera announcement');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'the drawing workflow fits a 1024 px window');
    await context.close();
    console.log(`PASS ${language}: shape Cancel/OK focus, modeless camera focus, sheet summary, localized camera announcement`);
  }
} finally {await browser?.close();await stack?.stop();}
