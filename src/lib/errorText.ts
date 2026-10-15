import { t } from "../i18n/index.ts";

/** Tauri commands may reject with a plain Rust error string instead of a JavaScript Error. */
export function errorText(error: unknown): string {
  if (error instanceof Error && error.name === "IdealWireMeshError") return t("export.mesh.idealWire");
  if (typeof error === "string") return error;
  if (error instanceof Error) return error.message;
  if (error && typeof error === "object" && "message" in error && typeof error.message === "string") {
    return error.message;
  }
  return String(error);
}
