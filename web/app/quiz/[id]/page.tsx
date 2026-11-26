"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { sourceHref, where } from "@/lib/knowledge";
import type { Ref } from "@/lib/study";
import { useMe } from "@/lib/useMe";

type Question = { id: string; kind: "choice" | "true_false" | "short"; prompt: string; options: string[] | null };
type Quiz = { id: string; title: string; status: string; questions: Question[]; attempts: { id: string; score: number | null }[] };
type Item = {
  question_id: string; kind: Question["kind"]; prompt: string; options: string[] | null; correct_answer: number | boolean | string;
  explanation: string | null; quote: string; reference: Ref | null; concept: { slug: string; name: string } | null;
  answer_id: string | null; answer: number | boolean | string | null; correct: boolean | null;
};
type Result = { id: string; score: number | null; finished_at: string | null; items: Item[] };

const show = (q: { kind: string; options: string[] | null }, v: unknown) =>
  v === null || v === undefined || v === "" ? "(sin responder)"
    : q.kind === "choice" ? q.options?.[v as number] ?? String(v)
    : q.kind === "true_false" ? (v ? "Verdadero" : "Falso") : String(v);

export default function QuizPage() {
  const user = useMe();
  const { id } = useParams<{ id: string }>();
  const [quiz, setQuiz] = useState<Quiz | null>(null);
  const [answers, setAnswers] = useState<Record<string, number | boolean | string>>({});
  const [result, setResult] = useState<Result | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { if (user) api<Quiz>(`/api/quizzes/${id}`).then(setQuiz).catch(() => setErr("Cuestionario no encontrado.")); }, [user, id]);

  async function submit() {
    try {
      setResult(await api<Result>(`/api/quizzes/${id}/attempts`, {
        method: "POST",
        body: JSON.stringify({ answers: quiz!.questions.map((q) => ({ question_id: q.id, answer: answers[q.id] ?? null })) }),
      }));
      window.scrollTo(0, 0);
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Error."); }
  }
  async function selfGrade(answerId: string, correct: boolean) {
    setResult(await api<Result>(`/api/quiz-answers/${answerId}`, { method: "PATCH", body: JSON.stringify({ correct }) }));
  }

  if (!quiz) return <main><Header user={user} /><p className={err ? "error" : "muted"}>{err ?? "Cargando…"}</p></main>;
  return (
    <main>
      <Header user={user} />
      <p className="muted"><Link href="/learning">← Aprendizaje</Link></p>
      <h1>{quiz.title}</h1>
      {!result ? (
        <>
          <p className="muted">{quiz.questions.length} preguntas basadas en los materiales del módulo. Al terminar verás la corrección con el pasaje de la fuente.</p>
          {quiz.questions.map((q, i) => (
            <div className="card" key={q.id} style={{ marginBottom: 8 }}>
              <strong>{i + 1}. {q.prompt}</strong>
              {q.kind === "choice" && q.options!.map((o, j) => (
                <label key={j} style={{ display: "flex", gap: 6, margin: "4px 0" }}>
                  <input type="radio" name={q.id} style={{ width: "auto" }} checked={answers[q.id] === j} onChange={() => setAnswers({ ...answers, [q.id]: j })} />{o}
                </label>
              ))}
              {q.kind === "true_false" && [true, false].map((v) => (
                <label key={String(v)} style={{ display: "flex", gap: 6, margin: "4px 0" }}>
                  <input type="radio" name={q.id} style={{ width: "auto" }} checked={answers[q.id] === v} onChange={() => setAnswers({ ...answers, [q.id]: v })} />{v ? "Verdadero" : "Falso"}
                </label>
              ))}
              {q.kind === "short" && (
                <textarea rows={2} style={{ width: "100%" }} value={(answers[q.id] as string) ?? ""} onChange={(e) => setAnswers({ ...answers, [q.id]: e.target.value })} />
              )}
            </div>
          ))}
          <button className="primary" onClick={submit}>Corregir</button>
          {err && <p className="error">{err}</p>}
        </>
      ) : (
        <>
          <p className="card">
            {result.score != null ? <>Resultado: <strong>{Math.round(result.score * 100)} %</strong> de lo corregido.</> : null}
            {!result.finished_at && " Revisa tus respuestas abiertas comparándolas con la fuente y márcalas."}
          </p>
          {result.items.map((it, i) => (
            <div className="card" key={it.question_id} style={{ marginBottom: 8, borderLeft: `4px solid ${it.correct === true ? "#2e7d32" : it.correct === false ? "#c62828" : "#999"}` }}>
              <strong>{i + 1}. {it.prompt}</strong>
              <div>Tu respuesta: {show(it, it.answer)}</div>
              <div>Respuesta correcta: <strong>{show(it, it.correct_answer)}</strong></div>
              {it.explanation && <div className="muted">{it.explanation}</div>}
              <blockquote style={{ margin: "6px 0", paddingLeft: 10, borderLeft: "3px solid var(--border)" }}>
                «{it.quote}»
                {it.reference && <div className="muted" style={{ fontSize: "0.85rem" }}><a href={sourceHref(it.reference)} target="_blank" rel="noopener">{it.reference.source_title}{where(it.reference)}</a></div>}
              </blockquote>
              {it.concept && <div className="muted" style={{ fontSize: "0.85rem" }}>Concepto: <Link href={`/concepts/${it.concept.slug}`}>{it.concept.name}</Link></div>}
              {it.kind === "short" && it.answer_id && (
                <div className="row" style={{ justifyContent: "flex-start", gap: 6, marginTop: 4 }}>
                  <span className="muted">¿Tu respuesta dice lo mismo?</span>
                  <button className={it.correct === true ? "primary" : ""} onClick={() => selfGrade(it.answer_id!, true)}>Sí</button>
                  <button className={it.correct === false ? "primary" : ""} onClick={() => selfGrade(it.answer_id!, false)}>No</button>
                </div>
              )}
            </div>
          ))}
          <button onClick={() => { setResult(null); setAnswers({}); }}>Volver a intentarlo</button>
        </>
      )}
    </main>
  );
}
