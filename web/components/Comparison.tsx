"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { CLAIM_TYPES, sourceHref } from "@/lib/knowledge";

type Ref = { quote: string; source_id: string; version_id: string; source_title: string; page: number | null; mime: string };
type ClaimRow = { id: string; text: string; claim_type: string; origin: string; context: string | null; ref: Ref | null };
type Perspective = { kind: string; id: string | null; label: string; quality?: string; claims: ClaimRow[] };
type Contra = { id: string; claim_a_id: string; claim_b_id: string; status: string; analysis: string | null };
type Finding = { description: string; claim_ids: string[] };
type Analysis = {
  created_at: string; stale: boolean;
  result: {
    summary: string; agreements: Finding[]; terminology_differences: Finding[]; conceptual_differences: Finding[];
    context_differences: Finding[]; possible_contradictions: { claim_a: string; claim_b: string; explanation: string }[];
    open_questions: string[];
  };
};
type Data = { perspectives: Perspective[]; contradictions: Contra[]; analysis: Analysis | null };
type Job = { id: string; kind: string; status: string; last_error: string | null };

const CONTRA: Record<string, string> = {
  candidate: "por revisar", real: "contradicción real", context_difference: "diferencia de contexto", dismissed: "descartada",
};
const KIND: Record<string, string> = { study: "Estudio", person: "Autor", course: "Curso", source: "Fuente", unknown: "" };
export const QUALITY: Record<string, string> = { not_assessed: "calidad no evaluada", low: "calidad baja", moderate: "calidad moderada", high: "calidad alta" };
const SECTIONS: [keyof Analysis["result"], string][] = [
  ["agreements", "Coincidencias"], ["terminology_differences", "Diferencias de terminología"],
  ["conceptual_differences", "Diferencias conceptuales"], ["context_differences", "Diferencias de contexto"],
];

