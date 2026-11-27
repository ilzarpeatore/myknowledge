"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { Bar, STATUS, hours, type Course } from "@/lib/learning";
import { useMe } from "@/lib/useMe";

type Program = { id: string; name: string; provider: string | null; status: string; courses: number; lessons: number; completed: number; percent: number };
type Discipline = { id: string; name: string; parent_id: string | null };

export default function LearningPage() {
  const user = useMe();
  const [programs, setPrograms] = useState<Program[]>([]);
  const [courses, setCourses] = useState<Course[]>([]);
  const [disciplines, setDisciplines] = useState<Discipline[]>([]);
  const [prog, setProg] = useState({ name: "", provider: "" });
  const [course, setCourse] = useState({ title: "", program_id: "", status: "in_progress" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    const [p, c, d] = await Promise.all([
      api<Program[]>("/api/programs"), api<Course[]>("/api/courses"), api<Discipline[]>("/api/disciplines"),
    ]);
    setPrograms(p); setCourses(c); setDisciplines(d);
  }, []);
  useEffect(() => { if (user) load(); }, [user, load]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); setMsg({ ok: true, text: ok }); load(); }
    catch (err) { setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Error." }); }
  }

  const roots = disciplines.filter((d) => !d.parent_id);
  return (
    <main>
      <Header user={user} />
      <h1>Aprendizaje</h1>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}

      <h2>Disciplinas</h2>
      <div className="card">
        {roots.length === 0 ? (
          <p style={{ margin: 0 }}>
            Aún no hay disciplinas.{" "}
            <button onClick={() => act(() => api("/api/disciplines/seed-training-science", { method: "POST" }), "Estructura creada.")}>
              Crear «Ciencias del entrenamiento» y sus áreas
            </button>
          </p>
        ) : roots.map((r) => (
          <p key={r.id} style={{ margin: "4px 0" }}>
            <strong>{r.name}</strong>: <span className="muted">{disciplines.filter((d) => d.parent_id === r.id).map((d) => d.name).join(" · ")}</span>
          </p>
        ))}
      </div>

      <h2>Formaciones</h2>
      {programs.map((p) => (
        <div className="card" key={p.id} style={{ marginBottom: 8 }}>
          <div className="row"><strong>{p.name}</strong><span className="badge">{STATUS[p.status]}</span></div>
          <div className="muted">{p.provider ? `${p.provider} · ` : ""}{p.courses} cursos · {p.completed}/{p.lessons} lecciones</div>
          <Bar percent={p.percent} />
        </div>
      ))}
      <form className="card row" style={{ justifyContent: "flex-start" }} onSubmit={(e) => {
        e.preventDefault();
        act(() => api("/api/programs", { method: "POST", body: JSON.stringify({ name: prog.name, provider: prog.provider || null }) }), "Formación creada.")
          .then(() => setProg({ name: "", provider: "" }));
      }}>
        <input style={{ maxWidth: 280 }} placeholder="Nueva formación" value={prog.name} onChange={(e) => setProg({ ...prog, name: e.target.value })} required />
        <input style={{ maxWidth: 220 }} placeholder="Institución (opcional)" value={prog.provider} onChange={(e) => setProg({ ...prog, provider: e.target.value })} />
        <button>Crear</button>
      </form>

      <h2>Cursos</h2>
      <div className="card table-wrap">
        {courses.length === 0 ? <p className="muted">Todavía no hay cursos.</p> : (
          <table>
            <thead><tr><th>Curso</th><th>Formación</th><th>Estado</th><th>Progreso</th><th>Tiempo</th></tr></thead>
            <tbody>{courses.map((c) => (
              <tr key={c.id}>
                <td><Link href={`/learning/courses/${c.id}`}>{c.title}</Link>{c.teacher && <div className="muted">{c.teacher}</div>}</td>
                <td>{c.program ?? "—"}</td>
                <td>{STATUS[c.status]}</td>
                <td style={{ minWidth: 120 }}>{c.progress.completed}/{c.progress.lessons}<Bar percent={c.progress.percent} /></td>
                <td>{hours(c.progress.minutes)}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
      <form className="card row" style={{ justifyContent: "flex-start", marginTop: 8 }} onSubmit={(e) => {
        e.preventDefault();
        act(() => api("/api/courses", { method: "POST", body: JSON.stringify({
          title: course.title, program_id: course.program_id || null, status: course.status,
        }) }), "Curso creado.").then(() => setCourse({ ...course, title: "" }));
      }}>
        <input style={{ maxWidth: 280 }} placeholder="Nuevo curso" value={course.title} onChange={(e) => setCourse({ ...course, title: e.target.value })} required />
        <select value={course.program_id} onChange={(e) => setCourse({ ...course, program_id: e.target.value })}>
          <option value="">Sin formación</option>
          {programs.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <select value={course.status} onChange={(e) => setCourse({ ...course, status: e.target.value })}>
          {Object.entries(STATUS).filter(([k]) => k !== "archived").map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <button>Crear</button>
      </form>
    </main>
  );
}
