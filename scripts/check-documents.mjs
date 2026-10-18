// Shared envelope cases; full embedded design validation remains the server's responsibility.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {documentEnvelope} from "../src/lib/fairbeamDocument.ts";

const fixture = JSON.parse(readFileSync(new URL("../python/tests/fixtures/document_envelopes.json", import.meta.url), "utf8"));
const base = {schema: "fairbeam.document/1", id: "abcdef01-1234-5678-9012-abcdef012345", design: {schema: "fairbeam.design/1"}, results_directory: "Patch.results", results: []};
let checks = 0;
for (const [group, cases] of Object.entries(fixture)) {
  for (const test of cases) {
    const value = {...base, ...test.set};
    if (group === "valid") {
      const original = JSON.stringify(value);
      assert.equal(documentEnvelope(value), value, test.name);
      assert.equal(JSON.stringify(value), original, `${test.name}: preserves stored spelling`);
    }
    else assert.throws(() => documentEnvelope(value), undefined, test.name);
    checks++;
  }
}
for (const value of [null, [], 1, "document", {...base, results: Array(10001).fill(null)}]) {
  assert.throws(() => documentEnvelope(value));
  checks++;
}
documentEnvelope({...base, results_directory: "😀".repeat(45)});
for (const name of ["😀".repeat(46), "a".repeat(181), "unpaired\ud800"]) {
  assert.throws(() => documentEnvelope({...base, results_directory: name}));
  checks++;
}
const {visibleProjects} = await import('../src/home/projectList.ts');
const entries = [
  {key:'fb_user_design',file:'fb_user_design.design.json',model:{name:'Patch'}},
  {key:'copy',file:'copy.design.json',model:{name:'Patch'},document:{id:'example',path:'/İzmir/文档.fb'}},
];
assert.equal(visibleProjects(entries,'','name',false,new Set()).length,2,'all ordinary and portable designs remain visible');
assert.deepEqual(visibleProjects(entries,'文档.fb','name',false,new Set()).map(x=>x.key),['copy']);
assert.deepEqual(visibleProjects(entries,'fb_user_design','name',false,new Set()).map(x=>x.key),['fb_user_design']);
assert.equal(visibleProjects(entries,'','name',true,new Set(['copy']))[0].key,'copy');
checks += 4;
console.log(`Document envelope and project labels: ${checks} checks passed`);
