import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {gpuInstallError,gpuProgress} from '../src/lib/gpuRuntimeFeedback.ts';
const read = path => readFileSync(new URL('../'+path,import.meta.url),'utf8');
assert.deepEqual(gpuInstallError({code:'gpu_runtime_install_failed',detail:'SHA-256 mismatch',log:'C:\\Logs\\GPU install.log'}),
 {code:'gpu_runtime_install_failed',detail:'SHA-256 mismatch',log:'C:\\Logs\\GPU install.log'});
assert.equal(gpuInstallError('nvidia_driver_too_old').code,'nvidia_driver_too_old');
assert.equal(gpuInstallError(new Error('IPC unavailable')).detail,'IPC unavailable');
assert.equal(gpuInstallError({code:3,detail:null,log:[]}).code,'');
assert.equal(gpuProgress(null),null);
assert.equal(gpuProgress({fraction:NaN}).fraction,undefined);
assert.equal(gpuProgress({fraction:Infinity}).fraction,undefined);
assert.equal(gpuProgress({fraction:4}).fraction,1);
assert.equal(gpuProgress({fraction:-1}).fraction,0);
assert.equal(gpuProgress({fraction:.25}).fraction,.25);
assert.equal(gpuProgress({finished:true}).finished,true);
const main=read('src-tauri/src/main.rs');
const toggle=main.slice(main.indexOf('async fn set_gpu_runtime_enabled('),main.indexOf('fn set_gpu_runtime_enabled_worker('));
assert.match(toggle,/spawn_blocking/,'GPU hardware/import probes stay off the UI thread');
const splash=read('src-tauri/splash/index.html');
const permissions=JSON.parse(read('src-tauri/capabilities/default.json')).permissions;
const commands=[...splash.matchAll(/(?:invoke|bind)\(\s*(?:"[^"]+"\s*,\s*)?"([a-z]+(?:_[a-z]+)+)"/g)].map(m=>m[1]);
function checkAcl(granted) {for(const command of commands) assert.ok(granted.includes('allow-'+command.replaceAll('_','-')),`Splash command ${command} is not granted`);}
checkAcl(permissions);
assert.throws(()=>checkAcl(permissions.filter(p=>p!=='allow-use-default-workspace')),'missing recovery capability must fail');
assert.ok(!JSON.parse(read('src-tauri/capabilities/viewer.json')).permissions.includes('allow-use-default-workspace'),'recovery is splash-only');
assert.doesNotMatch(splash,/invoke\("install_gpu_runtime"/,'GPU installation belongs in Settings');
const start=main.slice(main.indexOf('fn start(app:'),main.indexOf('fn splash_state('));
assert.ok(start.indexOf('workspace_settings::check_saved')<start.indexOf('seed::seed'),'saved-folder admission precedes every seed');
assert.match(main,/gpu_runtime_progress\(&worker_app, json!/,'installer progress reaches the viewer');
assert.match(main,/"code":"gpu_runtime_install_failed", "detail":detail, "log":log/,'failure retains reason and log location');
for(const locale of ['en','tr']) {
 const text=JSON.parse(read(`src/i18n/${locale}.json`));
 assert.notEqual(text['settings.gpuManaged.installed'],text['settings.gpuManaged.ready']);
 assert.doesNotMatch(text['home.designs.reveal'],/Explorer|Gezgini/,'reveal labels are platform neutral');
}
console.log('Runtime feedback: errors/logs/progress boundaries, async probe, splash ACL denial regression, recovery ordering and EN/TR wording passed');
