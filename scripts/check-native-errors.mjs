import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {nativeErrorMessage} from '../src/lib/nativeErrors.ts';
import {setLanguage} from '../src/i18n/index.ts';
const read=path=>readFileSync(new URL('../'+path,import.meta.url),'utf8');
const codes=new Set(['document_invalid_response']);
for(const path of ['src-tauri/src/native_document.rs','src-tauri/src/server.rs','src-tauri/src/export_payload.rs','src-tauri/src/main.rs']) {
 for(const match of read(path).matchAll(/(?:error|export_error)\(\s*"((?:document|export)_[a-z_]+)"/g))codes.add(match[1]);
}
for(const locale of ['en','tr']) {
 setLanguage(locale);
 const texts=JSON.parse(read(`src/i18n/${locale}.json`));
 for(const code of codes) {
  const expected=texts[`native.error.${code}`];assert.equal(typeof expected,'string',`${locale}: ${code}`);
  assert.equal(nativeErrorMessage({code,error:'English diagnostic',status:409},true),expected,`${locale}: ${code} not generic conflict`);
 }
 assert.doesNotMatch(nativeErrorMessage({code:'document_target_exists',status:409,error:'English diagnostic'},true),/English diagnostic/);
}
setLanguage('en');
assert.equal(nativeErrorMessage({error:'New diagnostic',code:'future_code'}),'New diagnostic','unknown failures retain diagnostics');
assert.match(nativeErrorMessage({error:'File changed on disk',status:409},true),/File changed on disk/,'older server conflicts remain understandable');
assert.equal(nativeErrorMessage(new Error('disk unavailable')),'disk unavailable');
console.log(`Native errors: ${codes.size} Rust command codes map to complete EN/TR messages; busy and existing-target errors do not use the conflict/reopen prefix`);
