"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { CardT } from "@/lib/study";

/** Flashcards of a concept: generate them from its reviewed claims (free) or with AI
 *  (proposals to accept), and see how each one is going. */
export function ConceptCards({ entityId }: { entityId: string }) {
  const [items, setItems] = useState<CardT[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const load = useCallback(() => api<CardT[]>(`/api/flashcards?entity_id=${entityId}`).then(setItems).catch(() => {}), [entityId]);
  useEffect(() => { load(); }, [load]);

  async function generate(ai: boolean) {
    try {
      const r = await api<{ created: number; job_id: string | null }>(`/api/entities/${entityId}/flashcards/generate`, { method: "POST", body: JSON.stringify({ ai }) });
      setMsg({ ok: true, text: `${r.created} tarjeta(s) nuevas de las afirmaciones revisadas.${ai ? " La IA está preparando propuestas: aparecerán en Estudiar para aceptarlas." : ""}` });
      load();
    } catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Error." }); }
  }
  async function setStatus(id: string, status: string) {
    await api(`/api/flashcards/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }).catch(() => {});
    load();
  }

  const active = items.filter((c) => c.status === "active").length;
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}>
        <button onClick={() => generate(false)} title="Una tarjeta por afirmación aceptada; sin IA">Crear tarjetas de las afirmaciones</button>
        <button onClick={() => generate(true)} title="La IA propone preguntas con cita de la fuente">Proponer más con IA</button>
        <Link href="/study">Ir a estudiar</Link>
      </div>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
      {items.length === 0 ? <p className="muted">Aún no hay tarjetas de este concepto.</p> : (
        <>
          <p className="muted">{active} activa(s){items.length > active ? `, ${items.length - active} propuesta(s) o en pausa` : ""}.</p>
          <table><tbody>
            {items.map((c) => (
              <tr key={c.id}>
                <td>{c.front}<div className="muted" style={{ fontSize: "0.85rem" }}>{c.back}</div></td>
                <td className="muted" style={{ fontSize: "0.85rem" }}>
                  {c.status === "proposed" ? "propuesta IA" : c.status === "suspended" ? "en pausa" : c.reps ? `${c.reps} repaso(s)${c.retrievability != null ? ` · recuerdo ${Math.round(c.retrievability * 100)} %` : ""}` : "nueva"}
                </td>
                <td>
                  {c.status === "proposed" && <button onClick={() => setStatus(c.id, "active")}>Aceptar</button>}
                  {c.status === "active" && <button onClick={() => setStatus(c.id, "suspended")}>Pausar</button>}
                  {c.status === "suspended" && <button onClick={() => setStatus(c.id, "active")}>Reanudar</button>}
                  <button onClick={() => setStatus(c.id, "archived")}>Archivar</button>
                </td>
              </tr>
            ))}
          </tbody></table>
        </>
      )}
    </div>
  );
}
