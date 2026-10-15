// Port and lumped-element labels of the 3D view (CSS2D, drawn in screen pixels) must not overprint: two
// ports a fraction of a millimetre apart (a probe's centre pin and ring, a feed and its series capacitor)
// would put their labels on top of each other in any full-model view. Like the chart markers
// (src/charts/labels.ts), labels that would overlap are stacked upward, away from their glyphs.
// Plain numbers: scripts/check-port-glyphs.mjs runs it in Node.

/** A label on screen: its anchor (the centre of the box, px from the top left) and its size (px). */
export interface ScreenLabel { x: number; y: number; w: number; h: number }

/**
 * Upward offsets (px, never negative) that keep the labels from overlapping, in input order. Labels are
 * taken bottom to top; one that overlaps a label already placed moves up to sit `gap` px above it, keeping
 * its x. A label that overlaps nothing stays where it is.
 */
export function stackLabels(labels: readonly ScreenLabel[], gap = 2): number[] {
  const order = labels.map((l, i) => ({ ...l, i, dy: 0 })).sort((a, b) => b.y - a.y || a.i - b.i);
  const placed: typeof order = [];
  for (const item of order) {
    for (let guard = 0; guard <= placed.length; guard++) {
      const y = item.y - item.dy;
      const hit = placed.find((p) => {
        const py = p.y - p.dy;
        return Math.abs(item.x - p.x) < (item.w + p.w) / 2 - 0.01 && Math.abs(y - py) < (item.h + p.h) / 2 + gap - 0.01;
      });
      if (!hit) break;
      // just above the label it hits (at least half a pixel of progress, so rounding cannot stall it)
      item.dy = Math.max(item.dy + 0.5, item.y - (hit.y - hit.dy - (item.h + hit.h) / 2 - gap));
    }
    placed.push(item);
  }
  const out = new Array<number>(labels.length).fill(0);
  for (const p of placed) out[p.i] = p.dy;
  return out;
}
