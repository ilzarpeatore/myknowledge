"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { LEVELS, money, type ChapterDetail, type Cite, type Level, type Summary } from "@/lib/chapters";
import { sourceHref } from "@/lib/knowledge";
import { useMe } from "@/lib/useMe";

function Pages({ cites, href }: { cites: Cite[]; href: (page: number | null) => string }) {
  if (!cites.length) return <span className="error" style={{ fontSize: "0.75rem" }} title="La IA no dio una cita que se encontrara literalmente en el texto: compruébalo."> sin cita</span>;
  const pages = [...new Set(cites.map((c) => c.page).filter((p): p is number => p != null))].sort((a, b) => a - b);
  return (
    <span style={{ fontSize: "0.8rem" }}>
      {" "}{pages.map((p, i) => (
        <span key={p}>{i ? ", " : ""}<a href={href(p)} target="_blank" rel="noreferrer"
          title={cites.filter((c) => c.page === p).map((c) => `«${c.quote}»`).join("\n")}>p. {p}</a></span>
      ))}
    </span>
  );
}

function SummaryView({ s, href }: { s: Summary; href: (page: number | null) => string }) {
  const c = s.content;
  return (
    <div>
      {c.overview && <p style={{ marginTop: 0 }}><em>{c.overview}</em></p>}
      {c.sections.map((sec, i) => (
        <div key={i}>
          {sec.heading && <h3 style={{ marginBottom: 4 }}>{sec.heading}</h3>}
          <ul style={{ marginTop: 0 }}>
            {sec.points.map((p, j) => <li key={j} style={{ marginBottom: 4 }}>{p.text}<Pages cites={p.citations} href={href} /></li>)}
          </ul>
        </div>
      ))}
      {c.key_terms.length > 0 && (
        <>
          <h3 style={{ marginBottom: 4 }}>Términos clave</h3>
          <ul style={{ marginTop: 0 }}>
            {c.key_terms.map((t, i) => <li key={i}><strong>{t.term}</strong>: {t.definition}{t.citations.length > 0 && <Pages cites={t.citations} href={href} />}</li>)}
          </ul>
        </>
      )}
    </div>
  );
}

