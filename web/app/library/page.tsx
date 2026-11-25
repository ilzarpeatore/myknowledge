"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Version = { id: string; original_filename: string | null; mime: string; size_bytes: number };
type Source = {
  id: string; title: string; status: string; authors_text: string | null;
  created_at: string; versions: Version[];
};
type UploadResult = { name: string; ok: boolean; message: string };

const STATUS: Record<string, string> = {
  uploaded: "Pendiente de procesar", processing: "Procesando", ready: "Listo",
  failed: "Error", archived: "Archivado",
};
const TYPE: Record<string, string> = {
  "application/pdf": "PDF", "text/markdown": "Markdown", "text/plain": "Texto",
};

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
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const [maxMb, setMaxMb] = useState<number | null>(null);

  useEffect(() => {
    if (user) api<{ max_upload_mb: number }>("/api/status").then((s) => setMaxMb(s.max_upload_mb)).catch(() => {});
  }, [user]);

  const load = useCallback(async () => {
    const params = new URLSearchParams({ limit: "200" });
    if (q.trim()) params.set("q", q.trim());
    if (archived) params.set("status_filter", "archived");
    try {
      const r = await api<{ items: Source[]; total: number }>(`/api/sources?${params}`);
      setItems(r.items); setTotal(r.total); setError(null);
    } catch {
      setError("No se pudo cargar la biblioteca.");
    }
  }, [q, archived]);

  useEffect(() => { if (user) load(); }, [user, load]);

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
    load();
  }

  async function toggleArchive(s: Source) {
    await api(`/api/sources/${s.id}/${s.status === "archived" ? "restore" : "archive"}`, { method: "POST" });
    load();
  }

  return (
    <main>
      <Header user={user} />
      <h1>Biblioteca</h1>

      <div
        className={`card drop ${over ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); if (!busy) uploadFiles(e.dataTransfer.files); }}
      >
        <p>Arrastra aquí tus documentos o</p>
        <input ref={input} type="file" multiple accept=".pdf,.md,.markdown,.txt" disabled={busy}
          onChange={(e) => e.target.files && uploadFiles(e.target.files)} />
        <p className="muted">
          Formatos admitidos por ahora: PDF, Markdown y TXT{maxMb ? ` (máx. ${maxMb} MB por archivo)` : ""}. El original se guarda sin modificar.
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

      <div className="row" style={{ marginTop: 20 }}>
        <input placeholder="Buscar por título…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 320 }} />
        <label className="muted" style={{ margin: 0 }}>
          <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} style={{ width: "auto" }} />{" "}
          Ver archivados
        </label>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="card table-wrap" style={{ marginTop: 12 }}>
        {items.length === 0 ? (
          <p className="muted">{q || archived ? "No hay resultados." : "La biblioteca está vacía."}</p>
        ) : (
          <table>
            <thead><tr><th>Título</th><th>Tipo</th><th>Tamaño</th><th>Estado</th><th>Añadido</th><th></th></tr></thead>
            <tbody>
              {items.map((s) => {
                const v = s.versions[0];
                return (
                  <tr key={s.id}>
                    <td>
                      {v ? (
                        <a href={`/api/sources/${s.id}/versions/${v.id}/file`} target="_blank" rel="noopener">{s.title}</a>
                      ) : s.title}
                      {s.authors_text && <div className="muted">{s.authors_text}</div>}
                    </td>
                    <td>{v ? TYPE[v.mime] ?? v.mime : "—"}</td>
                    <td>{v ? size(v.size_bytes) : "—"}</td>
                    <td><span className="badge">{STATUS[s.status] ?? s.status}</span></td>
                    <td>{new Date(s.created_at).toLocaleDateString("es-ES")}</td>
                    <td><button onClick={() => toggleArchive(s)}>{s.status === "archived" ? "Restaurar" : "Archivar"}</button></td>
                  </tr>
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
