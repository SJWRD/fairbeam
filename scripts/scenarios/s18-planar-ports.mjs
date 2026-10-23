// Native port editing and persistence, without a solver or private model.
import assert from 'node:assert/strict';
export default {
 id:'S18', title:'Native planar ports keep their reference and editable feed contract',
 async run(s,ctx){
  await s.step('create a starter and select its port',async()=>{
   await s.page.goto(s.url,{waitUntil:'domcontentloaded'});await s.wait('.home');
   await s.fill(await s.field(await s.T('home.newProject.name')),`Planar ${s.lang} ${ctx.stamp}`,{blur:false});
   await(await s.wait('label, .home-tpl',await s.T('templates.patch.name'))).click();
   await s.click('home.newProject.create');await s.wait('.rb');
   // A complete synthetic feed is required: a starter's lumped gap is not
   // a valid planar line, and cannot qualify a native preview by changing type.
   await s.store((_,m)=>m.s.edit(d=>{
    d.params=[];d.materials=[{name:'metal',kind:'metal'},{name:'substrate',kind:'dielectric',eps_r:3}];
    const box=(name,material,start,stop)=>({name,material,primitives:[{kind:'box',start,stop}]});
    d.parts=[box('substrate','substrate',[-12,-60,0],[12,60,1]),box('ground','metal',[-12,-60,0],[12,60,0])];
    d.ports=[{type:'microstrip',number:1,conductor:'metal',start:[-1,-60,1],stop:[1,-6,0],direction:'y',excitation_direction:'z',feed_shift:30,measurement_shift:45,reference_ohm:50,termination:'open',excite:true}];
    d.resistors=[];d.far_field={enabled:false};d.simulation={f_min:1,f_max:2,boundaries:['PML_8','PML_8','PML_8','PML_8','PML_8','PML_8']};
    d.mesh={mode:'auto',cells_per_wavelength:30,dielectric_cells:8,edge_res:.25,refine_features:true};
   }));
   await s.store((_,m)=>m.s.setSelection({type:'port',i:0}));
  });
  await s.step('select microstrip and display its physical contract',async()=>{
   await s.click('props.port.microstrip',{sel:'button',exact:true});
   await s.wait('.note',await s.T('props.port.notePlanar'));
   assert.equal(await s.store((_,m)=>m.s.draft.ports[0].type),'microstrip');
   await(await s.field(await s.T('props.port.excitationDirection'))).select('z');
   await s.fill(await s.field(await s.T('props.port.realReference')),'75');
   assert.equal(Number(await s.store((_,m)=>m.s.draft.ports[0].reference_ohm)),75);
  });
  await s.step('select CPW, edit its gap and save/reload without a fallback',async()=>{
   await s.store((_,m)=>m.s.edit(d=>{
    d.parts=d.parts.filter(p=>p.name!=='ground');
    for(const [name,a,b] of [['left',[-12,-60,1],[-1.7,60,1]],['right',[1.7,-60,1],[12,60,1]]])
      d.parts.push({name,material:'metal',primitives:[{kind:'box',start:a,stop:b}]});
    Object.assign(d.ports[0],{type:'cpw',start:[-1,-60,1],stop:[1,-6,1],gap:.7,direction:'y',excitation_direction:'x',feed_shift:30,measurement_shift:45});
   }));
   await s.click('props.port.cpw',{sel:'button',exact:true});
   await s.fill(await s.field(await s.T('props.port.cpwGap')),'0.7');
   await s.fill(await s.field(await s.T('props.port.realReference')),'75');
   const before=await s.store((_,m)=>JSON.parse(JSON.stringify(m.s.draft.ports[0])));
   assert.equal(before.type,'cpw');assert.equal(Number(before.gap),.7);
   await s.click('ribbon.tab.home',{sel:'.rb-tab'});
   await s.click('common.save',{sel:'.rb-btn'});
   await s.waitFor(async()=>!(await import('/src/designer/store.ts')).dirty(),null,{what:'native planar save',timeout:20000});
   await s.page.reload({waitUntil:'domcontentloaded'});await s.wait('.rb');
   assert.deepEqual(await s.store((_,m)=>JSON.parse(JSON.stringify(m.s.draft.ports[0]))),before);
   await s.store((_,m)=>m.s.setSelection({type:'port',i:0}));
   await s.wait('.note',await s.T('props.port.notePlanar'));
  });
 }
};
