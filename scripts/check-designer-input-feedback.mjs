import assert from 'node:assert/strict';
import { build } from 'vite';
import { fileURLToPath } from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url)).replaceAll('\\','/');
const built=await build({root,configFile:false,logLevel:'silent',resolve:{conditions:['browser']},plugins:[{
 name:'designer-feedback-entry',resolveId(id){if(id.endsWith('designer-feedback-entry'))return '\0designer-feedback-entry';},
 load(id){if(id==='\0designer-feedback-entry')return ['designer/sessionCore','i18n/index','designer/parameterTransfer','designer/checkText','designer/expr','designer/sweepPlan','designer/sessionIO'].map((p,i)=>`export * as m${i} from ${JSON.stringify(root+'src/'+p+'.ts')};`).join('\n');},
}],build:{write:false,minify:false,lib:{entry:'designer-feedback-entry',formats:['es']}}});
const code=(Array.isArray(built)?built[0]:built).output[0].code;
const {m0:c,m1:i18n,m2:transfer,m3:text,m4:expr,m5:sweep,m6:ioFactory}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const design={schema:'fairbeam.design/1',model:{id:'fixture',name:'Fixture'},params:[{key:'W',default:10}],materials:[],parts:[],ports:[],resistors:[],simulation:{f_min:1,f_max:2},mesh:{},far_field:{enabled:false}};
const changed=[];const session=c.createDesignerSession({edited:v=>changed.push(v),restored:v=>changed.push(v)});
session.takeFile({id:'fixture',hash:'base',file:'fixture.design.json',design});
const before=session.asyncState.ticket();
session.setChecksTicket(before);
session.edit(d=>d.parameter_sweep={schema:'fairbeam.parameter-sweep/1',sequences:[{name:'Plan',axes:[]}]},'parameter_sweep');
assert.equal(session.dirty(),true,'plan metadata is still saved and backed up');
assert.equal(session.asyncState.isDraftCurrent(before),false,'pending saves/navigation still see a newer draft');
assert.equal(session.asyncState.isPreviewCurrent(before),true,'in-flight geometry and existing checks stay current');
assert.deepEqual(changed,[false],'a plan edit must not rebuild geometry');
session.undo();session.redo();assert.deepEqual(changed,[false,false,false],'plan-only undo and redo do not rebuild');
const pending=session.asyncState.ticket();session.edit(d=>d.params[0].default=11,'parameter_sweep');
assert.equal(changed.at(-1),true,'using the metadata history key cannot hide a geometry edit');
assert.equal(session.asyncState.isPreviewCurrent(pending),false,'real geometry edits still retire preview work');
session.registerDeriveHook(d=>d.params[0].default++);
session.edit(d=>d.parameter_sweep.sequences[0].name='Derived','parameter_sweep');
assert.equal(changed.at(-1),true,'derived geometry also invalidates a plan edit');
session.registerDeriveHook(null);
session.takeFile({id:'fixture',hash:'base',file:'fixture.design.json',design});
assert.equal(session.asyncState.isPreviewCurrent(before),false,'document replacement still retires all preview work');
session.restoreBackup({...design,model:{...design.model,name:'Recovered'}},123);
for(const lang of ['en','tr']){
 i18n.setLanguage(lang);
 assert.equal(session.undoLabel(),i18n.t('history.restoredBackup'));
 assert.equal(session.historySteps().at(-1).label,i18n.t('history.restoredBackup'));
 session.undo();assert.equal(session.redoLabel(),i18n.t('history.restoredBackup'));session.redo();
}
i18n.setLanguage('tr');
const restored=c.createDesignerSession(),at=Date.UTC(2026,0,1,13,45);
const saved={id:'clock',hash:'base',file:'clock.design.json',design};
const io=ioFactory.createDesignerIO(restored,{
 transport:{design:async()=>saved,saveDesign:async()=>{throw new Error('unexpected save');}},
 rememberLastDesign:()=>{},readBackup:()=>({at,design:{...design,model:{...design.model,name:'Recovered'}}}),clearBackup:()=>{},
 stopPreview:()=>{},forgetPreviewFailure:()=>{},resetParamAsks:()=>{},schedulePreview:()=>{},applyModelEntry:()=>{},restoreModelSelection:()=>{},
});
const hostClock=Date.prototype.toLocaleTimeString;
try{
 Date.prototype.toLocaleTimeString=()=> 'HOST CLOCK';
 await io.openDesign('clock');
 assert.equal(restored.message().text,i18n.t('store.restored',{at:i18n.fmt.dateTime(at,{hour:'2-digit',minute:'2-digit'})}),'recovery time follows the app language, not the host clock');
}finally{Date.prototype.toLocaleTimeString=hostClock;io.dispose();restored.dispose();}
for(const key of ['9bad','for','sin'])assert.notEqual(text.parameterErrorTitle(expr.paramKeyError(key)),expr.paramKeyError(key));
const row={key:'W',expression:'2',evaluated:'2',unit:'mm',description:''};
const errors=[];
for(const [input,format] of [['abc','csv'],['key,expression,evaluated,unit,description\n"unfinished','csv'],['{','json'],['{}','json'],['[{}]','json']]){
 try{transfer.parseTransfer(input,format);assert.fail('bad import accepted');}catch(e){errors.push(e.message);}
}
errors.push(transfer.previewTransfer([row,row],[])[1].message,transfer.previewTransfer([{...row,expression:'Unknown/2'}],[])[0].message);
for(const error of errors)assert.notEqual(text.parameterErrorTitle(error),error,error);
const plan=sweep.planParameterSweep({schema:'fairbeam.parameter-sweep/1',sequences:[{name:'Test',axes:[{kind:'range',key:'W',start:'Missing',stop:'20',steps:2}]}]},design,{W:10});
assert.ok(plan.error);assert.doesNotMatch(plan.error,/unknown name/i);
i18n.setLanguage('en');assert.match(text.parameterErrorTitle(errors[0]),/CSV header/,'an existing raw error responds to language changes');
session.dispose();console.log('Designer feedback: metadata preview/save ownership, undo, restore labels and parameter diagnostics passed');


