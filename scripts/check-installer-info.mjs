// The Windows installer's version info: Tauri's NSIS template writes no CompanyName, so
// src-tauri/windows/installer-hooks.nsh adds it as plain text (the hooks are included before the
// template defines ${MANUFACTURER}). It must stay equal to bundle.publisher, and bundle.copyright
// must be set for LegalCopyright in the installer and in fairbeam.exe.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const read = (path) => readFileSync(fileURLToPath(new URL(`../src-tauri/${path}`, import.meta.url)), "utf8");
const config = JSON.parse(read("tauri.conf.json"));
const bundle = config.bundle;
const hooks = read("windows/installer-hooks.nsh");

assert.ok(bundle.publisher, "bundle.publisher is set");
assert.match(bundle.copyright ?? "", /^Copyright © \d{4} \S/, "bundle.copyright is set");
const company = [...hooks.matchAll(/^\s*VIAddVersionKey\s+"CompanyName"\s+"([^"]*)"/gm)].map((match) => match[1]);
assert.deepEqual(company, [bundle.publisher], "installer-hooks.nsh writes CompanyName once, equal to bundle.publisher");
assert.doesNotMatch(hooks, /^\s*VIAddVersionKey\s+"(ProductName|FileDescription|LegalCopyright|FileVersion|ProductVersion)"/m,
  "the template already writes these keys; a second VIAddVersionKey would fail the build");

// The production-only post-install correction must match the configured association and quote
// both paths. A renamed association or missing guard must fail before an installer is packaged.
assert.equal(config.identifier, "org.fairbeam.desktop", "the correction is scoped to the production identity");
const associations = bundle.fileAssociations.filter((association) => association.ext.includes("fb"));
assert.equal(associations.length, 1, "exactly one .fb association is configured");
const commandKey = `Software\\Classes\\${associations[0].name}\\shell\\open\\command`;
const command = `WriteRegStr SHCTX "${commandKey}" "" '"$INSTDIR\\\${MAINBINARYNAME}.exe" "%1"'`;
const postInstall = hooks.match(/^!macro NSIS_HOOK_POSTINSTALL\r?\n([\s\S]*?)^!macroend/m)?.[1];
assert.ok(postInstall, "the correction runs after Tauri creates its associations");
const normalized = postInstall.split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !line.startsWith(";")).join("\n");
assert.ok(normalized.includes(`!if "\${BUNDLEID}" == "${config.identifier}"\n${command}\n!endif`),
  "the quoted .fb command is guarded by the production bundle identity");
const commandWrites = hooks.split(/\r?\n/).map((line) => line.trim())
  .filter((line) => line.startsWith("WriteRegStr ") && line.includes(`"${commandKey}"`));
assert.deepEqual(commandWrites, [command], "the .fb command has one quoted correction");

console.log(`check-installer-info: CompanyName "${bundle.publisher}", ${bundle.copyright}, quoted .fb command`);
