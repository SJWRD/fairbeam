import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import puppeteer from 'puppeteer-core';
import {chromePath,startStack} from './scenarios/stack.mjs';
const stack=await startStack({log:console.log,niceSolver:false});
let browser, currentPage;
const pageErrors=[];
try {
 browser=await puppeteer.launch({headless:true,executablePath:await chromePath(),args:['--no-sandbox']});
 for(const lang of ['en','tr']) {
  const messages=JSON.parse(readFileSync(new URL(`../src/i18n/${lang}.json`,import.meta.url),'utf8'));
  const page=await browser.newPage();currentPage=page;page.setDefaultTimeout(20000);
  page.on('pageerror',error=>pageErrors.push(error.message));
  await page.setViewport({width:1200,height:800});
  await page.evaluateOnNewDocument(language=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language})),lang);
  await page.goto(stack.url,{waitUntil:'domcontentloaded'});await page.waitForSelector('.home');
  await page.evaluate(()=>{
   window.__gpuState={check_updates_on_start:false,gpu_supported:true,gpu_choice_ready:false,gpu_runtime_installing:false};
   window.__gpuCalls=[];window.__TAURI_INTERNALS__={invoke:async command=>{
    window.__gpuCalls.push(command);
    if(command==='get_general_settings')return {...window.__gpuState};
    if(command==='install_gpu_runtime'){
     window.__gpuState.gpu_runtime_installing=true;
     return new Promise((resolve,reject)=>window.__gpuPending={resolve,reject});
    }
    if(command==='telemetry_status')return {build_enabled:false,ask:false};
    return null;
   }};
  });
  const open=async()=>{await page.click(`[aria-label="${messages['header.settings']}"]`);await page.waitForSelector('#gs-title');};
  const install=async()=>{
   await page.waitForFunction(()=>{const button=document.querySelector('.gs-gpu-runtime button');return button&&!button.disabled;});
   await page.click('.gs-gpu-runtime button');
   await page.waitForFunction(()=>!!window.__gpuPending);
  };
  await open();await install();
  assert.equal(await page.$eval('.gs-dialog progress',el=>el.hasAttribute('value')),false,'initial progress is indeterminate');
  await page.evaluate(()=>{
   const detail={step:'download-openems',fraction:.42,message:'42 MB / 100 MB',log:'C:\\Logs\\GPU install.log'};
   window.__gpuState.gpu_runtime_progress=detail;
   window.dispatchEvent(new CustomEvent('fairbeam:gpu-runtime-progress',{detail}));
  });
  await page.waitForFunction(()=>document.querySelector('.gs-dialog progress')?.value===.42);
  assert.match(await page.$eval('.gs-dialog',el=>el.textContent),/42 MB/);
  await page.click('.gs-dialog .dialog-head button');await open();
  await page.waitForFunction(()=>document.querySelector('.gs-dialog progress')?.value===.42);
  await page.evaluate(()=>{
   window.__gpuState.gpu_runtime_installing=false;
   window.__gpuPending.reject({code:'gpu_runtime_install_failed',detail:'SHA-256 mismatch',log:'C:\\Logs\\GPU install.log'});
   delete window.__gpuPending;
  });
  await page.waitForFunction(()=>document.querySelector('.gs-dialog [role="alert"]')?.textContent.includes('SHA-256 mismatch'));
  assert.match(await page.$eval('.gs-dialog [role="alert"]',el=>el.textContent),/GPU install.log/);
  await install();
  await page.evaluate(()=>{
   Object.assign(window.__gpuState,{gpu_runtime_installing:false,gpu_runtime_ready:true,gpu_choice_ready:true,gpu_choice_source:'managed'});
   window.__gpuPending.resolve();delete window.__gpuPending;
  });
  await page.waitForFunction(message=>document.querySelector('.gs-dialog .status-good')?.textContent===message,{},messages['settings.gpuManaged.installed']);
  assert.equal(await page.$('.gs-dialog progress'),null);
  await page.close();
 }
 assert.deepEqual(pageErrors,[]);
 console.log('GPU Settings EN/TR: live download progress, reopen replay, actual error/log display, useful success message passed with mocked native installer');
} catch(error) {if(currentPage&&!currentPage.isClosed())console.log(await currentPage.evaluate(()=>({calls:window.__gpuCalls,state:window.__gpuState,dialog:document.querySelector('.gs-dialog')?.textContent,button:document.querySelector('.gs-gpu-runtime button')?.outerHTML})));throw error;} finally {if(browser)await browser.close();await stack.stop();}
