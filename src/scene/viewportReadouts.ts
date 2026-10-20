import { fmt } from "../i18n/index.ts";
import { num } from "../lib/format.ts";

/** Round down to a readable 1/2/5 step in the caller's units. */
export function niceLength(x: number): number {
  const p = Math.pow(10, Math.floor(Math.log10(x)));
  const m = x / p;
  return (m >= 5 ? 5 : m >= 2 ? 2 : 1) * p;
}

/** Display conversion only: the camera, picked points and WCS stay in model coordinates. */
export function viewportScaleBar(worldPerPx: number, toMm: number): { px: number; label: string } {
  const mmPerPx = worldPerPx * toMm;
  const len = niceLength(mmPerPx * 110);
  const px = Math.round((len / mmPerPx) * 2) / 2;
  return { px, label: `${fmt.num(len, 6)} mm` };
}

export function viewportCoordinates(point: readonly number[], toMm: number, local = false): string {
  const axes = local ? ["u", "v", "w"] : ["x", "y", "z"];
  return axes.map((axis, i) => `${axis} ${num(point[i] * toMm, 2)}`).join(" · ") + " mm";
}

export function viewportMeasurement(distance: number, toMm: number): string {
  return `${fmt.fixed(distance * toMm, 3)} mm`;
}
