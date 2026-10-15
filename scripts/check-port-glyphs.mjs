// Port / lumped-element glyphs: pixel-sized, x-ray, rescaled without rebuilding geometry.
// Run: node --experimental-strip-types scripts/check-port-glyphs.mjs
import assert from "node:assert/strict";
import * as THREE from "three";
import { lumpedObject, portObject, updatePortGlyphs, waveguidePortObject } from "../src/scene/portGlyphs.ts";
import { stackLabels } from "../src/scene/labelStack.ts";

const meshes = (g) => { const out = []; g.traverse((o) => o.isMesh && out.push(o)); return out; };
const size = (o) => new THREE.Box3().setFromObject(o).getSize(new THREE.Vector3());

// half-wave dipole feed: a 1 mm gap and a 2 mm wide strip in a ~300 mm scene
const port = portObject([-1, -1, -0.5], [1, 1, 0.5], "#d4462f", 0.3, "z");
assert.equal(port.userData.kind, "lumped");
assert.ok(port.children.length >= 8, "ball, shaft, head (solid + x-ray) and the area block");
const xray = meshes(port).filter((m) => m.material.depthTest === false && m.material.transparent);
assert.ok(xray.length >= 3, "x-ray pass ignores the depth buffer so a port inside metal stays visible");
assert.ok(meshes(port).some((m) => m.material.depthTest === true && !m.material.transparent), "solid pass present");

const camera = new THREE.PerspectiveCamera(32, 1, 0.05, 1e6);
camera.position.set(300, -300, 200);
const root = new THREE.Group();
root.add(port);
const geoms = new Set(meshes(port).map((m) => m.geometry));
const wpp = (d) => 2 * d * Math.tan(THREE.MathUtils.degToRad(16)) / 600;

// zoomed far out: still at least a few pixels long/wide
updatePortGlyphs(root, camera, 600);
const far = size(port);
const farPx = wpp(camera.position.distanceTo(port.userData.labelAnchor));
assert.ok(Math.max(far.x, far.y, far.z) >= 12 * farPx * 0.8, `arrow spans >= ~12 px at distance (got ${Math.max(far.x, far.y, far.z) / farPx} px)`);
// zoomed in: the true 1 mm gap length wins
camera.position.set(3, -3, 2);
updatePortGlyphs(root, camera, 600);
const near = size(port);
assert.ok(near.z >= 1 - 1e-6, "never smaller than the real gap");
// no geometry rebuilds: the same shared geometry objects before and after
assert.deepEqual(new Set(meshes(port).map((m) => m.geometry)), geoms, "geometries reused, only scales change");

// a zero-width wire feed has no area block; a strip feed does (when wider than ~2 px)
const wire = portObject([0, 0, -0.5], [0, 0, 0.5], "#d4462f", 0.05, "z");
updatePortGlyphs(Object.assign(new THREE.Group(), { children: [wire] }), camera, 600);
const fills = (g) => g.children.filter((c) => c.isMesh && c.renderOrder === 58 || c.isLineSegments && c.renderOrder === 61);
assert.ok(fills(wire).every((c) => !c.visible), "line port: no area block");
assert.ok(fills(port).every((c) => c.visible), "strip port: translucent block + outline");

// negative direction flips the arrowhead (stop below start)
const down = portObject([0, 0, 0.5], [0, 0, -0.5], "#d4462f", 0.05, "z");
const head = meshes(down).find((m) => m.geometry.type === "ConeGeometry");
assert.ok(new THREE.Vector3(0, 0, 1).applyQuaternion(head.quaternion).z < 0, "cone points along -z");

// highlight changes colour and strengthens the x-ray pass
const before = port.userData.labelAnchor.clone();
port.userData.setHighlight(true, "#2467b7");
const x = meshes(port).find((m) => m.material.transparent && m.material.depthTest === false);
assert.equal(x.material.color.getHexString(), "2467b7");
assert.ok(x.material.opacity > 0.8);
port.userData.setHighlight(false);
assert.equal(x.material.color.getHexString(), "d4462f");
assert.equal(before.x, port.userData.labelAnchor.x);

// waveguide port: translucent sheet, outline, arrow; direction x here
const wg = waveguidePortObject([0, -11, -5], [4, 11, 5], "x", "#d4462f", 0.2);
assert.equal(wg.userData.kind, "waveguide");
assert.ok(wg.children.some((c) => c.isMesh && c.material.transparent && c.material.opacity < 0.5), "translucent sheet");
assert.ok(wg.children.filter((c) => c.isLineSegments).length >= 2, "excitation outline + probe outline");
assert.equal(wg.userData.labelAnchor.x, 4, "label sits at the probe plane");

// lumped element: not the port colour, amber accent drawn through metal
const r = lumpedObject([0, 0, 0], [2, 0.5, 0], "#2f2d2a", 0.3, "#e0a526", "x");
assert.equal(r.userData.kind, "element");
const accent = r.children.find((c) => c.isLine && !c.isLineSegments);
assert.equal(accent.material.color.getHexString(), "e0a526");
assert.equal(accent.material.depthTest, false);
assert.ok(size(r).z >= 0.3 - 1e-9, "flat resistor thickened");

// labels that would overprint stack upward; the others stay where they are
{
  const P = (x, y, w = 80, h = 18) => ({ x, y, w, h });
  assert.deepEqual(stackLabels([P(100, 100), P(300, 100)]), [0, 0], "side by side: nothing moves");
  assert.deepEqual(stackLabels([P(100, 100)]), [0], "one label");
  // two ports 0.3 mm apart on a 20 mm tooth: the same pixel, give or take one
  const two = stackLabels([P(200, 300), P(201, 301)]);
  assert.equal(two[1], 0, "the lower label keeps its place");
  assert.ok(Math.abs((300 - two[0]) - (301 - 18 - 2)) < 1e-9, `the upper one sits a label height and the gap above the lower (${two[0]})`);
  // a port, its series capacitor and a second port in a column: a stack of three, no overlaps left
  const boxes = [P(200, 300), P(205, 298, 120), P(195, 302)];
  const lift = stackLabels(boxes);
  ok3: for (let i = 0; i < 3; i++) for (let j = i + 1; j < 3; j++) {
    const a = boxes[i], b = boxes[j], ya = a.y - lift[i], yb = b.y - lift[j];
    const overlap = Math.abs(a.x - b.x) < (a.w + b.w) / 2 && Math.abs(ya - yb) < (a.h + b.h) / 2;
    assert.ok(!overlap, `labels ${i} and ${j} no longer overlap`);
    if (overlap) break ok3;
  }
  assert.ok(lift.every((d) => d >= 0) && lift.filter((d) => d === 0).length === 1, "only upward, and the lowest stays");
  assert.deepEqual(stackLabels([P(100, 100), P(100, 140)]), [0, 0], "labels a row apart do not move");
  const dense = stackLabels(Array.from({ length: 100 }, () => P(100, 100)));
  assert.equal(new Set(dense).size, dense.length, "dense port arrays keep all labels separate beyond 64 rows");
  assert.ok(dense.every((d, i) => i === 0 || d - dense[i - 1] >= 20), "dense labels retain the full height and gap");
}
console.log("port glyph checks passed");
