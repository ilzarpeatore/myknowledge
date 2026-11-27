"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { sourceHref, where } from "@/lib/knowledge";

type Citation = {
  segment_id: string; quote: string; source_id: string; source_title: string; version_id: string;
  mime: string; page?: number | null; slide?: number | null; t_ms?: number | null; label?: string;
};
type Def = {
  id: string; short: string; extended: string | null; origin: "user" | "ai" | "mcp" | "imported";
  status: string; basis: "library" | "partial" | "general" | null; citations: Citation[];
  note: string | null; edited: boolean; created_at: string; decided_at: string | null;
};
type FromSource = {
  claim_id: string; text: string; review_status: string; person: string | null;
  provenance: { kind: string; label: string; person?: string; publication?: { title: string; year: number | null } };
  evidence: (Citation & { page: number | null; section: string | null })[];
};
type Overview = {
  current: Def | null; proposals: Def[]; history: Def[]; from_sources: FromSource[];
  generating: string | null; failed: string | null;
};

const ORIGIN: Record<Def["origin"], string> = {
  user: "escrita por ti", ai: "propuesta por la IA", mcp: "propuesta por Claude (MCP)", imported: "guardada antes",
};
const BASIS: Record<string, { text: string; cls: string; tip: string }> = {
  library: { text: "basada en tus fuentes", cls: "ok", tip: "Cada cita se ha encontrado literalmente en tus documentos." },
  partial: { text: "fuentes + conocimiento general", cls: "", tip: "Parte sale de tus fuentes y parte es conocimiento general de la IA." },
  general: { text: "conocimiento general (sin respaldo en tu biblioteca)", cls: "error", tip: "Tus fuentes no la respaldan: compruébala antes de aceptarla." },
};

function Citations({ items }: { items: Citation[] }) {
  if (!items.length) return null;
  return (
    <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: "0.85rem" }}>
      {items.map((c) => (
        <li key={c.segment_id}>
          «{c.quote}» — <a href={sourceHref({ ...c, page: c.page ?? null })} target="_blank" rel="noreferrer">{c.source_title}{where({ page: c.page ?? null, slide: c.slide ?? null, t_ms: c.t_ms ?? null }, false)}</a>
        </li>
      ))}
    </ul>
  );
}

function Who({ d }: { d: Def }) {
  return (
    <span className="muted" style={{ fontSize: "0.85rem" }}>
      {ORIGIN[d.origin]}{d.edited ? " · editada por ti" : ""}
      {d.basis && d.origin !== "user" && <> · <span className={BASIS[d.basis].cls} title={BASIS[d.basis].tip}>{BASIS[d.basis].text}</span></>}
    </span>
  );
}

function Editor({ initial, onSave, onCancel, saveLabel }: {
  initial: { short: string; extended: string }; saveLabel: string;
  onSave: (short: string, extended: string) => Promise<void>; onCancel: () => void;
}) {
  const [short, setShort] = useState(initial.short);
  const [extended, setExtended] = useState(initial.extended);
  return (
    <div className="card" style={{ marginTop: 8 }}>
      <label>Definición (una o dos frases: qué es)
        <textarea rows={3} value={short} maxLength={1200} onChange={(e) => setShort(e.target.value)} placeholder="Ej.: Número de repeticiones que quedan antes del fallo muscular al terminar una serie." />
      </label>
      <label>Explicación ampliada (opcional: cómo funciona, tipos, cuándo se aplica, en qué se diferencia de otros conceptos)
        <textarea rows={5} value={extended} maxLength={6000} onChange={(e) => setExtended(e.target.value)} />
      </label>
      <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}>
        <button disabled={!short.trim()} onClick={() => onSave(short, extended)}>{saveLabel}</button>
        <button onClick={onCancel}>Cancelar</button>
      </div>
    </div>
  );
}

/** The concept's definition: write it, generate a proposal with AI grounded in the
 *  library, accept/edit/discard proposals, see how the sources define it and the
 *  history. */
