import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {sMatrix,physicalPortNumber,mappedPairLabel,reflectionAtPort} from '../src/lib/sparams.ts';
import {activeReflection,uniformWeights,combine} from '../src/lib/array.ts';
import {arrayWeightsCsv,sparamsCsv,parseCsv} from '../src/export/csv.ts';
import {touchstoneNPort,parseTouchstoneNPort} from '../src/export/touchstone.ts';
import {resultDataTable,comparedResultDataTable} from '../src/designer/resultData.ts';
import {compareSParamQuantities} from '../src/compare/series.ts';
const b=JSON.parse(readFileSync(new URL('../public/projects/branchline-coupler.json',import.meta.url),'utf8'));
b.ports=b.ports.slice(0,2).map((p,i)=>({...p,number:[2,5][i],excite:true}));
b.results.frequency=[1e9];b.results.ports={'2':{z_ref:50,s11_re:[.1],s11_im:[0],zin_re:[550/9],zin_im:[0]},'5':{z_ref:50,s11_re:[.5],s11_im:[0],zin_re:[150],zin_im:[0]}};
b.results.sparams={ports:[1,2],port_numbers:[2,5],z_ref:[50,50],excited:[1,2],s:{'1,1':{re:[.1],im:[0]},'1,2':{re:[.2],im:[0]},'2,1':{re:[.3],im:[0]},'2,2':{re:[.5],im:[0]}}};
const m=sMatrix(b),close=(a,e)=>{assert.equal(a.length,e.length);a.forEach((x,i)=>assert.ok(Math.abs(x-e[i])<1e-12,`${a} != ${e}`));};
assert.deepEqual(m.ports,[1,2]);assert.deepEqual(m.physicalPorts,[2,5]);assert.equal(physicalPortNumber(m,2),5);
assert.equal(mappedPairLabel(m,[2,2]),'S22 [P5]');assert.equal(mappedPairLabel(m,[2,1]),'S21 [P5 <- P2]');
const uniform=uniformWeights([2,5]),g=activeReflection(m,uniform,1e9);close(g.get(2),[.3,0]);close(g.get(5),[.8,0]);assert.ok(!g.has(1));
const unequal=new Map([[2,{ampDb:0,phaseDeg:0}],[5,{ampDb:20*Math.log10(.5),phaseDeg:90}]]);
const gu=activeReflection(m,unequal,1e9);close(gu.get(2),[.1,.1]);close(gu.get(5),[.5,-.6]);
// Missing driven-column data must never appear as a measured zero coupling.
const partial=structuredClone(b);delete partial.results.sparams.s['1,2'];
const pm=sMatrix(partial),pg=activeReflection(pm,uniform,1e9);
assert.equal(pg.get(2),null);close(pg.get(5),[.8,0]);
close(activeReflection(pm,uniformWeights([2]),1e9).get(2),[.1,0]);
const off=new Map([[2,{ampDb:0,phaseDeg:0}],[5,{ampDb:-Infinity,phaseDeg:0}]]);
close(activeReflection(pm,off,1e9).get(2),[.1,0]);assert.equal(activeReflection(pm,off,1e9).get(5),null);
for(const bad of [NaN,Infinity]){const x=structuredClone(b);x.results.sparams.s['1,2'].re=[bad];assert.equal(activeReflection(sMatrix(x),uniform,1e9).get(2),null);}
const badWeight=new Map(uniform);badWeight.set(5,{ampDb:0,phaseDeg:NaN});
assert.ok([...activeReflection(m,badWeight,1e9).values()].every(g=>g===null));
const csv=parseCsv(arrayWeightsCsv(b,unequal,1e9));assert.equal(csv[1][0],'2');assert.equal(csv[2][0],'5');close(csv[1].slice(-2).map(Number),[.1,.1]);close(csv[2].slice(-2).map(Number),[.5,-.6]);
assert.ok(sparamsCsv(b).includes('S22 [P5]_re'));assert.ok(sparamsCsv(b).includes('S21 [P5 <- P2]_re'));
const table=resultDataTable(b,'smith',undefined,{smithPort:2});assert.equal(table.header[3],'Re Zin model port 5 (matrix 2) (Ω)');assert.equal(table.rows[0][3],150);
const ts=touchstoneNPort(b,'test'),parsed=parseTouchstoneNPort(ts,2);assert.ok(ts.includes('1 -> 2, 2 -> 5'));assert.deepEqual(parsed.s[0],[[[.1,0],[.2,0]],[[.3,0],[.5,0]]]);
const identity=structuredClone(b);delete identity.results.sparams.port_numbers;
const mi=sMatrix(identity);assert.equal(mappedPairLabel(mi,[2,2]),'S22');close(activeReflection(mi,uniformWeights([1,2]),1e9).get(1),[.3,0]);
assert.deepEqual([...activeReflection(mi,uniform,1e9)],[[2,null],[5,null]],'unmatched physical weights must not alias matrix ports');
for(const invalid of [null,[2],[2,2],[2,0],[2,1.5],['2',5]]){const x=structuredClone(b);x.results.sparams.port_numbers=invalid;const mm=sMatrix(x);assert.equal(mm.physicalPorts,null);assert.equal(physicalPortNumber(mm,2),null);assert.equal(mappedPairLabel(mm,[2,2]),'S22 [P?]');assert.deepEqual([...activeReflection(mm,uniform,1e9)],[[2,null],[5,null]]);assert.equal(touchstoneNPort(x,'test'),null,'invalid physical mapping must refuse export');}
for(const indices of [[2,1],[1,1],[2,5]]){const x=structuredClone(b);x.results.sparams.ports=indices;assert.equal(sMatrix(x).physicalPorts,null,'noncanonical matrix indices cannot associate physical IDs by order');}
const legacy=structuredClone(b);legacy.results.sparams={port_numbers:[9,9],s:{}};const ml=sMatrix(legacy);assert.ok(ml.legacy);assert.deepEqual(ml.ports,[2,5]);assert.equal(physicalPortNumber(ml,5),5);assert.equal(reflectionAtPort(legacy,5).zRe[0],150);
const b2=structuredClone(b);b2.results.sparams.port_numbers=[3,7];const ct=comparedResultDataTable([{file:'a',bundle:b},{file:'b',bundle:b2}],'smith',undefined,{smithPort:2});assert.ok(ct.header.some(h=>h.includes('model port 5')));assert.ok(ct.header.some(h=>h.includes('model port 7')));
const plots=compareSParamQuantities([{label:'A',bundle:b},{label:'B',bundle:b2}],[[2,2]],'plot');assert.ok(plots[0].series[0].label.includes('P5'));assert.ok(plots[0].series[1].label.includes('P7'));
// Field synthesis consumes physical element IDs already: mapping S metadata must not change it.
const set={f:1e9,theta:[0],phi:[0],elements:[{port:2,eThetaRe:[[1]],eThetaIm:[[0]],ePhiRe:[[0]],ePhiIm:[[0]]},{port:5,eThetaRe:[[2]],eThetaIm:[[0]],ePhiRe:[[0]],ePhiIm:[[0]]}]};
assert.equal(combine(set,uniform)[0][0],9);assert.ok(Math.abs(combine(set,unequal)[0][0]-2)<1e-12);
console.log('Physical port mapping checks passed: gapped uniform/complex weights, CSV/Smith/Touchstone identity, malformed/legacy maps, comparison labels and unchanged field synthesis.');
