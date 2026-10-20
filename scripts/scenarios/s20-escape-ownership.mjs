// Real DOM event ordering: local expression fields, modal layers and the Run drawer own
// different Escape actions. These checks use the normal UI and never start a solver.
import assert from 'node:assert/strict';
import { copyFileSync } from 'node:fs';
import { join } from 'node:path';

export default {
  id: 'S20', title: 'Escape belongs to the active field or dialog',
  async run(s, ctx) {
    let readonly, editable, source, preview;
    const runState = () => s.ev((_, m) => ({ key: m.r.modelKey(), open: m.r.runOpen(), preview: m.r.previewActive() }), null, { r: '/src/runner/store.ts' });
    await s.step('open a bundled Python model in the Run panel', async () => {
      // The isolated scenario workspace starts empty; supply one real bundled, read-only source.
      copyFileSync(join(ctx.root, 'python/models/dipole.py'), join(ctx.stack.dir, 'models/dipole.py'));
      await s.page.goto(s.url, { waitUntil: 'domcontentloaded' });
      await s.wait('.home');
      readonly = await s.page.evaluate(async () => {
        const run = await import('/src/runner/store.ts');
        await run.refreshModels();
        const model = run.models().find(m => m.readonly && m.kind !== 'design' && !m.error);
        if (!model) throw new Error('A bundled Python model is required');
        await (await import('/src/runner/startPython.ts')).runPythonModel(model.key);
        return model.key;
      });
      await s.wait('#run-panel');
      preview = await runState();
      assert.equal(preview.open, true);
    });
    await s.step('Settings consumes Escape without closing Run or restoring another preview', async () => {
      await s.click('header.settings');
      await s.wait('#gs-title');
      await s.press('Escape');
      await s.gone('#gs-title');
      assert.deepEqual(await runState(), preview);
      assert.equal(await s.page.$eval('#run-panel', el => el.isConnected), true);
    });
    await s.step('native read-only choice consumes Escape without closing Run or submitting a job', async () => {
      const before = (await (await fetch(`${ctx.stack.apiUrl}/api/runs`)).json()).runs.length;
      await s.clickSel('#run-panel .rp-start-btn');
      await s.wait('dialog.example-copy-dialog[open]');
      await s.press('Escape');
      await s.gone('dialog.example-copy-dialog[open]');
      assert.deepEqual(await runState(), preview);
      assert.equal((await (await fetch(`${ctx.stack.apiUrl}/api/runs`)).json()).runs.length, before);
    });
    await s.step('Cancel via Escape keeps a dirty source and its Run selection', async () => {
      editable = `escape_${s.lang}_${ctx.stamp}`;
      await s.page.evaluate(async id => {
        const { api } = await import('/src/runner/api.ts');
        await api.createModel({ id, name: id, template: 'blank' });
        await (await import('/src/runner/store.ts')).refreshModels();
        await (await import('/src/runner/startPython.ts')).runPythonModel(id);
      }, editable);
      await s.clickSel('#rp-tab-code');
      await s.wait('#run-panel .cm-content');
      await s.clickSel('#run-panel .cm-content');
      await s.press(process.platform === 'darwin' ? 'ArrowDown' : 'End', [process.platform === 'darwin' ? 'Meta' : 'Control']);
      await s.page.keyboard.type('\n# Preserve this unsaved edit\n');
      source = await s.ev((_, m) => m.e.draft(), null, { e: '/src/editor/store.ts' });
      await s.clickSel('#rp-tab-run');
      await s.page.select('#rp-model', readonly);
      await s.wait('#confirm-draft-title');
      await s.press('Escape');
      await s.gone('#confirm-draft-title');
      assert.equal((await runState()).key, editable);
      assert.equal((await runState()).open, true);
      assert.equal(await s.ev((_, m) => m.e.draft(), null, { e: '/src/editor/store.ts' }), source);
      // Clean up only this fixture edit so a plain drawer Escape has no confirmation.
      await s.ev((_, m) => m.e.setDraft(m.e.file().source), null, { e: '/src/editor/store.ts' });
      await s.page.$eval('#run-panel-title', el => el.focus());
      await s.press('Escape');
      await s.gone('#run-panel');
    });
    let before;
    await s.step('create a patch and edit several Simulation settings', async () => {
      s.consoleAllow.push(/Failed to load resource:.*422/); // intentional unknown frequency expression
      await s.click('header.screen.start');
      await s.wait('.home');
      await s.fill(await s.field(await s.T('home.newProject.name')), `Escape patch ${s.lang} ${ctx.stamp}`, { blur: false });
      await (await s.wait('label, .home-tpl', await s.T('templates.patch.name'))).click();
      await s.click('home.newProject.create');
      await s.wait('.rb');
      before = await s.store((_, m) => JSON.parse(JSON.stringify(m.s.draft)));
      await s.page.evaluate(async () => (await import('/src/runner/designRun.ts')).openSimSettings('freq'));
      await s.wait('.ss-dialog');
      await s.fill(await s.field('f max', { within: '.ss-dialog' }), '3.3');
      await s.clickSel('#ss-tab-monitors');
      await (await s.wait('label', await s.T('sim.mon.efficiency'), { within: '.ss-dialog', exact: true })).click();
      await s.clickSel('#ss-tab-freq');
      await s.fill(await s.field('f min', { within: '.ss-dialog' }), 'missing_freq', { blur: false });
    });
    await s.step('first field Escape dismisses the offer and preserves all other Simulation edits', async () => {
      s.consoleAllow.push(/Failed to load resource:.*422/); // the pending preview rejects that unknown name
      await s.wait('.ss-dialog .param-create');
      await s.press('Escape');
      await s.gone('.ss-dialog .param-create');
      assert.equal(await s.page.$eval('.ss-dialog', el => el.isConnected), true);
      const after = await s.store((_, m) => JSON.parse(JSON.stringify(m.s.draft)));
      assert.equal(after.simulation.f_max, 3.3);
      assert.ok(after.monitors.efficiency);
      assert.equal(after.simulation.f_min, 'missing_freq');
      // Dismissing from the offer's own input must return focus to its expression field.
      await (await s.field('f max', { within: '.ss-dialog' })).focus();
      const frequency = await s.field('f min', { within: '.ss-dialog' });
      await frequency.focus();
      await s.wait('.ss-dialog .param-create');
      await s.press('Tab');
      assert.equal(await s.page.evaluate(() => document.activeElement.matches('.ss-dialog .param-create input')), true);
      await s.press('Escape');
      await s.gone('.ss-dialog .param-create');
      assert.equal(await frequency.evaluate(el => document.activeElement === el), true, 'offer Escape restores expression focus');
      // The dimmed outside area is not a discard action for this live-edit form.
      await s.page.mouse.click(2, 2);
      assert.equal(await s.page.$eval('.ss-dialog', el => el.isConnected), true);
      assert.deepEqual(await s.store((_, m) => JSON.parse(JSON.stringify(m.s.draft))), after);
      await s.click('sim.discard', { within: '.ss-dialog', exact: true });
      await s.gone('.ss-dialog');
      assert.deepEqual(await s.store((_, m) => JSON.parse(JSON.stringify(m.s.draft))), before);
    });
    await s.step('lexical Escape is local once, then an unchanged field allows normal dialog Cancel', async () => {
      await s.page.evaluate(async () => (await import('/src/runner/designRun.ts')).openSimSettings('freq'));
      await s.wait('.ss-dialog');
      const input = await s.field('f min', { within: '.ss-dialog' });
      await s.fill(input, '01.5', { blur: false });
      await s.press('Escape');
      assert.equal(await input.evaluate(el => el.value), '1.5');
      assert.equal(await s.page.$eval('.ss-dialog', el => el.isConnected), true);
      await s.press('Escape');
      await s.gone('.ss-dialog');
      assert.deepEqual(await s.store((_, m) => JSON.parse(JSON.stringify(m.s.draft))), before);
    });
    await s.step('dismissing a shape parameter offer preserves the other entered coordinates', async () => {
      await s.click('ribbon.tab.model', { sel: '.rb-tab' });
      const group = `.rb-group[aria-label="${await s.T('ribbon.shapes.group')}"]`;
      if (!await s.find('.rb-btn:not(.rb-group-toggle)', await s.T('ribbon.shapes.box'), { within: group, exact: true })) {
        await s.click('ribbon.shapes.group', { sel: '.rb-group-toggle', within: group, exact: true });
      }
      await s.click('ribbon.shapes.box', { sel: '.rb-btn', within: group, exact: true });
      await s.wait('.sd');
      const fields = await s.page.$$('.sd-geom input');
      assert.ok(fields.length >= 3);
      await s.fill(fields[1], '17');
      await s.fill(fields[0], 'missing_width', { blur: false });
      await s.wait('.sd .param-create');
      await s.press('Escape');
      await s.gone('.sd .param-create');
      assert.equal(await fields[1].evaluate(el => el.value), '17');
      assert.equal(await s.page.$eval('.sd', el => el.isConnected), true);
      await fields[1].focus();
      await fields[0].focus();
      await s.wait('.sd .param-create');
      await s.press('Tab');
      await s.press('Tab');
      assert.equal(await s.page.evaluate(() => document.activeElement.matches('.sd .param-create button')), true);
      await s.press('Escape');
      await s.gone('.sd .param-create');
      assert.equal(await fields[0].evaluate(el => document.activeElement === el), true, 'offer button Escape restores expression focus');
      await s.press('Escape');
      await s.gone('.sd');
      assert.deepEqual(await s.store((_, m) => JSON.parse(JSON.stringify(m.s.draft))), before);
    });
  },
};
