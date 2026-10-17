import { batch } from "solid-js";
import type { Design } from "./types";
import type { Check } from "./checks";
import type { api, ApiError, DesignFile, Validation } from "../runner/api";
import type { createDesignerSession } from "./sessionCore";
import { t } from "../i18n/index.ts";

type Core = ReturnType<typeof createDesignerSession>;
export interface DesignerIODependencies {
  transport: { design: typeof api.design; saveDesign: (file: DesignFile, design: Design, hash: string) => ReturnType<typeof api.saveDesign> };
  rememberLastDesign: (file: DesignFile) => void;
  readBackup: (file: DesignFile) => { design: Design; at: number } | null;
  clearBackup: (file: DesignFile) => void;
  stopPreview: () => unknown;
  forgetPreviewFailure: () => void;
  resetParamAsks: () => void;
  schedulePreview: () => void;
  applyModelEntry: (model: NonNullable<Validation["model"]>) => void;
  restoreModelSelection: (file: DesignFile | null) => void;
  /** a design was opened (and its backup, if any, restored): the 3D view takes the draft's opening view */
  openedDesign?: (file: DesignFile) => void;
  /** Persist the saved workspace revision in its selected native document. A failed/cancelled
   * external save keeps the new workspace hash but never marks the draft fully saved. */
  persistSavedDesign?: (file: DesignFile, hash: string, saveAs: boolean) => Promise<{ file?: DesignFile; warnings?: string[] } | void>;
}
/** IO owns no global registration or viewport: dependencies belong to its captured session. */
export function createDesignerIO(core: Core, deps: DesignerIODependencies) {
  let disposed = false;
  const alive = () => !disposed && !core.isDisposed();
  const { file, saving, asyncState, setLoading, setMessage, setSaving, snapshot, conflict,
    setFile, setConflict, showSaveNote, setServerChecks, setChecksTicket, setServerErrors, dirty } = core;
  let navigationTarget: string | null = null;
  /** The draft revision the user chose not to save before a replacement ("Don't save"); any later
   * edit takes the release back. */
  let released: { draft: ReturnType<typeof asyncState.ticket>; results: string } | null = null;
  const isReleased = () => !!released && asyncState.isDraftCurrent(released.draft) && released.results === core.pendingResultsToken();

  function savedNotice(note: NonNullable<ReturnType<typeof core.message>>, warnings: readonly string[]) {
    if (!warnings.length) { showSaveNote(note); return; }
    core.forgetSaveNote();
    setMessage({ tone: "warn", text: t("document.saveWarnings", { count: warnings.length }) +
      (note.text === t("store.saved") ? "" : ` ${note.text}`), sticky: true });
  }

  function take(f: DesignFile) {
    if (!alive()) return;
    deps.rememberLastDesign(f);
    // read the backup first: loading the saved design makes the draft clean, and a clean draft
    // removes its backup
    const b = f.readonly ? null : deps.readBackup(f);
    // the previous draft was given up with "Don't save": its backup must not bring it back later
    const previous = file();
    if (previous && (previous.id !== f.id || previous.backup_scope !== f.backup_scope) && isReleased()) deps.clearBackup(previous);
    released = null;
    core.forgetSaveNote();
    deps.stopPreview();
    deps.forgetPreviewFailure();   // another document: its own preview decides
    core.takeFile(f);
    if (!alive()) return;
    deps.resetParamAsks();
    // Only this page's own matching backup is automatic. Previous pages and other windows
    // remain explicit choices in BackupNotice, never replacements of this window's work.
    if (b && JSON.stringify(b.design) !== JSON.stringify(f.design)) {
      batch(() => {
        core.restoreBackup(b.design, new Date(b.at).getTime());
      });
      const at = new Date(b.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      setMessage({ tone: "warn", text: t("store.restored", { at }), sticky: true });
    }
    deps.openedDesign?.(f);
  }

  async function openDesign(id: string, expectedScope?: string) {
    if (!alive()) return;
    const mine = asyncState.navigationTicket();
    const draftAtOpen = asyncState.ticket();
    const resultsAtOpen = core.pendingResultsToken();
    navigationTarget = id;
    deps.stopPreview();
    setLoading(true);
    setMessage(null);
    const keepNewerEdits = () => {
      const newResults = core.pendingResultsToken() !== resultsAtOpen;
      if (asyncState.isDraftCurrent(draftAtOpen) && !newResults) return false;
      released = null;
      deps.restoreModelSelection(file());
      setMessage({ tone: "warn", text: t(newResults ? "document.openCancelledByResults" : "store.openCancelledByEdit"), sticky: true });
      if (alive() && asyncState.isOwnedNavigationCurrent(mine)) deps.schedulePreview();
      return true;
    };
    try {
      const f = await deps.transport.design(id);
      if (!(alive() && asyncState.isOwnedNavigationCurrent(mine) && asyncState.isDocumentCurrent(draftAtOpen))) return;
      if (keepNewerEdits()) return;
      if (expectedScope !== undefined && f.backup_scope !== expectedScope) throw new Error(t("store.workspaceChanged"));
      take(f);
      if (alive() && asyncState.isOwnedNavigationCurrent(mine)) deps.schedulePreview();
    } catch (e) {
      if (!(alive() && asyncState.isOwnedNavigationCurrent(mine) && asyncState.isDocumentCurrent(draftAtOpen))) return;
      if (keepNewerEdits()) return;
      released = null;   // the draft stays open: a later replacement asks again
      setMessage({ tone: "critical", text: (e as Error).message });
    } finally {
      if (alive() && asyncState.isOwnedNavigationCurrent(mine)) {
        navigationTarget = null;
        setLoading(false);
      }
    }
  }

  function applyValidation(v: Validation, ticket = asyncState.ticket()) {
    if (!alive() || !asyncState.isDocumentCurrent(ticket)) return;
    if (v.model) deps.applyModelEntry(v.model);
    if (v.checks) { setServerChecks(v.checks); setChecksTicket(ticket); }
  }

  // Only the user's Save command may accept the conflict hash advertised by the warning.
  // Run/Optimize and save-before-navigation must keep using the originally opened version.
  const save = () => saveWithIntent(false);
  const saveExplicit = () => saveWithIntent(true);
  const saveAs = () => saveWithIntent(false, true);

  async function saveWithIntent(overwriteConflict: boolean, copy = false): Promise<boolean> {
    const f = file();
    if (!alive() || !f || saving()) return false;
    const ticket = asyncState.ticket();
    // A saved display rename can supersede this request without replacing the open document.
    // Its newer file metadata/hash must survive both an old success and an old conflict reply.
    const currentSave = () => alive() && asyncState.isDocumentCurrent(ticket) && file()?.id === f.id && file()?.hash === f.hash && file()?.backup_scope === f.backup_scope;
    // an unfinished design saves too: errors only keep it from running
    setSaving(true);
    try {
      const d = JSON.parse(snapshot()) as Design;
      const res = await deps.transport.saveDesign(f, d, (overwriteConflict ? conflict() : null) ?? f.hash);
      if (!currentSave()) return false;
      let replacement: DesignFile | void;
      let warnings: string[] = [];
      try {
        const persisted = await deps.persistSavedDesign?.(f, res.hash, copy);
        replacement = persisted?.file;
        warnings = persisted?.warnings ?? [];
      } catch (error) {
        if (currentSave()) {
          batch(() => {
            // The workspace revision really changed. Retain the old saved baseline so retry
            // writes with the new hash, while the draft/backup and close warning stay intact.
            setFile({ ...f, hash: res.hash });
            core.setPersistencePending(true);
            setConflict(null);
            setMessage({ tone: "critical", text: (error as Error).message, sticky: true });
          });
        }
        return false;
      }
      if (!currentSave()) return false;
      if (replacement && replacement.id !== f.id) {
        if (!asyncState.isDraftCurrent(ticket) || core.resultsPending()) {
          setFile({ ...f, hash: res.hash });
          core.setPersistencePending(true);
          savedNotice({ tone: "warn", text: t("document.copySavedNewer") }, warnings);
          return false;
        }
        deps.clearBackup(f);
        take(replacement);
        deps.restoreModelSelection(replacement);
        deps.schedulePreview();
        savedNotice({ tone: "good", text: t("store.saved") }, warnings);
        return true;
      }
      deps.clearBackup(f);
      setFile({ ...f, design: d, hash: res.hash });
      core.setPersistencePending(false);
      setConflict(null);
      if (!asyncState.isDraftCurrent(ticket)) {
        if (res.validation.model) deps.applyModelEntry(res.validation.model);
        savedNotice({ tone: "good", text: t("store.savedNewer") }, warnings);
        return false;
      }
      applyValidation(res.validation, ticket);
      const list = res.validation.checks ?? [];
      const errors = list.filter((c) => c.severity === "error").length;
      const checkWarnings = list.filter((c) => c.severity === "warning").length;
      savedNotice(errors || !res.validation.valid
        ? { tone: "warn", text: errors ? t("store.savedErrors", { count: errors }) : t("store.savedNoBuild") }
        : { tone: checkWarnings ? "warn" : "good", text: checkWarnings ? t("store.savedWarnings", { count: checkWarnings }) : t("store.saved") }, warnings);
      return true;
    } catch (e) {
      if (!currentSave()) return false;
      const err = e as ApiError;
      if (err.status === 409 && err.data.workspace_error === "scope_mismatch") {
        setMessage({ tone: "critical", text: t("store.workspaceChanged") });
      } else if (err.status === 409 && typeof err.data.current_hash === "string") {
        setConflict(err.data.current_hash as string);
        setMessage({ tone: "warn", text: t("store.conflict"), sticky: true });
      } else if (err.status === 422) {
        if (asyncState.isDraftCurrent(ticket)) {
          if (Array.isArray(err.data.checks)) setServerChecks(err.data.checks as Check[]);
          else setServerErrors(err.fields ?? {});
          setMessage({ tone: "critical", text: err.message });
        }
      } else {
        setMessage({ tone: "critical", text: err.status === 0 ? t("common.serverUnreachable") : err.message });
      }
      return false;
    } finally {
      if (alive() && asyncState.isDocumentCurrent(ticket)) setSaving(false);
    }
  }


  async function saveBeforeLeaving(): Promise<boolean> {
    if (!alive()) return false;
    const ticket = asyncState.ticket();
    return (await save()) && alive() && asyncState.isDocumentCurrent(ticket) && !dirty() && !conflict();
  }
  /** Explicit recovery is an undoable edit, never a replacement of the saved file/hash. */
  function restoreRecoveredDraft(design: Design): boolean {
    const f = file();
    if (!alive() || !f || f.readonly || saving()) return false;
    const restored = structuredClone(design);
    core.edit(d => {
      for (const key of Object.keys(d)) delete (d as unknown as Record<string, unknown>)[key];
      Object.assign(d, restored);
    }, "", t("store.restoreBackup"));
    return true;
  }
  function cancelNavigation() { asyncState.cancelNavigation(); navigationTarget = null; }
  function dispose() {
    if (disposed) return;
    disposed = true; cancelNavigation(); released = null;
    asyncState.invalidatePreview();
    if (!core.isDisposed()) { setLoading(false); setSaving(false); }
  }
  return { take, openDesign, save, saveExplicit, saveAs, saveBeforeLeaving, restoreRecoveredDraft, applyValidation, isReleased, dispose,
    navigationTarget: () => navigationTarget,
    setNavigationTarget: (id: string | null) => { if (alive()) navigationTarget = id; },
    releaseDraft: () => { if (alive()) released = { draft: asyncState.ticket(), results: core.pendingResultsToken() }; },
    forgetRelease: () => { released = null; }, cancelNavigation,
  };
}
