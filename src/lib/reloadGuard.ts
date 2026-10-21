// A reload loses the open, unsaved design. In the desktop app there is no reason to reload at
// all, so F5 / Ctrl+R / Ctrl+Shift+R do nothing there. In a browser the user may mean it, so it
// only asks first while something is unsaved. (The designer also keeps a local backup of an
// unsaved draft: see designer/draftBackup.ts.)

import { isMacPlatform } from "./shortcut.ts";

type KeyEvent = Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey">;
/** Leave macOS Cmd+N to its real native menu accelerator. No JavaScript second dispatch. */
export function desktopBrowserKey(e: KeyEvent, mac = isMacPlatform()): boolean {
  const modifier = (e.ctrlKey || e.metaKey) && !e.altKey;
  const key = e.key.toLowerCase();
  const nativeNew = mac && e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey && key === "n";
  return e.key === "F5" || (modifier && ["r", "t", "n", "w"].includes(key) && !nativeNew);
}

export function installReloadGuard(isDesktop: () => boolean, hasUnsaved: () => boolean) {
  window.addEventListener("keydown", (e) => {
    const modifier = (e.ctrlKey || e.metaKey) && !e.altKey;
    const reload = e.key === "F5" || (modifier && e.key.toLowerCase() === "r");
    if (desktopBrowserKey(e) && isDesktop()) {
      e.preventDefault();
      if (reload) e.stopPropagation();
    }
  }, true);
  window.addEventListener("beforeunload", (e) => {
    if (isDesktop() || !hasUnsaved()) return;
    e.preventDefault();
    e.returnValue = "";
  });
}
