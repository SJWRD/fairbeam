/** Geometry and field-plane coordinates are in model units, including legacy position_mm. */
export function modelToMm(bundle: { units?: { length_m?: number } } | null | undefined): number {
  return (bundle?.units?.length_m ?? 1e-3) * 1000;
}

/** Locale-independent report text, not geometry. Significant digits remove scaling residue
 * without rounding very small port positions to zero as fixed decimal places would. */
export function modelMmText(value: number, toMm: number): string {
  return String(Number((value * toMm).toPrecision(12)));
}
