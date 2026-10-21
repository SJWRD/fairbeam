import { createSignal, untrack } from "solid-js";
import type { DesignFile } from "../runner/api";
import type { Design } from "../designer/types";
import { t } from "../i18n";
import { nativeErrorMessage } from "./nativeErrors";
import { serverWarnings, type ServerMessage } from "./serverMessages";

export interface DocumentResult {
  file: string; sha256: string; design_sha256: string; provenance: "source-design";
  status: "current" | "stale" | "missing" | "invalid"; project_file?: string; error?: string;
}
export interface NativeDocument {
  status: "opened" | "saved"; handle: string; path: string; file: DesignFile;
  document: { id: string; path: string; revision: string; results_directory: string };
  results: DocumentResult[]; warnings: string[]; warning_details?: (ServerMessage | null)[];
}
export type NativeDocumentBinding = Omit<NativeDocument, "file" | "status"> & {
  file: Pick<DesignFile, "id" | "backup_scope">;
};
export type DocumentOpen = NativeDocument | { status: "canceled" } | { status: "import"; name: string; content: string };
type Native = { invoke: <T>(command: string, args?: Record<string, unknown>) => Promise<T> };
function native(): Native | undefined {
  return typeof window === "undefined" ? undefined : (window as unknown as { __TAURI_INTERNALS__?: Native }).__TAURI_INTERNALS__;
}
export const nativeDocumentsAvailable = () => !!native();
export type ProjectRemoval = { path: string; results_directory: string };
export async function archiveNativeProject(modelId: string, project: ProjectRemoval, backupScope?: string) {
  if (!native() || !backupScope) throw new Error(t("home.designs.archiveDesktop"));
  return invoke<{ id: string; file: string; moved_to: string; backup_scope: string; recovery_path: string }>(
    "native_document_archive", { modelId, path: project.path, resultsDirectory: project.results_directory, backupScope });
}
const key = (f: Pick<DesignFile, "id" | "backup_scope">) => JSON.stringify([f.backup_scope ?? "", f.id]);
const [bindings, setBindings] = createSignal<ReadonlyMap<string, NativeDocumentBinding>>(new Map());
export const nativeDocumentFor = (f: DesignFile | null) => f ? bindings().get(key(f)) : undefined;
export function rememberNativeDocument(document: NativeDocumentBinding) {
  setBindings(previous => new Map(previous).set(key(document.file), document));
}

/** A recovered portable workspace stays scoped while its native grant is restored. */
export const hasDocumentResultScope = (file: DesignFile | null) => !!file &&
  (!!file.document_id || !!nativeDocumentFor(file) || /^fb_[0-9a-f]{32}$/.test(file.id));
type ResultJob = { model: string; status: string; bundle?: string | null; kind?: string;
  research?: { backend: string; settings: Record<string, unknown>;
    source?: { model_id: string; workspace_hash: string; backup_scope: string; design_sha256: string } } };

/** Research results require explicit server-validated workspace ownership, even when an
 * old source-less run happens to share its embedded model ID with a workspace filename. */
