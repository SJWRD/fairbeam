// Tauri's NSIS template omits CompanyName; hooks add it before template definitions.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { checkInstallerAssociations } from "./check-installer-associations.mjs";

const read = (path) => readFileSync(fileURLToPath(new URL(`../src-tauri/${path}`, import.meta.url)), "utf8");
const config = JSON.parse(read("tauri.conf.json"));
const bundle = config.bundle;
const hooks = read("windows/installer-hooks.nsh");
assert.ok(bundle.publisher, "bundle.publisher is set");
assert.match(bundle.copyright ?? "", /^Copyright © \d{4} \S/, "bundle.copyright is set");
const company = [...hooks.matchAll(/^\s*VIAddVersionKey\s+"CompanyName"\s+"([^"]*)"/gm)].map((match) => match[1]);
assert.deepEqual(company, [bundle.publisher], "CompanyName is written once and equals bundle.publisher");
assert.doesNotMatch(hooks, /^\s*VIAddVersionKey\s+"(ProductName|FileDescription|LegalCopyright|FileVersion|ProductVersion)"/m,
  "the template already writes these keys; duplicates would fail the build");

assert.equal(config.identifier, "org.fairbeam.desktop");
assert.deepEqual(bundle.fileAssociations.map(({ ext, name }) => ({ ext, name })),
  [{ ext: ["fb"], name: "Fairbeam document" }], "review macro dispatch before adding associations");
assert.match(hooks, /^!include "\$\{__FILEDIR__\}\\association-lifecycle\.nsh"$/m);
assert.doesNotMatch(hooks, /^\s*(?:WriteReg|DeleteReg)/m, "association writes belong only to the dispatch macros");
const windows = JSON.parse(read("tauri.windows.conf.json"));
assert.equal(windows.bundle.windows.nsis.installMode, "currentUser");
checkInstallerAssociations(read("windows/association-lifecycle.nsh"), config.identifier,
  windows.bundle.windows.nsis.installMode);
console.log(`check-installer-info: CompanyName "${bundle.publisher}", ${bundle.copyright}, .fb lifecycle policy`);