export default function ChapterPage() {
  const user = useMe();
  const { id, chapterId } = useParams<{ id: string; chapterId: string }>();
  const [d, setD] = useState<ChapterDetail | null>(null);
  const [level, setLevel] = useState<Level>("study");
  const [old, setOld] = useState<Summary | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(() => api<ChapterDetail>(`/api/chapters/${chapterId}`).then(setD)
    .catch((e) => setMsg({ ok: false, text: e instanceof ApiError ? e.message : "No se pudo cargar." })), [chapterId]);
  useEffect(() => { if (user) load(); }, [user, load]);
  const busy = d ? Object.keys(d.generating).length > 0 : false;
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [busy, load]);
  useEffect(() => { setOld(null); }, [level, chapterId]);

  if (!d) return <main><Header user={user} /><p className={msg ? "error" : "muted"}>{msg?.text ?? "Cargando…"}</p></main>;
  const href = (page: number | null) => sourceHref({ source_id: d.source.id, version_id: d.version.id, mime: d.version.mime, page });
  const cur = d.current[level];
  const shown = old ?? cur;
  const est = d.estimates[level];
  const generating = Boolean(d.generating[level]);
  const failed = d.failed[level];
  const past = d.history.filter((h) => h.level === level);

  async function generate() {
    try {
      await api(`/api/chapters/${chapterId}/summaries`, { method: "POST", body: JSON.stringify({ level }) });
      setMsg({ ok: true, text: "Generando el resumen; suele tardar menos de un minuto." });
      await load();
    } catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Error." }); }
  }
  async function showOld(summaryId: string) {
    if (!summaryId) { setOld(null); return; }
    setOld(await api<Summary>(`/api/chapters/${chapterId}/summaries/${summaryId}`));
  }

  return (
    <main>
      <Header user={user} />
      <p className="muted"><Link href={`/sources/${id}`}>← {d.source.title}</Link></p>
      <h1 style={{ marginBottom: 4 }}>{d.title}</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        Páginas {d.page_start}–{d.page_end} ({d.pages}) · <a href={href(d.page_start)} target="_blank" rel="noreferrer">abrir en el PDF</a>
      </p>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span>{d.prev && <Link href={`/sources/${id}/chapters/${d.prev.id}`}>← {d.prev.title}</Link>}</span>
        <span>{d.next && <Link href={`/sources/${id}/chapters/${d.next.id}`}>{d.next.title} →</Link>}</span>
      </div>
      {d.points.length > 0 && (
        <p className="muted">Apartados: {d.points.map((p, i) => <span key={p.id}>{i ? " · " : ""}<Link href={`/sources/${id}/chapters/${p.id}`}>{p.title}</Link></span>)}</p>
      )}

      <h2>Resumen</h2>
      <div className="row" style={{ justifyContent: "flex-start", gap: 6 }} role="tablist">
        {LEVELS.map((l) => (
          <button key={l.key} role="tab" aria-selected={level === l.key} className={level === l.key ? "primary" : ""} title={l.tip} onClick={() => setLevel(l.key)}>
            {l.name}{d.current[l.key] ? " ✓" : ""}
          </button>
        ))}
      </div>
      <p className="muted" style={{ fontSize: "0.85rem" }}>{LEVELS.find((l) => l.key === level)?.tip} Unas {est.target_words} palabras para este capítulo.</p>

      <div className="card">
        {generating ? <p className="muted" style={{ margin: 0 }}>Generando el resumen…</p> : shown ? (
          <>
            <SummaryView s={shown} href={href} />
            <p className="muted" style={{ fontSize: "0.8rem", marginBottom: 0 }}>
              {shown.words} palabras · {shown.citations} citas comprobadas en el texto
              {shown.uncited_points > 0 && <> · <span className="error">{shown.uncited_points} punto(s) sin cita: compruébalos</span></>}
              {" · "}{new Date(shown.created_at).toLocaleString("es-ES")}{shown.origin === "mcp" ? " · pedido por un agente (MCP)" : ""}
              {" · "}<button style={{ fontSize: "0.75rem", padding: "0 6px" }} onClick={() => navigator.clipboard.writeText(shown.markdown).then(() => setMsg({ ok: true, text: "Copiado en Markdown." }))}>copiar en Markdown</button>
            </p>
          </>
        ) : <p className="muted" style={{ margin: 0 }}>Aún no hay resumen de este nivel.</p>}
        {failed && !generating && <p className="error">La última generación falló: {failed}</p>}
      </div>

      <div className="row" style={{ justifyContent: "flex-start", gap: 8, marginTop: 10 }}>
        <button className={cur ? "" : "primary"} disabled={generating || est.too_long || est.tokens === 0} onClick={generate}>
          {cur ? "Volver a generar" : "Generar resumen"}
        </button>
        <span className="muted" style={{ fontSize: "0.85rem" }}>
          {est.too_long ? "Demasiado largo: divídelo en capítulos más pequeños." : est.tokens === 0 ? "No hay texto en esas páginas." : `${money(est.usd)} con ${est.model ?? "la IA"}${est.by_parts ? " · capítulo largo: se resume por apartados y cada apartado queda resumido también" : ""}`}
        </span>
        {past.length > 1 && (
          <select onChange={(e) => showOld(e.target.value)} defaultValue="" style={{ maxWidth: 260 }}>
            <option value="">Versión actual</option>
            {past.slice(1).map((h) => <option key={h.id} value={h.id}>Anterior: {new Date(h.created_at).toLocaleString("es-ES")} ({h.words} pal.)</option>)}
          </select>
        )}
      </div>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
    </main>
  );
}
