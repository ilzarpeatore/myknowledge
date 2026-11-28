"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { money } from "@/lib/chapters";
import { sourceHref } from "@/lib/knowledge";
import { useMe } from "@/lib/useMe";

type Cite = { fragment_id: string; page: number | null; quote: string; source_id: string; source_title: string; version_id: string; mime: string; own: boolean };
type Point = { text: string; citations: Cite[] };
type Reading = {
  id: string; markdown: string; words: number; citations: number; created_at: string;
  content: {
    objectives: string[]; library_sources?: string[];
    sections: { heading: string; material: Point[]; library: Point[]; explanation: string | null }[];
    checks: { question: string; answer: string; citations: Cite[] }[];
  };
};
type Feedback = { score: number; understood: string[]; missing: string[]; misconceptions: string[]; next_step: string | null };
type Study = {
  id: string; title: string;
  course: { id: string; title: string }; module: { id: string; title: string };
  parts: { kind: string; source: { id: string; title: string }; version: { id: string; mime: string }; page_start: number | null; page_end: number | null }[];
  prev: { id: string; title: string } | null; next: { id: string; title: string } | null;
  reading: Reading | null;
  estimate: { tokens: number; target_words: number; model: string | null; usd: number | null; too_long: boolean };
  generating: string | null; failed: string | null;
  progress: { status: string; understanding: number | null };
  explanations: { id: string; text: string; status: string; score: number | null; feedback: Feedback | null; created_at: string }[];
};

const LAYER = {
  library: { borderLeft: "3px solid #2563eb", paddingLeft: 10, margin: "8px 0" },
  ai: { borderLeft: "3px solid #9ca3af", paddingLeft: 10, margin: "8px 0", fontStyle: "italic" as const },
};

function Where({ cites }: { cites: Cite[] }) {
  if (!cites.length) return <span className="error" style={{ fontSize: "0.75rem" }} title="Sin cita encontrada literalmente en el material: compruébalo."> sin cita</span>;
  const seen = new Set<string>();
  return (
    <span style={{ fontSize: "0.8rem" }}>
      {" "}{cites.map((c) => {
        const label = (c.own ? "" : `${c.source_title}${c.page ? ", " : ""}`) + (c.page ? `p. ${c.page}` : "");
        if (!label || seen.has(label)) return null;
        seen.add(label);
        return (
          <a key={label} style={{ marginRight: 6 }} href={sourceHref({ source_id: c.source_id, version_id: c.version_id, mime: c.mime, page: c.page })}
            target="_blank" rel="noreferrer" title={`«${c.quote}»`}>{label}</a>
        );
      })}
    </span>
  );
}

