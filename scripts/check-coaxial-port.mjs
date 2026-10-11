import assert from "node:assert/strict";
import { designChecks } from "../src/designer/checks.ts";
import { quickBundle } from "../src/designer/geometry.ts";
import { cstMacro } from "../src/export/cst.ts";
import { validateBundle } from "../src/lib/validate.ts";
const port = {number:1,type:"coaxial",mode:"TEM",direction:"z",start:[0,0,0],stop:[0,0,10],r_i:0.5,r_o:1.5,r_os:2,conductor:"PEC",dielectric:null,feed_shift:0,measurement_shift:5,termination:"open",excite:true};
const design = {schema:"fairbeam.design/1",model:{id:"coax-test",name:"Coax test"},params:[],materials:[{name:"PEC",kind:"metal"}],parts:[],ports:[port],simulation:{f_min:1,f_max:3,boundaries:"PML_8"},mesh:{mode:"auto"},farfield:{enabled:false}};
assert.ok(designChecks(design).some(c=>c.code==="coax-mesh" && c.severity==="warning"));
assert.deepEqual(designChecks(design).filter(c=>c.severity==="error"),[]);
for (const patch of [{r_i:0},{r_i:1.25},{r_o:0.4},{r_os:1.5},{stop:[1,0,10]},{measurement_shift:10},{feed_shift:5,measurement_shift:4},{R:50},{mode:"TE10"},{conductor:"missing"}]) {
 const d=structuredClone(design);Object.assign(d.ports[0],patch);
 assert.ok(designChecks(d).some(c=>c.code==="coax-port"&&c.path==="ports[0]"),JSON.stringify(patch));
}
const bundle=quickBundle(design,{},null);assert.ok(bundle);assert.equal(bundle.ports[0].type,"coaxial");assert.equal(bundle.ports[0].mode,"TEM");assert.equal(bundle.ports[0].conductor,"PEC");assert.ok(bundle.ports[0].R>0);assert.equal(bundle.parts.length,1);assert.equal(bundle.parts[0].primitives[1].kind,"cylindricalshell");assert.equal(bundle.parts[0].primitives[0].radius,0.5);
const filled=structuredClone(design);filled.materials.push({name:"PTFE",kind:"dielectric",eps_r:2.1,mu_r:1,tan_d:0});filled.ports[0].dielectric="PTFE";const fb=quickBundle(filled,{},null);assert.equal(fb.parts.length,2);assert.equal(fb.parts[1].material.eps_r,2.1);assert.ok(validateBundle(bundle).bundle);
const macro=cstMacro(bundle);assert.ok(macro.warnings.some(w=>/coaxial.*unsupported/.test(w)));assert.ok(!macro.text.includes('With DiscretePort'));
console.log("coaxial TS contract checks passed");
