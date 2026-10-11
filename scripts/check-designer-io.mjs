// Exercise the actual session factory and incumbent default store separately: no solver or disk build.
import assert from 'node:assert/strict';
import {build} from 'vite';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url)).replaceAll('\\','/');
const built=await build({root,configFile:false,logLevel:'silent',resolve:{conditions:['browser']},plugins:[{
 name:'designer-io-entry',resolveId(id){if(id.endsWith('designer-io-entry'))return '\0designer-io-entry';},
 load(id){if(id==='\0designer-io-entry')return `export {createDesignerSession} from ${JSON.stringify(root+'src/designer/sessionCore.ts')};export {createDesignerIO} from ${JSON.stringify(root+'src/designer/sessionIO.ts')};`;},
}],build:{write:false,minify:false,lib:{entry:'designer-io-entry',formats:['es']}}});
let writes=0;globalThis.localStorage={getItem:()=>null,setItem:()=>writes++,removeItem:()=>writes++};
const code=(Array.isArray(built)?built[0]:built).output[0].code;
const {createDesignerSession,createDesignerIO}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const design=id=>({schema:'fairbeam.design/1',model:{id,name:id},params:[],materials:[],parts:[],ports:[],resistors:[],simulation:{},mesh:{}});
const file=(id='same',hash='base',owner='A')=>({id,file:id+'.json',hash,readonly:false,design:design(id),backup_scope:owner});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const valid={valid:true,checks:[],model:{key:'same'}};
const response=hash=>({hash,validation:valid});
const fault=(status,message='failed')=>Object.assign(Error(message),{status,data:status===409?{current_hash:'conflict'}:{},fields:{field:'invalid'}});
function fixture(owner){
 const core=createDesignerSession(),log=[];let get,put;
 const io=createDesignerIO(core,{
  transport:{design:id=>{log.push(['get',id]);return get.promise;},saveDesign:(f,d,hash)=>{log.push(['put',f.backup_scope,d,hash]);return put.promise;}},
  rememberLastDesign:f=>log.push(['remember',f.backup_scope]),readBackup:f=>{log.push(['read',f.backup_scope]);return null;},clearBackup:f=>log.push(['clear',f.backup_scope]),
  stopPreview:()=>log.push(['stop']),forgetPreviewFailure:()=>log.push(['forget']),resetParamAsks:()=>log.push(['reset']),schedulePreview:()=>log.push(['preview']),applyModelEntry:m=>log.push(['model',m.key]),restoreModelSelection:f=>log.push(['selection',f?.id??'']),
 });
 core.takeFile(file('same','base',owner));
 return{core,io,log,nextGet:()=>get=deferred(),nextPut:()=>put=deferred()};
}
const a=fixture('A'),b=fixture('B');
// Same filename/hash does not share save state, errors, backup or request ownership.
let da=a.nextPut(),db=b.nextPut();a.core.edit(d=>d.model.description='A');b.core.edit(d=>d.model.description='B');
let pa=a.io.save(),pb=b.io.save();assert.equal(a.core.saving(),true);assert.equal(b.core.saving(),true);
da.resolve(response('a-saved'));assert.equal(await pa,true);assert.equal(b.core.saving(),true);assert.equal(b.core.file().hash,'base');
db.reject(fault(409));assert.equal(await pb,false);assert.equal(b.core.conflict(),'conflict');assert.equal(a.core.conflict(),null);assert.equal(a.core.dirty(),false);
assert.equal(a.log.find(x=>x[0]==='put')[1],'A');assert.equal(b.log.find(x=>x[0]==='put')[1],'B');
// Current failure belongs to B; obsolete success/failure after reload cannot alter either context.
db=b.nextPut();pb=b.io.save();db.reject(fault(422));assert.equal(await pb,false);assert.deepEqual(b.core.serverErrors(),{field:'invalid'});assert.deepEqual(a.core.serverErrors(),{});
for(const outcome of ['success','conflict','offline']){
 da=a.nextPut();pa=a.io.save();a.core.takeFile(file('same','reload','A'));const before=a.core.historyMark(),message=a.core.message();
 outcome==='success'?da.resolve(response('old')):da.reject(fault(outcome==='conflict'?409:0));assert.equal(await pa,false);assert.equal(a.core.file().hash,'reload');assert.deepEqual(a.core.historyMark(),before);assert.deepEqual(a.core.message(),message);
}
// Save with a newer edit updates its own saved base while retaining dirty state/history.
da=a.nextPut();pa=a.io.save();a.core.edit(d=>d.model.description='newer');da.resolve(response('new-base'));assert.equal(await pa,false);assert.equal(a.core.file().hash,'new-base');assert.equal(a.core.dirty(),true);
// Rename metadata supersedes a save without changing document identity.
da=a.nextPut();pa=a.io.save();a.core.setFile({...a.core.file(),hash:'renamed'});da.reject(fault(409));assert.equal(await pa,false);assert.equal(a.core.file().hash,'renamed');assert.equal(a.core.conflict(),null);
// Deferred opens resolve independently; newer navigation beats both old success and failure.
let ga=a.nextGet(),gb=b.nextGet();let oa=a.io.openDesign('new-a'),ob=b.io.openDesign('new-b');ga.resolve(file('new-a','a','A'));await oa;assert.equal(b.core.loading(),true);gb.resolve(file('new-b','b','B'));await ob;
assert.equal(a.core.file().id,'new-a');assert.equal(b.core.file().id,'new-b');assert(a.log.some(x=>x[0]==='read'&&x[1]==='A'));
for(const fails of [false,true]){ga=a.nextGet();oa=a.io.openDesign('old');const newer=a.nextGet(),on=a.io.openDesign('new');newer.resolve(file('new','next','A'));await on;fails?ga.reject(Error('obsolete')):ga.resolve(file('old'));await oa;assert.equal(a.core.file().id,'new');assert.equal(a.core.message(),null);}
// A real edit while an open is pending revokes replacement permission, even if its reply fails.
for(const fails of [false,true])for(const undo of [false,true]){
 const x=fixture('pending-edit'),get=x.nextGet();x.io.releaseDraft();const opening=x.io.openDesign('other');
 x.core.edit(d=>d.model.description='typed after navigation');if(undo)x.core.undo();
 const mark=x.core.historyMark(),draft=x.core.snapshot(),saved=x.core.file();
 fails?get.reject(Error('late network error')):get.resolve(file('other'));
 await opening;assert.equal(x.core.file(),saved);assert.equal(x.core.snapshot(),draft);assert.deepEqual(x.core.historyMark(),mark);
 assert.equal(x.core.dirty(),!undo);assert.equal(x.core.loading(),false);assert.equal(x.io.navigationTarget(),null);assert.equal(x.io.isReleased(),false);
 assert.equal(x.core.message().tone,'warn');assert.match(x.core.message().text,/cancelled.*edited/);
 assert.deepEqual(x.log.filter(e=>e[0]==='selection'),[['selection','same']]);
 assert.equal(x.log.some(e=>['clear','read','remember'].includes(e[0])),false,'cancelled open neither replaces nor clears the edited backup');
 assert.equal(x.log.filter(e=>e[0]==='preview').length,1,'only the retained current draft is previewed');
 x.io.dispose();x.core.dispose();
}
// A newer open, close, document replacement or disposal owns the state, including its message.
for(const change of ['new-open','cancel','replace','dispose'])for(const fails of [false,true]){
 const x=fixture('superseded'),get=x.nextGet(),opening=x.io.openDesign('old');
 x.core.edit(d=>d.model.description='new edit');
 if(change==='new-open'){const next=x.nextGet(),p=x.io.openDesign('new');next.resolve(file('new'));await p;}
 if(change==='cancel'){x.io.cancelNavigation();x.core.setLoading(false);}
 if(change==='replace')x.core.takeFile(file('replacement'));
 if(change==='dispose')x.io.dispose();
 x.core.setMessage({tone:'good',text:'new owner'});const mark=x.core.historyMark(),saved=x.core.file(),calls=x.log.length;
 fails?get.reject(Error('obsolete failure')):get.resolve(file('old'));await opening;
 assert.equal(x.core.file(),saved);assert.deepEqual(x.core.historyMark(),mark);assert.equal(x.core.message().text,'new owner');assert.equal(x.log.length,calls);
 x.io.dispose();x.core.dispose();
}
// Release tracks revision; disposal drops IO callbacks even if core survives.
a.io.releaseDraft();assert.equal(a.io.isReleased(),true);a.core.edit(d=>d.model.description='changed');assert.equal(a.io.isReleased(),false);
const c=fixture('C');ga=c.nextGet();oa=c.io.openDesign('pending');da=c.nextPut();pa=c.io.save();c.io.dispose();const logSize=c.log.length;ga.resolve(file('pending'));da.reject(fault(409));await oa;assert.equal(await pa,false);assert.equal(c.log.length,logSize);assert.equal(c.core.file().id,'same');assert.equal(c.core.saving(),false);assert.equal(c.core.loading(),false);assert.equal(c.core.message(),null);assert.equal(await c.io.save(),false);
const d=fixture('D');da=d.nextPut();pa=d.io.save();d.core.dispose();da.resolve(response('late'));assert.equal(await pa,false);assert.equal(d.core.file(),null);
// A conflict is information, not overwrite authorization for Run/Optimize/navigation.
const guarded=fixture('guarded');guarded.core.edit(d=>d.model.description='local');guarded.core.setConflict('disk');
for(const action of [()=>guarded.io.save(),()=>guarded.io.saveBeforeLeaving()]){
 const put=guarded.nextPut(),pending=action();assert.equal(guarded.log.findLast(x=>x[0]==='put')[3],'base');
 put.reject(fault(409));assert.equal(await pending,false);assert.equal(guarded.core.dirty(),true);assert.equal(guarded.core.file().hash,'base');
}
let put=guarded.nextPut(),pending=guarded.io.saveExplicit();assert.equal(guarded.log.findLast(x=>x[0]==='put')[3],'conflict');
// A further external edit still conflicts: explicit Save is conditional, never a force write.
put.reject(Object.assign(fault(409),{data:{current_hash:'newer-disk'}}));assert.equal(await pending,false);
assert.equal(guarded.core.conflict(),'newer-disk');assert.equal(guarded.core.dirty(),true);
put=guarded.nextPut();pending=guarded.io.saveExplicit();assert.equal(guarded.log.findLast(x=>x[0]==='put')[3],'newer-disk');
put.resolve(response('explicit-saved'));assert.equal(await pending,true);assert.equal(guarded.core.conflict(),null);assert.equal(guarded.core.dirty(),false);
// Explicit recovery is an ordinary undoable draft edit on the CURRENT disk hash.
const recovery=fixture('recovery'),recovered=design('same');recovered.model.description='older-base recovery';
const beforeRecovery=recovery.core.file(),requestCount=recovery.log.length;
assert.equal(recovery.io.restoreRecoveredDraft(recovered),true);
assert.equal(recovery.core.file(),beforeRecovery);assert.equal(recovery.core.file().hash,'base');
assert.equal(recovery.core.draft.model.description,'older-base recovery');assert.equal(recovery.core.dirty(),true);
assert.equal(recovery.log.length,requestCount,'recovery neither writes disk nor clears another owner');
recovery.core.undo();assert.equal(recovery.core.draft.model.description,undefined);
recovery.core.redo();assert.equal(recovery.core.draft.model.description,'older-base recovery');
put=recovery.nextPut();pending=recovery.io.save();assert.equal(recovery.log.findLast(x=>x[0]==='put')[3],'base');
put.reject(fault(409));assert.equal(await pending,false);assert.equal(recovery.core.dirty(),true);
recovery.io.dispose();recovery.core.dispose();
for(const x of [a,b,c,d,guarded]){x.io.dispose();x.core.dispose();}
console.log('Designer IO: independent deferred loads/saves, conflict/errors, stale responses, file-owned scope, rename guards, newer edits and disposal pass');
