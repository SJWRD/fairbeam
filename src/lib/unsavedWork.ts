/** Unsaved work outside the designer draft. Approval never mutates the source text. */
export interface UnsavedWork {
  snapshot: () => string | null;
  busy: () => boolean;
  confirm: () => Promise<boolean>;
}
const providers = new Map<string, UnsavedWork>();
export function registerUnsavedWork(name: string, provider: UnsavedWork) {
  providers.set(name, provider);
  return () => { if (providers.get(name) === provider) providers.delete(name); };
}
let request: { id: number; approved: Map<string, string>; pending: boolean; canceled: boolean } | null = null;
export function checkUnsavedWork(id: number): 'clear' | 'pending' | 'cancel' {
  if (!request || request.id !== id) request = { id, approved: new Map(), pending: false, canceled: false };
  const current = request;
  if (current.canceled) return 'cancel';
  if (current.pending || [...providers.values()].some(provider => provider.busy())) return 'pending';
  for (const [name, provider] of providers) {
    const snapshot = provider.snapshot();
    if (snapshot === null || current.approved.get(name) === snapshot) continue;
    current.pending = true;
    void Promise.resolve().then(() => provider.confirm()).then(approved => {
      if (request !== current) return;
      if (approved) current.approved.set(name, snapshot);
      else current.canceled = true;
    }, () => { if (request === current) current.canceled = true; }).finally(() => { current.pending = false; });
    return 'pending';
  }
  return 'clear';
}
export function resetUnsavedWorkApproval() { request = null; }