export function Comparison({ entityId, onChange }: { entityId: string; onChange?: () => void }) {
  const [d, setD] = useState<Data | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    setD(await api<Data>(`/api/entities/${entityId}/comparison`));
    const js = await api<(Job & { kind: string })[]>("/api/jobs?limit=50");
    setJobs(js.filter((j) => j.kind.startsWith("entity.")));
  }, [entityId]);
  useEffect(() => { load(); }, [load]);
  const busy = jobs.some((j) => j.status === "pending" || j.status === "processing");
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => { load(); onChange?.(); }, 3000);
    return () => clearInterval(t);
  }, [busy, load, onChange]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); setMsg({ ok: true, text: ok }); await load(); onChange?.(); }
    catch (err) { setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Error." }); }
  }

  if (!d) return null;
  const all = d.perspectives.flatMap((p) => p.claims.map((c) => ({ ...c, who: p.label })));
  const byId = new Map(all.map((c) => [c.id, c]));
  const cite = (ids: string[]) => ids.map((i) => byId.get(i)).filter(Boolean).map((c) => `${c!.who}: «${c!.text}»`).join(" · ");
  const lastFailed = jobs.find((j) => j.status === "failed");

  return (
    <div>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
      {d.perspectives.length < 2 ? (
        <p className="muted">Hacen falta afirmaciones de al menos dos autores, cursos o fuentes para comparar.</p>
      ) : (
        <div className="grid" style={{ gridTemplateColumns: `repeat(auto-fit, minmax(240px, 1fr))` }}>
          {d.perspectives.map((p) => (
            <div className="card" key={`${p.kind}-${p.id}`}>
              <div className="muted" style={{ fontSize: "0.8rem" }}>{KIND[p.kind]}</div>
              <strong>{p.kind === "study" && p.id ? <a href={`/studies/${p.id}`}>{p.label}</a> : p.label}</strong>
              {p.quality && <div><span className="badge">{QUALITY[p.quality]}</span></div>}
              {p.claims.map((c) => (
                <div key={c.id} style={{ marginTop: 8 }}>
                  <label style={{ margin: 0, display: "flex", gap: 6 }}>
                    <input type="checkbox" style={{ width: "auto" }} checked={picked.includes(c.id)}
                      onChange={(e) => setPicked(e.target.checked ? [...picked, c.id].slice(-2) : picked.filter((x) => x !== c.id))} />
                    <span>{c.text} <span className="badge">{CLAIM_TYPES[c.claim_type] ?? c.claim_type}</span></span>
                  </label>
                  {c.context && <div className="muted" style={{ fontSize: "0.8rem", paddingLeft: 22 }}>Contexto: {c.context}</div>}
                  {c.ref && (
                    <div className="muted" style={{ fontSize: "0.85rem", paddingLeft: 22 }}>
                      «{c.ref.quote}» — <a href={sourceHref(c.ref)} target="_blank" rel="noopener">{c.ref.source_title}{c.ref.page ? `, p. ${c.ref.page}` : ""}</a>
                    </div>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      <div className="row" style={{ justifyContent: "flex-start", marginTop: 10 }}>
        <button disabled={picked.length !== 2} onClick={() => act(() => api("/api/contradictions", {
          method: "POST", body: JSON.stringify({ claim_a_id: picked[0], claim_b_id: picked[1] }),
        }), "Contradicción registrada.").then(() => setPicked([]))}>Marcar las 2 seleccionadas como contradicción</button>
        <button disabled={busy || d.perspectives.length < 2} onClick={() => act(() => api(`/api/entities/${entityId}/comparison/analyze`, { method: "POST" }), "Análisis en cola.")}>Analizar con IA</button>
        <button disabled={busy} onClick={() => act(() => api(`/api/entities/${entityId}/relationships/propose`, { method: "POST" }), "Propuesta de relaciones en cola.")}>Proponer relaciones con IA</button>
        {busy && <span className="muted">Procesando…</span>}
      </div>
      {lastFailed && !busy && <p className="error" style={{ fontSize: "0.85rem" }}>Último análisis con IA fallido: {lastFailed.last_error}</p>}

      {d.contradictions.length > 0 && (<>
        <h3>Contradicciones</h3>
        {d.contradictions.map((c) => (
          <div className="card" key={c.id} style={{ marginBottom: 6 }}>
            <ContraSide c={byId.get(c.claim_a_id)} />
            <div className="muted">frente a</div>
            <ContraSide c={byId.get(c.claim_b_id)} />
            {c.analysis && <p className="muted">{c.analysis}</p>}
            <div className="row" style={{ justifyContent: "flex-start" }}>
              <span className="badge">{CONTRA[c.status]}</span>
              {(["real", "context_difference", "dismissed"] as const).filter((s) => s !== c.status).map((s) => (
                <button key={s} onClick={() => act(() => api(`/api/contradictions/${c.id}`, { method: "PATCH", body: JSON.stringify({ status: s }) }), "Clasificada.")}>{CONTRA[s]}</button>
              ))}
            </div>
          </div>
        ))}
      </>)}

      {d.analysis && (<>
        <h3>Análisis comparativo (IA)</h3>
        <div className="card">
          <p className="muted" style={{ marginTop: 0 }}>
            Inferencia de IA del {new Date(d.analysis.created_at).toLocaleString("es-ES")}: cada punto cita las afirmaciones en las que se basa.
            {d.analysis.stale && <strong className="error"> Hay afirmaciones nuevas desde este análisis.</strong>}
          </p>
          <p>{d.analysis.result.summary}</p>
          {SECTIONS.map(([k, label]) => {
            const items = d.analysis!.result[k] as Finding[];
            return items.length ? (
              <div key={k}><strong>{label}</strong>
                <ul>{items.map((f, i) => <li key={i}>{f.description}<div className="muted" style={{ fontSize: "0.85rem" }}>{cite(f.claim_ids)}</div></li>)}</ul>
              </div>
            ) : null;
          })}
          {d.analysis.result.open_questions.length > 0 && (<div><strong>Preguntas abiertas</strong>
            <ul>{d.analysis.result.open_questions.map((q, i) => <li key={i}>{q}</li>)}</ul></div>)}
        </div>
      </>)}
    </div>
  );
}

type Side = ClaimRow & { who: string };

/** One side of a contradiction: who says it, what, in which context, citing what. */
function ContraSide({ c }: { c: Side | undefined }) {
  if (!c) return <div className="muted">(afirmación rechazada o no disponible; no se ha borrado)</div>;
  return (
    <div>
      <strong>{c.who}</strong>: «{c.text}»
      {c.context && <div className="muted" style={{ fontSize: "0.85rem" }}>Contexto: {c.context}</div>}
      {c.ref && (
        <div className="muted" style={{ fontSize: "0.85rem" }}>
          Cita: «{c.ref.quote}» — <a href={sourceHref(c.ref)} target="_blank" rel="noopener">{c.ref.source_title}{c.ref.page ? `, p. ${c.ref.page}` : ""}</a>
        </div>
      )}
    </div>
  );
}
