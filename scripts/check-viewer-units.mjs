// Render the real Examples panels with physically identical bundles in mm, cm and m. Geometry
// stays in model coordinates; every displayed length (including hints) and cells/wavelength must
// agree. No run server, browser, solver or files written to a user's workspace are needed.
//   node scripts/check-viewer-units.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import solid from "vite-plugin-solid";

const root = fileURLToPath(new URL("../", import.meta.url)).replaceAll("\\", "/");
const read = (path) => readFileSync(`${root}${path}`, "utf8");
const built = await build({
  root, configFile: false, logLevel: "silent", css: { postcss: {} },
  plugins: [solid({ ssr: true }), {
    name: "viewer-units-entry", enforce: "pre",
    resolveId(id, importer) {
      if (id.endsWith("viewer-units-entry")) return "\0viewer-units-entry";
      if (id === "solid-js") {
        const markup = importer?.endsWith(".tsx") || importer?.replaceAll("\\", "/").endsWith("/web/dist/server.js");
        return `${root}node_modules/solid-js/dist/${markup ? "server" : "solid"}.js`;
      }
    },
    load(id) {
      if (id !== "\0viewer-units-entry") return;
      const src = (path) => JSON.stringify(`${root}src/${path}`);
      return [
        `export { default as ModelPanel } from ${src("components/ModelPanel.tsx")};`,
        `export { default as SpecPanel } from ${src("components/SpecPanel.tsx")};`,
        `export * as state from ${src("state.ts")};`,
        `export * as i18n from ${src("i18n/index.ts")};`,
        `export { dims } from ${src("lib/format.ts")};`,
        `export { modelToMm } from ${src("lib/modelUnits.ts")};`,
        'export { renderToString } from "solid-js/web";',
        'export { createComponent } from "solid-js";',
      ].join("\n");
    },
  }],
  ssr: { noExternal: true },
  build: { ssr: "viewer-units-entry", write: false, minify: false, rollupOptions: { output: { format: "es", inlineDynamicImports: true } } },
});
const chunk = (Array.isArray(built) ? built[0] : built).output.find((item) => item.type === "chunk");
const { ModelPanel, SpecPanel, state, i18n, dims, modelToMm, renderToString, createComponent } =
  await import(`data:text/javascript;base64,${Buffer.from(chunk.code).toString("base64")}`);
const source = JSON.parse(read("public/projects/patch-antenna.json"));
const text = (html) => html.replace(/<!--[^>]*-->/g, "").replace(/<[^>]*>/g, " ")
  .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const snapshot = (Component) => renderToString(() => createComponent(Component, {}));

function fixture(unit) {
  const b = structuredClone(source);
  const toMm = (unit ?? 1e-3) * 1000;
  const coordinates = (values) => values.map((value) => value / toMm);
  b.units = { length: unit === 1 ? "m" : unit === 1e-2 ? "cm" : "mm", length_m: unit ?? 1e-3, frequency: "Hz" };
  if (unit === undefined) delete b.units; // legacy bundle: no explicit length metadata
  b.model.params = [];
  const bbox = [coordinates([-15, -10, 0]), coordinates([15, 10, 1.5])];
  b.parts = [{ name: "patch", label: "Physical patch", type: "Metal", bbox,
    primitives: [{ kind: "box", start: [...bbox[0]], stop: [...bbox[1]], bbox: structuredClone(bbox), priority: 1, exact: true }] }];
  b.ports = [];
  b.lumped_elements = [];
  b.half_space = { axis: "z", side: "min", position: -1.25 / toMm, kind: "PEC" };
  const lines = [...Array.from({ length: 21 }, (_, index) => -20 + 2 * index), -2.5, -2.45].sort((a, b) => a - b);
  const cells = lines.length - 1;
  b.mesh = { x: coordinates(lines), y: coordinates(lines), z: coordinates(lines), cells: [cells, cells, cells], total_cells: cells ** 3,
    min_cell: 0.05 / toMm, max_cell: 2 / toMm };
  b.domain = { min: coordinates([-20, -20, -20]), max: coordinates([20, 20, 20]) };
  b.nf2ff_box = b.nf2ff_center = b.focus = null;
  b.solver.excitation.f_max = 3e9;
  b.solver.max_timesteps = 1000;
  b.run = { ...b.run, converged: false, hit_timestep_limit: true, timesteps: 1000, excitation_timesteps: 800, timestep_s: 1e-12 };
  b.results = null;
  delete b.fields;
  b.field_planes = [{ quantity: "E", component: "abs", normal: "z", axis: 2, position_mm: 2.5 / toMm, f: 2.45e9 }];
  return b;
}

for (const language of ["en", "tr"]) {
  i18n.setLanguage(language);
  let expected;
  for (const unit of [1e-3, 1e-2, 1, undefined]) {
    const b = fixture(unit);
    const before = JSON.stringify(b);
    state.setBundle(b);
    state.setLayers("mesh", true);
    state.setMeshPlane({ axis: "z", index: 9 });
    const model = snapshot(ModelPanel), spec = snapshot(SpecPanel);
    const actual = { model: text(model), spec: text(spec), titles: [...model.matchAll(/title="([^"]*)"/g)].map((match) => match[1]) };
    expected ??= actual;
    assert.deepEqual(actual, expected, `${language}/${unit}: physically equivalent models have identical readouts and hints`);
    const decimal = language === "tr" ? "," : ".";
    assert.ok(actual.model.includes(`30 × 20 × 1${decimal}5 mm`), "part dimensions are mm");
    assert.ok(actual.titles.some((title) => title.includes(`30 × 20 × 1${decimal}5 mm`)), "truncated part hint uses the same mm");
    assert.ok(actual.model.includes(`z = −1${decimal}25 mm`), "half-space position is mm");
    assert.ok(actual.model.includes(`z = −2${decimal}500 mm`), "mesh-plane position is mm");
    assert.ok(actual.model.includes(`z = 2${decimal}5 mm`), "already-scaled field-plane labels are not scaled twice");
    assert.ok(actual.spec.includes(`0${decimal}050 mm`), "smallest cell is mm");
    assert.ok(actual.spec.includes(`2${decimal}00 mm`), "largest cell is mm");
    assert.ok(actual.spec.includes(`50${decimal}0`), "3 GHz wavelength / 2 mm cell is 50.0, independent of model units");
    if (language === "en") assert.ok(actual.spec.includes("smallest cell (0.050 mm)"), "convergence explanation uses mm too");
    assert.equal(dims(b.parts[0].bbox, modelToMm(b)), `30 × 20 × 1${decimal}5 mm`);
    assert.equal(JSON.stringify(b), before, "formatting never rescales or mutates the bundle");
  }
}

// The 3D viewer's hover and screen-reader descriptions share the same formatter. Keep those
// two callers explicit so they cannot silently fall back to assuming millimetres again.
const viewport = read("src/scene/Viewport.tsx");
assert.match(viewport, /dims\(p\.bbox, modelToMm\(b\)\)/);
assert.match(viewport, /dims\(tipPart\(\)!\.bbox, modelToMm\(bundle\(\)\)\)/);
console.log("check-viewer-units: passed (real panels, mm/cm/m/legacy, EN/TR, hints, mesh, convergence, no mutation)");
