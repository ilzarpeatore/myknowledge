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

export function sourceHref(e: { source_id: string; version_id: string; mime: string; page: number | null }) {
  const base = `/api/sources/${e.source_id}/versions/${e.version_id}/file`;
  return e.mime === "application/pdf" && e.page ? `${base}#page=${e.page}` : base;
}