export function Definition({ entityId, onChanged }: { entityId: string; onChanged?: () => void }) {
  const [o, setO] = useState<Overview | null>(null);
  const [editing, setEditing] = useState<{ short: string; extended: string; proposal?: string } | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [showLong, setShowLong] = useState(false);
  const load = useCallback(() => api<Overview>(`/api/entities/${entityId}/definitions`).then(setO).catch(() => {}), [entityId]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {  // while the AI writes, check every few seconds
    if (!o?.generating) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [o?.generating, load]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); setMsg({ ok: true, text: ok }); setEditing(null); await load(); onChanged?.(); }
    catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Error." }); }
  }
  const post = (path: string, body?: object) => api(`/api/entities/${entityId}/${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined });

  if (!o) return null;
  const cur = o.current;
  return (
    <section className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ alignItems: "baseline" }}>
        <h2 style={{ margin: 0 }}>Definición</h2>
        <div className="row" style={{ gap: 8 }}>
          <button onClick={() => setEditing({ short: cur?.short ?? "", extended: cur?.extended ?? "" })}>{cur ? "Editar" : "Escribirla yo"}</button>
          <button disabled={!!o.generating} title="La IA la redacta a partir de tus fuentes, con citas comprobadas; queda como propuesta"
            onClick={() => act(() => post("definitions/generate"), "La IA está redactando una propuesta: aparecerá aquí en unos segundos.")}>
            {o.generating ? "Generando…" : "Generar con IA"}
          </button>
        </div>
      </div>

      {cur ? (
        <div style={{ marginTop: 8 }}>
          <p style={{ fontSize: "1.1rem", margin: "4px 0" }}>{cur.short}</p>
          {cur.extended && (showLong
            ? <><p style={{ whiteSpace: "pre-wrap" }}>{cur.extended}</p><button className="link" onClick={() => setShowLong(false)}>Ocultar explicación</button></>
            : <button className="link" onClick={() => setShowLong(true)}>Ver explicación ampliada</button>)}
          <div><Who d={cur} /></div>
          <Citations items={cur.citations} />
        </div>
      ) : !editing && (
        <p className="muted">Este concepto aún no tiene definición. Escríbela tú, usa una de tus fuentes como punto de partida o pide una propuesta a la IA.</p>
      )}
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
      {o.failed && !o.generating && <p className="error">La última generación falló: {o.failed}</p>}

      {editing && (
        <Editor initial={editing} saveLabel={editing.proposal ? "Aceptar con estos cambios" : "Guardar definición"}
          onCancel={() => setEditing(null)}
          onSave={(short, extended) => editing.proposal
            ? act(() => post(`definitions/${editing.proposal}/accept`, { short, extended }), "Propuesta aceptada con tus cambios.")
            : act(() => api(`/api/entities/${entityId}/definition`, { method: "PUT", body: JSON.stringify({ short, extended: extended || null }) }), "Definición guardada.")} />
      )}

      {o.proposals.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <strong>Propuestas por revisar</strong>
          {o.proposals.map((p) => (
            <div key={p.id} className="card" style={{ marginTop: 6 }}>
              <p style={{ margin: "0 0 4px" }}>{p.short}</p>
              {p.extended && <details><summary className="muted">Explicación ampliada</summary><p style={{ whiteSpace: "pre-wrap" }}>{p.extended}</p></details>}
              <Who d={p} />
              {p.note && <p className="muted" style={{ fontSize: "0.85rem", margin: "4px 0" }}>Nota: {p.note}</p>}
              <Citations items={p.citations} />
              <div className="row" style={{ justifyContent: "flex-start", gap: 8, marginTop: 6 }}>
                <button onClick={() => act(() => post(`definitions/${p.id}/accept`, {}), "Propuesta aceptada: es la nueva definición.")}>Aceptar</button>
                <button onClick={() => setEditing({ short: p.short, extended: p.extended ?? "", proposal: p.id })}>Editar y aceptar</button>
                <button onClick={() => act(() => post(`definitions/${p.id}/reject`), "Propuesta descartada.")}>Descartar</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {o.from_sources.length > 0 && (
        <details style={{ marginTop: 12 }} open={!cur}>
          <summary><strong>Cómo lo definen tus fuentes</strong> <span className="muted">({o.from_sources.length})</span></summary>
          {o.from_sources.map((s) => (
            <div key={s.claim_id} style={{ margin: "8px 0" }}>
              <div>{s.text} {s.review_status === "pending" && <span className="badge">sin revisar</span>}</div>
              <div className="muted" style={{ fontSize: "0.85rem" }}>
                {s.provenance.label}{s.provenance.publication ? `: ${s.provenance.publication.title}${s.provenance.publication.year ? ` (${s.provenance.publication.year})` : ""}` : s.provenance.person ? `: ${s.provenance.person}` : ""}
                {s.evidence.slice(0, 1).map((ev) => (
                  <span key={ev.segment_id}> · <a href={sourceHref(ev)} target="_blank" rel="noreferrer">{ev.source_title}{where(ev, false)}</a></span>
                ))}
                {" · "}<button className="link" onClick={() => setEditing({ short: s.text, extended: cur?.extended ?? "" })}>Usar como punto de partida</button>
              </div>
            </div>
          ))}
        </details>
      )}

      {(o.history.length > 0 || cur) && (
        <div className="row" style={{ justifyContent: "flex-start", gap: 8, marginTop: 8 }}>
          {o.history.length > 0 && (
            <details style={{ flex: 1 }}>
              <summary className="muted">Definiciones anteriores ({o.history.length})</summary>
              {o.history.map((h) => (
                <div key={h.id} style={{ margin: "6px 0" }}>
                  <div>{h.short}</div>
                  <span className="muted" style={{ fontSize: "0.85rem" }}>{ORIGIN[h.origin]} · sustituida el {h.decided_at ? new Date(h.decided_at).toLocaleDateString("es-ES") : "?"} · </span>
                  <button className="link" onClick={() => act(() => post(`definitions/${h.id}/accept`, {}), "Definición anterior recuperada.")}>Recuperar</button>
                </div>
              ))}
            </details>
          )}
          {cur && <button className="link" onClick={() => { if (confirm("¿Quitar la definición? Quedará en «Definiciones anteriores».")) act(() => api(`/api/entities/${entityId}/definition`, { method: "DELETE" }), "Definición quitada (puedes recuperarla)."); }}>Quitar definición</button>}
        </div>
      )}
    </section>
  );
}
