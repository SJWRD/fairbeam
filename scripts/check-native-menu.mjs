// Native accelerator declarations plus the real page capture guard. Actual AppKit key equivalents
// and system panel localization are verified on a built Mac app, not by this Node check.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installReloadGuard } from "../src/lib/reloadGuard.ts";
const listeners = new Map();
globalThis.window = { addEventListener: (name, handler) => listeners.set(name, handler) };
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { platform: "MacIntel" } });
installReloadGuard(() => true, () => true);
function key(value, mods = {}) {
  let prevented = false;
  listeners.get("keydown")({ key: value, metaKey: true, ctrlKey: false, altKey: false, shiftKey: false,
    ...mods, preventDefault() { prevented = true; }, stopPropagation() {} });
  return prevented;
}
assert.equal(key("n"), false, "macOS Cmd+N must reach the native New menu accelerator");
assert.equal(key("o"), false, "macOS Cmd+O must reach native Open");
for (const value of ["r", "t", "w", "F5"]) assert.equal(key(value), true, `${value}: browser/reload guard stays in place`);
assert.equal(key("n", { shiftKey: true }), true, "unbound Cmd+Shift+N cannot create a browser window");
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { platform: "Win32" } });
assert.equal(key("n", { metaKey: false, ctrlKey: true }), true, "Windows shortcut ownership stays unchanged");
const read = name => readFileSync(new URL(`../${name}`, import.meta.url), "utf8");
const main = read("src-tauri/src/main.rs");
for (const [id, accelerator] of Object.entries({
  "file-new": "CmdOrCtrl+N", "file-open": "CmdOrCtrl+O", "file-save": "CmdOrCtrl+S", "file-save-as": "CmdOrCtrl+Shift+S",
  "view-tree": "Ctrl+Shift+1", "view-dock": "Ctrl+Shift+2", "view-properties": "Ctrl+Shift+3", "view-ribbon": "Ctrl+F1",
  "view-zoom-in": "CmdOrCtrl+=", "view-zoom-out": "CmdOrCtrl+-", "view-zoom-reset": "CmdOrCtrl+0",
})) assert.ok(main.includes(`"${id}" => Some("${accelerator}")`), `${id}: actual accelerator declaration`);
assert.ok(main.includes('native_action_accelerator(id, cfg!(target_os = "macos"))'), "menu constructor uses the platform-gated accelerator");
assert.doesNotMatch(main, /format!\("\{\}    \{(?:command|control)\}/, "no fake shortcut text in native labels");
const workspace = read("src/designer/DesignWorkspace.tsx");
assert.ok(workspace.includes("desktopBrowserKey(e)"), "designer uses the same native passthrough policy");
assert.doesNotMatch(workspace, /\["t", "n", "w", "r"\]/, "designer no longer independently swallows native New");
const plist = read("src-tauri/Info.plist");
assert.match(plist, /<key>CFBundleDevelopmentRegion<\/key>\s*<string>en<\/string>/);
assert.match(plist, /<key>CFBundleLocalizations<\/key>\s*<array>\s*<string>en<\/string>\s*<string>tr<\/string>\s*<\/array>/);
assert.match(plist, /<key>CFBundleAllowMixedLocalizations<\/key>\s*<true\s*\/>/);
assert.equal(JSON.parse(read("src-tauri/tauri.conf.json")).bundle.category, "DeveloperTool");
console.log("check-native-menu: native accelerator declarations, page passthrough and Mac bundle locale/category contract pass");