export default function LessonPage() {
  const user = useMe();
  const { id } = useParams<{ id: string }>();
  const [d, setD] = useState<Study | null>(null);
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(() => api<Study>(`/api/lessons/${id}/study`).then(setD)
    .catch((e) => setMsg({ ok: false, text: e instanceof ApiError ? e.message : "No se pudo cargar." })), [id]);
  useEffect(() => { if (user) load(); }, [user, load]);
  const busy = d ? Boolean(d.generating) || d.explanations.some((e) => e.status === "pending") : false;
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [busy, load]);

  if (!d) return <main><Header user={user} /><p className={msg ? "error" : "muted"}>{msg?.text ?? "Cargando…"}</p></main>;
  const r = d.reading;

  async function act(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); setMsg({ ok: true, text: ok }); await load(); }
    catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Error." }); }
  }

  return (
    <main>
      <Header user={user} />
      <p className="muted"><Link href={`/learning/courses/${d.course.id}`}>← {d.course.title}</Link> · {d.module.title}</p>
      <h1 style={{ marginBottom: 4 }}>{d.title}</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        {d.parts.map((p, i) => (
          <span key={i}>{i ? " · " : ""}<a href={sourceHref({ source_id: p.source.id, version_id: p.version.id, mime: p.version.mime, page: p.page_start })} target="_blank" rel="noreferrer">
            {p.source.title}{p.page_start ? `, p. ${p.page_start}${p.page_end && p.page_end !== p.page_start ? `–${p.page_end}` : ""}` : ""}</a></span>
        ))}
        {d.progress.understanding != null && <> · comprensión {d.progress.understanding}/100{d.progress.status === "completed" ? " ✓" : ""}</>}
      </p>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span>{d.prev && <Link href={`/learning/lessons/${d.prev.id}`}>← {d.prev.title}</Link>}</span>
        <span>{d.next && <Link href={`/learning/lessons/${d.next.id}`}>{d.next.title} →</Link>}</span>
      </div>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}

      {!r ? (
        <div className="card" style={{ marginTop: 12 }}>
          {d.generating ? <p className="muted" style={{ margin: 0 }}>Preparando la lectura… (suele tardar un minuto; si el sistema está procesando un libro, espera su turno)</p> : (
            <>
              <p style={{ marginTop: 0 }}>Esta lección aún no tiene lectura. Se prepara a partir de su material, ampliada con lo que dicen tus otras fuentes y una explicación de la IA, cada cosa marcada.</p>
              <button className="primary" disabled={d.estimate.tokens === 0 || d.estimate.too_long}
                onClick={() => act(() => api(`/api/lessons/${id}/reading`, { method: "POST" }), "Preparando la lectura…")}>Preparar la lectura</button>{" "}
              <span className="muted" style={{ fontSize: "0.85rem" }}>{d.estimate.tokens === 0 ? "No hay texto en este material." : `${money(d.estimate.usd)} · unas ${d.estimate.target_words} palabras`}</span>
              {d.failed && <p className="error">La última vez falló: {d.failed}</p>}
            </>
          )}
        </div>
      ) : (
        <>
          <p className="muted" style={{ fontSize: "0.8rem" }}>
            <strong>Cómo leerlo:</strong> el texto normal es lo que dice el material (con su página) ·{" "}
            <span style={{ borderLeft: "3px solid #2563eb", paddingLeft: 4 }}>en azul, lo que añaden tus otras fuentes</span> ·{" "}
            <span style={{ borderLeft: "3px solid #9ca3af", paddingLeft: 4, fontStyle: "italic" }}>en gris, la explicación de la IA (conocimiento general)</span>
          </p>
          {r.content.objectives.length > 0 && (
            <div className="card">
              <strong>Al terminar deberías entender</strong>
              <ul style={{ marginBottom: 0 }}>{r.content.objectives.map((o, i) => <li key={i}>{o}</li>)}</ul>
            </div>
          )}
          {r.content.sections.map((s, i) => (
            <section key={i}>
              <h2>{s.heading}</h2>
              {s.material.length > 0 && <ul>{s.material.map((p, j) => <li key={j}>{p.text}<Where cites={p.citations} /></li>)}</ul>}
              {s.library.length > 0 && (
                <div style={LAYER.library}>
                  {s.library.map((p, j) => <p key={j} style={{ margin: "4px 0" }}>{p.text}<Where cites={p.citations} /></p>)}
                </div>
              )}
              {s.explanation && <div style={LAYER.ai}><p style={{ margin: "4px 0" }}>{s.explanation}</p></div>}
            </section>
          ))}

          {r.content.checks.length > 0 && (
            <>
              <h2>¿Lo has entendido?</h2>
              {r.content.checks.map((c, i) => (
                <details key={i} className="card" style={{ marginBottom: 8 }}>
                  <summary><strong>{c.question}</strong> <span className="muted">(piénsalo y pulsa para ver la respuesta)</span></summary>
                  <p style={{ marginBottom: 0 }}>{c.answer}<Where cites={c.citations} /></p>
                </details>
              ))}
            </>
          )}

          <h2>Explícalo con tus palabras</h2>
          <form className="card" onSubmit={(e) => {
            e.preventDefault();
            act(() => api(`/api/lessons/${id}/explanations`, { method: "POST", body: JSON.stringify({ text }) }), "Corrigiendo tu explicación…").then(() => setText(""));
          }}>
            <p className="muted" style={{ marginTop: 0 }}>Escribe, sin mirar, lo que has entendido: las ideas clave y cómo se relacionan. La IA te dice qué está bien, qué falta y qué está confundido. Con 70 o más, la lección queda como entendida.</p>
            <textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} />
            <button className="primary" disabled={text.trim().length < 30}>Corregir</button>
          </form>
          {d.explanations.map((e) => (
            <div key={e.id} className="card" style={{ marginTop: 8 }}>
              <p className="muted" style={{ marginTop: 0, fontSize: "0.8rem" }}>{new Date(e.created_at).toLocaleString("es-ES")}{e.score != null ? ` · ${e.score}/100` : ""}</p>
              <p style={{ whiteSpace: "pre-wrap" }}>{e.text}</p>
              {e.status === "pending" && <p className="muted">Corrigiendo…</p>}
              {e.status === "failed" && <p className="error">No se pudo corregir; vuelve a enviarla.</p>}
              {e.feedback && (
                <>
                  {e.feedback.understood.length > 0 && <><strong className="ok">Bien explicado</strong><ul>{e.feedback.understood.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
                  {e.feedback.missing.length > 0 && <><strong>Te falta</strong><ul>{e.feedback.missing.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
                  {e.feedback.misconceptions.length > 0 && <><strong className="error">A corregir</strong><ul>{e.feedback.misconceptions.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
                  {e.feedback.next_step && <p><strong>Siguiente paso:</strong> {e.feedback.next_step}</p>}
                </>
              )}
            </div>
          ))}

          <p className="muted" style={{ fontSize: "0.8rem", marginTop: 16 }}>
            Lectura de {r.words} palabras, {r.citations} citas comprobadas{r.content.library_sources?.length ? ` · fuentes de tu biblioteca: ${r.content.library_sources.join(", ")}` : ""} · {new Date(r.created_at).toLocaleString("es-ES")}{" "}
            <button style={{ fontSize: "0.75rem", padding: "0 6px" }} disabled={Boolean(d.generating)}
              onClick={() => { if (confirm("¿Preparar de nuevo la lectura? La actual se guarda.")) act(() => api(`/api/lessons/${id}/reading`, { method: "POST" }), "Preparando de nuevo…"); }}>rehacer</button>{" "}
            <button style={{ fontSize: "0.75rem", padding: "0 6px" }} onClick={() => navigator.clipboard.writeText(r.markdown).then(() => setMsg({ ok: true, text: "Copiado en Markdown." }))}>copiar en Markdown</button>
          </p>
        </>
      )}
    </main>
  );
}