export function documentJobMatches(file: DesignFile, job: ResultJob): boolean {
  if (job.model !== file.id) return false;
  if (job.kind !== "research") return true;
  const research = job.research, source = research?.source;
  return research?.backend === "elmer" && research.settings.analysis === "driven" && !!source &&
    source.model_id === file.id && source.backup_scope === file.backup_scope &&
    /^[0-9a-f]{64}$/.test(source.workspace_hash) && /^[0-9a-f]{64}$/.test(source.design_sha256);
}
type CompletedJob = ResultJob & { id: string; finished?: number | null; kind?: string };
type ResultJournal = { seen: string[]; pending: string[]; seed?: boolean };
const RESULTS_JOURNAL_KEY = "fairbeam.document-results/1";
const MAX_JOURNALS = 24, MAX_JOB_KEYS = 1000;
const OVERFLOW = "completed-results-before-reload";
function readResultJournals(): ReadonlyMap<string, ResultJournal> {
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(RESULTS_JOURNAL_KEY) ?? "[]");
    if (!Array.isArray(raw)) return new Map();
    return new Map(raw.slice(-MAX_JOURNALS).flatMap(entry => {
      if (!Array.isArray(entry) || entry.length !== 2 || typeof entry[0] !== "string") return [];
      const state = entry[1] as ResultJournal;
      if (!state || !Array.isArray(state.seen) || !Array.isArray(state.pending) ||
          [...state.seen, ...state.pending].some(value => typeof value !== "string" || value.length > 1024)) return [];
      return [[entry[0], { seen: state.seen.slice(-MAX_JOB_KEYS), pending: state.pending.slice(-MAX_JOB_KEYS), seed: !!state.seed }] as const];
    }));
  } catch { return new Map(); }
}
const [resultJournals, setResultJournals] = createSignal(readResultJournals());
let observedJobs: readonly CompletedJob[] = [];
let observedScope: string | undefined | null = null;
const completedKeys = (file: DesignFile, jobs: readonly CompletedJob[]) => jobs
  .filter(job => documentJobMatches(file, job) && job.status === "done" && job.bundle)
  .map(job => JSON.stringify([job.id, job.finished ?? null, job.bundle]));
function writeResultJournal(file: DesignFile, state: ResultJournal) {
  setResultJournals(previous => {
    const next = new Map(previous); next.delete(key(file)); next.set(key(file), state);
    while (next.size > MAX_JOURNALS) next.delete(next.keys().next().value!);
    return next;
  });
  try {
    sessionStorage.setItem(RESULTS_JOURNAL_KEY, JSON.stringify([...untrack(resultJournals)].map(([owner, value]) => [owner, {
      seen: value.seen.slice(-MAX_JOB_KEYS),
      pending: value.seen.length > MAX_JOB_KEYS || value.pending.length > MAX_JOB_KEYS
        ? [OVERFLOW, ...value.pending.filter(value => value !== OVERFLOW).slice(1 - MAX_JOB_KEYS)] : value.pending,
      seed: !!value.seed || value.seen.length > MAX_JOB_KEYS,
    }])));
  } catch { /* The current window still tracks pending results when storage is unavailable. */ }
}
/** Explicit Open accepts the document's saved references; old workspace history is not a new run. */
export function resetNativeDocumentResults(file: DesignFile, jobs: readonly CompletedJob[], scope: string | undefined | null = file.backup_scope) {
  observedJobs = jobs; observedScope = scope;
  writeResultJournal(file, { seen: scope === file.backup_scope ? completedKeys(file, jobs) : [], pending: [], seed: scope !== file.backup_scope });
}
/** Called with the current workspace and job history. Repeated polls never create new pending work. */
export function observeNativeDocumentResults(file: DesignFile | null, jobs: readonly CompletedJob[], scope: string | undefined | null = file?.backup_scope) {
  observedJobs = jobs; observedScope = scope;
  if (!nativeDocumentsAvailable() || !file?.backup_scope || scope !== file.backup_scope || !hasDocumentResultScope(file)) return;
  const completed = completedKeys(file, jobs), previous = untrack(resultJournals).get(key(file));
  if (!previous) { writeResultJournal(file, { seen: completed, pending: [] }); return; }
  const seen = new Set(previous.seen), pending = new Set(previous.pending);
  for (const result of completed) {
    if (!seen.has(result) && !previous.seed) pending.add(result);
    seen.add(result);
  }
  if (previous.seed || seen.size !== previous.seen.length || pending.size !== previous.pending.length)
    writeResultJournal(file, { seen: [...seen], pending: [...pending] });
}
/** A stable token also invalidates an earlier Don't save choice when another run finishes. */
export const nativeResultsPendingToken = (file: DesignFile | null) => nativeDocumentsAvailable() && file?.backup_scope
  ? JSON.stringify(resultJournals().get(key(file))?.pending ?? []) : "[]";
