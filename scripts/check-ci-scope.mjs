import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, writeFileSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {classify, verifiedMergeReuse} from './ci-scope.mjs';
for (const files of [['README.md'],['docs/BUNDLE.md','docs/tr/BUNDLE.md'],['landing/index.html'],['examples/drawings/dipole.svg']]) assert.deepEqual(classify(files),{full:false,native:false});
for (const file of ['src/designer/runResults.ts','api/ping.js','public/projects/index.json']) assert.deepEqual(classify([file]),{full:true,native:true});
for (const file of ['python/fairbeam/server.py','src-tauri/src/lib.rs','package-lock.json','scripts/check-ci-scope.mjs','.github/workflows/ci.yml','new-unknown-folder/input.dat']) assert.deepEqual(classify([file]),{full:true,native:true});
assert.deepEqual(classify([]),{full:true,native:true});
assert.deepEqual(classify(['docs/BUNDLE.md','python/fairbeam/server.py']),{full:true,native:true});
const git = ({parents='merge base reviewed', same=true}={}) => args => args[0]==='rev-list' ? parents : args[1]==='merge^{tree}' || same ? 'tree' : 'changed';
assert.equal(await verifiedMergeReuse('merge',git(),async sha=>sha==='reviewed'),true);
assert.equal(await verifiedMergeReuse('merge',git({same:false}),async()=>true),false);
assert.equal(await verifiedMergeReuse('merge',git(),async()=>false),false);
assert.equal(await verifiedMergeReuse('merge',git({parents:'direct parent'}),async()=>true),false);
assert.equal(await verifiedMergeReuse('merge',git({parents:'octopus a b c'}),async()=>true),false);
// Exercise actual Git ancestry and the CLI event/output contract without network or credentials.
const fixture = mkdtempSync(join(tmpdir(),'fairbeam-ci-scope-'));
const script = resolve('scripts/ci-scope.mjs');
try {
  const runGit = args => execFileSync('git',args,{cwd:fixture,encoding:'utf8',stdio:['ignore','pipe','pipe']}).trim();
  runGit(['init','-b','main']);
  runGit(['config','user.name','CI fixture']);
  runGit(['config','user.email','ci-fixture@example.invalid']);
  writeFileSync(join(fixture,'README.md'),'base');
  runGit(['add','.']);runGit(['commit','-m','base']);
  const base = runGit(['rev-parse','HEAD']);
  runGit(['checkout','-b','docs']);
  writeFileSync(join(fixture,'README.md'),'updated');
  runGit(['add','.']);runGit(['commit','-m','docs']);
  const docs = runGit(['rev-parse','HEAD']);
  const cli = (kind, event, sha) => {
    const eventPath=join(fixture,'event.json'), output=join(fixture,'output.txt');
    writeFileSync(eventPath,JSON.stringify(event));writeFileSync(output,'');
    execFileSync(process.execPath,[script],{cwd:fixture,env:{...process.env,
      GITHUB_EVENT_NAME:kind,GITHUB_EVENT_PATH:eventPath,GITHUB_OUTPUT:output,
      GITHUB_STEP_SUMMARY:'',GITHUB_TOKEN:'',GITHUB_SHA:sha},stdio:'pipe'});
    return readFileSync(output,'utf8');
  };
  assert.equal(cli('pull_request',{pull_request:{base:{sha:base},head:{sha:docs}}},docs),'full=false\nnative=false\nreuse=false\n');
  assert.equal(cli('workflow_dispatch',{},docs),'full=true\nnative=true\nreuse=false\n');
  writeFileSync(join(fixture,'unknown.code'),'code');
  runGit(['add','unknown.code']);runGit(['commit','-m','code']);
  const code=runGit(['rev-parse','HEAD']);
  assert.equal(cli('pull_request',{pull_request:{base:{sha:base},head:{sha:code}}},code),'full=true\nnative=true\nreuse=false\n');
  runGit(['checkout','main']);runGit(['merge','--no-ff','docs','-m','merge']);
  const merge=runGit(['rev-parse','HEAD']);
  assert.equal(await verifiedMergeReuse(merge,runGit,async sha=>sha===code),true);
  assert.equal(cli('push',{},merge),'full=true\nnative=true\nreuse=false\n');
  assert.equal(cli('pull_request',{pull_request:{base:{sha:'unavailable'},head:{sha:code}}},code),'full=true\nnative=true\nreuse=false\n');
} finally {
  // Only this freshly created fixture directory is removed.
  rmSync(fixture,{recursive:true,force:true});
}
console.log('CI planning: docs, application, native, mixed/unknown paths and exact successful merge-tree reuse passed');
