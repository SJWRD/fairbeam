// Report tables/text use physical millimetres even when geometry is stored in cm or m.
// node --experimental-strip-types scripts/check-report-units.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { meshText, readmeReport } from "../src/export/report.ts";
import { reportPages } from "../src/export/reportPdf.ts";

const source = JSON.parse(readFileSync(new URL("../public/projects/patch-antenna.json", import.meta.url), "utf8"));
const markdownOptions = { exported: "2026-10-19", files: [] };
const pdfOptions = { generated: "2026-10-19", date: "2026-10-19" };
const texts = (svg) => [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)].map((match) => match[1]).join(" ").replaceAll("−", "-");

function fixture(unit) {
  const b = structuredClone(source);
  const scale = (unit ?? 1e-3) * 1000;
  const coordinates = (values) => values.map((value) => value / scale);
  b.units = { length: unit === 1 ? "m" : unit === 1e-2 ? "cm" : "mm", length_m: unit ?? 1e-3, frequency: "Hz" };
  if (unit === undefined) delete b.units;
  b.model.params = [];
  const bbox = [coordinates([-15, -10, 0]), coordinates([15, 10, 1.5])];
  b.parts = [{ name: "patch", label: "Physical patch", type: "Metal", bbox,
    primitives: [{ kind: "box", start: [...bbox[0]], stop: [...bbox[1]], bbox: structuredClone(bbox), priority: 1, exact: true }] }];
  b.ports = [{ ...b.ports[0], start: coordinates([-2, -1, 0.5]), stop: coordinates([2, 1, 1.5]) }];
  b.half_space = { axis: "z", side: "min", position: -1.25 / scale, kind: "PEC" };
  b.domain = { min: coordinates([-20, -30, -5]), max: coordinates([20, 30, 15]) };
  b.mesh = { x: coordinates([-20, 0, 20]), y: coordinates([-30, 0, 30]), z: coordinates([-5, 0, 15]),
    cells: [2, 2, 2], total_cells: 8, min_cell: 0.05 / scale, max_cell: 2 / scale };
  b.nf2ff_box = b.nf2ff_center = b.focus = null;
  b.solver.max_timesteps = 1000;
  b.run = { ...b.run, converged: false, hit_timestep_limit: true, timesteps: 1000, excitation_timesteps: 800, timestep_s: 1e-12 };
  return b;
}

let expectedReadme;
for (const unit of [1e-3, 1e-2, 1, undefined]) {
  const b = fixture(unit);
  const before = JSON.stringify(b);
  assert.deepEqual(meshText(b), {
    cells: "2 × 2 × 2 = 8", size: "0.0500 – 2.000 mm", domain: "(-20, -30, -5) to (20, 30, 15) mm",
  }, `${unit}: mesh and domain text use mm`);
  const readme = readmeReport(b, markdownOptions);
  expectedReadme ??= readme;
  assert.equal(readme, expectedReadme, `${unit}: equivalent bundles produce identical Markdown`);
  assert.ok(readme.includes("| Physical patch | PEC | 1 | — | — | 30 × 20 × 1.5 |"), "Markdown part dimensions");
  assert.ok(readme.includes("at z = -1.25 mm"), "Markdown half-space position");
  assert.ok(readme.includes("| -2, -1, 0.5 | 2, 1, 1.5 |"), "Markdown port coordinates");

  const pages = reportPages(b, pdfOptions);
  const summary = texts(pages[0]);
  const tables = pages.filter((page) => page.includes(">Geometry and materials<")).map(texts).join(" ");
  assert.ok(summary.includes("smallest cell (0.050 mm)"), `${unit}: PDF convergence hint uses mm`);
  for (const expected of ["30 × 20 × 1.5", "at z = -1.25 mm", "-2, -1, 0.5", "2, 1, 1.5",
    "cell size 0.0500 – 2.000 mm", "(-20, -30, -5) to (20, 30, 15) mm"])
    assert.ok(tables.includes(expected), `${unit}: PDF setup tables include ${expected}`);
  assert.equal(JSON.stringify(b), before, `${unit}: reports do not rescale the original bundle`);
}

// 0.07 cm * 10 used to print 0.7000000000000001. Keep significant digits rather than
// fixed decimal places: even a sub-nanometre coordinate must not become a false zero.
let expectedOffGrid;
for (const unit of [1e-3, 1e-2, 1, undefined]) {
  const b = fixture(unit), toMm = (unit ?? 1e-3) * 1000;
  b.ports[0].start = [0.7, 7e-10, 0].map((v) => v / toMm);
  b.ports[0].stop = [1.4, 0, 0].map((v) => v / toMm);
  b.half_space.position = -0.7 / toMm;
  const before = JSON.stringify(b);
  const readme = readmeReport(b, markdownOptions);
  const tables = reportPages(b, pdfOptions).filter((page) => page.includes(">Geometry and materials<")).map(texts).join(" ");
  for (const [format, output] of [["Markdown", readme], ["PDF", tables]]) {
    assert.ok(output.includes("0.7, 7e-10, 0"), `${unit}: ${format} coordinates use compact significant digits without losing small values`);
    assert.ok(output.includes("1.4, 0, 0"), `${unit}: ${format} stop coordinates use the same formatter`);
    assert.ok(output.includes("at z = -0.7 mm"), `${unit}: ${format} half-space has no binary arithmetic residue`);
    assert.ok(!output.includes("0000000001"), `${unit}: ${format} has no scaled-coordinate floating-point noise`);
  }
  expectedOffGrid ??= { readme, tables };
  assert.deepEqual({ readme, tables }, expectedOffGrid, `${unit}: off-grid report values agree across model units`);
  assert.equal(JSON.stringify(b), before, "formatting off-grid report values does not alter the bundle");
}
console.log("check-report-units: passed (Markdown/PDF tables, mm/cm/m/legacy, off-grid/tiny coordinates, no input mutation)");
