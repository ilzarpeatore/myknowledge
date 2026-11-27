"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { sourceHref, where } from "@/lib/knowledge";
import { useMe } from "@/lib/useMe";

type Mention = { id: string; kind: string; raw: string; segment_id: string; version_id: string; mime: string; page: number | null; slide: number | null; t_ms: number | null; section: string | null; context: string };
type Detail = {
  id: string; key: string; label: string; first_author: string | null; year: number | null; authors_text: string | null; title: string | null; doi: string | null;
  ambiguous: boolean; status: string; notes: string | null; mentions: number; sources: number; in_bibliography: number;
  publication: { id: string; title: string; methodological_quality: string } | null;
  candidates: { id: string; title: string; year: number | null; doi: string | null }[];
  by_source: { source_id: string; source_title: string; kind: string; sections: { key: string; name: string }[]; mentions: Mention[] }[];
};
const KIND: Record<string, string> = { in_text: "cita en el texto", bibliography: "bibliografía", doi: "DOI" };

export default function ReferencePage() {
  const user = useMe();
  const { id } = useParams<{ id: string }>();
  const [d, setD] = useState<Detail | null>(null);
  const [form, setForm] = useState({ title: "", authors_text: "", doi: "", year: "" });
  const [mergeQ, setMergeQ] = useState("");
  const [mergeOpts, setMergeOpts] = useState<{ id: string; label: string; key: string }[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    const r = await api<Detail>(`/api/references/${id}`);
    setD(r);
    setForm({ title: r.title ?? "", authors_text: r.authors_text ?? "", doi: r.doi ?? "", year: r.year ? String(r.year) : "" });
  }, [id]);
  useEffect(() => { if (user) load().catch(() => setMsg({ ok: false, text: "Referencia no encontrada." })); }, [user, load]);
  useEffect(() => {
    if (mergeQ.trim().length < 2) { setMergeOpts([]); return; }
    api<{ items: { id: string; label: string; key: string }[] }>(`/api/references?q=${encodeURIComponent(mergeQ)}&limit=10`).then((r) => setMergeOpts(r.items.filter((x) => x.id !== id))).catch(() => {});
  }, [mergeQ, id]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); setMsg({ ok: true, text: ok }); await load(); }
    catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Error." }); }
  }
  if (!d) return <main><Header user={user} /><p className={msg ? "error" : "muted"}>{msg?.text ?? "Cargando…"}</p></main>;

  return (
    <main>
      <Header user={user} />
      <p className="muted"><Link href="/references">← Referencias</Link></p>
      <h1 style={{ marginBottom: 4 }}>{d.label}</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        {d.authors_text ?? d.first_author}{d.year ? ` · ${d.year}` : ""}{d.doi && <> · <a href={`https://doi.org/${d.doi}`} target="_blank" rel="noopener noreferrer">DOI {d.doi}</a></>}
        {d.status === "dismissed" && " · descartada"}
      </p>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
        <div className="card"><div className="muted">Veces citada</div><strong style={{ fontSize: "1.6rem" }}>{d.mentions}</strong></div>
        <div className="card"><div className="muted">Documentos</div><strong style={{ fontSize: "1.6rem" }}>{d.sources}</strong></div>
        <div className="card"><div className="muted">En bibliografías</div><strong style={{ fontSize: "1.6rem" }}>{d.in_bibliography}</strong></div>
      </div>
      {d.ambiguous && <p className="error">Hay entradas de bibliografía con este autor y año pero títulos distintos: pueden ser dos obras. Revisa las menciones.</p>}

      <h2>En Estudios</h2>
      <div className="card">
        {d.publication ? (
          <p style={{ margin: 0 }}>Vinculada a <Link href={`/studies/${d.publication.id}`}>{d.publication.title}</Link>. <button onClick={() => act(() => api(`/api/references/${id}/publication`, { method: "PUT", body: JSON.stringify({ publication_id: null }) }), "Desvinculada.")}>Desvincular</button></p>
        ) : (<>
          {d.candidates.length > 0 && <p style={{ marginTop: 0 }}>¿Es alguno de estos estudios que ya tienes?</p>}
          {d.candidates.map((c) => (
            <div key={c.id} className="row" style={{ justifyContent: "flex-start", gap: 6 }}>
              <span>{c.title}{c.year ? ` (${c.year})` : ""}</span>
              <button onClick={() => act(() => api(`/api/references/${id}/publication`, { method: "PUT", body: JSON.stringify({ publication_id: c.id }) }), "Vinculada.")}>Sí, es este</button>
            </div>
          ))}
          <button className="primary" style={{ marginTop: 6 }} onClick={() => act(() => api(`/api/references/${id}/publication`, { method: "POST" }), "Añadida a Estudios (calidad «no evaluada»: evalúala tú).")}>
            Añadir a Estudios{d.doi ? " (datos de Crossref)" : ""}
          </button>
        </>)}
      </div>

      <h2>Dónde aparece</h2>
      {d.by_source.map((s) => (
        <div className="card" key={s.source_id} style={{ marginBottom: 8 }}>
          <div className="row"><Link href={`/sources/${s.source_id}`}><strong>{s.source_title}</strong></Link><span className="muted">{s.sections.map((x) => x.name).join(", ")} · {s.mentions.length} vez/veces</span></div>
          {s.mentions.map((m) => (
            <blockquote key={m.id} style={{ margin: "6px 0", paddingLeft: 10, borderLeft: "3px solid var(--border)", fontSize: "0.9rem" }}>
              {highlight(m.context, m.raw)}
              <div className="muted"><span className="badge">{KIND[m.kind]}</span> <a href={sourceHref({ source_id: s.source_id, version_id: m.version_id, mime: m.mime, page: m.page, t_ms: m.t_ms })} target="_blank" rel="noopener">abrir{where(m)}</a></div>
            </blockquote>
          ))}
        </div>
      ))}

      <h2>Corregir</h2>
      <form className="card" onSubmit={(e) => { e.preventDefault(); act(() => api(`/api/references/${id}`, { method: "PATCH", body: JSON.stringify({ title: form.title || null, authors_text: form.authors_text || null, doi: form.doi || null, year: form.year ? Number(form.year) : null }) }), "Guardado."); }}>
        <label>Título<input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
        <label>Autores<input value={form.authors_text} onChange={(e) => setForm({ ...form, authors_text: e.target.value })} /></label>
        <div className="row" style={{ gap: 8 }}>
          <label style={{ flex: 1 }}>DOI<input value={form.doi} onChange={(e) => setForm({ ...form, doi: e.target.value })} /></label>
          <label style={{ width: 120 }}>Año<input value={form.year} inputMode="numeric" onChange={(e) => setForm({ ...form, year: e.target.value })} /></label>
        </div>
        <div className="row" style={{ justifyContent: "flex-start", gap: 6 }}>
          <button className="primary">Guardar</button>
          {d.status === "active"
            ? <button type="button" onClick={() => act(() => api(`/api/references/${id}`, { method: "PATCH", body: JSON.stringify({ status: "dismissed" }) }), "Descartada: no aparecerá en la lista.")}>No es una referencia</button>
            : <button type="button" onClick={() => act(() => api(`/api/references/${id}`, { method: "PATCH", body: JSON.stringify({ status: "active" }) }), "Recuperada.")}>Recuperar</button>}
        </div>
      </form>
      <div className="card" style={{ marginTop: 8 }}>
        <strong>¿Es la misma obra que otra referencia?</strong> <span className="muted">(por ejemplo, citada con otro año)</span>
        <input placeholder="busca la otra referencia" value={mergeQ} onChange={(e) => setMergeQ(e.target.value)} style={{ marginTop: 6 }} />
        {mergeOpts.map((o) => (
          <div key={o.id} className="row" style={{ justifyContent: "flex-start", gap: 6 }}>
            <span>{o.label} <span className="muted">({o.key})</span></span>
            <button onClick={() => act(() => api(`/api/references/${id}/merge`, { method: "POST", body: JSON.stringify({ into_id: o.id }) }), "Unidas.")}>Unir con esta</button>
          </div>
        ))}
      </div>
    </main>
  );
}

function highlight(text: string, raw: string) {
  const i = text.indexOf(raw);
  if (i < 0) return text;
  return <>{text.slice(0, i)}<mark>{raw}</mark>{text.slice(i + raw.length)}</>;
}
