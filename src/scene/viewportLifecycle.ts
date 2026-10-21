/** Coalesces work onto one animation frame and cancels it when its owner is removed. */
export function frameTask(
  run: () => void,
  schedule: (callback: FrameRequestCallback) => number = requestAnimationFrame,
  cancel: (handle: number) => void = cancelAnimationFrame,
) {
  let handle: number | null = null;
  let disposed = false;
  const request = () => {
    if (disposed || handle !== null) return;
    handle = schedule(() => {
      handle = null;
      if (!disposed) run();
    });
  };
  const runNow = () => {
    if (disposed) return;
    if (handle !== null) cancel(handle);
    handle = null;
    run();
  };
  const dispose = () => {
    disposed = true;
    if (handle !== null) cancel(handle);
    handle = null;
  };
  return { request, runNow, dispose };
}

/** Re-arm the resolution query after each monitor/zoom change, even without a CSS resize. */
export function watchPixelRatio(
  update: () => void,
  target: Pick<Window, "devicePixelRatio" | "matchMedia"> = window,
): () => void {
  let query: MediaQueryList;
  const changed = () => {
    query?.removeEventListener("change", changed);
    query = target.matchMedia(`(resolution: ${target.devicePixelRatio}dppx)`);
    query.addEventListener("change", changed);
    update();
  };
  changed();
  return () => query.removeEventListener("change", changed);
}

/** Fit a checked target with spacing relative to its size within the model. */
export function checkFrameDistance(
  radius: number, sceneRadius: number, fovDegrees: number, aspect: number,
  cameraCorners?: readonly (readonly [number, number, number])[],
): number {
  // Small targets need context; large targets should fill more of the viewport. Comparing sizes
  // rather than model units keeps the same framing when a design is scaled or changes units.
  const relativeSize = Math.min(1, radius / Math.max(sceneRadius, radius));
  const padding = 1.1 + 1.45 * (1 - Math.sqrt(relativeSize));
  const verticalHalfFov = fovDegrees * Math.PI / 360;
  const horizontalHalfFov = Math.atan(Math.tan(verticalHalfFov) * aspect);
  const sphereDistance = radius / Math.sin(Math.min(verticalHalfFov, horizontalHalfFov));
  let fittedDistance = sphereDistance;
  if (cameraCorners?.length) {
    // Coordinates relative to the target center: right, up, and toward the camera. Account for
    // depth as well as width/height so the nearest corners also stay inside the perspective view.
    fittedDistance = Math.max(radius * 0.01, ...cameraCorners.map(([x, y, z]) =>
      z + Math.max(Math.abs(x) / Math.tan(horizontalHalfFov), Math.abs(y) / Math.tan(verticalHalfFov), radius * 0.01)));
  }
  // Preserve small-element framing; progressively use the tighter box fit for larger targets.
  const blend = Math.max(0, (relativeSize - 0.05) / 0.95);
  return (sphereDistance + (fittedDistance - sphereDistance) * blend) * padding;
}

/** Keep feed glyphs visible without letting a two-line preview mesh dominate the model. */
export function portMarkerRadius(radius: number, xLines: readonly number[]): number {
  let spacing = Infinity;
  for (let i = 1; i < xLines.length; i++) {
    const delta = xLines[i] - xLines[i - 1];
    if (delta > 0 && delta < spacing) spacing = delta;
  }
  return Math.min(radius * 0.02, Math.max(radius * 0.006, spacing * 0.3));
}

/** Clear a development debug reference only while it still belongs to this viewport. */
export function clearOwnedDebugReference(
  target: { __fairbeam?: unknown },
  viewport: unknown,
): void {
  if (target.__fairbeam === viewport) delete target.__fairbeam;
}
