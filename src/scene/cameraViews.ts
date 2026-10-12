export type ViewName = "iso" | "top" | "front" | "right" | "bottom" | "back" | "left";
/** A camera pose in design coordinates (mm): the camera position and the orbit target (a design's opening view). */
export interface CameraPose { camera: [number, number, number]; target: [number, number, number] }

const point = (p: unknown): p is [number, number, number] => Array.isArray(p) && p.length === 3 && p.every((x) => typeof x === "number" && Number.isFinite(x));
/** A saved pose that the 3D view can take (python/fairbeam/design.py _check_view), else null. */
export function validPose(v: unknown): CameraPose | null {
  const o = v as Partial<CameraPose> | null | undefined;
  if (!o || !point(o.camera) || !point(o.target)) return null;
  return Math.hypot(...o.camera.map((x, i) => x - o.target![i])) < 1e-9 ? null : { camera: [...o.camera], target: [...o.target] };
}
/** The pose to save, on a 1e-4 mm grid (a design file holds short numbers; far below any visible change). */
export function poseToSave(camera: { x: number; y: number; z: number }, target: { x: number; y: number; z: number }): CameraPose {
  const r = (x: number) => Math.round(x * 1e4) / 1e4 || 0;
  return { camera: [r(camera.x), r(camera.y), r(camera.z)], target: [r(target.x), r(target.y), r(target.z)] };
}
export type ViewCommand = ViewName | "nearest" | "fit";

export const CAMERA_VIEWS = [
  { id: "iso", label: "viewport.btn.iso", title: "viewport.view.isometric", shortcut: "0" },
  { id: "top", label: "viewport.view.top", title: "viewport.view.top", shortcut: "8" },
  { id: "front", label: "viewport.view.front", title: "viewport.view.front", shortcut: "5" },
  { id: "right", label: "viewport.view.right", title: "viewport.view.right", shortcut: "6" },
  { id: "bottom", label: "viewport.view.bottom", title: "viewport.view.bottom", shortcut: "2" },
  { id: "back", label: "viewport.view.back", title: "viewport.view.back", shortcut: "3" },
  { id: "left", label: "viewport.view.left", title: "viewport.view.left", shortcut: "4" },
] as const satisfies readonly { id: ViewName; label: string; title: string; shortcut: string }[];

/** Camera-to-target offsets, with Z up. Tiny pole offsets avoid OrbitControls' singularity. */
export const VIEW_DIRECTIONS: Record<ViewName, readonly [number, number, number]> = {
  iso: [1, -1.25, 0.85], top: [0, -0.0001, 1], bottom: [0, 0.0001, -1],
  front: [0, -1, 0.0001], back: [0, 1, 0.0001], right: [1, 0, 0.0001], left: [-1, 0, 0.0001],
};

export function nearestAxis({ x, y, z }: { x: number; y: number; z: number }): ViewName {
  if (Math.abs(z) >= Math.max(Math.abs(x), Math.abs(y))) return z < 0 ? "bottom" : "top";
  if (Math.abs(x) >= Math.abs(y)) return x < 0 ? "left" : "right";
  return y < 0 ? "front" : "back";
}

const NUMBER_VIEWS: Record<string, ViewCommand> = { "0": "iso", "1": "nearest", "2": "bottom", "3": "back", "4": "left", "5": "front", "6": "right", "8": "top" };
/** Physical numpad codes still work with Num Lock off; top-row aliases support laptops. */
export function cameraShortcut(e: Pick<KeyboardEvent, "key" | "code" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey">): ViewCommand | undefined {
  if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
  const physical = /^(?:Numpad|Digit)([0-9])$/.exec(e.code)?.[1];
  const digit = physical ?? (/^[0-9]$/.test(e.key) ? e.key : undefined);
  return digit === undefined ? undefined : NUMBER_VIEWS[digit];
}

export function isViewCommand(value: unknown): value is ViewCommand {
  return value === "nearest" || value === "fit" || CAMERA_VIEWS.some((v) => v.id === value);
}
