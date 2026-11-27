export type Level = "essential" | "study" | "detailed";

export const LEVELS: { key: Level; name: string; tip: string }[] = [
  { key: "essential", name: "Esencial", tip: "Las 5-10 ideas clave: repasar en un minuto." },
  { key: "study", name: "Estudio", tip: "Organizado por los apartados del capítulo, con definiciones y datos: estudiar sin releer." },
  { key: "detailed", name: "Detallado", tip: "Punto por punto, cerca del texto: sustituye la lectura." },
];

export type Chapter = {
  id: string; parent_id: string | null; position: number; level: number; title: string;
  page_start: number; page_end: number; pages: number; is_content: boolean;
  origin: "bookmarks" | "index" | "headings" | "manual"; edited: boolean;
};

export type Listing = {
  version_id: string | null; mime?: string; page_count?: number | null;
  detecting: boolean; can_detect: boolean; items: (Chapter & { summaries: Level[] })[];
};

export type Cite = { fragment_id: string; page: number | null; quote: string };
export type SummaryContent = {
  overview: string;
  sections: { heading: string; points: { text: string; citations: Cite[] }[] }[];
  key_terms: { term: string; definition: string; citations: Cite[] }[];
};
export type Summary = {
  id: string; level: Level; content: SummaryContent; markdown: string; words: number;
  citations: number; uncited_points: number; origin: string; created_at: string;
};
export type Estimate = { level: Level; tokens: number; target_words: number; model: string | null; usd: number | null; too_long: boolean };

export type ChapterDetail = Chapter & {
  source: { id: string; title: string };
  version: { id: string; mime: string };
  points: Chapter[]; prev: Chapter | null; next: Chapter | null;
  current: Partial<Record<Level, Summary>>;
  history: { id: string; level: Level; words: number; created_at: string }[];
  estimates: Record<Level, Estimate>;
  generating: Partial<Record<Level, string>>;
  failed: Partial<Record<Level, string>>;
};

export const ORIGIN: Record<Chapter["origin"], string> = {
  bookmarks: "marcadores del PDF", index: "índice del libro", headings: "títulos del texto", manual: "a mano",
};

export function money(usd: number | null) {
  if (usd == null) return "";
  if (usd < 0.01) return "menos de 0,01 $";
  return `≈ ${usd.toFixed(2).replace(".", ",")} $`;
}
