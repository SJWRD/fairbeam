// Real ribbon and Checks navigation. No solver or native desktop privileges are needed.
import assert from 'node:assert/strict';
import {mkdirSync} from 'node:fs';
import {join} from 'node:path';

export default {
  id:'S24', title:'Settings focus, minimized ribbon and truthful selection help',
  async run(s,ctx) {
    const focus=()=>s.ev((_,m)=>m.r.resultFocus(),null,{r:'/src/designer/resultFocus.ts'});
    const seedFocus=view=>s.ev((view,m)=>m.r.focusResult({file:'navigation-fixture.json',view}),view,{r:'/src/designer/resultFocus.ts'});
    await s.step('create a design without warming the Simulation settings import',async()=>{
      await s.page.goto(s.url,{waitUntil:'domcontentloaded'});await s.wait('.home');
      await s.fill(await s.field(await s.T('home.newProject.name')),`Navigation ${s.lang} ${ctx.stamp}`,{blur:false});
      await s.click('home.newProject.create');await s.wait('.rb');
    });
    await s.step('minimized pointer and keyboard tab selection preserve each 3D result focus',async()=>{
      await s.click('ribbon.minimize',{sel:'.rb-minimize'});
      for(const view of ['pattern3d','currents','fieldplane']) {
        await seedFocus(view);
        assert.equal((await focus())?.view,view);
        for(const tab of ['home','model','sim','opt']) {
          const key={home:'home',model:'model',sim:'sim',opt:'optimize'}[tab];
          await s.click(`ribbon.tab.${key}`,{sel:'.rb-tab'});
          assert.equal((await focus())?.view,view,`${view}: selecting a hidden panel preserves the displayed result`);
        }
        for(const key of ['Home','End','ArrowLeft','ArrowRight']) {
          await s.press(key);
          assert.equal((await focus())?.view,view,`${view}: minimized ${key} preserves the result`);
        }
        assert.equal(await s.page.$$eval('.rb[role=tabpanel]',panels=>panels.length>0&&panels.every(el=>el.hidden)),true);
      }
      await s.click('ribbon.expand',{sel:'.rb-minimize'});
      await s.click('ribbon.tab.home',{sel:'.rb-tab'});
      assert.equal(await focus(),null,'the expanded geometry tab retains its existing result-exit behavior');
    });
    await s.step('a cold Checks link focuses the dialog field after the lazy import, never the inspector',async()=>{
      let held;
      const intercept=request=>{
        if(new URL(request.url()).pathname.endsWith('/SimSettingsDialog.tsx'))held=request;
        else void request.continue();
      };
      await s.page.setRequestInterception(true);s.page.on('request',intercept);
      try {
        await s.store((_,m)=>m.s.edit(d=>{
          d.mesh={mode:'auto',cells_per_wavelength:20,max_ratio:0.8};
          d.far_field={enabled:true,frequencies:[1.5]};
        }));
        await s.ev((_,m)=>m.d.setDesignDockTab('checks'),null,{d:'/src/designer/dockState.ts'});
        await s.wait('.dz-check-item');
        const place=await s.store((_,m)=>m.p.checkPlace(m.s.checks().find(c=>c.path==='mesh.max_ratio')),null,{p:'/src/designer/DesignPane.tsx'});
        const row=await s.page.evaluateHandle(place=>[...document.querySelectorAll('.dz-check-item')].find(el=>el.querySelector('.dz-check-where')?.textContent===place),place);
        assert.ok(row.asElement(),'mesh.max_ratio has a real Checks row');
        await row.click();await row.dispose();
        await s.click('props.checks.editField',{within:'.dz-check-detail:not([hidden])'});
        await s.wait('#sim-settings-loading-title');
        assert.ok(held,'the first Simulation settings import is held');
        assert.equal(await s.page.$eval('.dw-right',el=>el.querySelectorAll('input[id^="dz-mesh-"],input[id^="dz-simulation-"]').length),0,'inspector is a read-only summary while the dialog loads');
        await held.continue();held=null;
        await s.wait('.ss-dialog');
        await s.waitFor(()=>document.activeElement?.id==='dz-mesh-max_ratio');
        assert.equal(await s.page.$eval('#ss-tab-mesh',el=>el.getAttribute('aria-selected')),'true');
        assert.equal(await s.page.evaluate(()=>document.querySelector('.ss-dialog').contains(document.activeElement)),true);
        const duplicates=await s.page.$$eval('[id]',elements=>{
          const ids=elements.map(el=>el.id);return ids.filter((id,i)=>ids.indexOf(id)!==i&&/^dz-(simulation|mesh|far_field|monitors)/.test(id));
        });
        assert.deepEqual(duplicates,[]);
        for(const [path,id,section] of [
          ['simulation.end_criteria_db','dz-simulation-end_criteria_db','solver'],
          ['far_field.frequencies[0]','dz-far_field-frequencies','monitors'],
          ['mesh.pad','dz-mesh-pad','bounds'],
          ['simulation.boundaries[3]','dz-simulation-boundaries-3-','bounds'],
          ['far_field.enabled','dz-far_field-enabled','monitors'],
          ['monitors.efficiency','dz-monitors-efficiency','monitors'],
        ]) {
          await s.store((path,m)=>m.s.focusPath(path),path);
          await s.waitFor(id=>document.activeElement?.id===id,id);
          assert.equal(await s.page.$eval(`#ss-tab-${section}`,el=>el.getAttribute('aria-selected')),'true');
          if(path==='simulation.boundaries[3]')assert.equal(await s.page.$eval(`#${id}`,el=>el.getAttribute('aria-pressed')),'true','the requested boundary face owns its editor');
        }
        if(process.env.FAIRBEAM_SCENARIO_SCREENSHOTS) {
          mkdirSync(process.env.FAIRBEAM_SCENARIO_SCREENSHOTS,{recursive:true});
          await s.page.screenshot({path:join(process.env.FAIRBEAM_SCENARIO_SCREENSHOTS,`S24-${s.lang}-settings.png`)});
        }
        await s.press('Escape');await s.gone('.ss-dialog');
        await s.store((_,m)=>m.s.edit(d=>{d.mesh.max_ratio=1.4;}));
      } finally {
        if(held)await held.continue();
        s.page.off('request',intercept);await s.page.setRequestInterception(false);
      }
    });
    await s.step('automatic overrides and repeated checks stay in the single settings editor',async()=>{
      await s.store((_,m)=>m.s.edit(d=>{d.mesh={mode:'design',overrides:{max_ratio:1.3,pad:0.5}};}));
      for(const path of ['mesh.overrides.max_ratio','mesh.overrides.pad','mesh.overrides.max_ratio']) {
        await s.store((path,m)=>m.s.focusPath(path),path);await s.wait('.ss-dialog');
        const id=`dz-${path.replace(/[^\w]/g,'-')}`;
        await s.waitFor(id=>document.activeElement?.id===id,id);
        assert.equal(await s.page.$$eval(`#${id}`,nodes=>nodes.length),1,'one canonical editor id');
      }
      await s.press('Escape');await s.gone('.ss-dialog');
      await s.showDesignPanel('side');
      assert.equal(await s.page.$eval('.dw-right',el=>el.querySelectorAll('input,select').length),0,'Properties summary offers no second live editor');
      await s.click('props.sim.open',{within:'.dw-right'});await s.wait('.ss-dialog');
      assert.equal(await s.page.$eval('#ss-tab-freq',el=>el.getAttribute('aria-selected')),'true','an explicit open does not reuse an old check request');
      await s.press('Escape');await s.gone('.ss-dialog');
    });
    await s.step('the shortcut sheet describes single geometry and additive run selection accurately',async()=>{
      await s.ev((_,m)=>m.h.showShortcutHelp(),null,{h:'/src/designer/ShortcutHelp.tsx'});
      await s.wait('.shortcut-help-dialog');
      assert.equal(await s.T('shortcuts.extra.clickKeys'),s.lang==='en'?'Click':'Tıklama');
      assert.equal(await s.T('shortcuts.extra.treeMultiKeys'),s.lang==='en'?'Ctrl/⌘ click (runs)':'Ctrl/⌘ tıklama (çalıştırmalar)');
      const text=await s.text('.shortcut-help-dialog');
      assert.ok(text.includes(await s.T('shortcuts.extra.clickKeys')));
      assert.ok(text.includes(await s.T('shortcuts.extra.treeMultiKeys')));
      await s.press('Escape');await s.gone('.shortcut-help-dialog');
    });
  },
};
