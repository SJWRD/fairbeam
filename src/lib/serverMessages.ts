import { hasKey, t } from "../i18n/index.ts";

/** Optional, additive server metadata. Unrecognized codes retain their English diagnostic. */
export interface ServerMessage { code: string; args?: Record<string, unknown> }
export function serverMessage(value: unknown, fallback: string, depth = 0): string {
  if (!value || typeof value !== "object" || depth > 3) return fallback;
  const { code, args } = value as ServerMessage;
  if (typeof code !== "string" || !/^[a-z][a-z0-9_.]*$/.test(code) || !hasKey(`server.${code}`)) return fallback;
  const params: Record<string, string | number> = {};
  if (args && typeof args === "object" && !Array.isArray(args)) for (const [name, arg] of Object.entries(args)) {
    if (typeof arg === "string" || (typeof arg === "number" && Number.isFinite(arg))) params[name] = arg;
    else if (arg && typeof arg === "object") {
      const translated = serverMessage(arg, "", depth + 1);
      if (!translated) return fallback;
      params[name] = translated;
    }
  }
  return t(`server.${code}`, params);
}

export function serverWarnings(warnings: readonly string[], details?: readonly unknown[]): string[] {
  return warnings.map((text, index) => serverMessage(details?.[index], text));
}
