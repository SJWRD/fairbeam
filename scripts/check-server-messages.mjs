import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {build} from 'vite';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url)).replaceAll('\\','/');
const built=await build({root,configFile:false,logLevel:'silent',resolve:{conditions:['browser']},plugins:[{
 name:'server-messages-entry',resolveId(id){if(id.endsWith('server-messages-entry'))return '\0server-messages-entry';},
 load(id){if(id==='\0server-messages-entry')return ['runner/api','i18n/index','lib/serverMessages','lib/runSlug','lib/nativeDocuments'].map((p,i)=>`export * as m${i} from ${JSON.stringify(root+'src/'+p+'.ts')};`).join('\n');},
}],build:{write:false,minify:false,lib:{entry:'server-messages-entry',formats:['es']}}});
const code=(Array.isArray(built)?built[0]:built).output[0].code;
const {m0:{api,ApiError},m1:{setLanguage,t},m2:{serverMessage,serverWarnings},m3:{runSlug},m4:native}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
for(const [label,expected] of JSON.parse(readFileSync(root+'python/tests/fixtures/run_slugs.json','utf8'))) assert.equal(runSlug(label),expected,label);
assert.match(readFileSync(root+'src/components/RunPanel.tsx','utf8'),/runSlug\(runName\(\)\)/,'RunPanel filename preview must use shared transliteration');
const originalFetch=globalThis.fetch,originalWindow=globalThis.window;
try {
 for(const language of ['en','tr']) {
  setLanguage(language);
  const busy={error:'antenna_key has a run in progress',code:'run_in_progress',args:{name:'Çığ anteni'}};
  globalThis.fetch=async()=>new Response(JSON.stringify(busy),{status:409});
  await assert.rejects(api.deleteDesign('antenna_key'),error=>{
   assert.ok(error instanceof ApiError);assert.equal(error.status,409);assert.equal(error.data.code,'run_in_progress');
   assert.equal(error.message,t('server.run_in_progress',{name:'Çığ anteni'}));assert.ok(!error.message.includes('antenna_key'));return true;
  });
  globalThis.fetch=async()=>new Response(JSON.stringify({error:'invalid sweep',code:'invalid_sweep',args:{},fields:{n:'1.5: must be a whole number',extra:'unknown legacy field'},field_details:{n:{code:'field.value',args:{value:'1.5',reason:{code:'field.integer',args:{}}}}}}),{status:422});
  await assert.rejects(api.submitSweep({model:'x',params:{},threads:1,sweep:[]}),error=>{
   assert.equal(error.message,t('server.invalid_sweep'));assert.equal(error.fields.n,'1.5: '+t('server.field.integer'));assert.equal(error.fields.extra,'unknown legacy field');return true;
  });
  assert.equal(serverMessage({code:'preview_timeout',args:{seconds:'30'}},'fallback'),t('server.preview_timeout',{seconds:'30'}));
  assert.deepEqual(serverWarnings(['legacy','old English'],[null,{code:'warning.untracked_results',args:{count:2}}]),['legacy',t('server.warning.untracked_results',{count:2})]);
  const file={id:'local',hash:'base',backup_scope:'scope',design:{model:{name:'Çığ'}}};
  globalThis.window={__TAURI_INTERNALS__:{invoke:async(command)=>{
   if(command==='native_document_archive') throw {status:409,error:'This project has a run in progress; wait for it to finish first',code:'project_running',args:{}};
   return {status:'saved',handle:'handle',path:'/Çığ.fb',file,document:{id:'id',revision:'revision',results_directory:'Çığ.results'},results:[],warnings:['The document was saved; workspace metadata needs recovery: PermissionError','Future server warning'],warning_details:[{code:'warning.workspace_recovery',args:{detail:'PermissionError'}},{code:'future_warning',args:{}}]};
  }}};
  await assert.rejects(native.archiveNativeProject('local',{path:'/Çığ.fb',results_directory:'Çığ.results'},'scope'),error=>error.message===t('server.project_running'));
  const saved=await native.persistNativeDocument(file,'base',true);
  assert.deepEqual(saved.warnings,[t('server.warning.workspace_recovery',{detail:'PermissionError'}),'Future server warning']);
 }
 setLanguage('tr');
 assert.equal(serverMessage({code:'unknown.future',args:{}},'Original English'),'Original English');
 assert.equal(serverMessage({code:'field.value',args:{reason:{code:'unknown'}}},'Full fallback'),'Full fallback');
 assert.equal(serverMessage(null,'legacy'),'legacy');
 assert.equal(serverMessage({code:'__proto__'},'legacy'),'legacy');
 assert.equal(serverMessage({code:'common.save'},'legacy'),'legacy','server cannot select unrelated UI keys');
 assert.equal(new ApiError('unchanged',400).message,'unchanged');
 globalThis.fetch=async()=>new Response(JSON.stringify({error:'older server',fields:{x:'older field'}}),{status:400});
 await assert.rejects(api.deleteDesign('x'),error=>error.message==='older server' && error.fields.x==='older field');
 console.log('check-server-messages: Turkish NFC/NFD slug parity; real API and native adapters localize EN/TR errors, fields and save warnings; unknown/legacy fallbacks pass.');
} finally {globalThis.fetch=originalFetch;globalThis.window=originalWindow;setLanguage('en');}
