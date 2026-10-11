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
  let released: ReturnType<typeof asyncState.ticket> | null = null;
  const isReleased = () => !!released && asyncState.isDraftCurrent(released);

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
  }

  async function openDesign(id: string, expectedScope?: string) {
    if (!alive()) return;
    const mine = asyncState.navigationTicket();
    const draftAtOpen = asyncState.ticket();
    navigationTarget = id;
    deps.stopPreview();
    setLoading(true);
    setMessage(null);
    const keepNewerEdits = () => {
      if (asyncState.isDraftCurrent(draftAtOpen)) return false;
      released = null;
      deps.restoreModelSelection(file());
      setMessage({ tone: "warn", text: t("store.openCancelledByEdit"), sticky: true });
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

  async function saveWithIntent(overwriteConflict: boolean): Promise<boolean> {
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
      deps.clearBackup(f);
      setFile({ ...f, design: d, hash: res.hash });
      setConflict(null);
      if (!asyncState.isDraftCurrent(ticket)) {
        if (res.validation.model) deps.applyModelEntry(res.validation.model);
        showSaveNote({ tone: "good", text: t("store.savedNewer") });
        return false;
      }
      applyValidation(res.validation, ticket);
      const list = res.validation.checks ?? [];
      const errors = list.filter((c) => c.severity === "error").length;
      const warnings = list.filter((c) => c.severity === "warning").length;
      showSaveNote(errors || !res.validation.valid
        ? { tone: "warn", text: errors ? t("store.savedErrors", { count: errors }) : t("store.savedNoBuild") }
        : { tone: warnings ? "warn" : "good", text: warnings ? t("store.savedWarnings", { count: warnings }) : t("store.saved") });
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
  return { take, openDesign, save, saveExplicit, saveBeforeLeaving, restoreRecoveredDraft, applyValidation, isReleased, dispose,
    navigationTarget: () => navigationTarget,
    setNavigationTarget: (id: string | null) => { if (alive()) navigationTarget = id; },
    releaseDraft: () => { if (alive()) released = asyncState.ticket(); },
    forgetRelease: () => { released = null; }, cancelNavigation,
  };
}
