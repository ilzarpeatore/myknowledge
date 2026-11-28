"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { LEVELS, ORIGIN, PROGRESS, type Chapter, type Listing } from "@/lib/chapters";

/** Chapters of a book: detected from its bookmarks (or added by hand), each with its
 *  pages and a link to read and summarise it. Editing protects the list from a new
 *  automatic detection. */
export function Chapters({ sourceId }: { sourceId: string }) {
  const [list, setList] = useState<Listing | null>(null);
  const [editing, setEditing] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [add, setAdd] = useState({ title: "", page_start: "", page_end: "", parent_id: "" });

  const load = useCallback(() => api<Listing>(`/api/sources/${sourceId}/chapters`).then(setList).catch(() => {}), [sourceId]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!list?.detecting) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [list?.detecting, load]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); setMsg({ ok: true, text: ok }); await load(); }
    catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Error." }); }
  }
  const detect = (force: boolean) => act(() => api(`/api/sources/${sourceId}/chapters/detect${force ? "?force=true" : ""}`, { method: "POST" }), "Buscando los capítulos…");
  const patch = (c: Chapter, body: Partial<Chapter>) => act(() => api(`/api/chapters/${c.id}`, { method: "PATCH", body: JSON.stringify(body) }), "Guardado.");

  if (!list) return <div className="card"><p className="muted" style={{ margin: 0 }}>Cargando…</p></div>;
  const items = list.items;
  const edited = items.some((c) => c.edited);
  const tops = items.filter((c) => c.parent_id === null);
  const content = tops.filter((c) => c.is_content);
  const done = content.filter((c) => c.progress === "read" || c.progress === "studied").length;

  return (
    <div className="card">
      {content.length > 0 && (
        <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
          {done} de {content.length} capítulos leídos · {content.filter((c) => c.progress === "studied").length} estudiados
        </p>
      )}
      {items.length === 0 ? (
        <p className="muted" style={{ marginTop: 0 }}>
          {list.detecting ? "Buscando los capítulos…" : list.can_detect
            ? "No se han encontrado capítulos (el PDF no trae marcadores). Puedes añadirlos a mano abajo."
            : "Este tipo de documento aún no detecta capítulos solo: añádelos a mano abajo."}
        </p>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <tbody>
            {items.map((c) => (
              <tr key={c.id} style={{ opacity: c.is_content ? 1 : 0.55, borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "6px 4px", paddingLeft: c.level > 1 ? 24 : 4 }}>
                  {editing ? (
                    <input defaultValue={c.title} onBlur={(e) => e.target.value.trim() && e.target.value !== c.title && patch(c, { title: e.target.value.trim() })} />
                  ) : c.is_content ? (
                    <Link href={`/sources/${sourceId}/chapters/${c.id}`} style={{ fontWeight: c.level === 1 ? 600 : 400 }}>{c.title}</Link>
                  ) : <span>{c.title}</span>}
                </td>
                <td className="muted" style={{ whiteSpace: "nowrap", fontSize: "0.85rem", padding: "6px 4px" }}>
                  {editing ? (
                    <>
                      p. <input type="number" min={1} defaultValue={c.page_start} style={{ width: 64 }} onBlur={(e) => Number(e.target.value) !== c.page_start && patch(c, { page_start: Number(e.target.value) })} />
                      –<input type="number" min={1} defaultValue={c.page_end} style={{ width: 64 }} onBlur={(e) => Number(e.target.value) !== c.page_end && patch(c, { page_end: Number(e.target.value) })} />
                    </>
                  ) : c.page_start === c.page_end ? `p. ${c.page_start}` : `p. ${c.page_start}–${c.page_end}`}
                </td>
                <td style={{ whiteSpace: "nowrap", padding: "6px 4px", textAlign: "right" }}>
                  {editing ? (
                    <>
                      <label className="muted" style={{ margin: 0, fontSize: "0.8rem" }}>
                        <input type="checkbox" style={{ width: "auto" }} checked={c.is_content} onChange={(e) => patch(c, { is_content: e.target.checked })} /> contenido
                      </label>{" "}
                      <button style={{ fontSize: "0.75rem", padding: "0 6px" }} onClick={() => { if (confirm(`¿Quitar «${c.title}»? Sus apartados pasan a ser capítulos y sus resúmenes se borran.`)) act(() => api(`/api/chapters/${c.id}`, { method: "DELETE" }), "Quitado."); }}>quitar</button>
                    </>
                  ) : (
                    <>
                      {c.progress && <span className="badge ok" style={{ marginLeft: 4 }}>{PROGRESS.find((x) => x.key === c.progress)?.name}</span>}
                      {c.summaries.map((l) => <span key={l} className="badge" style={{ marginLeft: 4 }} title="Resumen hecho">{LEVELS.find((x) => x.key === l)?.name}</span>)}
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editing && (
        <form className="row" style={{ justifyContent: "flex-start", gap: 6, marginTop: 10 }}
          onSubmit={(e) => {
            e.preventDefault();
            act(() => api(`/api/sources/${sourceId}/chapters`, { method: "POST", body: JSON.stringify({
              title: add.title, page_start: Number(add.page_start), page_end: Number(add.page_end || add.page_start),
              parent_id: add.parent_id || null,
            }) }), "Añadido.").then(() => setAdd({ title: "", page_start: "", page_end: "", parent_id: "" }));
          }}>
          <input placeholder="Título del capítulo o apartado" value={add.title} onChange={(e) => setAdd({ ...add, title: e.target.value })} style={{ maxWidth: 300 }} />
          <span className="muted">p.</span>
          <input type="number" min={1} placeholder="desde" value={add.page_start} onChange={(e) => setAdd({ ...add, page_start: e.target.value })} style={{ width: 80 }} />
          <input type="number" min={1} placeholder="hasta" value={add.page_end} onChange={(e) => setAdd({ ...add, page_end: e.target.value })} style={{ width: 80 }} />
          <select value={add.parent_id} onChange={(e) => setAdd({ ...add, parent_id: e.target.value })} style={{ maxWidth: 240 }}>
            <option value="">— es un capítulo —</option>
            {tops.map((t) => <option key={t.id} value={t.id}>apartado de: {t.title}</option>)}
          </select>
          <button disabled={!add.title.trim() || !add.page_start}>Añadir</button>
        </form>
      )}

      <div className="row" style={{ justifyContent: "flex-start", gap: 8, marginTop: 10 }}>
        <button onClick={() => setEditing(!editing)}>{editing ? "Terminar de editar" : items.length ? "Editar capítulos" : "Añadir capítulos a mano"}</button>
        {list.can_detect && !list.detecting && (items.length === 0
          ? <button onClick={() => detect(false)}>Buscar capítulos</button>
          : <button onClick={() => { if (!edited || confirm("Has editado los capítulos. ¿Volver a detectarlos y perder tus cambios y los resúmenes?")) detect(true); }}>Volver a detectar</button>)}
        {items.length > 0 && <span className="muted" style={{ fontSize: "0.8rem" }}>Sacados de: {ORIGIN[items[0].origin]}{edited ? " · editados por ti" : ""}</span>}
      </div>
      {msg && <p className={msg.ok ? "ok" : "error"} style={{ marginBottom: 0 }}>{msg.text}</p>}
    </div>
  );
}
