/** Portable storage envelope. The server still validates the embedded design before importing it. */
export interface FairbeamDocument {
  schema: "fairbeam.document/1";
  id: string;
  // This preflight has not validated the full Design structure or expressions.
  design: Record<string, unknown> & { schema: "fairbeam.design/1" };
  results_directory: string;
  results: { file: string; sha256: string; design_sha256: string }[];
}

const hash = /^[0-9a-f]{64}$/;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const fields = (value: Record<string, unknown>, expected: string[]) => {
  const actual = Object.keys(value);
  return actual.length === expected.length && expected.every(key => Object.hasOwn(value, key));
};

function portableName(value: unknown): value is string {
  return typeof value === "string" && value.length >= 1 && new TextEncoder().encode(value).length <= 180
    && !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(value)
    && value !== "." && value !== ".." && !/[. ]$/.test(value) && !/[\x00-\x1f/\\:*?"<>|]/.test(value)
    && !/^(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])$/i.test(value.split(".")[0]);
}

/** Validate envelope metadata only. Never use a result checksum as evidence of solver accuracy. */
export function documentEnvelope(value: unknown): FairbeamDocument {
  if (!object(value) || value.schema !== "fairbeam.document/1"
      || !fields(value, ["schema", "id", "design", "results_directory", "results"])) {
    throw new Error("Unsupported Fairbeam document envelope");
  }
  if (typeof value.id !== "string" || !uuid.test(value.id)) throw new Error("Invalid document identity");
  if (!object(value.design) || value.design.schema !== "fairbeam.design/1") throw new Error("Invalid embedded design schema");
  if (!portableName(value.results_directory)) throw new Error("Invalid results directory");
  if (!Array.isArray(value.results) || value.results.length > 10000) throw new Error("Invalid result references");
  const names = new Set<string>();
  for (const ref of value.results) {
    if (!object(ref) || !fields(ref, ["file", "sha256", "design_sha256"])
        || !portableName(ref.file) || !ref.file.endsWith(".json") || names.has(ref.file.toLowerCase())
        || typeof ref.sha256 !== "string" || !hash.test(ref.sha256)
        || typeof ref.design_sha256 !== "string" || !hash.test(ref.design_sha256)) {
      throw new Error("Invalid result reference");
    }
    names.add(ref.file.toLowerCase());
  }
  return value as unknown as FairbeamDocument;
}
