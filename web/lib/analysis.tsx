export const LINK_KIND: Record<string, string> = {
  related: "Trata lo mismo",
  expands: "Amplía",
  summarizes: "Resume",
  contradicts: "Contradice",
  applies: "Aplica a la práctica",
  recommends: "Recomienda",
};

export const STUDY_STATUS: Record<string, { label: string; color: string }> = {
  supported: { label: "Respaldada por estudios", color: "#2e7d32" },
  partial: { label: "Respaldada en parte", color: "#558b2f" },
  contradicted: { label: "Contradicha por estudios", color: "#c62828" },
  mixed: { label: "Estudios a favor y en contra", color: "#b26a00" },
  unchecked: { label: "Hay estudios, sin comprobar", color: "#6d6d6d" },
  no_studies: { label: "Sin estudios sobre el tema", color: "#8d8d8d" },
  study: { label: "Afirmación de un estudio", color: "#1565c0" },
};

export const VERDICT: Record<string, string> = {
  supports: "respalda",
  contradicts: "contradice",
  partial: "respalda en parte",
  unrelated: "no trata lo mismo",
};

export const LEVEL: Record<string, { label: string; color: string }> = {
  high: { label: "Evidencia alta", color: "#2e7d32" },
  moderate: { label: "Evidencia moderada", color: "#558b2f" },
  low: { label: "Evidencia baja", color: "#b26a00" },
  unassessed: { label: "Estudios sin evaluar", color: "#6d6d6d" },
  none: { label: "Sin estudios", color: "#8d8d8d" },
};

export type Check = {
  id: string; verdict: string; explanation: string | null; confidence: number | null; status: string; origin: string;
  study_claim: { id: string; text: string };
  publication: { id: string; title: string; year: number | null; quality: string } | null;
};
export type StudyStatus = { status: string; text: string; pending_review?: boolean; checks: Check[] };

export type Passage = { segment_id: string; text: string; where: string; version_id: string; mime: string; page: number | null; slide: number | null; t_ms: number | null };
export type LinkProposal = {
  id: string; a: { id: string; title: string }; b: { id: string; title: string };
  kind: string; status: string; origin: string; score: number | null; note: string | null; review_task_id: string | null;
  evidence: { similarity: number; a: Passage | null; b: Passage | null }[];
};

export function Badge({ text, color }: { text: string; color: string }) {
  return <span className="badge" style={{ borderColor: color, color }}>{text}</span>;
}
