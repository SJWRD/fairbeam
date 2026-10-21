// Deterministic checks for the viewport's queued work and preview glyph bounds.
// Run with: node --experimental-strip-types scripts/check-viewport-lifecycle.mjs
import assert from "node:assert/strict";
import { checkFrameDistance, clearOwnedDebugReference, frameTask, frameTransition, portMarkerRadius, watchPixelRatio } from "../src/scene/viewportLifecycle.ts";
import { poseToSave, validPose } from "../src/scene/cameraViews.ts";

// A design's opening view (Design.view): the poses the viewport takes, and the pose it saves.
assert.deepEqual(validPose({ camera: [1, 2, 3], target: [0, 0, 0] }), { camera: [1, 2, 3], target: [0, 0, 0] });
for (const bad of [null, {}, { camera: [1, 2], target: [0, 0, 0] }, { camera: [1, 2, "x"], target: [0, 0, 0] },
  { camera: [1, 2, Infinity], target: [0, 0, 0] }, { camera: [1, 1, 1], target: [1, 1, 1] }]) assert.equal(validPose(bad), null, `refused: ${JSON.stringify(bad)}`);
assert.deepEqual(poseToSave({ x: 1.234567, y: -0.00001, z: 2 }, { x: 0, y: 3.33333, z: -1e-9 }), { camera: [1.2346, 0, 2], target: [0, 3.3333, 0] }, "saved on a 1e-4 mm grid, no -0");

let nextHandle = 1;
const frames = new Map();
const schedule = (callback) => {
  const handle = nextHandle++;
  frames.set(handle, callback);
  return handle;
};
const cancel = (handle) => frames.delete(handle);

let animationTime = 0;
const transition = frameTransition(schedule, cancel, () => animationTime);
const advance = (time) => {
  animationTime = time;
  const pending = [...frames.values()];
  frames.clear();
  pending.forEach(callback => callback(time));
};
const progress = [];
transition.start(value => progress.push(value), 400);
assert.deepEqual(progress, [], "animated focus does not snap to its destination");
advance(100);
assert.ok(progress.at(-1) > 0 && progress.at(-1) < 0.25, "movement eases in");
advance(200);
assert.equal(progress.at(-1), 0.5, "passes through an intermediate pose");
advance(400);
assert.equal(progress.at(-1), 1, "finishes exactly at the requested pose");
assert.equal(frames.size, 0, "completed transitions leave no animation loop");
transition.start(value => progress.push(value), 400);
advance(500);
transition.cancel();
const stopped = progress.length;
advance(1000);
assert.equal(progress.length, stopped, "user input cancels pending movement");
transition.start(() => assert.fail("superseded transition ran"), 400);
transition.start(value => progress.push(value), 0);
assert.equal(progress.at(-1), 1, "reduced motion and additions apply immediately");
assert.equal(frames.size, 0, "replacing a transition cancels its old frame");
transition.start(() => assert.fail("disposed transition ran"), 400);
transition.dispose();
advance(2000);
transition.start(() => assert.fail("unmounted viewport scheduled movement"), 400);
assert.equal(frames.size, 0, "unmount cancels and prevents further animation");

// Small targets retain more context, distances scale with model size, and both viewport
// dimensions contain the target even on a narrow display.
for (const aspect of [0.5, 1, 2]) {
  const small = checkFrameDistance(1, 100, 45, aspect);
  const large = checkFrameDistance(100, 100, 45, aspect);
  assert.ok(large > small, "larger objects require more camera distance");
  assert.ok(small > large / 100, "small elements get more surrounding context");
  assert.ok(Math.abs(checkFrameDistance(10, 1000, 45, aspect) - small * 10) < 1e-10,
    "scaling the model preserves its composition");
  const angularRadius = Math.asin(100 / large);
  assert.ok(angularRadius < Math.PI / 8, "target fits vertically");
  assert.ok(angularRadius < Math.atan(Math.tan(Math.PI / 8) * aspect), "target fits horizontally");
}

