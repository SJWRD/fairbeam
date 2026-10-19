// Model code editor state: the file open in the editor, the unsaved draft, save with optimistic
// concurrency, validation results (errors with lines), templates and version history.
import { createMemo, createRoot, createSignal } from "solid-js";
import { api, ApiError, type ModelEntry, type ModelSource, type ModelVersion, type Validation } from "../runner/api";
import { applyModelEntry, modelKey, refreshModels, runPreview, selectModel } from "../runner/store";

import { modifierShortcut } from "../lib/shortcut";
import { confirmDraftDiscard } from "../lib/ConfirmDialog";
import { registerUnsavedWork } from "../lib/unsavedWork";
import { fmt, t } from "../i18n";

export type PanelTab = "run" | "code" | "design";
/** the save shortcut as shown to the user (CodeMirror's Mod-s: Cmd on macOS, Ctrl elsewhere) */
export const SAVE_KEY = modifierShortcut("S");
export const [panelTab, setPanelTab] = createSignal<PanelTab>("run");

export const [file, setFile] = createSignal<ModelSource | null>(null);
export const [draft, setDraft] = createSignal("");
export const [loading, setLoading] = createSignal(false);
export const [saving, setSaving] = createSignal(false);
export const [validation, setValidation] = createSignal<Validation | null>(null);
export const [message, setMessage] = createSignal<{ tone: "good" | "warn" | "critical"; text: string } | null>(null);
export const [conflict, setConflict] = createSignal<string | null>(null); // hash on disk after a 409
export const [versions, setVersions] = createSignal<ModelVersion[]>([]);
export const [templates, setTemplates] = createSignal<ModelEntry[]>([]);
/** "new" from a template, or "duplicate" of the open model */
export const [dialog, setDialog] = createSignal<null | "new" | "duplicate">(null);
/** which kind the New model dialog starts on (Start opens it for a Python model) */
export const [newModelKind, setNewModelKind] = createSignal<"python" | "design">("design");
/** bumped when the editor content must be replaced from outside (load, revert) */
export const [docVersion, setDocVersion] = createSignal(0);

export const dirty = createRoot(() => createMemo(() => !!file() && draft() !== file()!.source));
registerUnsavedWork("python-code", {
  snapshot: () => dirty() ? JSON.stringify([file(), draft()]) : null,
  busy: saving,
  confirm: () => confirmDiscard(),
});

/** Ask before dropping unsaved edits. */
export async function confirmDiscard(): Promise<boolean> {
  if (!dirty()) return true;
  return confirmDraftDiscard(t("editor.confirmDiscard", { file: file()!.file }));
}

let sourceSeq = 0;

export async function openSource(id: string) {
  const mine = ++sourceSeq;
  setSaving(false);
  setLoading(true);
  setMessage(null);
  setConflict(null);
  try {
    const src = await api.modelSource(id);
    if (mine !== sourceSeq) return; // another model was picked while this one loaded
    setFile(src);
    setDraft(src.source);
    setValidation(null);
    setDocVersion((v) => v + 1);
    refreshHistory();
  } catch (e) {
    if (mine !== sourceSeq) return;
    setFile(null);
    setMessage({ tone: "critical", text: (e as Error).message });
  } finally {
    if (mine === sourceSeq) setLoading(false);
  }
}

export async function refreshHistory() {
  const f = file();
  const mine = sourceSeq;
  if (!f || f.readonly) return setVersions([]);
  try {
    const history = await api.modelHistory(f.id);
    if (mine === sourceSeq && file()?.id === f.id) setVersions(history);
  } catch {
    if (mine === sourceSeq && file()?.id === f.id) setVersions([]);
  }
}

/** After a save or create: new PARAMS into the form, then a fresh geometry preview. */
function applyValidation(v: Validation) {
  setValidation(v);
  if (v.model) applyModelEntry(v.model);
  if (v.valid) runPreview();
}

export async function save() {
  const f = file();
  if (!f || f.readonly || saving()) return;
  const mine = sourceSeq;
  const owns = () => mine === sourceSeq && file()?.id === f.id;
  setSaving(true);
  setMessage(null);
  try {
    const text = draft();
    const res = await api.saveSource(f.id, text, conflict() ?? f.hash);
    if (!owns()) return;
    setFile({ ...f, source: text, hash: res.hash });
    setConflict(null);
    applyValidation(res.validation);
    const v = res.validation;
    setMessage(
      v.valid
        ? { tone: "good", text: t("editor.saved", { params: v.model?.params?.length ?? 0 }) }
        : { tone: "critical", text: t(v.error?.stage === "build" ? "editor.savedNoBuild" : "editor.savedNoLoad") },
    );
    refreshHistory();
  } catch (e) {
    if (!owns()) return;
    const err = e as ApiError;
    if (err.status === 409 && typeof err.data.current_hash === "string") {
      setMessage({ tone: "warn", text: t("editor.conflict") });
      setConflict(err.data.current_hash as string);
    } else {
      setMessage({ tone: "critical", text: err.status === 0 ? t("common.serverUnreachable") : err.message });
    }
  } finally {
    if (owns()) setSaving(false);
  }
}

/** Put an older version into the editor as unsaved changes (save to restore it). */
export async function loadVersion(version: string) {
  const f = file();
  const mine = sourceSeq;
  const text = draft();
  const owns = () => mine === sourceSeq && file()?.id === f?.id && draft() === text;
  if (!f || !(await confirmDiscard())) return;
  if (!owns()) return;
  try {
    const v = await api.modelVersion(f.id, version);
    if (!owns()) return;
    setDraft(v.source);
    setDocVersion((n) => n + 1);
    setMessage({ tone: "warn", text: t("editor.loadedVersion", { time: prettyVersion(version) }) });
  } catch (e) {
    if (!owns()) return;
    setMessage({ tone: "critical", text: (e as Error).message });
  }
}

export async function loadTemplates() {
  try {
    setTemplates(await api.templates());
  } catch {
    setTemplates([]);
  }
}

export async function createModel(body: { id: string; name?: string; template?: string; from?: string }) {
  const res = await api.createModel(body); // errors (422 fields, 409) are shown by the dialog
  await refreshModels();
  ++sourceSeq;
  setSaving(false);
  selectModel(res.id);
  setFile({ id: res.id, file: res.file, source: res.source, hash: res.hash, readonly: res.readonly });
  setDraft(res.source);
  setDocVersion((v) => v + 1);
  setVersions([]);
  applyValidation(res.validation);
  setMessage(
    res.validation.valid
      ? { tone: "good", text: t("editor.created", { file: res.file, key: SAVE_KEY }) }
      : { tone: "critical", text: t("editor.createdNoLoad", { file: res.file }) },
  );
  setPanelTab("code");
  return res;
}

/** "20260924-234501-2" -> "24 Sep 23:45:01" (in the UI language: "24 Eyl 23:45:01") */
export function prettyVersion(v: string): string {
  const m = v.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/);
  if (!m) return v;
  const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
  return fmt.dateTime(d, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// The editor component registers how to move the cursor to a line (for the error panel).
let jump: ((line: number) => void) | null = null;
export function registerJump(fn: ((line: number) => void) | null) {
  jump = fn;
}
export function gotoLine(line: number) {
  jump?.(line);
}

/** Keep the editor on the model picked in the Run panel. */
export function syncToModel() {
  const id = modelKey();
  if (id && file()?.id !== id) openSource(id);
}
