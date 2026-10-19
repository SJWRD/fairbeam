import { searchText } from "../lib/searchText.ts";

export type ProjectSort = "modified" | "name";

/** The model list may contain older entries without a timestamp while the server refreshes. */
export interface ProjectListEntry {
  key: string;
  file?: string;
  document?: { id: string; path: string; results_directory?: string };
  model?: { name?: string };
  modified?: number;
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const projectName = (entry: ProjectListEntry) => entry.model?.name?.trim() || entry.key;
const modifiedTime = (entry: ProjectListEntry) => typeof entry.modified === "number" && Number.isFinite(entry.modified) && entry.modified > 0 ? entry.modified : null;

/** Presentation only: never pass this spelling back to file APIs or document ownership checks. */
export function displayProjectPath(path: string): string {
  const display = path.normalize("NFC");
  if (/^\\\\\?\\UNC\\/i.test(display)) return `\\\\${display.slice(8)}`;
  if (/^\\\\\?\\[a-z]:\\/i.test(display)) return display.slice(4);
  return display;
}

/** Keep the volume/share and nearest folder visible; the complete path remains in the tooltip. */
export function projectLocation(entry: ProjectListEntry): { full: string; label: string } {
  const full = displayProjectPath(entry.document?.path ?? entry.file ?? "");
  if (!entry.document) return { full, label: full };
  const separator = full.includes("\\") ? "\\" : "/";
  const root = full.match(/^(?:[a-z]:[\\/]|\\\\[^\\]+\\[^\\]+\\|\/)/i)?.[0];
  if (!root) return { full, label: full };
  const parts = full.slice(root.length).split(separator);
  return { full, label: parts.length > 2 ? `${root}…${separator}${parts.slice(-2).join(separator)}` : full };
}

/** Search the visible name, id and file name, then sort deterministically. Undated entries sort
 * after dated ones in Recently modified mode and use their name as a stable fallback. */
export function visibleProjects<T extends ProjectListEntry>(
  entries: readonly T[],
  query: string,
  sort: ProjectSort,
  favoritesOnly: boolean,
  favoriteKeys: ReadonlySet<string>,
): T[] {
  const term = searchText(query.trim());
  return entries.filter((entry) => {
    if (favoritesOnly && !favoriteKeys.has(entry.key)) return false;
    if (!term) return true;
    return [projectName(entry), entry.key, entry.file ?? "", entry.document?.path ?? "", entry.document?.results_directory ?? "", projectLocation(entry).full].some((value) => searchText(value).includes(term));
  }).sort((a, b) => {
    if (sort === "modified") {
      const aTime = modifiedTime(a), bTime = modifiedTime(b);
      if (aTime !== null || bTime !== null) {
        if (aTime === null) return 1;
        if (bTime === null) return -1;
        if (aTime !== bTime) return bTime - aTime;
      }
    }
    return collator.compare(projectName(a), projectName(b)) || collator.compare(a.key, b.key);
  });
}

export function hasUndatedProjects(entries: readonly ProjectListEntry[]): boolean {
  return entries.some((entry) => modifiedTime(entry) === null);
}