// A wide, flat object should fill a wide viewport instead of fitting an oversized sphere.
const corners = [-10, 10].flatMap(x => [-1, 1].flatMap(y => [-0.1, 0.1].map(z => [x, y, z])));
const radius = Math.hypot(10, 1, 0.1);
const fitted = checkFrameDistance(radius, radius, 45, 2, corners);
assert.ok(fitted < checkFrameDistance(radius, radius, 45, 2) * 0.6, 'large flat object gets a substantially tighter fit');
for (const [x, y, z] of corners) {
  assert.ok(Math.abs(x) / (fitted - z) < Math.tan(Math.PI / 8) * 2, 'corner fits horizontally');
  assert.ok(Math.abs(y) / (fitted - z) < Math.tan(Math.PI / 8), 'corner fits vertically');
}
assert.equal(checkFrameDistance(radius, radius * 100, 45, 2, corners),
  checkFrameDistance(radius, radius * 100, 45, 2), 'small-object framing is unchanged');

const flush = () => {
  const queued = [...frames.values()];
  frames.clear();
  queued.forEach((callback) => callback(0));
};

let renders = 0;
const render = frameTask(() => renders++, schedule, cancel);
render.request();
render.request();
assert.equal(frames.size, 1, "repeated render requests share one frame");
flush();
assert.equal(renders, 1);
render.request();
render.runNow();
assert.equal(renders, 2, "capture renders immediately");
assert.equal(frames.size, 0, "capture cancels the queued render");
flush();
assert.equal(renders, 2, "cancelled work does not render twice");
render.dispose();
render.request();
assert.equal(frames.size, 0, "unmounted viewport cannot schedule a render");

let picked = null;
let pointer = "first";
const pick = frameTask(() => { picked = pointer; }, schedule, cancel);
pick.request();
pointer = "latest";
pick.request();
assert.equal(frames.size, 1, "pointer moves share one pick frame");
flush();
assert.equal(picked, "latest", "pick uses the latest pointer position");
pick.request();
pick.dispose();
flush();
assert.equal(picked, "latest", "a queued pick cannot update state after unmount");

assert.equal(portMarkerRadius(10, [0, 100]), 0.2, "coarse preview mesh is capped at 2% of scene radius");
assert.equal(portMarkerRadius(10, [0, 0.1, 1]), 0.06, "fine mesh retains a visible minimum");
assert.equal(portMarkerRadius(10, [0, 0.5, 1]), 0.15, "ordinary mesh spacing still controls glyph size");
assert.equal(portMarkerRadius(10, [0]), 0.2, "missing spacing stays bounded");

const debugWindow = {};
const firstViewport = { scene: {} };
const remountedViewport = { scene: {} };
debugWindow.__fairbeam = firstViewport;
clearOwnedDebugReference(debugWindow, firstViewport);
assert.equal("__fairbeam" in debugWindow, false, "teardown releases its development debug reference");
debugWindow.__fairbeam = remountedViewport;
clearOwnedDebugReference(debugWindow, firstViewport);
assert.equal(debugWindow.__fairbeam, remountedViewport, "old teardown preserves a remounted viewport reference");

const queries = [];
const display = {
  devicePixelRatio: 1.25,
  matchMedia(media) {
    const listeners = new Set();
    const query = {
      media, listeners,
      addEventListener: (_event, callback) => listeners.add(callback),
      removeEventListener: (_event, callback) => listeners.delete(callback),
    };
    queries.push(query);
    return query;
  },
};
const ratios = [];
const stopPixelRatio = watchPixelRatio(() => ratios.push(display.devicePixelRatio), display);
for (const ratio of [1.5, 2, 1.25]) {
  const previous = queries.at(-1);
  display.devicePixelRatio = ratio;
  [...previous.listeners].forEach((callback) => callback());
  assert.equal(previous.listeners.size, 0, "old monitor query is detached");
  assert.equal(queries.at(-1).media, `(resolution: ${ratio}dppx)`);
}
assert.deepEqual(ratios, [1.25, 1.5, 2, 1.25], "fractional monitor changes update without a resize");
stopPixelRatio();
assert.ok(queries.every((query) => query.listeners.size === 0), "unmount removes all resolution listeners");

console.log("viewport lifecycle: frame cleanup, marker bounds, fractional DPR transitions and opening-view poses pass");
