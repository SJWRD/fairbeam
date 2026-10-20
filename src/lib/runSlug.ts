/** Keep this file-safe preview in step with the server's slugify; labels stay untouched. */
export function runSlug(text: string): string {
  return text.normalize("NFKD").replace(/ı/g, "i").replace(/\p{M}/gu, "")
    .replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "").toLowerCase().slice(0, 100);
}
