/** Shapes of the study API (flashcards, quizzes, gaps). */
import type { Loc } from "@/lib/knowledge";

export type Ref = Loc & { segment_id: string; source_id: string; version_id: string; mime: string; source_title: string };
export type CardT = {
  id: string; front: string; back: string; quote: string | null; kind: string; origin: string; status: string;
  due_at: string; reps: number; lapses: number; retrievability: number | null;
  entity: { id: string; slug: string; name: string }; reference: Ref | null; preview?: Record<string, string>;
};
export type QuizSummary = {
  id: string; title: string; status: string; error: string | null; module_id: string | null;
  stats: { questions?: number; dropped?: number } | null;
  attempts: { id: string; score: number | null; finished_at: string | null; created_at: string }[];
};
export const RATINGS: [number, string][] = [[1, "Otra vez"], [2, "Difícil"], [3, "Bien"], [4, "Fácil"]];
