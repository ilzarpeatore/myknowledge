"use client";

import Link from "next/link";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { ExtractKnowledge } from "@/components/ExtractKnowledge";
import { Header } from "@/components/Header";
import { SourceEdit } from "@/components/SourceEdit";
import { StudyLink } from "@/components/StudyLink";
import { api, ApiError } from "@/lib/api";
import { clock, sourceHref } from "@/lib/knowledge";
import { loadSections, type Section } from "@/lib/sections";
import { loadDisciplines, ordered, type Discipline } from "@/lib/disciplines";
import { BranchPicker } from "@/components/BranchPicker";
import { useMe } from "@/lib/useMe";

type Version = {
  id: string; original_filename: string | null; mime: string; size_bytes: number;
  page_count: number | null; extractor: string | null;
  extraction_info: { pages_without_text?: number[]; ocr_pages?: number[]; tables?: number; fallback_from?: string; slides?: number; duration_ms?: number; sections?: number } | null;
};
type Source = {
  id: string; title: string; status: string; authors_text: string | null; publication_id: string | null;
  url: string | null; error: string | null; kind: string; sections: string[]; disciplines: { id: string; name: string }[];
  created_at: string; versions: Version[];
};
type UploadResult = { name: string; ok: boolean; message: string };

const STATUS: Record<string, string> = {
  uploaded: "Pendiente de procesar", processing: "Procesando", ready: "Listo",
  failed: "Error", archived: "Archivado",
};
const TYPE: Record<string, string> = {
  "application/pdf": "PDF", "text/markdown": "Markdown", "text/plain": "Texto",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PowerPoint",
  "application/epub+zip": "EPUB", "text/vtt": "Subtítulos", "application/x-subrip": "Subtítulos",
  "audio/mpeg": "Audio", "audio/mp4": "Audio", "audio/wav": "Audio", "audio/ogg": "Audio", "audio/flac": "Audio",
  "video/mp4": "Vídeo", "video/webm": "Vídeo", "video/quicktime": "Vídeo", "video/x-matroska": "Vídeo", "video/x-msvideo": "Vídeo",
};

function details(v: Version | undefined) {
  const i = v?.extraction_info;
  if (!i) return "";
  const parts: string[] = [];
  if (i.tables) parts.push(`${i.tables} tabla${i.tables === 1 ? "" : "s"}`);
  if (i.ocr_pages?.length) parts.push(`OCR en ${i.ocr_pages.length} pág.`);
  if (i.slides) parts.push(`${i.slides} diapositivas`);
  if (i.duration_ms) parts.push(`${clock(i.duration_ms)} de ${i.sections ? "audio transcrito" : "subtítulos"}`);
  return parts.join(" · ");
}

function noText(v: Version | undefined) {
  return v?.extraction_info?.pages_without_text?.length ?? 0;
}

