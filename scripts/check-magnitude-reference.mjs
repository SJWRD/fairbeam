// Magnitude-only measurements never acquire an invented phase in plots or exports.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {importReference} from '../src/import/reference.ts';
import {compareSParams, compareSParamQuantities, traces} from '../src/compare/series.ts';
import {copyResultData, exportResultCsv, resultDataTable} from '../src/designer/resultData.ts';
import {exportResultTouchstone} from '../src/designer/resultTouchstone.ts';
import {touchstoneS1p, touchstoneNPort} from '../src/export/touchstone.ts';
import {hasSParameterPhase, sMatrix} from '../src/lib/sparams.ts';
const project=JSON.parse(readFileSync(new URL('../public/projects/inset-patch.json',import.meta.url),'utf8'));
const ref=importReference('magnitude.csv','Frequency (GHz),S11 Magnitude (dB)\n2,-10\n3,-20',project);
const complex=importReference('complex.csv','Frequency (GHz),S11 Real,S11 Imaginary\n2,0.1,0.2\n3,0.2,0.1',project);
const tr=traces(project,[ref,complex]);
assert.equal(hasSParameterPhase(ref),false);assert.equal(hasSParameterPhase(complex),true);
assert.equal(compareSParams(tr,[[1,1]],'db').series.length,3);
assert.equal(compareSParams(tr,[[1,1]],'phase').series.length,2);
for(const format of ['plot','db','db_phase','re_im','mag_phase','all'])for(const mode of ['db','phase']){
 const groups=compareSParamQuantities(tr,[[1,1]],format,mode);
 for(const group of groups){
  assert.equal(group.series.some(s=>s.label.includes('magnitude.csv')),['db','mag'].includes(group.key),`${format}/${mode}/${group.key}`);
  assert.ok(group.series.some(s=>s.label.includes('complex.csv')),'measured complex reference remains');
 }
}
const db=resultDataTable(ref,'sparams',undefined,{format:'db'});
assert.ok(Math.abs(db.rows[0][1]+10)<1e-12);assert.ok(Math.abs(db.rows[1][1]+20)<1e-12);
const all=resultDataTable(ref,'sparams',undefined,{format:'all'});
assert.deepEqual(all.rows[0].slice(2,5),[null,null,null],'unknown phase and RI cells stay missing');
assert.equal(resultDataTable(ref,'smith').rows.length,0);
assert.equal(resultDataTable(ref,'smith',undefined,{smithPort:1}).rows.length,0);
assert.equal(resultDataTable(ref,'impedance').rows.length,0);
const descriptor=Object.getOwnPropertyDescriptor(globalThis,'navigator');let copied='unchanged';
Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async text=>{copied=text;}}}});
try{
 const runs=[{file:'project.json',bundle:project},{file:'reference.csv',bundle:ref}];
 for(const format of ['db_phase','re_im','mag_phase','all']){
  const result=await copyResultData(project,'sparams',undefined,runs,{format});
  assert.equal(result.ok,false);assert.match(result.message,/phase data is missing/);assert.equal(copied,'unchanged');
  await assert.rejects(exportResultCsv(project,'sparams','blocked.csv',undefined,runs,{format}),/phase data is missing/);
 }
 assert.equal((await copyResultData(ref,'sparams',undefined,undefined,{sparamMode:'phase'})).ok,false);
 assert.equal((await copyResultData(ref,'smith')).ok,false);
 assert.equal((await copyResultData(ref,'sparams',undefined,undefined,{format:'db'})).ok,true);
 assert.ok(copied.includes('|S11| (dB)'));assert.ok(!copied.includes('∠'));
 assert.equal((await copyResultData(complex,'sparams',undefined,undefined,{format:'all'})).ok,true);
}finally{if(descriptor)Object.defineProperty(globalThis,'navigator',descriptor);else delete globalThis.navigator;}
assert.equal(touchstoneS1p(ref),null);assert.equal(touchstoneNPort(ref),null);
assert.ok(touchstoneS1p(complex));
await assert.rejects(exportResultTouchstone(ref,'blocked'),/phase data is missing/);
await assert.rejects(exportResultTouchstone(project,'blocked',[{file:'a',bundle:project},{file:'b',bundle:ref}]),/phase data is missing/);

