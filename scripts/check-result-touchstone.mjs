import assert from 'node:assert/strict';
import { resultExportStem } from '../src/lib/resultExportNames.ts';
import { unzipSync } from 'fflate';
import { exportResultTouchstone } from '../src/designer/resultTouchstone.ts';
import { parseTouchstone, parseTouchstoneNPort } from '../src/export/touchstone.ts';
import { parseTouchstoneN } from '../src/import/touchstone.ts';
import { arrayWeightsCsv, bandsCsv, farfieldCsv, parseCsv, sparamsCsv, sweepCsv } from '../src/export/csv.ts';

const blobs = new Map();
let offered;
globalThis.window = {};
globalThis.document = {
  body: { appendChild() {}, },
  createElement() { return { style: {}, click() { offered = { name: this.download, url: this.href }; }, remove() {} }; },
};
URL.createObjectURL = (blob) => { const url = `blob:test-${blobs.size}`; blobs.set(url, blob); return url; };
URL.revokeObjectURL = () => {};

function bundle(n) {
  const frequency = [1e9, 2e9];
  const common = { generator: { version: 'test', openems: null }, name: 'fixture', model: { id: 'fixture' }, created: 'today', solver: { engine: 'test', method: 'test' }, ports: Array.from({ length: n }, (_, i) => ({ number: i + 1, excite: true, type: 'lumped', R: 50, direction: 'z' })) };
  const matrix = {};
  for (let i = 1; i <= n; i++) for (let j = 1; j <= n; j++) matrix[`${i},${j}`] = { re: [i * 0.1 + j * 0.01, i * 0.2 + j * 0.01], im: [-i * 0.02, j * 0.03] };
  const results = { frequency, ports: Object.fromEntries(Array.from({ length: n }, (_, i) => [String(i + 1), { z_ref: 50, s11_re: matrix[`${i + 1},${i + 1}`].re, s11_im: matrix[`${i + 1},${i + 1}`].im }])), sparams: { ports: Array.from({ length: n }, (_, i) => i + 1), z_ref: Array(n).fill(50), excited: Array.from({ length: n }, (_, i) => i + 1), s: matrix } };
  return { ...common, results };
}
async function downloaded() { return await blobs.get(offered.url).arrayBuffer(); }
function near(a, b, eps = 6e-8) { assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`); }

const one = bundle(1);
await exportResultTouchstone(one, '../unsafe: result');
assert.equal(offered.name, 'unsafe__result.s1p');
const parsed1 = parseTouchstone(new TextDecoder().decode(await downloaded()));
assert.equal(parsed1.z0, 50);
assert.deepEqual(parsed1.f, one.results.frequency);
for (let i = 0; i < 2; i++) { near(parsed1.re[i], one.results.sparams.s['1,1'].re[i]); near(parsed1.im[i], one.results.sparams.s['1,1'].im[i]); }

const two = bundle(2);
await exportResultTouchstone(two, 'single');
assert.equal(offered.name, 'single.s2p');
const parsed2 = parseTouchstoneNPort(new TextDecoder().decode(await downloaded()), 2);
assert.equal(parsed2.z0, 50);
assert.deepEqual(parsed2.f, two.results.frequency);
for (let k = 0; k < 2; k++) for (let i = 1; i <= 2; i++) for (let j = 1; j <= 2; j++) {
  near(parsed2.s[k][i - 1][j - 1][0], two.results.sparams.s[`${i},${j}`].re[k]);
  near(parsed2.s[k][i - 1][j - 1][1], two.results.sparams.s[`${i},${j}`].im[k]);
}

// Exercise the actual UI export/download boundary: independent 100/150-ohm loads,
// originally referenced to unequal 50/75-ohm ports, both have native reflection 1/3.
const unequal = bundle(2);
unequal.results.sparams.z_ref = [50, 75];
unequal.results.ports['2'].z_ref = 75;
for (const [key, c] of Object.entries(unequal.results.sparams.s)) {
  const [i, j] = key.split(',');
  c.re.splice(0, 2, i === j ? 1/3 : 0, i === j ? 1/3 : 0);
  c.im.splice(0, 2, 0, 0);
}
await exportResultTouchstone(unequal, 'unequal-loads');
assert.equal(offered.name, 'unequal-loads.s2p');
const converted = parseTouchstoneNPort(new TextDecoder().decode(await downloaded()), 2);
assert.equal(converted.z0, 50);
for (const m of converted.s) {
  near(m[0][0][0], (100-50)/(100+50), 1e-14);
  near(m[1][1][0], (150-50)/(150+50), 1e-14);
  near(m[0][1][0], 0, 1e-14); near(m[1][0][0], 0, 1e-14);
}
for (const invalid of [0, -75, NaN]) {
  const bad = structuredClone(unequal); bad.results.sparams.z_ref[1] = invalid;
  const previous = offered;
  await assert.rejects(() => exportResultTouchstone(bad, 'invalid-refs'));
  assert.equal(offered, previous, 'invalid data must not reach the download boundary');
}
const stale = structuredClone(unequal);
stale.results.ports['2'].z_ref = 600;
stale.results.ports['2'].z_ref_f = [800, 500];
const previous = offered;
await assert.rejects(() => exportResultTouchstone(stale, 'stale-refs'));
assert.equal(offered, previous, 'the UI must preserve the writer reference-consistency gate');

await exportResultTouchstone(one, 'compare', [
  { file: '../same.case.json', bundle: one },
  { file: 'same.case.json', bundle: two },
]);
assert.equal(offered.name, 'compare.zip');
const members = unzipSync(new Uint8Array(await downloaded()));
const stem = resultExportStem(one, 'sparams');
assert.deepEqual(Object.keys(members).sort(), [`${stem}.s1p`, `${stem}.s2p`]);
const zip1 = parseTouchstone(new TextDecoder().decode(members[`${stem}.s1p`]));
const zip2 = parseTouchstoneNPort(new TextDecoder().decode(members[`${stem}.s2p`]), 2);
assert.deepEqual(zip1.f, one.results.frequency);
assert.deepEqual(zip2.f, two.results.frequency);

const incomplete = bundle(2);
delete incomplete.results.sparams.s['2,1'];
await assert.rejects(() => exportResultTouchstone(one, 'bad', [
  { file: 'ok.json', bundle: one }, { file: 'incomplete.json', bundle: incomplete },
]), /incomplete or invalid S21/);
const inconsistent = structuredClone(one);
inconsistent.results.ports['1'].s11_re = [0.9, ...inconsistent.results.ports['1'].s11_re.slice(1)];
await assert.rejects(() => exportResultTouchstone(inconsistent, 'bad'), /sweep and matrix S11 data disagree/);
console.log('Result Touchstone checks passed');

await exportResultTouchstone(one, resultExportStem(one, 'sparams'), [
  { file: 'a.json', bundle: one }, { file: 'b.json', bundle: one },
]);
assert.deepEqual(Object.keys(unzipSync(new Uint8Array(await downloaded()))).sort(), [`${stem}.s1p`, `${stem}_2.s1p`]);
const decimal = { ...one, results: { ...one.results, frequency: [1.25e9, 2.45e9] } };
await exportResultTouchstone(decimal, resultExportStem(decimal, 'sparams'));
assert.equal(offered.name, 'fixture_sparams_1.25-2.45GHz.s1p');

// Real result exports must preserve a narrow sweep and very small complex samples.
// The previous fixed decimals merged these frequencies and rounded the samples to zero.
for (const n of [1, 2, 3]) {
  const precise = bundle(n);
  precise.results.frequency = [1e9 + 0.125, 1e9 + 0.25];
  const z = 50.1234567890123;
  precise.results.sparams.z_ref.fill(z);
  for (const p of Object.values(precise.results.ports)) p.z_ref = z;
  for (const c of Object.values(precise.results.sparams.s)) {
    c.re.splice(0, 2, 1.234567890123456e-12, -2.345678901234567e-14);
    c.im.splice(0, 2, -3.456789012345678e-13, 4.567890123456789e-15);
  }
  for (const p of Object.values(precise.results.ports)) { p.zin_re = [50, 50]; p.zin_im = [0, 0]; }
  const sweepRows = parseCsv(sweepCsv(precise));
  assert.notEqual(sweepRows[1][0], sweepRows[2][0], 'CSV keeps narrow sweep samples distinct');
  assert.equal(Number(sweepRows[1][0]), precise.results.frequency[0] / 1e9);
  if (n > 1) {
    const matrixRows = parseCsv(sparamsCsv(precise));
    assert.notEqual(matrixRows[1][0], matrixRows[2][0], 'matrix CSV keeps narrow samples distinct');
    assert.equal(Number(matrixRows[1][0]), precise.results.frequency[0] / 1e9);
  }
  await exportResultTouchstone(precise, `precision-${n}`);
  const text = new TextDecoder().decode(await downloaded());
  // Use the production importer as well as the independent minimal format reader.
  assert.doesNotThrow(() => parseTouchstoneN(text, `precision.s${n}p`));
  const restored = n === 1 ? parseTouchstone(text) : parseTouchstoneNPort(text, n);
  assert.equal(restored.z0, z);
  restored.f.forEach((f, k) => near(f, precise.results.frequency[k], 2 * Number.EPSILON * f));
  for (let k = 0; k < 2; k++) for (let i = 1; i <= n; i++) for (let j = 1; j <= n; j++) {
    const c = precise.results.sparams.s[`${i},${j}`];
    assert.equal(n === 1 ? restored.re[k] : restored.s[k][i - 1][j - 1][0], c.re[k]);
    assert.equal(n === 1 ? restored.im[k] : restored.s[k][i - 1][j - 1][1], c.im[k]);
  }
}
console.log('Touchstone narrow-band and small-signal precision passed (1/2/3 ports)');

const fineBand = bundle(1);
const lo = 1e9 + 0.125, hi = 1e9 + 0.25;
fineBand.results.bands = [{ f_lo: lo, f_hi: hi, f_center: lo, s11_min_db: -12, edge_lo: false, edge_hi: false }];
const band = parseCsv(bandsCsv(fineBand))[1];
assert.equal(Number(band[0]), lo / 1e9);
assert.equal(Number(band[1]), hi / 1e9);
assert.equal(Number(band[6]), (hi - lo) / 1e6, 'sub-kHz band width must not become zero');
fineBand.results.farfield = [{ f: lo, dmax_dbi: 2.1, rad_efficiency: 0.8, prad_w: 1e-12, pacc_w: 2e-12 }];
assert.equal(Number(parseCsv(farfieldCsv(fineBand))[1][0]), lo / 1e9);
fineBand.results.sparams.s['1,1'].re = [1e-12, 1e-12];
fineBand.results.sparams.s['1,1'].im = [-1e-13, -1e-13];
const weights = parseCsv(arrayWeightsCsv(fineBand, new Map([[1, { ampDb: 0, phaseDeg: 0 }]]), lo))[1];
assert.equal(Number(weights[3]), lo / 1e9);
assert.equal(Number(weights[5]), 1e-12);
assert.equal(Number(weights[6]), -1e-13);
console.log('Package CSV precision passed for sweeps, matrices, bands, far-fields and active reflection');

// A one-port matrix index is distinct from the model's physical port number.
const mappedOne = bundle(1);
mappedOne.ports[0].number = 5;
mappedOne.results.ports['5'] = mappedOne.results.ports['1'];
delete mappedOne.results.ports['1'];
mappedOne.results.sparams.port_numbers = [5];
await exportResultTouchstone(mappedOne, 'physical-port-5');
assert.equal(offered.name, 'physical-port-5.s1p');
const mappedParsed = parseTouchstone(new TextDecoder().decode(await downloaded()));
assert.deepEqual(mappedParsed.re, mappedOne.results.ports['5'].s11_re);
assert.deepEqual(mappedParsed.im, mappedOne.results.ports['5'].s11_im);
for (const invalid of [null, [6], [5, 6]]) {
  const bad = structuredClone(mappedOne); bad.results.sparams.port_numbers = invalid;
  const before = offered;
  await assert.rejects(() => exportResultTouchstone(bad, 'bad-map'), /does not match the exported port/);
  assert.equal(offered, before, 'an invalid association cannot offer a file');
}
console.log('Mapped single-port download keeps physical port identity and rejects invalid associations');
