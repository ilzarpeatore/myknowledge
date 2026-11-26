"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { QuizSummary } from "@/lib/study";

/** Quizzes of a course module: generate one with AI from the module's materials. */
export function ModuleQuizzes({ moduleId }: { moduleId: string }) {
  const [items, setItems] = useState<QuizSummary[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api<QuizSummary[]>(`/api/quizzes?module_id=${moduleId}`).then(setItems).catch(() => {}), [moduleId]);
  useEffect(() => { load(); }, [load]);
  const generating = items.some((q) => q.status === "generating");
  useEffect(() => {
    if (!generating) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [generating, load]);

  async function create() {
    setErr(null);
    try {
      await api(`/api/modules/${moduleId}/quizzes`, { method: "POST" });
      load();
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Error."); }
  }
  const best = (q: QuizSummary) => Math.max(...q.attempts.map((a) => a.score ?? 0), 0);

  return (
    <div style={{ marginTop: 6 }}>
      <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}>
        <button onClick={create} disabled={generating}>{generating ? "Preparando cuestionario…" : "Cuestionario del módulo (IA)"}</button>
        {items.map((q) => (
          <span key={q.id} className="muted" style={{ fontSize: "0.85rem" }}>
            {q.status === "ready" ? <Link href={`/quiz/${q.id}`}>{q.title.split(" · ").at(-1)}</Link> : q.status === "failed" ? <span className="error" title={q.error ?? ""}>falló</span> : "…"}
            {q.attempts.length > 0 && ` (mejor ${Math.round(best(q) * 100)} %)`}
          </span>
        ))}
      </div>
      {err && <p className="error">{err}</p>}
    </div>
  );
}