// Add File replaces the active network sweep, not just the legacy S11 fields. The old
// full matrix must not win over a newer CSV/S1P on an equal-sized or changed frequency grid.
const oldNetwork='# GHz S RI R 50\n2 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8\n3 0.2 0.1 0.4 0.3 0.6 0.5 0.8 0.7\n';
const oldRef=importReference('old.s2p',oldNetwork,project);
const untouched=JSON.stringify(oldRef);
const magnitudeText='Frequency (GHz),S11 Magnitude (dB)\n2,-10\n3,-20';
const replaced=importReference('new.csv',magnitudeText,project,oldRef);
assert.equal(replaced.results.sparams,undefined);
assert.deepEqual(sMatrix(replaced).pairs,[[1,1]]);
assert.equal(sMatrix(replaced).get(2,1),null,'removed network columns cannot survive');
const replacementTable=resultDataTable(replaced,'sparams',undefined,{format:'db'});
assert.ok(Math.abs(replacementTable.rows[0][1]+10)<1e-12);
assert.ok(Math.abs(replacementTable.rows[1][1]+20)<1e-12);
assert.equal(hasSParameterPhase(replaced),false);
for(const group of compareSParamQuantities(traces(project,[replaced]),[[1,1]],'all')){
 assert.equal(group.series.some(s=>s.label.includes('new.csv')),['db','mag'].includes(group.key));
}
const newest=importReference('new-phase.csv','Frequency (GHz),S11 Real,S11 Imaginary\n2,0.3,0.3\n3,0.2,-0.2',project,replaced);
assert.deepEqual(sMatrix(newest).get(1,1),{re:[0.3,0.2],im:[0.3,-0.2]});
const newestTable=resultDataTable(newest,'sparams',undefined,{format:'db_phase'});
assert.ok(Math.abs(newestTable.rows[0][2]-45)<1e-12);
assert.ok(Math.abs(newestTable.rows[1][2]+45)<1e-12);
assert.ok(touchstoneS1p(newest));
assert.equal(touchstoneNPort(newest),null,'single-port replacement cannot export an obsolete full matrix');
for(const text of [magnitudeText.replace('2,-10','4,-10').replace('3,-20','5,-20'),magnitudeText+'\n4,-30']){
 const different=importReference('different-grid.csv',text,project,oldRef);
 assert.deepEqual(sMatrix(different).f,different.results.frequency);
 assert.deepEqual(sMatrix(different).pairs,[[1,1]]);
 assert.ok(Math.abs(resultDataTable(different,'sparams',undefined,{format:'db'}).rows[0][1]+10)<1e-12);
}
const onePort=importReference('replacement.s1p','# GHz S RI R 50\n4 0.3 -0.4\n5 0.4 -0.3',project,oldRef);
assert.deepEqual(sMatrix(onePort).get(1,1),{re:[0.3,0.4],im:[-0.4,-0.3]});
assert.deepEqual(sMatrix(onePort).f,[4e9,5e9]);
assert.deepEqual(sMatrix(onePort).pairs,[[1,1]]);
const nextNetwork=importReference('new-network.s2p',oldNetwork.replaceAll('0.1','0.9'),project,newest);
assert.deepEqual(sMatrix(nextNetwork).ports,[1,2]);
assert.equal(sMatrix(nextNetwork).get(1,1).re[0],0.9);
assert.equal(nextNetwork.reference.phaseKnown,true);
assert.deepEqual(newest.reference.files,['old.s2p','new.csv','new-phase.csv']);
assert.equal(newest.reference.raw[1].text,magnitudeText,'source files remain available unchanged');
assert.equal(JSON.stringify(oldRef),untouched,'successful replacement does not mutate the previous reference');
assert.throws(()=>importReference('bad.csv','no sweep data',project,oldRef));
assert.equal(JSON.stringify(oldRef),untouched,'failed replacement leaves the previous reference intact');
console.log('Magnitude-only reference: magnitude preserved; phase/RI/Smith omitted; complex clipboard, CSV and Touchstone blocked; complex references retained.');
console.log('Sequential reference imports: matrix, grid, plotted quantities and exported values follow the newest sweep; raw sources and failure rollback preserved.');
