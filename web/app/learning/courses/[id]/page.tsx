"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { ModuleQuizzes } from "@/components/ModuleQuizzes";
import { api, ApiError } from "@/lib/api";
import { Bar, STATUS, hours, type Course } from "@/lib/learning";
import { useMe } from "@/lib/useMe";

type Material = { id: string; source_id: string; title: string; role: string; status: string; lesson_id: string | null };
type LessonT = {
  id: string; title: string; duration_minutes: number | null; url: string | null;
  progress: { status: string; time_spent_minutes: number; notes: string | null };
  materials: Material[];
};
type ModuleT = { id: string; title: string; lessons: LessonT[] };
type Detail = Course & { modules: ModuleT[]; materials: Material[] };
type Concept = { id: string; slug: string; name: string; claims: number; lessons: string[] };
type SourceLite = { id: string; title: string };

const LESSON_STATUS: Record<string, string> = { not_started: "Sin empezar", in_progress: "En curso", completed: "Completada" };

export default function CoursePage() {
  const user = useMe();
  const { id } = useParams<{ id: string }>();
  const [c, setC] = useState<Detail | null>(null);
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [sources, setSources] = useState<SourceLite[]>([]);
  const [newModule, setNewModule] = useState("");
  const [newLesson, setNewLesson] = useState<Record<string, string>>({});
  const [mat, setMat] = useState({ source_id: "", lesson_id: "" });
  const [openNotes, setOpenNotes] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    const [d, k] = await Promise.all([api<Detail>(`/api/courses/${id}`), api<Concept[]>(`/api/courses/${id}/concepts`)]);
    setC(d); setConcepts(k);
  }, [id]);
  useEffect(() => {
    if (!user) return;
    load().catch(() => setMsg({ ok: false, text: "Curso no encontrado." }));
    api<{ items: SourceLite[] }>("/api/sources?limit=200").then((r) => setSources(r.items));
  }, [user, load]);

  async function act(fn: () => Promise<unknown>, ok?: string) {
    try { await fn(); if (ok) setMsg({ ok: true, text: ok }); await load(); }
    catch (err) { setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Error." }); }
  }
  const put = (lessonId: string, body: object) =>
    api(`/api/lessons/${lessonId}/progress`, { method: "PUT", body: JSON.stringify(body) });

  if (!c) return <main><Header user={user} /><p className={msg ? "error" : "muted"}>{msg?.text ?? "Cargando…"}</p></main>;
  const lessonName = (lid: string) => c.modules.flatMap((m) => m.lessons).find((l) => l.id === lid)?.title;

  return (
    <main>
      <Header user={user} />
      <p className="muted"><Link href="/learning">← Aprendizaje</Link>{c.program ? ` · ${c.program}` : ""}</p>
      <div className="row">
        <h1 style={{ marginBottom: 0 }}>{c.title}</h1>
        <select value={c.status} onChange={(e) => act(() => api(`/api/courses/${c.id}`, { method: "PATCH", body: JSON.stringify({ status: e.target.value }) }))}>
          {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </div>
      <p className="muted">{c.progress.completed}/{c.progress.lessons} lecciones · {hours(c.progress.minutes)} de estudio</p>
      <Bar percent={c.progress.percent} />
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}

      <h2>Estructura</h2>
      {c.modules.map((m) => (
        <div className="card" key={m.id} style={{ marginBottom: 8 }}>
          <div className="row">
            <strong>{m.title}</strong>
            <button onClick={() => { if (confirm(`¿Borrar «${m.title}» y sus lecciones?`)) act(() => api(`/api/modules/${m.id}`, { method: "DELETE" }), "Módulo borrado."); }}>Borrar módulo</button>
          </div>
          <ModuleQuizzes moduleId={m.id} />
          <table><tbody>
            {m.lessons.map((l) => (
              <tr key={l.id}>
                <td style={{ width: 28 }}>
                  <input type="checkbox" style={{ width: "auto" }} checked={l.progress.status === "completed"}
                    aria-label="Completada"
                    onChange={(e) => {
                      const status = e.target.checked ? "completed" : "in_progress";
                      // Optimistic: reflect the change at once, then reload from the server.
                      setC({ ...c, modules: c.modules.map((mm) => ({ ...mm, lessons: mm.lessons.map((ll) =>
                        ll.id === l.id ? { ...ll, progress: { ...ll.progress, status } } : ll) })) });
                      act(() => put(l.id, { status }));
                    }} />
                </td>
                <td>
                  {l.title}
                  {l.materials.map((x) => <div key={x.id} className="muted">📄 {x.title}</div>)}
                  {openNotes === l.id && (
                    <form onSubmit={(e) => { e.preventDefault(); act(() => put(l.id, { notes }), "Apuntes guardados.").then(() => setOpenNotes(null)); }}>
                      <textarea rows={4} style={{ width: "100%" }} value={notes} onChange={(e) => setNotes(e.target.value)} />
                      <button>Guardar apuntes</button>
                    </form>
                  )}
                </td>
                <td className="muted">{LESSON_STATUS[l.progress.status]} · {hours(l.progress.time_spent_minutes)}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <button onClick={() => act(() => put(l.id, { add_minutes: 30, status: l.progress.status === "not_started" ? "in_progress" : undefined }))}>+30 min</button>{" "}
                  <button onClick={() => { setOpenNotes(openNotes === l.id ? null : l.id); setNotes(l.progress.notes ?? ""); }}>Apuntes{l.progress.notes ? " ✓" : ""}</button>
                </td>
              </tr>
            ))}
          </tbody></table>
          <form className="row" style={{ justifyContent: "flex-start", marginTop: 8 }} onSubmit={(e) => {
            e.preventDefault();
            act(() => api(`/api/modules/${m.id}/lessons`, { method: "POST", body: JSON.stringify({ title: newLesson[m.id] }) })).then(() => setNewLesson({ ...newLesson, [m.id]: "" }));
          }}>
            <input style={{ maxWidth: 300 }} placeholder="Nueva lección" value={newLesson[m.id] ?? ""} onChange={(e) => setNewLesson({ ...newLesson, [m.id]: e.target.value })} required />
            <button>Añadir</button>
          </form>
        </div>
      ))}
      <form className="card row" style={{ justifyContent: "flex-start" }} onSubmit={(e) => {
        e.preventDefault();
        act(() => api(`/api/courses/${c.id}/modules`, { method: "POST", body: JSON.stringify({ title: newModule }) })).then(() => setNewModule(""));
      }}>
        <input style={{ maxWidth: 300 }} placeholder="Nuevo módulo" value={newModule} onChange={(e) => setNewModule(e.target.value)} required />
        <button>Añadir módulo</button>
      </form>

      <h2>Materiales</h2>
      <div className="card">
        {c.materials.length === 0 && c.modules.every((m) => m.lessons.every((l) => l.materials.length === 0)) && <p className="muted" style={{ marginTop: 0 }}>Sin materiales. Sube los documentos en la Biblioteca y asócialos aquí.</p>}
        {c.materials.map((x) => <div key={x.id}>📄 {x.title} <span className="muted">(curso)</span></div>)}
        <form className="row" style={{ justifyContent: "flex-start", marginTop: 8 }} onSubmit={(e) => {
          e.preventDefault();
          act(() => api(`/api/courses/${c.id}/materials`, { method: "POST", body: JSON.stringify({ source_id: mat.source_id, lesson_id: mat.lesson_id || null }) }), "Material asociado.");
        }}>
          <select value={mat.source_id} onChange={(e) => setMat({ ...mat, source_id: e.target.value })} required>
            <option value="">Elige una fuente de la biblioteca…</option>
            {sources.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
          </select>
          <select value={mat.lesson_id} onChange={(e) => setMat({ ...mat, lesson_id: e.target.value })}>
            <option value="">Todo el curso</option>
            {c.modules.flatMap((m) => m.lessons.map((l) => <option key={l.id} value={l.id}>{m.title} · {l.title}</option>))}
          </select>
          <button>Asociar</button>
        </form>
      </div>

      <h2>Conceptos de este curso ({concepts.length}) <Link className="badge" href={`/study?course=${c.id}`}>lagunas de este curso</Link></h2>
      <div className="card">
        {concepts.length === 0 ? <p className="muted" style={{ margin: 0 }}>Aparecerán cuando haya afirmaciones que citen los materiales de este curso.</p> :
          concepts.map((k) => (
            <div key={k.id}>
              <Link href={`/concepts/${k.slug}`}>{k.name}</Link> <span className="muted">· {k.claims} afirmación(es){k.lessons.length ? ` · ${k.lessons.map(lessonName).filter(Boolean).join(", ")}` : ""}</span>
            </div>
          ))}
      </div>
    </main>
  );
}