function size(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function LibraryPage() {
  const user = useMe();
  const [items, setItems] = useState<Source[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [archived, setArchived] = useState(false);
  const [results, setResults] = useState<UploadResult[]>([]);
  const [yt, setYt] = useState("");
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const [panel, setPanel] = useState<{ id: string; kind: "extract" | "study" | "edit" } | null>(null);
  const toggle = (id: string, kind: "extract" | "study" | "edit") =>
    setPanel(panel?.id === id && panel.kind === kind ? null : { id, kind });
  const [maxMb, setMaxMb] = useState<number | null>(null);
  const [sections, setSections] = useState<Section[]>([]);
  const [section, setSection] = useState<string>("");
  const [newSection, setNewSection] = useState("");
  const [branches, setBranches] = useState<Discipline[]>([]);
  const [uploadBranches, setUploadBranches] = useState<string[]>([]);
  const [branch, setBranch] = useState<string>("");
  const loadBranches = useCallback(() => loadDisciplines().then(setBranches).catch(() => {}), []);
  useEffect(() => { if (user) loadBranches(); }, [user, loadBranches]);
  const loadSecs = useCallback(() => loadSections().then(setSections).catch(() => {}), []);
  useEffect(() => {
    const s = new URLSearchParams(window.location.search).get("section");
    if (s) setSection(s);
  }, []);
  useEffect(() => { if (user) loadSecs(); }, [user, loadSecs]);
  const secName = (k: string) => sections.find((s) => s.key === k)?.name ?? k;

  useEffect(() => {
    if (user) api<{ max_upload_mb: number }>("/api/status").then((s) => setMaxMb(s.max_upload_mb)).catch(() => {});
  }, [user]);

  const load = useCallback(async () => {
    const params = new URLSearchParams({ limit: "200" });
    if (q.trim()) params.set("q", q.trim());
    if (archived) params.set("status_filter", "archived");
    if (section) params.set("section", section);
    if (branch) params.set("discipline", branch);
    try {
      const r = await api<{ items: Source[]; total: number }>(`/api/sources?${params}`);
      setItems(r.items); setTotal(r.total); setError(null);
    } catch {
      setError("No se pudo cargar la biblioteca.");
    }
  }, [q, archived, section, branch]);

  useEffect(() => { if (user) load(); }, [user, load]);

  // Refresh while something is being processed.
  const pending = items.some((s) => s.status === "uploaded" || s.status === "processing");
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [pending, load]);

  async function reprocess(s: Source) {
    await api(`/api/sources/${s.id}/reprocess`, { method: "POST" }).catch(() => undefined);
    load();
  }

  async function addYoutube(e: React.FormEvent) {
    e.preventDefault();
    try {
      const r = await api<{ source: Source; duplicate: boolean }>("/api/sources/youtube", { method: "POST", body: JSON.stringify({ url: yt, disciplines: uploadBranches }) });
      setResults([{ name: "YouTube", ok: true, message: r.duplicate ? "ya estaba en la biblioteca" : "añadido; se está descargando" }]);
      setYt("");
      await load();
    } catch (err) {
      setResults([{ name: "YouTube", ok: false, message: err instanceof ApiError ? err.message : "error" }]);
    }
  }

  async function uploadFiles(files: FileList | File[]) {
    setBusy(true);
    const out: UploadResult[] = [];
    for (const file of Array.from(files)) {
      if (maxMb !== null && file.size > maxMb * 1024 * 1024) {
        out.push({ name: file.name, ok: false, message: `supera el límite de ${maxMb} MB` });
        setResults([...out]);
        continue;
      }
      const form = new FormData();
      form.append("file", file);
      if (section) form.append("section", section);
      uploadBranches.forEach((d) => form.append("disciplines", d));
      try {
        const r = await api<{ source: Source; duplicate: boolean }>("/api/sources", { method: "POST", body: form });
        out.push({
          name: file.name, ok: true,
          message: r.duplicate ? `ya estaba en la biblioteca como «${r.source.title}»` : "añadido",
        });
      } catch (err) {
        out.push({ name: file.name, ok: false, message: err instanceof ApiError ? err.message : "error de red" });
      }
      setResults([...out]);
    }
    setBusy(false);
    if (input.current) input.current.value = "";
    load(); loadSecs();
  }

  async function remove(s: Source) {
    try {
      const p = await api<{ files: number; fragments: number; claims_deleted: number; claims_losing_a_quote: number; cards_deleted: number; references_mentions: number }>(`/api/sources/${s.id}/delete-preview`);
      const lines = [
        `Se eliminará «${s.title}» para siempre:`,
        `· ${p.files} archivo(s) y ${p.fragments} fragmento(s) de texto`,
        p.claims_deleted ? `· ${p.claims_deleted} afirmación(es) que solo se apoyan en esta fuente` : "",
        p.claims_losing_a_quote ? `· ${p.claims_losing_a_quote} afirmación(es) pierden una cita (se quedan con las demás)` : "",
        p.cards_deleted ? `· ${p.cards_deleted} tarjeta(s) de estudio` : "",
        p.references_mentions ? `· ${p.references_mentions} mención(es) de referencias` : "",
        "Los conceptos se conservan. No se puede deshacer (salvo restaurando una copia de seguridad anterior).",
      ].filter(Boolean);
      if (!confirm(lines.join("\n"))) return;
      await api(`/api/sources/${s.id}`, { method: "DELETE" });
      setResults([{ name: s.title, ok: true, message: "eliminado definitivamente" }]);
    } catch (err) {
      setResults([{ name: s.title, ok: false, message: err instanceof ApiError ? err.message : "no se pudo eliminar" }]);
    }
    load(); loadSecs();
  }

  async function toggleArchive(s: Source) {
    await api(`/api/sources/${s.id}/${s.status === "archived" ? "restore" : "archive"}`, { method: "POST" });
    load();
  }

  return (
    <main>
      <Header user={user} />
      <h1>Biblioteca</h1>
      <div className="row" style={{ justifyContent: "flex-start", gap: 6, flexWrap: "wrap", marginBottom: 10 }} role="tablist" aria-label="Secciones">
        <button className={section === "" ? "primary" : ""} onClick={() => setSection("")}>Todo</button>
        {sections.map((s) => (
          <button key={s.key} className={section === s.key ? "primary" : ""} onClick={() => setSection(s.key)} title={s.description ?? ""}>
            {s.name} <span style={{ opacity: 0.7 }}>({s.count})</span>
          </button>
        ))}
        <form onSubmit={async (e) => { e.preventDefault(); if (!newSection.trim()) return; try { const r = await api<{ key: string }>("/api/sections", { method: "POST", body: JSON.stringify({ name: newSection }) }); setNewSection(""); await loadSecs(); setSection(r.key); } catch (x) { setError(x instanceof ApiError ? x.message : "No se pudo crear la sección."); } }}
          className="row" style={{ gap: 4 }}>
          <input placeholder="+ nueva sección" value={newSection} onChange={(e) => setNewSection(e.target.value)} style={{ width: 150 }} />
        </form>
      </div>
      {section && <p className="muted" style={{ marginTop: 0 }}>{sections.find((s) => s.key === section)?.description} Lo que subas aquí se guarda en esta sección.</p>}

      <div className="card" style={{ marginBottom: 10 }}>
        <p style={{ margin: "0 0 6px" }}><strong>Rama de lo que subas</strong> <span className="muted" style={{ fontSize: "0.85rem" }}>(opcional; puedes elegir varias y cambiarlas después)</span></p>
        <BranchPicker all={branches} value={uploadBranches} onChange={setUploadBranches} onCreated={loadBranches} />
      </div>
      <div
        className={`card drop ${over ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); if (!busy) uploadFiles(e.dataTransfer.files); }}
      >
        <p>Arrastra aquí tus documentos o</p>
        <input ref={input} type="file" multiple accept=".pdf,.docx,.pptx,.epub,.md,.markdown,.txt,.srt,.vtt,audio/*,video/*" disabled={busy}
          onChange={(e) => e.target.files && uploadFiles(e.target.files)} />
        <p className="muted">
          Formatos admitidos: PDF, Word (DOCX), PowerPoint (PPTX), EPUB, Markdown, TXT, subtítulos (SRT/VTT), audio y vídeo (se transcriben){maxMb ? ` (máx. ${maxMb} MB por archivo)` : ""}. El original se guarda sin modificar.
        </p>
        {busy && <p>Subiendo…</p>}
        {results.length > 0 && (
          <ul className="results" style={{ textAlign: "left" }}>
            {results.map((r, i) => (
              <li key={i} className={r.ok ? "ok" : "error"}>{r.name}: {r.message}</li>
            ))}
          </ul>
        )}
      </div>
      <form className="row" style={{ gap: 6, marginTop: 10 }} onSubmit={addYoutube}>
        <input placeholder="Enlace de un vídeo de YouTube" value={yt} onChange={(e) => setYt(e.target.value)} style={{ maxWidth: 480 }} />
        <button disabled={busy || !yt.trim()}>Añadir vídeo de YouTube</button>
        <span className="muted" style={{ fontSize: "0.85rem" }}>Se guardan sus subtítulos (o se transcribe el audio si no tiene).</span>
      </form>

      <div className="row" style={{ marginTop: 20 }}>
        <input placeholder="Buscar por título…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 320 }} />
        <select value={branch} onChange={(e) => setBranch(e.target.value)} style={{ maxWidth: 220 }} aria-label="Rama">
          <option value="">Todas las ramas</option>
          {ordered(branches).map(({ d, depth }) => <option key={d.id} value={d.id}>{"\u00a0\u00a0".repeat(depth)}{d.name}</option>)}
        </select>
        <label className="muted" style={{ margin: 0 }}>
          <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} style={{ width: "auto" }} />{" "}
          Ver archivados (papelera)
        </label>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="card table-wrap" style={{ marginTop: 12 }}>
        {items.length === 0 ? (
          <p className="muted">{q || archived ? "No hay resultados." : "La biblioteca está vacía."}</p>
        ) : (
          <table>
            <thead><tr><th>Título</th><th>Tipo</th><th>Tamaño</th><th>Páginas</th><th>Estado</th><th>Añadido</th><th></th></tr></thead>
            <tbody>
              {items.map((s) => {
                const v = s.versions[0];
                return (
                  <Fragment key={s.id}>
                  <tr>
                    <td>
                      {v || s.url ? (
                        <a href={v ? sourceHref({ source_id: s.id, version_id: v.id, mime: v.mime, page: null }) : s.url!} target="_blank" rel="noopener">{s.title}</a>
                      ) : s.title}
                      {s.authors_text && <div className="muted">{s.authors_text}</div>}
                      <div>{s.sections.map((k) => <span key={k} className="badge" style={{ marginRight: 4, fontSize: "0.75rem" }}>{secName(k)}</span>)}{(s.disciplines ?? []).map((d) => <span key={d.id} className="badge" style={{ marginRight: 4, fontSize: "0.75rem", borderStyle: "dashed" }} title="Rama">{d.name}</span>)} <Link href={`/sources/${s.id}`} style={{ fontSize: "0.8rem" }}>conexiones →</Link></div>
                      {s.publication_id && <div><Link className="badge" href={`/studies/${s.publication_id}`}>estudio</Link></div>}
                    </td>
                    <td>{s.url?.startsWith("https://www.youtube.com/") ? "YouTube" : v ? TYPE[v.mime] ?? v.mime : "—"}</td>
                    <td>{v ? size(v.size_bytes) : "—"}</td>
                    <td>{v?.page_count ?? "—"}</td>
                    <td>
                      <span className="badge">{STATUS[s.status] ?? s.status}</span>
                      {details(v) && <div className="muted">{details(v)}</div>}
                      {s.error && <div className="error" style={{ fontSize: "0.85rem", maxWidth: 320 }}>{s.error}</div>}
                      {v?.extraction_info?.fallback_from && (
                        <div className="error" title="Docling falló con este archivo; se usó la extracción básica">
                          extracción básica (sin tablas)
                        </div>
                      )}
                      {noText(v) > 0 && (
                        <div className="muted" title={`Páginas: ${v!.extraction_info!.pages_without_text!.join(", ")}`}>
                          {noText(v)} pág. sin texto (¿escaneadas?)
                        </div>
                      )}
                    </td>
                    <td>{new Date(s.created_at).toLocaleDateString("es-ES")}</td>
                    <td className="row" style={{ justifyContent: "flex-end", gap: 6 }}>
                      {s.status !== "archived" && (
                        <button onClick={() => toggle(s.id, "edit")} title="Cambiar título, autores o sección (p. ej. pasarlo a Libros)">
                          Editar
                        </button>
                      )}
                      {(s.status === "failed" || s.status === "ready") && (
                        <button onClick={() => reprocess(s)} title="Volver a extraer el texto">Reprocesar</button>
                      )}
                      {s.status === "ready" && (
                        <button onClick={() => toggle(s.id, "extract")} title="Proponer conceptos y afirmaciones con IA">
                          Extraer conocimiento
                        </button>
                      )}
                      {s.status !== "archived" && (
                        <button onClick={() => toggle(s.id, "study")} title="Indicar que este documento es un estudio">
                          {s.publication_id ? "Estudio…" : "Vincular a estudio"}
                        </button>
                      )}
                      <button onClick={() => toggleArchive(s)}>{s.status === "archived" ? "Restaurar" : "Archivar"}</button>
                      {s.status === "archived" && <button className="danger" onClick={() => remove(s)} style={{ color: "var(--danger)" }}>Eliminar definitivamente</button>}
                    </td>
                  </tr>
                  {panel?.id === s.id && (
                    <tr><td colSpan={7}>
                      {panel.kind === "extract"
                        ? <ExtractKnowledge sourceId={s.id} onClose={() => setPanel(null)} />
                        : panel.kind === "edit"
                          ? <SourceEdit key={s.id} source={s} sections={sections} branches={branches} onBranchesChanged={loadBranches} onDone={() => { load(); loadSecs(); }} />
                          : <StudyLink sourceId={s.id} current={s.publication_id} onDone={load} />}
                    </td></tr>
                  )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
        <p className="muted" style={{ marginBottom: 0 }}>{total} fuente(s)</p>
      </div>
    </main>
  );
}
