// Browser-only fault fixture: exercise the real loading/error boundary without corrupting a project.
import { lazy } from "solid-js";
import { render } from "solid-js/web";
import PanelBoundary from "../../src/components/PanelBoundary";
import { t } from "../../src/i18n";

export function mountBoundary(host: HTMLElement, fail: boolean) {
  const Child = fail ? () => { throw new Error("Controlled boundary fixture"); }
    : lazy(() => new Promise<never>(() => {}));
  return render(() => <PanelBoundary name={t("panel.name.view3d")}><Child /></PanelBoundary>, host);
}
