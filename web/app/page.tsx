"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Header } from "@/components/Header";
import { Bar, type Course } from "@/lib/learning";
import { api, ApiError } from "@/lib/api";

type Status = { jobs: Record<string, number> };
type Overview = {
  counts: { programs: number; courses: number; sources: number; concepts: number; open_reviews: number };
  in_progress: Course[];
};
type Job = {
  id: string; kind: string; status: string; progress: number; attempts: number;
  max_attempts: number; last_error: string | null; created_at: string;
};

const JOB_STATUS: Record<string, string> = {
  pending: "Pendiente", processing: "En proceso", completed: "Completado",
  failed: "Fallido", cancelled: "Cancelado", awaiting_review: "En revisión",
};
const LABELS: Record<string, string> = {
  pending: "Pendientes", processing: "En proceso", completed: "Completados",
  failed: "Fallidos", cancelled: "Cancelados", awaiting_review: "En revisión",
};

export default function Home() {
  const router = useRouter();
  const [user, setUser] = useState<string | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [ov, setOv] = useState<Overview | null>(null);

  const load = useCallback(async () => {
    try {
      const [me, st, js, o] = await Promise.all([
        api<{ username: string }>("/api/auth/me"),
        api<Status>("/api/status"),
        api<Job[]>("/api/jobs?limit=20"),
        api<Overview>("/api/learning/overview"),
      ]);
      setUser(me.username); setStatus(st); setJobs(js); setOv(o); setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) router.replace("/login");
      else setError("No se pudo cargar el estado del sistema.");
    }
  }, [router]);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <main>
      <Header user={user} />
      <h1>Inicio</h1>
      {error && <p className="error">{error}</p>}

      {ov && (<>
        <div className="grid">
          {([["Fuentes", ov.counts.sources, "/library"], ["Conceptos", ov.counts.concepts, "/concepts"],
             ["Cursos", ov.counts.courses, "/learning"], ["Por revisar", ov.counts.open_reviews, "/review"]] as const).map(([label, n, href]) => (
            <Link key={label} href={href} className="card stat" style={{ textDecoration: "none", color: "inherit" }}>
              <strong>{n}</strong><span className="muted">{label}</span>
            </Link>
          ))}
        </div>
        <h2>Cursos en curso</h2>
        {ov.in_progress.length === 0 ? <p className="muted">Ningún curso marcado «En curso».</p> : (
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
            {ov.in_progress.map((c) => (
              <Link key={c.id} href={`/learning/courses/${c.id}`} className="card" style={{ textDecoration: "none", color: "inherit" }}>
                <strong>{c.title}</strong>
                <div className="muted">{c.progress.completed}/{c.progress.lessons} lecciones</div>
                <Bar percent={c.progress.percent} />
              </Link>
            ))}
          </div>
        )}
      </>)}

      <h2>Cola de procesamiento</h2>
      <div className="grid">
        {Object.entries(LABELS).map(([k, label]) => (
          <div className="card stat" key={k}>
            <strong>{status ? status.jobs[k] ?? 0 : "—"}</strong>
            <span className="muted">{label}</span>
          </div>
        ))}
      </div>

      <h2>Últimos trabajos</h2>
      <div className="card table-wrap">
        {jobs.length === 0 ? (
          <p className="muted">Todavía no hay trabajos. Aparecerán al incorporar fuentes a la biblioteca.</p>
        ) : (
          <table>
            <thead><tr><th>Tipo</th><th>Estado</th><th>Progreso</th><th>Intentos</th><th>Creado</th></tr></thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id} title={j.last_error ?? ""}>
                  <td>{j.kind}</td>
                  <td title={j.last_error ?? ""}>{JOB_STATUS[j.status] ?? j.status}</td>
                  <td>{Math.round(j.progress * 100)} %</td>
                  <td>{j.attempts}/{j.max_attempts}</td>
                  <td>{new Date(j.created_at).toLocaleString("es-ES")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}
