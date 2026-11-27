"use client";

import { useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { SeeAlso } from "@/components/SeeAlso";
import { api, ApiError } from "@/lib/api";
import { pickEvidence, sourceHref, where } from "@/lib/knowledge";
import { loadSections, type Section } from "@/lib/sections";
import { useMe } from "@/lib/useMe";

type Hit = {
  segment_id: string; source_id: string; version_id: string; source_title: string;
  mime: string; page: number | null; section: string | null; slide?: number | null; t_ms?: number | null; snippet: string;
  match: "keyword" | "semantic" | "both";
};
type Mode = "hybrid" | "keyword" | "semantic";
const MATCH: Record<Hit["match"], string> = {
  keyword: "por palabras", semantic: "por significado", both: "palabras y significado",
};

/** Render a snippet whose matches are wrapped in \x02 … \x03, without using HTML. */
function Snippet({ text }: { text: string }) {
  const parts = text.split(/(\x02[^\x03]*\x03)/);
  return (
    <p style={{ margin: "6px 0 0" }}>
      {parts.map((p, i) =>
        p.startsWith("\x02") ? <mark key={i}>{p.slice(1, -1)}</mark> : <span key={i}>{p}</span>,
      )}
    </p>
  );
}

function sourceLink(h: Hit) {
  return sourceHref(h);
}

export default function SearchPage() {
  const user = useMe();
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<Mode>("hybrid");
  const [section, setSection] = useState("");
  const [sections, setSections] = useState<Section[]>([]);
  useEffect(() => { loadSections().then(setSections).catch(() => {}); }, []);
  const [pickedId, setPickedId] = useState<string | null>(null);
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    try {
      setHits(await api<Hit[]>(`/api/search?${new URLSearchParams({ q: q.trim(), mode, ...(section ? { section } : {}) })}`));
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 503
          ? "La búsqueda por significado no está disponible (falta el modelo local)."
          : "No se pudo buscar.",
      );
    }
  }

  return (
    <main>
      <Header user={user} />
      <h1>Buscar en las fuentes</h1>
      <form className="row" onSubmit={submit} style={{ flexWrap: "nowrap" }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="p. ej. proximidad al fallo" autoFocus />
        <select value={section} onChange={(e) => setSection(e.target.value)} aria-label="Sección" style={{ maxWidth: 180 }}>
          <option value="">Todas las secciones</option>
          {sections.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
        </select>
        <select value={mode} onChange={(e) => setMode(e.target.value as Mode)} aria-label="Modo de búsqueda">
          <option value="hybrid">Combinada</option>
          <option value="keyword">Por palabras</option>
          <option value="semantic">Por significado</option>
        </select>
        <button className="primary">Buscar</button>
      </form>
      <p className="muted">
        «Combinada» une coincidencias por palabras (admite comillas y «-palabra») y por significado (encuentra
        sinónimos y textos en otro idioma). Los resultados solo «por significado» son aproximados.
      </p>
      {error && <p className="error">{error}</p>}
      {hits && hits.length === 0 && <p className="muted">Sin resultados.</p>}
      {hits?.map((h) => (
        <div className="card" key={h.segment_id} style={{ marginTop: 10 }}>
          <div className="row">
            <a href={sourceLink(h)} target="_blank" rel="noopener"><strong>{h.source_title}</strong></a>
            <span className="muted">
              <span className="badge" style={{ marginRight: 6 }}>{MATCH[h.match]}</span>
              {where(h).replace(/^, /, "")}
            </span>
          </div>
          <Snippet text={h.snippet} />
          <button style={{ marginTop: 6 }} onClick={() => {
            pickEvidence({ segment_id: h.segment_id, source_title: h.source_title, page: h.page });
            setPickedId(h.segment_id);
          }}>{pickedId === h.segment_id ? "Seleccionado ✓ (ve a un concepto)" : "Usar como evidencia"}</button>
          <SeeAlso segmentId={h.segment_id} />
        </div>
      ))}
    </main>
  );
}
