// Assumed circuit identities, not solver measurements. Shared inputs with Python tests.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {touchstoneS1p, touchstoneNPort, parseTouchstone, parseTouchstoneNPort} from '../src/export/touchstone.ts';
const fixture=JSON.parse(readFileSync(new URL('../python/tests/fixtures/touchstone_references.json',import.meta.url),'utf8'));
const close=(a,b,tol=1e-12)=>assert.ok(Math.abs(a-b)<=tol, `${a} != ${b}`);
const quotient=([a,b],[c,d])=>[(a*c+b*d)/(c*c+d*d),(b*c-a*d)/(c*c+d*d)];
const gamma=(load,r)=>quotient([load[0]-r,load[1]],[load[0]+r,load[1]]);
function one(load=[600,0]) {
  const samples=fixture.references[0].map(r=>gamma(load,r));
  return {name:'assumed load',model:{id:'fixture'},created:'check',solver:{engine:'synthetic',method:'circuit identity'},generator:{},
    ports:[{number:1,type:'waveguide',excite:true,R:600,direction:'z'}],
    results:{frequency:fixture.frequency,ports:{1:{z_ref:600,z_ref_f:fixture.references[0],
      s11_re:samples.map(s=>s[0]),s11_im:samples.map(s=>s[1]),zin_re:[],zin_im:[]}}}};
}
function through(physical=[1,2]) {
  const b=one();
  b.ports=physical.map(number=>({number,type:'waveguide',excite:true,R:600,direction:'z'}));
  b.results.ports=Object.fromEntries(physical.map((p,i)=>[p,{z_ref:600,z_ref_f:fixture.references[i]}]));
  const s={};
  for(let i=0;i<2;i++)for(let j=0;j<2;j++)s[`${i+1},${j+1}`]={
    re:fixture.frequency.map((_,k)=>{const a=fixture.references[0][k],c=fixture.references[1][k];
      return i!==j?2*Math.sqrt(a*c)/(a+c):(i===0?c-a:a-c)/(a+c);}),im:[0,0,0]};
  b.results.sparams={ports:[1,2],z_ref:[600,600],excited:[1,2],s};
  if(physical[0]!==1||physical[1]!==2)b.results.sparams.port_numbers=physical;
  return b;
}
for(const load of [[600,0],fixture.complexLoad]) {
  const b=one(load),t=parseTouchstone(touchstoneS1p(b,'check')),want=gamma(load,600);
  assert.equal(t.z0,600);
  t.re.forEach((r,k)=>{close(r,want[0]);close(t.im[k],want[1]);});
}
for(const v of [-1,1]) {
  const b=one(); b.results.ports[1].s11_re=[v,v,v]; b.results.ports[1].s11_im=[0,0,0];
  assert.deepEqual(parseTouchstone(touchstoneS1p(b,'check')).re,[v,v,v]);
}
{
  const missing=one();missing.ports.unshift({...missing.ports[0],number:7});
  assert.equal(touchstoneS1p(missing,'check'),null,'an unrecorded excited port must not borrow a different port result');
  missing.ports=[];assert.equal(touchstoneS1p(missing,'check'),null,'stored results need a matching physical port');
  const gapped=one();gapped.ports[0].number=7;gapped.results.ports[7]=gapped.results.ports[1];delete gapped.results.ports[1];
  const text=touchstoneS1p(gapped,'check');assert.match(text,/! Port 7:/);
  parseTouchstone(text).re.forEach(r=>close(r,0));
}
for(const physical of [[1,2],fixture.physicalPorts]) {
  const b=through(physical), t=parseTouchstoneNPort(touchstoneNPort(b,'check'),2);
  assert.equal(t.z0,600);
  for(const m of t.s)for(let i=0;i<2;i++)for(let j=0;j<2;j++){
    close(m[i][j][0],i===j?0:1);close(m[i][j][1],0);
  }
}
// Independent loads exercise frequency-to-reference and physical-port pairing, not only
// a through whose equal-reference result is invariant under the target resistance.
{
  const b=through(fixture.physicalPorts),loads=[[600,120],[340,-75]];
  for(let i=0;i<2;i++)for(let j=0;j<2;j++){
    const values=fixture.references[i].map(r=>i===j?gamma(loads[i],r):[0,0]);
    b.results.sparams.s[`${i+1},${j+1}`]={re:values.map(s=>s[0]),im:values.map(s=>s[1])};
  }
  const t=parseTouchstoneNPort(touchstoneNPort(b,'check'),2);
  for(const m of t.s)for(let i=0;i<2;i++)for(let j=0;j<2;j++){
    const expected=i===j?gamma(loads[i],600):[0,0];
    close(m[i][j][0],expected[0]);close(m[i][j][1],expected[1]);
  }
}
// Unequal constant native references must use C factors too, not entry-wise reflection.
{
  const b=through();
  b.results.frequency=[fixture.frequency[0]];
  for(const p of [1,2]){b.ports[p-1].type='lumped';delete b.results.ports[p].z_ref_f;}
  b.results.sparams.z_ref=[800,500];
  for(const s of Object.values(b.results.sparams.s)){s.re=s.re.slice(0,1);s.im=[0];}
  const t=parseTouchstoneNPort(touchstoneNPort(b,'check'),2);
  assert.equal(t.z0,800);close(t.s[0][0][0][0],0);close(t.s[0][1][0][0],1);
}
for(const invalid of [null,[],[800],[0,600,500],[-1,600,500],[NaN,600,500],[Infinity,600,500],[true,600,500],[[800,1],600,500]]) {
  for(const [create,write] of [[one,touchstoneS1p],[through,touchstoneNPort]]) {
    const b=create();b.results.ports[1].z_ref_f=invalid;
    assert.equal(write(b,'check'),null);
    b.ports[0].type='lumped';assert.equal(write(b,'check'),null,'malformed present vector cannot become a scalar fallback');
  }
}
for(const mapping of [null,[7,7],[7],[7,19.5],[true,19]]) {
  const b=through(fixture.physicalPorts);
  if(mapping===null)delete b.results.sparams.port_numbers;else b.results.sparams.port_numbers=mapping;
  assert.equal(touchstoneNPort(b,'check'),null);
}
for(const invalid of [null,[600],['600',600],[true,600],[NaN,600],-1]) {
  const b=through();b.results.sparams.z_ref=invalid;
  assert.equal(touchstoneNPort(b,'check'),null,'display adapter fallback must not repair malformed export references');
}
for(const invalid of [[2,1],[1,1],[true,2],[1,2.5]]) {
  const b=through(fixture.physicalPorts);b.results.sparams.ports=invalid;
  assert.equal(touchstoneNPort(b,'check'),null,'matrix indices must not alter physical reference pairing');
}
{
  const stale=through(fixture.physicalPorts);stale.results.sparams.z_ref=[50,50];
  for(const [key,values] of Object.entries(stale.results.sparams.s)){
    const [i,j]=key.split(',');values.re=Array(3).fill(i===j?(600-50)/(600+50):0);
  }
  assert.equal(touchstoneNPort(stale,'check'),null,'already-referenced matrix cannot reuse stale native vectors');
  for(const invalid of [null,NaN,true,-600,'600']){
    const b=through();b.results.ports[1].z_ref=invalid;
    assert.equal(touchstoneNPort(b,'check'),null,'native summary must be finite positive and match matrix summary');
  }
  const rounded=through(fixture.physicalPorts);rounded.results.sparams.z_ref=[600.123,600.123];
  for(const pr of Object.values(rounded.results.ports)){pr.z_ref=600.123;pr.z_ref_f=[600.123456,600.123456,600.123456];}
  // Independent fixed loads ensure rounding is not mistaken for the actual reference.
  for(const [key,values] of Object.entries(rounded.results.sparams.s)){
    const [i,j]=key.split(',');values.re=Array(3).fill(i===j?(600-600.123456)/(600+600.123456):0);
  }
  const t=parseTouchstoneNPort(touchstoneNPort(rounded,'check'),2);
  for(const m of t.s)for(let i=0;i<2;i++)for(let j=0;j<2;j++)close(m[i][j][0],i===j?(600-600.123)/(600+600.123):0);
}
for(const [create,write] of [[one,touchstoneS1p],[through,touchstoneNPort]]) {
  const b=create();delete b.results.ports[1].z_ref_f;assert.equal(write(b,'check'),null);
  const unknown=create();unknown.reference={phaseKnown:false};assert.equal(write(unknown,'check'),null);
}
// Preserve the actual horn's input impedance. A raw-S roundtrip under the wrong R would fail this.
{
  const b=JSON.parse(readFileSync(new URL('../public/projects/pyramidal-horn.json',import.meta.url),'utf8'));
  const pr=b.results.ports[1],t=parseTouchstone(touchstoneS1p(b,'check'));
  const impedance=(r,x,z)=>quotient([z*(1+r),z*x],[1-r,-x]);
  t.re.forEach((r,k)=>{
    const expected=impedance(pr.s11_re[k],pr.s11_im[k],pr.z_ref_f[k]);
    const got=impedance(r,t.im[k],t.z0);
    close(got[0],expected[0],1e-9);close(got[1],expected[1],1e-9);
  });
}
console.log('Touchstone reference checks: loads, open/short, power-wave through, physical mapping, invalid references and retained horn impedance pass');