function acknowledgeResults(file: DesignFile, candidates: ReadonlySet<string>) {
  const previous = untrack(resultJournals).get(key(file));
  const completed = observedScope === file.backup_scope ? completedKeys(file, observedJobs) : [];
  const pending = new Set(previous?.pending);
  for (const value of completed) if (!candidates.has(value)) pending.add(value);
  for (const value of candidates) pending.delete(value);
  writeResultJournal(file, { seen: [...new Set([...(previous?.seen ?? []), ...completed])], pending: [...pending] });
}
/** undefined retains the legacy model-ID result list; a portable document admits exact refs/jobs. */
export function documentResultFiles(file: DesignFile | null, jobs: readonly ResultJob[]): Set<string> | undefined {
  if (!file || !hasDocumentResultScope(file)) return undefined;
  const included = new Set(nativeDocumentFor(file)?.results.flatMap(result => result.project_file ? [result.project_file] : []));
  for (const job of jobs) if (documentJobMatches(file, job) && job.status === "done" && job.bundle) included.add(job.bundle);
  return included;
}
async function invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  const shell = native();
  if (!shell) throw new Error(t("document.desktopRequired"));
  try { return await shell.invoke<T>(command, args); }
  catch (error) {
    throw new Error(nativeErrorMessage(error, true));
  }
}
export const pickNativeDocument = (handle?: string) => invoke<DocumentOpen>("native_document_open", { handle });
export const pendingNativeDocument = (exclude: string[] = []) => invoke<{ handle: string; displayName: string } | null>("native_document_next_open", { exclude });
/** Consume a canceled/failed OS request without revoking an opened document's grant. */
export const dismissPendingNativeDocument = (handle: string) => invoke<boolean>("native_document_dismiss_pending", { handle });

/** A renderer reload may recover an existing shell grant. Never replace the current workspace
 * design with a cached file, and never infer authorization from a saved browser path. */
export async function restoreNativeDocumentBinding(file: DesignFile): Promise<void> {
  if (!nativeDocumentsAvailable() || nativeDocumentFor(file)) return;
  const result = await invoke<NativeDocumentBinding | null>("native_document_restore", { modelId: file.id });
  if (result && key(result.file) === key(file) && !nativeDocumentFor(file)) rememberNativeDocument(result);
}

/** The shell retains path and external revision. Bound saves use the saved workspace revision;
 * a first save may fork a draft snapshot while leaving its legacy source revision untouched. */
export async function persistNativeDocument(file: DesignFile, hash: string, saveAs: boolean, draftDesign?: Design): Promise<{ canceled: true } | { file?: DesignFile; warnings: string[] } | void> {
  if (!nativeDocumentsAvailable()) return;
  if (hasDocumentResultScope(file) && !nativeDocumentFor(file)) await restoreNativeDocumentBinding(file);
  const current = nativeDocumentFor(file);
  const firstDraft = !current && !hasDocumentResultScope(file) ? draftDesign : undefined;
  const candidates = new Set([...(observedScope === file.backup_scope ? completedKeys(file, observedJobs) : []), ...(untrack(resultJournals).get(key(file))?.pending ?? [])]);
  const result = await invoke<NativeDocument | { status: "canceled" }>("native_document_save", {
    request: { handle: current?.handle, modelId: file.id, workspaceHash: hash, saveAs,
      suggestedName: `${(firstDraft ?? file.design).model.name || file.id}.fb`,
      ...(firstDraft ? { draftDesign: firstDraft } : {}) },
  });
  if (result.status === "canceled") return { canceled: true };
  // Another Open of this UUID may have selected a different location meanwhile. A late save
  // remains committed on disk, but must not retarget that newer selection's next Save.
  if (nativeDocumentFor(file) === current) {
    rememberNativeDocument(result);
    acknowledgeResults(file, candidates);
    if (result.file.id !== file.id) resetNativeDocumentResults(result.file, observedJobs, observedScope);
  }
  return { ...(result.file.id === file.id ? {} : { file: result.file }), warnings: serverWarnings(result.warnings, result.warning_details) };
}
