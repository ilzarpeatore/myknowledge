"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Badge, STUDY_STATUS } from "@/lib/analysis";

type C = { id: string; name: string; slug: string };
type Report = {
  claims: number; concepts: { new: C[]; shared: C[] };
  confirmed: { id: string; text: string }[]; in_conflict: { id: string; text: string }[];
  against_studies: Record<string, number>; similarity_available: boolean; references: number;
  links: Record<string, number>;
  analysis: { status: string; result: Record<string, unknown> | null; error: string | null; at: string } | null;
};
type Ref = { id: string; label: string; year: number | null; mentions: number; publication: { id: string } | null };

/** What this source adds to the library, its references, and the cross-analysis. */
export function SourceContribution({ sourceId }: { sourceId: string }) {
  const [r, setR] = useState<Report | null>(null);
  const [refs, setRefs] = useState<Ref[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const load = useCallback(() => {
    api<Report>(`/api/sources/${sourceId}/contribution`).then(setR).catch(() => setR(null));
    api<{ items: Ref[] }>(`/api/sources/${sourceId}/references`).then((x) => setRefs(x.items)).catch(() => setRefs([]));
  }, [sourceId]);
  useEffect(load, [load]);
  async function analyze() {
    try { await api(`/api/sources/${sourceId}/analysis`, { method: "POST" }); setMsg({ ok: true, text: "Análisis en marcha. Las propuestas llegarán a Revisión." }); }
    catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Error." }); }
  }
  if (!r) return <div className="card"><p className="muted" style={{ margin: 0 }}>Cargando…</p></div>;
  const skipped = r.analysis?.result ? Object.values(r.analysis.result).find((v) => v && typeof v === "object" && "skipped" in (v as object)) as { skipped: string } | undefined : undefined;
  return (
    <>
      <div className="card">
        {r.claims === 0 ? <p className="muted" style={{ marginTop: 0 }}>Aún no hay afirmaciones de esta fuente: extrae conocimiento primero.</p> : (
          <ul style={{ margin: "0 0 8px", paddingLeft: 18 }}>
            <li><strong>{r.concepts.new.length}</strong> concepto(s) nuevos que no trata ninguna otra fuente{r.concepts.new.length > 0 && <>: {r.concepts.new.slice(0, 12).map((c, i) => <span key={c.id}>{i ? ", " : ""}<Link href={`/concepts/${c.slug}`}>{c.name}</Link></span>)}</>}</li>
            <li><strong>{r.concepts.shared.length}</strong> concepto(s) que también aparecen en otras fuentes</li>
            <li><strong>{r.confirmed.length}</strong> de {r.claims} afirmaciones coinciden con lo que dicen otras fuentes{!r.similarity_available && " (sin modelo de significado no se puede calcular)"}</li>
            <li><strong>{r.in_conflict.length}</strong> afirmación(es) en posible contradicción con otras fuentes{r.in_conflict.length > 0 && <> (ver <Link href="/review">Revisión</Link>)</>}</li>
            <li>Frente a los estudios: {Object.entries(r.against_studies).filter(([k]) => k !== "study").map(([k, n]) => <span key={k} style={{ marginRight: 4 }}><Badge text={`${STUDY_STATUS[k].label}: ${n}`} color={STUDY_STATUS[k].color} /></span>)}</li>
            <li><strong>{r.references}</strong> referencia(s) bibliográfica(s) citadas · enlaces: {r.links.accepted ?? 0} aceptados, {r.links.proposed ?? 0} propuestos</li>
          </ul>
        )}
        <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}>
          <button onClick={analyze}>Analizar de nuevo con la biblioteca</button>
          <span className="muted" style={{ fontSize: "0.85rem" }}>
            {r.analysis ? `Último análisis: ${new Date(r.analysis.at).toLocaleString("es-ES")} (${({ completed: "hecho", failed: "con error", pending: "en cola", processing: "en marcha" } as Record<string, string>)[r.analysis.status] ?? r.analysis.status})` : "Se analiza sola al terminar de extraer el conocimiento."}
            {skipped && ` · parte con IA no hecha: ${skipped.skipped}`}
          </span>
        </div>
        {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
      </div>
      <h2>Referencias citadas ({refs.length})</h2>
      <div className="card">
        {refs.length === 0 ? <p className="muted" style={{ margin: 0 }}>No se han encontrado citas bibliográficas en esta fuente.</p> :
          refs.map((x) => (
            <div key={x.id} className="row" style={{ justifyContent: "flex-start", gap: 6 }}>
              <Link href={`/references/${x.id}`}>{x.label}</Link>
              <span className="muted" style={{ fontSize: "0.85rem" }}>{x.mentions} vez/veces aquí{x.publication ? " · en Estudios" : ""}</span>
            </div>
          ))}
      </div>
    </>
  );
}
