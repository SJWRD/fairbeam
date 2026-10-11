// Fast, dependency-free contract checks; these do not claim a native package or GUI pass.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const json = (path) => JSON.parse(read(path));
const base = json("src-tauri/tauri.conf.json");
const linux = json("src-tauri/tauri.linux.conf.json");
const pkg = json("package.json");
assert.deepEqual(linux.bundle.targets, ["deb"], "Linux overrides macOS package targets");
assert.equal(linux.bundle.createUpdaterArtifacts, false, "experimental packages need no signing key");
assert.equal(linux.bundle.category, "Education");
assert.deepEqual(linux.bundle.linux.deb.depends, ["libwebkit2gtk-4.1-0", "libgtk-3-0", "libxdo3", "xdg-utils"]);
for (const icon of linux.bundle.icon) {
  assert.ok(icon.endsWith(".png"));
  assert.ok(readFileSync(new URL(`../src-tauri/${icon}`, import.meta.url)).length > 0);
}
assert.equal(linux.identifier, undefined, "keep the existing workspace identity");
assert.equal(linux.version, undefined, "inherit the app version");
assert.equal(linux.bundle.resources, undefined, "inherit the complete app resource map");
assert.ok(base.bundle.resources["../runtime/pins.json"]);
assert.equal(pkg.scripts["desktop:build:linux"], "tauri build --config src-tauri/tauri.linux.conf.json");
const workflow = read(".github/workflows/linux-desktop.yml");
assert.match(workflow, /runs-on: ubuntu-24\.04/);
assert.match(workflow, /workflow_dispatch:/);
assert.doesNotMatch(workflow, /^\s+(push|pull_request|schedule):/m);
assert.match(workflow, /contents: read/);
assert.match(workflow, /npm run desktop:build:linux -- -- --locked/);
assert.doesNotMatch(workflow, /publish-release|TAURI_SIGNING|gh release|contents: write/);
assert.ok(workflow.indexOf("npm run check:licenses") > 0);
assert.ok(workflow.indexOf("npm run check:licenses") < workflow.indexOf("actions/upload-artifact"), "license gate precedes artifact publication");
assert.match(workflow, /rustc --edition 2021 --test src-tauri\/src\/linux_reveal.rs/);
assert.match(workflow, /python3 scripts\/check-linux-package-install\.py/);
assert.match(workflow, /Install and purge package[^\n]*\n\s+shell: bash\n\s+run: \|\n\s+set -euo pipefail/, "tee must not hide installation failures");
assert.ok(workflow.indexOf("python3 scripts/check-linux-package-install.py") < workflow.indexOf("Keep experimental package for manual desktop testing"), "minimal-container install gate precedes package retention");
const collector = read("scripts/third-party-licenses.py");
assert.match(collector, /for target in \([^\n]*'x86_64-unknown-linux-gnu'/, "license collector includes the Linux target");
const inventory = json("scripts/licenses/inventory.json");
const linuxCrates = inventory.sections.flatMap(s => s.packages).filter(p => p.ecosystem === "cargo" && p.evidence?.includes("x86_64-unknown-linux-gnu"));
for (const name of ["gtk", "webkit2gtk", "javascriptcore-rs", "soup3"]) {
  assert.ok(linuxCrates.some(p => p.name === name && p.texts.length > 0), `${name} must have Linux license evidence and original notices`);
}
const shell = read("src-tauri/src/main.rs");
assert.match(shell, /#\[cfg\(target_os = "linux"\)\]\s+let spawned = linux_reveal::command\(file\)\?\.spawn\(\)/);
console.log("check-linux-desktop: experimental Debian config and manual-only build contract pass (no native build)");
