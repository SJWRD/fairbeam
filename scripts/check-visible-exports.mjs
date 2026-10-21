import {mkdir,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import puppeteer from 'puppeteer-core';
import {startStack,chromePath} from './scenarios/stack.mjs';
import {checkVisibleExports} from './scenarios/visible-export-checks.mjs';
const out=resolve(process.env.FAIRBEAM_TEST_ARTIFACTS||join(tmpdir(),'fairbeam-visible-exports'));
await mkdir(out,{recursive:true});
const stack=await startStack({log:console.log});let browser;
const failures=[];
try {
  browser=await puppeteer.launch({headless:true,executablePath:await chromePath(),args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
  for(const lang of ['en','tr']){
    const page=await browser.newPage();
    await page.evaluateOnNewDocument(lang=>localStorage.setItem('fairbeam.generalSettings',JSON.stringify({language:lang})),lang);
    await page.goto(stack.url,{waitUntil:'domcontentloaded'});
    await page.waitForSelector('.home',{timeout:45000});
    try {await checkVisibleExports(page,lang,out);}
    catch(error){failures.push(`${lang}: ${error.stack}`);console.error(error);await page.screenshot({path:join(out,`${lang}-failure.png`)});}
    finally{await page.close();}
  }
  await writeFile(join(out,'failures.json'),JSON.stringify(failures,null,2));
  if(failures.length)throw new Error(`${failures.length} visible export language checks failed`);
}finally{await browser?.close();await stack.stop();}
