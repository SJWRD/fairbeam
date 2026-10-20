import { importReferenceFile } from "../compare/store";
import { acceptsReferenceFile } from "../import/accept";
import { t } from "../i18n";
import { showToast } from "../lib/toast";
import { bundle } from "../state";
import { appMode } from "../workspace";
import { openUserProject } from "./openProject";

/** App-wide drops only accept the formats promised by the overlay. Reference data belongs
 * to the visible Results/Examples comparison; importing it into a hidden design preview
 * would discard it on the next project change. */
export async function openDroppedFile(file: File): Promise<void> {
  if (/\.(json|fb)$/i.test(file.name)) { await openUserProject(file); return; }
  const hint = /\.(bas|mcs)$/i.test(file.name) ? "app.dropCst"
    : /\.(dxf|gbr|gerber|drl)$/i.test(file.name) ? "app.dropPcb" : null;
  if (hint || !acceptsReferenceFile(file.name, file.type)) {
    showToast(t(hint ?? "app.dropUnsupported", { name: file.name }), { tone: "error", key: "file-drop" });
    return;
  }
  if (appMode() !== "results" || !bundle()?.results || bundle()?.preview) {
    showToast(t("app.dropReferenceContext", { name: file.name }), { key: "file-drop" });
    return;
  }
  // Text can be reference data or a macro/history export. Parse as data first so a
  // comment mentioning VBA does not reject a valid measurement. Capture the one read
  // inside importReferenceFile so its ownership ticket is established before any await.
  let text: string | undefined;
  const result = await importReferenceFile(/\.txt$/i.test(file.name)
    ? { name: file.name, text: async () => (text = await file.text()) }
    : file);
  if (result.status === "failed") {
    const macro = text !== undefined && /^\s*(?:(?:public\s+|private\s+)?sub\s+main\b|addtohistory\b|with\s+[a-z_]\w*\s*$|end\s+with\b)/im.test(text);
    showToast(macro ? t("app.dropCst", { name: file.name }) : result.error, { tone: "error", key: "file-drop" });
  }
  else if (result.status === "imported") showToast(t("app.dropReferenceImported", { name: file.name }), { key: "file-drop" });
}
