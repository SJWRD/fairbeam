import assert from 'node:assert/strict';
import {mkdirSync} from 'node:fs';
import {join} from 'node:path';

export default {
  id:'S23',title:'Viewer navigation, feedback and editable labels',
  async run(s,ctx) {
    const a=`viewer_a_${s.lang}_${ctx.stamp}`,b=`viewer_b_${s.lang}_${ctx.stamp}`;
    const mods={st:'/src/state.ts',w:'/src/workspace.ts',r:'/src/runner/store.ts',d:'/src/designer/store.ts',dr:'/src/runner/designRun.ts',l:'/src/designer/layoutState.ts'};
    const project=async(mode='result')=>s.ev(async(mode,m)=>{
      const bundle=await(await fetch('/projects/patch-antenna.json')).json();
      if(mode==='geometry'){bundle.results=null;delete bundle.preview;}
      await(await import('/src/runner/openProject.ts')).openUserProject(new File([JSON.stringify(bundle)],`local-${mode}.json`,{type:'application/json'}));
    },mode,mods);
    const drop=async(name,text)=>s.page.evaluate(({name,text})=>{
      const data=new DataTransfer();data.items.add(new File([text],name));
      document.querySelector('.app').dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:data}));
    },{name,text});
    const clearToasts=()=>s.ev(async()=>{const m=await import('/src/lib/toast.ts');for(const t of m.toasts())m.dismissToast(t.id);});

    await s.step('startup and design changes preserve a collapsed dock',async()=>{
      await s.page.evaluateOnNewDocument(()=>localStorage.setItem('fairbeam.bottomDockCollapsed','true'));
      await s.page.goto(s.url,{waitUntil:'domcontentloaded'});await s.wait('.home');
      assert.equal(await s.ev((_,m)=>m.l.bottomDockCollapsed(),null,mods),true);
      for(const [id,name] of [[a,`PIFA ${s.lang}`],[b,`IĞDIR ${s.lang}`]]) {
        const response=await fetch(`${ctx.stack.apiUrl}/api/designs`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,name,template:'empty'})});
        assert.ok(response.ok,await response.text());
      }
      await s.ev(async(id,m)=>{await m.r.refreshModels();await m.d.enterDesign(id);},a,mods);await s.wait('.rb');
      assert.equal(await s.ev((_,m)=>m.l.bottomDockCollapsed(),null,mods),true);
      await s.ev(async(id,m)=>m.d.enterDesign(id),b,mods);await s.wait('.rb');
      assert.equal(await s.ev((_,m)=>m.l.bottomDockCollapsed(),null,mods),true);
      await s.click('header.screen.start',{sel:'.mode-switch button'});await s.wait('.home');
      await s.fill('.home-design-search input','igdir');assert.ok(await s.page.$(`[data-home-design="${b}"]`));await s.fill('.home-design-search input','');
    });
    await s.step('file drop hint clears on leaving a child and stays during internal transitions',async()=>{
      const result=await s.page.evaluate(()=>{
        const child=document.querySelector('.home');
        const data=new DataTransfer();data.items.add(new File(['unsupported'],'photo.png'));
        const fire=(target,type,relatedTarget=null)=>target.dispatchEvent(new DragEvent(type,{bubbles:true,cancelable:true,dataTransfer:data,relatedTarget}));
        const shown=()=>!!document.querySelector('.drop-hint');
        fire(child,'dragover');const entered=shown();
        fire(child,'dragleave',document.querySelector('.drop-hint'));const internal=shown();
        fire(document.querySelector('.drop-hint'),'dragleave');const left=shown();
        fire(child,'dragover');const reentered=shown();
        fire(child,'dragleave',document.body);const outside=shown();
        fire(child,'dragover');fire(document.querySelector('.drop-hint'),'drop');const dropped=shown();
        return {entered,internal,left,reentered,outside,dropped};
      });
      assert.deepEqual(result,{entered:true,internal:true,left:false,reentered:true,outside:false,dropped:false});
      await s.wait('[data-toast-key="file-drop"]',await s.T('app.dropUnsupported',{name:'photo.png'}));
      await clearToasts();
    });
    await s.step('modal file drags never cover or open the background app',async()=>{
      for (const kind of ['cst','pcb']) {
        await clearToasts();
        await s.page.evaluate(async(kind)=>{
          if(kind==='cst')(await import('/src/lib/cstImport.ts')).setCstImportOpen(true);
          else (await import('/src/lib/pcbImport.ts')).setPcbImportOpen(true);
        },kind);
        await s.wait(kind==='cst'?'#ci-title':'#pi-title');
        const result=await s.page.evaluate(()=>{
          const dialog=document.querySelector('[role="dialog"][aria-modal="true"]');
          const child=dialog.querySelector('h2');
          const data=new DataTransfer();data.items.add(new File(['bad data'],'background.json'));
          child.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:data}));
          const covered=!!document.querySelector('.drop-hint');
          child.dispatchEvent(new DragEvent('dragleave',{bubbles:true,relatedTarget:null,dataTransfer:data}));
          return {covered,sticky:dialog.classList.contains('pi-dragging')};
        });
        assert.deepEqual(result,{covered:false,sticky:false});
        if(kind==='cst') {
          await s.page.evaluate(()=>{
            const data=new DataTransfer();data.items.add(new File(['bad data'],'background.json'));
            document.querySelector('#ci-title').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:data}));
          });
          assert.equal(await s.page.$('[data-toast-key="file-drop"]'),null);
          assert.ok(await s.page.$('#ci-title'));
        }
        await s.press('Escape');
      }
    });
    await s.step('drops on Start explain unsupported files and hidden reference context',async()=>{
      for(const [name,key] of [['macro.bas','app.dropCst'],['board.dxf','app.dropPcb'],['photo.png','app.dropUnsupported'],['ref.s1p','app.dropReferenceContext']]) {
        await clearToasts();await drop(name,'bad data');await s.wait('[data-toast-key="file-drop"]',await s.T(key,{name}));
      }
      assert.equal(await s.ev(async()=>!!(await import('/src/compare/store.ts')).reference()),false);
      await clearToasts();
    });
    await s.step('Home and Design text drops distinguish macro guidance from reference context',async()=>{
      const cases=[
        ['history.txt','With Brick\n .Name "body"\nEnd With','app.dropCst'],
        ['macro.txt','Sub Main()\nEnd Sub','app.dropCst'],
        ['measurement.txt','Frequency (GHz)\tS11 (dB)\n1\t-10\n2\t-20','app.dropReferenceContext'],
      ];
      for(const mode of ['home','design']) {
        if(mode==='design'){await s.ev(async(id,m)=>m.d.enterDesign(id),a,mods);await s.wait('.rb');}
        const before=await s.ev((_,m)=>({mode:m.w.appMode(),id:m.d.file()?.id,draft:JSON.stringify(m.d.draft)}),null,mods);
        for(const [name,text,key] of cases) {
          await clearToasts();await drop(name,text);
          await s.wait('[data-toast-key="file-drop"]',await s.T(key,{name}));
          assert.equal(await s.ev(async()=>!!(await import('/src/compare/store.ts')).reference()),false);
          assert.deepEqual(await s.ev((_,m)=>({mode:m.w.appMode(),id:m.d.file()?.id,draft:JSON.stringify(m.d.draft)}),null,mods),before);
        }
      }
      await clearToasts();await s.click('header.screen.start',{sel:'.mode-switch button'});await s.wait('.home');
    });
    await s.step('Start returns to the opened result or geometry without loading an example',async()=>{
      for(const kind of ['result','geometry']) {
        await project(kind);await s.wait('.workspace');
        await s.click('header.screen.start',{sel:'.mode-switch button'});await s.wait('.home');
        await s.click(`header.screen.${kind==='result'?'results':'geometry'}`,{sel:'.mode-switch button'});await s.wait('.workspace');
        const state=await s.ev((_,m)=>({mode:m.w.appMode(),source:m.st.source(),origin:m.st.sourceOrigin()}),null,mods);
        assert.deepEqual(state,{mode:'results',source:`local-${kind}.json`,origin:'local-file'});
      }
      await project();
    });
    await s.step('reference drops report errors and successful visible imports',async()=>{
      await clearToasts();await drop('broken.s1p','');await s.wait('.toast-error','broken.s1p');
      await drop('measurement.s1p','# GHz S RI R 50\n1 0.1 0\n2 0.2 0');
      await s.wait('[data-toast-key="file-drop"]',await s.T('app.dropReferenceImported',{name:'measurement.s1p'}));
      assert.deepEqual(await s.ev(async()=> (await import('/src/compare/store.ts')).reference()?.reference.files),['measurement.s1p']);
      await clearToasts();
      if(ctx.compact) await s.page.locator('[data-panel-toggle="right"]').click();
      await s.wait('.cmp-card');
      if(process.env.FAIRBEAM_SCENARIO_SCREENSHOTS) {
        mkdirSync(process.env.FAIRBEAM_SCENARIO_SCREENSHOTS,{recursive:true});
        await s.page.screenshot({path:join(process.env.FAIRBEAM_SCENARIO_SCREENSHOTS,`S23-${s.lang}-reference-${ctx.compact?'compact':'wide'}.png`)});
      }
    });
    await s.step('Retry removes the old error while pending and yields to a newer open',async()=>{
      await s.page.evaluate(async()=>{
        const state=await import('/src/state.ts');window.__viewerFetch=window.fetch;
        window.fetch=(url,...rest)=>String(url).includes('missing-viewer.json')?Promise.resolve(new Response('',{status:503})):window.__viewerFetch(url,...rest);
        await state.loadProject('missing-viewer.json');
        window.fetch=(url,...rest)=>String(url).includes('missing-viewer.json')?new Promise(resolve=>window.__viewerRelease=resolve):window.__viewerFetch(url,...rest);
      });
      try {
        await s.wait('[data-toast-key="load-error"] .toast-action');
        await s.page.$eval('[data-toast-key="load-error"] .toast-action',button=>button.click());
        assert.equal(await s.ev((_,m)=>m.st.loadError(),null,mods),null);
        assert.equal(await s.page.$('[data-toast-key="load-error"]'),null);
        await project();
        await s.page.evaluate(()=>window.__viewerRelease(new Response('',{status:503})));
        await s.sleep(30);assert.equal(await s.ev((_,m)=>m.st.loadError(),null,mods),null);
        assert.equal(await s.ev((_,m)=>m.st.source(),null,mods),'local-result.json');
      } finally {await s.page.evaluate(()=>{window.fetch=window.__viewerFetch;delete window.__viewerFetch;delete window.__viewerRelease;});}
    });
    await s.step('native View Examples clears the designer preview and loads an example',async()=>{
      await s.ev(async(id,m)=>m.d.enterDesign(id),a,mods);await s.wait('.rb');
      await s.waitFor(async()=>!!(await import('/src/state.ts')).bundle()?.preview);
      await s.page.evaluate(()=>window.dispatchEvent(new CustomEvent('fairbeam:menu-action',{detail:'view-examples'})));
      await s.waitFor(async()=>{const st=await import('/src/state.ts'),ex=await import('/src/runner/examples.ts');return !!st.bundle()&&!st.bundle().preview&&ex.isExampleSource(st.source(),st.sourceOrigin());});
      assert.equal(await s.ev((_,m)=>m.w.appMode(),null,mods),'results');
    });
    await s.step('rename Undo is confined to its own outcome',async()=>{
      await s.click('header.screen.start',{sel:'.mode-switch button'});await s.wait('.home');
      await s.page.locator(`[data-home-rename="${a}"]`).click();await s.wait('#home-rename-name');
      await s.fill('#home-rename-name',`Renamed ${s.lang}`);await s.click('home.designs.renameSave',{within:'.home-rename-dialog'});await s.gone('.home-rename-dialog');
      await s.wait('.home-design-action-note button');
      await s.page.locator(`[data-home-design="${b}"]`).click({button:'right'});await s.click('home.designs.reveal',{sel:'[role=menuitem]'});
      await s.wait('.home-design-action-note',await s.T('home.designs.revealDesktop'));
      assert.equal(await s.page.$('.home-design-action-note button'),null);
      const result=await(await fetch(`${ctx.stack.apiUrl}/api/designs/${a}`)).json();assert.equal(result.design.model.name,`Renamed ${s.lang}`);
    });
    await s.step('macro component edit buffer stays empty or Unicode; generated name stays safe',async()=>{
      await project();await s.ev((_,m)=>m.st.setExportOpen(true),null,mods);await s.wait('.export-dialog');
      const input='input[aria-describedby="export-component-name"]';
      await s.fill(input,'',{blur:false});assert.equal(await s.page.$eval(input,e=>e.value),'');
      await s.wait('#export-component-name',await s.T('export.componentEffective',{name:'fairbeam'}));
      await s.fill(input,'ön yüz',{blur:false});assert.equal(await s.page.$eval(input,e=>e.value),'ön yüz');
      await s.wait('#export-component-name',await s.T('export.componentEffective',{name:'_n_y_z'}));
      await s.page.$eval(input,e=>{e.setSelectionRange(2,2);e.focus();});await s.page.keyboard.type('X');
      assert.deepEqual(await s.page.$eval(input,e=>({value:e.value,caret:e.selectionStart})),{value:'önX yüz',caret:3});
      assert.ok((await s.text('.export-dialog pre.code')).includes('_nX_y_z'));await s.press('Escape');await s.gone('.export-dialog');
    });
    await s.step('optimization banner uses translated reason, selected decimals and its model units',async()=>{
      await s.ev(async(_,m)=>{
        const i=await import('/src/i18n/index.ts');i.setDecimalChoice('comma');
        m.r.setModels([...m.r.models(),{key:'optimization-fixture',model:{id:'optimization-fixture',name:'Optimization fixture'},params:[{key:'L',unit:'mm'}]}]);
        m.r.setNotice({kind:'optimize',job:{model:'optimization-fixture',label:'Optimization fixture'},reason:'simplex converged',best:{L:31.427583920113},bestFile:null});
      },null,mods);
      await s.wait('.rs-banner-good',await s.T('opt.reason.simplexConverged'));
      assert.ok((await s.text('.rs-banner-good')).includes('L=31,4276 mm'));
      await s.ev(async(_,m)=>{m.r.setNotice(null);(await import('/src/i18n/index.ts')).setDecimalChoice('point');},null,mods);
    });
    await s.step('translated loading/error boundaries preserve the 3D acronym',async()=>{
      await s.page.evaluate(async()=>{
        const host=document.createElement('div');host.id='viewer-boundary-fixture';host.style.cssText='position:fixed;inset:80px 32px auto;z-index:9999;background:var(--al-surface);padding:16px';document.body.append(host);
        window.__unmountBoundary=(await import('/scripts/scenarios/viewer-feedback-fixture.tsx')).mountBoundary(host,false);
      });
      const name=await s.T('panel.name.view3d');await s.wait('#viewer-boundary-fixture .panel-loading',await s.T('panel.loading',{name}));
      s.consoleAllow.push(/Controlled boundary fixture/);
      await s.page.evaluate(async()=>{window.__unmountBoundary();window.__unmountBoundary=(await import('/scripts/scenarios/viewer-feedback-fixture.tsx')).mountBoundary(document.getElementById('viewer-boundary-fixture'),true);});
      await s.wait('#viewer-boundary-fixture .panel-error',await s.T('panel.failed',{name}));
      await s.page.evaluate(()=>{window.__unmountBoundary();document.getElementById('viewer-boundary-fixture').remove();delete window.__unmountBoundary;});
    });
    await s.step('desktop offline guidance avoids developer paths, browser retains actionable CLI help',async()=>{
      for(const desktop of [true,false]) {
        await s.ev((desktop,m)=>{
          m.r.setRunOpen(false);
          if(desktop)window.__TAURI_INTERNALS__={invoke:async()=>null};else delete window.__TAURI_INTERNALS__;
          m.r.setModels([{key:'offline-fixture',model:{id:'offline-fixture',name:'Offline fixture'},params:[]}]);
          m.r.selectModel('offline-fixture');m.r.setServerState('offline');m.r.setRunOpen(true);
        },desktop,mods);
        await s.wait('#run-panel',await s.T(desktop?'runPanel.offline.desktop':'runPanel.offline.cli'));
        const text=await s.text('#run-panel');assert.equal(text.includes('~/opt/'),false);assert.equal(text.includes('npm run serve'),!desktop);
      }
      await s.ev(async(_,m)=>{delete window.__TAURI_INTERNALS__;m.r.setRunOpen(false);await m.r.probeServer();},null,mods);
    });
    await s.step('thread threshold follows the decimal preference in either language',async()=>{
      await s.page.evaluate(()=>window.dispatchEvent(new Event('fairbeam:open-settings')));await s.wait('#gs-title');
      const select=await s.field(await s.T('settings.decimals'));
      await select.select('point');assert.ok((await s.text('#gs-panel-general')).includes('0.5 M'));
      await select.select('comma');assert.ok((await s.text('#gs-panel-general')).includes('0,5 M'));
      if(s.lang==='tr')assert.ok(!(await s.T('home.python.choose')).includes("Run'a"));
      await s.press('Escape');
    });
  },
};
