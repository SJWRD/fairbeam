// Display colors of the 3D view: a part's own color wins over its material's, which wins over the
// theme's. A color is "#rrggbb"; anything else is ignored (the solver never sees colors). The same
// holds for the display opacity, a see-through look for a part or a whole material.

const HEX = /^#[0-9a-fA-F]{6}$/;

/** The color as lowercase "#rrggbb", or undefined when it is not one. */
export function validColor(c: unknown): string | undefined {
  return typeof c === "string" && HEX.test(c) ? c.toLowerCase() : undefined;
}

/** Part color over material color over the theme default. */
export function resolveColor(part: { color?: string } | undefined, material: { color?: string } | undefined, theme: string): string {
  return validColor(part?.color) ?? validColor(material?.color) ?? theme;
}

/** The most see-through a part can be drawn (python/fairbeam/design.py OPACITY_MIN): a fainter one is a hidden part. */
export const OPACITY_MIN = 0.05;

/** A display opacity (OPACITY_MIN .. 1), or undefined when it is not one. */
export function validOpacity(o: unknown): number | undefined {
  return typeof o === "number" && Number.isFinite(o) && o >= OPACITY_MIN && o <= 1 ? o : undefined;
}

/** Part opacity over material opacity; undefined: the viewer's default look (metal opaque, dielectric see-through). */
export function resolveOpacity(part: { opacity?: number } | undefined, material: { opacity?: number } | undefined): number | undefined {
  return validOpacity(part?.opacity) ?? validOpacity(material?.opacity);
}

/** The theme's solid color for a metal or dielectric (read from the CSS tokens of the 3D view). */
export function themeColor(metal: boolean): string {
  const v = typeof getComputedStyle === "undefined" ? "" : getComputedStyle(document.documentElement).getPropertyValue(metal ? "--al-3d-metal" : "--al-3d-dielectric").trim();
  return HEX.test(v) ? v : metal ? "#c8a040" : "#6f9a7e";
}
