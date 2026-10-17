/** Geometry and field-plane coordinates are in model units, including legacy position_mm. */
export function modelToMm(bundle: { units?: { length_m?: number } } | null | undefined): number {
  return (bundle?.units?.length_m ?? 1e-3) * 1000;
}
