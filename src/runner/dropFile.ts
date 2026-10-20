import { importReferenceFile } from "../compare/store";
import { acceptsReferenceFile } from "../import/accept";
import { t } from "../i18n";
import { showToast } from "../lib/toast";
import { bundle } from "../state";
import { appMode, appModeRevision } from "../workspace";
import { openUserProject } from "./openProject";

const MACRO_MARKERS = /^\s*(?:(?:public\s+|private\s+)?sub\s+main\b|addtohistory\b|with\s+[a-z_]\w*\s*$|end\s+with\b)/im;
let dropGeneration = 0;

/** App-wide drops only accept the formats promised by the overlay. Reference data belongs
 * to the visible Results/Examples comparison; importing it into a hidden design preview
 * would discard it on the next project change. */
export async function openDroppedFile(file: File): Promise<void> {
  const generation = ++dropGeneration;
  if (/\.(json|fb)$/i.test(file.name)) { await openUserProject(file); return; }
  const hint = /\.(bas|mcs)$/i.test(file.name) ? "app.dropCst"
    : /\.(dxf|gbr|gerber|drl)$/i.test(file.name) ? "app.dropPcb" : null;
  if (hint || !acceptsReferenceFile(file.name, file.type)) {
    showToast(t(hint ?? "app.dropUnsupported", { name: file.name }), { tone: "error", key: "file-drop" });
    return;
  }
  if (appMode() !== "results" || !bundle()?.results || bundle()?.preview) {
    // A .txt file may be a macro, which belongs in Start even when no result is open.
    // Classify without importing or changing hidden comparison state. Navigation or any
    // newer drop owns subsequent feedback, including leaving and returning to this screen.
    if (/\.txt$/i.test(file.name)) {
      const revision = appModeRevision(), owner = bundle();
      const current = () => generation === dropGeneration && appModeRevision() === revision && bundle() === owner;
      try {
        const text = await file.text();
        if (!current()) return;
        if (MACRO_MARKERS.test(text)) {
          showToast(t("app.dropCst", { name: file.name }), { tone: "error", key: "file-drop" });
          return;
        }
      } catch (error) {
        if (current()) showToast(`${file.name}: ${error instanceof Error ? error.message : String(error)}`, { tone: "error", key: "file-drop" });
        return;
      }
    }
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
    const macro = text !== undefined && MACRO_MARKERS.test(text);
    showToast(macro ? t("app.dropCst", { name: file.name }) : result.error, { tone: "error", key: "file-drop" });
  }
  else if (result.status === "imported") showToast(t("app.dropReferenceImported", { name: file.name }), { key: "file-drop" });
}
