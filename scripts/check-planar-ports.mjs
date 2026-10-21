import assert from "node:assert/strict";
import { quickBundle } from "../src/designer/geometry.ts";
import { designChecks } from "../src/designer/checks.ts";
import { portLine } from "../src/drawing/geometry.ts";
import { cstMacro, DEFAULT_CST_OPTIONS } from "../src/export/cst.ts";
import { validateBundle } from "../src/lib/validate.ts";
import { cstGeometryOnly, packageFiles } from "../src/export/package.ts";

for (const type of ["microstrip","cpw"]) {
  const design={schema:"fairbeam.design/1",model:{id:"native-planar",name:"Synthetic planar preview"},params:[],
    materials:[{name:"metal",kind:"metal"},{name:"substrate",kind:"dielectric",eps_r:3}],
    parts:[{name:"ground",material:"metal",primitives:[{kind:"box",start:[-30,-6,0],stop:[30,6,0]}]}],
    ports:[{type,number:1,conductor:"metal",start:[-30,-1,1],stop:[-5,1,type==="cpw" ? 1 : 0],
      direction:"x",excitation_direction:type==="cpw" ? "y":"z",gap:.6,feed_shift:6,
      measurement_shift:18,reference_ohm:75,termination:"pml",excite:true}],
    simulation:{f_min:1,f_max:2,boundaries:"PML_8"},mesh:{mode:"auto"},far_field:{enabled:false},resistors:[]};
  assert.equal(designChecks(design).some(c=>c.severity==="error"&&c.code==="planar-port"),false);
  const bundle=quickBundle(design,{},null);
  assert.ok(bundle);
  bundle.created="2026-01-01T00:00:00Z";
  const p=bundle.ports[0];assert.equal(p.type,type);assert.equal(p.R,75);assert.equal(p.reference_ohm,75);
  assert.equal(p.excitation_direction,type==="cpw" ? "y":"z");
  const strip=bundle.parts.find(part=>part.name==="Planar port 1 strip");
  assert.ok(strip);assert.equal(strip.bbox[0][2],1);assert.equal(strip.bbox[1][2],1);
  const [a,b]=portLine(p);assert.equal(a[0],-24);assert.equal(b[0],-12);
  const normalized=validateBundle(structuredClone(bundle)).bundle;
  assert.ok(normalized);
  assert.throws(()=>cstMacro(bundle),/no substitute port/);
  assert.doesNotThrow(()=>cstMacro(bundle,{...DEFAULT_CST_OPTIONS,includePorts:false}));
  assert.equal(cstGeometryOnly(bundle),true);
  const files=packageFiles(normalized,{project:true,cst:true,readme:true},{},new Date(2026,9,21));
  const macro=files.find(f=>f.path.endsWith(".bas"));
  assert.match(macro.description,/geometry only/);
  assert.match(macro.data,/native planar ports require an explicit modal-feed definition/);
  assert.doesNotMatch(macro.data,/With (?:DiscretePort|Port)\b/);
  assert.equal(JSON.parse(files.find(f=>f.path==="project.json").data).ports[0].type,type);
  assert.match(files.find(f=>f.path==="README.md").data,/geometry only/);
  const bad=structuredClone(bundle);bad.ports[0].R=50;
  assert.equal(validateBundle(bad).bundle.ports.length,0);
  const manual=structuredClone(design);
  manual.mesh={mode:"manual",lines:{x:[-50,-24,-13,-12,-11,0,50],y:[-15,-2,-1.6,-.5,0,.5,1.6,2,15],z:[-5,-1,0,1,2,5]}};
  assert.equal(designChecks(manual).some(c=>c.severity==="error"&&c.path.startsWith("ports[")),false);
}
console.log("Native planar preview, references, manual edge freedom, drawing, bundle and geometry-only package checks passed.");
