/** People of the Autores library (teachers, researchers, book authors). */
export type Author = { id?: string; name: string; role?: string | null };
export type PersonRow = {
  id: string; name: string; role: string | null; affiliation: string | null; notes: string | null;
  sources: number; claims: number;
};
export const ROLE: Record<string, string> = { teacher: "Profesor/a", researcher: "Investigador/a", author: "Autor/a" };

/** Lowercase, no accents, punctuation as spaces (as the server does). */
export function norm(s: string) {
  return s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^\p{L}\p{N}_]+/gu, " ").trim();
}

/** What the API takes: existing people by id, new ones by name. */
export const toRefs = (list: Author[]) => list.map((a) => (a.id ? { id: a.id } : { name: a.name }));

/** Split a free-text authors field ("A; B" or "A, B") into names to review. */
export function splitNames(text: string) {
  const parts = text.includes(";") ? text.split(";") : text.split(/,| y | and |\n/);
  return parts.map((p) => p.trim()).filter((p) => p.length > 1);
}
