import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {join} from 'node:path';

// Shared by S9 and the focused browser command. Exercise real chart components, Blob downloads,
// and the desktop text IPC boundary. The latter is a fake shell, not a native Save As/Excel test.
export async function checkVisibleExports(page, lang, artifacts) {
  const viewport = page.viewport();
  await page.setViewport({width: 1280, height: 900});
  if (artifacts) await mkdir(artifacts, {recursive: true});
  const failures = [], check = (ok, what) => { if (!ok) failures.push(what); };
  try {
    await page.evaluate(async () => {
      const root = document.querySelector('#root');
      window.__exportRootHidden = root?.hidden;
      if (root) root.hidden = true;
      const host = document.createElement('div');
      host.id = 'visible-export-fixture';
      host.style.cssText = 'position:fixed;left:32px;top:32px;width:650px;height:390px;background:var(--al-surface);';
      document.body.append(host);
      window.__exportOldAnchor = HTMLAnchorElement.prototype.click;
      window.__visibleDownloads = [];
      HTMLAnchorElement.prototype.click = function () {
        if (this.download) window.__visibleDownloads.push(fetch(this.href).then(async r => ({
          name: this.download, bytes: Array.from(new Uint8Array(await r.arrayBuffer())),
        })));
        else window.__exportOldAnchor.call(this);
      };
      window.__exportDecimals = (await import('/src/i18n/index.ts')).decimalChoice();
      window.__exportBundle = await(await fetch('/projects/patch-antenna.json')).json();
      await document.fonts.ready;
    });
    for (const kind of ['line', 'polar', 'smith', 'smith-variable']) {
      await page.evaluate(async kind => {
        window.__disposeExportChart?.();
        const host=document.querySelector('#visible-export-fixture');
        host.style.width=kind==='smith-variable'?'340px':'650px';
        window.__disposeExportChart = (await import('/scripts/scenarios/chart-export-fixture.tsx')).mountChart(host, kind, window.__exportBundle);
      }, kind);
      await page.waitForFunction(() => document.querySelector('#visible-export-fixture .chart svg')?.getBoundingClientRect().width > 300);
      if(kind==='line') {
        await page.click('#visible-export-fixture .chart-inspector-toolbar button');
        await page.waitForSelector('#visible-export-fixture .c-bw-label');
      }
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const result = await page.evaluate(async () => {
        const {visibleFigureSvg} = await import('/src/components/visibleFigure.ts');
        const host = document.querySelector('#visible-export-fixture');
        const html = document.documentElement;
        const oldTheme = html.getAttribute('data-theme');
        html.setAttribute('data-theme', 'dark');
        const before = host.innerHTML;
        const svg = visibleFigureSvg(host);
        const unchanged = before === host.innerHTML && html.getAttribute('data-theme') === 'dark';
        const parsed = new DOMParser().parseFromString(svg, 'image/svg+xml').documentElement;
        const notes = [...host.querySelectorAll('.legend-note,.chart-note')].map(note => ({
          text: note.textContent.trim(), bottom: note.getBoundingClientRect().bottom - host.getBoundingClientRect().top,
        }));
        // Decode the exact exported SVG as an image, then inspect note rows in the PNG raster.
        const {svgToPng} = await import('/src/drawing/render.ts');
        const png = await svgToPng(svg, 96);
        const blobUrl = URL.createObjectURL(new Blob([png], {type:'image/png'}));
        const image = new Image(); image.src = blobUrl; await image.decode();
        const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
        const ctx = canvas.getContext('2d'); ctx.drawImage(image,0,0); URL.revokeObjectURL(blobUrl);
        const [,,vw,vh] = parsed.getAttribute('viewBox').split(/\s+/).map(Number);
        const noteInk = notes.map(note => {
          const node = [...host.querySelectorAll('.legend-note,.chart-note')].find(n=>n.textContent.trim()===note.text);
          const r=node.getBoundingClientRect(), h=host.getBoundingClientRect(), sx=image.width/vw, sy=image.height/vh;
          const x=Math.max(0,Math.floor((r.left-h.left)*sx)), y=Math.max(0,Math.floor((r.top-h.top)*sy));
          const w=Math.max(1,Math.min(image.width-x,Math.ceil(r.width*sx))), height=Math.max(1,Math.min(image.height-y,Math.ceil(r.height*sy)));
          if(y>=image.height)return 0;
          const data=ctx.getImageData(x,y,w,height).data;let ink=0;
          for(let i=0;i<data.length;i+=4)if(data[i]<230&&data[i+1]<230&&data[i+2]<230)ink++;
          return ink;
        });
        if (oldTheme===null) html.removeAttribute('data-theme'); else html.setAttribute('data-theme',oldTheme);
        return {svg,png:Array.from(new Uint8Array(await png.arrayBuffer())),unchanged,notes,noteInk,height:vh,
          ui:host.textContent,
          text:[...parsed.querySelectorAll('text')].map(t=>t.textContent).join('\n'),
          pointTitle:parsed.querySelector('.c-points title')?.textContent,
          dashes:[...parsed.querySelectorAll(':scope > line')].map(n=>n.getAttribute('stroke-dasharray')),
          curveDashes:[...parsed.querySelectorAll('.c-line')].map(n=>n.style.strokeDasharray)};
      });
      check(result.unchanged, `${lang}/${kind}: export preserves live chart and theme`);
      check(!/\d,\d/.test(result.text.replaceAll('Probe 1,2','Probe').replaceAll('Reference 3,4','Reference').replaceAll('Point 5,6','Point')), `${lang}/${kind}: exported numbers use decimal points`);
      check(result.text.includes('Probe 1,2'),`${lang}/${kind}: export preserves raw run name punctuation`);
      for (const [i,note] of result.notes.entries()) {
        const expected=lang==='tr'?note.text.replace(/(\d),(?=\d)/g,'$1.'):note.text;
        check(result.text.replace(/\s+/g,' ').includes(expected.replace(/\s+/g,' ')), `${lang}/${kind}: note preserved: ${expected}`);
        check(result.height>=note.bottom, `${lang}/${kind}: export bounds include note`);
        check(result.noteInk[i]>10, `${lang}/${kind}: PNG has visible note glyphs`);
      }
      if (kind === 'line') {
        check(result.text.includes(lang==='tr'?'BG =':'BW ='), `${lang}: bandwidth caption retained`);
        check(result.text.includes('2.450 GHz'), `${lang}: marker normalized`);
        check(result.dashes.includes('6 4') && result.dashes.includes('2 3'), `${lang}: dashed and dotted legend keys retain their exact arrays`);
        const curves=result.curveDashes.map(d=>(d.match(/[\d.]+/g)??[]).join(' '));
        check(curves.includes('6 4')&&curves.includes('2 3'),`${lang}: plotted curves retain their matching dash arrays`);
        check(result.pointTitle?.includes('Point 5,6'),`${lang}: point tooltip preserves the raw series name`);
      }
      if(kind==='polar')check(result.text.includes('Probe 1,2 · 2.45 GHz'),`${lang}: generated comparison frequency uses a point without changing the run name`);
      if(kind.startsWith('smith'))check(result.text.includes('Reference 3,4'),`${lang}: imported reference name punctuation is preserved`);
      if (lang === 'tr') check(/\d,\d/.test(result.ui), `${kind}: Turkish live chart still uses its decimal comma`);
      if (artifacts) {
        await writeFile(join(artifacts, `${lang}-${kind}.svg`), result.svg);
        await writeFile(join(artifacts, `${lang}-${kind}.png`), Buffer.from(result.png));
        await page.screenshot({path:join(artifacts,`${lang}-${kind}-ui.png`)});
      }
    }
    const textCases = await page.evaluate(async () => {
      const {visibleFigureSvg}=await import('/src/components/visibleFigure.ts');
      const i18n=await import('/src/i18n/index.ts');
      const host=document.querySelector('#visible-export-fixture');
      // A genuine nested text node must keep its tspans; English thousands stay untouched.
      window.__disposeExportChart?.(); window.__disposeExportChart=undefined;
      host.innerHTML='<div class="chart" style="width:600px;height:120px"><svg width="600" height="120"><text class="c-tick" x="10" y="20">1,234 <tspan x="10" dy="20">2,45 GHz</tspan></text></svg><span class="legend-note" hidden>HIDDEN NOTE</span><span class="legend-note" inert>INERT NOTE</span><span class="chart-note" style="visibility:hidden">INVISIBLE NOTE</span></div>';
      const note=document.createElement('div');note.className='chart-note';
      note.style.cssText='position:absolute;left:0;top:150px;bottom:auto;width:180px';
      note.textContent='Reference 50,25 Ω; visible below the original SVG.';
      host.querySelector('.chart').append(note);
      i18n.setDecimalChoice('point'); const point=visibleFigureSvg(host);
      i18n.setDecimalChoice('comma'); const comma=visibleFigureSvg(host);
      i18n.setDecimalChoice(window.__exportDecimals);
      const parsed=new DOMParser().parseFromString(comma,'image/svg+xml').documentElement;
      return {point,comma,height:Number(parsed.getAttribute('viewBox').split(/\s+/)[3]),noteBottom:note.getBoundingClientRect().bottom-host.getBoundingClientRect().top};
    });
    check(textCases.point.includes('1,234') && textCases.point.includes('2,45 GHz'), `${lang}: point-decimal mode does not rewrite commas in text`);
    check(textCases.comma.includes('1.234') && textCases.comma.includes('2.45 GHz'), `${lang}: comma-decimal override normalizes all text`);
    check(textCases.comma.includes('<tspan'), `${lang}: normalization preserves nested SVG markup`);
    check(textCases.height>=textCases.noteBottom&&textCases.height>120,`${lang}: notes below the chart enlarge the exported paper`);
    check(!/HIDDEN NOTE|INERT NOTE|INVISIBLE NOTE/.test(textCases.comma), `${lang}: invisible notes stay excluded`);
    const csv = await page.evaluate(async () => {
      const b=await(await fetch('/projects/patch-antenna.json')).json();
      const data=await import('/src/designer/resultData.ts');
      const pkg=await import('/src/export/package.ts');
      const {parseCsv}=await import('/src/export/csv.ts');
      await data.exportResultCsv(b,'impedance','Ω-impedance.csv',undefined,undefined,{format:'all'});
      const browser=await window.__visibleDownloads.pop();
      const before=Object.getOwnPropertyDescriptor(window,'__TAURI_INTERNALS__'), old=window.__TAURI__;
      let native;
      Object.defineProperty(window,'__TAURI_INTERNALS__',{configurable:true,value:{}});
      window.__TAURI__={core:{invoke:async(command,args)=>{native={command,args};return{status:'saved',path:'C:/fixture/result.csv'};}}};
      try {await data.exportResultCsv(b,'impedance','Ω-impedance.csv',undefined,undefined,{format:'all'});}
      finally {if(before)Object.defineProperty(window,'__TAURI_INTERNALS__',before);else delete window.__TAURI_INTERNALS__;window.__TAURI__=old;}
      const table=data.resultDataTable(b,'efficiency'), toolbarColumn=table.header.indexOf('Radiation efficiency (%)');
      const included={readme:true,data:true};
      const files=pkg.packageFiles(b,included);
      const farfield=files.find(f=>f.path==='data/farfield.csv').data;
      const parsed=parseCsv(farfield), index=parsed[0].indexOf('rad_efficiency'), frequency=parsed[0].indexOf('f_GHz');
      return {browser,native,raw:data.resultDataCsv(data.resultDataTable(b,'impedance',undefined,{format:'all'})),
        files:files.filter(f=>f.path.endsWith('.csv')).map(f=>({path:f.path,data:f.data})),
        readme:files.find(f=>f.path==='README.md').data,
        values:parsed.slice(1).map(row=>({fraction:Number(row[index]),percent:table.rows.find(r=>r[0]===Number(row[frequency]))?.[toolbarColumn]}))};
    });
    check(csv.browser.bytes.slice(0,3).join(',')==='239,187,191',`${lang}: toolbar browser CSV starts with UTF-8 BOM`);
    check(csv.native.command==='save_download' && csv.native.args.text.charCodeAt(0)===0xfeff,`${lang}: desktop text IPC preserves BOM`);
    check(csv.native.args.text===new TextDecoder('utf-8',{ignoreBOM:true}).decode(new Uint8Array(csv.browser.bytes)),`${lang}: browser and desktop CSV payloads agree exactly`);
    check(csv.raw.charCodeAt(0)!==0xfeff && csv.raw.includes('Ω') && csv.raw.includes('∠') && csv.raw.includes('\r\n'),`${lang}: raw table CSV remains BOM-free with Unicode headers and CRLF`);
    check(csv.files.every(f=>f.data.charCodeAt(0)!==0xfeff&&!f.data.includes('\r')),`${lang}: package CSV remains BOM-free/LF`);
    check(csv.values.length>0&&csv.values.every(v=>Math.abs(v.percent-v.fraction*100)<1e-10),`${lang}: documented percentage/fraction distinction is numerically true`);
    check(/UTF-8 BOM/.test(csv.readme)&&/CRLF/.test(csv.readme)&&/fractions/.test(csv.readme)&&/inf/.test(csv.readme),`${lang}: generated README explains both dialects`);
    if (artifacts) {
      await writeFile(join(artifacts,`${lang}-toolbar.csv`),Buffer.from(csv.browser.bytes));
      await writeFile(join(artifacts,`${lang}-package-farfield.csv`),csv.files.find(f=>f.path==='data/farfield.csv').data);
    }
  } finally {
    await page.evaluate(async () => {
      window.__disposeExportChart?.();delete window.__disposeExportChart;
      document.querySelector('#visible-export-fixture')?.remove();
      const root=document.querySelector('#root');if(root)root.hidden=window.__exportRootHidden??false;
      if(window.__exportOldAnchor)HTMLAnchorElement.prototype.click=window.__exportOldAnchor;
      (await import('/src/i18n/index.ts')).setDecimalChoice(window.__exportDecimals);
    });
    if(viewport)await page.setViewport(viewport);
  }
  assert.deepEqual(failures,[],`Visible export regressions (${lang})`);
  console.log(`${lang}: SVG/PNG text, notes, exact dashes and browser/native-IPC CSV bytes passed.`);
}
