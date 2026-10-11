import type { Design } from "./types";
import { worldBoxes } from "./geometry";

/** Exact box envelope, not an absorbing boundary or an inferred antenna environment. */
export function elmerEnclosure(d: Design, names: Record<string, number>): number[] | null {
  try {
    const boxes = (d.parts ?? []).flatMap(p => worldBoxes(p, names));
    if (!boxes.length) return null;
    const lo = [0, 1, 2].map(k => Math.min(...boxes.flatMap(b => [b[0][k], b[1][k]])));
    const hi = [0, 1, 2].map(k => Math.max(...boxes.flatMap(b => [b[0][k], b[1][k]])));
    return [...lo, ...hi].every(Number.isFinite) && lo.every((v, k) => hi[k] > v) ? [...lo, ...hi] : null;
  } catch { return null; }
}
