import type { Bundle, ProjectIndexEntry } from "../types";

export const resultStamp = (entry: ProjectIndexEntry | undefined) =>
  entry ? JSON.stringify([entry.model, entry.created ?? "", entry.engine]) : undefined;

// Same labels as the index writer's _index_engine (python/fairbeam/cli.py), including
// legacy CPU runs and a GPU backend that is only identified in the solver log.
function indexedEngine(b: Bundle): string | undefined {
  if (!b.run) return undefined;
  if ((b.run.engine ?? "cpu") !== "gpu") return "CPU";
  for (const line of [...(b.run.log_tail ?? [])].reverse()) {
    const match = /backend:\s*(Metal|CUDA)\b/i.exec(line);
    if (match) return match[1].toUpperCase() === "CUDA" ? "CUDA" : "Metal";
  }
  return b.run.host?.os === "Darwin" ? "Metal" : "GPU";
}
export const matchesResultIndex = (entry: ProjectIndexEntry | undefined, b: Bundle) => {
  // Older indexes may omit created, and the index writer emits null for an undated bundle.
  // Unknown dates cannot prove freshness; retain the model/engine and request-generation guards.
  // A known index date always requires an exact match, including for an undated bundle.
  return !entry || (b.model.id === entry.model &&
    (entry.created == null || entry.created === "" || b.created === entry.created) &&
    (!entry.engine || indexedEngine(b) === entry.engine));
};
