import type { DesignFile } from "../runner/api";

/** Publish completed results from the saved workspace snapshot, never the live draft.
 * Failed attempts wait for an explicit Save or a new completion, rather than polling disk.
 */
export function createDocumentResultAutosave(deps: {
  persist: (file: DesignFile) => Promise<void>;
  busy: (value: boolean, file: DesignFile) => void;
  failed: (file: DesignFile, error: unknown) => void;
}) {
  const attempted = new Map<string, string>();
  let running = false;
  return async (file: DesignFile | null, token: string, blocked: boolean, bound: boolean) => {
    if (!file || file.readonly || blocked || !bound || running || token === "[]") return;
    const owner = JSON.stringify([file.backup_scope, file.id]);
    if (attempted.get(owner) === token) return;
    attempted.delete(owner); attempted.set(owner, token);
    while (attempted.size > 100) attempted.delete(attempted.keys().next().value!);
    running = true; deps.busy(true, file);
    try { await deps.persist(file); }
    catch (error) { deps.failed(file, error); }
    finally { running = false; deps.busy(false, file); }
  };
}
