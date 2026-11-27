export type EntityType = { key: string; label: string; description: string | null };
export type RelType = { key: string; label: string; inverse_label: string; is_symmetric: boolean };
export type Types = { entity_types: EntityType[]; relationship_types: RelType[] };
export type EntitySummary = {
  id: string; slug: string; type_key: string; canonical_name: string; short_definition: string | null;
  status: string; origin: string; claims: number; relationships: number;
};
export type Candidate = { id: string; canonical_name: string; type_key: string; score: number };

export const CLAIM_TYPES: Record<string, string> = {
  definition: "Definición", reported_fact: "Hecho reportado", hypothesis: "Hipótesis",
  interpretation: "Interpretación", opinion: "Opinión", recommendation: "Recomendación",
  ai_inference: "Inferencia de IA", personal_conclusion: "Conclusión personal",
};

/** A fragment picked in search to be used as evidence (kept in this browser only). */
export type PickedEvidence = { segment_id: string; source_title: string; page: number | null };
const KEY = "kos.evidence";
export function pickEvidence(e: PickedEvidence) {
  try { localStorage.setItem(KEY, JSON.stringify(e)); } catch {}
}
export function pickedEvidence(): PickedEvidence | null {
  try { const v = localStorage.getItem(KEY); return v ? JSON.parse(v) : null; } catch { return null; }
}
export function clearPicked() { try { localStorage.removeItem(KEY); } catch {} }

export type Loc = { page: number | null; section?: string | null; slide?: number | null; t_ms?: number | null };

export function clock(ms: number) {
  const s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

/** ", p. 4" / ", diap. 3" / ", 12:05" — where a citation is in its original. */
export function where(e: Loc, withSection = true) {
  const parts = [
    e.page ? `p. ${e.page}` : null,
    e.slide ? `diap. ${e.slide}` : null,
    e.t_ms != null ? clock(e.t_ms) : null,
  ].filter(Boolean);
  return (parts.length ? `, ${parts.join(", ")}` : "") + (withSection && e.section ? ` · ${e.section}` : "");
}

/** Where a citation opens: audio and video in the player at the second, PDFs in the
 *  reader at the page with the quote highlighted, anything else as the original file. */
export function sourceHref(e: { source_id: string; version_id: string; mime: string; page: number | null; t_ms?: number | null; quote?: string | null }) {
  const timed = e.mime.startsWith("audio/") || e.mime.startsWith("video/") || e.mime === "text/vtt" || e.mime === "application/x-subrip";
  if (timed) return `/play/${e.source_id}?v=${e.version_id}${e.t_ms != null ? `&t=${e.t_ms}` : ""}`;
  if (e.mime === "application/pdf") {
    const q = new URLSearchParams({ v: e.version_id });
    if (e.page) q.set("page", String(e.page));
    if (e.quote) q.set("q", e.quote.slice(0, 400));
    return `/read/${e.source_id}?${q}`;
  }
  return `/api/sources/${e.source_id}/versions/${e.version_id}/file`;
}
