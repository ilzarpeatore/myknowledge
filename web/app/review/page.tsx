"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Task = { id: string; kind: string; payload: Record<string, string | number>; created_at: string };

export default function ReviewPage() {
  const user = useMe();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const load = useCallback(() => api<Task[]>("/api/review-tasks").then(setTasks), []);
  useEffect(() => { if (user) load(); }, [user, load]);

  async function resolve(t: Task, action: string, keep?: string) {
    try {
      await api(`/api/review-tasks/${t.id}/resolve`, { method: "POST", body: JSON.stringify({ action, keep_entity_id: keep }) });
      setMsg({ ok: true, text: "Hecho." }); load();
    } catch (err) { setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Error." }); }
  }
  async function scan() {
    const r = await api<{ created: number }>("/api/review-tasks/scan-duplicates", { method: "POST" });
    setMsg({ ok: true, text: r.created ? `${r.created} posibles duplicados nuevos.` : "No hay posibles duplicados nuevos." });
    load();
  }

  return (
    <main>
      <Header user={user} />
      <div className="row"><h1>Revisión</h1><button onClick={scan}>Buscar posibles duplicados</button></div>
      <p className="muted">Nada se fusiona ni se da por verificado sin tu decisión. Las parejas que rechaces no se vuelven a proponer.</p>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
      {tasks.length === 0 && <p className="muted">No hay nada pendiente de revisar.</p>}
      {tasks.map((t) => (
        <div className="card" key={t.id} style={{ marginBottom: 8 }}>
          {t.kind === "entity_merge" ? (<>
            <strong>¿Son el mismo concepto?</strong> <span className="muted">(similitud {Math.round(Number(t.payload.score) * 100)} %)</span>
            <p>
              <Link href={`/concepts/${t.payload.entity_a}`} target="_blank">{t.payload.name_a}</Link>
              {" · "}
              <Link href={`/concepts/${t.payload.entity_b}`} target="_blank">{t.payload.name_b}</Link>
            </p>
            <div className="row" style={{ justifyContent: "flex-start" }}>
              <button onClick={() => resolve(t, "accept", String(t.payload.entity_a))}>Sí, conservar «{t.payload.name_a}»</button>
              <button onClick={() => resolve(t, "accept", String(t.payload.entity_b))}>Sí, conservar «{t.payload.name_b}»</button>
              <button onClick={() => resolve(t, "reject")}>No, son distintos</button>
            </div>
          </>) : t.kind === "relationship" ? (<>
            <strong>Relación propuesta</strong>
            <p className="muted">Ábrela desde la ficha del concepto para ver su evidencia.</p>
            <div className="row" style={{ justifyContent: "flex-start" }}>
              <button onClick={() => resolve(t, "accept")}>Verificar</button>
              <button onClick={() => resolve(t, "reject")}>Rechazar</button>
            </div>
          </>) : (<>
            <strong>{t.kind}</strong> <button onClick={() => resolve(t, "dismiss")}>Descartar</button>
          </>)}
        </div>
      ))}
    </main>
  );
}
