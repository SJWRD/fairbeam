/** Search-only folding. Never use this spelling for paths, identifiers or persisted names. */
export const searchText = (value: string): string => value
  .replace(/[İIı]/g, "i")
  .normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
