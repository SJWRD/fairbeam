import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { renderNotices } from './licenses/render.mjs';
import { licenseInputHash } from './licenses/fingerprint.mjs';
const fingerprintInputs = {
  'package.json': '{\n  "version": "0.7.2",\n  "dependencies": {"example": "1.2.3"}\n}\n',
  'package-lock.json': '{\n  "version": "0.7.2",\n    "": {\n      "name": "fairbeam",\n      "version": "0.7.2"\n    },\n    "node_modules/example": {"version": "1.2.3"}\n}\n',
  'src-tauri/Cargo.toml': '[package]\nname = "fairbeam"\nversion = "0.7.2"\n[dependencies]\nexample = "1.2.3"\n',
  'src-tauri/Cargo.lock': '[[package]]\nname = "fairbeam"\nversion = "0.7.2"\n[[package]]\nname = "example"\nversion = "1.2.3"\n',
  'runtime/setup-runtime.ps1': '# Installer\n$version = "1.2.3"\n',
  'scripts/licenses/render.mjs': 'export const notice = "Copyright Example";\n'
};
for (const [path, lf] of Object.entries(fingerprintInputs)) {
  const crlf = lf.replaceAll('\n', '\r\n');
  assert.equal(licenseInputHash(path, lf), licenseInputHash(path, crlf), `${path}: Git LF and Windows CRLF must agree`);
  assert.notEqual(licenseInputHash(path, lf), licenseInputHash(path, lf + '# changed content\n'), `${path}: content must still invalidate`);
  if (lf.includes('1.2.3')) assert.notEqual(licenseInputHash(path, lf), licenseInputHash(path, lf.replaceAll('1.2.3', '1.2.4')), `${path}: dependency version changes must invalidate`);
  if (lf.includes('0.7.2')) assert.equal(licenseInputHash(path, lf), licenseInputHash(path, crlf.replaceAll('0.7.2', '0.7.3')), `${path}: own app version exclusion must work with either line ending`);
}
assert.notEqual(licenseInputHash('notice.txt', 'A\rB'), licenseInputHash('notice.txt', 'A\nB'), 'only CRLF pairs are normalized');
const fixture = {sections:[{title:'Viewer (npm)',packages:[{ecosystem:'npm',name:'example',version:'1.0.0',license:'MIT',source:'https://example.org',texts:[{name:'LICENSE',text:'Copyright Example\nPermission text'}]}]}]};
assert.match(renderNotices(fixture, {}, '1.0.0'), /Copyright Example\nPermission text/);
for (const license of [null, '', 'UNKNOWN', 'NOASSERTION']) {
 const data = structuredClone(fixture); data.sections[0].packages[0].license = license;
 assert.throws(() => renderNotices(data, {}, '1'), /Missing or unknown license/);
 assert.match(renderNotices(data, {'npm:example@1.0.0':'Reviewed upstream terms.'}, '1'), /Reviewed upstream terms/);
}
const missing = structuredClone(fixture); missing.sections[0].packages[0].texts = [];
assert.throws(() => renderNotices(missing, {}, '1'), /No license text/);
assert.throws(() => renderNotices(missing, {'npm:example@2.0.0':'Different release'}, '1'), /No license text/);
const config = JSON.parse(readFileSync('src-tauri/tauri.conf.json'));
assert.equal(config.bundle.resources['../THIRD-PARTY-NOTICES.md'], 'THIRD-PARTY-NOTICES.md');
assert.match(readFileSync('scripts/publish-release.mjs','utf8'), /checksums.set\(notices, sha256\(notices\)\)/);
for (const lang of ['en','tr']) assert.ok(JSON.parse(readFileSync(`src/i18n/${lang}.json`))['about.thirdPartyLicenses']);
const version = JSON.parse(readFileSync('package.json')).version;
assert.equal(config.version, version);
assert.equal(readFileSync("python/pyproject.toml", "utf8").match(/^version\s*=\s*"([^"]+)"/m)?.[1], version, "Python package version must match the app");
assert.match(readFileSync('landing/index.html','utf8'), /blob\/main\/THIRD-PARTY-NOTICES\.md/);
assert.match(readFileSync('src-tauri/src/main.rs','utf8'), /"licenses" => concat!\("https:\/\/github.com\/ismailakdag\/fairbeam\/blob\/v", env!\("CARGO_PKG_VERSION"\)/);
console.log('License checks: missing licenses/texts, exact-version exceptions, preserved notices and distribution wiring pass.');
