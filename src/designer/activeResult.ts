// The run an export of the open design is made from (Export package, the Post-processing PDF report and Package).
// In Design mode the global bundle() is whatever the 3D view shows: the geometry preview most of the time (after an
// edit, after a visit to Examples), the shown run only while a run node is focused. The results of a design are its
// runs, so the exports take them from here: the run the design shows (the dock's result: the focused run, else the
// latest completed one, loaded by designRun.ts), else the newest run the project index lists for it (read here on
// demand). Never the geometry preview, never an Examples bundle.
import { createEffect, createRoot, createSignal, on } from "solid-js";
import type { Bundle } from "../types";
import { appMode } from "../workspace";
import { matchesResultIndex, resultStamp } from "../lib/resultIdentity";
import { bundle, index, source } from "../state";
import { designResult, designResultLoading, pendingDesignResultFile } from "../runner/designRun";
import { file as designFile } from "./store";
import { designRuns, loadRunBundle } from "./runResults";

export interface DesignResultState {
  /** the run's bundle, null while none is known */
  bundle: Bundle | null;
  /** its result file */
  file: string | null;
  /** the newest listed run is being read */
  loading: boolean;
}

const [fallback, setFallback] = createSignal<{ file: string; bundle: Bundle } | null>(null);
const [reading, setReading] = createSignal<{ file: string; key: string | undefined } | null>(null);

/** The newest run the index lists for the open design when the dock has none (a run of an earlier session, or one a
 * script started): read once per indexed generation, only in Design mode. */
createRoot(() => {
  createEffect(on([() => appMode(), () => designFile()?.design.model.id, () => designResult()?.file, () => designRuns()[0]?.file, () => resultStamp(index().find(entry => entry.file === designRuns()[0]?.file))], ([mode, model, shown, newest]) => {
    if (fallback() && (fallback()!.bundle.model.id !== model || fallback()!.file !== newest || !matchesResultIndex(index().find(entry => entry.file === fallback()!.file), fallback()!.bundle))) setFallback(null);
    if (mode !== "design" || !model || shown || pendingDesignResultFile() || !newest || fallback()?.file === newest || reading()?.file === newest && reading()?.key === resultStamp(index().find(entry => entry.file === newest))) return;
    const request = { file: newest, key: resultStamp(index().find(entry => entry.file === newest)) };
    setReading(request);
    loadRunBundle(newest).then((b) => {
      if (reading() === request && b.model.id === designFile()?.design.model.id && b.results) setFallback({ file: newest, bundle: b });
    }, () => { /* the run cannot be read: the design has no exportable run */ }).finally(() => {
      if (reading() === request) setReading(null);
    });
  }));
});

/** The design's run for exports in Design mode (see the top of this file); outside Design mode nothing. */
export function designResultState(): DesignResultState {
  const model = designFile()?.design.model.id;
  if (appMode() !== "design" || !model) return { bundle: null, file: null, loading: false };
  const pending = pendingDesignResultFile();
  if (pending) return { bundle: null, file: pending, loading: designResultLoading() !== null };
  const shown = designResult();
  if (shown?.bundle.results && shown.bundle.model.id === model) return { bundle: shown.bundle, file: shown.file, loading: false };
  const f = fallback();
  if (f && f.bundle.model.id === model && matchesResultIndex(index().find(entry => entry.file === f.file), f.bundle)) return { bundle: f.bundle, file: f.file, loading: false };
  return { bundle: null, file: null, loading: reading() !== null };
}

/** The design's run for exports, or null. */
export const designResultBundle = (): Bundle | null => designResultState().bundle;

/** The bundle an export package is made from: in Design mode the design's run, else its geometry as the 3D view shows
 * it (the preview, never a bundle of another model); outside Design mode the shown bundle. */
export function exportBundle(): Bundle | null {
  if (appMode() !== "design") return bundle();
  if (pendingDesignResultFile()) return null;
  const run = designResultBundle();
  if (run) return run;
  const b = bundle();
  const model = designFile()?.design.model.id;
  return b && model && b.model.id === model && (b.preview || matchesResultIndex(index().find(entry => entry.file === source()), b)) ? b : null;
}
