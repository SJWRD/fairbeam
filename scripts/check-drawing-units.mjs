import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { technicalDrawing } from '../src/drawing/drawing.ts';
import { paramLabel } from '../src/drawing/dimensions.ts';
import { materialRows } from '../src/drawing/sheet.ts';

const source = JSON.parse(readFileSync(new URL('../public/projects/patch-antenna.json', import.meta.url), 'utf8'));
function fixture(unit, label = 'mm') {
  const b = structuredClone(source), factor = unit * 1000;
  b.units = {length_m: unit, length: label};
  const box = [[-15 / factor, -10 / factor, 0], [15 / factor, 10 / factor, 1.5 / factor]];
  b.parts = [{name: 'substrate', type: 'Material', bbox: box, primitives: [{kind: 'box', start: box[0], stop: box[1], bbox: box, priority: 1, exact: true}]},
    {name: 'metal', type: 'ConductingSheet', bbox: box, conductor: {thickness: 0.035 / factor, conductivity: 5.8e7}, primitives: [{kind: 'box', start: box[0], stop: box[1], bbox: box, priority: 2, exact: true}]}];
  b.model.params = [{key: 'W', label: 'Width', value: 30 / factor, default: 30 / factor, unit: label}, {key: 'angle', value: 30, default: 30, unit: 'deg'}];
  b.ports = [
    {number: 1, type: 'lumped', direction: 'z', start: [0, 0, 0], stop: [0, 0, 1.5 / factor], R: 50, excite: true},
    {number: 2, type: 'waveguide', mode: 'TE10', direction: 'x', start: [0, 0, 0], stop: [1 / factor, 0, 0], a: 22.86 / factor, b: 10.16 / factor, R: 500},
    {number: 3, type: 'coaxial', direction: 'z', start: [0, 0, 0], stop: [0, 0, 2 / factor], R: 50},
  ];
  b.half_space = {kind: 'PEC', axis: 'z', position: -5 / factor};
  return b;
}
const millimetres = fixture(0.001);
for (const sheet of ['A3', 'A4', 'figure']) {
  const expected = technicalDrawing(millimetres, {sheet, paramLabels: true, date: '2026-10-19'});
  assert.ok(expected.dimensions.includes('W = 30'));
  for (const [unit, label] of [[0.01, 'cm'], [1, 'm'], [0.001, 'mm']]) {
    const b = fixture(unit, label), before = JSON.stringify(b);
    const result = technicalDrawing(b, {sheet, paramLabels: true, date: '2026-10-19'});
    assert.deepEqual(result.dimensions, expected.dimensions, `${sheet}/${label}: physical dimensions including parameter labels`);
    assert.deepEqual(result.notes, expected.notes, `${sheet}/${label}: outlines, ground, lumped/coaxial/waveguide notes`);
    assert.equal(result.scaleLabel, expected.scaleLabel, `${sheet}/${label}: physical paper/model ratio`);
    assert.ok(Math.abs(result.scale / (unit * 1000) - expected.scale) < 1e-6, 'scale API remains paper mm per model unit');
    assert.deepEqual(materialRows(b), materialRows(millimetres), 'sheet thickness is in physical mm');
    assert.ok(!/NaN|Infinity|undefined/.test(result.svg));
    assert.equal(JSON.stringify(b), before, 'export must not mutate source model, geometry or results');
  }
  const legacy = structuredClone(millimetres);
  delete legacy.units;
  assert.deepEqual(technicalDrawing(legacy, {sheet, paramLabels: true, date: '2026-10-19'}), expected, 'legacy missing units means mm');
}
// Parameter units are explicit, independently of the model coordinate unit.
const cm = fixture(0.01, 'cm');
cm.model.params[0] = {key: 'W', value: 30, default: 30, unit: 'mm'};
assert.equal(paramLabel(cm, 3, '30', 'size'), 'W = 30');
assert.equal(paramLabel(cm, 30, '30°', 'angle'), 'angle = 30°');
cm.model.params[0].value = -30;
assert.equal(paramLabel(cm, 3, '30', 'offset'), '|W| = 30');
assert.equal(paramLabel(cm, 3, '30', 'size'), '30');
console.log('Drawing unit checks passed: physical dimensions, parameter labels, notes, material thickness and scale in A3/A4/figure; mm/cm/m/legacy and immutable inputs.');
