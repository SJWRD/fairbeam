export type GpuRuntimeProgress = { step?: string; fraction?: number; message?: string; log?: string; finished?: boolean };
export function gpuProgress(value: unknown): GpuRuntimeProgress | null {
  if (!value || typeof value !== "object") return null;
  const p = value as Record<string, unknown>;
  return {
    step: typeof p.step === "string" ? p.step : undefined,
    fraction: typeof p.fraction === "number" && Number.isFinite(p.fraction) ? Math.max(0, Math.min(1, p.fraction)) : undefined,
    message: typeof p.message === "string" ? p.message : undefined,
    log: typeof p.log === "string" ? p.log : undefined,
    finished: p.finished === true,
  };
}
export function gpuInstallError(value: unknown): { code: string; detail: string; log: string } {
  if (value && typeof value === "object" && "code" in value) {
    const error = value as Record<string, unknown>;
    return { code: typeof error.code === "string" ? error.code : "",
      detail: typeof error.detail === "string" ? error.detail : "",
      log: typeof error.log === "string" ? error.log : "" };
  }
  const text = value instanceof Error ? value.message : String(value);
  return { code: text, detail: text, log: "" };
}
