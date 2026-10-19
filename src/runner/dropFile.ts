import { importReferenceFile } from "../compare/store";
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
  const hint = /\.(bas|mcs|txt)$/i.test(file.name) ? "app.dropCst"
    : /\.(dxf|gbr|gerber|drl)$/i.test(file.name) ? "app.dropPcb" : null;
  if (hint || !/\.(s\d+p|ts|csv)$/i.test(file.name)) {
    showToast(t(hint ?? "app.dropUnsupported", { name: file.name }), { tone: "error", key: "file-drop" });
    return;
  }
  if (appMode() !== "results" || !bundle()?.results || bundle()?.preview) {
    showToast(t("app.dropReferenceContext", { name: file.name }), { key: "file-drop" });
    return;
  }
  const result = await importReferenceFile(file);
  if (result.status === "failed") showToast(result.error, { tone: "error", key: "file-drop" });
  else if (result.status === "imported") showToast(t("app.dropReferenceImported", { name: file.name }), { key: "file-drop" });
}
